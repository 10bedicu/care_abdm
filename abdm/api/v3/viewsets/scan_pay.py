import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.scan_pay import (
    PatientScanPayOnNotifySerializer,
    PatientScanPayOrderStatusSerializer,
    PatientSelectionSerializer,
    PatientShareOpenOrderSerializer,
)
from abdm.authentication import ABDMAuthentication, ScanPayReceiptAuthentication
from abdm.models import CallbackType, PaymentOrder
from abdm.service.v3.receipt import receipt_pdf
from abdm.service.v3.scan_pay import order_has_receipt
from abdm.tasks.scan_pay import (
    scan_pay_on_order_status,
    scan_pay_on_selection,
    scan_pay_on_share_open_order,
)
from abdm.utils.callback import store_and_enqueue_callback
from care.utils.lock import ObjectLocked

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: Scan and Pay Callback"])
class ScanPayCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "patient__share__open_order": PatientShareOpenOrderSerializer,
        "patient__selection": PatientSelectionSerializer,
        "patient__scan_pay__on_notify": PatientScanPayOnNotifySerializer,
        "patient__scan_pay__order_status": PatientScanPayOrderStatusSerializer,
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

    def require_request_id(self, request):
        request_id = request.headers.get("REQUEST-ID")
        if request_id:
            return request_id, None
        return None, Response(
            {"detail": "REQUEST-ID header is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=["POST"], url_path="patient/share/open-order")
    def patient__share__open_order(self, request):
        request_id, error = self.require_request_id(request)
        if error:
            return error

        try:
            self.validate_request(request)
        except Exception:
            scan_pay_on_share_open_order.delay(
                {
                    "error": {
                        "message": "Bad Request, invalid request Body",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        return store_and_enqueue_callback(
            request, CallbackType.PATIENT_SHARE_OPEN_ORDER
        )

    @action(detail=False, methods=["POST"], url_path="patient/selection")
    def patient__selection(self, request):
        request_id, error = self.require_request_id(request)
        if error:
            return error

        try:
            self.validate_request(request)
        except Exception:
            scan_pay_on_selection.delay(
                {
                    "error": {
                        "message": "Bad Request, invalid request Body",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        return store_and_enqueue_callback(request, CallbackType.PATIENT_SELECTION)

    @action(detail=False, methods=["POST"], url_path="patient/scan-pay/on-notify")
    def patient__scan_pay__on_notify(self, request):
        try:
            self.validate_request(request)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        return store_and_enqueue_callback(
            request, CallbackType.PATIENT_SCAN_PAY_ON_NOTIFY
        )

    @action(detail=False, methods=["POST"], url_path="patient/scan-pay/order-status")
    def patient__scan_pay__order_status(self, request):
        request_id, error = self.require_request_id(request)
        if error:
            return error

        try:
            self.validate_request(request)
        except Exception:
            scan_pay_on_order_status.delay(
                {
                    "error": {
                        "message": "Bad Request, invalid request Body",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        return store_and_enqueue_callback(
            request, CallbackType.PATIENT_SCAN_PAY_ORDER_STATUS
        )


@extend_schema(tags=["ABDM: Scan and Pay"])
class ScanPayViewSet(GenericViewSet):
    permission_classes = (AllowAny,)
    authentication_classes = []

    def get_authenticators(self):
        # initialize_request builds authenticators before ViewSet.action is set.
        if getattr(self, "action_map", {}).get("get") == "receipt":
            return [ScanPayReceiptAuthentication()]
        return super().get_authenticators()

    def get_permissions(self):
        if getattr(self, "action_map", {}).get("get") == "receipt":
            return [IsAuthenticated()]
        return super().get_permissions()

    @action(
        detail=False,
        methods=["GET"],
        url_path="receipt/(?P<order_external_id>[^/]+)",
    )
    def receipt(self, request, order_external_id):
        try:
            order = (
                PaymentOrder.objects.filter(
                    external_id=order_external_id,
                    invoice__isnull=False,
                )
                .select_related(
                    "abha_number__patient",
                    "health_facility__facility",
                    "invoice__created_by",
                )
                .first()
            )
            if order and not order_has_receipt(order):
                order = None
        except (DjangoValidationError, ValueError):
            order = None

        if not order:
            return Response(
                {"detail": "Receipt not found"}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            pdf, filename = receipt_pdf(order)
        except ObjectLocked:
            logger.info(
                "Scan-and-pay receipt is locked for order %s", order.external_id
            )
            return Response(
                {"detail": "Receipt is being prepared"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception(
                "Failed to prepare scan-and-pay receipt for order %s",
                order.external_id,
            )
            return Response(
                {"detail": "Receipt is being prepared"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
