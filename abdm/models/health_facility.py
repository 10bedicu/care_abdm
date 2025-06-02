from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import F, Func, Value

from abdm.models.transaction import Transaction, TransactionStatus, TransactionType
from abdm.service.helper import ABDMAPIException
from care.utils.models.base import BaseModel


class HealthFacility(BaseModel):
    hf_id = models.CharField(max_length=50, unique=True)
    registered = models.BooleanField(default=False)
    facility = models.OneToOneField(
        "facility.Facility", on_delete=models.PROTECT, to_field="external_id"
    )

    def _update_hf_id_in_transactions(
        self, old_hf_id: str, new_hf_id: str, batch_size: int = 1000
    ):
        qs = Transaction.objects.filter(
            type=TransactionType.LINK_CARE_CONTEXT,
            meta_data__hf_id=old_hf_id,
            status__in=[TransactionStatus.INITIATED, TransactionStatus.FAILED],
        )

        paginator = Paginator(qs, batch_size)
        total_updated = 0

        for page_num in paginator.page_range:
            page = paginator.page(page_num)
            transaction_ids = [tx.id for tx in page]

            updated = Transaction.objects.filter(id__in=transaction_ids).update(
                meta_data=Func(
                    F("meta_data"),
                    Value("{hf_id}"),
                    Value(f'"{new_hf_id}"'),
                    function="jsonb_set",
                )
            )

            total_updated += updated

        return total_updated

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                old_instance = HealthFacility.objects.get(pk=self.pk)
                if old_instance.hf_id != self.hf_id:
                    try:
                        self._update_hf_id_in_transactions(
                            old_instance.hf_id, self.hf_id, batch_size=1000
                        )
                    except Exception as e:
                        raise ABDMAPIException(
                            detail="Failed to update transactions for hf_id change"
                        ) from e
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hf_id} {self.facility}"
