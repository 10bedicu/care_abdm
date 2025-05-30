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
from care.facility.models import (
    DailyRound,
    InvestigationSession,
    PatientConsultation,
    PatientRegistration,
    Prescription,
    SuggestionChoices,
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


def hf_id_from_abha_id(health_id: str):
    abha_number = AbhaNumber.objects.filter(
        Q(abha_number=health_id) | Q(health_id=health_id)
    ).first()

    if not abha_number:
        ABDMInternalException(detail="Given ABHA Number does not exist in the system")

    if not abha_number.patient:
        ABDMInternalException(detail="Given ABHA Number is not linked to any patient")

    patient_facility = abha_number.patient.last_consultation.facility

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
    patient: PatientRegistration, hf_id: str | None = None
):
    care_contexts = {}

    consultations = PatientConsultation.objects.filter(patient_id=patient.id)
    if hf_id:
        care_contexts[hf_id] = []
        consultations = consultations.filter(facility__healthfacility__hf_id=hf_id)

    for consultation in consultations:
        consultation_care_contexts = []

        consultation_care_contexts.append(
            {
                "reference": f"v1::consultation::{consultation.external_id}",
                "display": f"Encounter on {consultation.created_date.date()}",
                "hi_type": (
                    HealthInformationType.DISCHARGE_SUMMARY
                    if consultation.suggestion == SuggestionChoices.A
                    else HealthInformationType.OP_CONSULTATION
                ),
            }
        )

        daily_rounds = DailyRound.objects.filter(consultation=consultation)
        for daily_round in daily_rounds:
            consultation_care_contexts.append(
                {
                    "reference": f"v1::daily_round::{daily_round.external_id}",
                    "display": f"Daily Round on {daily_round.created_date.date()}",
                    "hi_type": HealthInformationType.WELLNESS_RECORD,
                }
            )

        investigation_sessions = InvestigationSession.objects.filter(
            investigationvalue__consultation=consultation
        )
        for investigation_session in investigation_sessions:
            consultation_care_contexts.append(
                {
                    "reference": f"v1::investigation_session::{investigation_session.external_id}",
                    "display": f"Investigation on {investigation_session.created_date.date()}",
                    "hi_type": HealthInformationType.DIAGNOSTIC_REPORT,
                }
            )

        prescriptions = (
            Prescription.objects.filter(consultation=consultation)
            .annotate(day=TruncDate("created_date"))
            .order_by("day")
            .distinct("day")
        )
        for prescription in prescriptions:
            consultation_care_contexts.append(
                {
                    "reference": f"v1::prescription::{prescription.created_date.date()}",
                    "display": f"Medication Prescribed on {prescription.created_date.date()}",
                    "hi_type": HealthInformationType.PRESCRIPTION,
                }
            )

        facility = consultation.facility
        if not hasattr(facility, "healthfacility"):
            # TODO: create transaction to log failed transaction for care_context
            pass

        hf_id = facility.healthfacility.hf_id
        if hf_id in care_contexts:
            care_contexts[hf_id].extend(consultation_care_contexts)
        else:
            care_contexts[hf_id] = consultation_care_contexts

    return care_contexts


def care_context_dict_from_reference_id(reference_id: str):
    [version, model, param] = reference_id.split("::")

    if version != "v1":
        return None

    if model == "consultation":
        patient_consultation = PatientConsultation.objects.filter(
            external_id=param
        ).first()
        if not patient_consultation:
            return None

        return {
            "reference": f"v1::consultation::{patient_consultation.external_id}",
            "display": f"Encounter on {patient_consultation.created_date.date()}",
            "hi_type": (
                HealthInformationType.DISCHARGE_SUMMARY
                if patient_consultation.suggestion == SuggestionChoices.A
                else HealthInformationType.OP_CONSULTATION
            ),
        }

    if model == "daily_round":
        daily_round = DailyRound.objects.filter(external_id=param).first()
        if not daily_round:
            return None

        return {
            "reference": f"v1::daily_round::{daily_round.external_id}",
            "display": f"Daily Round on {daily_round.created_date.date()}",
            "hi_type": HealthInformationType.WELLNESS_RECORD,
        }

    if model == "investigation_session":
        investigation_session = InvestigationSession.objects.filter(
            external_id=param
        ).first()
        if not investigation_session:
            return None

        return {
            "reference": f"v1::investigation_session::{investigation_session.external_id}",
            "display": f"Investigation on {investigation_session.created_date.date()}",
            "hi_type": HealthInformationType.DIAGNOSTIC_REPORT,
        }

    if model == "prescription":
        prescription = Prescription.objects.filter(created_date__date=param).first()
        if not prescription:
            return None

        return {
            "reference": f"v1::prescription::{prescription.created_date.date()}",
            "display": f"Medication Prescribed on {prescription.created_date.date()}",
            "hi_type": HealthInformationType.PRESCRIPTION,
        }

    return None
