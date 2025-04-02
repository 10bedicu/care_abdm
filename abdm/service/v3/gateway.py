from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from django.core.cache import cache

from abdm.models import HealthInformationType, Purpose, Transaction, TransactionType
from abdm.service.helper import (
    ABDMAPIException,
    cm_id,
    generate_care_contexts_for_existing_data,
    hf_id_from_abha_id,
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
from care.emr.models.medication_request import MedicationRequest


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
        abha_number = data.get("abha_number")

        if not abha_number:
            raise ABDMAPIException(detail="Provide an ABHA number to generate token")

        payload = {
            "abhaNumber": abha_number.abha_number.replace("-", ""),
            "abhaAddress": abha_number.health_id,
            "name": abha_number.name,
            "gender": abha_number.gender,
            "yearOfBirth": datetime.strptime(
                abha_number.date_of_birth, "%Y-%m-%d"
            ).year,
        }

        request_id = uuid()
        cache.set(
            "abdm_link_care_context__" + request_id,
            {
                "abha_number": abha_number.abha_number,
                "purpose": data.get("purpose"),
                "care_contexts": data.get("care_contexts"),
            },
            timeout=60 * 5,
        )

        path = "/v3/token/generate-token"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": request_id,
                "TIMESTAMP": timestamp(),
                "X-HIP-ID": hf_id_from_abha_id(abha_number.abha_number),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def link__carecontext(data: LinkCarecontextBody) -> LinkCarecontextResponse:
        patient = data.get("patient")
        if not patient:
            raise ABDMAPIException(detail="Provide a patient to link care context")

        abha_number = getattr(patient, "abha_number", None)
        if not abha_number:
            raise ABDMAPIException(
                detail="Failed to link consultation, Patient does not have an ABHA number"
            )

        care_contexts = data.get("care_contexts", [])
        if len(care_contexts) == 0:
            raise ABDMAPIException(detail="Provide at least 1 care contexts to link")

        link_token = cache.get("abdm_link_token__" + abha_number.health_id)

        if not link_token:
            GatewayService.token__generate_token(
                {
                    "abha_number": abha_number,
                    "purpose": "LINK_CARECONTEXT",
                    "care_contexts": care_contexts,
                }
            )
            return {}

        grouped_care_contexts = defaultdict(list)
        for care_context in care_contexts:
            grouped_care_contexts[care_context["hi_type"]].append(care_context)

        payload = {
            "abhaNumber": abha_number.abha_number.replace("-", ""),
            "abhaAddress": abha_number.health_id,
            "patient": list(
                map(
                    lambda hi_type: {
                        "referenceNumber": str(patient.external_id),
                        "display": patient.name,
                        "careContexts": list(
                            map(
                                lambda x: {
                                    "referenceNumber": x["reference"],
                                    "display": x["display"],
                                },
                                grouped_care_contexts[hi_type],
                            )
                        ),
                        "hiType": hi_type,
                        "count": len(grouped_care_contexts[hi_type]),
                    },
                    grouped_care_contexts.keys(),
                )
            ),
        }

        request_id = uuid()
        path = "/hip/v3/link/carecontext"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": request_id,
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "X-HIP-ID": hf_id_from_abha_id(abha_number.abha_number),
                "X-LINK-TOKEN": link_token,
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        Transaction.objects.create(
            reference_id=request_id,
            type=TransactionType.LINK_CARE_CONTEXT,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "type": "hip_initiated_linking",
                "care_contexts": list(map(lambda x: x["reference"], care_contexts)),
            },
            created_by=data.get("user"),
        )

        return {}

    @staticmethod
    def user_initiated_linking__patient__care_context__on_discover(
        data: UserInitiatedLinkingPatientCareContextOnDiscoverBody,
    ) -> UserInitiatedLinkingPatientCareContextOnDiscoverResponse:
        payload: dict = {
            "transactionId": data.get("transaction_id"),
            "response": {
                "requestId": data.get("request_id"),
            },
        }

        patient = data.get("patient")
        if patient:
            care_contexts = generate_care_contexts_for_existing_data(patient)

            grouped_care_contexts = defaultdict(list)
            for care_context in care_contexts:
                grouped_care_contexts[care_context["hi_type"]].append(care_context)

            payload["patient"] = list(
                map(
                    lambda hi_type: {
                        "referenceNumber": str(patient.external_id),
                        "display": patient.name,
                        "careContexts": list(
                            map(
                                lambda x: {
                                    "referenceNumber": x["reference"],
                                    "display": x["display"],
                                },
                                grouped_care_contexts[hi_type],
                            )
                        ),
                        "hiType": hi_type,
                        "count": len(grouped_care_contexts[hi_type]),
                    },
                    grouped_care_contexts.keys(),
                )
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
                        datetime.now() + timedelta(minutes=5)
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
        payload: dict = {
            "response": {
                "requestId": data.get("request_id"),
            },
        }

        patient = data.get("patient")
        care_context_ids = data.get("care_contexts", [])
        if len(care_context_ids) > 0 and patient:
            care_contexts = generate_care_contexts_for_existing_data(patient)

            grouped_care_contexts = defaultdict(list)
            for care_context in care_contexts:
                if care_context["reference"] not in care_context_ids:
                    continue

                grouped_care_contexts[care_context["hi_type"]].append(care_context)

            payload["patient"] = list(
                map(
                    lambda hi_type: {
                        "referenceNumber": str(patient.external_id),
                        "display": patient.name,
                        "careContexts": list(
                            map(
                                lambda x: {
                                    "referenceNumber": x["reference"],
                                    "display": x["display"],
                                },
                                grouped_care_contexts[hi_type],
                            )
                        ),
                        "hiType": hi_type,
                        "count": len(grouped_care_contexts[hi_type]),
                    },
                    grouped_care_contexts.keys(),
                )
            )

        request_id = uuid()
        path = "/user-initiated-linking/v3/link/care-context/on-confirm"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": request_id,
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        Transaction.objects.create(
            reference_id=request_id,
            type=TransactionType.LINK_CARE_CONTEXT,
            meta_data={
                "abha_number": str(patient.abha_number.external_id),
                "type": "patient_initiated_linking",
                "care_contexts": care_context_ids,
            },
        )

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
            "hiRequest": {
                "transactionId": data.get("transaction_id"),
                "sessionStatus": "ACKNOWLEDGED",
            },
            "response": {"requestId": data.get("request_id")},
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

        entries = []
        for care_context in consent.care_contexts:
            care_context_reference = care_context.get("careContextReference", "")
            patient_reference = care_context.get("patientReference", "")

            if "::" not in care_context_reference:
                care_context_reference = f"v0::consultation::{care_context_reference}"

            [version, model, param] = care_context_reference.split("::")

            if version != "v2":
                # Skip if version is not v2
                # TODO: Register the v1 care contexts as v2 after emr migration
                continue

            if (
                model == "medication_request"
                and HealthInformationType.PRESCRIPTION in consent.hi_types
            ):
                medication_requests = MedicationRequest.objects.filter(
                    created_date__date=param,
                    patient__external_id=patient_reference,
                )

                if not medication_requests.exists():
                    continue

                fhir_data = Fhir().create_prescription_record(list(medication_requests))

            else:
                continue

            encrypted_data = cipher.encrypt(fhir_data.json())["data"]
            entry = {
                "content": encrypted_data,
                "media": "application/fhir+json",
                "checksum": "",  # TODO: look into generating checksum
                "careContextReference": care_context.get("careContextReference"),
            }
            entries.append(entry)

        payload = {
            "pageNumber": 1,
            "pageCount": 1,
            "transactionId": data.get("transaction_id"),
            "entries": entries,
            "keyMaterial": {
                "cryptoAlg": data.get("key_material__crypto_algorithm"),
                "curve": data.get("key_material__curve"),
                "dhPublicKey": {
                    "expiry": (datetime.now() + timedelta(days=2)).strftime(
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

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        Transaction.objects.create(
            reference_id=data.get("transaction_id"),
            type=TransactionType.EXCHANGE_DATA,
            meta_data={
                "consent_artefact": str(consent.external_id),
                "is_incoming": False,
            },
        )

        return {}

    @staticmethod
    def data_flow__health_information__notify(
        data: DataFlowHealthInformationNotifyBody,
    ) -> DataFlowHealthInformationNotifyResponse:
        consent = data.get("consent")

        if not consent:
            raise ABDMAPIException(detail="Provide a consent to notify")

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
                    "statusResponses": list(
                        map(
                            lambda care_context: {
                                "careContextReference": care_context.get(
                                    "careContextReference"
                                ),
                                "hiStatus": (
                                    "DELIVERED"
                                    if data.get("status") == "TRANSFERRED"
                                    else "FAILED"
                                ),
                                "description": data.get("status"),
                            },
                            consent.care_contexts or [],
                        )
                    ),
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
        abha_number = data.get("abha_number")

        if not abha_number:
            raise ABDMAPIException(detail="Provide an ABHA number to authenticate")

        payload = {
            "scope": "DEMO",
            "parameters": {
                "abhaNumber": abha_number.abha_number.replace("-", ""),
                "abhaAddress": abha_number.health_id,
                "name": abha_number.name,
                "gender": abha_number.gender,
                "yearOfBirth": datetime.strptime(
                    abha_number.date_of_birth, "%Y-%m-%d"
                ).year,
            },
        }

        path = "/identity/authentication"
        response = GatewayService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
                "REQUESTER-ID": hf_id_from_abha_id(abha_number.abha_number),
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

        hiu_id = hf_id_from_abha_id(consent.patient_abha.health_id)

        payload = {
            "consent": {
                "purpose": {
                    "text": Purpose(consent.purpose).label,
                    "code": Purpose(consent.purpose).value,
                    "refUri": "http://terminology.hl7.org/ValueSet/v3-PurposeOfUse",
                },
                "patient": {"id": consent.patient_abha.health_id},
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
                "X-HIU-ID": hf_id_from_abha_id(consent.patient_abha.health_id),
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
                "X-HIU-ID": hf_id_from_abha_id(artefact.patient_abha.health_id),
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
                    "from": artefact.from_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "to": artefact.to_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
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
                "X-HIU-ID": hf_id_from_abha_id(artefact.patient_abha.health_id),
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(detail=GatewayService.handle_error(response.json()))

        return {}

    @staticmethod
    def patient_share__on_share(
        data: PatientShareOnShareBody,
    ) -> PatientShareOnShareResponse:
        payload = {
            "acknowledgement": {
                "status": data.get("status"),
                "abhaAddress": data.get("abha_address"),
                "profile": {
                    "context": data.get("context"),
                    "tokenNumber": data.get("token_number"),
                    "expiry": data.get("expiry"),
                },
            },
            "response": {"requestId": data.get("request_id")},
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
