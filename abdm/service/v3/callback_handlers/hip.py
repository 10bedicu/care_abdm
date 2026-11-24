import logging
from datetime import datetime
from uuid import UUID

from django.contrib.postgres.search import TrigramSimilarity
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from abdm.models import (
    AbhaAuthMethod,
    HealthFacility,
    Transaction,
    TransactionType,
)
from abdm.models.abha_auth import normalize_abha_address
from abdm.models.base import Status
from abdm.models.consent import ConsentArtefact
from abdm.service.abha import patient_for_abha, record_abha_auth
from abdm.service.helper import uuid, validate_and_format_date
from abdm.service.linking import (
    complete_care_context_notification,
    complete_deep_link_sms,
    complete_link_request,
    discoverable_care_contexts,
    ready_care_contexts,
    receive_link_token,
    record_user_initiated_links,
)
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.service.v3.gateway import GatewayService
from abdm.settings import plugin_settings as settings
from abdm.tasks.link_care_context import enqueue_link_patient_care_contexts
from abdm.utils.patient_identifier import ensure_abdm_patient_identifier
from abdm.utils.token import get_or_create_scan_and_share_token
from abdm.utils.user import get_or_create_abdm_user
from care.emr.locks.billing import PatientCreateLock
from care.emr.models.organization import Organization
from care.emr.models.patient import Patient
from care.emr.resources.organization.spec import OrganizationTypeChoices
from care.emr.resources.patient.spec import GenderChoices
from care.emr.resources.patient_identifier.default_expression_evaluator import (
    evaluate_patient_instance_default_values,
)
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)


def handle_token_on_generate_token(validated_data: dict, headers: dict):
    request_id = (validated_data.get("response") or {}).get("requestId")

    if not receive_link_token(
        hf_id=headers.get("X-HIP-ID"),
        abha_address=validated_data.get("abhaAddress"),
        link_token=validated_data.get("linkToken"),
        request_id=str(request_id) if request_id else None,
        error=validated_data.get("error"),
    ):
        message = (
            f"No valid ABHA auth is waiting for the link token of request {request_id}"
        )
        raise CallbackProcessingError(message)


def handle_link_on_carecontext(validated_data: dict, headers: dict):
    complete_link_request(
        str(validated_data.get("response", {}).get("requestId")),
        error=validated_data.get("error"),
    )


def _notify_outcome(validated_data: dict) -> tuple[str | None, dict | None]:
    """The request an on-notify callback answers, and its error when it failed."""
    request_id = (validated_data.get("response") or {}).get("requestId")
    request_id = request_id if isinstance(request_id, str) and request_id else None
    error = validated_data.get("error") or {}
    status = (validated_data.get("acknowledgement") or {}).get("status") or ""

    # the body is not confirmed against the sandbox: a failure is taken from
    # either the error or the status, whichever says so
    if not (error.get("code") or error.get("message")) and (
        not status or status.upper() == "SUCCESS"
    ):
        return request_id, None

    return request_id, {
        "code": error.get("code") or status,
        "message": error.get("message"),
    }


def handle_links_context_on_notify(validated_data: dict, headers: dict):
    request_id, error = _notify_outcome(validated_data)
    complete_care_context_notification(request_id, error=error)


def handle_patients_sms_on_notify(validated_data: dict, headers: dict):
    request_id, error = _notify_outcome(validated_data)
    complete_deep_link_sms(request_id, error=error)


