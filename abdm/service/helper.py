import re
from base64 import b64decode, b64encode
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple
from uuid import uuid4

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import TruncDate
from rest_framework.exceptions import APIException

from abdm.extensions import linking_opted_out
from abdm.models.base import HealthInformationType
from abdm.service.request import Request
from abdm.settings import plugin_settings as settings
from care.emr.models.charge_item import ChargeItem
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.immunisation import Immunization
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.observation import Observation
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.diagnostic_report.spec import DiagnosticReportStatusChoices
from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.emr.resources.file_upload.spec import FileTypeChoices
from care.emr.resources.immunisation.immunization import ImmunizationStatus
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.medication.request.spec import MedicationRequestStatus
from care.emr.resources.questionnaire_response.spec import (
    QuestionnaireResponseStatusChoices,
)


SHAREABLE_IMMUNIZATION_STATUSES = (
    ImmunizationStatus.completed.value,
    ImmunizationStatus.not_done.value,
)

# an encounter is linked once it is over, when its record type is settled
LINKABLE_ENCOUNTER_STATUSES = (
    StatusChoices.completed.value,
    StatusChoices.discharged.value,
)
LINKABLE_DIAGNOSTIC_REPORT_STATUS = DiagnosticReportStatusChoices.final.value
UNLINKABLE_MEDICATION_REQUEST_STATUS = MedicationRequestStatus.draft.value

# an encounter of one of these classes is shared as a discharge summary, any
# other as an OP consultation
ADMISSION_ENCOUNTER_CLASSES = (
    ClassChoices.imp.value,
    ClassChoices.emer.value,
    ClassChoices.obsenc.value,
)


class ABDMAPIException(APIException):
    status_code = 400
    default_code = "ABDM_ERROR"
    default_detail = "An error occured while trying to communicate with ABDM"


class ABDMInternalException(APIException):
    status_code = 400
    default_code = "ABDM_INTERNAL_ERROR"
    default_detail = "An internal error occured while trying to communicate with ABDM"


def timestamp():
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uuid():
    return str(uuid4())


def encrypt_message(message: str):
    rsa_public_key = RSA.importKey(
        b64decode(
            Request(settings.ABDM_ABHA_URL)
            .get(
                "/v3/profile/public/certificate",
                None,
                {"TIMESTAMP": timestamp(), "REQUEST-ID": uuid()},
            )
            .json()
            .get("publicKey", "")
        )
    )

    cipher = PKCS1_OAEP.new(rsa_public_key, hashAlgo=SHA1)
    encrypted_message = cipher.encrypt(message.encode())

    return b64encode(encrypted_message).decode()


def hf_id_from_encounter(encounter: Encounter):
    if not encounter or not hasattr(encounter, "facility"):
        return None

    facility = encounter.facility

    if not hasattr(facility, "healthfacility"):
        return None

    return facility.healthfacility.hf_id


def cm_id():
    return settings.ABDM_CM_ID


def benefit_name():
    return settings.ABDM_BENEFIT_NAME


def validate_and_format_date(year, month, day):
    if not year:
        raise ABDMAPIException(detail="Year is required")

    year = int(year)
    min_year = 1800
    current_year = datetime.now().year  # noqa: DTZ005
    if year < min_year or year > current_year:
        raise ABDMAPIException(detail="Year must be between 1800 and current year")

    month = 0 if month is None else int(month)
    day = 0 if day is None else int(day)

    if not 0 <= month <= 12:
        raise ABDMAPIException(detail="Month must be between 0 and 12")
    if not 0 <= day <= 31:
        raise ABDMAPIException(detail="Day must be between 0 and 31")

    return f"{year}-{month:02d}-{day:02d}"


