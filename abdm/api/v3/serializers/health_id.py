from django.core.cache import cache
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateField,
    Serializer,
    UUIDField,
)
from rest_framework_simplejwt.tokens import RefreshToken

from care_abdm.abdm.authentication import PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX


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


class PhrEnrollmentSendOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("abha-number", "ABHA Number"),
        ("mobile-number", "Mobile Number"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    value = CharField(max_length=50, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)


class PhrEnrollmentVerifyOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("abha-number", "ABHA Number"),
        ("mobile-number", "Mobile Number"),
    ]

    OTP_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    otp = CharField(max_length=6, min_length=6, required=True)
    otp_system = ChoiceField(choices=OTP_SYSTEM_CHOICES, required=True)
    transaction_id = UUIDField(required=True)


class PhrEnrollmentAbhaAddressSuggestionSerializer(Serializer):
    transaction_id = UUIDField(required=True)
    first_name = CharField(max_length=100, required=True)
    last_name = CharField(max_length=100, required=False, allow_blank=True)
    year_of_birth = CharField(max_length=4, required=True)
    month_of_birth = CharField(max_length=2, required=False, allow_blank=True)
    day_of_birth = CharField(max_length=2, required=False, allow_blank=True)


class PhrEnrollmentAbhaAddressExistsSerializer(Serializer):
    abha_address = CharField(max_length=50, min_length=3, required=True)


class PhrAddressDetailsSerializer(Serializer):
    abha_address = CharField(max_length=50, required=True)
    address = CharField(max_length=255, required=True)
    day_of_birth = CharField(max_length=2, required=False, allow_blank=True)
    district_code = CharField(max_length=3, min_length=3, required=True)
    district_name = CharField(max_length=100, required=True)
    email = CharField(max_length=100, required=False, allow_blank=True)
    profile_photo = CharField(required=False, allow_blank=True)
    first_name = CharField(max_length=100, required=True)
    gender = ChoiceField(choices=["M", "F", "O"], required=True)
    last_name = CharField(max_length=100, required=False, allow_blank=True)
    middle_name = CharField(max_length=100, required=False, allow_blank=True)
    mobile = CharField(max_length=10, required=True)
    month_of_birth = CharField(max_length=2, required=False, allow_blank=True)
    password = CharField(write_only=True, required=True)
    pincode = CharField(max_length=6, required=True)
    state_code = CharField(max_length=2, min_length=2, required=True)
    state_name = CharField(max_length=100, required=True)
    year_of_birth = CharField(max_length=4, required=True)


class PhrEnrollmentEnrolAbhaAddressSerializer(Serializer):
    phr_details = PhrAddressDetailsSerializer(required=True)
    transaction_id = UUIDField(required=True)


class PhrLoginSendOtpSerializer(Serializer):
    TYPE_CHOICES = [
        ("mobile-number", "Mobile"),
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


class PhrLoginVerifySerializer(Serializer):
    TYPE_CHOICES = [
        ("mobile-number", "Mobile"),
        ("abha-number", "ABHA Number"),
        ("abha-address", "ABHA Address"),
    ]

    VERIFY_SYSTEM_CHOICES = [
        ("aadhaar", "Aadhaar"),
        ("abdm", "Abdm"),
        ("password", "Password"),
    ]

    type = ChoiceField(choices=TYPE_CHOICES, required=True)
    otp = CharField(max_length=6, min_length=6, required=False)
    abha_address = CharField(max_length=50, min_length=3, required=False)
    password = CharField(min_length=8, required=False)
    verify_system = ChoiceField(choices=VERIFY_SYSTEM_CHOICES, required=True)
    transaction_id = UUIDField(required=False)

    def validate(self, attrs):
        verify_system = attrs.get("verify_system")

        if verify_system == "password":
            if not attrs.get("abha_address"):
                raise ValidationError(
                    {
                        "abha_address": "This field is required for password verification."
                    }
                )
            if not attrs.get("password"):
                raise ValidationError(
                    {"password": "This field is required for password verification."}
                )
        else:
            if not attrs.get("otp"):
                raise ValidationError(
                    {"otp": "This field is required for OTP verification."}
                )
            if not attrs.get("transaction_id"):
                raise ValidationError(
                    {
                        "transaction_id": "This field is required for OTP-based verification."
                    }
                )

        return attrs


class PhrLoginVerifyUserSerializer(Serializer):
    type = ChoiceField(
        choices=[
            ("mobile-number", "Mobile"),
            ("abha-number", "ABHA Number"),
        ],
        required=True,
    )
    verify_system = ChoiceField(
        choices=[
            ("aadhaar", "Aadhaar"),
            ("abdm", "Abdm"),
        ],
        required=True,
    )
    transaction_id = UUIDField(required=True)
    abha_address = CharField(max_length=50, min_length=3, required=True)


class PhrTokenRefreshSerializer(Serializer):
    refresh = CharField()
    access = CharField(read_only=True)

    def validate(self, attrs):
        refresh_token_str = attrs["refresh"]

        old_refresh_token = RefreshToken(refresh_token_str)

        cache_key_for_old_token = (
            f"{PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX}{refresh_token_str}"
        )
        if cache.get(cache_key_for_old_token):
            raise PermissionDenied(
                "This refresh token has been invalidated. Please log in again."
            )
        if "abha_address" not in old_refresh_token.payload:
            raise ValidationError(
                {"refresh": "Token is missing the required 'abha_address' claim."}
            )

        cache.set(cache_key_for_old_token, "invalidated_after_rotation", timeout=1800)
        new_rotated_refresh_token = RefreshToken()

        new_rotated_refresh_token["abha_address"] = old_refresh_token.payload[
            "abha_address"
        ]

        return {
            "access_token": str(new_rotated_refresh_token.access_token),
            "refresh_token": str(new_rotated_refresh_token),
        }
