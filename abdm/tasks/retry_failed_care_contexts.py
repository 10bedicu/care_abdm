import logging

from celery import shared_task
from django.db.models import Count, F, Q

from abdm.models.abha_number import AbhaNumber
from abdm.models.transaction import Transaction, TransactionStatus, TransactionType
from abdm.service.helper import care_context_dict_from_reference_id
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)


CARE_CONTEXT_BATCH_SIZE = 50


@shared_task
def retry_failed_care_contexts():
    filtered_transactions = Transaction.objects.filter(
        status__in=[TransactionStatus.INITIATED, TransactionStatus.FAILED],
        type=TransactionType.LINK_CARE_CONTEXT,
    )

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
        for transaction in patients_transactions:
            if transaction.meta_data.get("type") != "hip_initiated_linking":
                continue

            for care_context_reference in transaction.meta_data.get("care_contexts"):
                care_context = care_context_dict_from_reference_id(
                    care_context_reference
                )

                if care_context:
                    care_contexts.append(care_context)

            transaction.status = TransactionStatus.CANCELLED
            transaction.save()

        if len(care_contexts) == 0:
            continue

        for i in range(0, len(care_contexts), CARE_CONTEXT_BATCH_SIZE):
            batch = care_contexts[i : i + CARE_CONTEXT_BATCH_SIZE]
            try:
                GatewayService.link__carecontext(
                    {
                        "patient": patient,
                        "care_contexts": batch,
                        "user": transaction.created_by,
                        "hf_id": transaction.meta_data.get("hf_id"),
                    }
                )
            except Exception as e:
                logger.exception(
                    "Error while retrying care context linking for transaction %s with error %s",
                    transaction.id,
                    str(e),
                )
                continue
