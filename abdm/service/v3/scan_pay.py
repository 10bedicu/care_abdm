from collections import defaultdict

from django.db import transaction

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.settings import plugin_settings as settings
from abdm.utils.razorpay import get_razorpay_client
from abdm.utils.user import get_or_create_abdm_user
from care.emr.locks.billing import AccountLock, InvoiceCreateLock
from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.default_expression_evaluator import (
    evaluate_invoice_identifier_default_expression,
)
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.invoice.sync_items import sync_invoice_items
from care.utils.time_util import care_now

SERVICE_RESOURCE_CATEGORY_MAP = {
    "appointment": "OPD consultation",
    "service_request": "Laboratory and Diagnostics",
    "medication_dispense": "Pharmacy",
}
DEFAULT_CATEGORY = "Miscellaneous/Other"


def get_open_charge_items(patient, facility):
    return list(
        ChargeItem.objects.filter(
            patient=patient,
            facility=facility,
            status=ChargeItemStatusOptions.billable.value,
        ).select_related("account")
    )


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


def create_scan_pay_invoice(charge_items, account, facility, patient, abha_address):
    with transaction.atomic(), AccountLock(account):
        invoice = Invoice(
            facility=facility,
            patient=patient,
            account=account,
            title=f"Scan and Pay - {abha_address}",
            status=InvoiceStatusOptions.issued.value,
            issue_date=care_now(),
            charge_items=[item.id for item in charge_items],
            created_by=get_or_create_abdm_user(),
        )
        with InvoiceCreateLock():
            invoice.number = evaluate_invoice_identifier_default_expression(facility)
            invoice.save()
        ChargeItem.objects.filter(id__in=invoice.charge_items).update(
            status=ChargeItemStatusOptions.billed.value, paid_invoice=invoice
        )
        sync_invoice_items(invoice)
        invoice.save()
    rebalance_account_task.delay(account.id)
    return invoice


def create_scan_pay_payment_link(invoice):
    client = get_razorpay_client()
    return client.payment_link.create(
        {
            "amount": round(float(invoice.total_gross) * 100),
            "currency": "INR",
            "accept_partial": False,
            "description": invoice.title,
            "customer": {
                "name": invoice.patient.name,
                "contact": invoice.patient.phone_number,
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {
                "invoice_id": str(invoice.external_id),
                "account_id": str(invoice.account.external_id),
                "patient_id": str(invoice.patient.external_id),
                "facility_id": str(invoice.facility.external_id),
            },
        }
    )


def scan_pay_receipt_link(order: PaymentOrder):
    return f"{settings.BACKEND_DOMAIN}/api/abdm/v3/scan-pay/receipt/{order.external_id}/"


def build_scan_pay_acknowledgement(order: PaymentOrder):
    ack_status = order.status
    if ack_status in [
        PaymentOrderStatus.OPEN_ORDER_SHARED,
        PaymentOrderStatus.PAYMENT_INITIATED,
    ]:
        ack_status = PaymentOrderStatus.PENDING.value

    is_paid = order.status in PAYMENT_ORDER_PAID_STATUSES
    return {
        "status": ack_status,
        "abha_address": order.abha_number.health_id,
        "transaction_id": order.transaction_id,
        "order_number": order.order_number,
        "open_order_request_id": str(order.open_order_request_id),
        "payment_date": order.payment_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if order.payment_date
        else None,
        "payment_receipt_link": scan_pay_receipt_link(order) if is_paid else None,
    }
