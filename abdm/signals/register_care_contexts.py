import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from abdm.models import HealthInformationType
from abdm.service.helper import ABDMAPIException, hf_id_from_abha_id
from abdm.service.v3.gateway import GatewayService
from care.facility.models import (
    DailyRound,
    InvestigationValue,
    PatientConsultation,
    Prescription,
    SuggestionChoices,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PatientConsultation)
def create_care_context_on_consultation_creation(
    sender, instance: PatientConsultation, created: bool, **kwargs
):
    patient = instance.patient

    if not created or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        {
                            "hi_type": (
                                HealthInformationType.DISCHARGE_SUMMARY
                                if instance.suggestion == SuggestionChoices.A
                                else HealthInformationType.OP_CONSULTATION
                            ),
                            "reference": f"v1::consultation::{instance.external_id}",
                            "display": f"Encounter on {instance.created_date.date()}",
                        }
                    ],
                    "user": instance.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        # TODO: send a notification to the consultation.created_by to manually link the care_context
        logger.warning(
            f"Failed to link care context for consultation {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        )

    except Exception as e:
        logger.exception(
            f"Failed to link care context for consultation {instance.external_id} with patient {patient.external_id}, {e!s}"
        )


# using investigation value over investigation session because of the values are created after session which makes consultation inaccessible
@receiver(post_save, sender=InvestigationValue)
def create_care_context_on_investigation_creation(
    sender, instance: InvestigationValue, created: bool, **kwargs
):
    patient = instance.consultation.patient
    investigation_values = InvestigationValue.objects.filter(session=instance.session)

    if (
        not patient
        or getattr(patient, "abha_number", None) is None
        or len(investigation_values) > 1
    ):
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        {
                            "hi_type": HealthInformationType.DIAGNOSTIC_REPORT,
                            "reference": f"v1::investigation_session::{instance.session.external_id}",
                            "display": f"Investigation on {instance.session.created_date.date()}",
                        }
                    ],
                    "user": instance.session.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        logger.warning(
            f"Failed to link care context for investigation {instance.session.external_id} with patient {patient.external_id}, {e.detail!s}"
        )

    except Exception as e:
        logger.exception(
            f"Failed to link care context for investigation {instance.session.external_id} with patient {patient.external_id}, {e!s}"
        )


@receiver(post_save, sender=DailyRound)
def create_care_context_on_daily_round_creation(
    sender, instance: DailyRound, created: bool, **kwargs
):
    patient = instance.consultation.patient

    if not created or not patient or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        {
                            "hi_type": HealthInformationType.WELLNESS_RECORD,
                            "reference": f"v1::daily_round::{instance.external_id}",
                            "display": f"Daily Round on {instance.created_date.date()}",
                        }
                    ],
                    "user": instance.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        logger.warning(
            f"Failed to link care context for daily round {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        )

    except Exception as e:
        logger.exception(
            f"Failed to link care context for daily round {instance.external_id} with patient {patient.external_id}, {e!s}"
        )


@receiver(post_save, sender=Prescription)
def create_care_context_on_prescription_creation(
    sender, instance: Prescription, created: bool, **kwargs
):
    patient = instance.consultation.patient

    if (
        not created
        or not patient
        or getattr(patient, "abha_number", None) is None
        or Prescription.objects.filter(
            consultation=instance.consultation,
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
                            "reference": f"v1::prescription::{instance.created_date.date()}",
                            "display": f"Medication Prescribed on {instance.created_date.date()}",
                        }
                    ],
                    "user": instance.prescribed_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        logger.warning(
            f"Failed to link care context for prescription {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        )

    except Exception as e:
        logger.exception(
            f"Failed to link care context for prescription {instance.external_id} with patient {patient.external_id}, {e!s}"
        )
