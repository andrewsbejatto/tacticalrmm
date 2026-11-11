from rest_framework import permissions

from tacticalrmm.permissions import (
    _has_perm,
    _has_perm_on_agent,
    _has_perm_on_client,
    _has_perm_on_site,
)


class RemoteAccessPerms(permissions.BasePermission):
    """Role permission "Use Remote Access" (Permissions Manager)."""

    def has_permission(self, r, view) -> bool:
        from .models import RemoteAccessConfig

        # "enabled" off in the settings disables every remote access endpoint
        if not RemoteAccessConfig.load().enabled:
            return False
        if "agent_id" in view.kwargs:
            return _has_perm(r, "can_use_remote_access") and _has_perm_on_agent(
                r.user, view.kwargs["agent_id"]
            )
        return _has_perm(r, "can_use_remote_access")


class RemoteAccessSettingsPerms(permissions.BasePermission):
    def has_permission(self, r, view) -> bool:
        if r.method == "GET":
            return _has_perm(r, "can_view_core_settings")
        return _has_perm(r, "can_edit_core_settings")


def can_access_device(user, device) -> bool:
    """Client/site restrictions of the user's role, for one device."""
    if device.agent_id:
        return _has_perm_on_agent(user, device.agent.agent_id)
    if device.site_id:
        return _has_perm_on_site(user, device.site_id)
    if device.client_id:
        return _has_perm_on_client(user, device.client_id)
    # no agent and no client/site: only users without client/site restrictions
    if user.is_superuser:
        return True
    role = user.role
    if not role:
        return False
    if role.is_superuser:
        return True
    return not (role.can_view_clients.exists() or role.can_view_sites.exists())
