from datetime import datetime

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.profile import (
    PhrProfileRequestOtpSerializer,
    PhrProfileSwitchVerifySerializer,
    PhrProfileVerifyOtpSerializer,
    PhrRequestTokenSerializer,
)
from abdm.models import AbhaNumber
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings


class PhrProfileViewSet(GenericViewSet):
    permission_classes = []

    serializer_action_classes = {
        "phr_profile__switch__verify_user": PhrProfileSwitchVerifySerializer,
        "phr__request__token": PhrRequestTokenSerializer,
        "phr_profile__request_otp": PhrProfileRequestOtpSerializer,
        "phr_profile__verify_otp": PhrProfileVerifyOtpSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    def _update_abha_from_profile(self, profile_data, **tokens):
        date_of_birth = str(
            datetime.strptime(
                f"{profile_data.get('yearOfBirth')}-{profile_data.get('monthOfBirth')}-{profile_data.get('dayOfBirth')}",
                "%Y-%m-%d",
            )
        )[0:10]

        defaults = {
            "abha_number": profile_data.get("abhaNumber"),
            "health_id": profile_data.get("preferredAbhaAddress"),
            "name": profile_data.get("fullName"),
            "first_name": profile_data.get("firstName", ""),
            "middle_name": profile_data.get("middleName", ""),
            "last_name": profile_data.get("lastName", ""),
            "gender": profile_data.get("gender"),
            "date_of_birth": date_of_birth,
            "address": profile_data.get("address"),
            "pincode": profile_data.get("pinCode"),
            "district": profile_data.get("districtName"),
            "state": profile_data.get("stateName"),
            "email": profile_data.get("email"),
            "mobile": profile_data.get("mobile"),
            "profile_photo": profile_data.get("profilePhoto"),
            **tokens,
        }

        return AbhaNumber.objects.update_or_create(
            abha_number=profile_data.get("abhaNumber"),
            defaults=defaults,
        )

    def _normalize_abha_address(self, address):
        if not address.endswith(f"@{settings.ABDM_CM_ID}"):
            return f"{address}@{settings.ABDM_CM_ID}"
        return address

    def _build_profile_scope(self, login_hint, otp_system):
        base_scope = {
            "abha-number": ["abha-login"],
            "mobile-number": ["abha-address-profile"],
            "email": ["abha-address-profile"],
        }.get(login_hint, [])

        if otp_system == "abdm":
            auth_scope = (
                ["email-verify"] if login_hint == "email" else ["mobile-verify"]
            )
        else:
            auth_scope = ["aadhaar-verify"]

        return base_scope + auth_scope

    @action(detail=False, methods=["get"], url_path="profile/request_token")
    def phr__request__token(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.phr__request__token(
            {"r_token": validated_data.get("refresh_token")}
        )

        cache.set(
            "phr__access__token",
            result.get("tokens", {}).get("token"),
            timeout=1800,
        )

        return Response(
            result,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="profile")
    def phr_profile(self, request):
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Invalid Token. Please login again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        result = HealthIdService.phr__profile({"x_token": x_token})

        self._update_abha_from_profile(result)

        return Response(
            {
                "profile": result,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="profile/switch")
    def phr_profile__switch(self, request):
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Access token not found. Please request a token first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        result = HealthIdService.phr__profile__switch(
            {
                "x_token": x_token,
            }
        )

        cache.set(
            f"phr__profile__switch__token:{result.get('txnId')}",
            result.get("tokens", {}).get("token"),
            timeout=300,
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "users": result.get("users", []),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="profile/switch/verify_user")
    def phr_profile__switch__verify_user(self, request):
        validated_data = self.validate_request(request)

        t_token = cache.get(
            f"phr__profile__switch__token:{validated_data.get('transaction_id')}"
        )

        if not t_token:
            return Response(
                {"detail": "Token expired or not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = HealthIdService.phr__profile__switch__verify_user(
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

        self._update_abha_from_profile(
            profile_result,
            access_token=result.get("token"),
            refresh_token=result.get("refreshToken"),
        )

        return Response(
            {
                "token": result.get("token"),
                "refresh_token": result.get("refreshToken"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="profile/phr_card")
    def phr_profile__card(self, request):
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Invalid Token. Please login again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        phr_card = HealthIdService.phr_profile__card({"x_token": x_token})

        return HttpResponse(
            phr_card,
            content_type="image/png",
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="profile/request_otp")
    def phr_profile__request_otp(self, request):
        validated_data = self.validate_request(request)
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Access token not found. Please request a token first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        value = validated_data.get("value")

        scope = self._build_profile_scope(login_hint, otp_system)

        result = HealthIdService.phr__profile__request__otp(
            {
                "scope": scope,
                "type": validated_data.get("type"),
                "value": value,
                "otp_system": validated_data.get("otp_system"),
                "x_token": x_token,
            }
        )
        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="profile/verify_otp")
    def phr_profile__verify_otp(self, request):
        validated_data = self.validate_request(request)
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Access token not found. Please request a token first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        action = validated_data.get("action")

        scope = self._build_profile_scope(login_hint, otp_system)

        result = HealthIdService.phr__profile__verify__otp(
            {
                "scope": scope,
                "otp": validated_data.get("otp"),
                "transaction_id": str(validated_data.get("transaction_id")),
                "x_token": x_token,
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

        if action in ("LINK", "DE_LINK"):
            result = HealthIdService.phr__profile__link__delink(
                {
                    "action": action,
                    "transaction_id": str(result.get("txnId")),
                    "x_token": x_token,
                }
            )

            return Response(
                {
                    "detail": result.get("message"),
                },
                status=status.HTTP_200_OK,
            )

        if action == "SELECT_PREFERRED_ABHA":
            result = HealthIdService.phr__profile__select__preferred__abha(
                {
                    "transaction_id": str(result.get("txnId")),
                    "x_token": x_token,
                }
            )

            return Response(
                {
                    "abhaAddress": result.get("abhaAddress"),
                    "status": result.get("status"),
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="profile/update")
    def phr_profile__update(self, request):
        validated_data = self.validate_request(request)
        # TODO : Implement proper authentication and authorization checks here
        x_token = cache.get("phr__access__token")
        if not x_token:
            return Response(
                {"detail": "Access token not found. Please request a token first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # END TODO

        profile_data = validated_data.get("profile_data")

        result = HealthIdService.phr__profile__update(
            {
                "x_token": x_token,
                "profile_data": profile_data,
            }
        )

        return Response(
            {
                "profile": result,
            },
            status=status.HTTP_200_OK,
        )
