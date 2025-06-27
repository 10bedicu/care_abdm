from datetime import datetime
from logging import getLogger

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from abdm.api.v3.serializers.phr.profile import (
    PhrProfileLogoutSerializer,
    PhrProfileRequestOtpSerializer,
    PhrProfileResetPasswordSerializer,
    PhrProfileSwitchVerifySerializer,
    PhrProfileUpdateSerializer,
    PhrProfileVerifyOtpSerializer,
    PhrRequestTokenSerializer,
)
from abdm.authentication import (
    PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX,
    PHR_TEMP_REFRESH_TOKEN_INVALIDATION_PREFIX,
    IsPhrAuthenticated,
    PhrCustomAuthentication,
)
from abdm.models import AbhaNumber
from abdm.service.helper import (
    PHR_ACCESS_TOKEN_CACHE_TIMEOUT,
    PHR_ACCESS_TOKEN_PREFIX,
    PHR_REFRESH_TOKEN_CACHE_TIMEOUT,
    PHR_REFRESH_TOKEN_PREFIX,
    cache_phr_tokens,
    remove_cached_phr_tokens,
)
from abdm.service.v3.phr.profile import PhrProfileService
from abdm.settings import plugin_settings as settings

PHR_PROFILE_SWITCH_VERIFY_TOKEN_CACHE_KEY = "phr__profile__switch__token"

logger = getLogger(__name__)


