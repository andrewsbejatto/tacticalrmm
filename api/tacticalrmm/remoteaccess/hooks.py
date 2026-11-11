"""
Hooks called from Tactical RMM views (small calls added to the official code).
"""

import asyncio

from tacticalrmm.logger import logger

from .scripts import LINUX_BIN, WIN_EXE

# started detached: it must survive the tactical agent uninstall that follows
_WIN_UNINSTALL = rf"""
$exe = '{WIN_EXE}'
if (Test-Path $exe) {{
    Start-Process -FilePath $exe -ArgumentList '--uninstall' -WindowStyle Hidden
}}
Remove-Item (Join-Path $env:ProgramData 'TRMMDesktop\host') -Force -ErrorAction SilentlyContinue
Write-Output 'ok'
"""

_LINUX_UNINSTALL = rf"""
if [ -x '{LINUX_BIN}' ] || dpkg -s trmmdesktop >/dev/null 2>&1; then
    nohup sh -c 'sleep 2; DEBIAN_FRONTEND=noninteractive apt-get purge -y trmmdesktop; rm -rf /etc/trmmdesktop' >/dev/null 2>&1 &
fi
echo ok
"""


def before_agent_uninstall(agent) -> None:
    """
    Agent removed from Tactical RMM: also uninstall TRMM Desktop (host) from
    the computer and forget its remote access record. Never raises: the
    agent uninstall must go on.
    """
    from .models import RemoteAccessDevice

    try:
        device = RemoteAccessDevice.objects.filter(agent=agent).first()
        if device is None:
            return
        if agent.plat in ("windows", "linux"):
            windows = agent.plat == "windows"
            data = {
                "func": "rawcmd",
                "timeout": 30,
                "payload": {
                    "command": _WIN_UNINSTALL if windows else _LINUX_UNINSTALL,
                    "shell": "powershell" if windows else "/bin/bash",
                },
                "run_as_user": False,
            }
            # fire and forget: an offline agent must not hold the request
            asyncio.run(agent.nats_cmd(data, wait=False))
        device.delete()
    except Exception as e:
        logger.error(f"remote access uninstall on {agent.hostname}: {e}")


def on_agent_pre_delete(sender, instance, **kwargs) -> None:
    """
    Agents deleted by other paths (bulk delete command, admin): same cleanup.
    The view calls before_agent_uninstall() first, so this is a no-op there.
    """
    before_agent_uninstall(instance)
