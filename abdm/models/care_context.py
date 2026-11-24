from django.db import models

from abdm.models.base import HealthInformationType
from care.utils.models.base import BaseModel


class CareContextStatus(models.TextChoices):
    PENDING = "pending"
    REQUESTED = "requested"
    LINKED = "linked"
    FAILED = "failed"


class CareContextVia(models.TextChoices):
    HIP_INITIATED = "hip_initiated"
    USER_INITIATED = "user_initiated"


class CareContext(BaseModel):
    """Ledger of the care contexts linked, or being linked, at a health facility."""

    health_facility = models.ForeignKey("abdm.HealthFacility", on_delete=models.PROTECT)
    patient = models.ForeignKey("emr.Patient", on_delete=models.PROTECT)
    abha_address = models.CharField(max_length=255)
    abha_auth = models.ForeignKey(
        "abdm.AbhaAuth",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="care_contexts",
    )

    reference = models.CharField(max_length=255)
    hi_type = models.CharField(max_length=50, choices=HealthInformationType.choices)

    status = models.CharField(
        max_length=20,
        choices=CareContextStatus.choices,
        default=CareContextStatus.PENDING,
    )
    via = models.CharField(
        max_length=20,
        choices=CareContextVia.choices,
        default=CareContextVia.HIP_INITIATED,
    )
    # REQUEST-ID of the link call, matched by the on_carecontext callback
    request_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    legacy = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["health_facility", "abha_address", "reference"],
                name="abdm_carecontext_unique_link",
            )
        ]

    def __str__(self):
        return f"{self.reference} {self.status}"
