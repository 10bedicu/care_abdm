import logging

from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
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
    order.payment_date = instance.payment_datetime or care_now()
    order.save(
        update_fields=["status", "transaction_id", "payment_date", "modified_date"]
    )
    if (
        order.status == PaymentOrderStatus.SUCCESS
        and settings.ABDM_SCAN_AND_PAY_AUTO_BALANCE_INVOICE
    ):
        balance_scan_pay_invoice(order.invoice)
    notify_scan_pay_order(order)
