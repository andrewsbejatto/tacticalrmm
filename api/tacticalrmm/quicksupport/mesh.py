"""
MeshCentral helpers for quick support sessions.

Quick support computers run the mesh agent in temporary mode ("connect", no
install) inside a dedicated device group. The agent reports a name/tag that
contains the session code, which is how a code is matched to a mesh device.
"""

import asyncio
import hashlib
import json
import os
import tempfile
import time
import urllib.parse
from base64 import b64decode
from typing import TYPE_CHECKING, Any

import requests
import websockets
from django.conf import settings
from django.core.cache import cache

from core.utils import get_core_settings, get_mesh_ws_url
from tacticalrmm.constants import TRMM_WS_MAX_SIZE
from tacticalrmm.logger import logger

if TYPE_CHECKING:
    from accounts.models import User

    from .models import QuickSupportConfig, QuickSupportSession

# MeshCentral device group flags
# 1 = remove devices when disconnected, 2 = sync device name, 8 = use agentName
MESH_GROUP_FLAGS = 1 | 2 | 8

# same rights tactical gives to its users on agents (see core/mesh_utils.py)
MESH_USER_RIGHTS = 4088024

# goarch -> meshcentral agent id
MESH_AGENT_IDS = {
    "amd64": 4,
    "386": 3,
    "arm64": 43,
}

AGENT_CACHE_SECONDS = 60 * 60 * 12
GROUP_LOCK_KEY = "quicksupport_mesh_group_lock"


class MeshError(Exception):
    pass


