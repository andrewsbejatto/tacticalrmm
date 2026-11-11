from rest_framework import serializers

from .models import QuickSupportConfig, QuickSupportSession


class QuickSupportConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuickSupportConfig
        fields = [
            "enabled",
            "company_name",
            "welcome_message",
            "require_consent",
            "show_privacy_bar",
            "show_notification",
            "session_timeout",
            "mesh_group_name",
            "mesh_group_id",
        ]
        read_only_fields = ["mesh_group_id"]

    def validate_company_name(self, value):
        return "".join(c for c in value if c not in "\r\n\0").strip()

    def validate_mesh_group_name(self, value):
        from core.utils import get_core_settings

        value = value.strip()
        if not value:
            raise serializers.ValidationError("Required")
        if value.lower() == (get_core_settings().mesh_device_group or "").lower():
            raise serializers.ValidationError(
                "Must be different from the main Tactical RMM device group"
            )
        return value

    def validate_session_timeout(self, value):
        if value < 1 or value > 60:
            raise serializers.ValidationError("Must be between 1 and 60 minutes")
        return value


class QuickSupportSessionSerializer(serializers.ModelSerializer):
    formatted_code = serializers.ReadOnlyField()
    connected_by = serializers.SerializerMethodField()
    install_site_name = serializers.SerializerMethodField()

    def get_install_site_name(self, obj):
        site = obj.install_site
        return f"{site.client.name} / {site.name}" if site else None

    class Meta:
        model = QuickSupportSession
        fields = [
            "id",
            "code",
            "formatted_code",
            "status",
            "hostname",
            "username",
            "os",
            "client_ip",
            "client_version",
            "created",
            "last_seen",
            "connected_at",
            "connected_by",
            "install_status",
            "install_site_name",
            "install_message",
        ]

    def get_connected_by(self, obj):
        return obj.connected_by.username if obj.connected_by else None
