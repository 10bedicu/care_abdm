import logging

from celery import shared_task
from django.db.models import Count, F, Q

from abdm.models.abha_number import AbhaNumber
from abdm.models.transaction import Transaction, TransactionStatus, TransactionType
from abdm.service.helper import care_context_dict_from_reference_id, ABDMAPIException
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)

TRANSACTION_BATCH_SIZE = 500
CARE_CONTEXT_BATCH_SIZE = 20

@shared_task
def retry_failed_care_contexts():
    qs = (
        Transaction.objects.filter(
            status__in=[TransactionStatus.INITIATED, TransactionStatus.FAILED],
            type=TransactionType.LINK_CARE_CONTEXT,
        )
        .order_by("id")  # ensures consistent batching
        .values_list("id", flat=True)
    )

    ids = list(qs)

    logger.info(f"Dispatching {len(ids)} transactions for retry")

    for i in range(0, len(ids), TRANSACTION_BATCH_SIZE):
        batch_ids = ids[i : i + TRANSACTION_BATCH_SIZE]

        process_care_context_batch.delay(batch_ids)


@shared_task(bind=True, queue="care_context_queue")
def process_care_context_batch(self, transaction_ids: list[int]):
    filtered_transactions = Transaction.objects.filter(id__in=transaction_ids)

    grouped_transactions = filtered_transactions.values(
        hf_id=F("meta_data__hf_id"),
        abha_number=F("meta_data__abha_number"),
    ).annotate(count=Count("id"))

    for transaction_query in grouped_transactions:
        abha_id = transaction_query["abha_number"]
        abha_number = AbhaNumber.objects.filter(
            Q(abha_number=abha_id) | Q(health_id=abha_id) | Q(external_id=abha_id)
        ).first()
        if not abha_number:
            continue

        patient = abha_number.patient
        if not patient:
            continue

        patients_transactions = filtered_transactions.filter(
            meta_data__hf_id=transaction_query["hf_id"],
            meta_data__abha_number=transaction_query["abha_number"],
        )

        care_contexts = []
        representative_transaction = None
        for transaction in patients_transactions:
            if transaction.meta_data.get("type") != "hip_initiated_linking":
                continue

            representative_transaction = transaction

            for care_context_reference in transaction.meta_data.get(
                "care_contexts", []
            ):
                care_context = care_context_dict_from_reference_id(
                    care_context_reference
                )

                if care_context:
                    care_contexts.append(care_context)

            transaction.status = TransactionStatus.CANCELLED
            transaction.save()

        if len(care_contexts) == 0 or representative_transaction is None:
            continue

        for i in range(0, len(care_contexts), CARE_CONTEXT_BATCH_SIZE):
            batch = care_contexts[i : i + CARE_CONTEXT_BATCH_SIZE]
            try:
                GatewayService.link__carecontext(
                    {
                        "patient": patient,
                        "care_contexts": batch,
                        "user": representative_transaction.created_by,
                        "hf_id": transaction_query["hf_id"],
                    }
                )
            except ABDMAPIException as e:
                logger.warning(
                    "retry care context linking failed transaction_id=%s reference_id=%s detail=%s",
                    representative_transaction.id,
                    representative_transaction.reference_id,
                    e.detail,
                )
                continue
            except Exception as e:
                logger.error(
                    "retry care context linking failed transaction_id=%s reference_id=%s error_type=%s",
                    representative_transaction.id,
                    representative_transaction.reference_id,
                    type(e).__name__,
                    exc_info=True,
                )
                continue
