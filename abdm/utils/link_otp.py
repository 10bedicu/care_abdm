import hashlib
import secrets
import string
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from django.conf import settings as django_settings
from django.core.cache import cache

from abdm.settings import plugin_settings
from care.utils import sms
from care.utils.lock import Lock, ObjectLocked

LINK_OTP_CACHE_PREFIX = "abdm_user_initiated_linking__"
LINK_OTP_SEND_COUNT_PREFIX = "abdm_link_otp_send__"
LINK_OTP_FAILURE_COUNT_PREFIX = "abdm_link_otp_failures__"
LINK_OTP_LOCKOUT_PREFIX = "abdm_link_otp_lockout__"
LINK_OTP_ACTIVE_SESSION_PREFIX = "abdm_link_otp_active__"
LINK_OTP_TTL = 300
LINK_OTP_LENGTH = 6


class LinkOtpDeliveryError(Exception):
    pass


class LinkOtpThrottled(Exception):
    pass


class LinkOtpSendRateLimited(Exception):
    pass


class LinkOtpAttemptsExceeded(Exception):
    pass


class LinkOtpLockContention(Exception):
    pass


class LinkOtpVerifyStatus(Enum):
    VALID = "valid"
    NOT_FOUND = "not_found"
    LOCKED_OUT = "locked_out"
    INVALID = "invalid"
    ATTEMPTS_EXCEEDED = "attempts_exceeded"
    THROTTLED = "throttled"


@dataclass(frozen=True)
class LinkOtpVerifyResult:
    status: LinkOtpVerifyStatus
    patient_id: str | None = None
    care_contexts: list | None = None


def generate_otp() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(LINK_OTP_LENGTH))


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def verify_otp(provided: str, stored_hash: str | None) -> bool:
    if not provided or not stored_hash:
        return False
    provided_hash = hash_otp(provided)
    return secrets.compare_digest(provided_hash, stored_hash)


def _build_cache_key(reference_id: str) -> str:
    return f"{LINK_OTP_CACHE_PREFIX}{reference_id}"


def _send_count_key(patient_id: str) -> str:
    return f"{LINK_OTP_SEND_COUNT_PREFIX}{patient_id}"


def _failure_count_key(patient_id: str) -> str:
    return f"{LINK_OTP_FAILURE_COUNT_PREFIX}{patient_id}"


def _lockout_key(patient_id: str) -> str:
    return f"{LINK_OTP_LOCKOUT_PREFIX}{patient_id}"


def _active_session_key(patient_id: str) -> str:
    return f"{LINK_OTP_ACTIVE_SESSION_PREFIX}{patient_id}"


def _increment_counter(key: str, ttl: int) -> int:
    if cache.add(key, 1, timeout=ttl):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=ttl)
        return 1


def _decrement_counter(key: str) -> None:
    try:
        value = cache.decr(key)
        if value <= 0:
            cache.delete(key)
    except ValueError:
        return


def is_locked_out(patient_id: str) -> bool:
    return cache.get(_lockout_key(patient_id)) is not None


def set_lockout(patient_id: str) -> None:
    cache.set(
        _lockout_key(patient_id),
        True,
        timeout=plugin_settings.ABDM_LINK_OTP_LOCKOUT_SECONDS,
    )


def check_and_increment_send_count(patient_id: str) -> None:
    ttl = plugin_settings.ABDM_LINK_OTP_SEND_WINDOW_SECONDS
    count = _increment_counter(_send_count_key(patient_id), ttl)
    if count > plugin_settings.ABDM_LINK_OTP_MAX_SENDS_PER_WINDOW:
        _decrement_counter(_send_count_key(patient_id))
        raise LinkOtpSendRateLimited()


def decrement_send_count(patient_id: str) -> None:
    _decrement_counter(_send_count_key(patient_id))


def increment_failure_count(patient_id: str) -> int:
    ttl = plugin_settings.ABDM_LINK_OTP_LOCKOUT_SECONDS
    return _increment_counter(_failure_count_key(patient_id), ttl)


def clear_failure_count(patient_id: str) -> None:
    cache.delete(_failure_count_key(patient_id))
    cache.delete(_lockout_key(patient_id))


def get_active_session(patient_id: str) -> str | None:
    return cache.get(_active_session_key(patient_id))


def set_active_session(patient_id: str, reference_id: str) -> None:
    cache.set(_active_session_key(patient_id), reference_id, timeout=LINK_OTP_TTL)


def clear_active_session(patient_id: str) -> None:
    cache.delete(_active_session_key(patient_id))


def delete_link_session_and_active(reference_id: str, patient_id: str) -> None:
    delete_link_session(reference_id)
    if get_active_session(patient_id) == reference_id:
        clear_active_session(patient_id)


