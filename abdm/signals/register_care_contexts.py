from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from abdm.service.helper import (
    create_diagnostic_report_care_context,
    create_encounter_care_context,
    create_file_upload_care_context,
    create_invoice_care_context,
    create_medication_request_care_context,
    create_questionnaire_response_care_context,
    hf_id_from_encounter,
)
from abdm.tasks.link_care_context import enqueue_link_care_context
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.file_upload.spec import FileTypeChoices
from care.emr.resources.invoice.spec import InvoiceStatusOptions


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

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_medication_request_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


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

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_encounter_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


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

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_file_upload_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


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

    # Observations are bulk_created after this signal fires; the task checks for
    # coded observations once the transaction is committed and the rows are visible.
    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_questionnaire_response_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
            questionnaire_response_external_id=str(instance.external_id),
        )
    )


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

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_diagnostic_report_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


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

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_invoice_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )
