from datetime import UTC, datetime, time, timedelta

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from abdm.extensions import keep_recorded_choice, linking_opted_out, stamp_opt_out
from abdm.models import CareContext, CareContextStatus
from abdm.service.helper import (
    LINKABLE_DIAGNOSTIC_REPORT_STATUS,
    LINKABLE_ENCOUNTER_STATUSES,
    SHAREABLE_IMMUNIZATION_STATUSES,
    UNLINKABLE_MEDICATION_REQUEST_STATUS,
    create_diagnostic_report_care_context,
    create_encounter_care_context,
    create_file_upload_care_context,
    create_immunization_care_context,
    create_invoice_care_context,
    create_medication_request_care_context,
    create_questionnaire_response_care_context,
    encounter_class_hi_type,
    encounter_hi_type,
    hf_id_from_encounter,
)
from abdm.tasks.link_care_context import (
    enqueue_link_care_context,
    enqueue_notify_care_context_update,
    enqueue_sync_encounter_record_type,
)
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.immunisation import Immunization
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.file_upload.spec import FileTypeChoices
from care.emr.resources.immunisation.immunization import ImmunizationStatus
from care.emr.resources.invoice.spec import InvoiceStatusOptions

# These receivers only say that a record became ready to link. Whether it is
# linked, and to which ABHA address, is decided by the linking routine.


def _previous_status(sender, instance):
    return sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()


@receiver(pre_save, sender=MedicationRequest)
def note_medication_request_status_before_save(
    sender, instance: MedicationRequest, **kwargs
):
    instance.abdm_was_unlinkable = (
        instance.status != UNLINKABLE_MEDICATION_REQUEST_STATUS
        and (
            not instance.pk
            or _previous_status(sender, instance)
            == UNLINKABLE_MEDICATION_REQUEST_STATUS
        )
    )


@receiver(post_save, sender=MedicationRequest)
def create_care_context_on_medication_request_creation(
    sender, instance: MedicationRequest, created: bool, **kwargs
):
    patient = instance.patient
    hf_id = hf_id_from_encounter(instance.encounter)

    if (
        instance.status == UNLINKABLE_MEDICATION_REQUEST_STATUS
        or not getattr(instance, "abdm_was_unlinkable", created)
        or not hf_id
        or not patient
    ):
        return

    # a prescription is linked by the first medication request of the day that
    # is not a draft, whether it was created that way or left draft later. The
    # day is the one the care context reference names, which is in UTC
    day = datetime.combine(instance.created_date.astimezone(UTC).date(), time.min, UTC)
    is_first_of_the_day = not (
        MedicationRequest.objects.filter(
            encounter=instance.encounter,
            created_date__gte=day,
            created_date__lt=day + timedelta(days=1),
        )
        .exclude(status=UNLINKABLE_MEDICATION_REQUEST_STATUS)
        .exclude(pk=instance.pk)
        .exists()
    )
    care_context = create_medication_request_care_context(instance)

    # the prescription of the day may be linked already, by an earlier request
    # of this encounter or by another encounter of the patient here: ABDM is
    # then told that it has gained a record
    if not linking_opted_out(instance.encounter.extensions) and (
        CareContext.objects.filter(
            health_facility__hf_id=hf_id,
            patient=patient,
            reference=care_context["reference"],
            status=CareContextStatus.LINKED,
        ).exists()
    ):
        transaction.on_commit(
            lambda: enqueue_notify_care_context_update(
                patient_external_id=str(patient.external_id),
                reference=care_context["reference"],
                hf_id=hf_id,
            )
        )

    if not is_first_of_the_day:
        return

    instance.abdm_was_unlinkable = False

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=care_context,
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


def _link_encounter_on_commit(instance: Encounter):
    patient = instance.patient
    hf_id = hf_id_from_encounter(instance)

    if not patient or not hf_id:
        return

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_encounter_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


@receiver(pre_save, sender=Encounter)
def note_encounter_changes_before_save(
    sender, instance: Encounter, update_fields=None, **kwargs
):
    instance.abdm_record_type_changed = False

    if not instance.pk:
        # the choice to keep a visit out of ABDM is made as the encounter is created
        stamp_opt_out(instance)
        return

    def is_saved(field):
        return update_fields is None or field in update_fields

    if not (
        is_saved("status") or is_saved("encounter_class") or is_saved("extensions")
    ):
        return

    previous = (
        sender.objects.filter(pk=instance.pk)
        .values("status", "encounter_class", "extensions")
        .first()
    )
    if previous is None:
        return

    if is_saved("extensions"):
        keep_recorded_choice(instance, previous["extensions"])

    if is_saved("encounter_class"):
        instance.abdm_record_type_changed = encounter_class_hi_type(
            previous["encounter_class"]
        ) != encounter_hi_type(instance)

    # an encounter is linked when it ends: by then its class, which decides the
    # record type, is settled
    if (
        is_saved("status")
        and instance.status in LINKABLE_ENCOUNTER_STATUSES
        and previous["status"] not in LINKABLE_ENCOUNTER_STATUSES
    ):
        _link_encounter_on_commit(instance)


@receiver(post_save, sender=Encounter)
def act_on_encounter_changes_after_save(
    sender, instance: Encounter, created: bool, **kwargs
):
    record_type_changed = getattr(instance, "abdm_record_type_changed", False)
    instance.abdm_record_type_changed = False

    # an encounter that is created already over is ready as it is created
    if created and instance.status in LINKABLE_ENCOUNTER_STATUSES:
        _link_encounter_on_commit(instance)

    # a linked encounter is shared as the record type of its class, and ABDM is
    # told when that is no longer the type it was linked as
    if record_type_changed and hf_id_from_encounter(instance):
        transaction.on_commit(
            lambda: enqueue_sync_encounter_record_type(str(instance.external_id))
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

    if not patient or not hf_id:
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

    if not created or not hf_id or not patient:
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


@receiver(pre_save, sender=DiagnosticReport)
def create_care_context_on_diagnostic_report_final(
    sender, instance: DiagnosticReport, update_fields=None, **kwargs
):
    if instance.status != LINKABLE_DIAGNOSTIC_REPORT_STATUS or (
        update_fields is not None and "status" not in update_fields
    ):
        return

    if (
        instance.pk
        and _previous_status(sender, instance) == LINKABLE_DIAGNOSTIC_REPORT_STATUS
    ):
        return

    patient = instance.patient
    hf_id = hf_id_from_encounter(instance.encounter)

    if not patient or not hf_id:
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

    if not patient or not hf_id:
        return

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_invoice_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )


@receiver(pre_save, sender=Immunization)
def create_care_context_on_immunization_record(
    sender, instance: Immunization, **kwargs
):
    if instance.status not in SHAREABLE_IMMUNIZATION_STATUSES:
        return

    # Link only when the record first becomes shareable: on creation, or when a draft is finalised. Later edits keep the care context linked earlier.
    if instance.pk:
        previous_status = (
            sender.objects.filter(pk=instance.pk)
            .values_list("status", flat=True)
            .first()
        )
        if previous_status not in (None, ImmunizationStatus.draft.value):
            return

    patient = instance.patient
    hf_id = hf_id_from_encounter(instance.encounter)

    if not patient or not hf_id:
        return

    transaction.on_commit(
        lambda: enqueue_link_care_context(
            patient_external_id=str(patient.external_id),
            care_context=create_immunization_care_context(instance),
            hf_id=hf_id,
            user_id=instance.created_by_id,
        )
    )
