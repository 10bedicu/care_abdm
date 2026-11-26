from rest_framework import serializers

from abdm.api.serializers.abha_number import AbhaNumberSerializer
from abdm.api.serializers.base import EMRPydanticModelField
from abdm.models.consent import ConsentArtefact, ConsentRequest
from care.emr.resources.user.spec import UserSpec


class ConsentArtefactSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)

    class Meta:
        model = ConsentArtefact
        exclude = (
            "deleted",
            "external_id",
            "health_facility",
            "patient",
            "key_material_private_key",
            "key_material_public_key",
            "key_material_nonce",
            "key_material_algorithm",
            "key_material_curve",
            "signature",
        )


class ConsentRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    encounter_id = serializers.CharField(
        source="encounter__external_id", read_only=True
    )
    # the ABHA address the request was raised against, and the record it belongs to
    abha_address = serializers.SerializerMethodField()
    abha_number_object = serializers.SerializerMethodField()
    requester = EMRPydanticModelField(UserSpec, read_only=True)
    consent_artefacts = ConsentArtefactSerializer(many=True, read_only=True)

    class Meta:
        model = ConsentRequest
        exclude = ("deleted", "external_id", "abha_auth")
        # the patient and the facility of a request come from its encounter
        extra_kwargs = {"encounter": {"required": True, "allow_null": False}}

    def get_abha_address(self, obj):
        return obj.abha_auth.abha_address if obj.abha_auth else None

    def get_abha_number_object(self, obj):
        auth = obj.abha_auth
        if not auth:
            return None

        return AbhaNumberSerializer(
            auth.abha_number,
            context={**self.context, "health_facility": auth.health_facility},
        ).data
