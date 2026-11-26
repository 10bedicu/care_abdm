from rest_framework import serializers

from abdm.api.serializers.base import EMRPydanticModelField
from abdm.models import AbhaNumber
from abdm.service.abha import current_abha_address, latest_valid_auth
from care.emr.resources.patient.spec import PatientRetrieveSpec


class AbhaNumberSerializer(serializers.ModelSerializer):
    """An ABHA record, as seen from the health facility passed in the serializer
    context as ``health_facility``."""

    id = serializers.CharField(source="external_id", read_only=True)
    patient = serializers.UUIDField(
        source="patient.external_id", required=False, allow_null=True
    )
    patient_object = EMRPydanticModelField(
        PatientRetrieveSpec,
        source="patient",
        read_only=True,
    )
    new = serializers.BooleanField(read_only=True)
    date_of_birth = serializers.CharField(source="parsed_date_of_birth", read_only=True)
    abha_address = serializers.SerializerMethodField()
    auth = serializers.SerializerMethodField()

    class Meta:
        model = AbhaNumber
        exclude = ("deleted", "access_token", "refresh_token", "abha_address_legacy")

    def get_abha_address(self, obj):
        return current_abha_address(obj, self.context.get("health_facility"))

    def get_auth(self, obj):
        auth = latest_valid_auth(obj, self.context.get("health_facility"))
        if not auth:
            return None

        return {
            "id": str(auth.external_id),
            "abha_address": auth.abha_address,
            "method": auth.method,
            "created_date": serializers.DateTimeField().to_representation(
                auth.created_date
            ),
        }


class AbhaNumberCreateSerializer(serializers.Serializer):
    """An ABHA read off a QR code or card at a facility (scan and pull)."""

    facility = serializers.UUIDField(required=True)

    abha_number = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    abha_address = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    first_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    middle_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    last_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    gender = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    date_of_birth = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    address = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    district = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    state = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    pincode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    profile_photo = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )

    def validate(self, attrs):
        if not attrs.get("abha_number") and not attrs.get("abha_address"):
            raise serializers.ValidationError(
                "Provide an 'abha_number' or an 'abha_address'."
            )
        return attrs
