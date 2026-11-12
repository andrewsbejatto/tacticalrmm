import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone as djangotime
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.helpers import notify_error

from .models import (
    Recording,
    RecordingConfig,
    RecordingSource,
    RecordingStatus,
    RecordingToken,
    hash_token,
)
from .permissions import RecordingPerms, RecordingSettingsPerms
from .serializers import RecordingConfigSerializer, RecordingSerializer
from .tasks import attribute_rustdesk, recordings_for, spool_dir, upload_recording_task

# TRMM Desktop recorder file names (libs/scrap/src/common/record.rs)
FILE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,200}\.(webm|mp4)$")
MAX_CHUNK = 32 * 1024 * 1024
MAX_HEADER = 1024
QS_TOKEN_HOURS = 12
PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# dashboard / app
# ---------------------------------------------------------------------------


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if djangotime.is_naive(dt):
        dt = djangotime.make_aware(dt)
    return dt


class Recordings(APIView):
    permission_classes = [IsAuthenticated, RecordingPerms]

    def get(self, request):
        p = request.query_params
        qs = recordings_for(
            request.user,
            Recording.objects.select_related("agent", "client", "site"),
        )
        try:
            if p.get("client"):
                qs = qs.filter(client_id=int(p["client"]))
            if p.get("site"):
                qs = qs.filter(site_id=int(p["site"]))
            if p.get("technician"):
                qs = qs.filter(technician_id=int(p["technician"]))
            page = max(int(p.get("page", 1)), 1)
            page_size = min(max(int(p.get("page_size", PAGE_SIZE)), 1), 500)
        except ValueError:
            return notify_error("Invalid filter")
        if p.get("agent_id"):
            qs = qs.filter(agent__agent_id=p["agent_id"])
        if p.get("source") in RecordingSource.values:
            qs = qs.filter(source=p["source"])
        if p.get("status") in RecordingStatus.values:
            qs = qs.filter(status=p["status"])
        if start := _parse_date(p.get("from")):
            qs = qs.filter(started__gte=start)
        if end := _parse_date(p.get("to")):
            qs = qs.filter(started__lte=end)
        if search := (p.get("search") or "").strip():
            from django.db.models import Q

            qs = qs.filter(
                Q(hostname__icontains=search) | Q(technician_name__icontains=search)
            )

        total = qs.count()
        items = qs[(page - 1) * page_size : page * page_size]
        return Response(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "results": RecordingSerializer(items, many=True).data,
            }
        )


class Play(APIView):
    """Temporary link of the recording in the bucket (audited)."""

    permission_classes = [IsAuthenticated, RecordingPerms]

    def get(self, request, pk):
        from . import s3

        rec = get_object_or_404(recordings_for(request.user), pk=pk)
        if rec.status != RecordingStatus.UPLOADED or not rec.s3_key:
            return notify_error("This recording has not been uploaded yet")
        cfg = RecordingConfig.load()
        try:
            url = s3.presigned_url(cfg, rec.s3_key, rec.filename)
        except s3.StorageError as e:
            return notify_error(str(e))
        AuditLog.objects.create(
            username=request.user.username,
            agent=rec.hostname or None,
            agent_id=rec.agent.agent_id if rec.agent_id else None,
            object_type=AuditObjType.AGENT,
            action=AuditActionType.VIEW,
            message=(
                f"{request.user.username} watched the recording of {rec.hostname} "
                f"({rec.started:%Y-%m-%d %H:%M} UTC, by {rec.technician_name or '?'})."
            ),
            debug_info={"ip": getattr(request, "_client_ip", ""), "recording": rec.pk},
        )
        return Response(
            {
                "url": url,
                "kind": (
                    "mcrec" if rec.content_type == "application/x-mcrec" else "video"
                ),
                "content_type": rec.content_type,
                "filename": rec.filename,
                "expires_in": max(1, cfg.presign_minutes) * 60,
            }
        )


