from rest_framework import permissions

from tacticalrmm.permissions import _has_perm


class QuickSupportPerms(permissions.BasePermission):
    """Role permission "Use Quick Support" (Settings > Permissions Manager)."""

    def has_permission(self, r, view) -> bool:
        return _has_perm(r, "can_use_quick_support")


class QuickSupportSettingsPerms(permissions.BasePermission):
    def has_permission(self, r, view) -> bool:
        if r.method == "GET":
            return _has_perm(r, "can_view_core_settings")

        return _has_perm(r, "can_edit_core_settings")
