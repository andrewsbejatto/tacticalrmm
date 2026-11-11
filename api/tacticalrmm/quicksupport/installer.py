"""
Tactical RMM agent installer (core/installer.ps1) for the "install permanent
access" button of the quick support executable. The technician approves the
request and chooses the site; the executable downloads this script once and
runs it as administrator.
"""

import datetime as dt
from pathlib import Path

from django.conf import settings

GOARCHS = ("amd64", "386")
AGENT_TYPES = ("workstation", "server")


class InstallerError(ValueError):
    pass


def _installer_token(hours: int, created: list | None = None) -> str:
    from knox.models import AuthToken

    from accounts.models import User

    installer_user = User.objects.filter(is_installer_user=True).first()
    if installer_user is None:
        raise InstallerError("No installer user found")
    instance, token = AuthToken.objects.create(
        user=installer_user, expiry=dt.timedelta(hours=hours)
    )
    if created is not None:
        created.append(instance)
    return token


def build_windows_installer(
    api: str, site, data: dict, created: list | None = None
) -> str:
    """
    data: agenttype (workstation/server), goarch (amd64/386), expires (hours).
    created: receives the knox token instance, so the caller can revoke it
    when the install finishes.
    """
    from agents.utils import get_agent_url
    from core.utils import token_is_valid

    # same rule as agents.views.install_agent for the powershell method
    if getattr(settings, "TRMM_INSECURE", False):
        raise InstallerError("Not available in insecure mode")

    goarch = str(data.get("goarch", "amd64"))
    if goarch not in GOARCHS:
        raise InstallerError("Invalid architecture")
    agenttype = str(data.get("agenttype", "workstation"))
    if agenttype not in AGENT_TYPES:
        raise InstallerError("Invalid agent type")
    try:
        expires = min(max(int(data.get("expires", 2)), 1), 24)
    except (TypeError, ValueError):
        raise InstallerError("Invalid installer options")

    version = settings.LATEST_AGENT_VER
    codesign_token, _ = token_is_valid()
    token = _installer_token(expires, created)

    script = Path(settings.BASE_DIR / "core" / "installer.ps1").read_text()
    # same values as agents.views.install_agent ("powershell" method)
    for key, value in {
        "innosetupchange": f"tacticalagent-v{version}-windows-{goarch}.exe",
        "clientchange": str(site.client_id),
        "sitechange": str(site.pk),
        "apichange": api,
        "atypechange": agenttype,
        # servers never sleep/hibernate
        "powerchange": "1" if agenttype == "server" else "0",
        "rdpchange": "0",
        "pingchange": "0",
        "downloadchange": get_agent_url(
            goarch=goarch, plat="windows", token=codesign_token
        ),
        "tokenchange": token,
    }.items():
        script = script.replace(key, value)
    return script
