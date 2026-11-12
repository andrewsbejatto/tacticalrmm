from rest_framework import permissions

from tacticalrmm.permissions import _has_perm


class RecordingPerms(permissions.BasePermission):
    """Role permission "View Recordings"."""

    def has_permission(self, r, view) -> bool:
        return bool(_has_perm(r, "can_view_recordings"))


class RecordingSettingsPerms(permissions.BasePermission):
    def has_permission(self, r, view) -> bool:
        if r.method == "GET":
            return bool(_has_perm(r, "can_view_core_settings"))
        return bool(_has_perm(r, "can_edit_core_settings"))
