import json
import os
import shutil
import struct
import tempfile
import time
from unittest.mock import patch

from django.test import override_settings
from model_bakery import baker

from accounts.models import User
from logs.models import AuditLog
from remoteaccess.models import RemoteAccessDevice, RemoteAccessStatus
from tacticalrmm.test import TacticalTestCase

from .mcrec import mesh_user_pk, read_info
from .models import (
    Recording,
    RecordingConfig,
    RecordingSource,
    RecordingStatus,
    RecordingToken,
    RemoteConnectLog,
)
from .tasks import scan_mesh_recordings_task, upload_recording_task

NODE_HEX = "AB" * 48


def _node_b64() -> str:
    from base64 import b64encode

    return "node//" + b64encode(bytes.fromhex(NODE_HEX)).decode().replace(
        "/", "$"
    ).replace("+", "@")


def _block(btype: int, data: bytes, ms: int, flags: int = 0) -> bytes:
    header = (
        struct.pack(">hhi", btype, flags, len(data))
        + b"\x00\x00"
        + ms.to_bytes(6, "big")
    )
    return header + data


def make_mcrec(path: str, userid: str, start_ms: int, seconds: int) -> None:
    meta = {
        "magic": "MeshCentralRelaySession",
        "ver": 1,
        "userid": userid,
        "username": "john",
        "nodeid": _node_b64(),
        "protocol": 2,
    }
    with open(path, "wb") as f:
        f.write(_block(1, json.dumps(meta).encode(), start_ms))
        f.write(_block(2, b"\x00" * 100, start_ms + 1000, 1))
        f.write(_block(2, b"\x00" * 100, start_ms + seconds * 1000, 1))
        f.write(_block(3, b"MeshCentralMCREC", start_ms + seconds * 1000))


class _Base(TacticalTestCase):
    def setUp(self):
        self.authenticate()
        self.setup_coresettings()
        self.tmp = tempfile.mkdtemp()
        self.spool = os.path.join(self.tmp, "spool")
        self.mesh = os.path.join(self.tmp, "mesh")
        os.makedirs(self.mesh)
        self.settings_override = override_settings(
            RECORDINGS_SPOOL_DIR=self.spool, MESHCENTRAL_RECORDINGS_DIR=self.mesh
        )
        self.settings_override.enable()
        cfg = RecordingConfig.load()
        cfg.enabled = True
        cfg.save()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure_s3(self):
        cfg = RecordingConfig.load()
        cfg.s3_bucket = "bucket"
        cfg.s3_access_key = "AKIA"
        cfg.s3_secret = "secret"
        cfg.save()


class TestMcrec(_Base):
    def test_read_info(self):
        path = os.path.join(self.tmp, "a.mcrec")
        make_mcrec(path, f"user//john___{self.john.pk}", 1_700_000_000_000, 75)
        info = read_info(path)
        self.assertEqual(info.duration, 75)
        self.assertEqual(info.protocol, 2)
        self.assertEqual(info.nodeid, _node_b64())
        self.assertEqual(mesh_user_pk(info.userid), self.john.pk)
        self.assertIsNone(mesh_user_pk("user//admin"))

    def test_scan_links_agent_and_technician(self):
        agent = baker.make_recipe("agents.agent", mesh_node_id=NODE_HEX.lower())
        path = os.path.join(self.mesh, "relaysession-x.mcrec")
        make_mcrec(path, f"user//john___{self.john.pk}", int(time.time() * 1000), 30)
        old = time.time() - 600
        os.utime(path, (old, old))
        # still being written: skipped
        recent = os.path.join(self.mesh, "relaysession-y.mcrec")
        make_mcrec(recent, "user//x", int(time.time() * 1000), 5)

        self.assertEqual(scan_mesh_recordings_task(), 1)
        rec = Recording.objects.get()
        self.assertEqual(rec.source, RecordingSource.MESH)
        self.assertEqual(rec.status, RecordingStatus.PENDING)
        self.assertEqual(rec.agent, agent)
        self.assertEqual(rec.site, agent.site)
        self.assertEqual(rec.technician, self.john)
        self.assertEqual(rec.duration, 30)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isfile(rec.local_path))
        self.assertTrue(os.path.exists(recent))

    def test_scan_disabled(self):
        cfg = RecordingConfig.load()
        cfg.record_mesh = False
        cfg.save()
        path = os.path.join(self.mesh, "a.mcrec")
        make_mcrec(path, "user//x", 1_700_000_000_000, 5)
        os.utime(path, (1, 1))
        self.assertEqual(scan_mesh_recordings_task(), 0)
        self.assertTrue(os.path.exists(path))


