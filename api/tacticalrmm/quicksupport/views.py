import base64
import json
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone as djangotime
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.helpers import notify_error
from tacticalrmm.logger import logger

from .mesh import (
    MESH_AGENT_IDS,
    MeshError,
    b64_node_to_hex,
    ensure_mesh_group,
    file_sha256,
    find_session_node,
    get_login_url,
    get_mesh_agent_binary,
    get_session_msh,
    grant_user_on_node,
    hex_to_b64_node,
    remove_node,
)
from .installer import InstallerError, build_windows_installer
from .models import (
    InstallStatus,
    QuickSupportConfig,
    QuickSupportSession,
    QuickSupportStatus,
)
from .permissions import QuickSupportPerms, QuickSupportSettingsPerms
from .serializers import QuickSupportConfigSerializer, QuickSupportSessionSerializer

HEARTBEAT_INTERVAL = 30  # seconds
MAX_SESSIONS_PER_IP = 10
CONFIG_BEGIN = b"\r\n--TRMMQS-CONFIG-BEGIN--\r\n"
CONFIG_END = b"\r\n--TRMMQS-CONFIG-END--\r\n"


# ---------------------------------------------------------------------------
# endpoints used by the quick support executable (no user authentication)
# ---------------------------------------------------------------------------


def _real_ip(request) -> str:
    # tactical's nginx talks to the api with uwsgi_pass (install.sh and docker),
    # so REMOTE_ADDR is the address of the tcp connection. Don't use
    # X-Forwarded-For / X-Real-IP: in that setup nginx doesn't set them and
    # they come straight from the client, which would allow bypassing the
    # rate limits below
    return (request.META.get("REMOTE_ADDR") or "").strip()


class QuickSupportClientThrottle(SimpleRateThrottle):
    """Per ip limit for the endpoints used by the executable."""

    scope = "quicksupport_client"
    rate = "30/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _real_ip(request)}


class QuickSupportGlobalThrottle(SimpleRateThrottle):
    """Upper limit of new sessions for the whole server (token leaked/abused)."""

    scope = "quicksupport_global"
    rate = "120/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": "all"}


class QuickSupportConnectThrottle(UserRateThrottle):
    scope = "quicksupport_connect"
    rate = "30/minute"


def _check_client_token(request) -> QuickSupportConfig | None:
    cfg = QuickSupportConfig.load()
    token = request.headers.get("X-QuickSupport-Token", "")
    if not cfg.enabled or not token:
        return None
    if not secrets.compare_digest(token, cfg.client_token):
        return None
    return cfg


def _get_client_session(request, pk) -> QuickSupportSession | None:
    secret = request.headers.get("X-QuickSupport-Session", "")
    session = QuickSupportSession.objects.filter(pk=pk).first()
    if session is None or not secret:
        return None
    if not secrets.compare_digest(secret, session.secret):
        return None
    return session


def _trunc(value, length=255) -> str:
    return str(value or "")[:length]


def _revoke_install_token(session) -> None:
    """the installer token of a quick support install is not needed anymore"""
    if session.install_token_key:
        from knox.models import AuthToken

        AuthToken.objects.filter(token_key=session.install_token_key).delete()
        session.install_token_key = ""
        session.save(update_fields=["install_token_key"])


def _purge_old_sessions() -> None:
    # sessions can be created without a user login, keep the table small
    now = djangotime.now()
    QuickSupportSession.objects.filter(created__lt=now - timedelta(days=90)).delete()
    QuickSupportSession.objects.exclude(status=QuickSupportStatus.CLOSED).filter(
        last_seen__lt=now - timedelta(days=1)
    ).update(status=QuickSupportStatus.CLOSED, closed_at=now)


_agent_hashes: dict[tuple[str, float], str] = {}


def _agent_sha256(arch: str) -> str:
    path = get_mesh_agent_binary(arch)
    key = (path, os.path.getmtime(path))
    if key not in _agent_hashes:
        _agent_hashes.clear()
        _agent_hashes[key] = file_sha256(path)
    return _agent_hashes[key]


