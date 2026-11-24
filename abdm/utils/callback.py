import re

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from abdm.models import CallbackStatus, CallbackType, InboundCallback
from abdm.tasks.process_inbound_callback import (
    enqueue_inbound_callback,
    is_time_boxed,
)

STORED_HEADERS = ["REQUEST-ID", "TIMESTAMP", "X-HIP-ID", "X-CM-ID", "X-HIU-ID"]

# a key with one of these in its name may hold personal data
PERSONAL_DATA_KEY_PARTS = (
    "patient",
    "abha",
    "phone",
    "mobile",
    "name",
    "address",
    "email",
    "birth",
    "gender",
    "healthid",
)
# an ABHA address or an email, or a phone or ABHA number, wherever it turns up
PERSONAL_DATA_VALUE = re.compile(r"@|(?<![\w-])\+?\d[\d\s-]{8,}\d(?![\w-])")
REDACTED = "[redacted]"


def without_personal_data(payload):
    """A callback body that can be logged: its shape is kept, and the values of
    the fields that may hold personal data are not."""
    if isinstance(payload, dict):
        return {
            key: REDACTED
            if any(part in str(key).lower() for part in PERSONAL_DATA_KEY_PARTS)
            else without_personal_data(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [without_personal_data(item) for item in payload]
    if isinstance(payload, str) and PERSONAL_DATA_VALUE.search(payload):
        return REDACTED
    return payload


def _is_duplicate(request_id: str, callback_type: CallbackType) -> bool:
    if not request_id:
        return False

    existing = InboundCallback.objects.filter(
        callback_type=callback_type, request_id=request_id
    )

    # a correlated reply may already have reached the gateway even when the
    # attempt was recorded as failed, so never answer the same request twice
    if not is_time_boxed(callback_type):
        existing = existing.filter(
            status__in=[
                CallbackStatus.PENDING,
                CallbackStatus.PROCESSING,
                CallbackStatus.COMPLETED,
            ]
        )

    return existing.exists()


def store_and_enqueue_callback(request, callback_type: CallbackType) -> Response:
    request_id = request.headers.get("REQUEST-ID")

    if _is_duplicate(request_id, callback_type):
        return Response(status=status.HTTP_202_ACCEPTED)

    callback = InboundCallback.objects.create(
        request_id=request_id,
        callback_type=callback_type,
        payload=request.data,
        headers={
            header: request.headers.get(header)
            for header in STORED_HEADERS
            if request.headers.get(header)
        },
    )

    transaction.on_commit(lambda: enqueue_inbound_callback(callback))

    return Response(status=status.HTTP_202_ACCEPTED)
