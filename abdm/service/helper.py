from base64 import b64decode, b64encode
from datetime import UTC, datetime
from uuid import uuid4

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from django.db.models import Q
from django.db.models.functions import TruncDate
from rest_framework.exceptions import APIException

from abdm.models.abha_number import AbhaNumber
from abdm.models.base import HealthInformationType
from abdm.service.request import Request
from abdm.settings import plugin_settings as settings
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.observation import Observation
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.encounter.constants import ClassChoices
from care.emr.resources.file_upload.spec import FileTypeChoices


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


def hf_id_from_abha_id(health_id: str):
    abha_number = AbhaNumber.objects.filter(
        Q(abha_number=health_id) | Q(health_id=health_id)
    ).first()

    if not abha_number:
        ABDMInternalException(detail="Given ABHA Number does not exist in the system")

    if not abha_number.patient:
        ABDMInternalException(detail="Given ABHA Number is not linked to any patient")

    last_encounter = Encounter.objects.filter(patient=abha_number.patient).last()
    patient_facility = last_encounter.facility

    if not hasattr(patient_facility, "healthfacility"):
        raise ABDMInternalException(
            detail="The facility to which the patient is linked does not have a health facility linked"
        )

    return patient_facility.healthfacility.hf_id


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


def generate_care_contexts_for_existing_data(
    patient: Patient, hf_id: str | None = None
):
    care_contexts = {}

    encounters = Encounter.objects.filter(patient_id=patient.id)
    if hf_id:
        care_contexts[hf_id] = []
        encounters = encounters.filter(facility__healthfacility__hf_id=hf_id)

    for encounter in encounters:
        encounter_care_contexts = []

        encounter_care_contexts.append(create_encounter_care_context(encounter))

        medication_requests = (
            MedicationRequest.objects.filter(
                patient_id=patient.id, encounter_id=encounter.id
            )
            .annotate(day=TruncDate("created_date"))
            .order_by("day")
            .distinct("day")
        )
        for request in medication_requests:
            encounter_care_contexts.append(
                create_medication_request_care_context(request)
            )

        files = FileUpload.objects.filter(
            associating_id=encounter.external_id,
            file_type=FileTypeChoices.encounter,
            upload_completed=True,
        )
        for file in files:
            encounter_care_contexts.append(create_file_upload_care_context(file))

        questionnaire_responses = QuestionnaireResponse.objects.filter(
            encounter=encounter,
            patient=patient,
        )
        for response in questionnaire_responses:
            observations = Observation.objects.filter(
                questionnaire_response=response
            ).filter(
                Q(main_code__isnull=False) & ~Q(main_code={})
                | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
            )

            if observations.exists():
                encounter_care_contexts.append(
                    create_questionnaire_response_care_context(response)
                )

        facility = encounter.facility
        if not hasattr(facility, "healthfacility"):
            # TODO: create transaction to log failed transaction for care_context
            pass

        hf_id = facility.healthfacility.hf_id
        if hf_id in care_contexts:
            care_contexts[hf_id].extend(encounter_care_contexts)
        else:
            care_contexts[hf_id] = encounter_care_contexts

    return care_contexts


def care_context_dict_from_reference_id(reference_id: str):  # noqa: PLR0911
    [version, model, param] = reference_id.split("::")

    if version != "v2":
        return None

    if model == "medication_request":
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
        questionnaire_response = QuestionnaireResponse.objects.filter(
            external_id=param
        ).first()

        if not questionnaire_response:
            return None

        return create_questionnaire_response_care_context(questionnaire_response)

    return None


def create_medication_request_care_context(medication_request: MedicationRequest):
    return {
        "reference": f"v2::prescription::{medication_request.created_date.date()}",
        "display": f"Medication Prescribed on {medication_request.created_date.date()}",
        "hi_type": HealthInformationType.PRESCRIPTION,
    }


def create_encounter_care_context(encounter: Encounter):
    is_admission = encounter.encounter_class in [
        ClassChoices.imp,
        ClassChoices.emer,
        ClassChoices.obsenc,
    ]

    return {
        "reference": f"v2::encounter::{encounter.external_id}",
        "display": f"Encounter on {encounter.created_date}",
        "hi_type": HealthInformationType.DISCHARGE_SUMMARY
        if is_admission
        else HealthInformationType.OP_CONSULTATION,
    }


def create_file_upload_care_context(file_upload: FileUpload):
    return {
        "reference": f"v2::file_upload::{file_upload.external_id}",
        "display": f"File Uploaded on {file_upload.created_date}",
        "hi_type": HealthInformationType.RECORD_ARTIFACT,
    }


def create_questionnaire_response_care_context(
    questionnaire_response: QuestionnaireResponse,
):
    return {
        "reference": f"v2::questionnaire_response::{questionnaire_response.external_id}",
        "display": f"Observations Added on {questionnaire_response.created_date}",
        "hi_type": HealthInformationType.WELLNESS_RECORD,
    }
