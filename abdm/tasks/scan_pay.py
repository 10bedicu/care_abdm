import logging
from datetime import timedelta

import requests
from celery import shared_task

from abdm.models import PaymentOrder, Transaction, TransactionType
from abdm.models.payment_order import (
    PAYMENT_ORDER_PENDING_STATUSES,
    PaymentOrderStatus,
)
from abdm.service.helper import ABDMAPIException, uuid
from abdm.service.v3.gateway import GatewayService
from abdm.service.v3.payment_providers import close_payment_order, get_provider
from abdm.settings import plugin_settings as settings
from care.utils.lock import Lock, ObjectLocked
from care.utils.time_util import care_now

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_COUNTDOWN = 30
POLL_LOCK_KEY = "abdm:reconcile_pending_payment_orders"
POLL_LOCK_TIMEOUT = 300


def _call_gateway(task, method, payload, label, retry_transient=True):
    try:
        method(payload)
    except (requests.Timeout, requests.ConnectionError) as exc:
        if not retry_transient:
            # a reply correlated by requestId may already have reached the gateway;
            # sending it again is rejected with ABDM-2406
            logger.exception(
                "scan_pay %s transient failure for request %s; not retrying a "
                "correlated reply",
                label,
                payload.get("request_id"),
            )
            raise
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
        self,
        GatewayService.patient__on_share_open_order,
        payload,
        "on_share_open_order",
        retry_transient=False,
    )


@shared_task(bind=True, max_retries=MAX_RETRIES)
def scan_pay_on_selection(self, payload: dict):
    _call_gateway(
        self,
        GatewayService.patient__on_selection,
        payload,
        "on_selection",
        retry_transient=False,
    )


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
        self,
        GatewayService.patient__scan_pay_on_order_status,
        payload,
        "on_order_status",
        retry_transient=False,
    )


@shared_task
def reconcile_pending_payment_orders():
    """Poll the provider for each pending scan-and-pay order; fail stale ones.

    A stale order is only failed after the provider was asked about it, so a
    payment that landed late is recorded instead of being dropped. A tick that
    finds the previous run still going is skipped so a slow provider cannot
    tie up one worker per tick.
    """
    if not settings.ABDM_SCAN_AND_PAY_POLLING_ENABLED:
        return

    try:
        with Lock(POLL_LOCK_KEY, POLL_LOCK_TIMEOUT):
            _reconcile_pending_payment_orders()
    except ObjectLocked:
        logger.info("Previous scan-and-pay poll is still running; skipping this tick")


def _reconcile_pending_payment_orders():
    cutoff = care_now() - timedelta(seconds=settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE)
    orders = (
        PaymentOrder.objects.filter(
            status__in=PAYMENT_ORDER_PENDING_STATUSES,
            invoice__isnull=False,
        )
        .exclude(order_number__isnull=True)
        .exclude(order_number="")
        .select_related("invoice__account", "health_facility__facility")
    )
    for order in orders:
        try:
            provider = get_provider(
                order.provider or settings.ABDM_SCAN_AND_PAY_PROVIDER
            )
            provider.reconcile_order(order)
            if order.created_date and order.created_date < cutoff:
                close_payment_order(order, PaymentOrderStatus.FAIL)
        except ObjectLocked:
            logger.info(
                "Scan-and-pay order %s is being settled elsewhere; skipping",
                order.order_number,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.warning(
                "Scan-and-pay provider unreachable for order %s: %s",
                order.order_number,
                exc,
            )
        except Exception:
            logger.exception(
                "Failed to reconcile scan-and-pay order %s", order.order_number
            )
