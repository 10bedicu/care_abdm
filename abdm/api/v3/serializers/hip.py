from datetime import datetime

from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateTimeField,
    FloatField,
    IntegerField,
    ListField,
    Serializer,
    URLField,
    UUIDField,
)

from abdm.models import (
    AccessMode,
    FrequencyUnit,
    HealthInformationType,
    Purpose,
    Status,
)


class HipTokenOnGenerateTokenSerializer(Serializer):
    class ResponseSerializer(Serializer):
        requestId = UUIDField(required=True)

    abhaAddress = CharField(max_length=50, required=True)
    linkToken = CharField(max_length=1000, required=True)
    response = ResponseSerializer(required=True)


class LinkOnCarecontextSerializer(Serializer):

    class ErrorSerializer(Serializer):
        code = CharField(max_length=50, required=True)
        message = CharField(max_length=1000, required=True)

    class ResponseSerializer(Serializer):
        requestId = UUIDField(required=True)

    abhaAddress = CharField(max_length=50, required=True)
    status = CharField(max_length=1000, required=False)
    error = ErrorSerializer(required=False)
    response = ResponseSerializer(required=True)


class HipPatientCareContextDiscoverSerializer(Serializer):
    class PatientSerializer(Serializer):
        class IdentifierSerializer(Serializer):
            type = ChoiceField(
                choices=["MOBILE", "ABHA_NUMBER", "MR", "abhaAddress"], required=True
            )
            value = CharField(max_length=255, required=True)

        id = CharField(max_length=50, required=True)
        name = CharField(max_length=100, required=True)
        gender = ChoiceField(choices=["M", "F", "O"], required=True)
        yearOfBirth = IntegerField(required=True)
        verifiedIdentifiers = IdentifierSerializer(
            many=True, required=False, allow_null=True
        )
        unverifiedIdentifiers = IdentifierSerializer(
            many=True, required=False, allow_null=True
        )

    transactionId = UUIDField(required=True)
    patient = PatientSerializer(required=True)


class HipLinkCareContextInitSerializer(Serializer):
    class PatientSerializer(Serializer):
        class CareContextSerializer(Serializer):
            referenceNumber = CharField(required=True)

        referenceNumber = CharField(required=True)
        careContexts = CareContextSerializer(many=True, required=True)
        hiType = ChoiceField(
            choices=HealthInformationType.choices,
            required=True,
        )
        count = IntegerField(required=True)

    transactionId = UUIDField(required=True)
    abhaAddress = CharField(max_length=50, required=True)
    patient = PatientSerializer(many=True, required=True)


class HipLinkCareContextConfirmSerializer(Serializer):
    class ConfirmationSerializer(Serializer):
        linkRefNumber = CharField(max_length=50, required=True)
        token = CharField(max_length=20, required=True)

    confirmation = ConfirmationSerializer(required=True)


class ConsentRequestHipNotifySerializer(Serializer):
    class NotificationSerializer(Serializer):
        class ConsentDetailSerializer(Serializer):
            class PatientSerializer(Serializer):
                id = CharField(max_length=50, required=True)

            class CareContextSerializer(Serializer):
                patientReference = CharField(required=True)
                careContextReference = CharField(required=True)

            class PurposeSerializer(Serializer):
                text = CharField(max_length=50, required=False)
                code = ChoiceField(choices=Purpose.choices, required=True)
                refUri = CharField(max_length=100, allow_null=True)

            class HipSerializer(Serializer):
                id = CharField(max_length=50, required=True)
                name = CharField(max_length=50, required=False)

            class ConsentManagerSerializer(Serializer):
                id = CharField(max_length=50, required=True)

            class PermissionSerializer(Serializer):

                class DateRangeSerializer(Serializer):
                    fromTime = DateTimeField(source="from", required=True)
                    toTime = DateTimeField(source="to", required=True)

                    def to_internal_value(self, data):
                        return super().to_internal_value(
                            {
                                "fromTime": datetime.strptime(
                                    data.get("from"), "%Y-%m-%dT%H:%M:%S.%fZ"
                                ),
                                "toTime": datetime.strptime(
                                    data.get("to"), "%Y-%m-%dT%H:%M:%S.%fZ"
                                ),
                            }
                        )

                class FrequencySerializer(Serializer):
                    unit = ChoiceField(choices=FrequencyUnit.choices, required=True)
                    value = IntegerField(required=True)
                    repeats = IntegerField(required=True)

                accessMode = ChoiceField(choices=AccessMode.choices, required=True)
                dateRange = DateRangeSerializer(required=True)
                frequency = FrequencySerializer(required=True)

            schemaVersion = CharField(max_length=50, required=True)
            consentId = UUIDField(required=True)
            createdAt = DateTimeField(required=True)
            patient = PatientSerializer(required=True)
            careContexts = CareContextSerializer(many=True, required=True)
            purpose = PurposeSerializer(required=True)
            hip = HipSerializer(required=True)
            consentManager = ConsentManagerSerializer(required=True)
            hiTypes = ListField(
                child=ChoiceField(choices=HealthInformationType.choices),
                required=True,
            )
            permission = PermissionSerializer(required=True)

        status = ChoiceField(choices=Status.choices, required=True)
        consentId = UUIDField(required=True)
        consentDetail = ConsentDetailSerializer(required=True)
        signature = CharField(max_length=500, required=True)

    notification = NotificationSerializer(required=True)