def send_link_otp(phone_number: str, otp: str) -> None:
    if not django_settings.USE_SMS:
        raise LinkOtpDeliveryError("SMS delivery is not enabled")

    content = plugin_settings.ABDM_LINK_OTP_SMS_CONTENT.format(otp=otp)
    try:
        sent_count = sms.send_text_message(
            content=content,
            recipients=[phone_number],
        )
    except Exception as exc:
        raise LinkOtpDeliveryError("Failed to send OTP SMS") from exc

    if sent_count < 1:
        raise LinkOtpDeliveryError("Failed to send OTP SMS")


def create_link_session(
    reference_id: str,
    *,
    otp_hash: str,
    abha_address: str,
    patient_id: str,
    care_contexts: list,
    failed_attempts: int = 0,
) -> None:
    _save_link_session(
        reference_id,
        {
            "reference_id": reference_id,
            "otp_hash": otp_hash,
            "abha_address": abha_address,
            "patient_id": patient_id,
            "care_contexts": care_contexts,
            "failed_attempts": failed_attempts,
        },
    )


def _save_link_session(reference_id: str, session: dict) -> None:
    cache.set(_build_cache_key(reference_id), session, timeout=LINK_OTP_TTL)


def get_link_session(reference_id: str) -> dict | None:
    return cache.get(_build_cache_key(reference_id))


def delete_link_session(reference_id: str) -> None:
    cache.delete(_build_cache_key(reference_id))


def handle_failed_verify(session: dict) -> None:
    patient_id = session["patient_id"]
    reference_id = session["reference_id"]

    failed_attempts = session.get("failed_attempts", 0) + 1
    session["failed_attempts"] = failed_attempts
    _save_link_session(reference_id, session)

    failure_count = increment_failure_count(patient_id)
    if failure_count >= plugin_settings.ABDM_LINK_OTP_MAX_FAILURES:
        set_lockout(patient_id)
        delete_link_session(reference_id)
        clear_active_session(patient_id)
        raise LinkOtpThrottled()

    if failed_attempts >= plugin_settings.ABDM_LINK_OTP_MAX_VERIFY_ATTEMPTS:
        delete_link_session(reference_id)
        clear_active_session(patient_id)
        raise LinkOtpAttemptsExceeded()


def _rollback_new_session(
    patient_id: str,
    reference_id: str,
    previous_reference_id: str | None,
) -> None:
    delete_link_session(reference_id)
    if previous_reference_id:
        set_active_session(patient_id, previous_reference_id)
    else:
        clear_active_session(patient_id)


def init_link_otp(
    *,
    patient_id: str,
    phone_number: str,
    abha_address: str,
    care_contexts: list,
) -> str:
    reference_id = str(uuid4())

    try:
        with Lock(f"link_otp_send:{patient_id}"):
            if is_locked_out(patient_id):
                raise LinkOtpThrottled()

            check_and_increment_send_count(patient_id)

            previous_reference_id = get_active_session(patient_id)
            otp = generate_otp()

            create_link_session(
                reference_id,
                otp_hash=hash_otp(otp),
                abha_address=abha_address,
                patient_id=patient_id,
                care_contexts=care_contexts,
            )
            set_active_session(patient_id, reference_id)

            try:
                send_link_otp(phone_number, otp)
            except LinkOtpDeliveryError:
                _rollback_new_session(patient_id, reference_id, previous_reference_id)
                decrement_send_count(patient_id)
                raise

            if previous_reference_id:
                delete_link_session(previous_reference_id)
    except ObjectLocked as exc:
        raise LinkOtpLockContention from exc

    return reference_id


def rollback_link_otp_after_gateway_init_failure(
    patient_id: str,
    reference_id: str,
) -> None:
    delete_link_session(reference_id)
    clear_active_session(patient_id)


def verify_link_otp(*, reference_id: str, token: str) -> LinkOtpVerifyResult:
    try:
        with Lock(f"link_otp_verify:{reference_id}"):
            cached_data = get_link_session(reference_id)

            if not cached_data:
                return LinkOtpVerifyResult(status=LinkOtpVerifyStatus.NOT_FOUND)

            patient_id = cached_data.get("patient_id")
            if is_locked_out(patient_id):
                return LinkOtpVerifyResult(status=LinkOtpVerifyStatus.LOCKED_OUT)

            if verify_otp(token, cached_data.get("otp_hash")):
                return LinkOtpVerifyResult(
                    status=LinkOtpVerifyStatus.VALID,
                    patient_id=patient_id,
                    care_contexts=cached_data.get("care_contexts"),
                )

            try:
                handle_failed_verify(cached_data)
            except LinkOtpThrottled:
                return LinkOtpVerifyResult(status=LinkOtpVerifyStatus.THROTTLED)
            except LinkOtpAttemptsExceeded:
                return LinkOtpVerifyResult(status=LinkOtpVerifyStatus.ATTEMPTS_EXCEEDED)

            return LinkOtpVerifyResult(status=LinkOtpVerifyStatus.INVALID)
    except ObjectLocked as exc:
        raise LinkOtpLockContention from exc


def finalize_link_otp_confirmation(*, reference_id: str, patient_id: str) -> None:
    delete_link_session(reference_id)
    clear_active_session(patient_id)
    clear_failure_count(patient_id)
