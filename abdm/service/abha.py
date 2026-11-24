"""ABHA identity and auth.

Identity is instance-wide: one ``AbhaNumber`` per person, found by ABHA number and
then by the ABHA address on a valid auth at any facility. Use of an ABHA is scoped
to a health facility, through the ``AbhaAuth`` made there.

``record_abha_auth`` is the only place that writes ``AbhaNumber`` and creates
``AbhaAuth``.
"""

import logging
import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException

from abdm.models import (
    AbhaAuth,
    AbhaAuthInvalidationReason,
    AbhaNumber,
    CareContext,
    CareContextStatus,
    HealthFacility,
)
from abdm.models.abha_auth import normalize_abha_address
from abdm.service.helper import ABDMInternalException
from care.emr.models.patient import Patient
from care.utils.lock import MultipleItemsLock

logger = logging.getLogger(__name__)

ABHA_NUMBER_DIGITS = 14

PROFILE_FIELDS = (
    "name",
    "first_name",
    "middle_name",
    "last_name",
    "gender",
    "date_of_birth",
    "address",
    "district",
    "state",
    "pincode",
    "mobile",
    "email",
    "profile_photo",
    "new",
)


class HealthFacilityNotRegistered(APIException):
    status_code = 400
    default_code = "health_facility_not_registered"
    default_detail = "This facility is not registered as a health facility with ABDM"


def health_facility_for_facility(facility_external_id) -> HealthFacility:
    """The health facility of a Care facility, by the facility's external id."""
    health_facility = (
        HealthFacility.objects.filter(facility_id=facility_external_id).first()
        if facility_external_id
        else None
    )
    if not health_facility:
        raise HealthFacilityNotRegistered
    return health_facility


def abha_number_variants(abha_number: str | None) -> list[str]:
    """The spellings one ABHA number is stored under: as given, bare and hyphenated."""
    abha_number = (abha_number or "").strip()
    if not abha_number:
        return []

    variants = {abha_number}
    digits = re.sub(r"\D", "", abha_number)
    if len(digits) == ABHA_NUMBER_DIGITS:
        variants.add(digits)
        variants.add(f"{digits[:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:]}")
    return sorted(variants)


def is_same_abha_number(first: str | None, second: str | None) -> bool:
    return bool(set(abha_number_variants(first)) & set(abha_number_variants(second)))


def valid_auths():
    return AbhaAuth.objects.filter(invalidated_at__isnull=True)


def latest_valid_auth(
    abha_number: AbhaNumber | None,
    health_facility: HealthFacility | None,
    *,
    require_address: bool = True,
) -> AbhaAuth | None:
    """The newest valid auth for that record at that facility.

    An auth made before the person had an ABHA address cannot link records or
    raise consent requests, so it is left out unless ``require_address`` is off.
    """
    if not abha_number or not health_facility:
        return None

    auths = valid_auths().filter(
        abha_number=abha_number, health_facility=health_facility
    )
    if require_address:
        auths = auths.exclude(abha_address="")
    return auths.order_by("-created_date", "-id").first()


def abha_number_for_number(abha_number: str | None) -> AbhaNumber | None:
    variants = abha_number_variants(abha_number)
    if not variants:
        return None

    return (
        AbhaNumber.objects.filter(abha_number__in=variants)
        .order_by(F("patient_id").asc(nulls_last=True), "id")
        .first()
    )


def abha_number_for_address(address: str | None) -> AbhaNumber | None:
    """The record of the newest valid auth with that address, at any facility."""
    address = normalize_abha_address(address)
    if not address:
        return None

    auth = (
        valid_auths()
        .filter(abha_address=address, abha_number__deleted=False)
        .select_related("abha_number")
        .order_by("-created_date", "-id")
        .first()
    )
    return auth.abha_number if auth else None


def find_abha_number(identifier: str | None) -> AbhaNumber | None:
    return abha_number_for_number(identifier) or abha_number_for_address(identifier)


def patient_for_abha(
    abha_number: str | None = None, abha_address: str | None = None
) -> Patient | None:
    for record in (
        abha_number_for_number(abha_number),
        abha_number_for_address(abha_address),
    ):
        if record and record.patient_id:
            return record.patient
    return None


def current_abha_address(
    abha_number: AbhaNumber, health_facility: HealthFacility | None = None
) -> str | None:
    """The address in use at the facility, else the one used most recently anywhere."""
    auth = latest_valid_auth(abha_number, health_facility)
    if not auth:
        auth = (
            valid_auths()
            .filter(abha_number=abha_number)
            .exclude(abha_address="")
            .order_by("-created_date", "-id")
            .first()
        )
    return auth.abha_address if auth else None


def invalidate_auths(auths, reason: AbhaAuthInvalidationReason) -> int:
    auth_ids = list(
        auths.filter(invalidated_at__isnull=True).values_list("id", flat=True)
    )
    if not auth_ids:
        return 0

    now = timezone.now()
    AbhaAuth.objects.filter(id__in=auth_ids).update(
        invalidated_at=now, invalidation_reason=reason, modified_date=now
    )
    # links still waiting under these auths will never be sent; failing them
    # lets a later auth take the references over
    CareContext.objects.filter(
        abha_auth_id__in=auth_ids, status=CareContextStatus.PENDING
    ).update(status=CareContextStatus.FAILED, modified_date=now)

    return len(auth_ids)


