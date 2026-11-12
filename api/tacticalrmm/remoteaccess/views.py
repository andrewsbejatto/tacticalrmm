import os
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from agents.models import Agent
from clients.models import Site
from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.helpers import notify_error
from tacticalrmm.permissions import _has_perm, _has_perm_on_site

from .crypto import make_password
from .installer import InstallerError, build_linux, build_windows
from .files import api_url, check_download_token, check_enroll_ticket, host_file
from .models import RemoteAccessConfig, RemoteAccessDevice, RemoteAccessStatus
from .permissions import RemoteAccessPerms, RemoteAccessSettingsPerms, can_access_device
from .scripts import embedded_config
from .serializers import RemoteAccessConfigSerializer, RemoteAccessDeviceSerializer
from .tasks import provision_remote_access_task

DEFAULT_KEY_FILE = "/var/lib/rustdesk-server/id_ed25519.pub"


def _audit(request, message: str, hostname: str = "", agent_id: str | None = None):
    AuditLog.objects.create(
        username=request.user.username,
        agent=hostname or None,
        agent_id=agent_id,
        object_type=AuditObjType.AGENT,
        action=AuditActionType.REMOTE_SESSION,
        message=message,
        debug_info={"ip": getattr(request, "_client_ip", "")},
    )


def link_devices_without_agent() -> None:
    """Devices enrolled before the tactical agent existed: link by hostname + site."""
    for device in RemoteAccessDevice.objects.filter(agent__isnull=True).exclude(
        hostname=""
    ):
        agent = (
            Agent.objects.filter(hostname__iexact=device.hostname, site_id=device.site_id)
            .filter(remote_access__isnull=True)
            .first()
        )
        if agent:
            device.agent = agent
            device.save(update_fields=["agent", "updated"])


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


class EnrollThrottle(UserRateThrottle):
    rate = "30/min"


class DownloadThrottle(AnonRateThrottle):
    rate = "30/min"


class Settings(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessSettingsPerms]

    def get(self, request):
        cfg = RemoteAccessConfig.load()
        key_file = getattr(settings, "REMOTEACCESS_SERVER_KEY_FILE", DEFAULT_KEY_FILE)
        if not cfg.public_key and os.path.isfile(key_file):
            # rustdesk server installed on this machine: use its key
            cfg.public_key = Path(key_file).read_text().strip()
            cfg.save(update_fields=["public_key"])
        return Response(
            {
                **RemoteAccessConfigSerializer(cfg).data,
                "installers": {
                    f"{plat}-{arch}": bool(host_file(plat, arch))
                    for plat, arch in (("windows", "x64"), ("linux", "x64"))
                },
            }
        )

    def put(self, request):
        cfg = RemoteAccessConfig.load()
        serializer = RemoteAccessConfigSerializer(
            instance=cfg, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response("ok")


# ---------------------------------------------------------------------------
# technician endpoints
# ---------------------------------------------------------------------------


class Server(APIView):
    """RustDesk server of this TRMM (public data) for the TRMM Desktop app."""

    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request):
        cfg = RemoteAccessConfig.load()
        return Response(
            {
                "enabled": cfg.enabled,
                "id_server": cfg.id_server,
                "relay_server": cfg.relay_server,
                "key": cfg.public_key,
            }
        )


class Devices(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request):
        link_devices_without_agent()
        qs = RemoteAccessDevice.objects.select_related(
            "agent__site__client", "site__client", "client"
        )
        try:
            site = int(request.query_params["site"]) if request.query_params.get("site") else None
            client = int(request.query_params["client"]) if request.query_params.get("client") else None
        except ValueError:
            return notify_error("Invalid site or client")
        if site:
            qs = qs.filter(Q(agent__site_id=site) | Q(site_id=site))
        elif client:
            qs = qs.filter(
                Q(agent__site__client_id=client) | Q(site__client_id=client) | Q(client_id=client)
            )
        if request.query_params.get("without_agent") == "true":
            qs = qs.filter(agent__isnull=True)
        devices = [d for d in qs if can_access_device(request.user, d)]
        return Response(RemoteAccessDeviceSerializer(devices, many=True).data)


