import logging

import requests
from celery import shared_task

from abdm.models.transaction import Transaction, TransactionStatus
from abdm.service.v3.gateway import GatewayService
from care.emr.models.patient import Patient
from care.users.models import User

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_COUNTDOWN = 30


def _mark_failed(reference_id: str) -> None:
    Transaction.objects.filter(reference_id=reference_id).update(
        status=TransactionStatus.FAILED
    )


@shared_task(bind=True, max_retries=MAX_RETRIES)
def link_care_context(
    self,
    reference_id: str,
    patient_id: int,
    care_contexts: list[dict],
    hf_id: str,
    user_id: int | None = None,
):
    """Link care contexts with ABDM outside the request/response cycle.

    `reference_id` is generated once at dispatch and reused on every retry, so the
    `Transaction.objects.update_or_create` in `GatewayService.link__carecontext`
    updates one row instead of minting a new one per attempt.
    """
    patient = Patient.objects.filter(id=patient_id).first()
    if not patient:
        logger.warning(
            "Skipping care context linking, patient %s no longer exists", patient_id
        )
        return

    try:
        GatewayService.link__carecontext(
            {
                "reference_id": reference_id,
                "patient": patient,
                "care_contexts": care_contexts,
                "user": User.objects.filter(id=user_id).first() if user_id else None,
                "hf_id": hf_id,
            }
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        if self.request.retries >= MAX_RETRIES:
            _mark_failed(reference_id)
            logger.warning(
                "Care context linking for patient %s gave up after %s attempts",
                patient.external_id,
                MAX_RETRIES + 1,
            )
            return
        raise self.retry(exc=exc, countdown=RETRY_COUNTDOWN) from exc
    except Exception:
        # ponytail: an ABDM-side rejection is marked FAILED and left for the nightly
        # retry_failed_care_contexts sweep rather than retried here -- hammering a
        # gateway that is already returning errors does not help. Split retryable
        # (5xx) from permanent (validation) if the sweep proves too slow.
        _mark_failed(reference_id)
        logger.exception(
            "Failed to link care context for patient %s", patient.external_id
        )
