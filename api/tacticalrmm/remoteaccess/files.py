"""
TRMM Desktop binaries served by the tactical server.

They are built from the fork (see TrmmDesktop docs) and copied to
REMOTEACCESS_DIR (default /opt/trmm-desktop), outside the repo so update.sh
doesn't remove them:

    trmmdesktop-windows-x64.exe     installer / host / technician app / quick support
    trmmdesktop-linux-x64.deb       linux host / technician app
"""

import hashlib
import os

from django.conf import settings
from django.core import signing

SIGN_SALT = "remoteaccess-download"
DOWNLOAD_MAX_AGE = 60 * 60 * 2  # 2 hours

HOST_FILES = {
    ("windows", "x64"): "trmmdesktop-windows-x64.exe",
    ("linux", "x64"): "trmmdesktop-linux-x64.deb",
}

# tactical goarch -> host file arch
GOARCH = {"amd64": "x64", "386": "x86", "arm64": "arm64"}

_hash_cache: dict[tuple[str, float], str] = {}


def files_dir() -> str:
    return getattr(settings, "REMOTEACCESS_DIR", "/opt/trmm-desktop")


def host_file(plat: str, arch: str) -> str | None:
    name = HOST_FILES.get((plat, arch))
    if not name:
        return None
    path = os.path.join(files_dir(), name)
    return path if os.path.isfile(path) else None


def sha256(path: str) -> str:
    key = (path, os.path.getmtime(path))
    if key not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        _hash_cache.clear()
        _hash_cache[key] = h.hexdigest()
    return _hash_cache[key]


def download_token(plat: str, arch: str) -> str:
    return signing.TimestampSigner(salt=SIGN_SALT).sign(f"{plat}:{arch}")


def check_download_token(token: str, plat: str, arch: str) -> bool:
    try:
        value = signing.TimestampSigner(salt=SIGN_SALT).unsign(
            token, max_age=DOWNLOAD_MAX_AGE
        )
    except signing.BadSignature:
        return False
    return value == f"{plat}:{arch}"


ENROLL_SALT = "trmm-remoteaccess-enroll"


def enroll_ticket(site_pk: int, hours: int) -> str:
    """
    Signed ticket put in generated install scripts: the computers they enroll
    can only be registered in this site (Enroll view), for the script lifetime.
    """
    return signing.TimestampSigner(salt=ENROLL_SALT).sign(f"site:{int(site_pk)}:{int(hours)}")


def check_enroll_ticket(ticket: str) -> int | None:
    """site pk of a valid, not expired ticket"""
    try:
        signer = signing.TimestampSigner(salt=ENROLL_SALT)
        value = signer.unsign(str(ticket))
        kind, site_pk, hours = value.split(":")
        signer.unsign(str(ticket), max_age=int(hours) * 3600)
        return int(site_pk) if kind == "site" else None
    except (signing.BadSignature, ValueError):
        return None


def download_url(api_url: str, plat: str, arch: str) -> str:
    return (
        f"{api_url}/remoteaccess/download/{plat}/{arch}/"
        f"?t={download_token(plat, arch)}"
    )


def api_url(request) -> str:
    url = getattr(settings, "REMOTEACCESS_API_URL", None) or getattr(
        settings, "QUICKSUPPORT_API_URL", None
    )
    if url:
        return url.rstrip("/")
    scheme = "http" if settings.DEBUG and not request.is_secure() else "https"
    return f"{scheme}://{request.get_host()}"
