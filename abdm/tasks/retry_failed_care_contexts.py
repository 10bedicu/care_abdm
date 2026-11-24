import logging

from celery import shared_task

from abdm.models import AbhaAuth
from abdm.service.linking import requeue_care_contexts, send_pending_care_contexts
from abdm.utils.user import get_or_create_abdm_user

logger = logging.getLogger(__name__)

AUTH_BATCH_SIZE = 100


@shared_task
def retry_failed_care_contexts():
    auth_ids = requeue_care_contexts()

    logger.info(
        "Dispatching %s ABHA auths with pending care contexts", len(auth_ids)
    )

    for i in range(0, len(auth_ids), AUTH_BATCH_SIZE):
        send_pending_care_contexts_batch.delay(auth_ids[i : i + AUTH_BATCH_SIZE])


@shared_task(bind=True, queue="care_context_queue")
def send_pending_care_contexts_batch(self, auth_ids: list[int]):
    for auth in AbhaAuth.objects.filter(id__in=auth_ids).select_related(
        "abha_number", "health_facility"
    ):
        try:
            send_pending_care_contexts(auth, user=get_or_create_abdm_user())
        except Exception:
            logger.exception(
                "Error while sending the pending care contexts of ABHA auth %s",
                auth.external_id,
            )
