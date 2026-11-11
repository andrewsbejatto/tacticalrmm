"""
    python manage.py remoteaccess_diag

Shows the remote access (TRMM Desktop) configuration and device counts.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from remoteaccess.files import files_dir, host_file, sha256
from remoteaccess.models import RemoteAccessConfig, RemoteAccessDevice


class Command(BaseCommand):
    help = "Remote access (TRMM Desktop) diagnostics"

    def handle(self, *args, **kwargs):
        cfg = RemoteAccessConfig.load()
        self.stdout.write("== Settings")
        self.stdout.write(f"enabled:      {cfg.enabled}")
        self.stdout.write(f"id server:    {cfg.id_server or '(empty)'}")
        self.stdout.write(f"relay server: {cfg.relay_server or '(same as id server)'}")
        self.stdout.write(f"public key:   {cfg.public_key or '(empty)'}")

        self.stdout.write(f"\n== Installers ({files_dir()})")
        for plat, arch in (("windows", "x64"), ("linux", "x64")):
            path = host_file(plat, arch)
            self.stdout.write(
                f"{plat}/{arch}: " + (f"{path} sha256={sha256(path)}" if path else "NOT FOUND")
            )

        self.stdout.write("\n== Devices")
        for row in RemoteAccessDevice.objects.values("status").annotate(n=Count("id")):
            self.stdout.write(f"{row['status']}: {row['n']}")
        without_agent = RemoteAccessDevice.objects.filter(agent__isnull=True).count()
        self.stdout.write(f"without tactical agent: {without_agent}")
        unreadable = [
            d.hostname for d in RemoteAccessDevice.objects.exclude(password_enc="") if not d.password
        ]
        if unreadable:
            self.stdout.write(
                "passwords that can't be decrypted (SECRET_KEY changed?): " + ", ".join(unreadable)
            )
