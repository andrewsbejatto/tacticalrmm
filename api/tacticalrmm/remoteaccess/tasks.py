import asyncio

from logs.models import AuditLog
from tacticalrmm.celery import app
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.logger import logger

from .files import GOARCH, download_url, host_file, sha256
from .models import RemoteAccessConfig, RemoteAccessDevice, RemoteAccessStatus
from .scripts import host_script, parse_result, server_config

SCRIPT_TIMEOUT = 600  # download + install can be slow


@app.task
def provision_remote_access_task(
    device_pk: int, password: str, install: bool, api_url: str, username: str
) -> str:
    """Installs (optional) the TRMM Desktop host through the tactical agent,
    sets the fixed password and saves the id."""
    device = RemoteAccessDevice.objects.select_related("agent").filter(pk=device_pk).first()
    if device is None or device.agent is None:
        return "no device/agent"
    agent = device.agent

    def fail(msg: str) -> str:
        device.status = RemoteAccessStatus.ERROR
        device.last_error = msg
        device.save(update_fields=["status", "last_error", "updated"])
        logger.error(f"remote access on {agent.hostname}: {msg}")
        return msg

    plat = agent.plat
    arch = GOARCH.get(agent.goarch or "amd64", "x64")
    url = digest = ""
    if install:
        path = host_file(plat, arch)
        if path is None:
            return fail(f"TRMM Desktop installer for {plat}/{arch} not found on the server")
        url, digest = download_url(api_url, plat, arch), sha256(path)

    try:
        cfg = RemoteAccessConfig.load()
        script = host_script(
            plat=plat,
            password=password,
            download_url=url,
            sha256=digest,
            install=install,
            config=server_config(cfg.id_server, cfg.relay_server, cfg.public_key),
        )
    except ValueError as e:
        return fail(str(e))

    data = {
        "func": "rawcmd",
        "timeout": SCRIPT_TIMEOUT,
        "payload": {
            "command": script,
            "shell": "powershell" if plat == "windows" else "/bin/bash",
        },
        "run_as_user": False,
    }
    r = asyncio.run(agent.nats_cmd(data, timeout=SCRIPT_TIMEOUT + 10))
    if r == "timeout":
        return fail("Unable to contact the agent")

    ok, remote_id, msg = parse_result(str(r))
    if not ok:
        return fail(msg)

    device.remote_id = remote_id
    device.password = password
    device.hostname = agent.hostname
    device.os = agent.operating_system or ""
    device.status = RemoteAccessStatus.OK
    device.last_error = ""
    device.save()

    AuditLog.objects.create(
        username=username,
        agent=agent.hostname,
        agent_id=agent.agent_id,
        object_type=AuditObjType.AGENT,
        action=AuditActionType.MODIFY,
        message=(
            f"{username} {'installed' if install else 'changed the password of'} "
            f"remote access (TRMM Desktop id {remote_id}) on {agent.hostname}."
        ),
    )
    return "ok"
