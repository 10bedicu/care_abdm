import logging

from celery import shared_task
from django.db.models import Q

from abdm.models.abha_number import AbhaNumber
from abdm.models.transaction import Transaction, TransactionStatus, TransactionType
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)


@shared_task
def retry_failed_care_contexts():
    unsuccessfull_care_context_transactions = Transaction.objects.filter(
        ~Q(status=TransactionStatus.COMPLETED),
        type=TransactionType.LINK_CARE_CONTEXT,
    )

    for transaction in unsuccessfull_care_context_transactions:
        if transaction.meta_data.get("type") != "hip_initiated_linking":
            continue

        abha_id = transaction.meta_data.get("abha_number")
        abha_number = AbhaNumber.objects.filter(
            Q(abha_number=abha_id) | Q(health_id=abha_id) | Q(external_id=abha_id)
        ).first()
        if not abha_number:
            continue

        patient = abha_number.patient
        if not patient:
            continue

        try:
            GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": transaction.meta_data.get("care_contexts"),
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