def handle_patient_care_context_discover(validated_data: dict, headers: dict):
    patient_data = validated_data.get("patient", {})
    identifiers = [
        *(patient_data.get("verifiedIdentifiers", []) or []),
        *(patient_data.get("unverifiedIdentifiers", []) or []),
    ]

    shared_abha_number = next(
        filter(lambda x: x.get("type") == "ABHA_NUMBER", identifiers), {}
    ).get("value")
    patient = patient_for_abha(
        abha_number=shared_abha_number, abha_address=patient_data.get("id")
    )
    matched_by = "ABHA_NUMBER"

    if not patient:
        mobile = next(
            filter(lambda x: x.get("type") == "MOBILE", identifiers), {}
        ).get("value")
        patient = (
            Patient.objects.annotate(
                similarity=TrigramSimilarity("name", patient_data.get("name"))
            )
            .filter(
                Q(phone_number=mobile) | Q(phone_number="+91" + mobile),
                Q(
                    date_of_birth__year__gte=patient_data.get("yearOfBirth") - 5,
                    date_of_birth__year__lte=patient_data.get("yearOfBirth") + 5,
                )
                | Q(year_of_birth__gte=patient_data.get("yearOfBirth") - 5),
                year_of_birth__lte=patient_data.get("yearOfBirth") + 5,
                gender={"M": 1, "F": 2, "O": 3}.get(patient_data.get("gender"), 3),
                similarity__gt=0.3,
            )
            .order_by("-similarity")
            .first()
        )
        matched_by = "MOBILE"

    if not patient:
        # TODO: handle MR matching
        pass

    health_facility = HealthFacility.objects.filter(
        hf_id=headers.get("X-HIP-ID")
    ).first()

    GatewayService.user_initiated_linking__patient__care_context__on_discover(
        {
            "transaction_id": str(validated_data.get("transactionId")),
            "request_id": headers.get("REQUEST-ID"),
            "patient": patient,
            "care_contexts": discoverable_care_contexts(
                patient, health_facility, patient_data.get("id")
            )
            if patient and health_facility
            else [],
            "matched_by": [matched_by],
            "hf_id": headers.get("X-HIP-ID"),
        }
    )


def handle_link_care_context_init(validated_data: dict, headers: dict):
    care_contexts = [
        context.get("referenceNumber")
        for patient in validated_data.get("patient", [])
        for context in patient.get("careContexts", [])
    ]

    reference_id = uuid()
    cache.set(
        "abdm_user_initiated_linking__" + reference_id,
        {
            "reference_id": reference_id,
            # TODO: generate OTP and send it to the patient
            "otp": "000000",
            "abha_address": validated_data.get("abhaAddress"),
            "patient_id": validated_data.get("patient", [{}])[0].get(
                "referenceNumber"
            ),
            "care_contexts": care_contexts,
        },
    )

    GatewayService.user_initiated_linking__link__care_context__on_init(
        {
            "transaction_id": str(validated_data.get("transactionId")),
            "request_id": headers.get("REQUEST-ID"),
            "reference_id": reference_id,
        }
    )


def handle_link_care_context_confirm(validated_data: dict, headers: dict):
    link_ref_number = validated_data.get("confirmation").get("linkRefNumber")
    cached_data = cache.get("abdm_user_initiated_linking__" + link_ref_number)

    if not cached_data:
        raise CallbackProcessingError(
            f"Reference ID: {link_ref_number} not found in cache"
        )

    if cached_data.get("otp") != validated_data.get("confirmation").get("token"):
        raise CallbackProcessingError(
            f"Invalid OTP for Reference ID: {link_ref_number}"
        )

    patient_id = cached_data.get("patient_id")
    patient = Patient.objects.filter(external_id=patient_id).first()

    if not patient:
        raise CallbackProcessingError(
            f"Patient with ID: {patient_id} not found in the database"
        )

    health_facility = HealthFacility.objects.filter(
        hf_id=headers.get("X-HIP-ID")
    ).first()

    if not health_facility:
        message = f"Health Facility with ID: {headers.get('X-HIP-ID')} not found in the database"
        raise CallbackProcessingError(message)

    chosen = set(cached_data.get("care_contexts") or [])
    care_contexts = [
        care_context
        for care_context in ready_care_contexts(patient, health_facility)
        if care_context["reference"] in chosen
    ]

    reference_id = uuid()
    GatewayService.user_initiated_linking__link__care_context__on_confirm(
        {
            "request_id": headers.get("REQUEST-ID"),
            "reference_id": reference_id,
            "patient": patient,
            "care_contexts": care_contexts,
            "hf_id": health_facility.hf_id,
        }
    )

    # the patient linked these from their PHR app: it is not an ABHA auth at the
    # facility, and it says nothing about who the patient's ABHA record is
    record_user_initiated_links(
        patient=patient,
        health_facility=health_facility,
        abha_address=cached_data.get("abha_address"),
        care_contexts=care_contexts,
        request_id=reference_id,
    )

    abha_number = getattr(patient, "abha_number", None)
    Transaction.objects.create(
        reference_id=reference_id,
        type=TransactionType.LINK_CARE_CONTEXT,
        meta_data={
            **({"abha_number": str(abha_number.external_id)} if abha_number else {}),
            "patient": str(patient.external_id),
            "type": "patient_initiated_linking",
            "care_contexts": [
                care_context["reference"] for care_context in care_contexts
            ],
            "hf_id": health_facility.hf_id,
        },
    )


