from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.audit.models import AuditLog

EVENT_SNAPSHOT = "EVENT_SNAPSHOT"
EXISTING_EVENT_DATA = "EXISTING_EVENT_DATA"
CURRENT_RELATION_FALLBACK = "CURRENT_RELATION_FALLBACK"
UNAVAILABLE = "UNAVAILABLE"


class AuditLogPresentationMixin:
    @extend_schema_field(serializers.CharField())
    def get_action_label(self, obj):
        return str(obj.get_action_display())

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_actor_name(self, obj):
        return self._resolve_display_name(obj, "actor_name", "actor")[0]

    @extend_schema_field(serializers.CharField())
    def get_actor_name_source(self, obj):
        return self._resolve_display_name(obj, "actor_name", "actor")[1]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_court_name(self, obj):
        return self._resolve_display_name(obj, "court_name", "court")[0]

    @extend_schema_field(serializers.CharField())
    def get_court_name_source(self, obj):
        return self._resolve_display_name(obj, "court_name", "court")[1]

    @extend_schema_field(serializers.DictField(child=serializers.JSONField()))
    def get_summary(self, obj):
        if obj.entity_type == "Booking":
            keys = ("customer_name", "court_name", "start_time", "end_time", "status")
        elif obj.entity_type == "Transaction":
            keys = (
                "customer_name",
                "amount",
                "payment_method",
                "collector_name",
                "court_name",
            )
        elif obj.entity_type == "Settlement":
            keys = (
                "collected_by_name",
                "total_amount",
                "transaction_count",
                "settled_by_name",
                "settled_at",
            )
        else:
            keys = ()

        summary = {}
        for key in keys:
            found, value = self._event_value(obj, key)
            if found:
                summary[key] = value
        return summary

    @staticmethod
    def _event_value(obj, key):
        display_snapshot = (obj.metadata or {}).get("display_snapshot") or {}
        if key in display_snapshot:
            return True, display_snapshot[key]
        for event_data in (
            obj.after_data or {},
            obj.before_data or {},
            obj.metadata or {},
        ):
            if key in event_data:
                return True, event_data[key]
            if key == "court_name":
                nested_court = event_data.get("court")
                if isinstance(nested_court, dict) and "name" in nested_court:
                    return True, nested_court["name"]
        return False, None

    @staticmethod
    def _resolve_display_name(obj, snapshot_key, relation_name):
        display_snapshot = (obj.metadata or {}).get("display_snapshot") or {}
        if snapshot_key in display_snapshot:
            return display_snapshot[snapshot_key] or None, EVENT_SNAPSHOT

        for event_data in (
            obj.after_data or {},
            obj.before_data or {},
            obj.metadata or {},
        ):
            if snapshot_key in event_data:
                return event_data[snapshot_key] or None, EXISTING_EVENT_DATA
            if snapshot_key == "court_name":
                nested_court = event_data.get("court")
                if isinstance(nested_court, dict) and "name" in nested_court:
                    return nested_court["name"] or None, EXISTING_EVENT_DATA

        related_obj = getattr(obj, relation_name)
        if related_obj is None:
            return None, UNAVAILABLE
        if relation_name == "actor":
            full_name = related_obj.get_full_name().strip()
            return full_name or related_obj.username, CURRENT_RELATION_FALLBACK
        return related_obj.name, CURRENT_RELATION_FALLBACK


class AuditLogListSerializer(AuditLogPresentationMixin, serializers.ModelSerializer):
    action_label = serializers.SerializerMethodField()
    actor = serializers.PrimaryKeyRelatedField(read_only=True)
    actor_name = serializers.SerializerMethodField()
    actor_name_source = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()
    court_name_source = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "action_label",
            "entity_type",
            "entity_id",
            "actor",
            "actor_name",
            "actor_name_source",
            "court",
            "court_name",
            "court_name_source",
            "summary",
            "created",
        )
        read_only_fields = fields


class AuditLogDetailSerializer(AuditLogPresentationMixin, serializers.ModelSerializer):
    action_label = serializers.SerializerMethodField()
    actor = serializers.PrimaryKeyRelatedField(read_only=True)
    actor_name = serializers.SerializerMethodField()
    actor_name_source = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()
    court_name_source = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "court_name_source",
            "actor",
            "actor_name",
            "actor_name_source",
            "action",
            "action_label",
            "entity_type",
            "entity_id",
            "before_data",
            "after_data",
            "metadata",
            "summary",
            "created",
        )
        read_only_fields = fields
