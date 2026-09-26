from decimal import Decimal

from abdm.models.payment_order import PAYMENT_ORDER_PAID_STATUSES, PaymentOrder
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationIssuerTypeOptions,
    PaymentReconciliationKindOptions,
    PaymentReconciliationOutcomeOptions,
    PaymentReconciliationPaymentMethodOptions,
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.time_util import care_now


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