class HipHealthInformationRequestSerializer(Serializer):
    class HiRequestSerializer(Serializer):
        class ConsentSerializer(Serializer):
            id = UUIDField(required=True)

        class DateRangeSerializer(Serializer):
            fromTime = DateTimeField(source="from", required=True)
            toTime = DateTimeField(source="to", required=True)

            def to_internal_value(self, data):
                return super().to_internal_value(
                    {
                        "fromTime": datetime.strptime(
                            data.get("from"), "%Y-%m-%dT%H:%M:%S.%fZ"
                        ),
                        "toTime": datetime.strptime(
                            data.get("to"), "%Y-%m-%dT%H:%M:%S.%fZ"
                        ),
                    }
                )

        class KeyMaterialSerializer(Serializer):
            class DhPublicKeySerializer(Serializer):
                expiry = DateTimeField(required=True)
                parameters = CharField(max_length=50, required=False)
                keyValue = CharField(max_length=500, required=True)

            cryptoAlg = CharField(max_length=50, required=True)
            curve = CharField(max_length=50, required=True)
            dhPublicKey = DhPublicKeySerializer(required=True)
            nonce = CharField(max_length=50, required=True)

        consent = ConsentSerializer(required=True)
        dateRange = DateRangeSerializer(required=True)
        dataPushUrl = URLField(required=True)
        keyMaterial = KeyMaterialSerializer(required=True)

    transactionId = UUIDField(required=True)
    hiRequest = HiRequestSerializer(required=True)


class HipPatientShareSerializer(Serializer):
    class MetaDataSerializer(Serializer):
        hipId = CharField(max_length=50, required=True)
        context = CharField(max_length=50, required=True)
        hprId = CharField(max_length=50, required=True)
        latitude = FloatField(required=True)
        longitude = FloatField(required=True)

    class ProfileSerializer(Serializer):
        class PatientSerializer(Serializer):
            class AddressSerializer(Serializer):
                line = CharField(
                    max_length=200, required=True, allow_blank=True, allow_null=True
                )
                district = CharField(
                    max_length=50, required=False, allow_blank=True, allow_null=True
                )
                state = CharField(
                    max_length=50, required=False, allow_blank=True, allow_null=True
                )
                pincode = CharField(
                    max_length=50, required=False, allow_blank=True, allow_null=True
                )

            abhaNumber = CharField(max_length=50, required=True)
            abhaAddress = CharField(max_length=50, required=True)
            name = CharField(max_length=50, required=True)
            gender = ChoiceField(choices=["M", "F", "O"], required=True)
            dayOfBirth = IntegerField(required=True)
            monthOfBirth = IntegerField(required=True)
            yearOfBirth = IntegerField(required=True)
            address = AddressSerializer(required=True)
            phoneNumber = CharField(max_length=50, required=True)

        patient = PatientSerializer(required=True)

    intent = ChoiceField(choices=["PROFILE_SHARE"], required=True)
    metaData = MetaDataSerializer(required=True)
    profile = ProfileSerializer(required=True)
