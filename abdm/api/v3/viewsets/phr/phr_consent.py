from logging import getLogger

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
from abdm.service.phr_helper import get_phr_access_token, transform_phr_links_data
from abdm.service.v3.phr.phr_consent import PhrConsentService
from abdm.service.v3.phr.phr_gateway import PhrGatewayService

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

    def _get_query_params(self, request):
        status_param = request.query_params.get("status", "ALL")

        if status_param not in self.VALID_STATUSES:
            return None, None, None

        try:
            limit = max(int(request.query_params.get("limit", -1)), -1)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (ValueError, TypeError):
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

    def _build_links_response(self, consent_request):
        hip_id = consent_request.get("hip", {}).get("id")
        care_contexts = consent_request.get("careContexts", [])

        if hip_id and care_contexts:
            return [
                {
                    "hip": consent_request.get("hip"),
                    "careContexts": consent_request.get("careContexts"),
                }
            ]

        if consent_request.get("status") == "REQUESTED":
            links = PhrGatewayService.phr__gateway__patient__links(
                {
                    "x_token": self.x_token,
                }
            )
            return transform_phr_links_data(links)

        return []

    @action(detail=False, methods=["get"], url_path="requests")
    def phr_consent__requests(self, request):
        status_param, limit, offset = self._get_query_params(request)

        consent_requests = PhrConsentService.phr__consent__requests(
            {
                "x_token": self.x_token,
                "status": status_param,
                "limit": limit,
                "offset": offset,
            }
        )

        filtered_requests = self._filter_valid_items(
            consent_requests.get("requests", []), "request"
        )

        return Response(filtered_requests, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="request/(?P<request_id>[^/.]+)",
    )
    def phr_consent__request(self, request, request_id):
        consent_request = PhrConsentService.phr__consent__request(
            {"x_token": self.x_token, "request_id": request_id}
        )

        return Response(
            {
                "request": consent_request,
                "links": self._build_links_response(consent_request),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="artefacts")
    def phr_consent__artefacts(self, request):
        status_param, limit, offset = self._get_query_params(request)

        consent_artefacts = PhrConsentService.phr__consent__artefacts(
            {
                "x_token": self.x_token,
                "status": status_param,
                "limit": limit,
                "offset": offset,
            }
        )

        filtered_artefacts = self._filter_valid_items(
            consent_artefacts.get("consentArtefacts", []), "artefact"
        )

        return Response(filtered_artefacts, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="request/(?P<request_id>[^/.]+)/artefacts",
    )
    def phr_consent__request__artefacts(self, request, request_id):
        consent_request_artefacts = PhrConsentService.phr__consent__request__artefacts(
            {"x_token": self.x_token, "request_id": request_id}
        )

        filtered_artefacts = self._filter_valid_items(
            consent_request_artefacts, "artefact"
        )

        return Response(filtered_artefacts, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="artefact/(?P<artefact_id>[^/.]+)",
    )
    def phr_consent__artefact(self, request, artefact_id):
        consent_artefact = PhrConsentService.phr__consent__artefact(
            {"x_token": self.x_token, "artefact_id": artefact_id}
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

        result = PhrConsentService.phr__consent__request__approve(
            {
                "x_token": self.x_token,
                "request_id": request_id,
                "consents": validated_data.get("consents"),
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="request/(?P<request_id>[^/.]+)/deny",
    )
    def phr_consent__request__deny(self, request, request_id):
        validated_data = self.validate_request(request)

        result = PhrConsentService.phr__consent__request__deny(
            {
                "x_token": self.x_token,
                "request_id": request_id,
                "reason": validated_data.get("reason"),
            }
        )

        return Response({"detail": result.get("status")}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="revoke")
    def phr_consent__request__revoke(self, request):
        validated_data = self.validate_request(request)

        result = PhrConsentService.phr__consent__request__revoke(
            {
                "x_token": self.x_token,
                "consents": [
                    str(consent) for consent in validated_data.get("consents", [])
                ],
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="auto_approve/setup")
    def phr_consent__auto__approve__setup(self, request):
        result = PhrConsentService.phr__consent__auto__approve__setup(
            {
                "x_token": self.x_token,
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

        result = PhrConsentService.phr__consent__auto__approve__update(
            {
                "x_token": self.x_token,
                "auto_approve_request_id": auto_approve_request_id,
                "enable": validated_data.get("enable"),
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)