async def _mesh_request(payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    action = payload["action"]
    payload["responseid"] = "meshctrl"
    async with websockets.connect(get_mesh_ws_url(), max_size=TRMM_WS_MAX_SIZE) as ws:
        await ws.send(json.dumps(payload))
        while True:
            message = await asyncio.wait_for(ws.recv(), timeout)
            r = json.loads(message)
            if r.get("action") == action:
                return r


def mesh_request(payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    try:
        return asyncio.run(_mesh_request(payload, timeout))
    except asyncio.TimeoutError:
        raise MeshError("Timeout waiting for MeshCentral")
    except MeshError:
        raise
    except Exception as e:
        logger.error(f"Quick support mesh request failed: {e}")
        raise MeshError("Unable to communicate with MeshCentral")


def mesh_send(payload: dict[str, Any]) -> None:
    """Fire and forget."""

    async def _do():
        payload["responseid"] = "meshctrl"
        async with websockets.connect(
            get_mesh_ws_url(), max_size=TRMM_WS_MAX_SIZE
        ) as ws:
            await ws.send(json.dumps(payload))

    try:
        asyncio.run(_do())
    except Exception as e:
        logger.error(f"Quick support mesh request failed: {e}")


def b64_node_to_hex(node_id: str) -> str:
    """node//abc$@... -> HEX (format used by tactical in mesh urls)"""
    b64 = node_id.split("/")[-1].replace("$", "/").replace("@", "+")
    return b64decode(b64).hex().upper()


def hex_to_b64_node(hex_id: str) -> str:
    from base64 import b64encode

    b64 = b64encode(bytes.fromhex(hex_id)).decode()
    return "node//" + b64.replace("/", "$").replace("+", "@")


def ensure_mesh_group(cfg: "QuickSupportConfig") -> str:
    """
    Makes sure the quick support device group exists and returns its id.

    Only the group created by tactical (saved id) is used. An existing group is
    never adopted by name: the quick support flags (remove devices when they
    disconnect) applied to e.g. the main tactical group would delete agents.
    """
    if cfg.mesh_group_id:
        meshes = mesh_request({"action": "meshes"}).get("meshes", [])
        group = next(
            (m for m in meshes if m["_id"] == f"mesh//{cfg.mesh_group_id}"), None
        )
        if group is not None:
            if (
                group.get("flags") != MESH_GROUP_FLAGS
                or group.get("consent") != cfg.mesh_consent
                or group.get("name") != cfg.mesh_group_name
            ):
                update_mesh_group(group["_id"], cfg)
            return cfg.mesh_group_id
        # group was deleted in meshcentral, create a new one below

    # several executables can start at the same time, create only one group
    if not cache.add(GROUP_LOCK_KEY, 1, 60):
        raise MeshError("Quick support device group is being created, try again")
    try:
        cfg.refresh_from_db()
        if cfg.mesh_group_id and cfg.mesh_group_id in {
            m["_id"].split("mesh//")[1]
            for m in mesh_request({"action": "meshes"}).get("meshes", [])
        }:
            return cfg.mesh_group_id

        r = mesh_request(
            {
                "action": "createmesh",
                "meshname": cfg.mesh_group_name,
                "meshtype": 2,
                "desc": "Tactical RMM quick support (temporary devices)",
                "flags": MESH_GROUP_FLAGS,
                "consent": cfg.mesh_consent,
            }
        )
        mesh_id = r.get("meshid")
        if r.get("result") not in (None, "ok") or not mesh_id:
            raise MeshError(f"Unable to create MeshCentral device group: {r}")

        cfg.mesh_group_id = mesh_id.split("/")[-1]
        cfg.save(update_fields=["mesh_group_id"])
        return cfg.mesh_group_id
    finally:
        cache.delete(GROUP_LOCK_KEY)


def update_mesh_group(mesh_id: str, cfg: "QuickSupportConfig") -> None:
    # wait for meshcentral's answer so a failure is reported instead of lost
    r = mesh_request(
        {
            "action": "editmesh",
            "meshid": mesh_id,
            "meshname": cfg.mesh_group_name,
            "flags": MESH_GROUP_FLAGS,
            "consent": cfg.mesh_consent,
        }
    )
    if r.get("result") not in (None, "ok"):
        raise MeshError(f"Unable to update the MeshCentral device group: {r.get('result')}")


def _mesh_http_base() -> str:
    if settings.DOCKER_BUILD:
        return settings.MESH_WS_URL.replace("ws://", "http://")
    elif getattr(settings, "USE_EXTERNAL_MESH", False):
        return get_core_settings().mesh_site
    mesh_port = getattr(settings, "MESH_PORT", 4430)
    return f"http://127.0.0.1:{mesh_port}"


def get_session_msh(cfg: "QuickSupportConfig", session: "QuickSupportSession") -> str:
    """Mesh agent settings (.msh) for one quick support session."""
    group_id = ensure_mesh_group(cfg)
    url = f"{_mesh_http_base()}/meshsettings?" + urllib.parse.urlencode(
        {"id": group_id}
    )
    r = requests.get(url, timeout=15, verify=False)
    if r.status_code != 200:
        raise MeshError(f"Unable to get mesh agent settings ({r.status_code})")

    msh = r.text.strip()
    # the device name in meshcentral comes from agentName (sent by the agent
    # before authentication). The tag goes to a .tag file written by the
    # executable, because the agent discards a Tag= line from the .msh
    msh += f"\r\nagentName={session.mesh_agent_name}"
    company = _msh_value(cfg.company_name)
    if company:
        msh += f"\r\ncompanyName={company}"
    return msh + "\r\n"


def _msh_value(value: str) -> str:
    # one line only: a line break would let the value add other .msh settings
    # (e.g. a different MeshServer)
    return "".join(c for c in (value or "") if c not in "\r\n\0").strip()[:255]


def get_mesh_agent_binary(goarch: str) -> str:
    """Path of a cached generic (no settings) windows mesh agent."""
    ident = MESH_AGENT_IDS.get(goarch)
    if ident is None:
        raise MeshError("Unsupported architecture")

    path = os.path.join(settings.EXE_DIR, f"quicksupport-meshagent-{goarch}.exe")
    if (
        os.path.exists(path)
        and time.time() - os.path.getmtime(path) < AGENT_CACHE_SECONDS
    ):
        return path

    url = f"{_mesh_http_base()}/meshagents?id={ident}"
    os.makedirs(settings.EXE_DIR, exist_ok=True)
    # unique temp file, concurrent requests may download at the same time
    fd, tmp = tempfile.mkstemp(prefix="qs-meshagent-", dir=settings.EXE_DIR)
    try:
        with os.fdopen(fd, "wb") as f, requests.get(
            url, stream=True, timeout=30, verify=False
        ) as r:
            if r.status_code != 200:
                raise MeshError(f"Unable to download mesh agent ({r.status_code})")
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(64 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def find_session_node(
    cfg: "QuickSupportConfig", session: "QuickSupportSession"
) -> dict[str, Any] | None:
    """Finds the mesh device of a quick support session (prefers online ones)."""
    group_id = cfg.mesh_group_id or ensure_mesh_group(cfg)
    r = mesh_request({"action": "nodes", "meshid": f"mesh//{group_id}"})
    nodes = (r.get("nodes") or {}).get(f"mesh//{group_id}", [])

    # match only by the secret tag; the name (TRMM-QS-<code>) is self-reported
    # by the agent and could be faked by anyone that has the group .msh
    matches = [
        n
        for n in nodes
        if (n.get("agent") or {}).get("tag") == session.mesh_agent_tag
    ]
    online = [n for n in matches if n.get("conn", 0) & 1]
    if len(online) > 1:
        raise MeshError("More than one device matches this session, refusing")
    if online:
        return online[0]
    return matches[0] if matches else None


def grant_user_on_node(node_id: str, user: "User") -> None:
    if not get_core_settings().sync_mesh_with_trmm:
        # the mesh api superuser created the group so it already has full rights
        return

    mesh_request(
        {
            "action": "adddeviceuser",
            "nodeid": node_id,
            "usernames": [user.mesh_username],
            "rights": MESH_USER_RIGHTS,
            "remove": False,
        }
    )


def remove_node(node_id: str) -> None:
    mesh_send({"action": "removedevices", "nodeids": [node_id]})


def get_login_url(user: "User", node_hex: str, viewmode: int) -> str:
    from meshctrl.utils import get_login_token

    core = get_core_settings()
    mesh_user = (
        user.mesh_user_id
        if core.sync_mesh_with_trmm
        else f"user//{core.mesh_api_superuser}"
    )
    token = get_login_token(key=core.mesh_token, user=mesh_user)
    return (
        f"{core.mesh_site}/?login={token}&gotonode={node_hex}"
        f"&viewmode={viewmode}&hide=31"
    )
