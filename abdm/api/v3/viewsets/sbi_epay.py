import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: SBI ePay"])
class SbiEpayViewSet(GenericViewSet):
    permission_classes = (AllowAny,)
    authentication_classes = []

    @action(detail=False, methods=["POST"], url_path="webhook")
    def webhook(self, request):
        logger.info(
            "SBI ePay webhook received | headers=%s | body=%s",
            dict(request.headers),
            request.data,
        )

        push_resp_data = request.data.get("pushRespData")
        if push_resp_data:
            try:
                from abdm.service.v3.scan_pay import reconcile_sbi_epay_push
                from abdm.utils import sbi_epay

                reconcile_sbi_epay_push(sbi_epay.parse_push_response(push_resp_data))
            except Exception:
                logger.exception("Failed to process SBI ePay push response")

        return Response(status=status.HTTP_200_OK)
