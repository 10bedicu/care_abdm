import logging

import requests
from celery import shared_task

from abdm.models import Transaction, TransactionType
from abdm.service.helper import ABDMAPIException, uuid
from abdm.service.v3.gateway import GatewayService

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_COUNTDOWN = 30


def _call_gateway(task, method, payload, label):
    try:
        method(payload)
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.warning(
            "scan_pay %s transient failure for request %s (attempt %s/%s)",
            label,
            payload.get("request_id"),
            task.request.retries + 1,
            MAX_RETRIES + 1,
        )
        raise task.retry(exc=exc, countdown=RETRY_COUNTDOWN) from exc
    except ABDMAPIException:
        logger.exception(
            "scan_pay %s failed for request %s", label, payload.get("request_id")
        )
        raise


@shared_task(bind=True, max_retries=MAX_RETRIES)
def scan_pay_on_share_open_order(self, payload: dict):
    _call_gateway(
        self, GatewayService.patient__on_share_open_order, payload, "on_share_open_order"
    )


@shared_task(bind=True, max_retries=MAX_RETRIES)
def scan_pay_on_selection(self, payload: dict):
    _call_gateway(self, GatewayService.patient__on_selection, payload, "on_selection")


@shared_task(bind=True, max_retries=MAX_RETRIES)
def scan_pay_notify(self, payload: dict, transaction_meta: dict | None = None):
    _call_gateway(self, GatewayService.patient__scan_pay_notify, payload, "notify")

    if transaction_meta:
        Transaction.objects.create(
            reference_id=uuid(),
            type=TransactionType.SCAN_AND_PAY,
            meta_data=transaction_meta,
        )


@shared_task(bind=True, max_retries=MAX_RETRIES)
def scan_pay_on_order_status(self, payload: dict):
    _call_gateway(
        self, GatewayService.patient__scan_pay_on_order_status, payload, "on_order_status"
    )
