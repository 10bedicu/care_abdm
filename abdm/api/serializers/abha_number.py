# ModelSerializer
from rest_framework import serializers

from abdm.models import AbhaNumber
from care.facility.api.serializers.patient import PatientDetailSerializer
from care.facility.models import PatientRegistration
from care.utils.serializers.fields import ExternalIdSerializerField


class AbhaNumberSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    patient = ExternalIdSerializerField(
        queryset=PatientRegistration.objects.all(), required=False, allow_null=True
    )
    patient_object = PatientDetailSerializer(source="patient", read_only=True)
    new = serializers.BooleanField(read_only=True)
    date_of_birth = serializers.CharField(source="parsed_date_of_birth", read_only=True)

    class Meta:
        model = AbhaNumber
        exclude = ("deleted", "access_token", "refresh_token")
