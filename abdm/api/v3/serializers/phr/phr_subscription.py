from rest_framework.serializers import (
    BooleanField,
    CharField,
    ChoiceField,
    ListField,
    Serializer,
    ValidationError,
)

from abdm.api.v3.serializers.phr.phr_consent import DateRangeSerializer, HipSerializer
from abdm.models.base import HealthInformationType, Purpose


class PurposeSerializer(Serializer):
    text = CharField(max_length=50, required=True)
    code = ChoiceField(choices=Purpose.choices, required=True)
    refUri = CharField(max_length=100, required=True)


class SourceSerializer(Serializer):
    hiTypes = ListField(
        child=ChoiceField(choices=HealthInformationType.choices),
        required=True,
        allow_empty=False,
    )
    purpose = PurposeSerializer(required=True)
    hip = HipSerializer(required=False, allow_null=True)
    categories = ListField(
        child=ChoiceField(choices=["LINK", "DATA"]),
        required=True,
        allow_empty=False,
    )
    period = DateRangeSerializer(required=True)


class PhrSubscriptionEditAndApproveSerializer(Serializer):
    isApplicableForAllHIPs = BooleanField(required=True)
    includedSources = SourceSerializer(required=True, many=True)
    excludedSources = SourceSerializer(required=False, many=True, allow_empty=True)

    def validate(self, data):
        is_applicable_for_all_hips = data.get("isApplicableForAllHIPs")
        included_sources = data.get("includedSources")
        excluded_sources = data.get("excludedSources", [])

        if not is_applicable_for_all_hips:
            if len(excluded_sources) == 0:
                raise ValidationError(
                    "When isApplicableForAllHIPs is false, excludedSources must not be empty."
                )

            for i, source in enumerate(included_sources):
                if not source.get("hip"):
                    raise ValidationError(
                        f"Included source {i + 1}: HIP must be specified when isApplicableForAllHIPs is false."
                    )

            for i, source in enumerate(excluded_sources):
                if not source.get("hip"):
                    raise ValidationError(
                        f"Excluded source {i + 1}: HIP must be specified when isApplicableForAllHIPs is false."
                    )

        return data


class PhrSubscriptionRequestDenySerializer(Serializer):
    reason = CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        default="Successfully denied the subscription request",
    )


class PhrSubscriptionStatusUpdateSerializer(Serializer):
    enable = BooleanField(required=True)
