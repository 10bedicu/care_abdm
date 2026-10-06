import logging

from abdm.models import AbhaNumber, HealthFacility, PaymentOrder
from abdm.models.payment_order import PaymentOrderStatus
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.service.v3.gateway import GatewayService
from abdm.service.v3.scan_pay import (
    ScanPayPaymentLinkError,
    build_procedures,
    build_scan_pay_acknowledgement,
    create_scan_pay_invoice_with_payment_link,
    get_open_charge_items,
)
from care.utils.lock import ObjectLocked

logger = logging.getLogger(__name__)

ORDER_FAILURE_MESSAGES = {
    PaymentOrderStatus.FAIL: "Payment failed. Please try again.",
    PaymentOrderStatus.CANCELED: "Payment was cancelled.",
}


def handle_patient_share_open_order(validated_data: dict, headers: dict):
    request_id = headers.get("REQUEST-ID")

    hip_id = validated_data.get("metadata").get("hipId")
    patient_data = validated_data.get("profile").get("patient")
    abha_address = patient_data.get("abhaAddress")

    health_facility = HealthFacility.objects.filter(hf_id=hip_id).first()
    if not health_facility:
        GatewayService.patient__on_share_open_order(
            {
                "abha_address": abha_address,
                "error": {
                    "message": "HIP is not available",
                    "code": "ABDM-9999",
                },
                "request_id": request_id,
            }
        )

        raise CallbackProcessingError(
            f"Health Facility with ID: {hip_id} not found in the database"
        )

    abha_number = (
        AbhaNumber.objects.filter(health_id=abha_address)
        .select_related("patient")
        .first()
    )

    if not abha_number or not abha_number.patient_id:
        GatewayService.patient__on_share_open_order(
            {
                "abha_address": abha_address,
                "error": {
                    "message": "Patient not found for the given ABHA address",
                    "code": "ABDM-9999",
                },
                "request_id": request_id,
            }
        )
        return

    charge_items = get_open_charge_items(abha_number.patient, health_facility.facility)

    if not charge_items:
        GatewayService.patient__on_share_open_order(
            {
                "abha_address": abha_address,
                "error": {
                    "message": "No open orders found for the patient",
                    "code": "ABDM-9999",
                },
                "request_id": request_id,
            }
        )
        return

    PaymentOrder.objects.update_or_create(
        open_order_request_id=request_id,
        defaults={
            "abha_number": abha_number,
            "abha_address": abha_address,
            "health_facility": health_facility,
        },
    )

    GatewayService.patient__on_share_open_order(
        {
            "abha_address": abha_address,
            "patient_uid": str(abha_number.patient.external_id),
            "procedures": build_procedures(charge_items),
            "request_id": request_id,
        }
    )


def handle_patient_selection(validated_data: dict, headers: dict):  # noqa: PLR0911
    request_id = headers.get("REQUEST-ID")

    open_order_request_id = str(validated_data.get("openOrderRequestId"))
    abha_address = validated_data.get("abhaAddress")

    def send_error(message):
        GatewayService.patient__on_selection(
            {
                "open_order_request_id": open_order_request_id,
                "abha_address": abha_address,
                "error": {"message": message, "code": "ABDM-9999"},
                "request_id": request_id,
            }
        )

    order = (
        PaymentOrder.objects.filter(open_order_request_id=open_order_request_id)
        .select_related("abha_number__patient", "health_facility__facility")
        .first()
    )

    if not order:
        return send_error("Open order not found")

    if order.requesting_abha_address != abha_address:
        return send_error("Open order does not belong to the given ABHA")

    if order.invoice_id:
        return send_error("Payment is already initiated for this order")

    if order.status != PaymentOrderStatus.OPEN_ORDER_SHARED:
        return send_error("Open order has expired; please scan again")

    service_ids = [
        service.get("serviceId")
        for procedure in validated_data.get("procedures")
        for service in procedure.get("services")
    ]

    if not service_ids:
        return send_error("No services selected")

    charge_items = get_open_charge_items(
        order.abha_number.patient,
        order.health_facility.facility,
        service_ids=service_ids,
    )

    if len(charge_items) != len(set(service_ids)):
        return send_error(
            "One or more selected services are no longer available for payment"
        )

    if len({item.account_id for item in charge_items}) > 1:
        return send_error("Selected services must belong to a single account")

    try:
        invoice, payment = create_scan_pay_invoice_with_payment_link(
            charge_items,
            charge_items[0].account,
            order.health_facility.facility,
            order.abha_number.patient,
            abha_address,
        )
    except ObjectLocked:
        # propagates to the task which retries shortly after
        raise
    except ScanPayPaymentLinkError:
        logger.exception(
            f"Failed to create payment link for scan and pay order {open_order_request_id}"
        )
        return send_error("Failed to create payment link")
    except Exception:
        logger.exception(
            f"Failed to create invoice for scan and pay order {open_order_request_id}"
        )
        return send_error("Failed to create payment order")

    order.invoice = invoice
    order.order_number = payment["order_number"]
    order.payment_link_id = payment["payment_link_id"]
    order.provider = payment.get("provider", "")
    order.amount = payment.get("amount")
    order.status = PaymentOrderStatus.PAYMENT_INITIATED
    order.save(
        update_fields=[
            "invoice",
            "order_number",
            "payment_link_id",
            "provider",
            "amount",
            "status",
        ]
    )

    GatewayService.patient__on_selection(
        {
            "open_order_request_id": open_order_request_id,
            "abha_address": abha_address,
            "procedures": build_procedures(charge_items),
            "payment_bundle": {
                "payment_mode": "GATEWAY",
                "payment_url": payment["payment_url"],
                "order_number": order.order_number,
                "amount": float(invoice.total_gross),
                "merchant_id": order.health_facility.hf_id,
                "description": invoice.title,
            },
            "request_id": request_id,
        }
    )
    return None


def handle_scan_pay_on_notify(validated_data: dict, headers: dict):
    acknowledgement = validated_data.get("acknowledgement") or {}
    logger.info(
        f"Scan and pay notify acknowledged by PHR for order {acknowledgement.get('openOrderRequestId')}"
    )


def handle_scan_pay_order_status(validated_data: dict, headers: dict):
    request_id = headers.get("REQUEST-ID")

    query_status = validated_data.get("queryStatus")
    order = (
        PaymentOrder.objects.filter(
            open_order_request_id=str(query_status.get("openOrderRequestId"))
        )
        .select_related("abha_number")
        .first()
    )

    if not order or (
        order.order_number and order.order_number != query_status.get("orderNumber")
    ):
        GatewayService.patient__scan_pay_on_order_status(
            {
                "error": {"message": "Order not found", "code": "ABDM-9999"},
                "request_id": request_id,
            }
        )
        return

    GatewayService.patient__scan_pay_on_order_status(
        {
            "acknowledgement": build_scan_pay_acknowledgement(order),
            "request_id": request_id,
            "error": {
                "message": ORDER_FAILURE_MESSAGES[order.status],
                "code": "ABDM-9999",
            }
            if order.status in ORDER_FAILURE_MESSAGES
            else None,
        }
    )
