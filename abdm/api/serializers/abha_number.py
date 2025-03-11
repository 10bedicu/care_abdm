# ModelSerializer

from rest_framework import serializers

from abdm.models import AbhaNumber
from care.emr.models.patient import Patient
from care.emr.resources.patient.spec import PatientRetrieveSpec
from care.utils.serializers.fields import ExternalIdSerializerField
from abdm.api.serializers.base import EMRPydanticModelField


class AbhaNumberSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    patient = ExternalIdSerializerField(
        queryset=Patient.objects.all(), required=False, allow_null=True
    )
    patient_object = EMRPydanticModelField(
        PatientRetrieveSpec,
        source="patient",
        read_only=True,
    )
    new = serializers.BooleanField(read_only=True)

    class Meta:
        model = AbhaNumber
        exclude = ("deleted", "access_token", "refresh_token")