class Settings(APIView):
    permission_classes = [IsAuthenticated, RecordingSettingsPerms]

    def get(self, request):
        from .tasks import mesh_dir

        cfg = RecordingConfig.load()
        try:
            import boto3  # noqa: F401

            boto3_ok = True
        except ImportError:
            boto3_ok = False
        return Response(
            {
                **RecordingConfigSerializer(cfg).data,
                "boto3_installed": boto3_ok,
                "mesh_dir_found": os.path.isdir(mesh_dir()),
                "pending": Recording.objects.exclude(
                    status=RecordingStatus.UPLOADED
                ).count(),
            }
        )

    def put(self, request):
        cfg = RecordingConfig.load()
        serializer = RecordingConfigSerializer(
            instance=cfg, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        AuditLog.objects.create(
            username=request.user.username,
            object_type=AuditObjType.CORE,
            action=AuditActionType.MODIFY,
            message=f"{request.user.username} changed the session recording settings.",
            debug_info={"ip": getattr(request, "_client_ip", "")},
        )
        return Response("ok")


class SettingsTest(APIView):
    permission_classes = [IsAuthenticated, RecordingSettingsPerms]

    def post(self, request):
        from . import s3

        cfg = RecordingConfig.load()
        origins = [
            o.rstrip("/")
            for o in getattr(settings, "CORS_ORIGIN_WHITELIST", [])
            if o.startswith("https://")
        ]
        try:
            return Response(s3.test(cfg, origins))
        except s3.StorageError as e:
            return notify_error(str(e))


# ---------------------------------------------------------------------------
# computers that record (TRMM Desktop host / quick support)
# ---------------------------------------------------------------------------


def host_record_config(device, api: str) -> dict | None:
    """
    For `trmmdesktop --record-config <file>` on a managed host (file = this
    dict as JSON): upload url + a new token of the device. None = recording
    off (the host gets an empty file).
    """
    cfg = RecordingConfig.load()
    if not (cfg.enabled and cfg.record_rustdesk):
        RecordingToken.objects.filter(device=device).delete()
        return None
    token = RecordingToken.issue(device=device)
    return {"url": f"{api}/recordings/upload/", "token": token}


def host_record_json(device, api: str) -> str:
    rec = host_record_config(device, api)
    return json.dumps(rec, separators=(",", ":")) if rec else ""


class QuickSupportToken(APIView):
    """TRMM Desktop quick support: recording credential of its session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, pk):
        from quicksupport.views import _check_client_token, _get_client_session
        from remoteaccess.files import api_url

        if _check_client_token(request) is None:
            return Response({"detail": "Invalid client token"}, status=403)
        session = _get_client_session(request, pk)
        if session is None:
            return Response({"detail": "Session not found"}, status=404)
        cfg = RecordingConfig.load()
        if not (cfg.enabled and cfg.record_qs) or session.kind != "rustdesk":
            return Response({"detail": "Recording disabled"}, status=404)
        token = RecordingToken.issue(
            qs_session=session,
            expires=djangotime.now() + timedelta(hours=QS_TOKEN_HOURS),
        )
        return Response(
            {"url": f"{api_url(request)}/recordings/upload/", "token": token}
        )


class UploadThrottle(SimpleRateThrottle):
    rate = "1200/min"

    def get_cache_key(self, request, view):
        token = request.headers.get("X-TRMM-Record-Token", "")
        return self.cache_format % {
            "scope": "recordings_upload",
            "ident": hash_token(token)[:32] if token else self.get_ident(request),
        }


def _error(message: str, status: int = 400):
    # the TRMM Desktop uploader stops when the json has "error"
    return JsonResponse({"error": message}, status=status)


class Upload(APIView):
    """
    Receives the recordings of TRMM Desktop (RustDesk record_upload.rs
    protocol): ?type=new|part|tail|remove&file=<name>[&offset=&length=],
    body = raw bytes. Authenticated by X-TRMM-Record-Token.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [UploadThrottle]

    def _owner(self, request):
        token = request.headers.get("X-TRMM-Record-Token", "")
        if not token:
            return None
        rt = (
            RecordingToken.objects.select_related(
                "device__agent__site__client", "device__site__client", "qs_session"
            )
            .filter(token_hash=hash_token(token))
            .first()
        )
        if rt is None or (rt.expires and rt.expires < djangotime.now()):
            return None
        return rt

    def _read_body(self, request, length: int) -> bytes | None:
        # raw stream: request.body would refuse chunks over
        # DATA_UPLOAD_MAX_MEMORY_SIZE
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return None
        if declared != length or length > MAX_CHUNK:
            return None
        data = b""
        while len(data) < length:
            chunk = request._request.read(min(1024 * 1024, length - len(data)))
            if not chunk:
                break
            data += chunk
        return data if len(data) == length else None

    def post(self, request):
        rt = self._owner(request)
        if rt is None:
            return _error("invalid token", 403)
        cfg = RecordingConfig.load()
        enabled = cfg.enabled and (
            cfg.record_rustdesk if rt.device_id else cfg.record_qs
        )
        if not enabled:
            return _error("recording disabled", 403)

        kind = request.query_params.get("type", "")
        filename = request.query_params.get("file", "")
        if not FILE_RE.match(filename):
            return _error("invalid file name")
        owner = {"device": rt.device} if rt.device_id else {"qs_session": rt.qs_session}
        rec = (
            Recording.objects.filter(filename=filename, **owner)
            .exclude(status=RecordingStatus.UPLOADED)
            .first()
        )

        if kind == "new":
            if rec is not None:
                # same file announced again (retry): start over
                self._discard(rec)
            rec = self._create(rt, filename)
            return JsonResponse({})

        if rec is None:
            return _error("unknown file")
        if kind == "remove":
            self._discard(rec)
            return JsonResponse({})
        if rec.status != RecordingStatus.RECEIVING:
            return _error("file already finished")

        try:
            offset = int(request.query_params.get("offset", ""))
            length = int(request.query_params.get("length", ""))
        except ValueError:
            return _error("invalid offset or length")
        if offset < 0 or length < 0:
            return _error("invalid offset or length")
        if kind == "tail" and (offset != 0 or length > MAX_HEADER):
            return _error("invalid tail")
        if kind not in ("part", "tail"):
            return _error("invalid type")

        size = os.path.getsize(rec.local_path) if os.path.isfile(rec.local_path) else 0
        if kind == "part" and offset > size:
            return _error("missing data before offset")
        if offset + length > cfg.max_file_mb * 1024 * 1024:
            self._fail(rec, "file larger than the configured limit")
            return _error("file too large")
        data = self._read_body(request, length)
        if data is None:
            return _error("invalid body")

        with open(
            rec.local_path, "r+b" if os.path.isfile(rec.local_path) else "wb"
        ) as f:
            f.seek(offset)
            f.write(data)

        if kind == "tail":
            rec.status = RecordingStatus.PENDING
            rec.duration = max(0, int((djangotime.now() - rec.started).total_seconds()))
            rec.size = os.path.getsize(rec.local_path)
            rec.save(update_fields=["status", "duration", "size", "updated"])
            if cfg.s3_configured:
                upload_recording_task.delay(rec.pk)
        else:
            rec.size = max(rec.size, offset + length)
            rec.save(update_fields=["size", "updated"])
        return JsonResponse({})

    def _create(self, rt: RecordingToken, filename: str) -> Recording:
        ext = filename.rsplit(".", 1)[-1].lower()
        path = os.path.join(
            spool_dir(),
            f"rd-{int(time.time() * 1000)}-{secrets.token_hex(4)}-{filename}",
        )
        open(path, "wb").close()
        common = {
            "status": RecordingStatus.RECEIVING,
            "started": djangotime.now(),
            "filename": filename,
            "content_type": "video/webm" if ext == "webm" else "video/mp4",
            "local_path": path,
        }
        if rt.device_id:
            device = rt.device
            user, name = attribute_rustdesk(device)
            return Recording.objects.create(
                source=RecordingSource.RUSTDESK,
                device=device,
                agent=device.agent,
                client=device.effective_client,
                site=device.effective_site,
                hostname=device.hostname,
                technician=user,
                technician_name=name,
                **common,
            )
        session = rt.qs_session
        return Recording.objects.create(
            source=RecordingSource.QS_RUSTDESK,
            qs_session=session,
            hostname=session.hostname,
            technician=session.connected_by,
            technician_name=(
                session.connected_by.username if session.connected_by_id else ""
            ),
            **common,
        )

    def _discard(self, rec: Recording) -> None:
        if rec.local_path:
            try:
                os.remove(rec.local_path)
            except OSError:
                pass
        rec.delete()

    def _fail(self, rec: Recording, message: str) -> None:
        rec.status = RecordingStatus.PENDING
        rec.last_error = message
        rec.save(update_fields=["status", "last_error", "updated"])
        if RecordingConfig.load().s3_configured:
            upload_recording_task.delay(rec.pk)
