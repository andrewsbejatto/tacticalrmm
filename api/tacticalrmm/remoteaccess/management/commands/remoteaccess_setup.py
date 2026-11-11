"""
    python manage.py remoteaccess_setup --id-server <host> --key <public key> [--relay-server <host:port>]

Saves the RustDesk server used by TRMM Desktop (called by
rustdesk/install-rustdesk-server.sh).
"""

from django.core.management.base import BaseCommand

from remoteaccess.models import RemoteAccessConfig


class Command(BaseCommand):
    help = "Configure the RustDesk server used by TRMM Desktop"

    def add_arguments(self, parser):
        parser.add_argument("--id-server", required=True)
        parser.add_argument("--key", required=True)
        parser.add_argument("--relay-server", default="")

    def handle(self, *args, **kwargs):
        cfg = RemoteAccessConfig.load()
        cfg.id_server = kwargs["id_server"].strip()
        cfg.public_key = kwargs["key"].strip()
        cfg.relay_server = kwargs["relay_server"].strip()
        cfg.save()
        self.stdout.write(
            f"saved: id server {cfg.id_server}, relay {cfg.relay_server or cfg.id_server + ':21117'}"
        )
