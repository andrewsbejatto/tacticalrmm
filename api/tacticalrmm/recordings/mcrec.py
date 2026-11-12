"""
MeshCentral session recordings (.mcrec, MeshCentral meshrelay.js recordingEntry):
blocks with a 16 byte header, big endian:
    type (2)  flags (2)  size (4)  time (8, ms in the last 6 bytes)
type 1 = JSON metadata (MeshCentralRelaySession), 2 = data, 3 = end;
MeshCentral's indexer (sessionRecording.index) appends more blocks later.
"""

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

HEADER = 16
MAGIC = "MeshCentralRelaySession"


class McrecError(ValueError):
    pass


@dataclass
class McrecInfo:
    meta: dict = field(default_factory=dict)
    started: datetime | None = None
    duration: int = 0  # seconds

    @property
    def nodeid(self) -> str:
        return str(self.meta.get("nodeid") or "")

    @property
    def userid(self) -> str:
        return str(self.meta.get("userid") or "")

    @property
    def protocol(self) -> int | None:
        try:
            return int(self.meta.get("protocol"))
        except (TypeError, ValueError):
            return None


def _header(raw: bytes) -> tuple[int, int, int, int]:
    btype, flags, size = struct.unpack(">hhi", raw[:8])
    ms = int.from_bytes(raw[10:16], "big")
    return btype, flags, size, ms


def read_info(path: str, max_blocks: int = 5_000_000) -> McrecInfo:
    info = McrecInfo()
    first_ms = last_ms = None
    with open(path, "rb") as f:
        raw = f.read(HEADER)
        if len(raw) < HEADER:
            raise McrecError("file too small")
        btype, _, size, ms = _header(raw)
        if btype != 1 or not 0 < size < 64 * 1024:
            raise McrecError("not a MeshCentral recording")
        try:
            info.meta = json.loads(f.read(size).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise McrecError("invalid recording metadata")
        if info.meta.get("magic") != MAGIC:
            raise McrecError("not a MeshCentral recording")
        first_ms = last_ms = ms
        # walk the block headers only, for the duration
        for _ in range(max_blocks):
            raw = f.read(HEADER)
            if len(raw) < HEADER:
                break
            btype, _, size, ms = _header(raw)
            if size < 0:
                break
            if btype in (1, 2, 3) and ms >= first_ms:
                last_ms = max(last_ms, ms)
            if btype == 3:
                break
            f.seek(size, 1)
    info.started = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc)
    info.duration = max(0, int((last_ms - first_ms) / 1000))
    return info


def mesh_user_pk(userid: str) -> int | None:
    """TRMM users are user//<name>___<pk> in MeshCentral (accounts User.mesh_user_id)."""
    name = userid.split("//", 1)[-1]
    if "___" not in name:
        return None
    try:
        return int(name.rsplit("___", 1)[1])
    except ValueError:
        return None