class ClientSessions(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle, QuickSupportGlobalThrottle]

    # executable started: open a session and get a code
    def post(self, request):
        cfg = _check_client_token(request)
        if cfg is None:
            return Response({"error": "Quick support is disabled"}, status=403)

        _purge_old_sessions()

        client_ip = _real_ip(request) or None
        if (
            client_ip
            and QuickSupportSession.objects.active().filter(client_ip=client_ip).count()
            >= MAX_SESSIONS_PER_IP
        ):
            return Response({"error": "Too many open sessions"}, status=429)

        arch = str(request.data.get("arch", "amd64"))
        if arch not in MESH_AGENT_IDS:
            return Response({"error": "Unsupported architecture"}, status=400)

        session = QuickSupportSession(
            code=QuickSupportSession.generate_code(),
            secret=secrets.token_urlsafe(32),
            hostname=_trunc(request.data.get("hostname")),
            username=_trunc(request.data.get("username")),
            os=_trunc(request.data.get("os")),
            client_version=_trunc(request.data.get("version"), 50),
            client_ip=_real_ip(request) or None,
        )

        try:
            msh = get_session_msh(cfg, session)
            agent_sha256 = _agent_sha256(arch)
        except MeshError as e:
            logger.error(f"Quick support: {e}")
            return Response({"error": "Server error, try again later"}, status=503)

        session.save()
        return Response(
            {
                "session_id": str(session.pk),
                "session_secret": session.secret,
                "code": session.code,
                "formatted_code": session.formatted_code,
                "msh": msh,
                "tag": session.mesh_agent_tag,
                # the executable checks the downloaded agent against this hash
                "agent_sha256": agent_sha256,
                "heartbeat_interval": HEARTBEAT_INTERVAL,
                "company_name": cfg.company_name,
                "welcome_message": cfg.welcome_message,
            }
        )


