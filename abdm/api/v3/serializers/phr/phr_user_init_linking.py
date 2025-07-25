from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateTimeField,
    IntegerField,
    Serializer,
    UUIDField,
)

from abdm.models import HealthInformationType


class HipSerializer(Serializer):
    id = CharField(required=True, max_length=50)
    name = CharField(required=False, max_length=255)
    type = CharField(required=False, max_length=10)


class IdentifierSerializer(Serializer):
    type = ChoiceField(
        choices=["MOBILE", "ABHA_NUMBER", "MR", "abhaAddress"], required=True
    )
    value = CharField(max_length=255, required=True)


class CareContextSerializer(Serializer):
    referenceNumber = CharField(required=True)
    display = CharField(required=True)


class PatientSerializer(Serializer):
    referenceNumber = CharField(required=True)
    display = CharField(required=True)
    careContexts = CareContextSerializer(many=True, required=True)
    hiType = ChoiceField(choices=HealthInformationType.choices, required=True)
    count = IntegerField(required=True)


class ResponseSerializer(Serializer):
    requestId = UUIDField(required=True)


class ErrorSerializer(Serializer):
    code = CharField(max_length=50, required=True)
    message = CharField(max_length=1000, required=True)


class PhrUserInitLinkingCareContextDiscoverSerializer(Serializer):
    hip = HipSerializer(required=True)
    unverified_identifiers = IdentifierSerializer(many=True, required=False)


class PhrUserInitLinkingCareContextOnDiscoverSerializer(Serializer):
    transactionId = UUIDField(required=True)
    patient = PatientSerializer(many=True, required=False, default=list)
    error = ErrorSerializer(required=False, allow_null=True, default=None)
    response = ResponseSerializer(required=True)
    createdAt = DateTimeField(required=False)

    def to_internal_value(self, data):
        cleaned_data = data.copy()

        patient_raw = cleaned_data.get("patient")
        if patient_raw in [None, "", {}] or (
            isinstance(patient_raw, list)
            and len(patient_raw) == 1
            and patient_raw[0] in [None, "", {}]
        ):
            cleaned_data["patient"] = []

        error_raw = cleaned_data.get("error")
        if error_raw in [None, "", {}]:
            cleaned_data["error"] = None

        return super().to_internal_value(cleaned_data)


class PhrUserInitLinkingCareContextInitSerializer(Serializer):
    transaction_id = UUIDField(required=True)
    patient = PatientSerializer(many=True, required=True)


class MetaSerializer(Serializer):
    communicationMedium = ChoiceField(choices=["MOBILE"], required=True)
    communicationHint = ChoiceField(choices=["OTP"], required=True)
    communicationExpiry = DateTimeField(required=True)


class LinkSerializer(Serializer):
    referenceNumber = CharField(required=True)
    authenticationType = ChoiceField(choices=["DIRECT", "MEDIATE"], required=True)
    meta = MetaSerializer(required=True)


class PhrUserInitLinkingCareContextOnInitSerializer(Serializer):
    transactionId = UUIDField(required=True)
    link = LinkSerializer(required=True)
    error = ErrorSerializer(required=False, allow_null=True, default=None)
    response = ResponseSerializer(required=True)

    def to_internal_value(self, data):
        cleaned_data = data.copy()

        error_raw = cleaned_data.get("error")
        if error_raw in [None, "", {}]:
            cleaned_data["error"] = None

        return super().to_internal_value(cleaned_data)


class PhrUserInitLinkingCareContextConfirmSerializer(Serializer):
    link_ref_number = CharField(max_length=50, required=True)
    token = CharField(max_length=20, required=True)


class PhrUserInitLinkingCareContextOnConfirmSerializer(Serializer):
    patient = PatientSerializer(many=True, required=False, default=list)
    error = ErrorSerializer(required=False, allow_null=True, default=None)
    response = ResponseSerializer(required=True)

    def to_internal_value(self, data):
        cleaned_data = data.copy()

        patient_raw = cleaned_data.get("patient")
        if patient_raw in [None, "", {}] or (
            isinstance(patient_raw, list)
            and len(patient_raw) == 1
            and patient_raw[0] in [None, "", {}]
        ):
            cleaned_data["patient"] = []

        error_raw = cleaned_data.get("error")
        if error_raw in [None, "", {}]:
            cleaned_data["error"] = None

        return super().to_internal_value(cleaned_data)
