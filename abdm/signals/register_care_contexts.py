import logging

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from abdm.service.helper import (
    ABDMAPIException,
    create_encounter_care_context,
    create_file_upload_care_context,
    create_medication_request_care_context,
    create_questionnaire_response_care_context,
    hf_id_from_abha_id,
)
from abdm.service.v3.gateway import GatewayService
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.observation import Observation
from care.emr.resources.file_upload.spec import FileTypeChoices

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
                    "care_contexts": [create_medication_request_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for medication request {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for medication request {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)


@receiver(post_save, sender=Encounter)
def create_care_context_on_encounter_creation(
    sender, instance: Encounter, created: bool, **kwargs
):
    patient = instance.patient

    if not created or not patient or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_encounter_care_context(instance)],
                    "user": instance.created_by,
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for encounter {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for encounter {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)


@receiver(pre_save, sender=FileUpload)
def create_care_context_on_file_upload_creation(sender, instance: FileUpload, **kwargs):
    if not instance.pk:
        return

    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    if (
        old_instance.upload_completed is True
        or instance.upload_completed is False
        or instance.file_type != FileTypeChoices.encounter
    ):
        return

    encounter = Encounter.objects.filter(external_id=instance.associating_id).first()
    patient = getattr(encounter, "patient", None)

    if not patient or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_file_upload_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for file upload {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for file upload {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)


@receiver(post_save, sender=Observation)
def create_care_context_on_questionnaire_response_creation(
    sender, instance: Observation, created: bool, **kwargs
):
    patient = instance.questionnaire_response.patient
    observations = Observation.objects.filter(
        questionnaire_response=instance.questionnaire_response
    ).filter(
        Q(main_code__isnull=False) & ~Q(main_code={})
        | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
    )

    if (
        not created
        or not patient
        or getattr(patient, "abha_number", None) is None
        or len(observations) != 1
    ):
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        create_questionnaire_response_care_context(
                            instance.questionnaire_response
                        )
                    ],
                    "user": instance.created_by,
                    "hf_id": hf_id_from_abha_id(patient.abha_number.abha_number),
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for questionnaire response {instance.questionnaire_response.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for questionnaire response {instance.questionnaire_response.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)