class AgentDevice(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request, agent_id):
        agent = get_object_or_404(Agent, agent_id=agent_id)
        device = RemoteAccessDevice.objects.filter(agent=agent).first()
        if device is None:
            return Response(
                {"agent_id": agent_id, "status": RemoteAccessStatus.NOT_INSTALLED}
            )
        return Response(RemoteAccessDeviceSerializer(device).data)


def _start_provision(request, agent: Agent, install: bool) -> RemoteAccessDevice:
    device, _ = RemoteAccessDevice.objects.get_or_create(
        agent=agent, defaults={"hostname": agent.hostname}
    )
    device.status = RemoteAccessStatus.INSTALLING
    device.last_error = ""
    device.save(update_fields=["status", "last_error", "updated"])
    provision_remote_access_task.delay(
        device.pk, make_password(), install, api_url(request), request.user.username
    )
    return device


class Provision(APIView):
    """Installs (or repairs) the TRMM Desktop host through the agent."""

    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def post(self, request, agent_id):
        agent = get_object_or_404(Agent, agent_id=agent_id)
        if agent.plat not in ("windows", "linux"):
            return notify_error("Remote access is only available for Windows and Linux")
        if agent.status != "online":
            return notify_error("The agent is offline")
        device = _start_provision(request, agent, install=True)
        return Response(RemoteAccessDeviceSerializer(device).data)


class ChangePassword(APIView):
    """Sets a new fixed password on the host (it must be installed)."""

    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def post(self, request, agent_id):
        agent = get_object_or_404(Agent, agent_id=agent_id)
        if agent.status != "online":
            return notify_error("The agent is offline")
        device = _start_provision(request, agent, install=False)
        return Response(RemoteAccessDeviceSerializer(device).data)


