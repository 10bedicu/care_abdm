from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateField,
    Serializer,
    UUIDField,
)


class AbhaCreateVerifyAadhaarDemographicsSerializer(Serializer):
    aadhaar = CharField(max_length=12, min_length=12, required=True)
    transaction_id = UUIDField(required=False)
    name = CharField(max_length=50, required=True)
    date_of_birth = DateField(required=True)
    gender = ChoiceField(choices=["M", "F", "O"], required=True)
    state_code = CharField(max_length=2, min_length=2, required=True)
    district_code = CharField(max_length=3, min_length=3, required=True)
    address = CharField(max_length=1000, required=False)
    pin_code = CharField(max_length=6, min_length=6, required=False)
    mobile = CharField(max_length=10, min_length=10, required=False)
    profile_photo = CharField(max_length=1000, required=False)


class AbhaCreateSendAadhaarOtpSerializer(Serializer):
    aadhaar = CharField(max_length=12, min_length=12, required=True)
    transaction_id = UUIDField(required=False)


class AbhaCreateVerifyAadhaarOtpSerializer(Serializer):
    transaction_id = UUIDField(required=True)
    otp = CharField(max_length=6, min_length=6, required=True)
    mobile = CharField(max_length=10, min_length=10, required=True)


class AbhaCreateLinkMobileNumberSerializer(Serializer):
    mobile = CharField(max_length=10, min_length=10, required=True)
    transaction_id = UUIDField(required=True)


class AbhaCreateVerifyMobileOtpSerializer(Serializer):
    transaction_id = UUIDField(required=True)
    otp = CharField(max_length=6, min_length=6, required=True)


class AbhaCreateAbhaAddressSuggestionSerializer(Serializer):
    transaction_id = UUIDField(required=True)


class AbhaCreateEnrolAbhaAddressSerializer(Serializer):
    transaction_id = UUIDField(required=True)
    abha_address = CharField(min_length=3, max_length=50, required=True)


class AbhaLoginSendOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("mobile", "Mobile"),
        ("abha-number", "ABHA Number"),
        ("abha-address", "ABHA Address"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    value = CharField(max_length=50, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)


class AbhaLoginVerifyOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("mobile", "Mobile"),
        ("abha-number", "ABHA Number"),
        ("abha-address", "ABHA Address"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    otp = CharField(max_length=6, min_length=6, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)
    transaction_id = UUIDField(required=True)


class AbhaLoginCheckAuthMethodsSerializer(Serializer):
    abha_address = CharField(max_length=50, min_length=3, required=True)


class LinkAbhaNumberAndPatientSerializer(Serializer):
    patient = UUIDField(required=True)
    abha_number = UUIDField(required=True)
