from logging import getLogger

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.phr_user_init_linking import (
    PhrUserInitLinkingCareContextConfirmSerializer,
    PhrUserInitLinkingCareContextDiscoverSerializer,
    PhrUserInitLinkingCareContextInitSerializer,
    PhrUserInitLinkingCareContextOnConfirmSerializer,
    PhrUserInitLinkingCareContextOnDiscoverSerializer,
    PhrUserInitLinkingCareContextOnInitSerializer,
)
from abdm.authentication import (
    ABDMAuthentication,
    IsPhrAuthenticated,
    PhrCustomAuthentication,
)
from abdm.service.helper import uuid
from abdm.service.phr_helper import get_phr_access_token, normalize_abha_address
from abdm.service.v3.phr.phr_user_init_linking import PhrUserInitLinkingService

logger = getLogger(__name__)

RETRY_CACHE_TIMEOUT = 10 * 60
DUPLICATE_DISCOVERY_CACHE_KEY = "duplicate_discovery:"
DUPLICATE_INIT_CACHE_KEY = "duplicate_init:"
DUPLICATE_CONFIRM_CACHE_KEY = "duplicate_confirm:"
CALLBACK_DATA_CACHE_KEY = "callback_data:"


def _make_callback_cache_key(request_id: str) -> str:
    return f"{CALLBACK_DATA_CACHE_KEY}{request_id}"


