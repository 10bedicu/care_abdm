import logging
from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)

from abdm.models import AbhaNumber
from abdm.service.v3.phr.phr_profile import PhrProfileService
from abdm.settings import plugin_settings as settings

PHR_ACCESS_TOKEN_PREFIX = "phr_access_token:"
PHR_REFRESH_TOKEN_PREFIX = "phr_refresh_token:"
PHR_ACCESS_TOKEN_CACHE_TIMEOUT = 1800
PHR_REFRESH_TOKEN_CACHE_TIMEOUT = 129600


def get_phr_hf_id():
    return settings.PHR_HF_ID


def update_abha_from_profile(data, abha_key="abhaNumber", health_ids_list=None):
    abha_number_value = data.get(abha_key) if data else None
    current_health_id = data.get("abhaAddress") if data else None
    if current_health_id:
        current_health_id = normalize_abha_address(current_health_id)

    metadata_updates = {}
    if health_ids_list:
        for health_id in health_ids_list:
            if health_id:
                normalized_id = normalize_abha_address(health_id)
                metadata_updates[normalized_id] = {
                    "subscription_id": None,
                    "auto_approve_id": None,
                    "is_auto_approve_enabled": False,
                }
    elif current_health_id:
        metadata_updates[current_health_id] = {
            "subscription_id": None,
            "auto_approve_id": None,
            "is_auto_approve_enabled": False,
        }

    date_of_birth = None
    if data and data.get("yearOfBirth"):
        try:
            date_of_birth = str(
                datetime.strptime(
                    f"{data.get('yearOfBirth')}-{data.get('monthOfBirth') or '01'}-{data.get('dayOfBirth') or '01'}",
                    "%Y-%m-%d",
                )
            )[:10]
        except (ValueError, TypeError):
            date_of_birth = None

    defaults = {
        "abha_number": abha_number_value,
        "phr_health_id": current_health_id,
    }

    if data:
        defaults.update(
            {
                "name": data.get("name") or data.get("fullName"),
                "first_name": data.get("firstName"),
                "middle_name": data.get("middleName"),
                "last_name": data.get("lastName"),
                "gender": data.get("gender"),
                "email": data.get("email"),
                "address": data.get("address"),
                "district": data.get("districtName"),
                "district_code": data.get("districtCode"),
                "state": data.get("stateName"),
                "state_code": data.get("stateCode"),
                "pincode": data.get("pinCode") or data.get("pincode"),
                "mobile": data.get("mobile"),
                "profile_photo": data.get("profilePhoto"),
            }
        )

        if date_of_birth:
            defaults["date_of_birth"] = date_of_birth

    abha_instance = None

    if abha_number_value:
        try:
            abha_instance = AbhaNumber.objects.get(abha_number=abha_number_value)
        except AbhaNumber.DoesNotExist:
            pass

    if not abha_instance and (health_ids_list or current_health_id):
        health_ids_to_check = health_ids_list or [current_health_id]
        normalized_ids = [
            normalize_abha_address(hid) for hid in health_ids_to_check if hid
        ]

        for normalized_id in normalized_ids:
            existing_records = AbhaNumber.objects.filter(
                phr_health_ids_metadata__has_key=normalized_id
            )
            if existing_records.exists():
                abha_instance = existing_records.first()
                break

        if not abha_instance and current_health_id:
            try:
                abha_instance = AbhaNumber.objects.get(phr_health_id=current_health_id)
            except AbhaNumber.DoesNotExist:
                pass

    if abha_instance:
        for key, value in defaults.items():
            if value is not None:
                setattr(abha_instance, key, value)

        existing_metadata = abha_instance.phr_health_ids_metadata or {}
        for health_id, default_data in metadata_updates.items():
            if health_id not in existing_metadata:
                existing_metadata[health_id] = default_data

        abha_instance.phr_health_ids_metadata = existing_metadata
        abha_instance.save()
        created = False
    else:
        if not metadata_updates and not defaults:
            logger.warning("update_abha_from_profile: No data to create record with")
            return None, False

        defaults["phr_health_ids_metadata"] = metadata_updates

        try:
            abha_instance = AbhaNumber.objects.create(**defaults)
            created = True
        except Exception as e:
            logger.warning(f"Failed to create AbhaNumber: {e}")
            if current_health_id:
                try:
                    abha_instance = AbhaNumber.objects.get(
                        phr_health_id=current_health_id
                    )
                    created = False
                except AbhaNumber.DoesNotExist:
                    return None, False
            else:
                return None, False

    return abha_instance, created


