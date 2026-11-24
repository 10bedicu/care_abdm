import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from django.db.models import Q

from abdm.extensions import linking_opted_out
from abdm.models import (
    CareContext,
    HealthInformationType,
    Purpose,
    Transaction,
    TransactionType,
)
from abdm.service.helper import (
    SHAREABLE_IMMUNIZATION_STATUSES,
    ABDMAPIException,
    ABDMInternalException,
    cm_id,
    encounter_hi_type,
    resolve_care_context,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.gateway import (
    ConsentFetchBody,
    ConsentFetchResponse,
    ConsentRequestHipOnNotifyBody,
    ConsentRequestHipOnNotifyResponse,
    ConsentRequestHiuOnNotifyBody,
    ConsentRequestHiuOnNotifyResponse,
    ConsentRequestInitBody,
    ConsentRequestInitResponse,
    ConsentRequestStatusBody,
    ConsentRequestStatusResponse,
    DataFlowHealthInformationHipOnRequestBody,
    DataFlowHealthInformationHipOnRequestResponse,
    DataFlowHealthInformationNotifyBody,
    DataFlowHealthInformationNotifyResponse,
    DataFlowHealthInformationRequestBody,
    DataFlowHealthInformationRequestResponse,
    DataFlowHealthInformationTransferBody,
    DataFlowHealthInformationTransferResponse,
    IdentityAuthenticationBody,
    IdentityAuthenticationResponse,
    LinkCarecontextBody,
    LinkCarecontextResponse,
    LinkContextNotifyBody,
    LinkContextNotifyResponse,
    LinkPatientLinksSmsNotifyBody,
    LinkPatientLinksSmsNotifyResponse,
    PatientOnSelectionBody,
    PatientOnSelectionResponse,
    PatientOnShareOpenOrderBody,
    PatientOnShareOpenOrderResponse,
    PatientScanPayNotifyBody,
    PatientScanPayNotifyResponse,
    PatientScanPayOnOrderStatusBody,
    PatientScanPayOnOrderStatusResponse,
    PatientShareOnShareBody,
    PatientShareOnShareResponse,
    TokenGenerateTokenBody,
    TokenGenerateTokenResponse,
    UserInitiatedLinkingLinkCareContextOnConfirmBody,
    UserInitiatedLinkingLinkCareContextOnConfirmResponse,
    UserInitiatedLinkingLinkCareContextOnInitBody,
    UserInitiatedLinkingLinkCareContextOnInitResponse,
    UserInitiatedLinkingPatientCareContextOnDiscoverBody,
    UserInitiatedLinkingPatientCareContextOnDiscoverResponse,
)
from abdm.settings import plugin_settings as settings
from abdm.utils.cipher import Cipher
from abdm.utils.fhir import Fhir
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.file_upload import FileUpload
from care.emr.models.immunisation import Immunization
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.questionnaire_response.spec import (
    QuestionnaireResponseStatusChoices,
)
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)


def _encounter_in_range(encounter: Encounter, date_range: dict) -> bool:
    period = encounter.period or {}
    start = (
        datetime.fromisoformat(period["start"])
        if period.get("start")
        else encounter.created_date
    )
    end = datetime.fromisoformat(period["end"]) if period.get("end") else care_now()
    return start <= date_range["to"] and end >= date_range["from"]


