"""Linking care contexts to a patient's ABHA address at a health facility.

``link_care_contexts`` is the one routine behind every link. It runs when a
record becomes ready and when an ABHA auth is made. ``CareContext`` is the ledger
of what was sent where, and ``GatewayService`` only makes the HTTP calls.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

import jwt
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from abdm.extensions import linking_opted_out
from abdm.models import (
    AbhaAuth,
    AbhaAuthInvalidationReason,
    AbhaNumber,
    CareContext,
    CareContextStatus,
    CareContextVia,
    HealthFacility,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from abdm.models.abha_auth import normalize_abha_address
from abdm.service.abha import invalidate_auths, latest_valid_auth, valid_auths
from abdm.service.helper import (
    ABDMAPIException,
    care_context_dict_from_reference_id,
    create_encounter_care_context,
    ready_care_contexts_for_encounter,
    resolve_care_context,
    timestamp,
    uuid,
)
from abdm.service.v3.gateway import GatewayService
from abdm.settings import plugin_settings as settings
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.utils.lock import Lock, ObjectLocked

logger = logging.getLogger(__name__)

LINK_BATCH_SIZE = 20

GENERATE_TOKEN_THROTTLE = 60 * 30
GENERATE_TOKEN_THROTTLE_KEY = "abdm_generate_token__{hf_id}__{abha_address}"
# the on-generate-token callback need not name the address when it reports an error
GENERATE_TOKEN_REQUEST_KEY = "abdm_generate_token_request__{request_id}"
GENERATE_TOKEN_REQUEST_TTL = 60 * 60

# used when the link token does not carry its own expiry
LINK_TOKEN_LIFETIME = timedelta(days=180)
LINK_TOKEN_ERROR_CODES = ("ABDM-1026", "ABDM-1038")

# ABDM-1056 is also "Invalid Link Reference Number", so the message decides
ALREADY_LINKED_ERROR_CODE = "ABDM-1056"
ALREADY_LINKED_MESSAGE = re.compile(r"already\s+(been\s+)?linked", re.IGNORECASE)
# arrives as ABDM-9999 or as ABDM-1090, so only the message tells
DUPLICATE_LINK_REQUEST_MESSAGE = re.compile(
    r"duplicate\s+hip\s+link\s+request", re.IGNORECASE
)

# a link request whose callback never arrived is sent again after this long
STALE_LINK_REQUEST_AGE = timedelta(days=1)

# long enough to send every batch of an auth with the gateway at its slowest
SEND_LOCK_TIMEOUT = 60 * 10
SEND_LOCK_KEY = "abdm:send_care_contexts:{auth_id}"
SEND_PASSES = 3

# the deep link SMS goes out at most once per patient and facility in a day
DEEP_LINK_SMS_THROTTLE = 60 * 60 * 24
DEEP_LINK_SMS_THROTTLE_KEY = "abdm_deep_link_sms__{health_facility}__{patient}__{day}"
DEEP_LINK_SMS_REQUEST_KEY = "abdm_deep_link_sms_request__{request_id}"
CARE_CONTEXT_NOTIFY_REQUEST_KEY = "abdm_care_context_notify_request__{request_id}"
# how long the outcome of a notify call is matched to what it was made for
NOTIFY_REQUEST_TTL = 60 * 60 * 24

# an Indian mobile number, with or without the country code or a leading zero
MOBILE_NUMBER = re.compile(r"(?:\+?91|0)?([6-9]\d{9})")

# statuses in which a reference is taken, and must not be linked a second time
ACTIVE_STATUSES = (
    CareContextStatus.PENDING,
    CareContextStatus.REQUESTED,
    CareContextStatus.LINKED,
)


def is_linking_opted_out(encounter: Encounter) -> bool:
    """Whether the records of this encounter are to be kept out of ABDM.

    The patient's choice is held in the ``abdm`` extension of the encounter,
    where it is recorded as the encounter is created.
    """
    return linking_opted_out(encounter.extensions)


def _mobile_number(patient: Patient) -> str | None:
    match = MOBILE_NUMBER.fullmatch(re.sub(r"[\s()-]", "", patient.phone_number or ""))
    return match.group(1) if match else None


def notify_patient_without_auth(patient: Patient, health_facility: HealthFacility):
    """Called when records are ready to link and the patient has no ABHA auth at
    the facility.

    Asks ABDM to text the patient a deep link, with which they can link the
    records from a PHR app. At most one text goes out per patient and facility
    in a calendar day, and none when ``ABDM_DEEP_LINK_SMS_ENABLED`` is off or the
    patient has no mobile number. Not being able to send it is not an error.
    """
    if not settings.ABDM_DEEP_LINK_SMS_ENABLED:
        return

    mobile_number = _mobile_number(patient)
    if not mobile_number:
        return

    request_id = uuid()
    # taken before the call and kept whatever comes of it: a call that timed out
    # may still have sent the text. Without the cache nothing is sent
    if not cache.add(
        DEEP_LINK_SMS_THROTTLE_KEY.format(
            health_facility=health_facility.external_id,
            patient=patient.external_id,
            day=timezone.localdate().isoformat(),
        ),
        request_id,
        timeout=DEEP_LINK_SMS_THROTTLE,
    ):
        return

    try:
        cache.set(
            DEEP_LINK_SMS_REQUEST_KEY.format(request_id=request_id),
            {
                "patient": str(patient.external_id),
                "health_facility": str(health_facility.external_id),
            },
            timeout=NOTIFY_REQUEST_TTL,
        )
        GatewayService.link__patient__links__sms__notify2(
            {
                "health_facility": health_facility,
                "phone_number": mobile_number,
                "request_id": request_id,
            }
        )
    except Exception as exception:
        # ABDM's message is left out, it may repeat the number
        logger.warning(
            "The deep link SMS for patient %s at health facility %s was not sent: %s",
            patient.external_id,
            health_facility.external_id,
            type(exception).__name__,
        )


def complete_deep_link_sms(request_id: str | None, error: dict | None = None):
    """Record the outcome ABDM reported for a deep link SMS."""
    if not error:
        return

    sent_for = (
        cache.get(DEEP_LINK_SMS_REQUEST_KEY.format(request_id=request_id))
        if request_id
        else None
    ) or {}
    logger.warning(
        "ABDM could not send the deep link SMS of request %s, for patient %s at "
        "health facility %s: %s",
        request_id,
        sent_for.get("patient"),
        sent_for.get("health_facility"),
        error.get("code"),
    )


def is_link_token_error(code: str | None, message: str | None) -> bool:
    # ABDM reuses some codes for unrelated messages, so the message decides
    # where the code alone cannot
    return code in LINK_TOKEN_ERROR_CODES or "link token" in (message or "").lower()


def is_already_linked_error(code: str | None, message: str | None) -> bool:
    # the code comes bare or with a trailing ": "
    code = str(code or "").strip().rstrip(":").strip()
    return code == ALREADY_LINKED_ERROR_CODE and bool(
        ALREADY_LINKED_MESSAGE.search(message or "")
    )


def is_duplicate_link_error(message: str | None) -> bool:
    return bool(DUPLICATE_LINK_REQUEST_MESSAGE.search(message or ""))


def ready_care_contexts(patient: Patient, health_facility: HealthFacility) -> list:
    """Every care context of the patient at the facility that is ready to link."""
    care_contexts = {}

    encounters = Encounter.objects.filter(
        patient=patient, facility=health_facility.facility
    )
    for encounter in encounters:
        if is_linking_opted_out(encounter):
            continue

        for care_context in ready_care_contexts_for_encounter(encounter):
            care_contexts.setdefault(care_context["reference"], care_context)

    return list(care_contexts.values())


def discoverable_care_contexts(
    patient: Patient, health_facility: HealthFacility, abha_address: str
) -> list:
    """The care contexts to offer a patient who is looking for their records."""
    care_contexts = ready_care_contexts(patient, health_facility)

    held = set(
        CareContext.objects.filter(
            health_facility=health_facility,
            abha_address=normalize_abha_address(abha_address),
            reference__in=[care_context["reference"] for care_context in care_contexts],
        )
        # a link that failed is not held, the patient can still make it
        .exclude(status=CareContextStatus.FAILED)
        .values_list("reference", flat=True)
    )
    return [
        care_context
        for care_context in care_contexts
        if care_context["reference"] not in held
    ]


def _linkable_care_contexts(patient, health_facility, care_contexts: list) -> list:
    linkable = {}

    for care_context in care_contexts:
        record = resolve_care_context(
            care_context["reference"], patient, health_facility.facility
        )
        if not record:
            logger.warning(
                "Not linking care context %s, it is not a record of patient %s at "
                "health facility %s",
                care_context["reference"],
                patient.external_id,
                health_facility.external_id,
            )
            continue

        if record.opted_out:
            continue

        linkable.setdefault(care_context["reference"], care_context)

    return list(linkable.values())


def _take_reference(auth: AbhaAuth, patient: Patient, care_context: dict):
    # the unique constraint counts soft-deleted rows too
    row, created = CareContext._base_manager.get_or_create(  # noqa: SLF001
        health_facility=auth.health_facility,
        abha_address=auth.abha_address,
        reference=care_context["reference"],
        defaults={
            "patient": patient,
            "abha_auth": auth,
            "hi_type": care_context["hi_type"],
            "status": CareContextStatus.PENDING,
            "via": CareContextVia.HIP_INITIATED,
        },
    )
    if created or (not row.deleted and row.status in ACTIVE_STATUSES):
        return

    # a link that failed earlier, or was dropped, is taken over by this auth
    row.patient = patient
    row.abha_auth = auth
    row.hi_type = care_context["hi_type"]
    row.status = CareContextStatus.PENDING
    row.via = CareContextVia.HIP_INITIATED
    row.request_id = None
    row.legacy = False
    row.deleted = False
    row.save()


def link_care_contexts(
    patient: Patient,
    health_facility: HealthFacility,
    care_contexts: list | None = None,
    user=None,
):
    """Link care contexts of a patient at a health facility to their ABHA address.

    ``care_contexts`` is a list of care context dicts (``reference``, ``display``,
    ``hi_type``). Left out, every care context that is ready to link is taken.

    Linking uses the latest valid ABHA auth made at that facility. Without one
    nothing is linked.
    """
    if care_contexts is None:
        care_contexts = ready_care_contexts(patient, health_facility)
    else:
        care_contexts = _linkable_care_contexts(patient, health_facility, care_contexts)

    if not care_contexts:
        return

    taken = set(
        CareContext.objects.filter(
            health_facility=health_facility,
            patient=patient,
            status__in=ACTIVE_STATUSES,
            reference__in=[care_context["reference"] for care_context in care_contexts],
        ).values_list("reference", flat=True)
    )
    care_contexts = [
        care_context
        for care_context in care_contexts
        if care_context["reference"] not in taken
    ]

    auth = latest_valid_auth(
        AbhaNumber.objects.filter(patient=patient).first(), health_facility
    )
    if not auth:
        if care_contexts:
            notify_patient_without_auth(patient, health_facility)
        return

    with transaction.atomic():
        for care_context in care_contexts:
            _take_reference(auth, patient, care_context)

    # also sends what an earlier attempt left pending under this auth
    send_pending_care_contexts(auth, user=user)


def _same_link(auth: AbhaAuth):
    """Valid auths a link token is shared between: it is issued to a facility for
    an ABHA address, and checked against the demographics of one ABHA record."""
    return valid_auths().filter(
        abha_number_id=auth.abha_number_id,
        health_facility_id=auth.health_facility_id,
        abha_address=auth.abha_address,
    )


def _usable_link_token(auth: AbhaAuth) -> str | None:
    if auth.has_usable_link_token:
        return auth.link_token

    holder = (
        _same_link(auth)
        .filter(link_token__isnull=False, link_token_expires_at__gt=timezone.now())
        .order_by("-created_date", "-id")
        .first()
    )
    if not holder:
        return None

    auth.link_token = holder.link_token
    auth.link_token_expires_at = holder.link_token_expires_at
    auth.save(update_fields=["link_token", "link_token_expires_at", "modified_date"])
    return auth.link_token


def _discard_link_token(auth: AbhaAuth):
    _same_link(auth).update(
        link_token=None, link_token_expires_at=None, modified_date=timezone.now()
    )
    auth.link_token = None
    auth.link_token_expires_at = None


def link_token_expiry(link_token: str) -> datetime:
    try:
        claims = jwt.decode(link_token, options={"verify_signature": False})
        return datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, OverflowError):
        return timezone.now() + LINK_TOKEN_LIFETIME


def request_link_token(auth: AbhaAuth) -> bool:
    """Ask ABDM for a link token. The token arrives on the on-generate-token callback.

    At most one request goes out per facility and address in 30 minutes.
    """
    throttle_key = GENERATE_TOKEN_THROTTLE_KEY.format(
        hf_id=auth.health_facility.hf_id, abha_address=auth.abha_address
    )
    if cache.get(throttle_key):
        return False

    request_id = uuid()
    cache.set(
        GENERATE_TOKEN_REQUEST_KEY.format(request_id=request_id),
        auth.pk,
        timeout=GENERATE_TOKEN_REQUEST_TTL,
    )
    GatewayService.token__generate_token({"abha_auth": auth, "request_id": request_id})
    cache.set(throttle_key, timestamp(), timeout=GENERATE_TOKEN_THROTTLE)

    return True


def _care_context_dict(row: CareContext) -> dict | None:
    try:
        return care_context_dict_from_reference_id(row.reference)
    except (ValidationError, ValueError):
        return None


def _link_request_batches(rows: list) -> list:
    """Batches of at most 20 care contexts of one patient.

    Rows that already carry a request id are a request that did not get through;
    they go out again together, under the same id. The request id of a backfilled
    row is from before the ledger, and is not used again.
    """
    batches = {}
    for row in rows:
        if row.request_id and not row.legacy:
            batches.setdefault((row.patient_id, row.request_id), []).append(row)
            continue

        key = (row.patient_id, 0)
        while len(batches.get(key, [])) >= LINK_BATCH_SIZE:
            key = (row.patient_id, key[1] + 1)
        batches.setdefault(key, []).append(row)

    return list(batches.values())


def _send_one_at_a_time(auth: AbhaAuth, link_token: str, rows: list, user) -> bool:
    """Send each care context of a refused link request as a request of its own,
    so that the one ABDM already holds can be told apart from the rest.

    A request of one care context is settled without being split, so this never
    goes round. Returns False when the link token was refused.
    """
    CareContext.objects.filter(id__in=[row.id for row in rows]).update(
        request_id=None
    )
    for row in rows:
        row.request_id = None
        if not _send_link_request(auth, link_token, [row], user):
            return False
    return True


def _settle_duplicate_link_request(
    auth: AbhaAuth, link_token: str, rows: list, request_id: str, user, first_attempt
) -> bool:
    """ABDM refused a link request outright as a duplicate. Returns False when
    the link token was refused while settling it."""
    batch = CareContext.objects.filter(id__in=[row.id for row in rows])
    audit = Transaction.objects.filter(reference_id=request_id)
    now = timezone.now()

    if not first_attempt:
        # the request went out before under this id, and ABDM may be saying
        # that it has it: its callback, or the lack of one, tells
        batch.filter(status=CareContextStatus.PENDING).update(
            status=CareContextStatus.REQUESTED, modified_date=now
        )
        return True

    if len(rows) == 1:
        # ABDM holds this link already. It is done: asking again would be
        # refused every time
        batch.update(status=CareContextStatus.LINKED, modified_date=now)
        audit.update(status=TransactionStatus.COMPLETED)
        return True

    audit.update(status=TransactionStatus.FAILED)
    return _send_one_at_a_time(auth, link_token, rows, user)


def _send_link_request(auth: AbhaAuth, link_token: str, rows: list, user) -> bool:
    """Send one link request. Returns False when the link token was refused."""
    care_contexts = []
    for row in list(rows):
        care_context = _care_context_dict(row)
        if care_context:
            care_contexts.append(care_context)
            # the ledger holds the record type ABDM is told, which for an
            # encounter follows its class at this moment
            if row.hi_type != care_context["hi_type"]:
                row.hi_type = care_context["hi_type"]
                row.save(update_fields=["hi_type", "modified_date"])
            continue

        # the record is gone or no longer shareable, so there is nothing to link
        row.delete()
        rows.remove(row)

    if not rows:
        return True

    request_id = (not rows[0].legacy and rows[0].request_id) or uuid()
    batch = CareContext.objects.filter(id__in=[row.id for row in rows])
    batch.update(request_id=request_id)

    _, first_attempt = Transaction.objects.update_or_create(
        reference_id=request_id,
        defaults={"status": TransactionStatus.INITIATED},
        create_defaults={
            "type": TransactionType.LINK_CARE_CONTEXT,
            "status": TransactionStatus.INITIATED,
            "meta_data": {
                "abha_number": str(auth.abha_number.external_id),
                "type": "hip_initiated_linking",
                "care_contexts": [
                    care_context["reference"] for care_context in care_contexts
                ],
                "hf_id": auth.health_facility.hf_id,
            },
            "created_by": user,
        },
    )

    try:
        GatewayService.link__carecontext(
            {
                "abha_auth": auth,
                "patient": rows[0].patient,
                "care_contexts": care_contexts,
                "link_token": link_token,
                "request_id": request_id,
            }
        )
    except ABDMAPIException as exception:
        code = getattr(exception, "abdm_error_code", None)

        if is_duplicate_link_error(str(exception.detail)):
            return _settle_duplicate_link_request(
                auth, link_token, rows, request_id, user, first_attempt
            )

        Transaction.objects.filter(reference_id=request_id).update(
            status=TransactionStatus.FAILED
        )

        if is_link_token_error(code, str(exception.detail)):
            batch.update(request_id=None)
            _discard_link_token(auth)
            request_link_token(auth)
            return False

        batch.update(status=CareContextStatus.FAILED, modified_date=timezone.now())
        logger.warning(
            "ABDM refused link request %s of ABHA auth %s with %s",
            request_id,
            auth.external_id,
            code,
        )
        return True

    batch.filter(status=CareContextStatus.PENDING).update(
        status=CareContextStatus.REQUESTED, modified_date=timezone.now()
    )
    return True


def _send_lock(auth: AbhaAuth) -> Lock:
    # two workers sending the same care contexts would each make a link request,
    # and ABDM refuses the second as a duplicate
    return Lock(SEND_LOCK_KEY.format(auth_id=auth.pk), timeout=SEND_LOCK_TIMEOUT)


def send_pending_care_contexts(auth: AbhaAuth, user=None):
    """Send the care contexts waiting under an auth to ABDM.

    Without an unexpired link token, a token is requested instead and the care
    contexts stay pending until it arrives.
    """
    if auth.invalidated_at or not auth.abha_address:
        return

    lock = _send_lock(auth)
    try:
        lock.acquire()
    except ObjectLocked:
        return

    try:
        # goes round again for what another worker added while this one was sending
        for _ in range(SEND_PASSES):
            rows = list(
                CareContext.objects.filter(
                    abha_auth=auth, status=CareContextStatus.PENDING
                )
                .select_related("patient")
                .order_by("id")
            )
            if not rows:
                return

            link_token = _usable_link_token(auth)
            if not link_token:
                request_link_token(auth)
                return

            for batch in _link_request_batches(rows):
                if not _send_link_request(auth, link_token, batch, user):
                    return
    finally:
        lock.release()


def _auth_for_link_token(hf_id, abha_address, request_id) -> AbhaAuth | None:
    abha_address = normalize_abha_address(abha_address)

    if hf_id and abha_address:
        auth = (
            valid_auths()
            .filter(health_facility__hf_id=hf_id, abha_address=abha_address)
            .order_by("-created_date", "-id")
            .first()
        )
        if auth:
            return auth

    auth_id = request_id and cache.get(
        GENERATE_TOKEN_REQUEST_KEY.format(request_id=request_id)
    )
    return valid_auths().filter(pk=auth_id).first() if auth_id else None


def receive_link_token(
    *, hf_id, abha_address, link_token, request_id=None, error=None
) -> bool:
    """Handle the outcome of a link token request.

    Returns False when no valid auth is waiting for it.
    """
    auth = _auth_for_link_token(hf_id, abha_address, request_id)
    if not auth:
        return False

    if error:
        # the demographics held no longer satisfy ABDM for this address at this
        # facility, which only a fresh verification can repair
        invalidate_auths(_same_link(auth), AbhaAuthInvalidationReason.LINK_TOKEN_REFUSED)
        logger.warning(
            "ABDM refused a link token for ABHA auth %s with %s",
            auth.external_id,
            error.get("code"),
        )
        return True

    if not link_token:
        return True

    auth.link_token = link_token
    auth.link_token_expires_at = link_token_expiry(link_token)
    auth.save(update_fields=["link_token", "link_token_expires_at", "modified_date"])

    waiting = _same_link(auth).filter(
        care_contexts__status=CareContextStatus.PENDING
    )
    for waiting_auth in [auth, *waiting.exclude(pk=auth.pk).distinct()]:
        send_pending_care_contexts(waiting_auth, user=get_or_create_abdm_user())

    return True


def _settle_already_linked(request_id: str, rows):
    """ABDM answered a link request by saying a care context is already linked.

    Raises ``ObjectLocked`` while the care contexts of the auth are being sent.
    """
    waiting = list(rows.select_related("abha_auth", "patient").order_by("id"))
    if not waiting:
        return

    audit = Transaction.objects.filter(reference_id=request_id)
    now = timezone.now()

    if len(waiting) == 1:
        # the link exists, which is what was asked for: it is done, not failed,
        # and is not asked for again
        rows.update(status=CareContextStatus.LINKED, modified_date=now)
        audit.update(status=TransactionStatus.COMPLETED)
        return

    # ABDM does not say which of them it holds, and marking them all linked
    # would leave the others out for good, so each is asked for on its own
    auth = waiting[0].abha_auth
    if (
        not auth
        or auth.invalidated_at
        or any(row.abha_auth_id != auth.id for row in waiting)
    ):
        rows.update(status=CareContextStatus.FAILED, modified_date=now)
        audit.update(status=TransactionStatus.FAILED)
        return

    lock = _send_lock(auth)
    lock.acquire()
    try:
        rows.update(
            status=CareContextStatus.PENDING, request_id=None, modified_date=now
        )
        audit.update(status=TransactionStatus.FAILED)

        link_token = _usable_link_token(auth)
        if not link_token:
            request_link_token(auth)
            return

        _send_one_at_a_time(auth, link_token, waiting, get_or_create_abdm_user())
    finally:
        lock.release()


def complete_link_request(request_id: str, error: dict | None = None):
    """Record the outcome ABDM reported for a link request."""
    rows = CareContext.objects.filter(
        request_id=request_id,
        status__in=[CareContextStatus.PENDING, CareContextStatus.REQUESTED],
    )
    now = timezone.now()

    if error and is_already_linked_error(error.get("code"), error.get("message")):
        _settle_already_linked(request_id, rows)
        return

    if error and is_link_token_error(error.get("code"), error.get("message")):
        auths = list(
            valid_auths().filter(
                id__in=rows.exclude(abha_auth=None).values_list("abha_auth_id")
            )
        )
        rows.update(
            status=CareContextStatus.PENDING, request_id=None, modified_date=now
        )
        Transaction.objects.filter(reference_id=request_id).update(
            status=TransactionStatus.FAILED
        )
        for auth in auths:
            _discard_link_token(auth)
            request_link_token(auth)
        return

    rows.update(
        status=CareContextStatus.FAILED if error else CareContextStatus.LINKED,
        modified_date=now,
    )
    Transaction.objects.filter(reference_id=request_id).update(
        status=TransactionStatus.FAILED if error else TransactionStatus.COMPLETED
    )


def _notify_linked_care_context(row: CareContext, hi_type: str) -> bool:
    """Tell ABDM that a linked care context changed, for the address and the
    facility it was linked under. Returns False when ABDM refused to be told."""
    request_id = uuid()
    cache.set(
        CARE_CONTEXT_NOTIFY_REQUEST_KEY.format(request_id=request_id),
        str(row.external_id),
        timeout=NOTIFY_REQUEST_TTL,
    )

    try:
        GatewayService.link__context__notify(
            {
                "hf_id": row.health_facility.hf_id,
                "abha_address": row.abha_address,
                "patient": row.patient,
                "reference": row.reference,
                "hi_type": hi_type,
                "request_id": request_id,
            }
        )
    except ABDMAPIException as exception:
        logger.warning(
            "ABDM refused the change notification of care context %s with %s",
            row.external_id,
            getattr(exception, "abdm_error_code", None),
        )
        return False

    return True


def _linked_care_contexts(patient, health_facility, reference: str):
    return CareContext.objects.filter(
        health_facility=health_facility,
        patient=patient,
        reference=reference,
        status=CareContextStatus.LINKED,
    ).select_related("health_facility", "patient")


def notify_care_context_updated(
    patient: Patient, health_facility: HealthFacility, reference: str
):
    """Tell ABDM that a linked care context has gained records, as a prescription
    does when a medication request is added on a day that is already linked."""
    record = resolve_care_context(reference, patient, health_facility.facility)
    if not record or record.opted_out:
        return

    for row in _linked_care_contexts(patient, health_facility, reference):
        _notify_linked_care_context(row, row.hi_type)


def sync_linked_record_type(encounter: Encounter):
    """Tell ABDM the record type of a linked encounter when its class has made
    it another one than it was linked as, and keep the ledger to it."""
    health_facility = HealthFacility.objects.filter(facility=encounter.facility).first()
    if not health_facility:
        return

    care_context = create_encounter_care_context(encounter)
    rows = _linked_care_contexts(
        encounter.patient, health_facility, care_context["reference"]
    ).exclude(hi_type=care_context["hi_type"])

    for row in rows:
        if _notify_linked_care_context(row, care_context["hi_type"]):
            row.hi_type = care_context["hi_type"]
            row.save(update_fields=["hi_type", "modified_date"])


def complete_care_context_notification(
    request_id: str | None, error: dict | None = None
):
    """Record the outcome ABDM reported for a care context change notification."""
    if not error:
        return

    care_context = (
        cache.get(CARE_CONTEXT_NOTIFY_REQUEST_KEY.format(request_id=request_id))
        if request_id
        else None
    )
    logger.warning(
        "ABDM could not take the change notification of request %s, for care "
        "context %s: %s",
        request_id,
        care_context,
        error.get("code"),
    )


def requeue_care_contexts() -> list[int]:
    """Make failed and unanswered link requests pending again.

    Returns the ids of the auths that have care contexts waiting to be sent.
    """
    sendable = CareContext.objects.filter(
        via=CareContextVia.HIP_INITIATED,
        abha_auth__invalidated_at__isnull=True,
        abha_auth__deleted=False,
    )
    now = timezone.now()

    sendable.filter(status=CareContextStatus.FAILED).update(
        status=CareContextStatus.PENDING, request_id=None, modified_date=now
    )
    sendable.filter(
        status=CareContextStatus.REQUESTED,
        modified_date__lt=now - STALE_LINK_REQUEST_AGE,
    ).update(status=CareContextStatus.PENDING, request_id=None, modified_date=now)

    return list(
        sendable.filter(status=CareContextStatus.PENDING)
        .order_by("abha_auth_id")
        .values_list("abha_auth_id", flat=True)
        .distinct()
    )


def record_user_initiated_links(
    *, patient, health_facility, abha_address, care_contexts: list, request_id=None
):
    """Record the care contexts a patient linked from their PHR app.

    They are linked to the address the patient asked with. No auth is involved.
    """
    abha_address = normalize_abha_address(abha_address)

    with transaction.atomic():
        for care_context in care_contexts:
            link = {
                "patient": patient,
                "abha_auth": None,
                "hi_type": care_context["hi_type"],
                "status": CareContextStatus.LINKED,
                "via": CareContextVia.USER_INITIATED,
                "request_id": request_id,
            }
            # the unique constraint counts soft-deleted rows too
            row, created = CareContext._base_manager.get_or_create(  # noqa: SLF001
                health_facility=health_facility,
                abha_address=abha_address,
                reference=care_context["reference"],
                defaults=link,
            )
            if created or (
                not row.deleted and row.status == CareContextStatus.LINKED
            ):
                continue

            for field, value in link.items():
                setattr(row, field, value)
            row.legacy = False
            row.deleted = False
            row.save()
