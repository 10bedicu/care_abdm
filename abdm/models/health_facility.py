from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import F, Func, Value
from django.utils import timezone

from abdm.models.transaction import Transaction, TransactionStatus, TransactionType
from care.utils.models.base import BaseModel

# links, link tokens and consents are held by ABDM against the hf_id, so it can
# be corrected only shortly after the health facility is added
HF_ID_CHANGE_WINDOW = timedelta(hours=48)
HF_ID_CHANGE_WINDOW_MESSAGE = (
    "The health facility ID cannot be changed more than 48 hours after the "
    "health facility was added"
)


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

    def _is_hf_id_change_late(self, stored: "HealthFacility") -> bool:
        if stored.hf_id == self.hf_id:
            return False
        added_at = stored.created_date
        return not added_at or timezone.now() - added_at > HF_ID_CHANGE_WINDOW

    def clean(self):
        super().clean()
        stored = HealthFacility.objects.filter(pk=self.pk).first() if self.pk else None
        if stored and self._is_hf_id_change_late(stored):
            raise ValidationError({"hf_id": HF_ID_CHANGE_WINDOW_MESSAGE})

    def save(self, *args, **kwargs):
        from abdm.service.helper import ABDMAPIException

        with transaction.atomic():
            if self.pk:
                old_instance = HealthFacility.objects.get(pk=self.pk)
                if old_instance.hf_id != self.hf_id:
                    if self._is_hf_id_change_late(old_instance):
                        raise ValidationError(
                            HF_ID_CHANGE_WINDOW_MESSAGE, code="hf_id_change_window"
                        )

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
