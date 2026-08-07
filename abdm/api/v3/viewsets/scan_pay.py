import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
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
from abdm.models import AbhaNumber, HealthFacility, PaymentOrder
from abdm.models.payment_order import PAYMENT_ORDER_PAID_STATUSES, PaymentOrderStatus
from abdm.service.v3.scan_pay import (
    build_procedures,
    build_scan_pay_acknowledgement,
    create_scan_pay_invoice,
    create_scan_pay_payment_link,
    get_open_charge_items,
)
from abdm.settings import plugin_settings as settings
from abdm.tasks.scan_pay import (
    scan_pay_on_order_status,
    scan_pay_on_selection,
    scan_pay_on_share_open_order,
)
from care.emr.models.charge_item import ChargeItem
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions

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
            validated_data = self.validate_request(request)
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

        hip_id = validated_data.get("metadata").get("hipId")
        patient_data = validated_data.get("profile").get("patient")
        abha_address = patient_data.get("abhaAddress")

        health_facility = HealthFacility.objects.filter(hf_id=hip_id).first()
        if not health_facility:
            logger.warning(
                f"Health Facility with ID: {hip_id} not found in the database"
            )
            scan_pay_on_share_open_order.delay(
                {
                    "abha_address": abha_address,
                    "error": {
                        "message": "HIP is not available",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_404_NOT_FOUND)

        abha_number = (
            AbhaNumber.objects.filter(
                Q(health_id=abha_address)
                | (
                    Q(abha_number=patient_data.get("abhaNumber"))
                    & Q(abha_number__isnull=False)
                )
            )
            .select_related("patient")
            .first()
        )

        if not abha_number or not abha_number.patient_id:
            scan_pay_on_share_open_order.delay(
                {
                    "abha_address": abha_address,
                    "error": {
                        "message": "Patient not found for the given ABHA",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        charge_items = get_open_charge_items(
            abha_number.patient, health_facility.facility
        )

        if not charge_items:
            scan_pay_on_share_open_order.delay(
                {
                    "abha_address": abha_number.health_id,
                    "error": {
                        "message": "No open orders found for the patient",
                        "code": "ABDM-9999",
                    },
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        PaymentOrder.objects.update_or_create(
            open_order_request_id=request_id,
            defaults={
                "abha_number": abha_number,
                "health_facility": health_facility,
            },
        )

        scan_pay_on_share_open_order.delay(
            {
                "abha_address": abha_number.health_id,
                "patient_uid": str(abha_number.patient.external_id),
                "procedures": build_procedures(charge_items),
                "request_id": request_id,
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="patient/selection")
    def patient__selection(self, request):  # noqa: PLR0911
        request_id = request.headers.get("REQUEST-ID")

        if not request_id:
            return Response(
                {"detail": "REQUEST-ID header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validated_data = self.validate_request(request)
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

        open_order_request_id = str(validated_data.get("openOrderRequestId"))
        abha_address = validated_data.get("abhaAddress")

        def error_response(message):
            scan_pay_on_selection.delay(
                {
                    "open_order_request_id": open_order_request_id,
                    "abha_address": abha_address,
                    "error": {"message": message, "code": "ABDM-9999"},
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        order = (
            PaymentOrder.objects.filter(open_order_request_id=open_order_request_id)
            .select_related("abha_number__patient", "health_facility__facility")
            .first()
        )

        if not order:
            return error_response("Open order not found")

        if order.abha_number.health_id != abha_address:
            return error_response("Open order does not belong to the given ABHA")

        if order.invoice_id:
            return error_response("Payment is already initiated for this order")

        service_ids = [
            service.get("serviceId")
            for procedure in validated_data.get("procedures")
            for service in procedure.get("services")
        ]

        if not service_ids:
            return error_response("No services selected")

        charge_items = list(
            ChargeItem.objects.filter(
                external_id__in=service_ids,
                patient=order.abha_number.patient,
                facility=order.health_facility.facility,
                status=ChargeItemStatusOptions.billable.value,
            ).select_related("account")
        )

        if len(charge_items) != len(set(service_ids)):
            return error_response(
                "One or more selected services are no longer available for payment"
            )

        if len({item.account_id for item in charge_items}) > 1:
            return error_response("Selected services must belong to a single account")

        try:
            invoice = create_scan_pay_invoice(
                charge_items,
                charge_items[0].account,
                order.health_facility.facility,
                order.abha_number.patient,
                abha_address,
            )
        except Exception:
            logger.exception(
                f"Failed to create invoice for scan and pay order {open_order_request_id}"
            )
            return error_response("Failed to create payment order")

        try:
            payment_link = create_scan_pay_payment_link(invoice)
        except Exception:
            logger.exception(
                f"Failed to create payment link for scan and pay order {open_order_request_id}"
            )
            return error_response("Failed to create payment link")

        order.invoice = invoice
        order.order_number = invoice.number or str(invoice.external_id)
        order.payment_link_id = payment_link.get("id")
        order.status = PaymentOrderStatus.PAYMENT_INITIATED
        order.save(
            update_fields=["invoice", "order_number", "payment_link_id", "status"]
        )

        scan_pay_on_selection.delay(
            {
                "open_order_request_id": open_order_request_id,
                "abha_address": abha_address,
                "procedures": build_procedures(charge_items),
                "payment_bundle": {
                    "payment_mode": "GATEWAY",
                    "payment_url": payment_link.get("short_url"),
                    "order_number": order.order_number,
                    "amount": float(invoice.total_gross),
                    "merchant_id": settings.ABDM_SCAN_AND_PAY_MERCHANT_ID,
                    "description": invoice.title,
                },
                "request_id": request_id,
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="patient/scan-pay/on-notify")
    def patient__scan_pay__on_notify(self, request):
        try:
            validated_data = self.validate_request(request)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        acknowledgement = validated_data.get("acknowledgement") or {}
        logger.info(
            f"Scan and pay notify acknowledged by PHR for order {acknowledgement.get('openOrderRequestId')}"
        )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="patient/scan-pay/order-status")
    def patient__scan_pay__order_status(self, request):
        request_id = request.headers.get("REQUEST-ID")

        if not request_id:
            return Response(
                {"detail": "REQUEST-ID header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validated_data = self.validate_request(request)
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

        query_status = validated_data.get("queryStatus")
        order = (
            PaymentOrder.objects.filter(
                open_order_request_id=str(query_status.get("openOrderRequestId"))
            )
            .select_related("abha_number")
            .first()
        )

        if not order or (
            order.order_number
            and order.order_number != query_status.get("orderNumber")
        ):
            scan_pay_on_order_status.delay(
                {
                    "error": {"message": "Order not found", "code": "ABDM-9999"},
                    "request_id": request_id,
                }
            )
            return Response(status=status.HTTP_200_OK)

        scan_pay_on_order_status.delay(
            {
                "acknowledgement": build_scan_pay_acknowledgement(order),
                "request_id": request_id,
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)


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
