import logging

from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.service.v3.scan_pay import build_scan_pay_acknowledgement
from abdm.tasks.scan_pay import scan_pay_notify
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
    order.save(update_fields=["status", "transaction_id", "payment_date"])

    scan_pay_notify.delay(
        {
            "acknowledgement": build_scan_pay_acknowledgement(order),
            "hip_id": order.health_facility.hf_id,
        },
        transaction_meta={
            "abha_number": str(order.abha_number.external_id),
            "payment_order": str(order.external_id),
            "status": order.status,
        },
    )
