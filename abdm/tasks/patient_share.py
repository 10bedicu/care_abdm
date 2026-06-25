import logging

import requests
from celery import shared_task

from abdm.models import Transaction, TransactionType
from abdm.service.helper import ABDMAPIException, uuid
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_COUNTDOWN = 30


@shared_task(bind=True, max_retries=MAX_RETRIES)
def patient_share_on_share(self, on_share_payload: dict, transaction_meta: dict | None = None):
    try:
        GatewayService.patient_share__on_share(on_share_payload)
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.warning(
            "patient_share on_share transient failure for request %s (attempt %s/%s)",
            on_share_payload.get("request_id"),
            self.request.retries + 1,
            MAX_RETRIES + 1,
        )
        raise self.retry(exc=exc, countdown=RETRY_COUNTDOWN) from exc
    except ABDMAPIException:
        logger.exception(
            "patient_share on_share failed for request %s",
            on_share_payload.get("request_id"),
        )
        raise

    if transaction_meta:
        Transaction.objects.create(
            reference_id=uuid(),
            type=TransactionType.SCAN_AND_SHARE,
            meta_data=transaction_meta,
        )
