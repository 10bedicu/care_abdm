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
from care.emr.locks.billing import AccountLock, InvoiceLock
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
from care.utils.lock import Lock, ObjectLocked
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)


class PaymentOrderLock(Lock):
    """Serialises settlement/closure of one order across webhook and polling workers."""

    def __init__(self, order: PaymentOrder):
        super().__init__(f"abdm:payment_order:{order.id}")


def _fresh(order: PaymentOrder) -> PaymentOrder:
    return PaymentOrder.objects.select_related(
        "invoice__account", "health_facility__facility"
    ).get(pk=order.pk)


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


def balance_scan_pay_invoice(invoice: Invoice) -> bool:
    """
    Move a fully paid, issued invoice to ``balanced`` and mark its charge items
    paid, the same way the core invoice API does when staff balance it.

    Never fails the surrounding settlement: if the invoice is locked elsewhere
    the step is skipped and staff can balance it manually.
    """
    try:
        with InvoiceLock(invoice), transaction.atomic():
            invoice = Invoice.objects.select_related("account").get(pk=invoice.pk)
            if invoice.status != InvoiceStatusOptions.issued.value:
                return False
            now = care_now()
            ChargeItem.objects.filter(
                account=invoice.account,
                status=ChargeItemStatusOptions.billed.value,
                id__in=invoice.charge_items,
            ).update(
                status=ChargeItemStatusOptions.paid.value,
                paid_invoice=invoice,
                paid_on=now,
            )
            invoice.status = InvoiceStatusOptions.balanced.value
            invoice.updated_by = get_or_create_abdm_user()
            invoice.save(update_fields=["status", "updated_by", "modified_date"])
    except ObjectLocked:
        logger.warning(
            "Invoice %s is locked; leaving it issued after scan and pay payment",
            invoice.number or invoice.external_id,
        )
        return False
    return True


def close_payment_order(order: PaymentOrder, status: PaymentOrderStatus) -> bool:
    """
    Move a still-pending order to a terminal state (``FAIL`` or ``CANCELED``),
    void its invoice so the selected services can be paid for again, and tell
    the PHR about the outcome.

    Providers call this when the gateway reports the payment link expired,
    cancelled or failed, so the order stops being polled. Orders that already
    reached a terminal state are left untouched; returns whether it changed.
    """
    # imported here: service.v3.scan_pay imports this package at module level
    from abdm.service.v3.scan_pay import notify_scan_pay_order

    with PaymentOrderLock(order), transaction.atomic():
        order = _fresh(order)
        if order.status not in PAYMENT_ORDER_PENDING_STATUSES:
            return False
        if order.invoice_id:
            outcome = "cancelled" if status == PaymentOrderStatus.CANCELED else "failed"
            void_scan_pay_invoice(
                order.invoice,
                f"Scan and pay payment {outcome} (order {order.order_number})",
            )
        order.status = status
        order.save(update_fields=["status", "modified_date"])
        notify_scan_pay_order(order)
    return True


def create_payment_reconciliation(
    order: PaymentOrder, reference, amount=None, *, created_by=None, note=None
) -> bool:
    """
    Record a successful online payment against the order's invoice, once.

    Runs under the order lock with a fresh copy of the order so concurrent
    webhook/polling confirmations cannot credit the invoice twice. The amount
    recorded is what the provider confirmed (else what the link was created
    for), never the invoice's current total; a deviation, or an invoice that
    was voided before the payment landed, is noted for review. The post-save
    signal on ``PaymentReconciliation`` marks the order paid and notifies the
    PHR. ``created_by``/``note`` identify a staff member confirming the payment
    by hand. Returns whether a reconciliation was recorded by this call.
    """
    with PaymentOrderLock(order), transaction.atomic():
        order = _fresh(order)
        if not order.invoice_id or order.status in PAYMENT_ORDER_PAID_STATUSES:
            return False
        invoice = order.invoice
        if (
            reference
            and PaymentReconciliation.objects.filter(
                target_invoice=invoice,
                reference_number=reference,
                status=PaymentReconciliationStatusOptions.active.value,
            ).exists()
        ):
            return False

        expected = order.amount if order.amount is not None else invoice.total_gross
        recorded = Decimal(str(amount)) if amount is not None else expected
        notes = [
            f"Scan and pay via {order.provider or 'gateway'} (order {order.order_number})."
        ]
        if note:
            notes.append(note)
        if recorded != expected:
            notes.append(
                f"Needs review: amount mismatch, expected {expected}, got {recorded}."
            )
            logger.warning(
                "Scan and pay order %s confirmed %s but link was for %s",
                order.order_number,
                recorded,
                expected,
            )
        if invoice.status in INVOICE_CANCELLED_STATUS:
            notes.append(
                "Needs review: invoice was voided before the payment was confirmed."
            )
            logger.warning(
                "Scan and pay order %s paid after its invoice %s was voided",
                order.order_number,
                invoice.number or invoice.external_id,
            )

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
            note=" ".join(notes),
            tendered_amount=recorded,
            returned_amount=Decimal(0),
            amount=recorded,
            created_by=created_by or get_or_create_abdm_user(),
        )
        account_id = invoice.account_id
        transaction.on_commit(lambda: rebalance_account_task.delay(account_id))
    return True


def reconcile_payment_order(
    order_number: str, reference, amount=None
) -> PaymentOrder | None:
    """
    Look up an order by its provider order number and reconcile it.

    Intended for provider webhooks that only carry the order number. Returns the
    matched order (if any) whether or not anything new was recorded.
    """
    order = (
        PaymentOrder.objects.filter(order_number=order_number)
        .select_related("invoice__account", "health_facility__facility")
        .first()
    )
    if not order or not order.invoice_id:
        return order

    create_payment_reconciliation(order, reference, amount)
    return order
