import hashlib
import os
import shutil
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone as djangotime

from tacticalrmm.celery import app
from tacticalrmm.logger import logger

from .models import (
    Recording,
    RecordingConfig,
    RecordingSource,
    RecordingStatus,
    RecordingToken,
    RemoteConnectLog,
)

# MeshCentral closes the file ~5 s after the session ends and its indexer
# rewrites it right after: only take files that stopped changing
MESH_IDLE_SECONDS = 120
MAX_ATTEMPTS = 20


def spool_dir() -> str:
    path = getattr(settings, "RECORDINGS_SPOOL_DIR", "/opt/trmm-recordings")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def mesh_dir() -> str:
    return getattr(
        settings, "MESHCENTRAL_RECORDINGS_DIR", "/meshcentral/meshcentral-recordings"
    )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _key_parts(rec: Recording) -> list[str]:
    if rec.client_id or rec.site_id:
        return [
            rec.client.name if rec.client_id else "_",
            rec.site.name if rec.site_id else "_",
            rec.hostname or "_",
        ]
    return ["quick-support", rec.hostname or "_"]


@app.task
def upload_recording_task(pk: int) -> str:
    from . import s3

    rec = (
        Recording.objects.select_related("client", "site")
        .filter(pk=pk, status__in=[RecordingStatus.PENDING, RecordingStatus.ERROR])
        .first()
    )
    if rec is None:
        return "nothing to do"
    cfg = RecordingConfig.load()
    if not rec.local_path or not os.path.isfile(rec.local_path):
        rec.status = RecordingStatus.ERROR
        rec.last_error = "local file not found"
        rec.save(update_fields=["status", "last_error", "updated"])
        return rec.last_error

    rec.attempts += 1
    try:
        rec.size = os.path.getsize(rec.local_path)
        rec.sha256 = _sha256(rec.local_path)
        key = rec.s3_key or s3.object_key(
            cfg, rec.started, _key_parts(rec), rec.filename
        )
        s3.upload(cfg, rec.local_path, key, rec.content_type)
        if s3.object_size(cfg, key) != rec.size:
            raise s3.StorageError("size of the uploaded object doesn't match")
    except (s3.StorageError, OSError) as e:
        rec.status = RecordingStatus.ERROR
        rec.last_error = str(e)[:2000]
        rec.save(
            update_fields=[
                "status",
                "last_error",
                "attempts",
                "size",
                "sha256",
                "updated",
            ]
        )
        logger.error(f"recording {rec.pk} upload failed: {e}")
        return rec.last_error

    try:
        os.remove(rec.local_path)
    except OSError as e:
        logger.error(f"recording {rec.pk}: unable to delete {rec.local_path}: {e}")
    rec.s3_key = key
    rec.local_path = ""
    rec.status = RecordingStatus.UPLOADED
    rec.last_error = ""
    rec.save()
    return "ok"


@app.task
def retry_recordings_task() -> None:
    """Uploads that failed (bucket offline, wrong credentials...)."""
    cfg = RecordingConfig.load()
    if not cfg.enabled or not cfg.s3_configured:
        return
    for pk in Recording.objects.filter(
        status__in=[RecordingStatus.PENDING, RecordingStatus.ERROR],
        attempts__lt=MAX_ATTEMPTS,
    ).values_list("pk", flat=True):
        upload_recording_task(pk)

    # rustdesk files that stopped receiving data (host lost the connection
    # and never sent the tail): keep what arrived
    stale = djangotime.now() - timedelta(hours=2)
    for rec in Recording.objects.filter(
        status=RecordingStatus.RECEIVING, updated__lt=stale
    ):
        rec.status = RecordingStatus.PENDING
        rec.last_error = "incomplete: the computer stopped sending the recording"
        rec.save(update_fields=["status", "last_error", "updated"])
        upload_recording_task(rec.pk)

    # housekeeping
    RecordingToken.objects.filter(expires__lt=djangotime.now()).delete()
    RemoteConnectLog.objects.filter(
        time__lt=djangotime.now() - timedelta(days=7)
    ).delete()


