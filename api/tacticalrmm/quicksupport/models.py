import secrets
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone as djangotime

CODE_LENGTH = 9
DEFAULT_MESH_GROUP = "TRMM Quick Support"

# MeshCentral device group consent flags
CONSENT_NOTIFY = 0x0001 | 0x0002 | 0x0004  # desktop, terminal, files notify
CONSENT_PROMPT = 0x0008 | 0x0010 | 0x0020  # desktop, terminal, files prompt
CONSENT_PRIVACY_BAR = 0x0040


def make_client_token() -> str:
    return secrets.token_urlsafe(32)


def make_agent_tag() -> str:
    # secret per session, only known by the server and the executable. The
    # mesh device is matched by this tag (the agentName is self-reported and
    # predictable, anyone with the group .msh could fake it)
    return "TRMMQS" + secrets.token_hex(16)


class QuickSupportConfig(models.Model):
    """Singleton with the quick support settings."""

    enabled = models.BooleanField(default=True)
    # embedded in the quick support executable, allows it to open sessions
    client_token = models.CharField(max_length=128, default=make_client_token)
    company_name = models.CharField(max_length=255, blank=True, default="")
    welcome_message = models.TextField(blank=True, default="")
    # ask the remote user to accept each remote desktop/terminal/files session
    require_consent = models.BooleanField(default=False)
    # bar on the remote screen showing it is being accessed (with a disconnect
    # button). In "connect" mode it runs inside the agent process.
    show_privacy_bar = models.BooleanField(default=False)
    # windows notification ("... started a remote desktop session") when the
    # technician opens the desktop, terminal or files
    show_notification = models.BooleanField(default=False)
    # minutes without heartbeat from the executable before a session expires
    session_timeout = models.PositiveIntegerField(default=3)
    mesh_group_name = models.CharField(max_length=128, default=DEFAULT_MESH_GROUP)
    # meshcentral device group id (base64 form, without the "mesh//" prefix)
    mesh_group_id = models.CharField(max_length=255, blank=True, default="")

    def __str__(self) -> str:
        return "Quick Support Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "QuickSupportConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def mesh_consent(self) -> int:
        consent = 0
        if self.show_notification:
            consent |= CONSENT_NOTIFY
        if self.show_privacy_bar:
            consent |= CONSENT_PRIVACY_BAR
        if self.require_consent:
            consent |= CONSENT_PROMPT
        return consent


class QuickSupportStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    CONNECTED = "connected", "Connected"
    CLOSED = "closed", "Closed"


class InstallStatus(models.TextChoices):
    NONE = "", "None"
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    SENT = "sent", "Sent"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    DENIED = "denied", "Denied"


class QuickSupportSessionQuerySet(models.QuerySet):
    def active(self):
        cfg = QuickSupportConfig.load()
        cutoff = djangotime.now() - timedelta(minutes=max(cfg.session_timeout, 1))
        return self.exclude(status=QuickSupportStatus.CLOSED).filter(
            last_seen__gte=cutoff
        )


class QuickSupportSession(models.Model):
    objects = QuickSupportSessionQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=CODE_LENGTH, db_index=True)
    # secret only known by the executable that created the session
    secret = models.CharField(max_length=128)
    agent_tag = models.CharField(max_length=64, default=make_agent_tag)
    status = models.CharField(
        max_length=20,
        choices=QuickSupportStatus.choices,
        default=QuickSupportStatus.WAITING,
    )
    hostname = models.CharField(max_length=255, blank=True, default="")
    username = models.CharField(max_length=255, blank=True, default="")
    os = models.CharField(max_length=255, blank=True, default="")
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    client_version = models.CharField(max_length=50, blank=True, default="")
    # meshcentral node id (hex), filled when the technician connects
    mesh_node_id = models.CharField(max_length=255, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=djangotime.now)
    connected_at = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        "accounts.User",
        related_name="quick_support_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    # "mesh": quick support executable (MeshCentral); "rustdesk": TRMM Desktop QS
    kind = models.CharField(max_length=20, default="mesh")
    # TRMM Desktop id of a "rustdesk" session
    remote_id = models.CharField(max_length=64, blank=True, default="")
    # temporary password shown by the TRMM Desktop quick support (encrypted,
    # remoteaccess.crypto); sent to technicians that connect from the app
    remote_password_enc = models.TextField(blank=True, default="")
    # "install permanent access": requested by the customer, approved by a
    # technician (who chooses the site), then downloaded once by the executable
    install_status = models.CharField(
        max_length=20, choices=InstallStatus.choices, blank=True, default=""
    )
    install_site = models.ForeignKey(
        "clients.Site",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    install_agent_type = models.CharField(max_length=20, default="workstation")
    install_by = models.ForeignKey(
        "accounts.User",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    install_message = models.TextField(blank=True, default="")
    # knox token_key of the installer token (deleted after the install result)
    install_token_key = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ("-created",)

    def __str__(self) -> str:
        return f"{self.code} - {self.hostname}"

    @property
    def mesh_agent_name(self) -> str:
        return f"TRMM-QS-{self.code}"

    @property
    def mesh_agent_tag(self) -> str:
        return self.agent_tag

    @property
    def formatted_code(self) -> str:
        return " ".join(self.code[i : i + 3] for i in range(0, len(self.code), 3))

    @staticmethod
    def generate_code() -> str:
        # unique among sessions that are not closed
        while True:
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
            if code[0] == "0":
                continue
            if (
                not QuickSupportSession.objects.exclude(
                    status=QuickSupportStatus.CLOSED
                )
                .filter(code=code)
                .exists()
            ):
                return code

    def close(self) -> None:
        self.status = QuickSupportStatus.CLOSED
        self.closed_at = djangotime.now()
        self.save(update_fields=["status", "closed_at"])
