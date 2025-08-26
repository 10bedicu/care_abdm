from logging import getLogger

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.phr_profile import (
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
from abdm.models.abha_number import AbhaNumber
from abdm.service.phr_helper import (
    cache_phr_tokens,
    get_phr_access_token,
    get_phr_temp_tokens,
    normalize_abha_address,
    remove_cached_phr_tokens,
    update_abha_from_profile,
)
from abdm.service.v3.phr.phr_profile import PhrProfileService
from abdm.service.v3.phr.phr_subscription import PhrSubscriptionService

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

    @property
    def x_token(self):
        return get_phr_access_token(self.request.user.abha_address)

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

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

    @action(detail=False, methods=["get"], url_path="setup")
    def phr_profile__setup(self, request):
        current_health_id = request.user.abha_address

        try:
            abha_number = AbhaNumber.objects.get(phr_health_id=current_health_id)
        except AbhaNumber.DoesNotExist:
            return Response(
                {"detail": "ABHA record not found"}, status=status.HTTP_404_NOT_FOUND
            )

        health_data = abha_number.phr_health_ids_metadata.get(current_health_id, {})
        subscription_id = health_data.get("subscription_id")

        if not subscription_id:
            try:
                PhrSubscriptionService.phr__subscription__request__init(
                    {"abha_address": current_health_id}
                )
            except Exception as e:
                logger.error(f"Error initializing subscription: {e}")

        auto_approve_id = health_data.get("auto_approve_id")
        is_auto_approve_enabled = health_data.get("is_auto_approve_enabled", False)

        return Response(
            {
                "auto_approve_id": auto_approve_id,
                "is_auto_approve_enabled": is_auto_approve_enabled,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="get_profile")
    def phr_profile(self, request):
        profile = PhrProfileService.phr__profile({"x_token": self.x_token})

        update_abha_from_profile(profile)

        return Response(profile, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="switch")
    def phr_profile__switch(self, request):
        result = PhrProfileService.phr__profile__switch({"x_token": self.x_token})

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
        abha_address = normalize_abha_address(validated_data.get("abha_address"))
        t_token = cache.get(
            f"{PHR_PROFILE_SWITCH_VERIFY_TOKEN_CACHE_KEY}:{validated_data.get('transaction_id')}"
        )

        if not t_token:
            return Response(
                {"detail": "Session expired. Please try again."},
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
                {"detail": "User verfication failed. Please try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = PhrProfileService.phr__profile(
            {"x_token": result.get("token")}
        )

        abha_number, _ = update_abha_from_profile(profile_result)

        remove_cached_phr_tokens(abha_health_id=request.user.abha_address)
        cache_phr_tokens(
            abha_health_id=abha_number.phr_health_id,
            access_token=result.get("token"),
            refresh_token=result.get("refreshToken"),
        )

        return Response(
            {
                "switchProfileEnabled": result.get("switchProfileEnabled", True),
                **get_phr_temp_tokens(
                    abha_address=abha_number.phr_health_id,
                    id=abha_number.id,
                ),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="phr_card")
    def phr_profile__card(self, request):
        phr_card = PhrProfileService.phr_profile__card({"x_token": self.x_token})

        return HttpResponse(
            phr_card,
            content_type="image/png",
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="request_otp")
    def phr_profile__request_otp(self, request):
        validated_data = self.validate_request(request)

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
                "x_token": self.x_token,
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

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")
        action = validated_data.get("action")

        scope = self._build_scope(login_hint, otp_system)

        result = PhrProfileService.phr__profile__verify__otp(
            {
                "scope": scope,
                "otp": validated_data.get("otp"),
                "transaction_id": str(validated_data.get("transaction_id")),
                "x_token": self.x_token,
            }
        )

        if result.get("authResult") == "failed":
            return Response(
                {"detail": result.get("message")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action in ("LINK", "DE_LINK"):
            result = PhrProfileService.phr__profile__link__delink(
                {
                    "action": action,
                    "transaction_id": str(result.get("txnId")),
                    "x_token": self.x_token,
                }
            )

            return Response(
                {"detail": result.get("message")},
                status=status.HTTP_200_OK,
            )

        if action == "SELECT_PREFERRED_ABHA":
            result = PhrProfileService.phr__profile__select__preferred__abha(
                {
                    "transaction_id": str(result.get("txnId")),
                    "x_token": self.x_token,
                }
            )

            return Response(
                {"abhaAddress": result.get("abhaAddress")},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"detail": result.get("message")},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="update")
    def phr_profile__update(self, request):
        validated_data = self.validate_request(request)

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
                "x_token": self.x_token,
                "profile_data": profile_data,
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="reset_password")
    def phr_profile__reset__password(self, request):
        validated_data = self.validate_request(request)
        abha_address = normalize_abha_address(validated_data.get("abha_address"))

        result = PhrProfileService.phr__profile__reset__password(
            {
                "x_token": self.x_token,
                "abha_address": abha_address,
                "password": validated_data.get("password"),
            }
        )

        if result.get("authResult") == "failure":
            return Response(
                {"detail": result.get("message")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": result.get("message")},
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
            result = PhrProfileService.phr__profile__logout({"x_token": self.x_token})

            remove_cached_phr_tokens(abha_health_id=request.user.abha_address)

            return Response(
                {"detail": result.get("message", "Successfully logged out")},
                status=status.HTTP_200_OK,
            )

        except Exception:
            remove_cached_phr_tokens(abha_health_id=request.user.abha_address)

            return Response(
                {"detail": "Successfully logged out"},
                status=status.HTTP_200_OK,
            )
