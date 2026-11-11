import base64
import hashlib
import secrets
import string

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    # key derived from django's SECRET_KEY, dedicated to this purpose
    digest = hashlib.sha256(
        f"trmm-remoteaccess:{settings.SECRET_KEY}".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # SECRET_KEY changed: the stored password can't be read anymore
        return ""


def make_password(length: int = 20) -> str:
    # letters and digits only: safe inside powershell / bash quotes
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------- embedded config signature
def _signing_key():
    """Ed25519 private key of this server (created on first use)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from .models import RemoteAccessConfig

    cfg = RemoteAccessConfig.load()
    raw = decrypt(cfg.signing_key_enc) if cfg.signing_key_enc else ""
    if raw:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    cfg.signing_key_enc = encrypt(base64.b64encode(seed).decode())
    cfg.save(update_fields=["signing_key_enc"])
    return key


def signing_public_key() -> str:
    """base64 of the raw 32 bytes public key (goes into the TRMM Desktop build)"""
    from cryptography.hazmat.primitives import serialization

    pub = _signing_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(pub).decode()


def sign(data: bytes) -> str:
    """base64 Ed25519 signature"""
    return base64.b64encode(_signing_key().sign(data)).decode()
