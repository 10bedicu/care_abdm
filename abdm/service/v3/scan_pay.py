import logging
import re
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.settings import plugin_settings as settings
from abdm.utils import sbi_epay
from abdm.utils.razorpay import get_razorpay_client
from abdm.utils.sbi_epay import SbiEpayError
from abdm.utils.user import get_or_create_abdm_user
from care.emr.locks.billing import AccountLock, InvoiceCreateLock
from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.default_expression_evaluator import (
    evaluate_invoice_identifier_default_expression,
)
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.invoice.sync_items import sync_invoice_items
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationIssuerTypeOptions,
    PaymentReconciliationKindOptions,
    PaymentReconciliationOutcomeOptions,
    PaymentReconciliationPaymentMethodOptions,
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)

SBI_EPAY_PAID_RESPONSE_STATUSES = {"SUCCESS"}

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
    if settings.ABDM_SCAN_AND_PAY_PROVIDER == "sbi_epay":
        return _create_sbi_epay_payment_link(invoice)
    return _create_razorpay_payment_link(invoice)


def _create_razorpay_payment_link(invoice):
    client = get_razorpay_client()
    link = client.payment_link.create(
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
    return {
        "order_number": invoice.number or str(invoice.external_id),
        "payment_link_id": link.get("id"),
        "payment_url": link.get("short_url"),
    }


def _sbi_other_details(invoice):
    return re.sub(r"[^A-Za-z0-9]", "", invoice.patient.name or "")[:100] or "CARE"


def _create_sbi_epay_payment_link(invoice):
    merch_order_no = sbi_epay.merch_order_number()
    result = sbi_epay.create_payment_link(
        merch_order_no=merch_order_no,
        amount=invoice.total_gross,
        other_details=_sbi_other_details(invoice),
    )
    payment_url = result.get("paymentUrl")
    if not payment_url:
        raise SbiEpayError("SBI ePay did not return a payment URL")
    return {
        "order_number": merch_order_no,
        "payment_link_id": payment_url,
        "payment_url": payment_url,
    }


def reconcile_sbi_epay_order(order: PaymentOrder):
    if settings.ABDM_SCAN_AND_PAY_PROVIDER != "sbi_epay":
        return
    if not order.invoice_id or order.status in PAYMENT_ORDER_PAID_STATUSES:
        return

    result = sbi_epay.status_query(
        merch_order_no=order.order_number,
        amount=order.invoice.total_gross,
    )
    status = (result.get("Response Status") or "").upper()
    if status not in SBI_EPAY_PAID_RESPONSE_STATUSES:
        logger.info(
            "SBI ePay order %s not paid, status %s", order.order_number, status
        )
        return

    reference = result.get("SBIePayRefID/ATRN") or result.get("Bank Reference Number")
    _create_sbi_epay_reconciliation(order, reference)


def reconcile_sbi_epay_push(push: dict):
    order = (
        PaymentOrder.objects.filter(order_number=push.get("merch_order_no"))
        .exclude(status__in=PAYMENT_ORDER_PAID_STATUSES)
        .select_related("invoice__account", "health_facility__facility")
        .first()
    )
    if not order or not order.invoice_id:
        return order

    if (push.get("status") or "").upper() not in SBI_EPAY_PAID_RESPONSE_STATUSES:
        logger.info(
            "SBI ePay push for order %s not paid, status %s",
            push.get("merch_order_no"),
            push.get("status"),
        )
        return order

    _create_sbi_epay_reconciliation(
        order, push.get("atrn") or push.get("bank_ref_number")
    )
    return order


def _create_sbi_epay_reconciliation(order: PaymentOrder, reference):
    invoice = order.invoice
    PaymentReconciliation.objects.create(
        facility=order.health_facility.facility,
        target_invoice=invoice,
        account=invoice.account,
        reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
        status=PaymentReconciliationStatusOptions.active.value,
        kind=PaymentReconciliationKindOptions.online.value,
        issuer_type=PaymentReconciliationIssuerTypeOptions.patient.value,
        outcome=PaymentReconciliationOutcomeOptions.complete.value,
        method=PaymentReconciliationPaymentMethodOptions.ccca.value,
        payment_datetime=care_now(),
        reference_number=reference,
        tendered_amount=invoice.total_gross,
        returned_amount=Decimal(0),
        amount=invoice.total_gross,
        created_by=get_or_create_abdm_user(),
    )
    rebalance_account_task.delay(invoice.account_id)



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
