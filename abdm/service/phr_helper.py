from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from abdm.models import AbhaNumber
from abdm.settings import plugin_settings as settings
from care_abdm.abdm.service.v3.phr.phr_profile import PhrProfileService

PHR_ACCESS_TOKEN_PREFIX = "phr_access_token:"
PHR_REFRESH_TOKEN_PREFIX = "phr_refresh_token:"
PHR_ACCESS_TOKEN_CACHE_TIMEOUT = 1800
PHR_REFRESH_TOKEN_CACHE_TIMEOUT = 129600


def update_abha_from_profile(data, abha_key="abhaNumber", **tokens):
    date_of_birth = str(
        datetime.strptime(
            f"{data.get('yearOfBirth')}-{data.get('monthOfBirth') or '01'}-{data.get('dayOfBirth') or '01'}",
            "%Y-%m-%d",
        )
    )[:10]

    defaults = {
        "abha_number": data.get(abha_key),
        "phr_health_id": data.get("abhaAddress"),
        "name": data.get("name") or data.get("fullName"),
        "first_name": data.get("firstName"),
        "middle_name": data.get("middleName"),
        "last_name": data.get("lastName"),
        "gender": data.get("gender"),
        "email": data.get("email"),
        "date_of_birth": date_of_birth,
        "address": data.get("address"),
        "district": data.get("districtName"),
        "state": data.get("stateName"),
        "pincode": data.get("pinCode") or data.get("pincode"),
        "mobile": data.get("mobile"),
        "profile_photo": data.get("profilePhoto"),
        **tokens,
    }

    return AbhaNumber.objects.update_or_create(
        abha_number=data.get(abha_key),
        defaults=defaults,
    )


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


def get_phr_access_token(abha_address: str = "dora8sbx"):
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


def transform_phr_links_data(links_data, include_links=True):
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

        if include_links:
            care_contexts = link.get("careContexts", [])
            for care_context in care_contexts:
                link_object = {
                    "patientReference": link.get("referenceNumber"),
                    "careContextReference": care_context.get("referenceNumber"),
                    "display": care_context.get("display"),
                }
                hip_groups[hip_id]["links"].append(link_object)

    if include_links:
        transformed_data = [
            {"hip": group_data["hip"], "careContexts": group_data["links"]}
            for group_data in hip_groups.values()
        ]
    else:
        transformed_data = [
            {"hip": group_data["hip"]} for group_data in hip_groups.values()
        ]

    return transformed_data
