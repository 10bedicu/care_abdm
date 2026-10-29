import logging
from collections import defaultdict
from urllib.parse import urlencode

import jwt
from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from jwt.exceptions import InvalidTokenError

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.service.v3.payment_providers import get_provider
from abdm.settings import plugin_settings as settings
from abdm.tasks.scan_pay import scan_pay_notify
from abdm.utils.user import get_or_create_abdm_user
from care.emr.locks.billing import AccountLock, InvoiceCreateLock
from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.resources.account.spec import (
    AccountBillingStatusOptions,
    AccountStatusOptions,
)
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.default_expression_evaluator import (
    evaluate_invoice_identifier_default_expression,
)
from care.emr.resources.invoice.spec import (
    INVOICE_CANCELLED_STATUS,
    InvoiceStatusOptions,
)
from care.emr.resources.invoice.sync_items import sync_invoice_items
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)

SERVICE_RESOURCE_CATEGORY_MAP = {
    "appointment": "OPD consultation",
    "service_request": "Laboratory and Diagnostics",
    "medication_dispense": "Pharmacy",
}
DEFAULT_CATEGORY = "Miscellaneous/Other"
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _invoice_title(abha_address, issued_at) -> str:
    local = timezone.localtime(issued_at)
    hour = local.strftime("%I").lstrip("0") or "12"
    stamp = (
        f"{local.day:02d} {_MONTHS[local.month - 1]} {local.year}, "
        f"{hour}:{local.strftime('%M')} {local.strftime('%p')}"
    )
    return f"Scan and Pay - {abha_address} - {stamp}"


class ScanPayPaymentLinkError(Exception):
    pass


def get_open_charge_items(patient, facility, service_ids=None):
    queryset = ChargeItem.objects.filter(
        patient=patient,
        facility=facility,
        status=ChargeItemStatusOptions.billable.value,
        total_price__gt=0,
        account__status=AccountStatusOptions.active.value,
        account__billing_status=AccountBillingStatusOptions.open.value,
    )
    if service_ids is not None:
        queryset = queryset.filter(external_id__in=service_ids)
    return list(queryset.select_related("account"))


def build_procedures(charge_items):
    grouped = defaultdict(list)
    for item in charge_items:
        category = SERVICE_RESOURCE_CATEGORY_MAP.get(
            item.service_resource, DEFAULT_CATEGORY
        )
        grouped[category].append(
            {
                "service_id": str(item.external_id),
                "name": item.title,
                "description": item.description or "",
                "amount": float(item.total_price or 0),
            }
        )
    return [
        {"category": category, "services": services}
        for category, services in grouped.items()
    ]


def _create_invoice(charge_items, account, facility, patient, abha_address, created_by):
    issued_at = care_now()
    invoice = Invoice(
        facility=facility,
        patient=patient,
        account=account,
        title=_invoice_title(abha_address, issued_at),
        status=InvoiceStatusOptions.issued.value,
        issue_date=issued_at,
        charge_items=[item.id for item in charge_items],
        created_by=created_by,
    )
    with InvoiceCreateLock():
        invoice.number = evaluate_invoice_identifier_default_expression(facility)
        invoice.save()
    ChargeItem.objects.filter(id__in=invoice.charge_items).update(
        status=ChargeItemStatusOptions.billed.value, paid_invoice=invoice
    )
    sync_invoice_items(invoice)
    invoice.save()
    return invoice


def create_scan_pay_invoice_with_payment_link(
    charge_items, account, facility, patient, abha_address
):
    """
    Invoice the selected charge items and get a payment link for the invoice.

    Runs in one transaction under the account lock: if the gateway does not
    return a link, the invoice and charge item updates are rolled back so the
    items stay billable.
    """
    created_by = get_or_create_abdm_user()
    with transaction.atomic(), AccountLock(account):
        invoice = _create_invoice(
            charge_items, account, facility, patient, abha_address, created_by
        )
        try:
            payment = create_scan_pay_payment_link(invoice)
        except Exception as exc:
            raise ScanPayPaymentLinkError(str(exc)) from exc
        transaction.on_commit(lambda: rebalance_account_task.delay(account.id))
    return invoice, payment


def create_scan_pay_payment_link(invoice):
    provider = get_provider(settings.ABDM_SCAN_AND_PAY_PROVIDER)
    result = provider.create_payment_link(invoice)
    result.setdefault("provider", provider.name)
    result.setdefault("amount", invoice.total_gross)
    return result


def order_has_receipt(order: PaymentOrder) -> bool:
    """A paid order, or one whose invoice was cancelled, can be printed."""
    if not order.invoice_id:
        return False
    if order.status in PAYMENT_ORDER_PAID_STATUSES:
        return True
    invoice = order.invoice
    return bool(invoice and invoice.status in INVOICE_CANCELLED_STATUS)


