import json
import logging

from abdm.models import ConsentArtefact, ConsentRequest, Transaction, TransactionType
from abdm.models.base import Status
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.service.v3.gateway import GatewayService
from abdm.utils.cipher import Cipher
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.file_upload import FileUpload
from care.emr.resources.file_upload.spec import FileCategoryChoices, FileTypeChoices

logger = logging.getLogger(__name__)


def handle_consent_request_on_init(validated_data: dict, headers: dict):
    request_id = validated_data.get("response").get("requestId")

    consent = ConsentRequest.objects.filter(external_id=request_id).first()

    if not consent:
        raise CallbackProcessingError(
            f"Consent Request: {request_id} not found in the database"
        )

    if "consentRequest" in validated_data and validated_data.get("consentRequest"):
        consent_id = validated_data.get("consentRequest").get("id")

        consent.consent_id = consent_id
        consent.save()

    if "error" in validated_data and validated_data.get("error"):
        logger.warning(
            f"Consent Request: {request_id}, Error in Consent Request while On Init: {validated_data.get('error').get('message')}"
        )


def handle_consent_request_on_status(validated_data: dict, headers: dict):
    consent_request = validated_data.get("consentRequest")
    consent_status = consent_request.get("status")
    consent_artefacts = consent_request.get("consentArtefacts")

    consent = ConsentRequest.objects.filter(
        consent_id=consent_request.get("id")
    ).first()

    if not consent:
        raise CallbackProcessingError(
            f"Consent Request: {consent_request.get('id')} not found in the database"
        )

    if consent_status != Status.DENIED:
        for artefact in consent_artefacts:
            consent_artefact = ConsentArtefact.objects.filter(
                external_id=artefact.get("id")
            ).first()

            if not consent_artefact:
                consent_artefact = ConsentArtefact.objects.create(
                    external_id=artefact.get("id"),
                    consent_request=consent,
                    **consent.consent_details_dict(),
                )

            consent_artefact.status = consent_status
            consent_artefact.save()

    consent.status = consent_status
    consent.save()


def handle_consent_request_notify(validated_data: dict, headers: dict):
    notification = validated_data.get("notification")
    consent_status = notification.get("status")
    consent_artefacts = notification.get("consentArtefacts", [])

    consent = ConsentRequest.objects.filter(
        consent_id=notification.get("consentRequestId")
    ).first()

    if not consent:
        raise CallbackProcessingError(
            f"Consent Request: {notification.get('consentRequestId')} not found in the database"
        )

    if consent_status != Status.DENIED:
        for artefact in consent_artefacts:
            consent_artefact = ConsentArtefact.objects.filter(
                external_id=artefact.get("id")
            ).first()

            if not consent_artefact:
                consent_artefact = ConsentArtefact.objects.create(
                    external_id=artefact.get("id"),
                    consent_request=consent,
                    **consent.consent_details_dict(),
                )

            consent_artefact.status = consent_status
            consent_artefact.save()

    consent.status = consent_status
    consent.save()

    if consent_status == Status.GRANTED:
        GatewayService.consent__request__hiu__on_notify(
            {
                "consent": consent,
                "request_id": headers.get("REQUEST-ID"),
            }
        )

        for artefact in consent.consent_artefacts.all():
            GatewayService.consent__fetch(
                {
                    "artefact": artefact,
                }
            )


def handle_consent_on_fetch(validated_data: dict, headers: dict):
    consent = validated_data.get("consent")
    consent_detail = consent.get("consentDetail")

    # updating an existing consent artefact
    (artefact, _) = ConsentArtefact.objects.update_or_create(
        external_id=consent_detail.get("consentId"),
        defaults={
            "hip": consent_detail.get("hip", {}).get("id"),
            "hiu": consent_detail.get("hiu", {}).get("id"),
            "cm": consent_detail.get("consentManager", {}).get("id"),
            "care_contexts": consent_detail.get("careContexts", []),
            "hi_types": consent_detail.get("hiTypes", []),
            "status": consent.get("status"),
            "access_mode": consent_detail.get("permission").get("accessMode"),
            "from_time": consent_detail.get("permission")
            .get("dateRange")
            .get("from"),
            "to_time": consent_detail.get("permission").get("dateRange").get("to"),
            "expiry": consent_detail.get("permission").get("dataEraseAt"),
            "frequency_unit": consent_detail.get("permission")
            .get("frequency")
            .get("unit"),
            "frequency_value": consent_detail.get("permission")
            .get("frequency")
            .get("value"),
            "frequency_repeats": consent_detail.get("permission")
            .get("frequency")
            .get("repeats"),
            "signature": consent.get("signature"),
        },
    )

    GatewayService.data_flow__health_information__request(
        {
            "artefact": artefact,
        }
    )


def handle_health_information_on_request(validated_data: dict, headers: dict):
    if "hiRequest" in validated_data:
        artefact = ConsentArtefact.objects.filter(
            consent_id=validated_data.get("response").get("requestId")
        ).first()

        if not artefact:
            raise CallbackProcessingError(
                f"Consent Artefact: {validated_data.get('response').get('requestId')} not found in the database"
            )

        artefact.consent_id = validated_data.get("hiRequest").get("transactionId")
        artefact.save()

    if "error" in validated_data:
        logger.warning(
            f"Consent Artefact: {validated_data.get('response').get('requestId')}, Error in Health Information Request: {validated_data.get('error')}"
        )


def handle_health_information_transfer(validated_data: dict, headers: dict):
    key_material = validated_data.get("keyMaterial")

    artefact = ConsentArtefact.objects.filter(
        consent_id=validated_data.get("transactionId")
    ).first()

    if not artefact:
        raise CallbackProcessingError(
            f"Consent Artefact: {validated_data.get('transactionId')} not found in the database"
        )

    cipher = Cipher(
        external_public_key=key_material.get("dhPublicKey").get("keyValue"),
        external_nonce=key_material.get("nonce"),
        internal_private_key=artefact.key_material_private_key,
        internal_public_key=artefact.key_material_public_key,
        internal_nonce=artefact.key_material_nonce,
    )

    entries = []
    for entry in validated_data.get("entries"):
        if "content" in entry:
            entries.append(
                {
                    "content": cipher.decrypt(entry.get("content")),
                    "care_context_reference": entry.get("careContextReference"),
                }
            )

        if "link" in entry:
            # TODO: handle link entry (link to raw data)
            pass

    file = FileUpload(
        internal_name=f"{validated_data.get('pageNumber')} / {validated_data.get('pageCount')} -- {artefact.external_id}.json",
        file_type=FileTypeChoices.patient.value,
        file_category=FileCategoryChoices.unspecified.value,
        associating_id=artefact.consent_request.external_id,
        created_by=get_or_create_abdm_user(),
    )
    file.files_manager.put_object(
        file, json.dumps(entries), ContentType="application/json"
    )
    file.upload_completed = True
    file.save(skip_internal_name=True)

    Transaction.objects.create(
        reference_id=validated_data.get("transactionId"),
        type=TransactionType.EXCHANGE_DATA,
        meta_data={
            "consent_artefact": str(artefact.external_id),
            "is_incoming": True,
        },
    )

    GatewayService.data_flow__health_information__notify(
        {
            "consent": artefact,
            "consent_id": str(artefact.artefact_id),
            "transaction_id": str(artefact.transaction_id),
            "notifier__type": "HIU",
            "notifier__id": artefact.hiu,
            "status": "TRANSFERRED",
            "hip_id": artefact.hip,
        }
    )
