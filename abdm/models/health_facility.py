from django.db import models, transaction

from abdm.models.permissions.health_facility import HealthFacilityPermissions
from abdm.service.helper import ABDMAPIException, update_hf_id_in_transactions
from care.utils.models.base import BaseModel


class HealthFacility(BaseModel, HealthFacilityPermissions):
    hf_id = models.CharField(max_length=50, unique=True)
    registered = models.BooleanField(default=False)
    facility = models.OneToOneField(
        "facility.Facility", on_delete=models.PROTECT, to_field="external_id"
    )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                old_instance = HealthFacility.objects.get(pk=self.pk)
                if old_instance.hf_id != self.hf_id:
                    try:
                        update_hf_id_in_transactions(
                            old_instance.hf_id, self.hf_id, batch_size=1000
                        )
                    except Exception as e:
                        raise ABDMAPIException(
                            detail=f"Failed to update transactions for hf_id change: {e!s}"
                        ) from e
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hf_id} {self.facility}"