class PhrProfileViewSet(GenericViewSet):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]

    serializer_action_classes = {
        "phr__request__token": PhrRequestTokenSerializer,
        "phr_profile__switch__verify_user": PhrProfileSwitchVerifySerializer,
        "phr_profile__request_otp": PhrProfileRequestOtpSerializer,
        "phr_profile__verify_otp": PhrProfileVerifyOtpSerializer,
        "phr_profile__update": PhrProfileUpdateSerializer,
        "phr_profile__reset__password": PhrProfileResetPasswordSerializer,
        "phr_profile__logout": PhrProfileLogoutSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    def _get_x_token(self, request):
        abha_address = self._normalize_abha_address(request.user.abha_address)
        access_key = f"{PHR_ACCESS_TOKEN_PREFIX}{abha_address}"
        refresh_key = f"{PHR_REFRESH_TOKEN_PREFIX}{abha_address}"

        x_token = cache.get(access_key)
        if x_token:
            return x_token

        refresh_token = cache.get(refresh_key)

        result = PhrProfileService.phr__request__token({"r_token": refresh_token})
        tokens = result.get("tokens") or {}

        access_token = tokens.get("token")
        new_refresh_token = tokens.get("refreshToken")

        cache.set(access_key, access_token, timeout=PHR_ACCESS_TOKEN_CACHE_TIMEOUT)
        cache.set(
            refresh_key, new_refresh_token, timeout=PHR_REFRESH_TOKEN_CACHE_TIMEOUT
        )

        return access_token

    def _update_abha_from_profile(self, data, abha_key="abhaNumber", **tokens):
        date_of_birth = str(
            datetime.strptime(
                f"{data.get('yearOfBirth')}-{data.get('monthOfBirth') or '01'}-{data.get('dayOfBirth') or '01'}",
                "%Y-%m-%d",
            )
        )[:10]

        defaults = {
            "abha_number": data.get(abha_key),
            "health_id": data.get("abhaAddress"),
            "name": data.get("name") or data.get("fullName"),
            "first_name": data.get("firstName"),
            "middle_name": data.get("middleName"),
            "last_name": data.get("lastName"),
            "gender": data.get("gender"),
            "email": data.get("email"),
            "date_of_birth": date_of_birth,
            "address": data.get("address"),
            "district": data.get("districtName"),
            "state": data.get("stateName"),
            "pincode": data.get("pinCode") or data.get("pincode"),
            "mobile": data.get("mobile"),
            "profile_photo": data.get("profilePhoto"),
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

    def _build_scope(self, login_hint, otp_system):
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

    def _get_tokens(self, abha_address, id):
        refresh_token = RefreshToken()
        refresh_token["abha_address"] = self._normalize_abha_address(abha_address)
        refresh_token["id"] = id
        return {
            "refresh_token": str(refresh_token),
            "access_token": str(refresh_token.access_token),
        }

    # TEMPORARY ACTION FOR PHR PROFILE
    @action(detail=False, methods=["get"], url_path="request_token")
    def phr__request__token(self, request):
        validated_data = self.validate_request(request)

        result = PhrProfileService.phr__request__token(
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

    @action(detail=False, methods=["get"], url_path="get_profile")
    def phr_profile(self, request):
        x_token = self._get_x_token(request)

        profile = PhrProfileService.phr__profile({"x_token": x_token})

        self._update_abha_from_profile(profile)

        return Response(
            profile,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="switch")
    def phr_profile__switch(self, request):
        x_token = self._get_x_token(request)

        result = PhrProfileService.phr__profile__switch(
            {
                "x_token": x_token,
            }
        )

        cache.set(
            f"{PHR_PROFILE_SWITCH_VERIFY_TOKEN_CACHE_KEY}:{result.get('txnId')}",
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

    @action(detail=False, methods=["post"], url_path="switch/verify_user")
    def phr_profile__switch__verify_user(self, request):
        validated_data = self.validate_request(request)
        abha_address = self._normalize_abha_address(validated_data.get("abha_address"))
        t_token = cache.get(
            f"{PHR_PROFILE_SWITCH_VERIFY_TOKEN_CACHE_KEY}:{validated_data.get('transaction_id')}"
        )

        if not t_token:
            return Response(
                {"detail": "Token expired or not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = PhrProfileService.phr__profile__switch__verify_user(
            {
                "t_token": t_token,
                "abha_address": abha_address,
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        if not result.get("token"):
            return Response(
                {"detail": "User verification failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = PhrProfileService.phr__profile(
            {"x_token": result.get("token")}
        )

        abha_number, _ = self._update_abha_from_profile(
            profile_result,
            access_token=result.get("token"),
            refresh_token=result.get("refreshToken"),
        )

        remove_cached_phr_tokens(abha_health_id=request.user.abha_address)
        cache_phr_tokens(
            abha_health_id=abha_number.health_id,
            access_token=result.get("token"),
            refresh_token=result.get("refreshToken"),
        )

        return Response(
            {
                "switchProfileEnabled": result.get("switchProfileEnabled", True),
                **self._get_tokens(
                    abha_address=abha_number.health_id,
                    id=abha_number.id,
                ),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="phr_card")
    def phr_profile__card(self, request):
        x_token = self._get_x_token(request)

        phr_card = PhrProfileService.phr_profile__card({"x_token": x_token})

        return HttpResponse(
            phr_card,
            content_type="image/png",
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="request_otp")
    def phr_profile__request_otp(self, request):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        value = validated_data.get("value")

        scope = self._build_scope(login_hint, otp_system)

        result = PhrProfileService.phr__profile__request__otp(
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

    @action(detail=False, methods=["post"], url_path="verify_otp")
    def phr_profile__verify_otp(self, request):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        action = validated_data.get("action")

        scope = self._build_scope(login_hint, otp_system)

        result = PhrProfileService.phr__profile__verify__otp(
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
                    "detail": result.get("message"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action in ("LINK", "DE_LINK"):
            result = PhrProfileService.phr__profile__link__delink(
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
            result = PhrProfileService.phr__profile__select__preferred__abha(
                {
                    "transaction_id": str(result.get("txnId")),
                    "x_token": x_token,
                }
            )

            return Response(
                {
                    "abhaAddress": result.get("abhaAddress"),
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="update")
    def phr_profile__update(self, request):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        profile_data = {
            "address": validated_data.get("address"),
            "firstName": validated_data.get("first_name"),
            "middleName": validated_data.get("middle_name", ""),
            "lastName": validated_data.get("last_name", ""),
            "gender": validated_data.get("gender"),
            "dayOfBirth": validated_data.get("day_of_birth", ""),
            "monthOfBirth": validated_data.get("month_of_birth", ""),
            "yearOfBirth": validated_data.get("year_of_birth"),
            "stateCode": validated_data.get("state_code"),
            "stateName": validated_data.get("state_name"),
            "districtCode": validated_data.get("district_code"),
            "districtName": validated_data.get("district_name"),
            "pinCode": validated_data.get("pincode"),
            "profilePhoto": validated_data.get("profile_photo", ""),
        }

        PhrProfileService.phr__profile__update(
            {
                "x_token": x_token,
                "profile_data": profile_data,
            }
        )

        return Response(
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="reset_password")
    def phr_profile__reset__password(self, request):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        result = PhrProfileService.phr__profile__reset__password(
            {
                "x_token": x_token,
                "abha_address": self._normalize_abha_address(
                    validated_data.get("abha_address")
                ),
                "password": validated_data.get("password"),
            }
        )

        if result.get("authResult") == "failure":
            return Response(
                {
                    "detail": result.get("message")
                    or "Password update failed. Please try again.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="logout")
    def phr_profile__logout(self, request):
        validated_data = self.validate_request(request)

        cache.set(
            f"{PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX}{validated_data['access_token']}",
            "invalidated_on_logout",
            timeout=1800,
        )

        cache.set(
            f"{PHR_TEMP_REFRESH_TOKEN_INVALIDATION_PREFIX}{validated_data['refresh_token']}",
            "invalidated_on_logout",
            timeout=1800,
        )

        try:
            x_token = self._get_x_token(request)
            result = PhrProfileService.phr__profile__logout({"x_token": x_token})

            remove_cached_phr_tokens(abha_health_id=request.user.abha_address)

            return Response(
                {
                    "detail": result.get("message", "Successfully logged out"),
                },
                status=status.HTTP_200_OK,
            )

        except Exception:
            remove_cached_phr_tokens(abha_health_id=request.user.abha_address)

            return Response(
                {
                    "detail": "Successfully logged out",
                },
                status=status.HTTP_200_OK,
            )
