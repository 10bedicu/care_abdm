from logging import getLogger

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.user_init_linking import (
    PhrUserInitLinkingCareContextConfirmSerializer,
    PhrUserInitLinkingCareContextDiscoverSerializer,
    PhrUserInitLinkingCareContextInitSerializer,
    PhrUserInitLinkingCareContextOnConfirmSerializer,
    PhrUserInitLinkingCareContextOnDiscoverSerializer,
    PhrUserInitLinkingCareContextOnInitSerializer,
)
from abdm.authentication import ABDMAuthentication
from abdm.service.helper import (
    PHR_ACCESS_TOKEN_CACHE_TIMEOUT,
    PHR_ACCESS_TOKEN_PREFIX,
    PHR_REFRESH_TOKEN_CACHE_TIMEOUT,
    PHR_REFRESH_TOKEN_PREFIX,
    transform_phr_links_data,
)
from abdm.service.v3.phr.profile import PhrProfileService
from abdm.service.v3.phr.user_init_linking import PhrUserInitLinkingService

logger = getLogger(__name__)

LAST_PATIENT_DISCOVER_CACHE_TIMEOUT = 10 * 60
LAST_PATIENT_DISCOVER_CACHE_KEY = "last_patient_discover:"


@extend_schema(tags=["PHR User Initiated Linking"])
class PhrUserInitLinkingViewSet(GenericViewSet):
    permission_classes = []

    serializer_action_classes = {
        "phr_user_initiated_linking__care_context__discover": PhrUserInitLinkingCareContextDiscoverSerializer,
        "phr_user_initiated_linking__care_context__init": PhrUserInitLinkingCareContextInitSerializer,
        "phr_user_initiated_linking__care_context__confirm": PhrUserInitLinkingCareContextConfirmSerializer,
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
        # abha_address = self._normalize_abha_address(request.user.abha_address)
        abha_address = "dora8sbx@sbx"

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

    @action(detail=False, methods=["get"], url_path="links")
    def phr_user_initiated_linking__care_context__links(self, request):
        x_token = self._get_x_token(request)

        links = (
            PhrUserInitLinkingService.phr__user_initiated_linking__care_context__links(
                {
                    "x_token": x_token,
                }
            )
        )

        return Response(transform_phr_links_data(links), status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="discover")
    def phr_user_initiated_linking__care_context__discover(self, request):
        validated_data = self.validate_request(request)

        hip = validated_data.get("hip")
        # abha_address = request.user.abha_address
        abha_address = "dora8sbx@sbx"

        cache_key = f"{LAST_PATIENT_DISCOVER_CACHE_KEY}{hip['id']}_{abha_address}"

        if cache.get(cache_key):
            return Response(
                {
                    "detail": "Duplicate discovery request. Please try again later after 10 minutes"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache.set(cache_key, "temp_value", timeout=LAST_PATIENT_DISCOVER_CACHE_TIMEOUT)

        x_token = self._get_x_token(request)

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__discover(
            {
                "x_token": x_token,
                "hip": validated_data.get("hip"),
                "unverified_identifiers": validated_data.get(
                    "unverified_identifiers", []
                ),
            }
        )

        return Response(
            {"detail": "Care context discovery initiated successfully"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="init")
    def phr_user_initiated_linking__care_context__init(self, request):
        validated_data = self.validate_request(request)

        x_token = self._get_x_token(request)

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__init(
            {
                "x_token": x_token,
                "transaction_id": str(validated_data.get("transaction_id")),
                "patient": validated_data.get("patient"),
            }
        )

        return Response(
            {"detail": "Care context link initiated successfully"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="confirm")
    def phr_user_initiated_linking__care_context__confirm(self, request):
        validated_data = self.validate_request(request)

        x_token = self._get_x_token(request)

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__confirm(
            {
                "x_token": x_token,
                "link_ref_number": str(validated_data.get("link_ref_number")),
                "token": validated_data.get("token"),
            }
        )

        return Response(
            {"detail": "Care context link confirmed successfully"},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(tags=["PHR User Initiated Linking Callback"])
class PhrUserInitLinkingCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "phr_user_initiated_linking__care_context__on_discover": PhrUserInitLinkingCareContextOnDiscoverSerializer,
        "phr_user_initiated_linking__care_context__on_init": PhrUserInitLinkingCareContextOnInitSerializer,
        "phr_user_initiated_linking__care_context__on_confirm": PhrUserInitLinkingCareContextOnConfirmSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @action(
        detail=False, methods=["post"], url_path="hiu/patient/care-context/on-discover"
    )
    def phr_user_initiated_linking__care_context__on_discover(self, request):
        validated_data = self.validate_request(request)

        logger.info(f"PHR USER INITIATED LINKING ON DISCOVER: {validated_data}")

        error = validated_data.get("error")
        if error:
            return Response(
                {"detail": error.get("message")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(validated_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="hiu/patient/care-context/on-init")
    def phr_user_initiated_linking__care_context__on_init(self, request):
        validated_data = self.validate_request(request)

        logger.info(f"PHR USER INITIATED LINKING ON INIT: {validated_data}")

        error = validated_data.get("error")
        if error:
            return Response(
                {"detail": error.get("message")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(validated_data, status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["post"], url_path="hiu/patient/care-context/on-confirm"
    )
    def phr_user_initiated_linking__care_context__on_confirm(self, request):
        validated_data = self.validate_request(request)

        logger.info(f"PHR USER INITIATED LINKING ON CONFIRM: {validated_data}")

        error = validated_data.get("error")
        if error:
            return Response(
                {"detail": error.get("message")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(validated_data, status=status.HTTP_200_OK)
