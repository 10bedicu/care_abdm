from rest_framework.serializers import (
    CharField,
    ChoiceField,
    Serializer,
    UUIDField,
)


class PhrRequestTokenSerializer(Serializer):
    refresh_token = CharField(required=True)


class PhrProfileSwitchVerifySerializer(Serializer):
    abha_address = CharField(max_length=50, min_length=3, required=True)
    transaction_id = UUIDField(required=True)


class PhrProfileRequestOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("mobile-number", "Mobile"),
        ("abha-number", "ABHA Number"),
        ("email", "Email"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    value = CharField(max_length=50, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)


class PhrProfileVerifyOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("mobile-number", "Mobile"),
        ("abha-number", "ABHA Number"),
        ("email", "Email"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    otp = CharField(max_length=6, min_length=6, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)
    transaction_id = UUIDField(required=True)
    action = ChoiceField(
        choices=[
            ("SELECT_PREFERRED_ABHA", "Select Preferred ABHA"),
            ("LINK", "Link"),
            ("DE_LINK", "De-Link"),
            ("UPDATE_MOBILE", "Update Mobile"),
            ("UPDATE_EMAIL", "Update Email"),
        ],
        required=True,
    )


class PhrProfileUpdateSerializer(Serializer):
    address = CharField(max_length=255, required=True)
    first_name = CharField(max_length=100, required=True)
    middle_name = CharField(max_length=100, required=False, allow_blank=True)
    last_name = CharField(max_length=100, required=False, allow_blank=True)
    gender = ChoiceField(choices=["M", "F", "O"], required=True)
    day_of_birth = CharField(max_length=2, required=False, allow_blank=True)
    month_of_birth = CharField(max_length=2, required=False, allow_blank=True)
    year_of_birth = CharField(max_length=4, required=True)
    state_code = CharField(max_length=2, required=True)
    state_name = CharField(max_length=100, required=True)
    district_code = CharField(max_length=3, required=True)
    district_name = CharField(max_length=100, required=True)
    pincode = CharField(max_length=6, required=True)
    profile_photo = CharField(required=False, allow_blank=True)


class PhrProfileResetPasswordSerializer(Serializer):
    abha_address = CharField(max_length=50, min_length=3, required=True)
    password = CharField(write_only=True, min_length=8, required=True)


class PhrProfileLogoutSerializer(Serializer):
    access_token = CharField(required=True, write_only=True)
    refresh_token = CharField(required=True, write_only=True)
