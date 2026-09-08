from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers


class TimezoneAwareDateTimeField(serializers.DateTimeField):
    default_error_messages = {
        **serializers.DateTimeField.default_error_messages,
        "timezone_required": "Datetime must include timezone information.",
    }

    def to_internal_value(self, value):
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is not None and timezone.is_naive(parsed):
                self.fail("timezone_required")
        parsed_value = super().to_internal_value(value)
        if timezone.is_naive(parsed_value):
            self.fail("timezone_required")
        return parsed_value


class EgyptLocationCitySerializer(serializers.Serializer):
    code = serializers.CharField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    type = serializers.CharField()


class EgyptLocationGovernorateSerializer(serializers.Serializer):
    code = serializers.CharField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    region = serializers.CharField()
    cities = EgyptLocationCitySerializer(many=True)


class EgyptLocationPayloadSerializer(serializers.Serializer):
    governorates = EgyptLocationGovernorateSerializer(many=True)
