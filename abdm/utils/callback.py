from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from abdm.models import CallbackStatus, CallbackType, InboundCallback
from abdm.tasks.process_inbound_callback import enqueue_inbound_callback

STORED_HEADERS = ["REQUEST-ID", "TIMESTAMP", "X-HIP-ID", "X-CM-ID", "X-HIU-ID"]


def store_and_enqueue_callback(request, callback_type: CallbackType) -> Response:
    request_id = request.headers.get("REQUEST-ID")

    if (
        request_id
        and InboundCallback.objects.filter(
            callback_type=callback_type,
            request_id=request_id,
            status__in=[
                CallbackStatus.PENDING,
                CallbackStatus.PROCESSING,
                CallbackStatus.COMPLETED,
            ],
        ).exists()
    ):
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
