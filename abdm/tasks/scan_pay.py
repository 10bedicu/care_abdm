import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.core.cache import cache

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


@shared_task(ignore_result=True)
def reconcile_pending_payment_orders():
    """Poll the provider for each pending scan-and-pay order; fail stale ones.

    A stale order is only failed after the provider was asked about it, so a
    payment that landed late is recorded instead of being dropped; one the
    provider never answers about is given up on at the hard cap. A tick that
    finds the previous run still going is skipped so a slow provider cannot
    tie up one worker per tick, and a tick with nothing pending does no work.
    """
    if not settings.ABDM_SCAN_AND_PAY_POLLING_ENABLED:
        return
    expire_abandoned_open_orders()
    if not pending_payment_orders().exists():
        return

    try:
        with Lock(POLL_LOCK_KEY, POLL_LOCK_TIMEOUT):
            _reconcile_pending_payment_orders()
    except ObjectLocked:
        logger.info("Previous scan-and-pay poll is still running; skipping this tick")


def expire_abandoned_open_orders() -> int:
    """Cancel orders the patient scanned but never selected services for.

    Nothing was invoiced, so there is nothing to void or notify; a selection
    arriving later is rejected and the patient asked to scan again.
    """
    cutoff = care_now() - timedelta(seconds=settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE)
    return PaymentOrder.objects.filter(
        status=PaymentOrderStatus.OPEN_ORDER_SHARED,
        invoice__isnull=True,
        created_date__lt=cutoff,
    ).update(status=PaymentOrderStatus.CANCELED, modified_date=care_now())


def pending_payment_orders():
    return (
        PaymentOrder.objects.filter(
            status__in=PAYMENT_ORDER_PENDING_STATUSES,
            invoice__isnull=False,
        )
        .exclude(order_number__isnull=True)
        .exclude(order_number="")
    )


def backoff_key(order: PaymentOrder) -> str:
    return f"abdm:scan_pay:backoff:{order.external_id}"


def _ask_provider(order: PaymentOrder) -> bool:
    """Let the provider reconcile the order; False when it could not be reached."""
    try:
        provider = get_provider(order.provider or settings.ABDM_SCAN_AND_PAY_PROVIDER)
        provider.reconcile_order(order)
    except ObjectLocked:
        raise
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.warning(
            "Scan-and-pay provider unreachable for order %s: %s",
            order.order_number,
            exc,
        )
        return False
    except Exception:
        logger.exception(
            "Failed to reconcile scan-and-pay order %s", order.order_number
        )
        return False
    return True


def _reconcile_pending_payment_orders():
    now = care_now()
    stale_before = now - timedelta(seconds=settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE)
    give_up_before = stale_before - timedelta(
        seconds=settings.ABDM_SCAN_AND_PAY_ORDER_HARD_CAP
    )
    orders = pending_payment_orders().select_related(
        "invoice__account", "health_facility__facility"
    )
    for order in orders:
        if cache.get(backoff_key(order)):
            continue
        try:
            reached = _ask_provider(order)
            if not order.created_date:
                continue
            if reached:
                if order.created_date < stale_before:
                    close_payment_order(order, PaymentOrderStatus.FAIL)
                continue
            cache.set(
                backoff_key(order),
                1,
                timeout=settings.ABDM_SCAN_AND_PAY_UNREACHABLE_BACKOFF,
            )
            if order.created_date < give_up_before:
                logger.warning(
                    "Scan-and-pay order %s unresolved past the hard cap; failing it",
                    order.order_number,
                )
                close_payment_order(
                    order,
                    PaymentOrderStatus.FAIL,
                    reason=(
                        "Scan and pay payment unresolved: the payment provider could "
                        f"not be reached before the deadline (order {order.order_number})"
                    ),
                )
        except ObjectLocked:
            logger.info(
                "Scan-and-pay order %s is being settled elsewhere; skipping",
                order.order_number,
            )
