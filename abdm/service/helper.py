from base64 import b64decode, b64encode
from datetime import UTC, datetime
from uuid import uuid4

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from django.db.models import Q
from django.db.models.functions import TruncDate
from rest_framework.exceptions import APIException

from abdm.models import AbhaNumber, HealthInformationType
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


def generate_care_contexts_for_existing_data(patient: PatientRegistration):
    care_contexts = []

    consultations = PatientConsultation.objects.filter(patient=patient)
    for consultation in consultations:
        care_contexts.append(
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
            care_contexts.append(
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
            care_contexts.append(
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
            care_contexts.append(
                {
                    "reference": f"v1::prescription::{prescription.created_date.date()}",
                    "display": f"Medication Prescribed on {prescription.created_date.date()}",
                    "hi_type": HealthInformationType.PRESCRIPTION,
                }
            )

    return care_contexts