def normalize_abha_address(address):
    if not address.endswith(f"@{settings.ABDM_CM_ID}"):
        return f"{address}@{settings.ABDM_CM_ID}"
    return address


def get_phr_temp_tokens(abha_address, id):
    refresh_token = RefreshToken()
    refresh_token["abha_address"] = normalize_abha_address(abha_address)
    refresh_token["id"] = id
    return {
        "refresh_token": str(refresh_token),
        "access_token": str(refresh_token.access_token),
    }


def cache_phr_tokens(abha_health_id, access_token, refresh_token):
    cache.set(
        f"{PHR_ACCESS_TOKEN_PREFIX}{abha_health_id}",
        access_token,
        timeout=PHR_ACCESS_TOKEN_CACHE_TIMEOUT,
    )

    cache.set(
        f"{PHR_REFRESH_TOKEN_PREFIX}{abha_health_id}",
        refresh_token,
        timeout=PHR_REFRESH_TOKEN_CACHE_TIMEOUT,
    )


def remove_cached_phr_tokens(abha_health_id):
    cache.delete(f"{PHR_ACCESS_TOKEN_PREFIX}{abha_health_id}")
    cache.delete(f"{PHR_REFRESH_TOKEN_PREFIX}{abha_health_id}")


def get_phr_access_token(abha_address):
    if not abha_address:
        abha_address = "dora8sbx"
    abha_address = normalize_abha_address(abha_address)
    access_key = f"{PHR_ACCESS_TOKEN_PREFIX}{abha_address}"
    refresh_key = f"{PHR_REFRESH_TOKEN_PREFIX}{abha_address}"

    x_token = cache.get(access_key)
    if x_token:
        return x_token

    refresh_token = cache.get(refresh_key)

    result = PhrProfileService.phr__request__token({"r_token": refresh_token})
    tokens = result.get("tokens") or {}

    access_token = tokens.get("token")
    new_refresh_token = tokens.get("refreshToken")

    cache.set(access_key, access_token, timeout=PHR_ACCESS_TOKEN_CACHE_TIMEOUT)
    cache.set(refresh_key, new_refresh_token, timeout=PHR_REFRESH_TOKEN_CACHE_TIMEOUT)

    return access_token


def format_abdm_datetime(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_default_abdm_period(days=365):
    now = timezone.now()
    return {
        "from": format_abdm_datetime(now + timedelta(seconds=10)),
        "to": format_abdm_datetime(now + timedelta(days=days)),
    }


def transform_phr_links_data(links_data, include_care_contexts=True):
    patient = links_data.get("patient", {})
    links = patient.get("links", [])

    hip_groups = {}

    for link in links:
        hip = link.get("hip", {})
        hip_id = hip.get("id")

        if not hip_id:
            continue

        if hip_id not in hip_groups:
            hip_groups[hip_id] = {"hip": hip, "links": []}

        if include_care_contexts:
            care_contexts = link.get("careContexts", [])
            for care_context in care_contexts:
                link_object = {
                    "patientReference": link.get("referenceNumber"),
                    "careContextReference": care_context.get("referenceNumber"),
                    "display": care_context.get("display"),
                }
                hip_groups[hip_id]["links"].append(link_object)

    if include_care_contexts:
        transformed_data = [
            {"hip": group_data["hip"], "careContexts": group_data["links"]}
            for group_data in hip_groups.values()
        ]
    else:
        transformed_data = [
            {"hip": group_data["hip"]} for group_data in hip_groups.values()
        ]

    return transformed_data


def update_phr_metadata(phr_health_id: str, updates: dict) -> bool:
    try:
        abha_number = AbhaNumber.objects.get(
            phr_health_id=normalize_abha_address(phr_health_id)
        )
        health_data = abha_number.phr_health_ids_metadata.get(phr_health_id, {})
        health_data.update(updates)
        abha_number.phr_health_ids_metadata[phr_health_id] = health_data
        abha_number.save()
        return True
    except AbhaNumber.DoesNotExist:
        logger.warning(f"ABHA record not found for address: {phr_health_id}")
        return False
