import logging

from django.db.models import Sum
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.open_payment_link import ensure_staff_may_edit
from abdm.service.v3.payment_providers import balance_scan_pay_invoice
from abdm.service.v3.scan_pay import notify_scan_pay_order
from abdm.settings import plugin_settings as settings
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)


def as_payment_datetime(value):
    """A timezone-aware moment. The billing API stores datetimes as JSON strings."""
    if isinstance(value, str):
        value = parse_datetime(value)
    if value is None:
        return care_now()
    if timezone.is_naive(value):
        return timezone.make_aware(value)
    return value


@receiver(pre_save, sender=PaymentReconciliation)
def reject_payment_while_scan_pay_link_is_open(sender, instance, **kwargs):
    if not instance._state.adding or not instance.target_invoice_id:
        return
    ensure_staff_may_edit(instance.target_invoice_id)


@receiver(post_save, sender=PaymentReconciliation)
def notify_scan_pay_payment(sender, instance, created, **kwargs):
    if not created or not instance.target_invoice_id:
        return

    order = (
        PaymentOrder.objects.filter(invoice_id=instance.target_invoice_id)
        .exclude(status__in=PAYMENT_ORDER_PAID_STATUSES)
        .select_related("abha_number", "health_facility", "invoice")
        .first()
    )

    if not order:
        return

    total_paid = (
        PaymentReconciliation.objects.filter(
            target_invoice_id=instance.target_invoice_id,
            reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
            status=PaymentReconciliationStatusOptions.active.value,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    order.status = (
        PaymentOrderStatus.SUCCESS
        if total_paid >= order.invoice.total_gross
        else PaymentOrderStatus.PENDING
    )
    order.transaction_id = instance.reference_number or str(instance.external_id)
    order.payment_date = as_payment_datetime(instance.payment_datetime)
    order.save(
        update_fields=["status", "transaction_id", "payment_date", "modified_date"]
    )
    if (
        order.status == PaymentOrderStatus.SUCCESS
        and settings.ABDM_SCAN_AND_PAY_AUTO_BALANCE_INVOICE
    ):
        balance_scan_pay_invoice(order.invoice)
    notify_scan_pay_order(order)
