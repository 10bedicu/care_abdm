import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.hip import (
    ConsentRequestHipNotifySerializer,
    HipHealthInformationRequestSerializer,
    HipLinkCareContextConfirmSerializer,
    HipLinkCareContextInitSerializer,
    HipPatientCareContextDiscoverSerializer,
    HipPatientShareSerializer,
    HipTokenOnGenerateTokenSerializer,
    LinkOnCarecontextSerializer,
)
from abdm.authentication import ABDMAuthentication
from abdm.models import CallbackType
from abdm.tasks.patient_share import patient_share_on_share
from abdm.utils.callback import store_and_enqueue_callback
from abdm.utils.token import get_scan_and_share_token_by_token_number
from care.emr.resources.patient.spec import PatientRetrieveSpec
from care.facility.models.facility import Facility

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: HIP"])
class HIPViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    @action(detail=False, methods=["POST"], url_path="link_care_context")
    def link__carecontext(self, request):
        return Response(
            {
                "detail": "All care contexts are linked automatically, and no manual intervention is required",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=False,
        methods=["GET"],
        url_path="patient/fetch-by-token",
    )
    def patient__fetch_by_token(self, request):
        token = request.query_params.get("token")
        facility_id = request.query_params.get("facility_id")

        if not token or not facility_id:
            return Response(
                {"detail": "Token and facility are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token_number = int(token)
        except ValueError:
            return Response(
                {"detail": "Token must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        facility = Facility.objects.filter(external_id=facility_id).first()
        if not facility:
            return Response(
                {"detail": "Facility not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        token = get_scan_and_share_token_by_token_number(
            token_number=token_number, facility=facility
        )

        if not token:
            return Response(
                {"detail": "Token not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = PatientRetrieveSpec.serialize(token.patient).to_json()
        return Response(data, status=status.HTTP_200_OK)


@extend_schema(tags=["ABDM: HIP Callback"])
class HIPCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "hip__token__on_generate_token": HipTokenOnGenerateTokenSerializer,
        "link__on_carecontext": LinkOnCarecontextSerializer,
        "hip__patient__care_context__discover": HipPatientCareContextDiscoverSerializer,
        "hip__link__care_context__init": HipLinkCareContextInitSerializer,
        "hip__link__care_context__confirm": HipLinkCareContextConfirmSerializer,
        "consent__request__hip__notify": ConsentRequestHipNotifySerializer,
        "hip__health_information__request": HipHealthInformationRequestSerializer,
        "hip__patient__share": HipPatientShareSerializer,
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

    @action(detail=False, methods=["POST"], url_path="hip/token/on-generate-token")
    def hip__token__on_generate_token(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.TOKEN_ON_GENERATE_TOKEN
        )

    @action(detail=False, methods=["POST"], url_path="link/on_carecontext")
    def link__on_carecontext(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(request, CallbackType.LINK_ON_CARECONTEXT)

    @action(
        detail=False, methods=["POST"], url_path="hip/patient/care-context/discover"
    )
    def hip__patient__care_context__discover(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.PATIENT_CARE_CONTEXT_DISCOVER
        )

    @action(detail=False, methods=["POST"], url_path="hip/link/care-context/init")
    def hip__link__care_context__init(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.LINK_CARE_CONTEXT_INIT
        )

    @action(detail=False, methods=["POST"], url_path="hip/link/care-context/confirm")
    def hip__link__care_context__confirm(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.LINK_CARE_CONTEXT_CONFIRM
        )

    @action(detail=False, methods=["POST"], url_path="consent/request/hip/notify")
    def consent__request__hip__notify(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.CONSENT_REQUEST_HIP_NOTIFY
        )

    @action(detail=False, methods=["POST"], url_path="hip/health-information/request")
    def hip__health_information__request(self, request):
        self.validate_request(request)

        return store_and_enqueue_callback(
            request, CallbackType.HEALTH_INFORMATION_REQUEST
        )

    @action(detail=False, methods=["POST"], url_path="hip/patient/share")
    def hip__patient__share(self, request):
        try:
            self.validate_request(request)
        except Exception:
            patient_share_on_share.delay(
                {
                    "error": {
                        "message": "Bad Request, invalid request Body",
                        "code": "ABDM-9999",
                    },
                    "request_id": request.headers.get("REQUEST-ID"),
                }
            )

            return Response(status=status.HTTP_200_OK)

        return store_and_enqueue_callback(request, CallbackType.PATIENT_SHARE)