@extend_schema(tags=["PHR User Initiated Linking"])
class PhrUserInitLinkingViewSet(GenericViewSet):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]

    serializer_action_classes = {
        "phr_user_initiated_linking__care_context__discover": PhrUserInitLinkingCareContextDiscoverSerializer,
        "phr_user_initiated_linking__care_context__init": PhrUserInitLinkingCareContextInitSerializer,
        "phr_user_initiated_linking__care_context__confirm": PhrUserInitLinkingCareContextConfirmSerializer,
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

    def _start_request(self):
        request_id = str(uuid())
        key = _make_callback_cache_key(request_id)
        added = cache.add(key, {"status": "pending"}, timeout=RETRY_CACHE_TIMEOUT)
        if not added:
            request_id = str(uuid())
            cache.set(
                _make_callback_cache_key(request_id),
                {"status": "pending"},
                timeout=RETRY_CACHE_TIMEOUT,
            )
        return request_id

    def _check_and_set_duplicate(
        self, hip_id: str, abha_address: str, cache_key_suffix: str
    ):
        cache_key = f"duplicate_{cache_key_suffix}:{hip_id}_{abha_address}"
        added = cache.add(cache_key, "temp_value", timeout=RETRY_CACHE_TIMEOUT)
        if not added:
            return Response(
                {
                    "detail": "Duplicate request. Please try again later after 10 minutes"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @action(detail=False, methods=["post"], url_path="discover")
    def phr_user_initiated_linking__care_context__discover(self, request):
        validated_data = self.validate_request(request)

        hip = validated_data.get("hip")
        abha_address = normalize_abha_address(request.user.abha_address)

        duplicate_resp = self._check_and_set_duplicate(
            hip["id"], abha_address, "discovery"
        )

        if duplicate_resp:
            return duplicate_resp

        request_id = self._start_request()

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__discover(
            {
                "x_token": self.x_token,
                "hip": validated_data.get("hip"),
                "unverified_identifiers": validated_data.get(
                    "unverified_identifiers", []
                ),
                "request_id": request_id,
            }
        )

        return Response(
            {
                "request_id": request_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="init")
    def phr_user_initiated_linking__care_context__init(self, request):
        validated_data = self.validate_request(request)
        abha_address = normalize_abha_address(request.user.abha_address)
        hip_id = validated_data.get("hip")["id"]

        duplicate_resp = self._check_and_set_duplicate(hip_id, abha_address, "init")
        if duplicate_resp:
            return duplicate_resp

        request_id = self._start_request()

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__init(
            {
                "x_token": self.x_token,
                "transaction_id": str(validated_data.get("transaction_id")),
                "patient": validated_data.get("patient"),
                "request_id": request_id,
            }
        )

        return Response(
            {
                "request_id": request_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="confirm")
    def phr_user_initiated_linking__care_context__confirm(self, request):
        validated_data = self.validate_request(request)
        abha_address = normalize_abha_address(request.user.abha_address)
        hip_id = validated_data.get("hip")["id"]

        duplicate_resp = self._check_and_set_duplicate(hip_id, abha_address, "confirm")
        if duplicate_resp:
            return duplicate_resp

        request_id = self._start_request()

        PhrUserInitLinkingService.phr__user_initiated_linking__care_context__confirm(
            {
                "x_token": self.x_token,
                "link_ref_number": str(validated_data.get("link_ref_number")),
                "token": validated_data.get("token"),
                "request_id": request_id,
            }
        )

        return Response(
            {
                "request_id": request_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="status")
    def phr_user_initiated_linking__care_context__status(self, request):
        """
        GET /status?request_id=<id>
        Returns:
          - 200 + {"status": "pending"} if still pending
          - 200 + data (and deletes cache) if completed successfully
          - 400 + {"detail": ...} if callback had an error (and deletes cache)
          - 404 if request_id not found or expired
        """
        request_id = request.query_params.get("request_id")
        if not request_id:
            return Response(
                {"detail": "Request ID is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        cache_key = _make_callback_cache_key(request_id)
        cache_data = cache.get(cache_key)
        logger.info(f"TESTLOG - CHECKING STATUS CACHE KEY: {cache_key}")
        logger.info(f"TESTLOG - CHECKING STATUS CACHE DATA: {cache_data}")

        if not cache_data:
            return Response(
                {"detail": "Request ID not found"}, status=status.HTTP_404_NOT_FOUND
            )

        status_val = cache_data.get("status")
        if status_val == "pending":
            return Response({"status": "pending"}, status=status.HTTP_200_OK)

        response_payload = (
            cache_data.get("data")
            if status_val == "completed"
            else cache_data.get("error")
        )
        cache.delete(cache_key)

        if status_val == "failed":
            return Response(
                {
                    "detail": response_payload.get("message")
                    if isinstance(response_payload, dict)
                    else response_payload
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "status": status_val,
                "data": response_payload,
            },
            status=status.HTTP_200_OK,
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

    def _store_callback_result(self, request_id: str, success: bool, payload: dict):
        cache_key = _make_callback_cache_key(request_id)
        if success:
            cache.set(
                cache_key,
                {"status": "completed", "data": payload},
                timeout=RETRY_CACHE_TIMEOUT,
            )
        else:
            cache.set(
                cache_key,
                {"status": "failed", "error": payload},
                timeout=RETRY_CACHE_TIMEOUT,
            )

    def _extract_request_id_from_payload(self, validated_data: dict) -> str:
        response = validated_data.get("response") or {}
        return response.get("requestId")

    @action(
        detail=False, methods=["post"], url_path="hiu/patient/care-context/on-discover"
    )
    def phr_user_initiated_linking__care_context__on_discover(self, request):
        validated_data = self.validate_request(request)
        logger.info(f"PHR USER INITIATED LINKING ON DISCOVER: {validated_data}")

        request_id = self._extract_request_id_from_payload(validated_data)
        if not request_id:
            logger.error(
                "Callback missing request_id (discover). Payload: %s", validated_data
            )
            return Response(
                {"detail": "Missing request_id in callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        error = validated_data.get("error")
        if error:
            self._store_callback_result(request_id, success=False, payload=error)
            return Response({"detail": "Stored error"}, status=status.HTTP_200_OK)

        self._store_callback_result(request_id, success=True, payload=validated_data)
        return Response({"detail": "Stored callback data"}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="hiu/patient/care-context/on-init")
    def phr_user_initiated_linking__care_context__on_init(self, request):
        validated_data = self.validate_request(request)
        logger.info(f"PHR USER INITIATED LINKING ON INIT: {validated_data}")

        request_id = self._extract_request_id_from_payload(validated_data)
        if not request_id:
            logger.error(
                "Callback missing request_id (init). Payload: %s", validated_data
            )
            return Response(
                {"detail": "Missing request_id in callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        error = validated_data.get("error")
        if error:
            self._store_callback_result(request_id, success=False, payload=error)
            return Response({"detail": "Stored error"}, status=status.HTTP_200_OK)

        self._store_callback_result(request_id, success=True, payload=validated_data)
        return Response({"detail": "Stored callback data"}, status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["post"], url_path="hiu/patient/care-context/on-confirm"
    )
    def phr_user_initiated_linking__care_context__on_confirm(self, request):
        validated_data = self.validate_request(request)
        logger.info(f"PHR USER INITIATED LINKING ON CONFIRM: {validated_data}")

        request_id = self._extract_request_id_from_payload(validated_data)
        if not request_id:
            logger.error(
                "Callback missing request_id (confirm). Payload: %s", validated_data
            )
            return Response(
                {"detail": "Missing request_id in callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        error = validated_data.get("error")
        if error:
            self._store_callback_result(request_id, success=False, payload=error)
            return Response({"detail": "Stored error"}, status=status.HTTP_200_OK)

        self._store_callback_result(request_id, success=True, payload=validated_data)
        return Response({"detail": "Stored callback data"}, status=status.HTTP_200_OK)
