from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogActionLabelMixin:
    def get_action_label(self, obj):
        return str(obj.get_action_display())


class AuditLogListSerializer(AuditLogActionLabelMixin, serializers.ModelSerializer):
    action_label = serializers.SerializerMethodField()
    actor = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "action_label",
            "entity_type",
            "entity_id",
            "actor",
            "court",
            "created",
        )
        read_only_fields = fields


class AuditLogDetailSerializer(AuditLogActionLabelMixin, serializers.ModelSerializer):
    action_label = serializers.SerializerMethodField()
    actor = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "club",
            "court",
            "actor",
            "action",
            "action_label",
            "entity_type",
            "entity_id",
            "before_data",
            "after_data",
            "metadata",
            "created",
        )
        read_only_fields = fields
