"""
Remote access with the TRMM Desktop host (built on RustDesk).

Every managed computer can have a "device" with the RustDesk id and a fixed
password (stored encrypted). A device is normally linked to a tactical agent,
but it can also exist without one: when the tactical/mesh agent fails to
install, the combined installer still installs the host and enrolls it.
"""

from django.db import models

from .crypto import decrypt, encrypt


class RemoteAccessConfig(models.Model):
    """Singleton with the remote access settings."""

    enabled = models.BooleanField(default=True)
    # rustdesk id server (hbbs), "host" or "host:port"
    id_server = models.CharField(max_length=255, blank=True, default="")
    # rustdesk relay server (hbbr), empty = same host as the id server
    relay_server = models.CharField(max_length=255, blank=True, default="")
    # public key of hbbs (content of id_ed25519.pub)
    public_key = models.CharField(max_length=255, blank=True, default="")
    # Ed25519 key that signs the config embedded in TRMM Desktop executables
    # (encrypted, remoteaccess.crypto); its public key goes into the app build
    signing_key_enc = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return "Remote Access Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "RemoteAccessConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class RemoteAccessStatus(models.TextChoices):
    NOT_INSTALLED = "not_installed", "Not installed"
    INSTALLING = "installing", "Installing"
    OK = "ok", "Ok"
    ERROR = "error", "Error"


class RemoteAccessDevice(models.Model):
    agent = models.OneToOneField(
        "agents.Agent",
        related_name="remote_access",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # used when there is no agent (enrolled by the combined installer)
    client = models.ForeignKey(
        "clients.Client",
        related_name="remote_access_devices",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    site = models.ForeignKey(
        "clients.Site",
        related_name="remote_access_devices",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    hostname = models.CharField(max_length=255, blank=True, default="")
    os = models.CharField(max_length=255, blank=True, default="")
    remote_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    password_enc = models.TextField(blank=True, default="")
    version = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=RemoteAccessStatus.choices,
        default=RemoteAccessStatus.NOT_INSTALLED,
    )
    last_error = models.TextField(blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("hostname",)

    def __str__(self) -> str:
        return f"{self.hostname} ({self.remote_id or '-'})"

    @property
    def password(self) -> str:
        return decrypt(self.password_enc) if self.password_enc else ""

    @password.setter
    def password(self, value: str) -> None:
        self.password_enc = encrypt(value) if value else ""

    @property
    def effective_site(self):
        return self.agent.site if self.agent_id else self.site

    @property
    def effective_client(self):
        site = self.effective_site
        return site.client if site else self.client
