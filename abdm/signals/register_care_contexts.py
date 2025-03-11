import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from abdm.models import HealthInformationType
from abdm.service.helper import ABDMAPIException
from abdm.service.v3.gateway import GatewayService
from care.emr.models.medication_request import MedicationRequest

logger = logging.getLogger(__name__)


@receiver(post_save, sender=MedicationRequest)
def create_care_context_on_medication_request_creation(
    sender, instance: MedicationRequest, created: bool, **kwargs
):
    patient = instance.patient

    if (
        not created
        or not patient
        or getattr(patient, "abha_number", None) is None
        or MedicationRequest.objects.filter(
            encounter=instance.encounter,
            created_date__date=instance.created_date.date(),
        ).count()
        > 1
    ):
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        {
                            "hi_type": HealthInformationType.PRESCRIPTION,
                            "reference": f"v2::medication_request::{instance.created_date.date()}",
                            "display": f"Medication Prescribed on {instance.created_date.date()}",
                        }
                    ],
                    "user": instance.created_by,
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for medication request {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for medication request {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)