def _care_context_fhir_bundle(care_context: dict, consent, date_range: dict):  # noqa: PLR0911, PLR0912
    care_context_reference = care_context.get("careContextReference", "")
    patient_reference = care_context.get("patientReference", "")
    requested_range = (date_range["from"], date_range["to"])

    if "::" not in care_context_reference:
        care_context_reference = f"v0::consultation::{care_context_reference}"

    [version, model, param] = care_context_reference.split("::")

    if version != "v2":
        # TODO: Register the v1 care contexts as v2 after emr migration
        return None

    if (
        model == "prescription"
        and HealthInformationType.PRESCRIPTION in consent.hi_types
    ):
        medication_requests = MedicationRequest.objects.filter(
            created_date__date=param,
            created_date__range=requested_range,
            patient__external_id=patient_reference,
        )
        # the reference is only a date, so keep to the facility that was asked
        if consent.health_facility_id:
            medication_requests = medication_requests.filter(
                encounter__facility=consent.health_facility.facility
            )
        # the reference covers every request of the day, and those of an
        # encounter that was opted out of linking stay out of ABDM
        medication_requests = [
            medication_request
            for medication_request in medication_requests.select_related("encounter")
            if not linking_opted_out(medication_request.encounter.extensions)
        ]
        if not medication_requests:
            return None
        return Fhir().create_prescription_record(medication_requests)

    if model == "encounter":
        encounter = Encounter.objects.filter(external_id=param).first()
        if not encounter or not _encounter_in_range(encounter, date_range):
            return None

        # the record type follows the class the encounter has now, which may
        # not be the one it had when the consent was granted
        hi_type = encounter_hi_type(encounter)
        if hi_type not in consent.hi_types:
            return None
        if hi_type == HealthInformationType.DISCHARGE_SUMMARY:
            return Fhir().create_discharge_summary_record(encounter)
        return Fhir().create_op_consult_record(encounter)

    if (
        model == "file_upload"
        and HealthInformationType.RECORD_ARTIFACT in consent.hi_types
    ):
        file_upload = FileUpload.objects.filter(
            external_id=param, created_date__range=requested_range
        ).first()
        return (
            Fhir().create_health_document_record(file_upload) if file_upload else None
        )

    if (
        model == "questionnaire_response"
        and HealthInformationType.WELLNESS_RECORD in consent.hi_types
    ):
        questionnaire_response = (
            QuestionnaireResponse.objects.filter(
                external_id=param, created_date__range=requested_range
            )
            .exclude(status=QuestionnaireResponseStatusChoices.entered_in_error.value)
            .first()
        )
        return (
            Fhir().create_wellness_record(questionnaire_response)
            if questionnaire_response
            else None
        )

    if (
        model == "diagnostic_report"
        and HealthInformationType.DIAGNOSTIC_REPORT in consent.hi_types
    ):
        diagnostic_report = DiagnosticReport.objects.filter(
            external_id=param, created_date__range=requested_range
        ).first()
        return (
            Fhir().create_diagnostic_report_record(diagnostic_report)
            if diagnostic_report
            else None
        )

    if model == "invoice" and HealthInformationType.INVOICE in consent.hi_types:
        invoice = (
            Invoice.objects.filter(external_id=param)
            .filter(
                Q(issue_date__range=requested_range)
                | Q(issue_date__isnull=True, modified_date__range=requested_range)
            )
            .first()
        )
        return Fhir().create_invoice_record(invoice) if invoice else None

    if (
        model == "immunization"
        and HealthInformationType.IMMUNIZATION_RECORD in consent.hi_types
    ):
        immunization = Immunization.objects.filter(
            external_id=param,
            created_date__range=requested_range,
            status__in=SHAREABLE_IMMUNIZATION_STATUSES,
        ).first()
        return Fhir().create_immunization_record(immunization) if immunization else None

    return None


def _care_context_belongs_to_consent(care_context: dict, consent) -> bool:
    """Whether a care context named in a consent artefact is a record of the
    artefact's patient at the artefact's facility, and not one kept out of ABDM."""
    if not consent.patient_id or not consent.health_facility_id:
        return False

    reference = care_context.get("careContextReference", "")
    if str(care_context.get("patientReference")) != str(consent.patient.external_id):
        return False

    record = resolve_care_context(
        reference, consent.patient, consent.health_facility.facility
    )
    if not record or record.opted_out:
        return False

    # a link we made ourselves says which address the record went to; backfilled
    # rows carry whatever address was stored at the time, so they prove nothing
    links = CareContext.objects.filter(
        health_facility=consent.health_facility,
        patient=consent.patient,
        reference=reference,
        legacy=False,
    )
    return (
        not links.exists() or links.filter(abha_address=consent.abha_address).exists()
    )


