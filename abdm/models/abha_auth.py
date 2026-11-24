from django.db import models
from django.utils import timezone

from care.users.models import User
from care.utils.models.base import BaseModel


class AbhaAuthMethod(models.TextChoices):
    SCAN_AND_SHARE = "scan_and_share"
    STAFF_VERIFICATION = "staff_verification"
    ABHA_CREATION = "abha_creation"
    LEGACY = "legacy"


class AbhaAuthInvalidationReason(models.TextChoices):
    ADDRESS_MOVED = "address_moved"
    LINK_TOKEN_REFUSED = "link_token_refused"


def normalize_abha_address(abha_address: str | None) -> str:
    return (abha_address or "").strip().lower()


class AbhaAuth(BaseModel):
    """One ABHA authentication at a health facility, with the address presented.

    A row is never overwritten: only its link token and its invalidation change.
    """

    MUTABLE_FIELDS = frozenset(
        {
            "link_token",
            "link_token_expires_at",
            "invalidated_at",
            "invalidation_reason",
            "modified_date",
            "deleted",
        }
    )

    abha_number = models.ForeignKey(
        "abdm.AbhaNumber", on_delete=models.PROTECT, related_name="auths"
    )
    health_facility = models.ForeignKey(
        "abdm.HealthFacility", on_delete=models.PROTECT, related_name="abha_auths"
    )
    abha_address = models.CharField(max_length=255, db_index=True)
    method = models.CharField(max_length=30, choices=AbhaAuthMethod.choices)
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )

    link_token = models.TextField(null=True, blank=True)
    link_token_expires_at = models.DateTimeField(null=True, blank=True)

    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.CharField(max_length=50, blank=True, default="")

    def __str__(self):
        return f"{self.pk} {self.method}"

    @property
    def is_valid(self) -> bool:
        return self.invalidated_at is None

    @property
    def has_usable_link_token(self) -> bool:
        return bool(
            self.link_token
            and self.link_token_expires_at
            and self.link_token_expires_at > timezone.now()
        )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or set(update_fields) - self.MUTABLE_FIELDS:
                raise ValueError(
                    "An ABHA auth is immutable; only its link token and "
                    "invalidation can be updated"
                )

        self.abha_address = normalize_abha_address(self.abha_address)
        return super().save(*args, **kwargs)
