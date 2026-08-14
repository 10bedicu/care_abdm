from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateTimeField,
    FloatField,
    IntegerField,
    Serializer,
    UUIDField,
)


class ScanPayErrorSerializer(Serializer):
    code = CharField(max_length=50, required=True)
    message = CharField(max_length=1000, required=True)


class ScanPayResponseSerializer(Serializer):
    requestId = UUIDField(required=True)


class PatientShareOpenOrderSerializer(Serializer):
    class MetadataSerializer(Serializer):
        hipId = CharField(max_length=50, required=True)
        counterId = CharField(
            max_length=50, required=False, allow_null=True, allow_blank=True
        )

    class ProfileSerializer(Serializer):
        class PatientSerializer(Serializer):
            class AddressSerializer(Serializer):
                line = CharField(required=False, allow_blank=True, allow_null=True)
                district = CharField(required=False, allow_blank=True, allow_null=True)
                state = CharField(required=False, allow_blank=True, allow_null=True)
                pincode = CharField(required=False, allow_blank=True, allow_null=True)

            abhaNumber = CharField(max_length=50, required=False, allow_null=True)
            abhaAddress = CharField(max_length=50, required=True)
            name = CharField(max_length=100, required=False, allow_null=True)
            gender = ChoiceField(choices=["M", "F", "O"], required=False)
            dayOfBirth = IntegerField(required=False, allow_null=True)
            monthOfBirth = IntegerField(required=False, allow_null=True)
            yearOfBirth = IntegerField(required=False, allow_null=True)
            address = AddressSerializer(required=False, allow_null=True)
            phoneNumber = CharField(max_length=50, required=False, allow_null=True)

        patient = PatientSerializer(required=True)

    intent = ChoiceField(choices=["OPEN_PAYMENT_ORDER"], required=True)
    metadata = MetadataSerializer(required=True)
    profile = ProfileSerializer(required=True)


class PatientSelectionSerializer(Serializer):
    class ProcedureSerializer(Serializer):
        class ServiceSerializer(Serializer):
            serviceId = CharField(max_length=100, required=True)
            name = CharField(max_length=1000, required=False, allow_null=True)
            description = CharField(required=False, allow_blank=True, allow_null=True)
            amount = FloatField(required=False, allow_null=True)

        category = CharField(max_length=100, required=True)
        services = ServiceSerializer(many=True, required=True)

    intent = ChoiceField(choices=["PAYMENT_ORDER"], required=True)
    openOrderRequestId = UUIDField(required=True)
    abhaAddress = CharField(max_length=50, required=True)
    procedures = ProcedureSerializer(many=True, required=True)


class PatientScanPayOnNotifySerializer(Serializer):
    class AcknowledgementSerializer(Serializer):
        status = CharField(max_length=50, required=True)
        abhaAddress = CharField(max_length=50, required=False, allow_null=True)
        transactionId = CharField(max_length=100, required=False, allow_null=True)
        orderNumber = CharField(max_length=100, required=False, allow_null=True)
        openOrderRequestId = UUIDField(required=False, allow_null=True)
        paymentDate = DateTimeField(required=False, allow_null=True)
        paymentReceiptLink = CharField(required=False, allow_null=True)

    acknowledgement = AcknowledgementSerializer(required=False)
    error = ScanPayErrorSerializer(required=False)
    response = ScanPayResponseSerializer(required=True)


class PatientScanPayOrderStatusSerializer(Serializer):
    class QueryStatusSerializer(Serializer):
        orderNumber = CharField(max_length=100, required=True)
        abhaAddress = CharField(max_length=50, required=True)
        openOrderRequestId = UUIDField(required=True)

    queryStatus = QueryStatusSerializer(required=True)
