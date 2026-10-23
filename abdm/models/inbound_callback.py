from django.db import models
from django.utils import timezone

from care.utils.models.base import BaseModel


class CallbackType(models.TextChoices):
    """One value per inbound ABDM gateway callback URL we expose."""

    # HIP
    TOKEN_ON_GENERATE_TOKEN = (
        "token__on_generate_token",
        "hip/token/on-generate-token",
    )
    LINK_ON_CARECONTEXT = ("link__on_carecontext", "link/on_carecontext")
    PATIENT_CARE_CONTEXT_DISCOVER = (
        "patient__care_context__discover",
        "hip/patient/care-context/discover",
    )
    LINK_CARE_CONTEXT_INIT = (
        "link__care_context__init",
        "hip/link/care-context/init",
    )
    LINK_CARE_CONTEXT_CONFIRM = (
        "link__care_context__confirm",
        "hip/link/care-context/confirm",
    )
    CONSENT_REQUEST_HIP_NOTIFY = (
        "consent__request__hip__notify",
        "consent/request/hip/notify",
    )
    HEALTH_INFORMATION_REQUEST = (
        "health_information__request",
        "hip/health-information/request",
    )
    PATIENT_SHARE = ("patient__share", "hip/patient/share")

    # HIU
    CONSENT_REQUEST_ON_INIT = (
        "consent__request__on_init",
        "hiu/consent/request/on-init",
    )
    CONSENT_REQUEST_ON_STATUS = (
        "consent__request__on_status",
        "hiu/consent/request/on-status",
    )
    CONSENT_REQUEST_NOTIFY = (
        "consent__request__notify",
        "hiu/consent/request/notify",
    )
    CONSENT_ON_FETCH = ("consent__on_fetch", "hiu/consent/on-fetch")
    HEALTH_INFORMATION_ON_REQUEST = (
        "health_information__on_request",
        "hiu/health-information/on-request",
    )
    HEALTH_INFORMATION_TRANSFER = (
        "health_information__transfer",
        "hiu/health-information/transfer",
    )


class CallbackStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class InboundCallback(BaseModel):
    # REQUEST-ID header; unique per gateway delivery, used for deduplication
    request_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    callback_type = models.CharField(
        max_length=64,
        choices=CallbackType.choices,
        db_index=True,
    )

    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16,
        choices=CallbackStatus.choices,
        default=CallbackStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["callback_type", "request_id"]),
            models.Index(fields=["status", "callback_type"]),
        ]

    def __str__(self):
        return f"{self.callback_type}:{self.request_id or self.pk}:{self.status}"

    def mark_processing(self):
        self.status = CallbackStatus.PROCESSING
        self.attempts = (self.attempts or 0) + 1
        self.save(update_fields=["status", "attempts", "modified_date"])

    def mark_completed(self):
        self.status = CallbackStatus.COMPLETED
        self.processed_at = timezone.now()
        self.error_message = ""
        self.save(
            update_fields=["status", "processed_at", "error_message", "modified_date"]
        )

    def mark_failed(self, error_message: str):
        self.status = CallbackStatus.FAILED
        self.error_message = (error_message or "")[:8000]
        self.save(update_fields=["status", "error_message", "modified_date"])
