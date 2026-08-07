import logging

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.consent import ConsentRequestSerializer
from abdm.api.v3.serializers.hiu import (
    ConsentFetchSerializer,
    ConsentRequestStatusSerializer,
    DataFlowHealthInformationRequestSerializer,
    HiuConsentOnFetchSerializer,
    HiuConsentRequestNotifySerializer,
    HiuConsentRequestOnInitSerializer,
    HiuConsentRequestOnStatusSerializer,
    HiuHealthInformationOnRequestSerializer,
    HiuHealthInformationTransferSerializer,
    IdentityAuthenticationSerializer,
)
from abdm.api.viewsets.consent import ConsentViewSet
from abdm.authentication import ABDMAuthentication
from abdm.models import AbhaNumber, CallbackType, ConsentArtefact, ConsentRequest
from abdm.service.v3.gateway import GatewayService
from abdm.utils.callback import store_and_enqueue_callback

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: HIU"])
class HIUViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    serializer_action_classes = {
        "identity__authentication": IdentityAuthenticationSerializer,
        "consent__request__init": ConsentRequestSerializer,
        "consent__request__status": ConsentRequestStatusSerializer,
        "consent__fetch": ConsentFetchSerializer,
        "data_flow__health_information__request": DataFlowHealthInformationRequestSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @action(detail=False, methods=["POST"], url_path="verify_identity")
    def identity__authentication(self, request):
        validated_data = self.validate_request(request)

        abha_number = AbhaNumber.objects.filter(
            Q(
                Q(abha_number=validated_data.get("abha_number"))
                & Q(abha_number__isnull=False)
            )
            | Q(health_id=validated_data.get("abha_number"))
            | Q(patient__external_id=validated_data.get("patient"))
        ).first()

        if not abha_number:
            return Response(
                {"detail": "No Abha Number Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = GatewayService.identity__authentication({"abha_number": abha_number})

        return Response(
            {
                "status": result.get("authenticated"),
                "abha_address": result.get("abhaAddress"),
                "transaction_id": result.get("transactionId"),
                "requestId": result.get("response").get("requestId"),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["POST"], url_path="create_consent_request")
    def consent__request__init(self, request):
        return ConsentViewSet().create(request)

    @action(detail=False, methods=["POST"], url_path="consent_request_status")
    def consent__request__status(self, request):
        validated_data = self.validate_request(request)

        consent = ConsentRequest.objects.filter(
            external_id=validated_data.get("consent_request")
        ).first()

        if not consent:
            return Response(
                {"detail": "No Consent Request Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        GatewayService.consent__request__status(
            {
                "consent": consent,
            }
        )

        return Response(
            {"detail": "Consent Request Status Initiated"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["POST"], url_path="fetch_consent_artefact")
    def consent__fetch(self, request):
        validated_data = self.validate_request(request)

        artefacts = ConsentArtefact.objects.filter(
            Q(external_id=validated_data.get("consent_artefact"))
            | Q(consent_request__external_id=validated_data.get("consent_request"))
        )

        if len(artefacts) == 0:
            return Response(
                {"detail": "No Consent Artefact Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        for artefact in artefacts:
            GatewayService.consent__fetch(
                {
                    "artefact": artefact,
                }
            )

        return Response(
            {"detail": "Consent Artefact Fetch Initiated"},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["POST"], url_path="request_health_information")
    def data_flow__health_information__request(self, request):
        validated_data = self.validate_request(request)

        artefact = ConsentArtefact.objects.filter(
            external_id=validated_data.get("consent_artefact")
        ).first()

        if not artefact:
            return Response(
                {"detail": "No Consent Artefact Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        GatewayService.data_flow__health_information__request(
            {
                "artefact": artefact,
            }
        )

        return Response(
            {"detail": "Health Information Request Initiated"},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(tags=["ABDM: HIU Callback"])
class HIUCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "hiu__consent__request__on_init": HiuConsentRequestOnInitSerializer,
        "hiu__consent__request__on_status": HiuConsentRequestOnStatusSerializer,
        "hiu__consent__request__notify": HiuConsentRequestNotifySerializer,
        "hiu__consent__on_fetch": HiuConsentOnFetchSerializer,
        "hiu__health_information__on_request": HiuHealthInformationOnRequestSerializer,
        "hiu__health_information__transfer": HiuHealthInformationTransferSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as exception:
            logger.warning(
                f"Validation failed for request data: {request.data}, "
                f"Path: {request.path}, Method: {request.method}, "
                f"Error details: {exception!s}"
            )

            raise exception

        return serializer.validated_data

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/on-init")
    def hiu__consent__request__on_init(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.CONSENT_REQUEST_ON_INIT
        )

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/on-status")
    def hiu__consent__request__on_status(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.CONSENT_REQUEST_ON_STATUS
        )

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/notify")
    def hiu__consent__request__notify(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.CONSENT_REQUEST_NOTIFY
        )

    @action(detail=False, methods=["POST"], url_path="hiu/consent/on-fetch")
    def hiu__consent__on_fetch(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(request, CallbackType.CONSENT_ON_FETCH)

    @action(
        detail=False, methods=["POST"], url_path="hiu/health-information/on-request"
    )
    def hiu__health_information__on_request(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.HEALTH_INFORMATION_ON_REQUEST
        )

    @action(
        detail=False,
        methods=["POST"],
        url_path="hiu/health-information/transfer",
    )
    def hiu__health_information__transfer(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.HEALTH_INFORMATION_TRANSFER
        )
