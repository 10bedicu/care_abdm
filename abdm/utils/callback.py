from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from abdm.models import CallbackStatus, CallbackType, InboundCallback
from abdm.tasks.process_inbound_callback import (
    enqueue_inbound_callback,
    is_time_boxed,
)

STORED_HEADERS = ["REQUEST-ID", "TIMESTAMP", "X-HIP-ID", "X-CM-ID", "X-HIU-ID"]


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
