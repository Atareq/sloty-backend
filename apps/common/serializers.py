from rest_framework import serializers


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