class TestUploadTask(_Base):
    def _rec(self):
        os.makedirs(self.spool, exist_ok=True)
        path = os.path.join(self.spool, "f.webm")
        with open(path, "wb") as f:
            f.write(b"x" * 1000)
        from django.utils import timezone

        return Recording.objects.create(
            source=RecordingSource.RUSTDESK,
            started=timezone.now(),
            filename="f.webm",
            content_type="video/webm",
            local_path=path,
            hostname="pc1",
        )

    @patch("recordings.s3.object_size", return_value=1000)
    @patch("recordings.s3.upload")
    def test_upload_deletes_local(self, upload, size):
        self.configure_s3()
        rec = self._rec()
        path = rec.local_path
        self.assertEqual(upload_recording_task(rec.pk), "ok")
        rec.refresh_from_db()
        self.assertEqual(rec.status, RecordingStatus.UPLOADED)
        self.assertTrue(rec.s3_key.startswith("recordings/"))
        self.assertTrue(rec.s3_key.endswith("/quick-support/pc1/f.webm"))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(len(rec.sha256), 64)

    @patch("recordings.s3.object_size", return_value=10)
    @patch("recordings.s3.upload")
    def test_upload_size_mismatch_keeps_file(self, upload, size):
        self.configure_s3()
        rec = self._rec()
        upload_recording_task(rec.pk)
        rec.refresh_from_db()
        self.assertEqual(rec.status, RecordingStatus.ERROR)
        self.assertTrue(os.path.exists(rec.local_path))


class TestUploadView(_Base):
    url = "/recordings/upload/"

    def setUp(self):
        super().setUp()
        self.agent = baker.make_recipe("agents.agent")
        self.device = RemoteAccessDevice.objects.create(
            agent=self.agent,
            hostname="pc1",
            remote_id="123456789",
            status=RemoteAccessStatus.OK,
        )
        self.token = RecordingToken.issue(device=self.device)
        self.client.logout()

    def post(
        self, kind, file="incoming_1_2_display0_vp9.webm", body=b"", token=None, **q
    ):
        params = "&".join(
            [f"type={kind}", f"file={file}"] + [f"{k}={v}" for k, v in q.items()]
        )
        return self.client.generic(
            "POST",
            f"{self.url}?{params}",
            data=body,
            content_type="application/octet-stream",
            HTTP_X_TRMM_RECORD_TOKEN=token if token is not None else self.token,
        )

    @patch("recordings.views.upload_recording_task.delay")
    def test_full_upload(self, delay):
        RemoteConnectLog.objects.create(
            device=self.device, user=self.john, username="john"
        )
        self.assertEqual(self.post("new").status_code, 200)
        rec = Recording.objects.get()
        self.assertEqual(rec.status, RecordingStatus.RECEIVING)
        self.assertEqual(rec.technician, self.john)
        self.assertEqual(rec.agent, self.agent)

        self.assertEqual(
            self.post("part", body=b"a" * 10, offset=0, length=10).status_code, 200
        )
        self.assertEqual(
            self.post("part", body=b"b" * 5, offset=10, length=5).status_code, 200
        )
        # gap
        r = self.post("part", body=b"c", offset=100, length=1)
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())
        # header rewritten by the tail
        self.assertEqual(
            self.post("tail", body=b"HH", offset=0, length=2).status_code, 200
        )
        rec.refresh_from_db()
        self.assertEqual(rec.status, RecordingStatus.PENDING)
        with open(rec.local_path, "rb") as f:
            self.assertEqual(f.read(), b"HH" + b"a" * 8 + b"b" * 5)
        delay.assert_not_called()  # bucket not configured

    def test_bad_token_and_name(self):
        self.assertEqual(self.post("new", token="nope").status_code, 403)
        self.assertEqual(self.post("new", token="").status_code, 403)
        self.assertEqual(self.post("new", file="../../etc/passwd").status_code, 400)
        self.assertEqual(self.post("new", file="a.exe").status_code, 400)

    def test_disabled(self):
        cfg = RecordingConfig.load()
        cfg.record_rustdesk = False
        cfg.save()
        self.assertEqual(self.post("new").status_code, 403)

    def test_length_mismatch(self):
        self.post("new")
        self.assertEqual(
            self.post("part", body=b"abc", offset=0, length=10).status_code, 400
        )

    def test_remove(self):
        self.post("new")
        path = Recording.objects.get().local_path
        self.assertEqual(self.post("remove").status_code, 200)
        self.assertFalse(Recording.objects.exists())
        self.assertFalse(os.path.exists(path))

    def test_other_device_cant_touch(self):
        self.post("new")
        other = RemoteAccessDevice.objects.create(hostname="pc2", remote_id="987654321")
        token2 = RecordingToken.issue(device=other)
        r = self.post("part", body=b"x", offset=0, length=1, token=token2)
        self.assertEqual(r.status_code, 400)  # unknown file for that device