def _consented_patient(consent_detail: dict) -> Patient:
    """The patient whose records a consent artefact covers.

    The care contexts name the patient by our own reference, which holds whatever
    ABHA address the consent was granted from. The address identifies the patient
    only when the artefact lists no care context.
    """
    consent_id = consent_detail.get("consentId")
    references = {
        care_context.get("patientReference")
        for care_context in consent_detail.get("careContexts") or []
    }

    if len(references) > 1:
        message = f"Consent {consent_id} names care contexts of more than one patient"
        raise CallbackProcessingError(message)

    if references:
        try:
            patient = Patient.objects.filter(
                external_id=UUID(str(references.pop()))
            ).first()
        except ValueError:
            patient = None
    else:
        patient = patient_for_abha(
            abha_address=consent_detail.get("patient").get("id")
        )

    if not patient:
        message = f"Patient of consent {consent_id} not found in the database"
        raise CallbackProcessingError(message)

    return patient


def handle_consent_request_hip_notify(validated_data: dict, headers: dict):
    # TODO: handle the case where hip/notify is called before hip/on-init

    notification = validated_data.get("notification")

    if notification.get("status") != Status.GRANTED:
        if not ConsentArtefact.objects.filter(
            consent_id=notification.get("consentId")
        ).update(status=notification.get("status")):
            logger.warning(
                "Consent %s was %s but is not known to this HIP",
                notification.get("consentId"),
                notification.get("status"),
            )

        GatewayService.consent__request__hip__on_notify(
            {
                "consent_id": str(notification.get("consentId")),
                "request_id": headers.get("REQUEST-ID"),
            }
        )
        return

    consent_detail = notification.get("consentDetail")
    permission = consent_detail.get("permission")
    frequency = permission.get("frequency")

    hf_id = headers.get("X-HIP-ID") or consent_detail.get("hip").get("id")
    health_facility = HealthFacility.objects.filter(hf_id=hf_id).first()

    if not health_facility:
        message = f"Health Facility with ID: {hf_id} not found in the database"
        raise CallbackProcessingError(message)

    patient = _consented_patient(consent_detail)

    ConsentArtefact.objects.update_or_create(
        consent_id=notification.get("consentId"),
        defaults={
            "health_facility": health_facility,
            "abha_address": normalize_abha_address(
                consent_detail.get("patient").get("id")
            ),
            "patient": patient,
            "care_contexts": consent_detail.get("careContexts"),
            "status": notification.get("status"),
            "purpose": consent_detail.get("purpose").get("code"),
            "hi_types": consent_detail.get("hiTypes"),
            "hip": consent_detail.get("hip").get("id"),
            "cm": consent_detail.get("consentManager").get("id"),
            "requester": get_or_create_abdm_user(),
            "access_mode": permission.get("accessMode"),
            "from_time": permission.get("dateRange").get("from"),
            "to_time": permission.get("dateRange").get("to"),
            "expiry": permission.get("dataEraseAt"),
            "frequency_unit": frequency.get("unit"),
            "frequency_value": frequency.get("value"),
            "frequency_repeats": frequency.get("repeats"),
            "signature": notification.get("signature"),
        },
    )

    GatewayService.consent__request__hip__on_notify(
        {
            "consent_id": str(notification.get("consentId")),
            "request_id": headers.get("REQUEST-ID"),
        }
    )


