from logging import getLogger

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.phr_consent import (
    PhrConsentAutoApproveUpdateSerializer,
    PhrConsentRequestApproveSerializer,
    PhrConsentRequestDenySerializer,
    PhrConsentRequestRevokeSerializer,
)
from abdm.authentication import IsPhrAuthenticated, PhrCustomAuthentication
from abdm.service.helper import (
    PHR_ACCESS_TOKEN_CACHE_TIMEOUT,
    PHR_ACCESS_TOKEN_PREFIX,
    PHR_REFRESH_TOKEN_CACHE_TIMEOUT,
    PHR_REFRESH_TOKEN_PREFIX,
    transform_phr_links_data,
)
from abdm.service.v3.phr.phr_consent import PhrConsentService
from abdm.service.v3.phr.profile import PhrProfileService
from abdm.service.v3.phr.user_init_linking import PhrUserInitLinkingService

logger = getLogger(__name__)


@extend_schema(tags=["PHR Consent"])
class PhrConsentViewSet(GenericViewSet):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]

    REQUIRED_REQUEST_FIELDS = [
        "purpose",
        "requester",
        "permission",
        "hiTypes",
    ]
    REQUIRED_ARTEFACT_FIELDS = [
        "purpose",
        "requester",
        "permission",
        "hiTypes",
        "careContexts",
        "hip",
    ]
    VALID_STATUSES = ["ALL", "REQUESTED", "EXPIRED", "REVOKED", "GRANTED", "DENIED"]

    serializer_action_classes = {
        "phr_consent__request__approve": PhrConsentRequestApproveSerializer,
        "phr_consent__request__deny": PhrConsentRequestDenySerializer,
        "phr_consent__request__revoke": PhrConsentRequestRevokeSerializer,
        "phr_consent__auto__approve__update": PhrConsentAutoApproveUpdateSerializer,
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

    def _get_query_params(self, request):
        status_param = request.query_params.get("status", "ALL")

        try:
            limit = max(int(request.query_params.get("limit", -1)), -1)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (ValueError, TypeError):
            return None, None, None

        if status_param not in self.VALID_STATUSES:
            return None, None, None

        return status_param, limit, offset

    def _is_valid_item(self, item, item_type="request"):
        if item_type == "request":
            return all(item.get(field) for field in self.REQUIRED_REQUEST_FIELDS)
        if item_type == "artefact":
            consent_detail = item.get("consentDetail")
            return (
                consent_detail
                and all(
                    consent_detail.get(field) for field in self.REQUIRED_ARTEFACT_FIELDS
                )
                and len(consent_detail.get("careContexts", [])) > 0
            )
        return False

    def _filter_valid_items(self, items, item_type="request"):
        return [item for item in items if self._is_valid_item(item, item_type)]

    def _get_links_for_eligible_status(self, status_value, x_token):
        if status_value == "REQUESTED":
            links = PhrUserInitLinkingService.phr__user_initiated_linking__care_context__links(
                {
                    "x_token": x_token,
                }
            )
            return transform_phr_links_data(links)

        return []

    @action(detail=False, methods=["get"], url_path="requests")
    def phr_consent__requests(self, request):
        x_token = self._get_x_token(request)

        status_param, limit, offset = self._get_query_params(request)
        if status_param is None:
            return Response([], status=status.HTTP_200_OK)

        consent_requests = PhrConsentService.phr__consent__requests(
            {
                "x_token": x_token,
                "status": status_param,
                "limit": limit,
                "offset": offset,
            }
        )

        filter_consent_requests = self._filter_valid_items(
            consent_requests.get("requests", []), "request"
        )

        return Response(filter_consent_requests, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="request/(?P<request_id>[^/.]+)",
    )
    def phr_consent__request(self, request, request_id):
        x_token = self._get_x_token(request)

        consent_request = PhrConsentService.phr__consent__request(
            {"x_token": x_token, "request_id": request_id}
        )

        hip_id = consent_request.get("hip", {}).get("id")
        care_contexts = consent_request.get("careContexts", [])

        if hip_id and care_contexts:
            return Response(
                {
                    "request": consent_request,
                    "links": [
                        {
                            "hip": consent_request.get("hip"),
                            "careContexts": consent_request.get("careContexts"),
                        }
                    ],
                },
                status=status.HTTP_200_OK,
            )

        links = self._get_links_for_eligible_status(
            consent_request.get("status"), x_token
        )

        return Response(
            {"request": consent_request, "links": links}, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"], url_path="artefacts")
    def phr_consent__artefacts(self, request):
        x_token = self._get_x_token(request)

        status_param, limit, offset = self._get_query_params(request)
        if status_param is None:
            return Response([], status=status.HTTP_200_OK)

        consent_artefacts = PhrConsentService.phr__consent__artefacts(
            {
                "x_token": x_token,
                "status": status_param,
                "limit": limit,
                "offset": offset,
            }
        )

        filter_consent_artefacts = self._filter_valid_items(
            consent_artefacts.get("consentArtefacts", []), "artefact"
        )

        return Response(filter_consent_artefacts, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="request/(?P<request_id>[^/.]+)/artefacts",
    )
    def phr_consent__request__artefacts(self, request, request_id):
        x_token = self._get_x_token(request)

        consent_request_artefacts = PhrConsentService.phr__consent__request__artefacts(
            {"x_token": x_token, "request_id": request_id}
        )

        filter_consent_request_artefacts = self._filter_valid_items(
            consent_request_artefacts, "artefact"
        )

        return Response(filter_consent_request_artefacts, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="artefact/(?P<artefact_id>[^/.]+)",
    )
    def phr_consent__artefact(self, request, artefact_id):
        x_token = self._get_x_token(request)

        consent_artefact = PhrConsentService.phr__consent__artefact(
            {"x_token": x_token, "artefact_id": artefact_id}
        )

        consent_detail = consent_artefact.get("consentDetail")

        return Response(
            {
                "artefact": consent_artefact,
                "links": [
                    {
                        "hip": consent_detail.get("hip"),
                        "careContexts": consent_detail.get("careContexts"),
                    }
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="request/(?P<request_id>[^/.]+)/approve",
    )
    def phr_consent__request__approve(self, request, request_id):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        result = PhrConsentService.phr__consent__request__approve(
            {
                "x_token": x_token,
                "request_id": request_id,
                "consents": validated_data.get("consents"),
            }
        )

        return Response(
            {"detail": result.get("message")},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="request/(?P<request_id>[^/.]+)/deny",
    )
    def phr_consent__request__deny(self, request, request_id):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        result = PhrConsentService.phr__consent__request__deny(
            {
                "x_token": x_token,
                "request_id": request_id,
                "reason": validated_data.get("reason"),
            }
        )

        return Response(
            {"detail": result.get("status")},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="revoke")
    def phr_consent__request__revoke(self, request):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        result = PhrConsentService.phr__consent__request__revoke(
            {
                "x_token": x_token,
                "consents": [
                    str(consent) for consent in validated_data.get("consents", [])
                ],
            }
        )

        return Response(
            {"detail": result.get("message")},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="auto_approve/setup")
    def phr_consent__auto__approve__setup(self, request):
        x_token = self._get_x_token(request)

        result = PhrConsentService.phr__consent__auto__approve__setup(
            {
                "x_token": x_token,
            }
        )

        return Response(
            {
                "detail": result.get("message"),
                "autoApprovalId": result.get("autoApprovalId"),
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="auto_approve/(?P<auto_approve_request_id>[^/.]+)/update",
    )
    def phr_consent__auto__approve__update(self, request, auto_approve_request_id):
        validated_data = self.validate_request(request)
        x_token = self._get_x_token(request)

        result = PhrConsentService.phr__consent__auto__approve__update(
            {
                "x_token": x_token,
                "auto_approve_request_id": auto_approve_request_id,
                "enable": validated_data.get("enable"),
            }
        )

        return Response(
            {"detail": result.get("message")},
            status=status.HTTP_200_OK,
        )