class TestRecordingsApi(_Base):
    def setUp(self):
        super().setUp()
        from django.utils import timezone

        self.agent1 = baker.make_recipe("agents.agent")
        self.agent2 = baker.make_recipe("agents.agent")
        for agent in (self.agent1, self.agent2):
            Recording.objects.create(
                source=RecordingSource.MESH,
                status=RecordingStatus.UPLOADED,
                agent=agent,
                client=agent.client,
                site=agent.site,
                hostname=agent.hostname,
                started=timezone.now(),
                filename="a.mcrec",
                content_type="application/x-mcrec",
                s3_key="recordings/a.mcrec",
            )

    def _limited_user(self, **perms):
        role = baker.make("accounts.Role", **perms)
        user = baker.make("accounts.User", role=role, is_superuser=False)
        self.client.force_authenticate(user=user)
        return role, user

    def test_list_superuser(self):
        r = self.client.get("/recordings/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["total"], 2)
        r = self.client.get(f"/recordings/?agent_id={self.agent1.agent_id}")
        self.assertEqual(r.data["total"], 1)

    def test_permission_required(self):
        self._limited_user(can_view_recordings=False)
        self.assertEqual(self.client.get("/recordings/").status_code, 403)

    def test_client_restriction(self):
        role, _ = self._limited_user(can_view_recordings=True)
        role.can_view_clients.set([self.agent1.client])
        r = self.client.get("/recordings/")
        self.assertEqual(r.data["total"], 1)
        other = Recording.objects.get(agent=self.agent2)
        self.assertEqual(
            self.client.get(f"/recordings/{other.pk}/play/").status_code, 404
        )

    @patch("recordings.s3.presigned_url", return_value="https://bucket/a.mcrec?sig")
    def test_play_audited(self, presign):
        self.configure_s3()
        rec = Recording.objects.get(agent=self.agent1)
        r = self.client.get(f"/recordings/{rec.pk}/play/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["kind"], "mcrec")
        self.assertTrue(
            AuditLog.objects.filter(message__contains="watched the recording").exists()
        )

    def test_settings_secret_write_only(self):
        r = self.client.put(
            "/recordings/settings/",
            {"s3_bucket": "b", "s3_access_key": "k", "s3_secret": "topsecret"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/recordings/settings/")
        self.assertNotIn("s3_secret", r.data)
        self.assertTrue(r.data["s3_secret_set"])
        self.assertNotIn("topsecret", json.dumps(r.data))
        self.assertEqual(RecordingConfig.load().s3_secret, "topsecret")
        # empty secret keeps the stored one
        self.client.put("/recordings/settings/", {"s3_secret": ""}, format="json")
        self.assertEqual(RecordingConfig.load().s3_secret, "topsecret")
        r = self.client.put(
            "/recordings/settings/", {"s3_endpoint": "http://x"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_dashinfo_flag(self):
        role, _ = self._limited_user(can_view_recordings=True)
        r = self.client.get("/core/dashinfo/")
        self.assertTrue(r.data["can_view_recordings"])


class TestHostConfig(_Base):
    def test_host_script_record(self):
        from remoteaccess.scripts import host_script

        from .views import host_record_json

        device = RemoteAccessDevice.objects.create(hostname="pc", remote_id="111222333")
        record = host_record_json(device, "https://api.example.com")
        data = json.loads(record)
        self.assertEqual(data["url"], "https://api.example.com/recordings/upload/")
        self.assertEqual(RecordingToken.objects.filter(device=device).count(), 1)

        script = host_script(
            plat="windows", password="pw123456", install=False, record=record
        )
        self.assertIn("--record-config", script)
        self.assertIn(data["token"], script)
        self.assertNotIn("__RECORD", script)
        script = host_script(plat="linux", password="pw123456", install=False)
        self.assertIn("if [ '0' = '1' ]", script)

        with self.assertRaises(ValueError):
            host_script(plat="linux", password="pw", install=False, record="{'x'}")

        cfg = RecordingConfig.load()
        cfg.record_rustdesk = False
        cfg.save()
        self.assertEqual(host_record_json(device, "https://api.example.com"), "")
        self.assertFalse(RecordingToken.objects.filter(device=device).exists())

    def test_qs_token(self):
        from quicksupport.models import QuickSupportConfig, QuickSupportSession

        qs = QuickSupportConfig.load()
        qs.enabled = True
        qs.save()
        session = QuickSupportSession.objects.create(
            code="123456789", secret="s3cr3t", kind="rustdesk"
        )
        self.client.logout()
        url = f"/recordings/qs/{session.pk}/token/"
        headers = {
            "HTTP_X_QUICKSUPPORT_TOKEN": qs.client_token,
            "HTTP_X_QUICKSUPPORT_SESSION": "s3cr3t",
        }
        r = self.client.post(url, **headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["url"].endswith("/recordings/upload/"))
        rt = RecordingToken.objects.get(qs_session=session)
        self.assertIsNotNone(rt.expires)
        # wrong session secret
        r = self.client.post(
            url,
            HTTP_X_QUICKSUPPORT_TOKEN=qs.client_token,
            HTTP_X_QUICKSUPPORT_SESSION="x",
        )
        self.assertEqual(r.status_code, 404)
        cfg = RecordingConfig.load()
        cfg.record_qs = False
        cfg.save()
        self.assertEqual(self.client.post(url, **headers).status_code, 404)


class TestConnectLog(_Base):
    def test_connect_creates_log(self):
        from remoteaccess.models import RemoteAccessConfig

        agent = baker.make_recipe("agents.agent")
        device = RemoteAccessDevice.objects.create(
            agent=agent,
            hostname="pc",
            remote_id="123123123",
            status=RemoteAccessStatus.OK,
        )
        device.password = "secretpw1"
        device.save()
        RemoteAccessConfig.load()
        r = self.client.get(f"/remoteaccess/agents/{agent.agent_id}/connect/")
        self.assertEqual(r.status_code, 200)
        log = RemoteConnectLog.objects.get()
        self.assertEqual(log.device, device)
        self.assertEqual(log.user, self.john)
        self.assertIsInstance(User.objects.get(pk=self.john.pk), User)