def _health_information_request_error(consent: ConsentArtefact, date_range: dict):
    if consent.status == Status.EXPIRED or (
        consent.expiry and consent.expiry <= care_now()
    ):
        return {
            "code": "ABDM-1112",
            "message": "Invalid or already expired consent artefact id",
        }

    if consent.status != Status.GRANTED:
        return {"code": "ABDM-1062", "message": "Consent Not granted"}

    requested_from, requested_to = date_range.get("from"), date_range.get("to")
    if (
        requested_from > requested_to
        or (consent.from_time and requested_from < consent.from_time)
        or (consent.to_time and requested_to > consent.to_time)
    ):
        return {"code": "ABDM-1063", "message": "Date Range given is invalid"}

    return None


def handle_health_information_request(validated_data: dict, headers: dict):
    hi_request = validated_data.get("hiRequest")
    key_material = hi_request.get("keyMaterial")

    consent = ConsentArtefact.objects.filter(
        consent_id=hi_request.get("consent").get("id")
    ).first()

    if not consent:
        raise CallbackProcessingError(
            f"Consent with ID: {hi_request.get('consent').get('id')} not found in the database"
        )

    error = _health_information_request_error(consent, hi_request.get("dateRange"))

    GatewayService.data_flow__health_information__hip__on_request(
        {
            "request_id": headers.get("REQUEST-ID"),
            "transaction_id": str(validated_data.get("transactionId")),
            "error": error,
        }
    )

    if error:
        logger.warning(
            "Refused health information request %s for consent %s: %s",
            validated_data.get("transactionId"),
            consent.consent_id,
            error["message"],
        )
        return

    try:
        transfer = GatewayService.data_flow__health_information__transfer(
            {
                "transaction_id": str(validated_data.get("transactionId")),
                "consent": consent,
                "url": hi_request.get("dataPushUrl"),
                "date_range": hi_request.get("dateRange"),
                "key_material__crypto_algorithm": key_material.get("cryptoAlg"),
                "key_material__curve": key_material.get("curve"),
                "key_material__public_key": key_material.get("dhPublicKey").get(
                    "keyValue"
                ),
                "key_material__nonce": key_material.get("nonce"),
            }
        )

        GatewayService.data_flow__health_information__notify(
            {
                "consent": consent,
                "consent_id": str(consent.consent_id),
                "transaction_id": str(validated_data.get("transactionId")),
                "notifier__type": "HIP",
                "notifier__id": headers.get("X-HIP-ID"),
                "status": "TRANSFERRED",
                "hip_id": headers.get("X-HIP-ID"),
                "errored_care_context_references": transfer[
                    "errored_care_context_references"
                ],
                "skipped_care_context_references": transfer[
                    "skipped_care_context_references"
                ],
            }
        )
    except Exception as exception:
        logger.error(
            f"Error occurred while transferring health information: {exception!s}"
        )

        GatewayService.data_flow__health_information__notify(
            {
                "consent": consent,
                "consent_id": str(consent.consent_id),
                "transaction_id": str(validated_data.get("transactionId")),
                "notifier__type": "HIP",
                "notifier__id": headers.get("X-HIP-ID"),
                "status": "FAILED",
                "hip_id": headers.get("X-HIP-ID"),
            }
        )


