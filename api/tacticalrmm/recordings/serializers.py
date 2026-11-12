from rest_framework import serializers

from .models import Recording, RecordingConfig


class RecordingConfigSerializer(serializers.ModelSerializer):
    # write only: never returned
    s3_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    s3_secret_set = serializers.SerializerMethodField()

    class Meta:
        model = RecordingConfig
        fields = [
            "enabled",
            "record_mesh",
            "record_rustdesk",
            "record_qs",
            "s3_endpoint",
            "s3_region",
            "s3_bucket",
            "s3_prefix",
            "s3_access_key",
            "s3_secret",
            "s3_secret_set",
            "s3_path_style",
            "s3_sse",
            "presign_minutes",
            "max_file_mb",
        ]

    def get_s3_secret_set(self, obj) -> bool:
        return bool(obj.s3_secret_enc)

    def validate_s3_endpoint(self, value):
        value = value.strip()
        if value.lower().startswith("http://"):
            raise serializers.ValidationError("The S3 endpoint must use https")
        return value

    def validate_s3_bucket(self, value):
        return value.strip()

    def validate_s3_prefix(self, value):
        value = value.strip().strip("/")
        if ".." in value.split("/"):
            raise serializers.ValidationError("Invalid prefix")
        return value

    def validate_s3_access_key(self, value):
        return value.strip()

    def validate_presign_minutes(self, value):
        return min(max(value, 1), 24 * 60)

    def validate_max_file_mb(self, value):
        return min(max(value, 10), 100 * 1024)

    def update(self, instance, validated_data):
        # empty secret in a PUT = keep the stored one
        secret = validated_data.pop("s3_secret", None)
        if secret:
            instance.s3_secret = secret.strip()
        return super().update(instance, validated_data)


class RecordingSerializer(serializers.ModelSerializer):
    agent_id = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    site_name = serializers.SerializerMethodField()
    kind = serializers.SerializerMethodField()

    class Meta:
        model = Recording
        fields = [
            "id",
            "source",
            "status",
            "kind",
            "agent_id",
            "hostname",
            "client_name",
            "site_name",
            "technician_name",
            "started",
            "duration",
            "size",
            "filename",
            "content_type",
            "last_error",
        ]

    def get_agent_id(self, obj):
        return obj.agent.agent_id if obj.agent_id else None

    def get_client_name(self, obj):
        return obj.client.name if obj.client_id else None

    def get_site_name(self, obj):
        return obj.site.name if obj.site_id else None

    def get_kind(self, obj):
        # how the dashboard plays it
        return "mcrec" if obj.content_type == "application/x-mcrec" else "video"
