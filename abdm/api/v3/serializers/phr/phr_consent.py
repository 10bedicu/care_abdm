from rest_framework.exceptions import ValidationError
from rest_framework.serializers import (
    BooleanField,
    CharField,
    ChoiceField,
    IntegerField,
    ListField,
    Serializer,
    UUIDField,
)

from abdm.models.base import AccessMode, HealthInformationType


class CareContextSerializer(Serializer):
    patientReference = CharField(required=True)
    careContextReference = CharField(required=True)


class HipSerializer(Serializer):
    id = CharField(required=True)
    name = CharField(required=False)
    type = CharField(required=False)


class DateRangeSerializer(Serializer):
    def to_internal_value(self, data):
        from_value = data.get("from")
        to_value = data.get("to")

        if not from_value:
            raise ValidationError({"from": ["This field is required."]})
        if not to_value:
            raise ValidationError({"to": ["This field is required."]})

        return {
            "from": from_value,
            "to": to_value,
        }


class FrequencySerializer(Serializer):
    unit = ChoiceField(choices=["HOUR", "DAY", "WEEK", "MONTH", "YEAR"], required=True)
    value = IntegerField(required=True, min_value=1)
    repeats = IntegerField(required=True, min_value=0)


class PermissionSerializer(Serializer):
    accessMode = ChoiceField(choices=AccessMode.choices, required=True)
    dateRange = DateRangeSerializer(required=True)
    dataEraseAt = CharField(required=True)
    frequency = FrequencySerializer(required=True)


class ConsentSerializer(Serializer):
    careContexts = CareContextSerializer(required=True, many=True, allow_empty=False)
    hiTypes = ListField(
        child=ChoiceField(choices=HealthInformationType.choices),
        required=True,
        allow_empty=False,
    )
    hip = HipSerializer(required=True)
    permission = PermissionSerializer(required=True)


class PhrConsentRequestApproveSerializer(Serializer):
    consents = ConsentSerializer(required=True, many=True, allow_empty=False)


class PhrConsentRequestDenySerializer(Serializer):
    reason = CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        default="Successfully denied the consent request",
    )


class PhrConsentRequestRevokeSerializer(Serializer):
    consents = ListField(
        child=UUIDField(required=True), required=True, allow_empty=False
    )


class PhrConsentAutoApproveUpdateSerializer(Serializer):
    enable = BooleanField(required=True)
