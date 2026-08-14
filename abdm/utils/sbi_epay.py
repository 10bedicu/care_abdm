import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache

from abdm.settings import plugin_settings as settings
from abdm.utils import sbi_epay_crypto

logger = logging.getLogger(__name__)

AUTH_PATH = "/authenticationAPI/authentication/v1/getmerchantapiauthentication"
GENERATE_PAYMENT_URL_PATH = "/SBIePayPayment/payagg/generatePaymentURL/payURL"
STATUS_QUERY_PATH = "/MerchantDVAPI/getStatusQueryAPI"

TOKEN_CACHE_KEY = "abdm_sbi_epay_token"
TOKEN_CACHE_TTL = 23 * 60 * 60  # SBI token valid ~24h; refresh a little early.

IST = ZoneInfo("Asia/Kolkata")


class SbiEpayError(Exception):
    pass


def encrypt(plain: str) -> str:
    return sbi_epay_crypto.encrypt(settings.ABDM_SBI_EPAY_MERCHANT_KEY, plain)


def decrypt(enc: str) -> str:
    return sbi_epay_crypto.decrypt(settings.ABDM_SBI_EPAY_MERCHANT_KEY, enc)


def checksum(plain: str) -> str:
    return sbi_epay_crypto.checksum(plain)


def format_amount(value) -> str:
    return f"{Decimal(str(value)):.2f}"


def merch_order_number() -> str:
    # SBI merchOrderNo is VARCHAR(15); use a short alphanumeric unique reference.
    return uuid.uuid4().hex[:15]


def _source_url() -> str:
    return settings.ABDM_SBI_EPAY_SOURCE_URL or settings.BACKEND_DOMAIN


def get_token(force_refresh: bool = False) -> str:
    if not force_refresh:
        token = cache.get(TOKEN_CACHE_KEY)
        if token:
            return token

    response = requests.post(
        settings.ABDM_SBI_EPAY_BASE_URL + AUTH_PATH,
        headers={
            "api_key_id": settings.ABDM_SBI_EPAY_API_KEY_ID,
            "api_secret_key": settings.ABDM_SBI_EPAY_API_SECRET_KEY,
        },
        timeout=settings.ABDM_REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    token = (response.json().get("data") or {}).get("token")
    if not token:
        raise SbiEpayError("Failed to obtain SBI ePay token")

    cache.set(TOKEN_CACHE_KEY, token, timeout=TOKEN_CACHE_TTL)
    return token


def _post_encrypted(path: str, req: dict) -> dict:
    plain = json.dumps({"req": req}, separators=(",", ":"))
    body = {
        "encData": encrypt(plain),
        "cs": checksum(plain),
        "merchantCode": settings.ABDM_SBI_EPAY_MERCHANT_CODE,
    }

    def _call(token):
        return requests.post(
            settings.ABDM_SBI_EPAY_BASE_URL + path,
            headers={
                "Authorization": token,  # token already carries the "Bearer " prefix
                "api_name": path,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=settings.ABDM_REQUEST_TIMEOUT,
        )

    response = _call(get_token())
    if response.status_code in (401, 403):
        response = _call(get_token(force_refresh=True))
    response.raise_for_status()

    payload = response.json()
    enc = payload.get("encData")
    if not enc or enc == "ERROR":
        raise SbiEpayError(payload.get("cs") or "SBI ePay request failed")

    decrypted = json.loads(decrypt(enc))
    return decrypted.get("res") or decrypted


def create_payment_link(*, merch_order_no: str, amount, other_details: str) -> dict:
    now = datetime.now(IST)
    validity = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return _post_encrypted(
        GENERATE_PAYMENT_URL_PATH,
        {
            "merchOrderNo": merch_order_no,
            "amount": format_amount(amount),
            "transactionDate": now.strftime("%d/%m/%Y %H:%M:%S"),
            "merchOrderNoValidity": validity.strftime("%d/%m/%Y %H:%M:%S"),
            "sourceUrl": _source_url(),
            "otherDetails": other_details,
        },
    )


def status_query(*, merch_order_no: str, amount, atrn: str = "") -> dict:
    return _post_encrypted(
        STATUS_QUERY_PATH,
        {
            "atrn": atrn,
            "merchantId": settings.ABDM_SBI_EPAY_MERCHANT_CODE,
            "merchOrderNo": merch_order_no,
            "amount": format_amount(amount),
            "sourceUrl": _source_url(),
        },
    )


# Push response is a pipe-delimited string with a trailing SHA-512 checksum.
PUSH_FIELDS = (
    "merch_order_no",
    "atrn",
    "status",
    "amount",
    "currency",
    "pay_mode",
    "other_details",
    "response_status",
    "bank_code",
    "bank_ref_number",
    "transaction_date",
    "country",
    "cin",
    "merchant_id",
)


def parse_push_response(push_resp_data: str) -> dict:
    plain = decrypt(push_resp_data)
    idx = plain.rfind("|")
    if idx == -1:
        raise SbiEpayError("Malformed SBI ePay push payload")
    # checksum covers everything up to and including the pipe before it.
    if checksum(plain[: idx + 1]) != plain[idx + 1 :]:
        raise SbiEpayError("SBI ePay push checksum mismatch")

    fields = plain.split("|")
    return {name: fields[i] for i, name in enumerate(PUSH_FIELDS) if i < len(fields)}