def handle_patient_share(validated_data: dict, headers: dict):
    hip_id = validated_data.get("metaData").get("hipId")
    health_facility = HealthFacility.objects.filter(hf_id=hip_id).first()

    if not health_facility:
        GatewayService.patient_share__on_share(
            {
                "error": {
                    "message": "HIP is not available",
                    "code": "ABDM-9999",
                },
                "request_id": headers.get("REQUEST-ID"),
            }
        )

        raise CallbackProcessingError(
            f"Health Facility with ID: {hip_id} not found in the database"
        )

    patient_data = validated_data.get("profile").get("patient")
    # ObjectLocked propagates to the task which retries shortly after
    abha_number, _, _ = record_abha_auth(
        abha_number=patient_data.get("abhaNumber"),
        abha_address=patient_data.get("abhaAddress"),
        profile={
            "name": patient_data.get("name"),
            "gender": patient_data.get("gender"),
            "date_of_birth": validate_and_format_date(
                patient_data.get("yearOfBirth"),
                patient_data.get("monthOfBirth"),
                patient_data.get("dayOfBirth"),
            ),
            "address": patient_data.get("address", {}).get("line"),
            "district": patient_data.get("address", {}).get("district"),
            "state": patient_data.get("address", {}).get("state"),
            "pincode": patient_data.get("address", {}).get("pinCode"),
            "mobile": patient_data.get("phoneNumber"),
        },
        health_facility=health_facility,
        method=AbhaAuthMethod.SCAN_AND_SHARE,
    )

    is_existing_patient = True
    if not abha_number.patient_id:
        # ObjectLocked propagates to the task which retries shortly after
        lock = PatientCreateLock()
        lock.acquire()

        try:
            with transaction.atomic():
                abha_number.refresh_from_db()
                if not abha_number.patient_id:
                    is_existing_patient = False
                    full_address = ", ".join(
                        filter(
                            lambda x: x,
                            [
                                patient_data.get("address").get("line"),
                                patient_data.get("address").get("district"),
                                patient_data.get("address").get("state"),
                                patient_data.get("address").get("pinCode"),
                            ],
                        )
                    )
                    phone_number = (
                        "+91"
                        + patient_data.get("phoneNumber", "").replace(" ", "")[-10:]
                    )
                    date_of_birth = datetime.strptime(
                        f"{patient_data.get('yearOfBirth')}-{patient_data.get('monthOfBirth', 1):02d}-{patient_data.get('dayOfBirth', 1):02d}",
                        "%Y-%m-%d",
                    ).date()

                    state_name = patient_data.get("address", {}).get("state")
                    state_organization = None
                    if state_name:
                        state_organization = Organization.objects.filter(
                            name__iexact=state_name,
                            org_type=OrganizationTypeChoices.govt.value,
                            metadata__govt_org_type="state",
                        ).first()

                    district_organization = None
                    district_name = patient_data.get("address", {}).get("district")
                    if state_organization and district_name:
                        district_organization = Organization.objects.filter(
                            name__iexact=district_name,
                            org_type=OrganizationTypeChoices.govt.value,
                            parent=state_organization,
                            metadata__govt_org_type="district",
                        ).first()

                    # TODO: consider the case of existing patient without abha number
                    patient = Patient.objects.create(
                        name=patient_data.get("name"),
                        gender={
                            "M": GenderChoices.male,
                            "F": GenderChoices.female,
                            "O": GenderChoices.non_binary,
                        }.get(patient_data.get("gender"), "O"),
                        date_of_birth=date_of_birth,
                        phone_number=phone_number,
                        emergency_phone_number=phone_number,
                        address=full_address,
                        permanent_address=full_address,
                        pincode=patient_data.get("address").get("pinCode"),
                        geo_organization=district_organization
                        if district_organization
                        else state_organization,
                    )
                    evaluate_patient_instance_default_values(patient)
                    abha_number.patient = patient
                    abha_number.save(update_fields=["patient"])

                    if abha_number.abha_number:
                        abdm_user = get_or_create_abdm_user()
                        ensure_abdm_patient_identifier(
                            patient,
                            system=settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_SYSTEM,
                            display=settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_DISPLAY,
                            value=abha_number.abha_number,
                            created_by=abdm_user,
                        )

                    patient.build_instance_identifiers()
                    patient.save()

            transaction.on_commit(lock.release)
        except Exception:
            lock.release()
            raise

    patient = abha_number.patient

    token = get_or_create_scan_and_share_token(patient, health_facility.facility)

    GatewayService.patient_share__on_share(
        {
            "acknowledgement": {
                "status": "SUCCESS",
                "abha_address": patient_data.get("abhaAddress"),
                "context": validated_data.get("metaData").get("context"),
                "token_number": token.number,
                "expiry": settings.ABDM_SCAN_AND_SHARE_TOKEN_EXPIRY_TIME,
            },
            "request_id": headers.get("REQUEST-ID"),
        }
    )

    Transaction.objects.create(
        reference_id=uuid(),
        type=TransactionType.SCAN_AND_SHARE,
        meta_data={
            "abha_number": str(abha_number.external_id),
            "is_existing_patient": is_existing_patient,
            "token": str(token.external_id),
        },
    )

    if is_existing_patient:
        # records made here before this auth existed could not be linked until now
        transaction.on_commit(
            lambda: enqueue_link_patient_care_contexts(patient, health_facility)
        )
