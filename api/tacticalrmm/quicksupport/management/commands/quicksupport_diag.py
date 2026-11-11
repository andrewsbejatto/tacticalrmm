"""
Shows where the quick support privacy bar / consent comes from.

    python manage.py quicksupport_diag

MeshCentral combines (OR) the consent flags of the domain, the device group,
the device and the user that connects, so any of them can turn the bar on.
"""

from django.core.management.base import BaseCommand

from quicksupport.mesh import MESH_GROUP_FLAGS, MeshError, mesh_request
from quicksupport.models import CONSENT_PRIVACY_BAR, QuickSupportConfig


def _flags(value) -> str:
    if not isinstance(value, int):
        return "-"
    names = []
    if value & 0x0007:
        names.append("notify")
    if value & 0x0038:
        names.append("prompt")
    if value & CONSENT_PRIVACY_BAR:
        names.append("PRIVACY BAR")
    return f"{value} ({', '.join(names) or 'none'})"


class Command(BaseCommand):
    help = "Quick support diagnostics (privacy bar / consent)"

    def handle(self, *args, **kwargs):
        cfg = QuickSupportConfig.load()
        self.stdout.write("== Tactical RMM")
        self.stdout.write(f"show_privacy_bar: {cfg.show_privacy_bar}")
        self.stdout.write(f"show_notification: {cfg.show_notification}")
        self.stdout.write(f"require_consent:  {cfg.require_consent}")
        self.stdout.write(f"expected group consent: {_flags(cfg.mesh_consent)}")
        self.stdout.write(f"group id: {cfg.mesh_group_id or '(not created yet)'}")

        try:
            info = mesh_request({"action": "serverinfo"}).get("serverinfo", {})
            meshes = mesh_request({"action": "meshes"}).get("meshes", [])
            users = mesh_request({"action": "users"}).get("users", [])
        except MeshError as e:
            self.stderr.write(f"MeshCentral error: {e}")
            return

        self.stdout.write("\n== MeshCentral")
        self.stdout.write(f"domain userConsentFlags: {_flags(info.get('consent'))}")
        group = next(
            (m for m in meshes if m["_id"] == f"mesh//{cfg.mesh_group_id}"), None
        )
        if group is None:
            self.stdout.write("quick support group: NOT FOUND")
        else:
            self.stdout.write(f"group name:    {group.get('name')}")
            self.stdout.write(f"group consent: {_flags(group.get('consent'))}")
            self.stdout.write(
                f"group flags:   {group.get('flags')} (expected {MESH_GROUP_FLAGS})"
            )

        with_bar = [
            u
            for u in users
            if isinstance(u.get("consent"), int) and u["consent"] & CONSENT_PRIVACY_BAR
        ]
        self.stdout.write(
            "users with their own privacy bar flag: "
            + (", ".join(u.get("name", u["_id"]) for u in with_bar) or "none")
        )

        sources = []
        if (
            isinstance(info.get("consent"), int)
            and info["consent"] & CONSENT_PRIVACY_BAR
        ):
            sources.append("domain (MeshCentral config.json: userConsentFlags)")
        if (
            group
            and isinstance(group.get("consent"), int)
            and group["consent"] & CONSENT_PRIVACY_BAR
        ):
            sources.append("device group (save the quick support settings again)")
        if with_bar:
            sources.append(
                "user account(s) listed above (MeshCentral > My Account / Users)"
            )
        self.stdout.write(
            "\nprivacy bar comes from: "
            + ("; ".join(sources) if sources else "nothing - it should not appear")
        )
