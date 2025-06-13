from rest_framework import serializers

from abdm.models import HealthFacility
from abdm.settings import plugin_settings as settings


class HealthFacilitySerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    registered = serializers.BooleanField(read_only=True)
    benefit_name = serializers.CharField(
        default=settings.ABDM_BENEFIT_NAME, read_only=True
    )

    class Meta:
        model = HealthFacility
        exclude = ("deleted",)
