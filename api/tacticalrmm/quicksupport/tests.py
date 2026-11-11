import os
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone as djangotime
from knox.models import AuthToken
from model_bakery import baker

from logs.models import AuditLog
from tacticalrmm.test import TacticalTestCase

from .models import (
    InstallStatus,
    QuickSupportConfig,
    QuickSupportSession,
    QuickSupportStatus,
)
from .views import CONFIG_BEGIN, CONFIG_END

BASE = "/quicksupport"


class QuickSupportTestCase(TacticalTestCase):
    def setUp(self):
        # the executable endpoints are rate limited per ip (cache)
        cache.clear()
        self.setup_coresettings()
        self.authenticate()
        self.cfg = QuickSupportConfig.load()

    def client_headers(self, session=None, token=None):
        headers = {"HTTP_X_QUICKSUPPORT_TOKEN": token or self.cfg.client_token}
        if session is not None:
            headers["HTTP_X_QUICKSUPPORT_SESSION"] = session.secret
        return headers

    def make_session(self, **kwargs):
        return QuickSupportSession.objects.create(
            code=QuickSupportSession.generate_code(),
            secret="s3cret-" + os.urandom(8).hex(),
            hostname="DESKTOP-1",
            username="customer",
            **kwargs,
        )


@patch("quicksupport.views._agent_sha256", return_value="ab" * 32)
@patch("quicksupport.views.get_session_msh", return_value="MeshName=QS")
class TestClientSessions(QuickSupportTestCase):
    url = f"{BASE}/client/sessions/"

    def test_create_session(self, mock_msh, mock_sha):
        r = self.client.post(
            self.url,
            {"hostname": "DESKTOP-1", "username": "customer", "arch": "amd64"},
            format="json",
            **self.client_headers(),
        )
        self.assertEqual(r.status_code, 200)
        session = QuickSupportSession.objects.get(pk=r.data["session_id"])
        self.assertEqual(r.data["code"], session.code)
        self.assertEqual(len(session.code), 9)
        self.assertEqual(r.data["session_secret"], session.secret)
        self.assertEqual(r.data["tag"], session.agent_tag)
        self.assertEqual(r.data["msh"], "MeshName=QS")
        self.assertEqual(r.data["agent_sha256"], "ab" * 32)
        self.assertEqual(session.hostname, "DESKTOP-1")

    def test_create_session_token(self, mock_msh, mock_sha):
        # no token / wrong token
        r = self.client.post(self.url, {}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.client.post(
            self.url, {}, format="json", **self.client_headers(token="x")
        )
        self.assertEqual(r.status_code, 403)

        # disabled
        self.cfg.enabled = False
        self.cfg.save()
        r = self.client.post(self.url, {}, format="json", **self.client_headers())
        self.assertEqual(r.status_code, 403)
        self.assertFalse(QuickSupportSession.objects.exists())

    def test_create_session_arch(self, mock_msh, mock_sha):
        r = self.client.post(
            self.url, {"arch": "mips"}, format="json", **self.client_headers()
        )
        self.assertEqual(r.status_code, 400)

    def test_sessions_per_ip(self, mock_msh, mock_sha):
        for _ in range(10):
            self.make_session(client_ip="127.0.0.1")
        r = self.client.post(self.url, {}, format="json", **self.client_headers())
        self.assertEqual(r.status_code, 429)


class TestClientSessionLifecycle(QuickSupportTestCase):
    def test_heartbeat(self):
        session = self.make_session()
        session.last_seen = djangotime.now() - timedelta(minutes=2)
        session.save()
        url = f"{BASE}/client/sessions/{session.pk}/heartbeat/"

        # wrong session secret
        r = self.client.post(url, {}, format="json", HTTP_X_QUICKSUPPORT_SESSION="x")
        self.assertEqual(r.status_code, 404)

        r = self.client.post(url, {}, format="json", **self.client_headers(session))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], QuickSupportStatus.WAITING)
        session.refresh_from_db()
        self.assertGreater(session.last_seen, djangotime.now() - timedelta(seconds=30))

    @patch("quicksupport.views.remove_node")
    def test_close(self, mock_remove):
        session = self.make_session(mesh_node_id="ab" * 24)
        url = f"{BASE}/client/sessions/{session.pk}/close/"
        r = self.client.post(url, {}, format="json", **self.client_headers(session))
        self.assertEqual(r.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, QuickSupportStatus.CLOSED)
        mock_remove.assert_called_once()

    def test_active_sessions_expire(self):
        session = self.make_session()
        self.assertTrue(
            QuickSupportSession.objects.active().filter(pk=session.pk).exists()
        )
        session.last_seen = djangotime.now() - timedelta(
            minutes=self.cfg.session_timeout + 1
        )
        session.save()
        self.assertFalse(
            QuickSupportSession.objects.active().filter(pk=session.pk).exists()
        )


