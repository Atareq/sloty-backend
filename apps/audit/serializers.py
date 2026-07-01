from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogListSerializer(serializers.ModelSerializer):
    actor = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "entity_type",
            "entity_id",
            "actor",
            "court",
            "created",
        )
        read_only_fields = fields


class AuditLogDetailSerializer(serializers.ModelSerializer):
    actor = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "club",
            "court",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "before_data",
            "after_data",
            "metadata",
            "created",
        )
        read_only_fields = fields