RECEIPT_TOKEN_TYPE = "scan_pay_receipt"


def scan_pay_receipt_token(order: PaymentOrder) -> str:
    """A signed capability for this order's receipt. It is not stored."""
    return jwt.encode(
        {"sub": str(order.external_id), "type": RECEIPT_TOKEN_TYPE},
        django_settings.SECRET_KEY,
        algorithm="HS256",
    )


def scan_pay_receipt_order_id(token: str) -> str | None:
    """Return the order id from a receipt token, or None when it does not verify."""
    try:
        payload = jwt.decode(
            token,
            django_settings.SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["sub", "type"]},
        )
    except InvalidTokenError:
        return None
    if payload.get("type") != RECEIPT_TOKEN_TYPE:
        return None
    order_id = payload.get("sub")
    if not isinstance(order_id, str) or not order_id:
        return None
    return order_id


def scan_pay_receipt_token_matches(order_id: str, token: str) -> bool:
    """True when the token is a receipt capability for this order id."""
    return scan_pay_receipt_order_id(token) == str(order_id)


def scan_pay_receipt_link(order: PaymentOrder):
    query = urlencode({"token": scan_pay_receipt_token(order)})
    return (
        f"{settings.BACKEND_DOMAIN}/api/abdm/v3/scan-pay/receipt/"
        f"{order.external_id}/?{query}"
    )


def acknowledgement_payment_date(value) -> str | None:
    """Format a payment moment. The billing API can leave a JSON string on the order."""
    if isinstance(value, str):
        value = parse_datetime(value)
    if not value:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# Statuses ABDM accepts on an acknowledgement. EXPIRED is local: the gateway
# rejects it (ABDM-2501) and an expired link is reported as FAIL.
_ABDM_ACKNOWLEDGEMENT_STATUS = {
    PaymentOrderStatus.OPEN_ORDER_SHARED: PaymentOrderStatus.PENDING.value,
    PaymentOrderStatus.PAYMENT_INITIATED: PaymentOrderStatus.PENDING.value,
    PaymentOrderStatus.EXPIRED: PaymentOrderStatus.FAIL.value,
}

SCAN_PAY_FAILURE_MESSAGES = {
    PaymentOrderStatus.FAIL: "Payment failed. Please try again.",
    PaymentOrderStatus.EXPIRED: "Payment link has expired. Please try again.",
    PaymentOrderStatus.CANCELED: "Payment was cancelled.",
}


def abdm_acknowledgement_status(status: str) -> str:
    return _ABDM_ACKNOWLEDGEMENT_STATUS.get(status, status)


def scan_pay_status_error(order: PaymentOrder) -> dict | None:
    """Reason ABDM can attach to a payment that did not succeed.

    The acknowledgement has no reason field. Notify and on-order-status take an
    optional error (code and message), and the spec says to send it for a
    failure. The invoice's cancellation reason is the specific one recorded
    when the order was closed; the short message is the fallback.
    """
    fallback = SCAN_PAY_FAILURE_MESSAGES.get(order.status)
    if not fallback:
        return None
    message = ""
    if order.invoice_id:
        message = (order.invoice.cancelled_reason or "").strip()
    return {"code": "ABDM-9999", "message": message or fallback}


def build_scan_pay_acknowledgement(order: PaymentOrder):
    is_paid = order.status in PAYMENT_ORDER_PAID_STATUSES
    return {
        "status": abdm_acknowledgement_status(order.status),
        "abha_address": order.requesting_abha_address,
        "transaction_id": order.transaction_id,
        "order_number": order.order_number,
        "open_order_request_id": str(order.open_order_request_id),
        "payment_date": acknowledgement_payment_date(order.payment_date),
        "payment_receipt_link": scan_pay_receipt_link(order) if is_paid else None,
    }


def notify_scan_pay_order(order: PaymentOrder) -> None:
    """Tell the PHR the order's outcome (paid, pending, failed, cancelled).

    Queued on commit so the PHR is never told about a state that rolls back.
    A local EXPIRED status is reported as FAIL, with the reason on the error
    ABDM accepts for a failed payment.
    """
    payload = {
        "acknowledgement": build_scan_pay_acknowledgement(order),
        "hip_id": order.health_facility.hf_id,
    }
    if abdm_acknowledgement_status(order.status) == PaymentOrderStatus.FAIL.value:
        error = scan_pay_status_error(order)
        if error:
            payload["error"] = error
    transaction_meta = {
        "abha_number": str(order.abha_number.external_id),
        "payment_order": str(order.external_id),
        "status": order.status,
    }
    transaction.on_commit(
        lambda: scan_pay_notify.delay(payload, transaction_meta=transaction_meta)
    )
