"""
Turns MeshCentral's server-side recording of desktop sessions on (or off).

    python manage.py recordings_setup_mesh            # on
    python manage.py recordings_setup_mesh --disable  # off
    sudo systemctl restart meshcentral

Edits domains."".sessionRecording in MeshCentral's config.json (a backup is
saved next to it). TRMM's update.sh keeps this block (it only patches other
keys with jq). Files go to MeshCentral's default folder
(meshcentral-recordings); the recordings task uploads them to the bucket and
deletes them.
"""

import json
import os
import shutil
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_CONFIG = "/meshcentral/meshcentral-data/config.json"


class Command(BaseCommand):
    help = "Enable/disable MeshCentral desktop session recording"

    def add_arguments(self, parser):
        parser.add_argument("--disable", action="store_true")
        parser.add_argument("--config", default=None, help="MeshCentral config.json")

    def handle(self, *args, **kwargs):
        path = kwargs["config"] or getattr(
            settings, "MESHCENTRAL_CONFIG", DEFAULT_CONFIG
        )
        if not os.path.isfile(path):
            raise CommandError(f"{path} not found (set MESHCENTRAL_CONFIG or --config)")
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            raise CommandError(f"unable to read {path}: {e}")

        domains = cfg.setdefault("domains", {})
        domain = domains.setdefault("", {})
        # MeshCentral ignores the case of the keys: drop other spellings
        for key in [k for k in domain if k.lower() == "sessionrecording"]:
            del domain[key]
        if not kwargs["disable"]:
            # 2 = desktop only (terminal and files are not recorded)
            domain["sessionRecording"] = {"protocols": [2], "index": True}

        backup = f"{path}.{time.strftime('%Y%m%d%H%M%S')}.bak"
        shutil.copy2(path, backup)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)

        state = "disabled" if kwargs["disable"] else "enabled (desktop sessions)"
        self.stdout.write(self.style.SUCCESS(f"MeshCentral session recording {state}."))
        self.stdout.write(f"Backup: {backup}")
        self.stdout.write("Apply with: sudo systemctl restart meshcentral")