class BulkProvision(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def post(self, request):
        agents = Agent.objects.filter_by_role(request.user).filter(  # type: ignore
            plat__in=["windows", "linux"]
        )
        if request.data.get("agent_ids"):
            agents = agents.filter(agent_id__in=request.data["agent_ids"])
        elif request.data.get("site"):
            agents = agents.filter(site_id=request.data["site"])
        elif request.data.get("client"):
            agents = agents.filter(site__client_id=request.data["client"])
        elif not request.data.get("all"):
            return notify_error("Select the agents")

        if not request.data.get("reinstall"):
            agents = agents.exclude(remote_access__status=RemoteAccessStatus.OK)

        started = offline = 0
        for agent in agents.only("pk", "agent_id", "hostname", "plat", "last_seen", "offline_time", "overdue_time"):
            if agent.status != "online":
                offline += 1
                continue
            _start_provision(request, agent, install=True)
            started += 1
        return Response({"started": started, "offline": offline})


def _connect_response(request, device: RemoteAccessDevice):
    if device.status != RemoteAccessStatus.OK or not device.remote_id:
        return notify_error("Remote access is not installed on this computer")
    password = device.password
    if not password:
        return notify_error("The stored password can't be read, change the password")
    cfg = RemoteAccessConfig.load()
    from recordings.models import RemoteConnectLog

    RemoteConnectLog.objects.create(
        device=device, user=request.user, username=request.user.username
    )
    _audit(
        request,
        f"{request.user.username} started a remote access session "
        f"(TRMM Desktop id {device.remote_id}) to {device.hostname}.",
        device.hostname,
        device.agent.agent_id if device.agent_id else None,
    )
    return Response(
        {
            "remote_id": device.remote_id,
            "password": password,
            "id_server": cfg.id_server,
            "relay_server": cfg.relay_server,
            "key": cfg.public_key,
            "hostname": device.hostname,
            # title of the remote window in the app: client - site - hostname
            "client": device.effective_client.name if device.effective_client else "",
            "site": device.effective_site.name if device.effective_site else "",
        }
    )


class AgentConnect(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request, agent_id):
        agent = get_object_or_404(Agent, agent_id=agent_id)
        device = get_object_or_404(RemoteAccessDevice, agent=agent)
        return _connect_response(request, device)


class DeviceConnect(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request, pk):
        device = get_object_or_404(RemoteAccessDevice, pk=pk)
        if not can_access_device(request.user, device):
            return Response({"detail": "Permission denied"}, status=403)
        return _connect_response(request, device)


class DeviceDelete(APIView):
    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def delete(self, request, pk):
        device = get_object_or_404(RemoteAccessDevice, pk=pk)
        if not can_access_device(request.user, device):
            return Response({"detail": "Permission denied"}, status=403)
        _audit(
            request,
            f"{request.user.username} removed the remote access record of "
            f"{device.hostname} (TRMM Desktop id {device.remote_id}).",
            device.hostname,
            device.agent.agent_id if device.agent_id else None,
        )
        device.delete()
        return Response("ok")


class QuickSupportDownload(APIView):
    """
    TRMM Desktop in quick support mode (no install). The id server and key of
    this TRMM are appended to the end of the executable (embedded_config),
    never put in the file name.
    """

    permission_classes = [IsAuthenticated, RemoteAccessPerms]

    def get(self, request):
        path = host_file("windows", "x64")
        if path is None:
            return notify_error("TRMM Desktop installer not found on the server")
        cfg = RemoteAccessConfig.load()
        if not cfg.id_server:
            return notify_error("Configure the RustDesk server first (install-rustdesk-server.sh)")
        try:
            # "install permanent access" button of the quick support: it uses
            # the same client token as the MeshCentral quick support executable
            from quicksupport.models import QuickSupportConfig

            qs = QuickSupportConfig.load()
            extra = {"trmm_api": api_url(request)}
            if qs.enabled and _has_perm(request, "can_use_quick_support"):
                extra["trmm_token"] = qs.client_token
            trailer = embedded_config(
                "qs", cfg.id_server, cfg.relay_server, cfg.public_key, extra
            ).encode()
        except ValueError as e:
            return notify_error(str(e))

        def stream():
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
            yield trailer

        filename = getattr(settings, "REMOTEACCESS_QS_FILENAME", "TRMM-Suporte.exe")
        response = StreamingHttpResponse(
            stream(), content_type="application/vnd.microsoft.portable-executable"
        )
        response["Content-Length"] = str(os.path.getsize(path) + len(trailer))
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ---------------------------------------------------------------------------
# managed computers (no user login)
# ---------------------------------------------------------------------------


class Download(APIView):
    """Host installer, for scripts. Protected by a short-lived signed token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [DownloadThrottle]

    def get(self, request, plat, arch):
        if not check_download_token(request.query_params.get("t", ""), plat, arch):
            return Response({"error": "invalid or expired link"}, status=403)
        path = host_file(plat, arch)
        if path is None:
            return Response({"error": "not found"}, status=404)
        return FileResponse(open(path, "rb"), as_attachment=True, filename=os.path.basename(path))


class Enroll(APIView):
    """
    Called by the generated install scripts after installing the host (with
    the tactical installer token and the signed site ticket of the script),
    also when the tactical agent failed to install.

    Security: the installer token is in scripts given to customers, so it can
    only register computers in the site of its ticket and never touches a
    remote access record of another site. Technicians need permission on the
    site / agent / existing record.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [EnrollThrottle]

    def post(self, request):
        import re

        from tacticalrmm.permissions import _has_perm_on_agent

        user = request.user
        installer = bool(user.is_installer_user)
        if not (installer or _has_perm(request, "can_use_remote_access")):
            return Response({"detail": "Permission denied"}, status=403)
        if not RemoteAccessConfig.load().enabled:
            return Response({"detail": "Remote access is disabled"}, status=403)

        remote_id = str(request.data.get("remote_id", "")).strip()
        password = str(request.data.get("password", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", remote_id) or not 6 <= len(password) <= 128:
            return notify_error("Invalid remote_id or password")

        # site: from the signed ticket for the installer token, checked for technicians
        if installer:
            site_pk = check_enroll_ticket(request.data.get("ticket", ""))
            if site_pk is None:
                return Response({"detail": "Invalid or expired install ticket"}, status=403)
        else:
            site_pk = request.data.get("site")
        site = None
        if site_pk:
            try:
                site = Site.objects.filter(pk=int(site_pk)).select_related("client").first()
            except (TypeError, ValueError):
                site = None
            if site is None:
                return notify_error("Invalid site")
        if site and not installer and not _has_perm_on_site(user, site.pk):
            return Response({"detail": "Permission denied"}, status=403)

        # tactical agent of this computer: only inside the ticket/permitted site
        hostname = "".join(c for c in str(request.data.get("hostname", "")) if c.isprintable())[:255]
        agent = None
        agent_id = str(request.data.get("agent_id") or "").strip()
        if agent_id:
            agent = Agent.objects.filter(agent_id=agent_id).first()
            if agent is not None:
                if installer and (site is None or agent.site_id != site.pk):
                    agent = None
                elif not installer and not _has_perm_on_agent(user, agent.agent_id):
                    agent = None
        if agent is None and hostname and site:
            agent = Agent.objects.filter(hostname__iexact=hostname, site=site).first()

        def allowed(device) -> bool:
            if installer:
                return site is not None and device.effective_site is not None and device.effective_site.pk == site.pk
            return can_access_device(user, device)

        device = RemoteAccessDevice.objects.filter(remote_id=remote_id).first()
        if device is not None and not allowed(device):
            return Response({"detail": "This remote id belongs to another computer"}, status=403)
        if device is None and agent is not None:
            device = RemoteAccessDevice.objects.filter(agent=agent).first()
            if device is not None and not allowed(device):
                device = None
                agent = None
        if device is None:
            device = RemoteAccessDevice()

        if agent is not None and not RemoteAccessDevice.objects.filter(agent=agent).exclude(pk=device.pk).exists():
            device.agent = agent
        if site is not None:
            device.site = site
            device.client = site.client
        device.hostname = hostname
        device.os = "".join(c for c in str(request.data.get("os", "")) if c.isprintable())[:255]
        device.remote_id = remote_id
        device.password = password
        device.status = RemoteAccessStatus.OK
        device.last_error = ""
        device.save()

        AuditLog.objects.create(
            username=user.username,
            agent=hostname or None,
            agent_id=device.agent.agent_id if device.agent_id else None,
            object_type=AuditObjType.AGENT,
            action=AuditActionType.AGENT_INSTALL,
            message=(
                f"Remote access enrolled for {hostname} (TRMM Desktop id {remote_id})"
                + ("" if device.agent_id else " without a tactical agent")
                + "."
            ),
        )
        from recordings.views import host_record_config

        record = host_record_config(device, api_url(request)) or {}
        return Response(
            {
                "device": device.pk,
                "linked_agent": bool(device.agent_id),
                # session recording (empty = off): the install script passes
                # them to `trmmdesktop --record-config`
                "record_url": record.get("url", ""),
                "record_token": record.get("token", ""),
            }
        )


# ---------------------------------------------------------------------------
# combined installer: tactical agent (may fail) + remote access (always)
# ---------------------------------------------------------------------------


class CombinedInstaller(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not (
            _has_perm(request, "can_install_agents")
            and _has_perm(request, "can_use_remote_access")
        ):
            return Response({"detail": "Permission denied"}, status=403)

        site = get_object_or_404(Site.objects.select_related("client"), pk=request.data.get("site"))
        if not _has_perm_on_site(request.user, site.pk):
            return Response({"detail": "Permission denied"}, status=403)

        linux = request.data.get("plat", "windows") == "linux"
        try:
            if linux:
                script = build_linux(request, site, request.data)
            else:
                script = build_windows(request, site, request.data, remote_access=True)
        except InstallerError as e:
            return notify_error(str(e))

        name = "trmm-install-remoteaccess." + ("sh" if linux else "ps1")
        response = HttpResponse(script, content_type="text/plain")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