class TestDashboard(QuickSupportTestCase):
    def test_sessions(self):
        self.make_session()
        url = f"{BASE}/sessions/"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)
        self.check_not_authenticated("get", url)

    def test_sessions_permission(self):
        url = f"{BASE}/sessions/"
        user = self.create_user_with_roles([])
        self.client.force_authenticate(user=user)
        self.check_not_authorized("get", url)

        user = self.create_user_with_roles(["can_use_quick_support"])
        self.client.force_authenticate(user=user)
        self.check_authorized("get", url)

    def test_restricted_user_only_sees_own_sessions(self):
        mine = self.make_session()
        self.make_session()
        user = self.create_user_with_roles(["can_use_quick_support"])
        site = baker.make("clients.Site")
        user.role.can_view_sites.set([site])
        mine.connected_by = user
        mine.save()

        self.client.force_authenticate(user=user)
        r = self.client.get(f"{BASE}/sessions/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["id"] for s in r.data], [str(mine.pk)])

    @patch("quicksupport.views.get_login_url", return_value="https://mesh/login")
    @patch("quicksupport.views.grant_user_on_node")
    @patch("quicksupport.views.find_session_node")
    def test_connect(self, mock_find, mock_grant, mock_login):
        session = self.make_session()
        url = f"{BASE}/connect/"

        r = self.client.post(url, {"code": "000000000"}, format="json")
        self.assertEqual(r.status_code, 400)

        # agent not connected to meshcentral yet
        mock_find.return_value = None
        r = self.client.post(url, {"code": session.formatted_code}, format="json")
        self.assertEqual(r.status_code, 400)

        mock_find.return_value = {"_id": "node//" + "A" * 64, "conn": 1}
        r = self.client.post(url, {"code": session.formatted_code}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["control"], "https://mesh/login")
        mock_grant.assert_called_once()
        session.refresh_from_db()
        self.assertEqual(session.status, QuickSupportStatus.CONNECTED)
        self.assertEqual(session.connected_by, self.john)
        self.assertTrue(AuditLog.objects.filter(username="john").exists())

        self.check_not_authenticated("post", url)

    @patch("quicksupport.views.remove_node")
    def test_end_session(self, mock_remove):
        session = self.make_session()
        r = self.client.post(f"{BASE}/sessions/{session.pk}/end/")
        self.assertEqual(r.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, QuickSupportStatus.CLOSED)


@patch("core.utils.token_is_valid", return_value=("", False))
@patch("agents.utils.get_agent_url", return_value="https://example.com/agent.exe")
class TestInstall(QuickSupportTestCase):
    def setUp(self):
        super().setUp()
        self.site = baker.make("clients.Site")
        baker.make("accounts.User", is_installer_user=True)
        self.session = self.make_session()

    def approve(self, **data):
        return self.client.post(
            f"{BASE}/sessions/{self.session.pk}/install/",
            {"site": self.site.pk, **data},
            format="json",
        )

    def test_install_flow(self, mock_url, mock_token):
        s = self.session
        hdrs = self.client_headers(s)

        # customer asks for permanent access
        r = self.client.post(
            f"{BASE}/client/sessions/{s.pk}/install/request/", {}, format="json", **hdrs
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["install_status"], InstallStatus.REQUESTED)

        # not approved yet
        r = self.client.get(f"{BASE}/client/sessions/{s.pk}/installer/", **hdrs)
        self.assertEqual(r.status_code, 409)

        r = self.approve(agenttype="server")
        self.assertEqual(r.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.install_status, InstallStatus.APPROVED)
        self.assertEqual(s.install_site, self.site)

        # installer: once only, with an installer token
        r = self.client.get(f"{BASE}/client/sessions/{s.pk}/installer/", **hdrs)
        self.assertEqual(r.status_code, 200)
        script = r.content.decode()
        self.assertIn(f"'{self.site.pk}'", script)
        self.assertIn("server", script)
        s.refresh_from_db()
        self.assertEqual(s.install_status, InstallStatus.SENT)
        self.assertTrue(
            AuthToken.objects.filter(token_key=s.install_token_key).exists()
        )

        r = self.client.get(f"{BASE}/client/sessions/{s.pk}/installer/", **hdrs)
        self.assertEqual(r.status_code, 409)

        # result: the installer token is revoked
        token_key = s.install_token_key
        r = self.client.post(
            f"{BASE}/client/sessions/{s.pk}/install/result/",
            {"ok": True, "message": "done"},
            format="json",
            **hdrs,
        )
        self.assertEqual(r.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.install_status, InstallStatus.DONE)
        self.assertFalse(AuthToken.objects.filter(token_key=token_key).exists())

    def test_deny(self, mock_url, mock_token):
        r = self.approve(approve=False)
        self.assertEqual(r.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.install_status, InstallStatus.DENIED)

    def test_approve_permissions(self, mock_url, mock_token):
        # needs "install agents"
        user = self.create_user_with_roles(["can_use_quick_support"])
        self.client.force_authenticate(user=user)
        self.assertEqual(self.approve().status_code, 403)

        # and access to the site
        user = self.create_user_with_roles(
            ["can_use_quick_support", "can_install_agents"]
        )
        user.role.can_view_sites.set([baker.make("clients.Site")])
        self.client.force_authenticate(user=user)
        self.assertEqual(self.approve().status_code, 403)

    @override_settings(TRMM_INSECURE=True)
    def test_insecure(self, mock_url, mock_token):
        self.approve()
        r = self.client.get(
            f"{BASE}/client/sessions/{self.session.pk}/installer/",
            **self.client_headers(self.session),
        )
        self.assertEqual(r.status_code, 503)
        self.session.refresh_from_db()
        self.assertEqual(self.session.install_status, InstallStatus.FAILED)


class TestSettings(QuickSupportTestCase):
    url = f"{BASE}/settings/"

    def test_get(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("client_token", r.data)
        self.check_not_authenticated("get", self.url)

    @patch("quicksupport.views.ensure_mesh_group")
    def test_put(self, mock_group):
        r = self.client.put(
            self.url,
            {"company_name": "ACME\r\n", "session_timeout": 5},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        cfg = QuickSupportConfig.load()
        self.assertEqual(cfg.company_name, "ACME")
        self.assertEqual(cfg.session_timeout, 5)
        mock_group.assert_called_once()

        # same name as the main tactical device group
        self.coresettings.mesh_device_group = "TacticalRMM"
        self.coresettings.save()
        r = self.client.put(self.url, {"mesh_group_name": "tacticalrmm"}, format="json")
        self.assertEqual(r.status_code, 400)

        r = self.client.put(self.url, {"session_timeout": 0}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_permissions(self):
        user = self.create_user_with_roles(["can_view_core_settings"])
        self.client.force_authenticate(user=user)
        self.check_authorized("get", self.url)
        self.check_not_authorized("put", self.url, {"company_name": "x"})

    def test_reset_token(self):
        old = self.cfg.client_token
        r = self.client.post(f"{BASE}/settings/resettoken/")
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(QuickSupportConfig.load().client_token, old)


class TestDownloadClient(QuickSupportTestCase):
    url = f"{BASE}/download/"

    def test_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "qs.exe")
            with open(exe, "wb") as f:
                f.write(b"MZ-fake-exe")

            with override_settings(QUICKSUPPORT_CLIENT_EXE=exe):
                r = self.client.get(self.url)
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.content.startswith(b"MZ-fake-exe" + CONFIG_BEGIN))
                self.assertTrue(r.content.endswith(CONFIG_END))

            with override_settings(QUICKSUPPORT_CLIENT_EXE=exe + ".missing"):
                r = self.client.get(self.url)
                self.assertEqual(r.status_code, 400)

        self.check_not_authenticated("get", self.url)
