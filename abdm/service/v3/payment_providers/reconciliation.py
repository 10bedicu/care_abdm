import logging
from decimal import Decimal

from django.db import transaction

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PAYMENT_ORDER_PENDING_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.utils.user import get_or_create_abdm_user
from care.emr.locks.billing import AccountLock
from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import (
    INVOICE_CANCELLED_STATUS,
    InvoiceStatusOptions,
)
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


def void_scan_pay_invoice(invoice: Invoice, reason: str) -> bool:
    """
    Mark an unpaid scan-and-pay invoice entered-in-error and release its charge
    items back to billable, the same way the core cancel-invoice API does.

    Invoices that already received a payment are left for staff to handle.
    """
    if invoice.status in INVOICE_CANCELLED_STATUS:
        return False
    if PaymentReconciliation.objects.filter(
        target_invoice=invoice,
        status=PaymentReconciliationStatusOptions.active.value,
    ).exists():
        logger.warning(
            "Scan and pay invoice %s has payments recorded; not voiding it",
            invoice.number or invoice.external_id,
        )
        return False

    updated_by = get_or_create_abdm_user()
    with transaction.atomic(), AccountLock(invoice.account):
        invoice.status = InvoiceStatusOptions.entered_in_error.value
        invoice.cancelled_reason = reason
        invoice.updated_by = updated_by
        invoice.save(
            update_fields=["status", "cancelled_reason", "updated_by", "modified_date"]
        )
        ChargeItem.objects.filter(
            account=invoice.account,
            id__in=invoice.charge_items,
        ).update(
            status=ChargeItemStatusOptions.billable.value,
            paid_invoice=None,
            paid_on=None,
        )
        transaction.on_commit(lambda: rebalance_account_task.delay(invoice.account_id))
    return True


def close_payment_order(order: PaymentOrder, status: PaymentOrderStatus) -> bool:
    """
    Move a still-pending order to a terminal state (``FAIL`` or ``CANCELED``)
    and void its invoice so the selected services can be paid for again.

    Providers call this when the gateway reports the payment link expired,
    cancelled or failed, so the order stops being polled. Orders that already
    reached a terminal state are left untouched; returns whether it changed.
    """
    if order.status not in PAYMENT_ORDER_PENDING_STATUSES:
        return False
    with transaction.atomic():
        if order.invoice_id:
            outcome = "cancelled" if status == PaymentOrderStatus.CANCELED else "failed"
            void_scan_pay_invoice(
                order.invoice,
                f"Scan and pay payment {outcome} (order {order.order_number})",
            )
        order.status = status
        order.save(update_fields=["status", "modified_date"])
    return True


def create_payment_reconciliation(order: PaymentOrder, reference) -> None:
    """
    Record a successful online payment against the order's invoice.

    The post-save signal on ``PaymentReconciliation`` marks the order paid and
    notifies the PHR. Payment providers call this once they have confirmed a
    payment (via status query or webhook).
    """
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
        note=f"Scan and pay via {order.provider or 'gateway'} (order {order.order_number}).",
        tendered_amount=invoice.total_gross,
        returned_amount=Decimal(0),
        amount=invoice.total_gross,
        created_by=get_or_create_abdm_user(),
    )
    rebalance_account_task.delay(invoice.account_id)


def reconcile_payment_order(order_number: str, reference) -> PaymentOrder | None:
    """
    Look up an unpaid order by its provider order number and reconcile it.

    Intended for provider webhooks that only carry the order number. Returns the
    matched order (if any).
    """
    order = (
        PaymentOrder.objects.filter(order_number=order_number)
        .exclude(status__in=PAYMENT_ORDER_PAID_STATUSES)
        .select_related("invoice__account", "health_facility__facility")
        .first()
    )
    if not order or not order.invoice_id:
        return order

    create_payment_reconciliation(order, reference)
    return order