def _is_abha_number_held(abha_number: str, exclude_pk=None) -> bool:
    # the unique constraint counts soft-deleted rows too
    return (
        AbhaNumber._base_manager.filter(  # noqa: SLF001
            abha_number__in=abha_number_variants(abha_number)
        )
        .exclude(pk=exclude_pk)
        .exists()
    )


def _new_record(abha_number: str | None) -> AbhaNumber:
    if abha_number and _is_abha_number_held(abha_number):
        logger.warning(
            "A deleted ABHA record holds the ABHA number of a new record; "
            "the new record is created without it"
        )
        abha_number = None

    return AbhaNumber(abha_number=abha_number)


def _move_address(address: str, from_record: AbhaNumber, to_record=None):
    invalidated = invalidate_auths(
        valid_auths().filter(abha_number=from_record, abha_address=address),
        AbhaAuthInvalidationReason.ADDRESS_MOVED,
    )
    logger.warning(
        "An ABHA address moved from ABHA record %s to %s; %s auth(s) invalidated. "
        "The two records may be the same person",
        from_record.external_id,
        to_record.external_id if to_record else "a new ABHA record",
        invalidated,
    )


def _resolve_record(abha_number: str | None, address: str) -> tuple[AbhaNumber, bool]:
    by_number = abha_number_for_number(abha_number)
    by_address = abha_number_for_address(address)

    if by_number:
        # the number is the identity, so it wins over where the address was last seen
        if by_address and by_address.pk != by_number.pk:
            _move_address(address, by_address, by_number)
        return by_number, False

    if not by_address:
        return _new_record(abha_number), True

    if not abha_number or is_same_abha_number(by_address.abha_number, abha_number):
        return by_address, False

    if not by_address.abha_number:
        if _is_abha_number_held(abha_number, exclude_pk=by_address.pk):
            logger.warning(
                "ABHA record %s was not given its ABHA number, a deleted ABHA "
                "record holds it",
                by_address.external_id,
            )
        else:
            by_address.abha_number = abha_number
        return by_address, False

    _move_address(address, by_address)
    return _new_record(abha_number), True


def _update_profile(record: AbhaNumber, profile: dict | None, tokens: dict | None):
    for field in PROFILE_FIELDS:
        if field not in (profile or {}):
            continue
        value = profile[field]
        setattr(record, field, bool(value) if field == "new" else value)

    if tokens:
        record.access_token = tokens.get("access_token")
        record.refresh_token = tokens.get("refresh_token")


def _create_auth(record, address, health_facility, method, performed_by) -> AbhaAuth:
    auth = AbhaAuth(
        abha_number=record,
        health_facility=health_facility,
        abha_address=address,
        method=method,
        performed_by=performed_by,
    )

    if address:
        previous = (
            valid_auths()
            .filter(
                abha_number=record, health_facility=health_facility, abha_address=address
            )
            .order_by("-created_date", "-id")
            .first()
        )
        if previous and previous.has_usable_link_token:
            auth.link_token = previous.link_token
            auth.link_token_expires_at = previous.link_token_expires_at

    auth.save()
    return auth


def record_abha_auth(
    *,
    abha_number: str | None,
    abha_address: str | None,
    profile: dict | None,
    health_facility: HealthFacility,
    method: str,
    performed_by=None,
    tokens: dict | None = None,
) -> tuple[AbhaNumber, AbhaAuth, bool]:
    """Record that an ABHA was authenticated at a health facility.

    Finds or creates the ``AbhaNumber`` for the person, refreshes its profile and
    adds an ``AbhaAuth`` for the address presented. Returns the record, the auth
    and whether the record was created.

    Raises ``ObjectLocked`` while another auth for the same number or address is
    being recorded.
    """
    abha_number = (abha_number or "").strip() or None
    address = normalize_abha_address(abha_address)

    if not abha_number and not address:
        raise ABDMInternalException(
            detail="An ABHA number or an ABHA address is required"
        )
    if not health_facility:
        raise ABDMInternalException(detail="A health facility is required")

    lock_keys = []
    if abha_number:
        lock_keys.append(f"abdm:abha_number:{min(abha_number_variants(abha_number))}")
    if address:
        lock_keys.append(f"abdm:abha_address:{address}")

    lock = MultipleItemsLock(lock_keys)
    lock.acquire()

    try:
        with transaction.atomic():
            record, created = _resolve_record(abha_number, address)
            _update_profile(record, profile, tokens)
            record.save()

            auth = _create_auth(record, address, health_facility, method, performed_by)

        # held until the surrounding transaction commits, so that a concurrent
        # auth for the same person finds this record instead of creating another
        transaction.on_commit(lock.release)
    except Exception:
        lock.release()
        raise

    return record, auth, created