def ready_care_contexts_for_encounter(encounter: Encounter):
    """Care contexts of an encounter whose records are ready to be linked."""
    care_contexts = []

    if encounter.status in LINKABLE_ENCOUNTER_STATUSES:
        care_contexts.append(create_encounter_care_context(encounter))

    medication_requests = (
        MedicationRequest.objects.filter(
            patient_id=encounter.patient_id, encounter_id=encounter.id
        )
        .exclude(status=UNLINKABLE_MEDICATION_REQUEST_STATUS)
        .annotate(day=TruncDate("created_date"))
        .order_by("day")
        .distinct("day")
    )
    for request in medication_requests:
        care_contexts.append(create_medication_request_care_context(request))

    files = FileUpload.objects.filter(
        associating_id=encounter.external_id,
        file_type=FileTypeChoices.encounter,
        upload_completed=True,
    )
    for file in files:
        care_contexts.append(create_file_upload_care_context(file))

    questionnaire_responses = QuestionnaireResponse.objects.filter(
        encounter_id=encounter.id,
        patient_id=encounter.patient_id,
    ).exclude(status=QuestionnaireResponseStatusChoices.entered_in_error.value)
    for response in questionnaire_responses:
        observations = Observation.objects.filter(
            questionnaire_response_id=response.id
        ).filter(
            Q(main_code__isnull=False) & ~Q(main_code={})
            | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
        )

        if observations.exists():
            care_contexts.append(create_questionnaire_response_care_context(response))

    diagnostic_reports = DiagnosticReport.objects.filter(
        encounter_id=encounter.id,
        patient_id=encounter.patient_id,
        status=LINKABLE_DIAGNOSTIC_REPORT_STATUS,
    )
    for report in diagnostic_reports:
        care_contexts.append(create_diagnostic_report_care_context(report))

    invoices = (
        Invoice.objects.filter(
            account__primary_encounter_id=encounter.id,
            patient_id=encounter.patient_id,
        )
        .exclude(status=InvoiceStatusOptions.draft.value)
        .select_related("account")
    )
    for invoice in invoices:
        if not _is_invoice_opted_out(invoice):
            care_contexts.append(create_invoice_care_context(invoice))

    immunizations = Immunization.objects.filter(
        encounter_id=encounter.id,
        patient_id=encounter.patient_id,
        status__in=SHAREABLE_IMMUNIZATION_STATUSES,
    )
    for immunization in immunizations:
        care_contexts.append(create_immunization_care_context(immunization))

    return care_contexts


class CareContextRecord(NamedTuple):
    # whether the opt-out of the encounters behind the record keeps it out of ABDM
    opted_out: bool


def _is_invoice_opted_out(invoice: Invoice) -> bool:
    """Whether an invoice bills an encounter that was opted out of linking.

    An invoice can carry the charges of several encounters. It is one document,
    so a charge of an opted-out encounter keeps the whole of it out of ABDM.
    """
    encounters = Encounter.objects.filter(
        Q(id=invoice.account.primary_encounter_id if invoice.account_id else None)
        | Q(
            id__in=ChargeItem.objects.filter(id__in=invoice.charge_items).values(
                "encounter_id"
            )
        )
    )
    return any(
        linking_opted_out(extensions)
        for extensions in encounters.values_list("extensions", flat=True)
    )


def resolve_care_context(reference: str, patient, facility) -> CareContextRecord | None:  # noqa: PLR0911
    """The record behind a care context reference, when it is a record of that
    patient at that facility. None for anything else."""
    parts = (reference or "").split("::")

    if not patient or not facility or len(parts) != 3 or parts[0] != "v2":  # noqa: PLR2004
        return None

    _, model, param = parts
    at_facility = {"patient": patient, "encounter__facility": facility}

    try:
        if model == "prescription":
            # the reference names the UTC day of the request, while the records
            # shared for it are picked by the day in the local timezone
            day = datetime.combine(date.fromisoformat(param), time.min, UTC)
            encounter_extensions = list(
                MedicationRequest.objects.filter(
                    Q(created_date__date=param)
                    | Q(created_date__gte=day, created_date__lt=day + timedelta(days=1)),
                    **at_facility,
                )
                .exclude(status=UNLINKABLE_MEDICATION_REQUEST_STATUS)
                .values_list("encounter__extensions", flat=True)
            )
            if not encounter_extensions:
                return None
            # a prescription covers every request of the day, across encounters:
            # it is kept out only when all of them are of opted-out encounters
            return CareContextRecord(
                all(linking_opted_out(extensions) for extensions in encounter_extensions)
            )

        if model == "encounter":
            encounter = Encounter.objects.filter(
                external_id=param, patient=patient, facility=facility
            ).first()
            return (
                CareContextRecord(linking_opted_out(encounter.extensions))
                if encounter
                else None
            )

        if model == "file_upload":
            file_upload = FileUpload.objects.filter(
                external_id=param, file_type=FileTypeChoices.encounter
            ).first()
            encounter = file_upload and Encounter.objects.filter(
                external_id=file_upload.associating_id,
                patient=patient,
                facility=facility,
            ).first()
            return (
                CareContextRecord(linking_opted_out(encounter.extensions))
                if encounter
                else None
            )

        if model == "invoice":
            invoice = (
                Invoice.objects.filter(
                    external_id=param, patient=patient, facility=facility
                )
                .select_related("account")
                .first()
            )
            return CareContextRecord(_is_invoice_opted_out(invoice)) if invoice else None

        records = {
            "questionnaire_response": QuestionnaireResponse.objects.exclude(
                status=QuestionnaireResponseStatusChoices.entered_in_error.value
            ),
            "diagnostic_report": DiagnosticReport.objects.all(),
            "immunization": Immunization.objects.filter(
                status__in=SHAREABLE_IMMUNIZATION_STATUSES
            ),
        }.get(model)
        if records is None:
            return None

        record = (
            records.filter(external_id=param, **at_facility)
            .select_related("encounter")
            .first()
        )
        return (
            CareContextRecord(linking_opted_out(record.encounter.extensions))
            if record
            else None
        )
    except (ValidationError, ValueError):
        # a reference that carries something other than the id or date expected
        return None


