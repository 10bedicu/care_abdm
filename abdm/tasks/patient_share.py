import logging

from celery import shared_task

from abdm.models import Transaction, TransactionType
from abdm.service.helper import ABDMAPIException, uuid
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)


# no transient retry: on-share is correlated by requestId and the gateway may have
# received it before the connection failed; a repeat is rejected with ABDM-2406
@shared_task
def patient_share_on_share(
    on_share_payload: dict, transaction_meta: dict | None = None
):
    try:
        GatewayService.patient_share__on_share(on_share_payload)
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