def _grouped_care_contexts(patient, care_contexts: list[dict]) -> list[dict]:
    grouped_care_contexts = defaultdict(list)
    for care_context in care_contexts:
        grouped_care_contexts[care_context["hi_type"]].append(care_context)

    return [
        {
            "referenceNumber": str(patient.external_id),
            "display": patient.name,
            "careContexts": [
                {
                    "referenceNumber": care_context["reference"],
                    "display": care_context["display"],
                }
                for care_context in hi_type_care_contexts
            ],
            "hiType": hi_type,
            "count": len(hi_type_care_contexts),
        }
        for hi_type, hi_type_care_contexts in grouped_care_contexts.items()
    ]


def _demographics(abha_auth) -> dict:
    abha_number = abha_auth.abha_number

    return {
        "abhaNumber": abha_number.abha_number.replace("-", "")
        if abha_number.abha_number
        else None,
        "abhaAddress": abha_auth.abha_address,
        "name": abha_number.name,
        "gender": abha_number.gender,
        "yearOfBirth": abha_number.parsed_date_of_birth.split("-")[0]
        if abha_number.parsed_date_of_birth
        else None,
    }


def _error_code(response) -> str | None:
    try:
        error = response.json()
    except ValueError:
        return None

    # the gateway wraps errors differently from one call to the next
    while error:
        if isinstance(error, list):
            error = error[0]
        elif isinstance(error, dict) and error.get("code"):
            return str(error["code"])
        elif isinstance(error, dict):
            error = error.get("error")
        else:
            break
    return None


def _hiu_id(consent) -> str:
    """The health facility a consent request, or an artefact of one, was raised from."""
    consent_request = getattr(consent, "consent_request", consent)
    auth = getattr(consent_request, "abha_auth", None)

    if auth:
        return auth.health_facility.hf_id
    if getattr(consent, "health_facility_id", None):
        return consent.health_facility.hf_id
    if consent.hiu:
        return consent.hiu

    raise ABDMInternalException(
        detail="The consent is not tied to a health facility of this system"
    )


