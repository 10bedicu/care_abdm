import logging

import requests
from celery import shared_task

from abdm.models import CallbackStatus, CallbackType, InboundCallback
from care.utils.lock import ObjectLocked

logger = logging.getLogger(__name__)

LOCK_RETRY_COUNTDOWN = 2

RETRY_FOR = (ObjectLocked, requests.Timeout, requests.ConnectionError)

# redis broker priorities are inverted: 0 is consumed first (steps 0/3/6/9)
HIGH_PRIORITY = 0
LOW_PRIORITY = 6


def _dispatch_table():
    # built lazily to avoid circular imports at app load time
    from abdm.api.v3.serializers import hip as hip_serializers
    from abdm.api.v3.serializers import hiu as hiu_serializers
    from abdm.api.v3.serializers import scan_pay as scan_pay_serializers
    from abdm.service.v3.callback_handlers import hip, hiu, scan_pay

    return {
        CallbackType.TOKEN_ON_GENERATE_TOKEN: (
            hip_serializers.HipTokenOnGenerateTokenSerializer,
            hip.handle_token_on_generate_token,
        ),
        CallbackType.LINK_ON_CARECONTEXT: (
            hip_serializers.LinkOnCarecontextSerializer,
            hip.handle_link_on_carecontext,
        ),
        CallbackType.PATIENT_CARE_CONTEXT_DISCOVER: (
            hip_serializers.HipPatientCareContextDiscoverSerializer,
            hip.handle_patient_care_context_discover,
        ),
        CallbackType.LINK_CARE_CONTEXT_INIT: (
            hip_serializers.HipLinkCareContextInitSerializer,
            hip.handle_link_care_context_init,
        ),
        CallbackType.LINK_CARE_CONTEXT_CONFIRM: (
            hip_serializers.HipLinkCareContextConfirmSerializer,
            hip.handle_link_care_context_confirm,
        ),
        CallbackType.CONSENT_REQUEST_HIP_NOTIFY: (
            hip_serializers.ConsentRequestHipNotifySerializer,
            hip.handle_consent_request_hip_notify,
        ),
        CallbackType.HEALTH_INFORMATION_REQUEST: (
            hip_serializers.HipHealthInformationRequestSerializer,
            hip.handle_health_information_request,
        ),
        CallbackType.PATIENT_SHARE: (
            hip_serializers.HipPatientShareSerializer,
            hip.handle_patient_share,
        ),
        CallbackType.CONSENT_REQUEST_ON_INIT: (
            hiu_serializers.HiuConsentRequestOnInitSerializer,
            hiu.handle_consent_request_on_init,
        ),
        CallbackType.CONSENT_REQUEST_ON_STATUS: (
            hiu_serializers.HiuConsentRequestOnStatusSerializer,
            hiu.handle_consent_request_on_status,
        ),
        CallbackType.CONSENT_REQUEST_NOTIFY: (
            hiu_serializers.HiuConsentRequestNotifySerializer,
            hiu.handle_consent_request_notify,
        ),
        CallbackType.CONSENT_ON_FETCH: (
            hiu_serializers.HiuConsentOnFetchSerializer,
            hiu.handle_consent_on_fetch,
        ),
        CallbackType.HEALTH_INFORMATION_ON_REQUEST: (
            hiu_serializers.HiuHealthInformationOnRequestSerializer,
            hiu.handle_health_information_on_request,
        ),
        CallbackType.HEALTH_INFORMATION_TRANSFER: (
            hiu_serializers.HiuHealthInformationTransferSerializer,
            hiu.handle_health_information_transfer,
        ),
        CallbackType.PATIENT_SHARE_OPEN_ORDER: (
            scan_pay_serializers.PatientShareOpenOrderSerializer,
            scan_pay.handle_patient_share_open_order,
        ),
        CallbackType.PATIENT_SELECTION: (
            scan_pay_serializers.PatientSelectionSerializer,
            scan_pay.handle_patient_selection,
        ),
        CallbackType.PATIENT_SCAN_PAY_ON_NOTIFY: (
            scan_pay_serializers.PatientScanPayOnNotifySerializer,
            scan_pay.handle_scan_pay_on_notify,
        ),
        CallbackType.PATIENT_SCAN_PAY_ORDER_STATUS: (
            scan_pay_serializers.PatientScanPayOrderStatusSerializer,
            scan_pay.handle_scan_pay_order_status,
        ),
    }


# callbacks where a patient is actively waiting on their PHR app for a response
INTERACTIVE_CALLBACK_TYPES = {
    CallbackType.PATIENT_SHARE,
    CallbackType.PATIENT_SHARE_OPEN_ORDER,
    CallbackType.PATIENT_SELECTION,
    CallbackType.PATIENT_SCAN_PAY_ON_NOTIFY,
    CallbackType.PATIENT_SCAN_PAY_ORDER_STATUS,
}


def enqueue_inbound_callback(callback: InboundCallback):
    priority = (
        HIGH_PRIORITY
        if callback.callback_type in INTERACTIVE_CALLBACK_TYPES
        else LOW_PRIORITY
    )

    process_inbound_callback.apply_async(args=[callback.pk], priority=priority)


@shared_task(
    bind=True,
    name="abdm.process_inbound_callback",
    autoretry_for=RETRY_FOR,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def process_inbound_callback(self, callback_id: int):
    callback = InboundCallback.objects.filter(pk=callback_id).first()
    if callback is None:
        logger.warning("ABDM inbound callback %s not found; skipping", callback_id)
        return

    if callback.status == CallbackStatus.COMPLETED:
        logger.info("ABDM inbound callback %s already completed; skipping", callback_id)
        return

    callback.mark_processing()
    logger.info(
        "Processing ABDM inbound callback id=%s type=%s request_id=%s attempt=%s",
        callback.pk,
        callback.callback_type,
        callback.request_id,
        callback.attempts,
    )

    try:
        serializer_class, handler = _dispatch_table()[callback.callback_type]

        serializer = serializer_class(data=callback.payload)
        serializer.is_valid(raise_exception=True)

        handler(serializer.validated_data, callback.headers or {})
    except ObjectLocked as exc:
        logger.warning(
            "ABDM inbound callback %s blocked on lock; retrying", callback.pk
        )
        raise self.retry(exc=exc, countdown=LOCK_RETRY_COUNTDOWN) from exc
    except Exception as exc:
        callback.mark_failed(f"{type(exc).__name__}: {exc}")
        logger.exception(
            "ABDM inbound callback %s failed on attempt %s",
            callback.pk,
            callback.attempts,
        )
        raise

    callback.mark_completed()
    logger.info("ABDM inbound callback %s completed", callback.pk)
