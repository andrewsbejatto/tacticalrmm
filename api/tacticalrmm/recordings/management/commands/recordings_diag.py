"""
Session recording diagnostics.

    python manage.py recordings_diag [--test]
"""

import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from recordings.models import Recording, RecordingConfig, RecordingStatus
from recordings.tasks import mesh_dir, spool_dir


class Command(BaseCommand):
    help = "Session recording diagnostics"

    def add_arguments(self, parser):
        parser.add_argument(
            "--test", action="store_true", help="write a test object to the bucket"
        )

    def ok(self, label, value, good=True):
        style = self.style.SUCCESS if good else self.style.ERROR
        self.stdout.write(f"{label:<28} {style(str(value))}")

    def handle(self, *args, **kwargs):
        cfg = RecordingConfig.load()
        self.stdout.write("== Settings")
        self.ok("enabled", cfg.enabled, cfg.enabled)
        self.ok(
            "record mesh / rustdesk / qs",
            f"{cfg.record_mesh} / {cfg.record_rustdesk} / {cfg.record_qs}",
        )
        self.ok("bucket", cfg.s3_bucket or "(not set)", bool(cfg.s3_bucket))
        self.ok("endpoint", cfg.s3_endpoint or "Amazon S3")
        self.ok(
            "credentials", "set" if cfg.s3_configured else "missing", cfg.s3_configured
        )
        try:
            import boto3

            self.ok("boto3", boto3.__version__)
        except ImportError:
            self.ok("boto3", "NOT INSTALLED: /rmm/api/env/bin/pip install boto3", False)

        self.stdout.write("== Folders")
        try:
            spool = spool_dir()
            self.ok("spool", spool, os.access(spool, os.W_OK))
        except OSError as e:
            self.ok("spool", e, False)
        mdir = mesh_dir()
        found = os.path.isdir(mdir)
        self.ok(
            "meshcentral recordings",
            mdir if found else f"{mdir} (not created yet)",
            found,
        )
        if found:
            self.ok(
                "  writable (move files)",
                os.access(mdir, os.W_OK),
                os.access(mdir, os.W_OK),
            )
            files = [f for f in os.listdir(mdir) if f.endswith(".mcrec")]
            self.ok("  waiting .mcrec files", len(files))

        mesh_cfg = getattr(
            settings, "MESHCENTRAL_CONFIG", "/meshcentral/meshcentral-data/config.json"
        )
        try:
            with open(mesh_cfg, encoding="utf-8") as f:
                domain = json.load(f).get("domains", {}).get("", {})
            rec = next(
                (v for k, v in domain.items() if k.lower() == "sessionrecording"), None
            )
            self.ok(
                "meshcentral sessionRecording",
                json.dumps(rec) if rec else "off (run recordings_setup_mesh)",
                bool(rec),
            )
        except (OSError, ValueError) as e:
            self.ok("meshcentral config", e, False)

        self.stdout.write("== Recordings")
        for status in RecordingStatus:
            self.ok(status.label, Recording.objects.filter(status=status).count())
        for rec in Recording.objects.filter(status=RecordingStatus.ERROR).order_by(
            "-updated"
        )[:5]:
            self.ok(f"  #{rec.pk} {rec.hostname}", rec.last_error[:120], False)

        if kwargs["test"]:
            from recordings import s3

            self.stdout.write("== Bucket test")
            try:
                self.ok("result", s3.test(cfg, []))
            except s3.StorageError as e:
                self.ok("result", e, False)
