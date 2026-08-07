import logging
from datetime import datetime

from django.contrib.postgres.search import TrigramSimilarity
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from abdm.models import (
    AbhaNumber,
    HealthFacility,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from abdm.models.consent import ConsentArtefact
from abdm.service.helper import uuid, validate_and_format_date
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.service.v3.gateway import GatewayService
from abdm.settings import plugin_settings as settings
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

logger = logging.getLogger(__name__)


def get_patient_by_abha_id(abha_id: str):
    patient = Patient.objects.filter(
        Q(abha_number__abha_number=abha_id) | Q(abha_number__health_id=abha_id)
    ).first()

    if not patient and "@" in abha_id:
        # TODO: get abha number using gateway api and search patient
        pass

    return patient


def handle_token_on_generate_token(validated_data: dict, headers: dict):
    hf_id = headers.get("X-HIP-ID")
    health_id = validated_data.get("abhaAddress")

    abha_number = AbhaNumber.objects.filter(health_id=health_id).first()

    if not abha_number:
        raise CallbackProcessingError(f"ABHA number {health_id} not found")

    cache.set(
        f"abdm_link_token__{hf_id}__{health_id}",
        validated_data.get("linkToken"),
        timeout=60 * 30,
    )

    link_care_context_request_cache_keys = cache.keys(
        f"abdm_link_care_context__{hf_id}__{health_id}__*"
    )

    for request_cache_key in link_care_context_request_cache_keys:
        cached_data = cache.get(request_cache_key)

        if cached_data.get("purpose") == "LINK_CARECONTEXT":
            GatewayService.link__carecontext(
                {
                    "reference_id": cached_data.get("reference_id"),
                    "patient": abha_number.patient,
                    "care_contexts": cached_data.get("care_contexts", []),
                    "user": get_or_create_abdm_user(),
                    "hf_id": cached_data.get("hf_id"),
                }
            )

            cache.delete(request_cache_key)


def handle_link_on_carecontext(validated_data: dict, headers: dict):
    request_id = validated_data.get("response", {}).get("requestId")

    Transaction.objects.filter(reference_id=request_id).update(
        status=TransactionStatus.COMPLETED
    )


def handle_patient_care_context_discover(validated_data: dict, headers: dict):
    patient_data = validated_data.get("patient", {})
    identifiers = [
        *(patient_data.get("verifiedIdentifiers", []) or []),
        *(patient_data.get("unverifiedIdentifiers", []) or []),
    ]

    health_id_number = next(
        filter(lambda x: x.get("type") == "ABHA_NUMBER", identifiers), {}
    ).get("value")
    patient = Patient.objects.filter(
        Q(abha_number__abha_number=health_id_number)
        | Q(abha_number__health_id=patient_data.get("id"))
    ).first()
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

    GatewayService.user_initiated_linking__patient__care_context__on_discover(
        {
            "transaction_id": str(validated_data.get("transactionId")),
            "request_id": headers.get("REQUEST-ID"),
            "patient": patient,
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

    GatewayService.user_initiated_linking__link__care_context__on_confirm(
        {
            "request_id": headers.get("REQUEST-ID"),
            "patient": patient,
            "care_contexts": cached_data.get("care_contexts"),
            "hf_id": headers.get("X-HIP-ID"),
        }
    )


def handle_consent_request_hip_notify(validated_data: dict, headers: dict):
    # TODO: handle the case where hip/notify is called before hip/on-init

    notification = validated_data.get("notification")
    consent_detail = notification.get("consentDetail")
    permission = consent_detail.get("permission")
    frequency = permission.get("frequency")

    patient = get_patient_by_abha_id(consent_detail.get("patient").get("id"))

    if not patient:
        raise CallbackProcessingError(
            f"Patient with ABHA ID: {consent_detail.get('patient').get('id')} not found in the database"
        )

    ConsentArtefact.objects.update_or_create(
        consent_id=notification.get("consentId"),
        defaults={
            "patient_abha": patient.abha_number,
            "care_contexts": consent_detail.get("careContexts"),
            "status": notification.get("status"),
            "purpose": consent_detail.get("purpose").get("code"),
            "hi_types": consent_detail.get("hiTypes"),
            "hip": consent_detail.get("hip").get("id"),
            "cm": consent_detail.get("consentManager").get("id"),
            "requester": get_or_create_abdm_user(),
            "access_mode": permission.get("accessMode"),
            "from_time": permission.get("dateRange").get("fromTime"),
            "to_time": permission.get("dateRange").get("toTime"),
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

    GatewayService.data_flow__health_information__hip__on_request(
        {
            "request_id": headers.get("REQUEST-ID"),
            "transaction_id": str(validated_data.get("transactionId")),
        }
    )

    try:
        GatewayService.data_flow__health_information__transfer(
            {
                "transaction_id": str(validated_data.get("transactionId")),
                "consent": consent,
                "url": hi_request.get("dataPushUrl"),
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
    abha_number = AbhaNumber.objects.filter(
        Q(health_id=patient_data.get("abhaAddress"))
        | (
            Q(abha_number=patient_data.get("abhaNumber"))
            & Q(abha_number__isnull=False)
        )
    ).first()
    (abha_number, created) = AbhaNumber.objects.update_or_create(
        pk=abha_number.pk if abha_number else None,
        defaults={
            "abha_number": patient_data.get("abhaNumber"),
            "health_id": patient_data.get("abhaAddress"),
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
                "abha_address": abha_number.health_id,
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
