import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.template.loader import render_to_string
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from weasyprint import HTML

from abdm.api.v3.serializers.scan_pay import (
    PatientScanPayOnNotifySerializer,
    PatientScanPayOrderStatusSerializer,
    PatientSelectionSerializer,
    PatientShareOpenOrderSerializer,
)
from abdm.authentication import ABDMAuthentication
from abdm.models import CallbackType, PaymentOrder
from abdm.models.payment_order import PAYMENT_ORDER_PAID_STATUSES
from abdm.tasks.scan_pay import (
    scan_pay_on_order_status,
    scan_pay_on_selection,
    scan_pay_on_share_open_order,
)
from abdm.utils.callback import store_and_enqueue_callback
from care.emr.models.charge_item import ChargeItem

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

    @action(detail=False, methods=["POST"], url_path="patient/share/open-order")
    def patient__share__open_order(self, request):
        request_id = request.headers.get("REQUEST-ID")

        if not request_id:
            return Response(
                {"detail": "REQUEST-ID header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        request_id = request.headers.get("REQUEST-ID")

        if not request_id:
            return Response(
                {"detail": "REQUEST-ID header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        request_id = request.headers.get("REQUEST-ID")

        if not request_id:
            return Response(
                {"detail": "REQUEST-ID header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
                    status__in=PAYMENT_ORDER_PAID_STATUSES,
                    invoice__isnull=False,
                )
                .select_related(
                    "abha_number", "health_facility__facility", "invoice"
                )
                .first()
            )
        except (DjangoValidationError, ValueError):
            order = None

        if not order:
            return Response(
                {"detail": "Receipt not found"}, status=status.HTTP_404_NOT_FOUND
            )

        charge_items = ChargeItem.objects.filter(id__in=order.invoice.charge_items)
        html = render_to_string(
            "abdm/scan_and_pay_receipt.html",
            {
                "order": order,
                "invoice": order.invoice,
                "facility": order.health_facility.facility,
                "hf_id": order.health_facility.hf_id,
                "patient": order.abha_number.patient,
                "abha_address": order.abha_number.health_id,
                "charge_items": charge_items,
            },
        )

        pdf = HTML(string=html).write_pdf()
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="receipt-{order.order_number}.pdf"'
        )
        return response