class ClientSessionHeartbeat(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def post(self, request, pk):
        session = _get_client_session(request, pk)
        if session is None:
            return Response({"error": "Invalid session"}, status=404)

        if session.status != QuickSupportStatus.CLOSED:
            session.last_seen = djangotime.now()
            session.save(update_fields=["last_seen"])

        return Response(
            {
                "status": session.status,
                "install_status": session.install_status,
                "technician": (
                    session.connected_by.get_full_name()
                    or session.connected_by.username
                    if session.connected_by
                    else ""
                ),
            }
        )


class ClientSessionClose(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def post(self, request, pk):
        session = _get_client_session(request, pk)
        if session is None:
            return Response({"error": "Invalid session"}, status=404)

        if session.status != QuickSupportStatus.CLOSED:
            session.close()
            if session.mesh_node_id:
                remove_node(hex_to_b64_node(session.mesh_node_id))

        return Response("ok")


class ClientInstallRequest(APIView):
    """The customer asks for permanent access (button in the quick support)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def post(self, request, pk):
        session = _get_client_session(request, pk)
        if session is None or session.status == QuickSupportStatus.CLOSED:
            return Response({"error": "Invalid session"}, status=404)
        if session.install_status not in (
            InstallStatus.APPROVED,
            InstallStatus.SENT,
            InstallStatus.DONE,
        ):
            session.install_status = InstallStatus.REQUESTED
            session.install_message = ""
            session.last_seen = djangotime.now()
            session.save(
                update_fields=["install_status", "install_message", "last_seen"]
            )
        return Response({"install_status": session.install_status})


class ClientInstaller(APIView):
    """
    Installer (Tactical RMM agent, installer.ps1) of an approved session,
    downloaded once by the executable that owns the session (session secret).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def get(self, request, pk):
        session = _get_client_session(request, pk)
        if session is None or session.status == QuickSupportStatus.CLOSED:
            return Response({"error": "Invalid session"}, status=404)
        if session.install_site is None:
            return Response({"error": "Installation not approved"}, status=409)
        # one time only, atomic (parallel requests get one script at most)
        taken = QuickSupportSession.objects.filter(
            pk=session.pk, install_status=InstallStatus.APPROVED
        ).update(install_status=InstallStatus.SENT)
        if taken != 1:
            return Response({"error": "Installation not approved"}, status=409)

        data = {
            "agenttype": session.install_agent_type,
            "goarch": (
                "amd64" if request.query_params.get("arch", "amd64") != "386" else "386"
            ),
            "expires": 2,
        }
        created: list = []
        try:
            script = build_windows_installer(
                _api_url(request), session.install_site, data, created=created
            )
        except InstallerError as e:
            session.install_status = InstallStatus.FAILED
            session.install_message = str(e)
            session.save(update_fields=["install_status", "install_message"])
            return Response({"error": str(e)}, status=503)

        session.install_token_key = created[0].token_key if created else ""
        session.save(update_fields=["install_token_key"])
        response = HttpResponse(script, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="trmm-install.ps1"'
        return response


class ClientInstallResult(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def post(self, request, pk):
        session = _get_client_session(request, pk)
        if session is None or session.install_status != InstallStatus.SENT:
            return Response({"error": "Invalid session"}, status=404)
        ok = request.data.get("ok") is True
        session.install_status = InstallStatus.DONE if ok else InstallStatus.FAILED
        # client text: printable chars only, short (shown in the panel and audit log)
        message = "".join(
            c for c in str(request.data.get("message") or "") if c.isprintable()
        )
        session.install_message = message[:300]
        session.save(update_fields=["install_status", "install_message"])
        _revoke_install_token(session)
        AuditLog.objects.create(
            username=(
                session.install_by.username if session.install_by else "quicksupport"
            ),
            agent=session.hostname or None,
            object_type=AuditObjType.AGENT,
            action=AuditActionType.AGENT_INSTALL,
            message=(
                f"Quick support (code {session.code}) on {session.hostname}: permanent "
                f"access install {'finished' if ok else 'failed'} "
                f"(reported by the quick support client: {session.install_message})."
            )[:1000],
        )
        return Response("ok")


class ClientMeshAgent(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [QuickSupportClientThrottle]

    def get(self, request):
        if _check_client_token(request) is None:
            return Response({"error": "Quick support is disabled"}, status=403)

        arch = request.query_params.get("arch", "amd64")
        try:
            path = get_mesh_agent_binary(arch)
        except MeshError as e:
            logger.error(f"Quick support: {e}")
            return Response({"error": str(e)}, status=503)

        return FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename="meshagent.exe",
            content_type="application/octet-stream",
        )


# ---------------------------------------------------------------------------
# endpoints used by the dashboard
# ---------------------------------------------------------------------------


def _is_restricted(user) -> bool:
    """Users whose role only allows some clients/sites."""
    role = user.get_and_set_role_cache()
    if user.is_superuser or (role and role.is_superuser):
        return False
    if not role:
        return True
    return role.can_view_clients.exists() or role.can_view_sites.exists()


def _audit(request, message: str, hostname: str = "") -> None:
    AuditLog.objects.create(
        username=request.user.username,
        agent=hostname or None,
        object_type=AuditObjType.AGENT if hostname else AuditObjType.CORE,
        action=AuditActionType.REMOTE_SESSION if hostname else AuditActionType.MODIFY,
        message=message,
        debug_info={"ip": getattr(request, "_client_ip", "")},
    )


class Sessions(APIView):
    permission_classes = [IsAuthenticated, QuickSupportPerms]

    # active (waiting or connected) sessions
    def get(self, request):
        sessions = QuickSupportSession.objects.active().select_related("connected_by")
        # users limited to some clients/sites only see their own sessions: the
        # code is what the customer gives to "his" technician, so it must not
        # be listed to technicians that don't work for that customer
        if _is_restricted(request.user):
            sessions = sessions.filter(connected_by=request.user)
        return Response(QuickSupportSessionSerializer(sessions, many=True).data)


class EndSession(APIView):
    permission_classes = [IsAuthenticated, QuickSupportPerms]

    def post(self, request, pk):
        session = get_object_or_404(QuickSupportSession, pk=pk)
        if _is_restricted(request.user) and session.connected_by_id != request.user.pk:
            return notify_error("You can only end your own quick support sessions")
        session.close()
        if session.mesh_node_id:
            remove_node(hex_to_b64_node(session.mesh_node_id))
        _audit(
            request,
            f"{request.user.username} ended the quick support session "
            f"(code {session.code}) to {session.hostname}.",
            session.hostname or "-",
        )
        return Response("ok")


class ApproveInstall(APIView):
    """
    Technician approves (choosing the site) or denies the permanent access
    install of a quick support session.
    """

    permission_classes = [IsAuthenticated, QuickSupportPerms]

    def post(self, request, pk):
        from clients.models import Site
        from tacticalrmm.permissions import _has_perm, _has_perm_on_site

        session = get_object_or_404(QuickSupportSession, pk=pk)
        if session.status == QuickSupportStatus.CLOSED:
            return notify_error("The quick support session is closed")
        if not _has_perm(request, "can_install_agents"):
            return Response({"detail": "Permission denied"}, status=403)
        if _is_restricted(request.user) and session.connected_by_id not in (
            None,
            request.user.pk,
        ):
            return Response({"detail": "Permission denied"}, status=403)
        if session.install_status in (InstallStatus.SENT, InstallStatus.DONE):
            return notify_error("The installation was already sent to this computer")

        if request.data.get("approve", True) is False:
            session.install_status = InstallStatus.DENIED
            session.save(update_fields=["install_status"])
            return Response("ok")

        site = get_object_or_404(
            Site.objects.select_related("client"), pk=request.data.get("site")
        )
        if not _has_perm_on_site(request.user, site.pk):
            return Response({"detail": "Permission denied"}, status=403)

        agent_type = request.data.get("agenttype", "workstation")
        session.install_status = InstallStatus.APPROVED
        session.install_site = site
        session.install_agent_type = (
            agent_type if agent_type in ("workstation", "server") else "workstation"
        )
        session.install_by = request.user
        session.install_message = ""
        if session.connected_by is None:
            session.connected_by = request.user
        session.save(
            update_fields=[
                "install_status",
                "install_site",
                "install_agent_type",
                "install_by",
                "install_message",
                "connected_by",
            ]
        )
        _audit(
            request,
            f"{request.user.username} approved the permanent access install of the quick "
            f"support session (code {session.code}) on {session.hostname} "
            f"({site.client.name} / {site.name}).",
            session.hostname or "-",
        )
        return Response("ok")


class Connect(APIView):
    permission_classes = [IsAuthenticated, QuickSupportPerms]
    throttle_classes = [QuickSupportConnectThrottle]

    def post(self, request):
        code = "".join(c for c in str(request.data.get("code", "")) if c.isdigit())
        if not code:
            return notify_error("Enter the support code")

        session = QuickSupportSession.objects.active().filter(code=code).first()
        if session is None:
            return notify_error("No active quick support session found with this code")

        cfg = QuickSupportConfig.load()
        try:
            node = find_session_node(cfg, session)
            if node is None or not (node.get("conn", 0) & 1):
                return notify_error(
                    "The remote computer is still connecting, try again in a few seconds"
                )
            grant_user_on_node(node["_id"], request.user)
        except MeshError as e:
            return notify_error(str(e))

        node_hex = b64_node_to_hex(node["_id"])
        session.mesh_node_id = node_hex
        session.status = QuickSupportStatus.CONNECTED
        session.connected_by = request.user
        session.connected_at = djangotime.now()
        session.save(
            update_fields=["mesh_node_id", "status", "connected_by", "connected_at"]
        )

        _audit(
            request,
            f"{request.user.username} started a quick support session "
            f"(code {session.code}) to {session.hostname} ({session.username}, "
            f"ip {session.client_ip}).",
            session.hostname or "-",
        )

        return Response(
            {
                "session": QuickSupportSessionSerializer(session).data,
                "control": get_login_url(request.user, node_hex, 11),
                "terminal": get_login_url(request.user, node_hex, 12),
                "file": get_login_url(request.user, node_hex, 13),
            }
        )


class Settings(APIView):
    permission_classes = [IsAuthenticated, QuickSupportSettingsPerms]

    def get(self, request):
        cfg = QuickSupportConfig.load()
        return Response(
            {
                **QuickSupportConfigSerializer(cfg).data,
                "client_available": os.path.exists(_client_exe_path()),
            }
        )

    def put(self, request):
        cfg = QuickSupportConfig.load()
        serializer = QuickSupportConfigSerializer(
            instance=cfg, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        cfg = serializer.save()
        _audit(
            request,
            f"{request.user.username} changed the quick support settings: "
            f"{dict(serializer.validated_data)}",
        )

        try:
            # recreates the group if it was deleted in meshcentral, and applies
            # name, flags and consent (privacy bar etc) when they differ
            ensure_mesh_group(cfg)
        except MeshError as e:
            return notify_error(str(e))

        return Response("ok")


class ResetClientToken(APIView):
    permission_classes = [IsAuthenticated, QuickSupportSettingsPerms]

    def post(self, request):
        from .models import make_client_token

        cfg = QuickSupportConfig.load()
        cfg.client_token = make_client_token()
        cfg.save(update_fields=["client_token"])
        _audit(request, f"{request.user.username} reset the quick support token.")
        return Response("ok")


def _client_exe_path() -> str:
    return getattr(
        settings,
        "QUICKSUPPORT_CLIENT_EXE",
        os.path.join(settings.EXE_DIR, "trmm-quicksupport.exe"),
    )


def _api_url(request) -> str:
    url = getattr(settings, "QUICKSUPPORT_API_URL", None)
    if url:
        return url.rstrip("/")
    scheme = "http" if settings.DEBUG and not request.is_secure() else "https"
    return f"{scheme}://{request.get_host()}"


class DownloadClient(APIView):
    """
    Returns the generic quick support executable with this server's settings
    appended to the end of the file (read by the executable at startup).
    """

    permission_classes = [IsAuthenticated, QuickSupportPerms]

    def get(self, request):
        path = _client_exe_path()
        if not os.path.exists(path):
            return notify_error(
                f"Quick support executable not found on the server ({path}). "
                "Build it from quicksupport-client/ and copy it there."
            )

        cfg = QuickSupportConfig.load()
        if not cfg.enabled:
            return notify_error("Quick support is disabled")

        config = {
            "api": _api_url(request),
            "token": cfg.client_token,
            "company": cfg.company_name,
            "insecure": bool(getattr(settings, "QUICKSUPPORT_INSECURE_TLS", False)),
        }
        payload = base64.b64encode(json.dumps(config).encode())

        with open(path, "rb") as f:
            exe = f.read()

        filename = getattr(settings, "QUICKSUPPORT_CLIENT_FILENAME", "QuickSupport.exe")
        response = HttpResponse(
            exe + CONFIG_BEGIN + payload + CONFIG_END,
            content_type="application/vnd.microsoft.portable-executable",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