def _technician(user_pk: int | None, fallback: str):
    from accounts.models import User

    user = User.objects.filter(pk=user_pk).first() if user_pk else None
    return user, (user.username if user else fallback)


def ingest_mesh_file(path: str) -> Recording | None:
    """One finished MeshCentral recording -> Recording (pending upload)."""
    from agents.models import Agent
    from quicksupport.mesh import b64_node_to_hex
    from quicksupport.models import QuickSupportSession

    from .mcrec import McrecError, mesh_user_pk, read_info

    try:
        info = read_info(path)
    except (McrecError, OSError) as e:
        logger.error(f"mesh recording {path}: {e}")
        return None

    agent = session = None
    node_hex = ""
    if info.nodeid:
        try:
            node_hex = b64_node_to_hex(info.nodeid)
        except ValueError:
            node_hex = ""
    if node_hex:
        agent = (
            Agent.objects.select_related("site__client")
            .filter(mesh_node_id__iexact=node_hex)
            .first()
        )
        if agent is None:
            session = QuickSupportSession.objects.filter(
                mesh_node_id__iexact=node_hex
            ).first()

    user, name = _technician(
        mesh_user_pk(info.userid), str(info.meta.get("username") or "")
    )
    if user is None and session is not None and session.connected_by_id:
        user, name = session.connected_by, session.connected_by.username

    filename = os.path.basename(path)
    target = os.path.join(spool_dir(), f"mesh-{int(time.time() * 1000)}-{filename}")
    shutil.move(path, target)

    return Recording.objects.create(
        source=RecordingSource.MESH,
        status=RecordingStatus.PENDING,
        agent=agent,
        qs_session=session,
        client=agent.client if agent else None,
        site=agent.site if agent else None,
        hostname=(
            agent.hostname
            if agent
            else (
                session.hostname if session else str(info.meta.get("devicename") or "")
            )
        )[:255],
        technician=user,
        technician_name=name[:255],
        started=info.started or djangotime.now(),
        duration=info.duration,
        size=os.path.getsize(target),
        filename=filename,
        content_type="application/x-mcrec",
        local_path=target,
    )


@app.task
def scan_mesh_recordings_task() -> int:
    cfg = RecordingConfig.load()
    if not (cfg.enabled and cfg.record_mesh):
        return 0
    folder = mesh_dir()
    if not os.path.isdir(folder):
        return 0
    count = 0
    now = time.time()
    for entry in os.scandir(folder):
        if not entry.is_file() or not entry.name.endswith(".mcrec"):
            continue
        try:
            if now - entry.stat().st_mtime < MESH_IDLE_SECONDS:
                continue
        except OSError:
            continue
        rec = ingest_mesh_file(entry.path)
        if rec is not None:
            count += 1
            if cfg.s3_configured:
                upload_recording_task.delay(rec.pk)
    return count


def attribute_rustdesk(device) -> tuple:
    """Technician of a new TRMM Desktop recording: last connect request."""
    log = (
        RemoteConnectLog.objects.filter(
            device=device, time__gte=djangotime.now() - timedelta(minutes=10)
        )
        .select_related("user")
        .order_by("-time")
        .first()
    )
    if log is None:
        return None, ""
    return log.user, log.username


def recordings_for(user, qs=None):
    """Recordings the user may see (role client/site restrictions)."""
    qs = Recording.objects.all() if qs is None else qs
    if user.is_superuser:
        return qs
    role = user.role
    if not role:
        return qs.none()
    if role.is_superuser:
        return qs
    clients = role.can_view_clients.all()
    sites = role.can_view_sites.all()
    if not clients and not sites:
        return qs
    q = Q(technician=user)
    if clients:
        q |= Q(client__in=clients)
    if sites:
        q |= Q(site__in=sites)
    return qs.filter(q)