class GatewayService:
    request = Request(settings.ABDM_GATEWAY_URL)

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return GatewayService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return GatewayService.handle_error(error["error"])

        # { message: "error message" }
        if "message" in error:
            return error["message"]

        # { field_name: "error message" }
        if isinstance(error, dict) and len(error) >= 1:
            error.pop("code", None)
            error.pop("timestamp", None)
            return "".join(list(map(lambda x: str(x), list(error.values()))))

        return "Unknown error occurred at ABDM's end while processing the request. Please try again later."

    @staticmethod
    def token__generate_token(
        data: TokenGenerateTokenBody,
    ) -> TokenGenerateTokenResponse:
        abha_auth = data.get("abha_auth")

        if not abha_auth:
            raise ABDMAPIException(detail="Provide an ABHA auth to generate token")

        path = "/v3/token/generate-token"
        response = GatewayService.request.post(
            path,
            _demographics(abha_auth),
            headers={
                "REQUEST-ID": data.get("request_id") or uuid(),
                "TIMESTAMP": timestamp(),
                "X-HIP-ID": abha_auth.health_facility.hf_id,
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def link__carecontext(data: LinkCarecontextBody) -> LinkCarecontextResponse:
        abha_auth = data.get("abha_auth")
        if not abha_auth:
            raise ABDMAPIException(detail="Provide an ABHA auth to link care context")

        patient = data.get("patient")
        if not patient:
            raise ABDMAPIException(detail="Provide a patient to link care context")

        care_contexts = data.get("care_contexts", [])
        if len(care_contexts) == 0:
            raise ABDMAPIException(detail="Provide at least 1 care contexts to link")

        link_token = data.get("link_token")
        if not link_token:
            raise ABDMAPIException(detail="Provide a link token to link care context")

        payload = {
            "abhaNumber": abha_auth.abha_number.abha_number.replace("-", "")
            if abha_auth.abha_number.abha_number
            else None,
            "abhaAddress": abha_auth.abha_address,
            "patient": _grouped_care_contexts(patient, care_contexts),
        }

        path = "/hip/v3/link/carecontext"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": data.get("request_id") or uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIP-ID": abha_auth.health_facility.hf_id,
                "X-LINK-TOKEN": link_token,
            },
        )

        if response.status_code != 202:
            exception = ABDMAPIException(
                detail=GatewayService.handle_error(response.json())
            )
            exception.abdm_error_code = _error_code(response)
            raise exception

        return {}

    @staticmethod
    def link__context__notify(data: LinkContextNotifyBody) -> LinkContextNotifyResponse:
        hf_id = data.get("hf_id")
        abha_address = data.get("abha_address")
        patient = data.get("patient")
        reference = data.get("reference")
        hi_type = data.get("hi_type")

        if not (hf_id and abha_address and patient and reference and hi_type):
            raise ABDMAPIException(
                detail="Provide a health facility, an ABHA address, a patient, a care context and its record type to notify"
            )

        payload = {
            "notification": {
                "patient": {"id": abha_address},
                "careContext": {
                    "patientReference": str(patient.external_id),
                    "careContextReference": reference,
                },
                "hiTypes": [hi_type],
                "date": timestamp(),
                "hip": {"id": hf_id},
            }
        }

        path = "/hip/v3/link/context/notify"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": data.get("request_id") or uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIP-ID": hf_id,
            },
        )

        if response.status_code != requests.codes.accepted:
            exception = ABDMAPIException(
                detail=GatewayService.handle_error(response.json())
            )
            exception.abdm_error_code = _error_code(response)
            raise exception

        return {}

    @staticmethod
    def link__patient__links__sms__notify2(
        data: LinkPatientLinksSmsNotifyBody,
    ) -> LinkPatientLinksSmsNotifyResponse:
        health_facility = data.get("health_facility")
        phone_number = data.get("phone_number")

        if not health_facility or not phone_number:
            raise ABDMAPIException(
                detail="Provide a health facility and a mobile number to notify the patient"
            )

        payload = {
            "notification": {
                "phoneNo": phone_number,
                "hip": {
                    "id": health_facility.hf_id,
                    "name": health_facility.facility.name,
                },
            }
        }

        path = "/hip/v3/link/patient/links/sms/notify2"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": data.get("request_id") or uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != requests.codes.accepted:
            exception = ABDMAPIException(
                detail=GatewayService.handle_error(response.json())
            )
            exception.abdm_error_code = _error_code(response)
            raise exception

        return {}

    @staticmethod
    def user_initiated_linking__patient__care_context__on_discover(
        data: UserInitiatedLinkingPatientCareContextOnDiscoverBody,
    ) -> UserInitiatedLinkingPatientCareContextOnDiscoverResponse:
        hf_id = data.get("hf_id", None)

        if not hf_id:
            raise ABDMAPIException(
                detail="Provide a health facility id to discover care context"
            )

        payload: dict = {
            "transactionId": data.get("transaction_id"),
            "response": {
                "requestId": data.get("request_id"),
            },
        }

        patient = data.get("patient")
        if patient:
            payload["patient"] = _grouped_care_contexts(
                patient, data.get("care_contexts") or []
            )
            payload["matchedBy"] = data.get("matched_by", [])
        else:
            payload["error"] = {
                "code": "ABDM-1010",
                "message": "Patient not found",
            }

        path = "/user-initiated-linking/v3/patient/care-context/on-discover"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def user_initiated_linking__link__care_context__on_init(
        data: UserInitiatedLinkingLinkCareContextOnInitBody,
    ) -> UserInitiatedLinkingLinkCareContextOnInitResponse:
        payload = {
            "transactionId": data.get("transaction_id"),
            "link": {
                "referenceNumber": data.get("reference_id"),
                "authenticationType": "DIRECT",
                "meta": {
                    "communicationMedium": "MOBILE",
                    "communicationHint": "OTP",
                    "communicationExpiry": (
                        datetime.now(UTC) + timedelta(minutes=5)
                    ).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                },
            },
            "response": {
                "requestId": data.get("request_id"),
            },
        }

        path = "/user-initiated-linking/v3/link/care-context/on-init"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def user_initiated_linking__link__care_context__on_confirm(
        data: UserInitiatedLinkingLinkCareContextOnConfirmBody,
    ) -> UserInitiatedLinkingLinkCareContextOnConfirmResponse:
        hf_id = data.get("hf_id", None)

        if not hf_id:
            raise ABDMAPIException(
                detail="Provide a health facility id to confirm care context"
            )

        payload: dict = {
            "response": {
                "requestId": data.get("request_id"),
            },
        }

        patient = data.get("patient")
        care_contexts = data.get("care_contexts", [])
        if len(care_contexts) > 0 and patient:
            payload["patient"] = _grouped_care_contexts(patient, care_contexts)

        path = "/user-initiated-linking/v3/link/care-context/on-confirm"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": data.get("reference_id") or uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def consent__request__hip__on_notify(
        data: ConsentRequestHipOnNotifyBody,
    ) -> ConsentRequestHipOnNotifyResponse:
        payload = {
            "acknowledgement": {
                "status": "ok",
                "consentId": data.get("consent_id"),
            },
            "response": {"requestId": data.get("request_id")},
        }

        path = "/consent/v3/request/hip/on-notify"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def data_flow__health_information__hip__on_request(
        data: DataFlowHealthInformationHipOnRequestBody,
    ) -> DataFlowHealthInformationHipOnRequestResponse:
        payload = {
            "response": {"requestId": data.get("request_id")},
        }
        if data.get("error"):
            payload["error"] = data["error"]
        else:
            payload["hiRequest"] = {
                "transactionId": data.get("transaction_id"),
                "sessionStatus": "ACKNOWLEDGED",
            }

        path = "/data-flow/v3/health-information/hip/on-request"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def data_flow__health_information__transfer(
        data: DataFlowHealthInformationTransferBody,
    ) -> DataFlowHealthInformationTransferResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(
                detail="Provide a consent to transfer health information"
            )

        cipher = Cipher(
            external_public_key=data.get("key_material__public_key"),
            external_nonce=data.get("key_material__nonce"),
        )
        cipher.generate_key_pair()

        logger.info(
            f"ABDM_DEBUG__DATA_FLOW__HEALTH_INFORMATION__TRANSFER :: Consent: {consent.__dict__}"
        )

        entries = []
        errored_care_context_references = []
        skipped_care_context_references = []
        for care_context in consent.care_contexts:
            if not _care_context_belongs_to_consent(care_context, consent):
                logger.warning(
                    "ABDM_DEBUG__DATA_FLOW__HEALTH_INFORMATION__TRANSFER :: Care context %s is not a record of the patient and facility of consent %s",
                    care_context.get("careContextReference"),
                    consent.external_id,
                )
                errored_care_context_references.append(
                    care_context.get("careContextReference")
                )
                continue

            try:
                fhir_data = _care_context_fhir_bundle(
                    care_context, consent, data.get("date_range")
                )
            except Exception:
                logger.exception(
                    "ABDM_DEBUG__DATA_FLOW__HEALTH_INFORMATION__TRANSFER :: Failed to build FHIR data for %s",
                    care_context.get("careContextReference"),
                )
                errored_care_context_references.append(
                    care_context.get("careContextReference")
                )
                continue

            if not fhir_data:
                skipped_care_context_references.append(
                    care_context.get("careContextReference")
                )
                continue

            logger.info(
                f"ABDM_DEBUG__DATA_FLOW__HEALTH_INFORMATION__TRANSFER :: FHIR Data: {fhir_data.json()}"
            )

            encrypted_data = cipher.encrypt(fhir_data.json())["data"]
            entry = {
                "content": encrypted_data,
                "media": "application/fhir+json",
                "checksum": "",  # TODO: look into generating checksum
                "careContextReference": care_context.get("careContextReference"),
            }
            entries.append(entry)

        if not entries:
            raise ABDMAPIException(
                detail="No health information could be generated for the requested care contexts"
            )

        logger.info(
            f"ABDM_DEBUG__DATA_FLOW__HEALTH_INFORMATION__TRANSFER :: Entries: {entries}"
        )

        payload = {
            "pageNumber": 1,
            "pageCount": 1,
            "transactionId": data.get("transaction_id"),
            "entries": entries,
            "keyMaterial": {
                "cryptoAlg": data.get("key_material__crypto_algorithm"),
                "curve": data.get("key_material__curve"),
                "dhPublicKey": {
                    "expiry": (datetime.now(UTC) + timedelta(days=2)).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                    "parameters": "Curve25519/32byte random key",
                    "keyValue": cipher.key_to_share,
                },
                "nonce": cipher.internal_nonce,
            },
        }

        auth_header = Request("").auth_header()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **auth_header,
        }

        path = data.get("url", "")
        response = requests.post(
            path,
            json=payload,
            headers=headers,
            timeout=20,
        )

        if not response.ok:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.text))

        Transaction.objects.create(
            reference_id=data.get("transaction_id"),
            type=TransactionType.EXCHANGE_DATA,
            meta_data={
                "consent_artefact": str(consent.external_id),
                "is_incoming": False,
            },
        )

        return {
            "errored_care_context_references": errored_care_context_references,
            "skipped_care_context_references": skipped_care_context_references,
        }

    @staticmethod
    def data_flow__health_information__notify(
        data: DataFlowHealthInformationNotifyBody,
    ) -> DataFlowHealthInformationNotifyResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(detail="Provide a consent to notify")

        succeeded = data.get("status") in ("TRANSFERRED", "RECEIVED")
        success_hi_status = "OK" if data.get("notifier__type") == "HIU" else "DELIVERED"
        errored = set(data.get("errored_care_context_references") or [])
        skipped = set(data.get("skipped_care_context_references") or [])

        status_responses = []
        for care_context in consent.care_contexts or []:
            reference = care_context.get("careContextReference")
            if succeeded and reference in errored:
                hi_status = "ERRORED"
                description = "Failed to generate health information"
            elif succeeded and reference in skipped:
                hi_status = "ERRORED"
                description = "No health information found"
            else:
                hi_status = success_hi_status if succeeded else "ERRORED"
                description = data.get("status")
            status_responses.append(
                {
                    "careContextReference": reference,
                    "hiStatus": hi_status,
                    "description": description,
                }
            )

        payload = {
            "notification": {
                "consentId": data.get("consent_id"),
                "transactionId": data.get("transaction_id"),
                "doneAt": timestamp(),
                "notifier": {
                    "type": data.get("notifier__type"),
                    "id": data.get("notifier__id"),
                },
                "statusNotification": {
                    "sessionStatus": data.get("status"),
                    "hipId": data.get("hip_id"),
                    "statusResponses": status_responses,
                },
            }
        }

        path = "/data-flow/v3/health-information/notify"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def identity__authentication(
        data: IdentityAuthenticationBody,
    ) -> IdentityAuthenticationResponse:
        abha_auth = data.get("abha_auth")

        if not abha_auth:
            raise ABDMAPIException(detail="Provide an ABHA auth to authenticate")

        payload = {
            "scope": "DEMO",
            "parameters": _demographics(abha_auth),
        }

        path = "/identity/authentication"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "REQUESTER-ID": abha_auth.health_facility.hf_id,
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def consent__request__init(
        data: ConsentRequestInitBody,
    ) -> ConsentRequestInitResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(detail="Provide a consent to initiate")

        if not consent.abha_auth:
            raise ABDMAPIException(
                detail="Provide a consent raised from an ABHA auth to initiate"
            )

        hiu_id = consent.abha_auth.health_facility.hf_id

        payload = {
            "consent": {
                "purpose": {
                    "text": Purpose(consent.purpose).label,
                    "code": Purpose(consent.purpose).value,
                    "refUri": "http://terminology.hl7.org/ValueSet/v3-PurposeOfUse",
                },
                "patient": {"id": consent.abha_auth.abha_address},
                "hiu": {"id": hiu_id},
                "requester": {
                    "name": f"{consent.requester.first_name} {consent.requester.last_name}",
                    "identifier": {
                        "type": "CARE Username",
                        "value": consent.requester.username,
                        "system": settings.CURRENT_DOMAIN,
                    },
                },
                "hiTypes": consent.hi_types,
                "permission": {
                    "accessMode": consent.access_mode,
                    "dateRange": {
                        "from": consent.from_time.astimezone(UTC).strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                        "to": consent.to_time.astimezone(UTC).strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                    },
                    "dataEraseAt": consent.expiry.astimezone(UTC).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                    "frequency": {
                        "unit": consent.frequency_unit,
                        "value": consent.frequency_value,
                        "repeats": consent.frequency_repeats,
                    },
                },
            },
        }

        path = "/consent/v3/request/init"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": str(consent.external_id),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIU-ID": hiu_id,
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def consent__request__status(
        data: ConsentRequestStatusBody,
    ) -> ConsentRequestStatusResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(detail="Provide a consent to check status")

        payload = {
            "consentRequestId": str(consent.consent_id),
        }

        path = "/consent/v3/request/status"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIU-ID": _hiu_id(consent),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def consent__request__hiu__on_notify(
        data: ConsentRequestHiuOnNotifyBody,
    ) -> ConsentRequestHiuOnNotifyResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(detail="Provide a consent to notify")

        payload = {
            "acknowledgement": list(
                map(
                    lambda x: {
                        "consentId": str(x.external_id),
                        "status": "OK",
                    },
                    consent.consent_artefacts.all() or [],
                )
            ),
            "response": {"requestId": data.get("request_id")},
        }

        path = "/consent/v3/request/hiu/on-notify"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def consent__fetch(
        data: ConsentFetchBody,
    ) -> ConsentFetchResponse:
        artefact = data.get("artefact")

        if not artefact:
            raise ABDMAPIException(detail="Provide a consent to check status")

        payload = {
            "consentId": str(artefact.artefact_id),
        }

        path = "/consent/v3/fetch"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIU-ID": _hiu_id(artefact),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def data_flow__health_information__request(
        data: DataFlowHealthInformationRequestBody,
    ) -> DataFlowHealthInformationRequestResponse:
        artefact = data.get("artefact")

        if not artefact:
            raise ABDMAPIException(detail="Provide a consent artefact to request")

        request_id = uuid()
        artefact.consent_id = request_id
        artefact.save()

        payload = {
            "hiRequest": {
                "consent": {"id": str(artefact.artefact_id)},
                "dateRange": {
                    "from": artefact.from_time.astimezone(UTC).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                    "to": artefact.to_time.astimezone(UTC).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                },
                "dataPushUrl": settings.BACKEND_DOMAIN
                + "/api/abdm/api/v3/hiu/health-information/transfer",
                "keyMaterial": {
                    "cryptoAlg": artefact.key_material_algorithm,
                    "curve": artefact.key_material_curve,
                    "dhPublicKey": {
                        "expiry": artefact.expiry.astimezone(UTC).strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                        "parameters": f"{artefact.key_material_curve}/{artefact.key_material_algorithm}",
                        "keyValue": artefact.key_material_public_key,
                    },
                    "nonce": artefact.key_material_nonce,
                },
            },
        }

        path = "/data-flow/v3/health-information/request"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": request_id,
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIU-ID": _hiu_id(artefact),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def patient_share__on_share(
        data: PatientShareOnShareBody,
    ) -> PatientShareOnShareResponse:
        acknowledgement = data.get("acknowledgement")
        error = data.get("error")

        if not acknowledgement and not error:
            raise ABDMAPIException(detail="Provide an acknowledgement or error")

        payload = {
            "response": {"requestId": data.get("request_id")},
        }

        if acknowledgement:
            payload["acknowledgement"] = {
                "status": acknowledgement.get("status"),
                "abhaAddress": acknowledgement.get("abha_address"),
                "profile": {
                    "context": acknowledgement.get("context"),
                    "tokenNumber": acknowledgement.get("token_number"),
                    "expiry": acknowledgement.get("expiry"),
                },
            }

        if error:
            payload["error"] = {
                "message": error.get("message"),
                "code": error.get("code"),
            }

        path = "/patient-share/v3/on-share"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def serialize_scan_pay_procedures(procedures):
        return [
            {
                "category": procedure.get("category"),
                "services": [
                    {
                        "serviceId": service.get("service_id"),
                        "name": service.get("name"),
                        "description": service.get("description"),
                        "amount": service.get("amount"),
                    }
                    for service in procedure.get("services", [])
                ],
            }
            for procedure in procedures
        ]

    @staticmethod
    def serialize_scan_pay_acknowledgement(acknowledgement):
        # gateway swagger uses misspelled paymentRecipetLink while postman uses paymentReceiptLink, send both
        return {
            "status": acknowledgement.get("status"),
            "abhaAddress": acknowledgement.get("abha_address"),
            "transactionId": acknowledgement.get("transaction_id"),
            "orderNumber": acknowledgement.get("order_number"),
            "openOrderRequestId": acknowledgement.get("open_order_request_id"),
            "paymentDate": acknowledgement.get("payment_date"),
            "paymentReceiptLink": acknowledgement.get("payment_receipt_link"),
            "paymentRecipetLink": acknowledgement.get("payment_receipt_link"),
        }

    @staticmethod
    def patient__on_share_open_order(
        data: PatientOnShareOpenOrderBody,
    ) -> PatientOnShareOpenOrderResponse:
        payload = {
            "intent": "OPEN_PAYMENT_ORDER",
            "response": {"requestId": data.get("request_id")},
        }

        if data.get("abha_address"):
            payload["abhaAddress"] = data.get("abha_address")

        if data.get("patient_uid"):
            payload["patientUid"] = data.get("patient_uid")

        if data.get("procedures") is not None:
            payload["procedures"] = GatewayService.serialize_scan_pay_procedures(
                data.get("procedures")
            )

        if data.get("error"):
            payload["error"] = {
                "message": data.get("error").get("message"),
                "code": data.get("error").get("code"),
            }

        path = "/scan-gateway/v3/patient/on-share/open-order"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def patient__on_selection(
        data: PatientOnSelectionBody,
    ) -> PatientOnSelectionResponse:
        payload = {
            "intent": "PAYMENT_ORDER",
            "response": {"requestId": data.get("request_id")},
        }

        if data.get("open_order_request_id"):
            payload["openOrderRequestId"] = data.get("open_order_request_id")

        if data.get("abha_address"):
            payload["abhaAddress"] = data.get("abha_address")

        if data.get("procedures") is not None:
            payload["procedures"] = GatewayService.serialize_scan_pay_procedures(
                data.get("procedures")
            )

        if data.get("payment_bundle"):
            payment_bundle = data.get("payment_bundle")
            payload["paymentBundle"] = {
                "paymentMode": payment_bundle.get("payment_mode"),
                "paymentUrl": payment_bundle.get("payment_url"),
                "orderNumber": payment_bundle.get("order_number"),
                "amount": payment_bundle.get("amount"),
                "merchantId": payment_bundle.get("merchant_id"),
                "description": payment_bundle.get("description"),
            }

        if data.get("error"):
            payload["error"] = {
                "message": data.get("error").get("message"),
                "code": data.get("error").get("code"),
            }

        path = "/scan-gateway/v3/patient/on-selection"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def patient__scan_pay_notify(
        data: PatientScanPayNotifyBody,
    ) -> PatientScanPayNotifyResponse:
        payload = {
            "acknowledgement": GatewayService.serialize_scan_pay_acknowledgement(
                data.get("acknowledgement")
            ),
        }

        if data.get("error"):
            payload["error"] = {
                "message": data.get("error").get("message"),
                "code": data.get("error").get("code"),
            }

        path = "/scan-gateway/v3/patient/scan-pay/notify"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIP-ID": data.get("hip_id"),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def patient__scan_pay_on_order_status(
        data: PatientScanPayOnOrderStatusBody,
    ) -> PatientScanPayOnOrderStatusResponse:
        payload = {
            "response": {"requestId": data.get("request_id")},
        }

        if data.get("acknowledgement"):
            payload["acknowledgement"] = (
                GatewayService.serialize_scan_pay_acknowledgement(
                    data.get("acknowledgement")
                )
            )

        if data.get("error"):
            payload["error"] = {
                "message": data.get("error").get("message"),
                "code": data.get("error").get("code"),
            }

        path = "/scan-gateway/v3/patient/scan-pay/on-order-status"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}
