from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from model_bakery import baker

from tacticalrmm.test import TacticalTestCase

from .models import RemoteAccessDevice, RemoteAccessStatus
from .tasks import BULK_LANES, bulk_provision_step_task, bulk_queued_key


# production uses redis; the tests' default dummy cache stores nothing
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class TestBulkProvision(TacticalTestCase):
    url = "/remoteaccess/bulk/provision/"

    def setUp(self):
        self.authenticate()
        self.setup_coresettings()
        cache.clear()
        self.site = baker.make("clients.Site")
        self.online = baker.make_recipe("agents.online_agent", site=self.site, _quantity=7)
        self.offline = baker.make_recipe("agents.offline_agent", site=self.site)

    @patch("celery.canvas._chain.apply_async")
    def test_lanes_and_counts(self, apply_async):
        # one already installed: skipped unless reinstall
        RemoteAccessDevice.objects.create(
            agent=self.online[0], hostname="a", status=RemoteAccessStatus.OK
        )
        r = self.client.post(self.url, {"site": self.site.pk}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["started"], 6)
        self.assertEqual(r.data["offline"], 1)
        self.assertEqual(r.data["parallel"], BULK_LANES)
        # 6 computers -> 3 lanes, sequential inside each lane
        self.assertEqual(apply_async.call_count, BULK_LANES)
        # the queued computers keep their status until their turn
        self.assertFalse(
            RemoteAccessDevice.objects.filter(status=RemoteAccessStatus.INSTALLING).exists()
        )
        self.assertEqual(
            RemoteAccessDevice.objects.get(agent=self.online[0]).status,
            RemoteAccessStatus.OK,
        )

        # again: everything is already queued, nothing new starts
        apply_async.reset_mock()
        r = self.client.post(self.url, {"site": self.site.pk, "reinstall": True}, format="json")
        self.assertEqual(r.data["started"], 1)  # only the installed one is new
        self.assertEqual(r.data["queued"], 6)

    def test_requires_a_target(self):
        r = self.client.post(self.url, {}, format="json")
        self.assertEqual(r.status_code, 400)

    @patch("remoteaccess.tasks.provision_remote_access_task")
    def test_step_offline_and_errors_do_not_stop_the_lane(self, provision):
        off = RemoteAccessDevice.objects.create(
            agent=self.offline, hostname="off", status=RemoteAccessStatus.OK
        )
        cache.set(bulk_queued_key(off.pk), True)
        self.assertEqual(bulk_provision_step_task(off.pk, "https://api.x", "john"), "offline")
        off.refresh_from_db()
        self.assertEqual(off.status, RemoteAccessStatus.OK)  # still usable
        self.assertIn("Offline", off.last_error)
        self.assertIsNone(cache.get(bulk_queued_key(off.pk)))
        provision.assert_not_called()

        dev = RemoteAccessDevice.objects.create(agent=self.online[1], hostname="on")
        provision.side_effect = RuntimeError("boom")
        self.assertTrue(bulk_provision_step_task(dev.pk, "https://api.x", "john").startswith("error"))

        provision.side_effect = None
        provision.return_value = "ok"
        self.assertEqual(bulk_provision_step_task(dev.pk, "https://api.x", "john"), "ok")
        dev.refresh_from_db()
        self.assertEqual(dev.status, RemoteAccessStatus.INSTALLING)  # set at its turn
        args = provision.call_args.args
        self.assertEqual(args[0], dev.pk)
        self.assertEqual(len(args[1]), 20)  # password generated at its turn
        self.assertTrue(args[2])


class TestWindowsScripts(TacticalTestCase):
    # Windows 7 has PowerShell 2.0, 8.1 / 2012 R2 have 4.0
    PS3_ONLY = (
        "-NoNewline",
        "Get-FileHash",
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "ConvertTo-Json",
        "ConvertFrom-Json",
        "Get-CimInstance",
        "SecurityProtocolType]::Tls12",
    )

    @staticmethod
    def code(script: str) -> str:
        """the script without its comment lines"""
        return "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))

    def test_host_script_runs_on_old_powershell(self):
        from .scripts import host_script

        script = host_script(
            plat="windows",
            password="Abc123",
            download_url="https://api.x/d",
            sha256="0" * 64,
            install=True,
            record='{"url":"https://api.x/r","token":"t"}',
        )
        for word in self.PS3_ONLY:
            self.assertNotIn(word, self.code(script))
        self.assertIn("TrmmAscii $_.Exception.Message", script)

    def test_combined_installer_runs_on_old_powershell(self):
        from .installer import COMBINED_PS1

        for word in self.PS3_ONLY:
            self.assertNotIn(word, self.code(COMBINED_PS1))
        self.assertNotIn("\t", COMBINED_PS1)

    def test_localized_error_keeps_its_accents(self):
        from .scripts import parse_result

        # what TrmmAscii (PowerShell) prints: 7-bit text with \uXXXX escapes
        out = (
            r"TRMMRA-ERR:N\u00e3o \u00e9 poss\u00edvel localizar um par\u00e2metro "
            r"'NoNewline' em C:\u005cTemp" + "\r\n"
        )
        self.assertTrue(out.isascii())
        ok, remote_id, msg = parse_result(out)
        self.assertFalse(ok)
        self.assertEqual(msg, "Não é possível localizar um parâmetro 'NoNewline' em C:\\Temp")
