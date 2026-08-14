from django.db import models

from care.utils.models.base import BaseModel


class PaymentOrderStatus(models.TextChoices):
    OPEN_ORDER_SHARED = "OPEN_ORDER_SHARED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    SUCCESS = "SUCCESS"
    CANCELED = "CANCELED"
    PENDING = "PENDING"
    FAIL = "FAIL"
    REFUND_INITIATED = "REFUND_INITIATED"
    REFUND_SUCCESS = "REFUND_SUCCESS"


PAYMENT_ORDER_PAID_STATUSES = [
    PaymentOrderStatus.SUCCESS,
    PaymentOrderStatus.REFUND_INITIATED,
    PaymentOrderStatus.REFUND_SUCCESS,
]


class PaymentOrder(BaseModel):
    open_order_request_id = models.UUIDField(unique=True, db_index=True)
    abha_number = models.ForeignKey("abdm.AbhaNumber", on_delete=models.PROTECT)
    health_facility = models.ForeignKey("abdm.HealthFacility", on_delete=models.PROTECT)
    invoice = models.ForeignKey(
        "emr.Invoice", on_delete=models.PROTECT, null=True, blank=True
    )
    status = models.CharField(
        max_length=50,
        choices=PaymentOrderStatus.choices,
        default=PaymentOrderStatus.OPEN_ORDER_SHARED,
    )
    order_number = models.CharField(
        max_length=100, null=True, blank=True, db_index=True
    )
    payment_link_id = models.CharField(max_length=500, null=True, blank=True)
    transaction_id = models.CharField(max_length=100, null=True, blank=True)
    payment_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PaymentOrder: {self.open_order_request_id} - {self.status}"
