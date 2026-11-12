"""
Video recordings of remote sessions, stored in an S3 compatible bucket.

Sources:
- mesh: MeshCentral records the desktop sessions on this server (.mcrec,
  domains."".sessionRecording in its config.json); a periodic task picks the
  finished files up.
- rustdesk / qs_rustdesk: TRMM Desktop is end-to-end encrypted, so the
  computer being accessed records the session itself (.webm) and streams the
  file to this server while the session runs (RecordUpload view).

The local copy is deleted as soon as the upload to the bucket is confirmed.
"""

import hashlib
import secrets

from django.db import models

from remoteaccess.crypto import decrypt, encrypt


class RecordingConfig(models.Model):
    """Singleton with the recording settings."""

    enabled = models.BooleanField(default=False)
    record_mesh = models.BooleanField(default=True)
    record_rustdesk = models.BooleanField(default=True)
    record_qs = models.BooleanField(default=True)
    # S3: empty endpoint = Amazon S3; otherwise Wasabi, Backblaze B2, MinIO...
    s3_endpoint = models.CharField(max_length=255, blank=True, default="")
    s3_region = models.CharField(max_length=64, blank=True, default="")
    s3_bucket = models.CharField(max_length=255, blank=True, default="")
    s3_prefix = models.CharField(max_length=255, blank=True, default="recordings")
    s3_access_key = models.CharField(max_length=255, blank=True, default="")
    s3_secret_enc = models.TextField(blank=True, default="")
    s3_path_style = models.BooleanField(default=False)
    s3_sse = models.BooleanField(default=True)
    # lifetime of the playback links
    presign_minutes = models.PositiveIntegerField(default=15)
    max_file_mb = models.PositiveIntegerField(default=4096)

    def __str__(self) -> str:
        return "Recording Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "RecordingConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def s3_secret(self) -> str:
        return decrypt(self.s3_secret_enc) if self.s3_secret_enc else ""

    @s3_secret.setter
    def s3_secret(self, value: str) -> None:
        self.s3_secret_enc = encrypt(value) if value else ""

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_bucket and self.s3_access_key and self.s3_secret_enc)


class RecordingSource(models.TextChoices):
    MESH = "mesh", "MeshCentral"
    RUSTDESK = "rustdesk", "TRMM Desktop"
    QS_RUSTDESK = "qs_rustdesk", "TRMM Desktop Quick Support"


class RecordingStatus(models.TextChoices):
    # rustdesk file still being received
    RECEIVING = "receiving", "Receiving"
    # complete on this server, waiting for the upload
    PENDING = "pending", "Pending upload"
    UPLOADED = "uploaded", "Uploaded"
    ERROR = "error", "Error"


class Recording(models.Model):
    source = models.CharField(max_length=20, choices=RecordingSource.choices)
    status = models.CharField(
        max_length=20, choices=RecordingStatus.choices, default=RecordingStatus.PENDING
    )
    agent = models.ForeignKey(
        "agents.Agent",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    device = models.ForeignKey(
        "remoteaccess.RemoteAccessDevice",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    qs_session = models.ForeignKey(
        "quicksupport.QuickSupportSession",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # snapshot when recorded: the agent may move or be deleted later
    client = models.ForeignKey(
        "clients.Client",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    site = models.ForeignKey(
        "clients.Site",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    hostname = models.CharField(max_length=255, blank=True, default="")
    technician = models.ForeignKey(
        "accounts.User",
        related_name="recordings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    technician_name = models.CharField(max_length=255, blank=True, default="")
    started = models.DateTimeField(db_index=True)
    duration = models.PositiveIntegerField(default=0)  # seconds
    size = models.BigIntegerField(default=0)
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    # file on this server while it is received / waiting for the upload
    local_path = models.CharField(max_length=1024, blank=True, default="")
    s3_key = models.CharField(max_length=1024, blank=True, default="")
    sha256 = models.CharField(max_length=64, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-started",)

    def __str__(self) -> str:
        return f"{self.hostname} {self.started:%Y-%m-%d %H:%M} ({self.source})"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RecordingToken(models.Model):
    """
    Credential of a computer that uploads its recordings (TRMM Desktop host
    or quick support session). Only the sha256 is stored.
    """

    token_hash = models.CharField(max_length=64, unique=True)
    device = models.ForeignKey(
        "remoteaccess.RemoteAccessDevice",
        related_name="recording_tokens",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    qs_session = models.ForeignKey(
        "quicksupport.QuickSupportSession",
        related_name="recording_tokens",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    expires = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, *, device=None, qs_session=None, expires=None) -> str:
        """New token (replaces the previous one of the device / session)."""
        if device is not None:
            cls.objects.filter(device=device).delete()
        if qs_session is not None:
            cls.objects.filter(qs_session=qs_session).delete()
        token = secrets.token_urlsafe(32)
        cls.objects.create(
            token_hash=hash_token(token),
            device=device,
            qs_session=qs_session,
            expires=expires,
        )
        return token


class RemoteConnectLog(models.Model):
    """
    Who asked for the credentials of a TRMM Desktop host: the host doesn't
    know the technician, its recordings are attributed to the last request.
    """

    device = models.ForeignKey(
        "remoteaccess.RemoteAccessDevice",
        related_name="connect_logs",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        "accounts.User",
        related_name="+",
        null=True,
        on_delete=models.SET_NULL,
    )
    username = models.CharField(max_length=255, blank=True, default="")
    time = models.DateTimeField(auto_now_add=True, db_index=True)