def care_context_dict_from_reference_id(reference_id: str):  # noqa: PLR0911, PLR0912
    [version, model, param] = reference_id.split("::")

    if version != "v2":
        return None

    if model == "prescription":
        medication_request = MedicationRequest.objects.filter(
            created_date__date=param
        ).first()

        if not medication_request:
            return None

        return create_medication_request_care_context(medication_request)

    if model == "encounter":
        encounter = Encounter.objects.filter(external_id=param).first()

        if not encounter:
            return None

        return create_encounter_care_context(encounter)

    if model == "file_upload":
        file_upload = FileUpload.objects.filter(external_id=param).first()

        if not file_upload:
            return None

        return create_file_upload_care_context(file_upload)

    if model == "questionnaire_response":
        questionnaire_response = (
            QuestionnaireResponse.objects.filter(external_id=param)
            .exclude(status=QuestionnaireResponseStatusChoices.entered_in_error.value)
            .first()
        )

        if not questionnaire_response:
            return None

        return create_questionnaire_response_care_context(questionnaire_response)

    if model == "diagnostic_report":
        diagnostic_report = DiagnosticReport.objects.filter(external_id=param).first()

        if not diagnostic_report:
            return None

        return create_diagnostic_report_care_context(diagnostic_report)

    if model == "invoice":
        invoice = Invoice.objects.filter(external_id=param).first()

        if not invoice:
            return None

        return create_invoice_care_context(invoice)

    if model == "immunization":
        immunization = Immunization.objects.filter(
            external_id=param, status__in=SHAREABLE_IMMUNIZATION_STATUSES
        ).first()

        if not immunization:
            return None

        return create_immunization_care_context(immunization)

    return None


def clean_care_context_display(display: str) -> str:
    # ABDM allows only alphanumeric characters, spaces, hyphens, colons, and dots in displays
    return re.sub(r"[^a-zA-Z0-9\-: .]", "", display.strip())


def create_diagnostic_report_care_context(diagnostic_report: DiagnosticReport):
    return {
        "reference": f"v2::diagnostic_report::{diagnostic_report.external_id}",
        "display": clean_care_context_display(
            f"Diagnostic Report {diagnostic_report.service_request.title}"
        ),
        "hi_type": HealthInformationType.DIAGNOSTIC_REPORT,
    }


def create_medication_request_care_context(medication_request: MedicationRequest):
    return {
        "reference": f"v2::prescription::{medication_request.created_date.date()}",
        "display": clean_care_context_display(
            f"Medication Prescribed on {medication_request.created_date.date()}"
        ),
        "hi_type": HealthInformationType.PRESCRIPTION,
    }


def encounter_class_hi_type(encounter_class: str | None) -> HealthInformationType:
    if encounter_class in ADMISSION_ENCOUNTER_CLASSES:
        return HealthInformationType.DISCHARGE_SUMMARY
    return HealthInformationType.OP_CONSULTATION


def encounter_hi_type(encounter: Encounter) -> HealthInformationType:
    """The record an encounter is linked and shared as, by its current class."""
    return encounter_class_hi_type(encounter.encounter_class)


def create_encounter_care_context(encounter: Encounter):
    return {
        "reference": f"v2::encounter::{encounter.external_id}",
        "display": clean_care_context_display(
            f"Encounter on {encounter.created_date.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        "hi_type": encounter_hi_type(encounter),
    }


def create_file_upload_care_context(file_upload: FileUpload):
    return {
        "reference": f"v2::file_upload::{file_upload.external_id}",
        "display": clean_care_context_display(
            f"File Uploaded on {file_upload.created_date.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        "hi_type": HealthInformationType.RECORD_ARTIFACT,
    }


def create_questionnaire_response_care_context(
    questionnaire_response: QuestionnaireResponse,
):
    return {
        "reference": f"v2::questionnaire_response::{questionnaire_response.external_id}",
        "display": clean_care_context_display(
            f"Observations Added on {questionnaire_response.created_date.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        "hi_type": HealthInformationType.WELLNESS_RECORD,
    }


def create_invoice_care_context(invoice: Invoice):
    display_label = invoice.title or f"Invoice {invoice.number or invoice.external_id}"

    return {
        "reference": f"v2::invoice::{invoice.external_id}",
        "display": clean_care_context_display(
            f"{display_label} on {invoice.modified_date.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        "hi_type": HealthInformationType.INVOICE,
    }


def create_immunization_care_context(immunization: Immunization):
    return {
        "reference": f"v2::immunization::{immunization.external_id}",
        "display": clean_care_context_display(
            f"Immunization Recorded on {immunization.created_date.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        "hi_type": HealthInformationType.IMMUNIZATION_RECORD,
    }
