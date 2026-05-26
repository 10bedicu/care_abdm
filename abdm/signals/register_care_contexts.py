import logging

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from abdm.service.helper import (
    ABDMAPIException,
    create_diagnostic_report_care_context,
    create_encounter_care_context,
    create_file_upload_care_context,
    create_invoice_care_context,
    create_medication_request_care_context,
    create_questionnaire_response_care_context,
    hf_id_from_encounter,
)
from abdm.service.v3.gateway import GatewayService
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.observation import Observation
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.file_upload.spec import FileTypeChoices
from care.emr.resources.invoice.spec import InvoiceStatusOptions

logger = logging.getLogger(__name__)


@receiver(post_save, sender=MedicationRequest)
def create_care_context_on_medication_request_creation(
    sender, instance: MedicationRequest, created: bool, **kwargs
):
    patient = instance.patient
    hf_id = hf_id_from_encounter(instance.encounter)

    if (
        not created
        or not hf_id
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
                    "hf_id": hf_id,
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
    hf_id = hf_id_from_encounter(instance)

    if (
        not created
        or not hf_id
        or not patient
        or getattr(patient, "abha_number", None) is None
    ):
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_encounter_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id,
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
    hf_id = hf_id_from_encounter(encounter)

    if not patient or not hf_id or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_file_upload_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id,
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for file upload {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for file upload {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)


@receiver(post_save, sender=QuestionnaireResponse)
def create_care_context_on_questionnaire_response_creation(
    sender, instance: QuestionnaireResponse, created: bool, **kwargs
):
    patient = instance.patient
    hf_id = hf_id_from_encounter(instance.encounter)

    if (
        not created
        or not hf_id
        or not patient
        or getattr(patient, "abha_number", None) is None
    ):
        return

    # Observations are bulk_created after this signal fires, so the check is deferred to on_commit when the related rows are visible in the DB.
    def link_if_has_coded_observations():
        has_coded_observation = (
            Observation.objects.filter(questionnaire_response=instance)
            .filter(
                Q(main_code__isnull=False) & ~Q(main_code={})
                | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
            )
            .exists()
        )

        if not has_coded_observation:
            return

        try:
            GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [
                        create_questionnaire_response_care_context(instance)
                    ],
                    "user": instance.created_by,
                    "hf_id": hf_id,
                }
            )
        except ABDMAPIException as e:
            warning = f"Failed to link care context for questionnaire response {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
            logger.warning(warning)
        except Exception as e:
            warning = f"Failed to link care context for questionnaire response {instance.external_id} with patient {patient.external_id}, {e!s}"
            logger.exception(warning)

    transaction.on_commit(link_if_has_coded_observations)


@receiver(post_save, sender=DiagnosticReport)
def create_care_context_on_diagnostic_report_creation(
    sender, instance: DiagnosticReport, created: bool, **kwargs
):
    patient = instance.patient
    hf_id = hf_id_from_encounter(instance)

    if (
        not created
        or not hf_id
        or not patient
        or getattr(patient, "abha_number", None) is None
    ):
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_diagnostic_report_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id,
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for diagnostic report {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for diagnostic report {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)


@receiver(pre_save, sender=Invoice)
def create_care_context_on_invoice_issue(sender, instance: Invoice, **kwargs):
    if instance.status != InvoiceStatusOptions.issued.value:
        return

    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            old_instance = None

        if old_instance and old_instance.status == InvoiceStatusOptions.issued.value:
            return

    patient = instance.patient
    hf_id = hf_id_from_encounter(instance)

    if not patient or not hf_id or getattr(patient, "abha_number", None) is None:
        return

    try:
        transaction.on_commit(
            lambda: GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": [create_invoice_care_context(instance)],
                    "user": instance.created_by,
                    "hf_id": hf_id,
                }
            )
        )
    except ABDMAPIException as e:
        warning = f"Failed to link care context for invoice {instance.external_id} with patient {patient.external_id}, {e.detail!s}"
        logger.warning(warning)

    except Exception as e:
        warning = f"Failed to link care context for invoice {instance.external_id} with patient {patient.external_id}, {e!s}"
        logger.exception(warning)
