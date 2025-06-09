from datetime import datetime

from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.abha_number import AbhaNumberSerializer
from abdm.api.v3.serializers.health_id import (
    AbhaLoginCheckAuthMethodsSerializer,
    PhrEnrollmentAbhaAddressExistsSerializer,
    PhrEnrollmentAbhaAddressSuggestionSerializer,
    PhrEnrollmentEnrolAbhaAddressSerializer,
    PhrEnrollmentSendOtpSerializer,
    PhrEnrollmentVerifyOtpSerializer,
    PhrLoginSendOtpSerializer,
    PhrLoginVerifySerializer,
    PhrLoginVerifyUserSerializer,
)
from abdm.models import AbhaNumber, Transaction, TransactionType
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings


class PhrEnrollmentViewSet(GenericViewSet):
    permission_classes = []

    serializer_action_classes = {
        "phr_enrollment__send_otp": PhrEnrollmentSendOtpSerializer,
        "phr_enrollment__verify_otp": PhrEnrollmentVerifyOtpSerializer,
        "phr_enrollment__abha_address_suggestion": PhrEnrollmentAbhaAddressSuggestionSerializer,
        "phr_enrollment__abha_address_exists": PhrEnrollmentAbhaAddressExistsSerializer,
        "phr_enrollment__enrol_abha_address": PhrEnrollmentEnrolAbhaAddressSerializer,
        "phr_login__send_otp": PhrLoginSendOtpSerializer,
        "phr_login__verify": PhrLoginVerifySerializer,
        "phr_login__verify__user": PhrLoginVerifyUserSerializer,
        "phr_login__check_auth_methods": AbhaLoginCheckAuthMethodsSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    def _build_scope(self, login_hint, otp_system, context):
        if context == "enrollment" and login_hint != "abha-number":
            return ["abha-address-enroll", "mobile-verify"]

        base_scope = {
            "abha-address": ["abha-address-login"],
            "abha-number": ["abha-login"],
            "mobile-number": ["abha-address-login"],
        }.get(login_hint, [])

        auth_scope = {
            "aadhaar": ["aadhaar-verify"],
            "abdm": ["mobile-verify"],
            "password": ["password-verify"],
        }.get(otp_system, [])

        return base_scope + auth_scope

    def _update_abha_from_profile(self, data, abha_key="abhaNumber", **tokens):
        date_of_birth = str(
            datetime.strptime(
                f"{data.get('yearOfBirth')}-{data.get('monthOfBirth') or '01'}-{data.get('dayOfBirth') or '01'}",
                "%Y-%m-%d",
            )
        )[:10]

        defaults = {
            "abha_number": data.get(abha_key, ""),
            "health_id": data.get("preferredAbhaAddress", ""),
            "name": data.get("name") or data.get("fullName"),
            "first_name": data.get("firstName"),
            "middle_name": data.get("middleName", ""),
            "last_name": data.get("lastName", ""),
            "gender": data.get("gender"),
            "date_of_birth": date_of_birth,
            "address": data.get("address"),
            "district": data.get("districtName"),
            "state": data.get("stateName"),
            "pincode": data.get("pinCode") or data.get("pincode"),
            "mobile": data.get("mobile"),
            "profile_photo": data.get("profilePhoto", ""),
            **tokens,
        }

        return AbhaNumber.objects.update_or_create(
            abha_number=data.get(abha_key),
            defaults=defaults,
        )

    def _normalize_abha_address(self, address):
        if not address.endswith(f"@{settings.ABDM_CM_ID}"):
            return f"{address}@{settings.ABDM_CM_ID}"
        return address

    @action(detail=False, methods=["post"], url_path="create/send_otp")
    def phr_enrollment__send_otp(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")

        scope = self._build_scope(login_hint, otp_system, "enrollment")

        result = HealthIdService.phr__enrollment__request__otp(
            {
                "scope": scope,
                "type": validated_data.get("type"),
                "value": validated_data.get("value"),
                "otp_system": validated_data.get("otp_system"),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/verify_otp")
    def phr_enrollment__verify_otp(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")

        scope = self._build_scope(login_hint, otp_system, "enrollment")

        result = HealthIdService.phr__enrollment__verify__otp(
            {
                "scope": scope,
                "transaction_id": str(validated_data.get("transaction_id")),
                "otp": validated_data.get("otp"),
            }
        )
        cache.set(
            f"phr_verify_user_token:{result['txnId']}",
            result["tokens"]["token"],
            timeout=300,
        )

        accounts = result.get("accounts", [])
        if result.get("authResult") == "success" and accounts:
            account = accounts[0]
            token = result.get("tokens", {})

            (abha_number, _) = self._update_abha_from_profile(
                account,
                abha_key="ABHANumber",
                access_token=token.get("token"),
                refresh_token=token.get("refreshToken"),
            )

            Transaction.objects.create(
                reference_id=str(validated_data.get("transaction_id")),
                type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
                meta_data={
                    "abha_number": str(abha_number.external_id),
                    "method": "link_via_otp",
                    "type": "abha-number" if login_hint == "abha-number" else "mobile",
                    "system": otp_system,
                },
            )

            return Response(
                {
                    "transaction_id": result.get("txnId"),
                    "detail": result.get("message"),
                    "users": result.get("users"),
                    "abha_number": AbhaNumberSerializer(abha_number).data,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
                "users": result.get("users"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/abha_address_suggestion")
    def phr_enrollment__abha_address_suggestion(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.phr__enrollment__abha_address__suggestion(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "first_name": validated_data.get("first_name"),
                "last_name": validated_data.get("last_name", ""),
                "year_of_birth": validated_data.get("year_of_birth"),
                "month_of_birth": validated_data.get("month_of_birth", ""),
                "day_of_birth": validated_data.get("day_of_birth", ""),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_addresses": result.get("abhaAddressList", []),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/abha_address_exists")
    def phr_enrollment__abha_address_exists(self, request):
        validated_data = self.validate_request(request)

        abha_address = self._normalize_abha_address(
            validated_data.get("abha_address"),
        )

        exists = HealthIdService.phr__enrollment__abha_address__exists(
            {
                "abha_address": abha_address,
            }
        )

        return Response(
            {
                "exists": exists,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/enrol_abha_address")
    def phr_enrollment__enrol_abha_address(self, request):
        validated_data = self.validate_request(request)

        phr_details = validated_data.get("phr_details")

        phr_details_camel = {
            "abhaAddress": phr_details.get("abha_address"),
            "address": phr_details.get("address"),
            "dayOfBirth": phr_details.get("day_of_birth", ""),
            "districtCode": phr_details.get("district_code"),
            "districtName": phr_details.get("district_name"),
            "email": phr_details.get("email", ""),
            "profilePhoto": phr_details.get("profile_photo", ""),
            "firstName": phr_details.get("first_name"),
            "gender": phr_details.get("gender"),
            "lastName": phr_details.get("last_name", ""),
            "middleName": phr_details.get("middle_name", ""),
            "mobile": phr_details.get("mobile"),
            "monthOfBirth": phr_details.get("month_of_birth", ""),
            "password": phr_details.get("password"),
            "pinCode": phr_details.get("pincode"),
            "stateCode": phr_details.get("state_code"),
            "stateName": phr_details.get("state_name"),
            "yearOfBirth": phr_details.get("year_of_birth"),
        }

        result = HealthIdService.phr__enrollment__enrol__abha_address(
            {
                "phr_details": phr_details_camel,
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        abha_addresses = result.get("phrDetails", {}).get("abhaAddress", [])

        abha_number = AbhaNumber.objects.filter(health_id__in=abha_addresses).first()

        if not abha_number:
            return Response(
                {
                    "detail": "Couldn't enroll abha address, ABHA Number not found, Please try again later",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = HealthIdService.phr__profile(
            {"x_token": abha_number.access_token}
        )
        abha_number, _ = self._update_abha_from_profile(
            profile_result,
            access_token=result.get("tokens", {}).get("token"),
            refresh_token=result.get("tokens", {}).get("refreshToken"),
        )

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_ABHA_ADDRESS,
            meta_data={
                "abha_number": str(abha_number.external_id),
            },
        )

        return Response(
            {
                "abha_number": AbhaNumberSerializer(abha_number).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/send_otp")
    def phr_login__send_otp(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        value = validated_data.get("value")

        scope = self._build_scope(login_hint, otp_system, "login")

        if login_hint == "abha-address":
            value = self._normalize_abha_address(value)

        result = HealthIdService.phr__login__request__otp(
            {
                "scope": scope,
                "type": validated_data.get("type"),
                "value": value,
                "otp_system": validated_data.get("otp_system"),
            }
        )
        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/verify")
    def phr_login__verify(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        verify_system = validated_data.get("verify_system")

        scope = self._build_scope(login_hint, verify_system, "login")
        token = None

        if verify_system == "password":
            result = HealthIdService.phr__login__verify__password(
                {
                    "scope": scope,
                    "abha_address": self._normalize_abha_address(
                        validated_data.get("abha_address", "")
                    ),
                    "password": validated_data.get("password"),
                }
            )

            if result.get("authResult") == "failed":
                return Response(
                    {
                        "transaction_id": result.get("txnId"),
                        "detail": result.get("message"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token = {
                "access_token": result.get("tokens", {}).get("token"),
                "refresh_token": result.get("tokens", {}).get("refreshToken"),
                "switchProfileEnabled": result.get("tokens", {}).get(
                    "switchProfileEnabled"
                ),
            }

        else:
            result = HealthIdService.phr__login__verify__otp(
                {
                    "scope": scope,
                    "transaction_id": str(validated_data.get("transaction_id")),
                    "otp": validated_data.get("otp"),
                }
            )

            if result.get("authResult") == "failed":
                return Response(
                    {
                        "transaction_id": result.get("txnId"),
                        "detail": result.get("message"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if login_hint != "abha-address":
                cache.set(
                    f"phr_verify_user_token:{result['txnId']}",
                    result["tokens"]["token"],
                    timeout=300,
                )

                return Response(
                    {
                        "transaction_id": result.get("txnId"),
                        "detail": result.get("message"),
                        "users": result.get("users"),
                    },
                    status=status.HTTP_200_OK,
                )

            token = {
                "txn_id": result.get("txnId"),
                "access_token": result.get("tokens", {}).get("token"),
                "refresh_token": result.get("tokens", {}).get("refreshToken"),
                "switchProfileEnabled": result.get("tokens", {}).get(
                    "switchProfileEnabled"
                ),
            }

        if not token:
            return Response(
                {
                    "detail": "Unable to verify OTP, Please try again later",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = HealthIdService.phr__profile(
            {"x_token": token.get("access_token")}
        )

        abha_number, _ = self._update_abha_from_profile(
            profile_result,
            access_token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
        )

        Transaction.objects.create(
            reference_id=token.get("txn_id")
            or str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "link_via_otp",  # TODO : NEED TO CHANGE THIS FOR PASSWORD
                "type": "abha-address",
                "system": verify_system,
            },
        )

        return Response(
            {"abha_number": AbhaNumberSerializer(abha_number).data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/verify_user")
    def phr_login__verify__user(self, request):
        validated_data = self.validate_request(request)

        t_token = cache.get(
            f"phr_verify_user_token:{validated_data.get('transaction_id')}"
        )

        if not t_token:
            return Response(
                {"detail": "Token expired or not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = HealthIdService.phr__login__verify__user(
            {
                "t_token": t_token,
                "abha_address": self._normalize_abha_address(
                    validated_data.get("abha_address", "")
                ),
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        if not result.get("token"):
            return Response(
                {"detail": "User verification failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = HealthIdService.phr__profile({"x_token": result.get("token")})

        abha_number, _ = self._update_abha_from_profile(
            profile_result,
            access_token=result.get("token"),
            refresh_token=result.get("refreshToken"),
        )

        login_hint = validated_data.get("type")

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "link_via_otp",
                "type": "abha-number" if login_hint == "abha-number" else "mobile",
                "system": validated_data.get("verify_system"),
            },
        )

        return Response(
            {"abha_number": AbhaNumberSerializer(abha_number).data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/check_auth_methods")
    def phr_login__check_auth_methods(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.phr__login_search_auth_methods(
            {
                "abha_address": self._normalize_abha_address(
                    validated_data.get("abha_address")
                ),
            }
        )

        return Response(
            {
                "abha_number": result.get("healthIdNumber"),
                "auth_methods": result.get("authMethods"),
            },
            status=status.HTTP_200_OK,
        )
