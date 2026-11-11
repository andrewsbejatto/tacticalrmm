from rest_framework import serializers

from .models import RemoteAccessConfig, RemoteAccessDevice


class RemoteAccessConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemoteAccessConfig
        fields = ["enabled", "id_server", "relay_server", "public_key"]

    def validate_id_server(self, value):
        return value.strip()

    def validate_relay_server(self, value):
        return value.strip()

    def validate_public_key(self, value):
        return value.strip()


class RemoteAccessDeviceSerializer(serializers.ModelSerializer):
    """Never includes the password (see the connect endpoint)."""

    agent_id = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = RemoteAccessDevice
        fields = [
            "id",
            "agent_id",
            "hostname",
            "os",
            "client_name",
            "site_name",
            "remote_id",
            "status",
            "last_error",
            "version",
            "has_password",
            "created",
            "updated",
        ]

    def get_agent_id(self, obj):
        return obj.agent.agent_id if obj.agent_id else None

    def get_client_name(self, obj):
        client = obj.effective_client
        return client.name if client else None

    def get_site_name(self, obj):
        site = obj.effective_site
        return site.name if site else None

    def get_has_password(self, obj):
        return bool(obj.password_enc)
