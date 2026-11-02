"""Guards that keep request-correlated ABDM replies single and on time.

The gateway holds a callback open for ABDM_CALLBACK_RESPONSE_WINDOW and answers a
late or repeated on-* reply with ABDM-2406 "Invalid API sequence flow". These tests
pin the two behaviours that prevent that: never re-send a correlated reply, and
never send one after the window has closed.
"""

from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from abdm.models import CallbackStatus, CallbackType, InboundCallback, PaymentOrder
from abdm.service.helper import uuid
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.settings import plugin_settings as abdm_settings
from abdm.tasks import setup_periodic_tasks
from abdm.tasks.patient_share import patient_share_on_share
from abdm.tasks.process_inbound_callback import (
    LOW_PRIORITY,
    TIME_BOXED_CALLBACK_TYPES,
    process_inbound_callback,
)
from abdm.tasks.scan_pay import (
    MAX_RETRIES,
    scan_pay_notify,
    scan_pay_on_order_status,
    scan_pay_on_selection,
    scan_pay_on_share_open_order,
)
from abdm.utils.callback import _is_duplicate
from celery import current_app
from django.utils import timezone
from rest_framework.serializers import Serializer

from tests.test_scan_and_pay import SHARE_OPEN_ORDER_URL, ScanPayTestBase

GATEWAY_ON_SHARE_OPEN_ORDER = (
    "abdm.service.v3.gateway.GatewayService.patient__on_share_open_order"
)


class EmptySerializer(Serializer):
    pass


class InboundCallbackGuardTests(ScanPayTestBase):
    def store_open_order_callback(self, request_id=None, age_seconds=0):
        request_id = request_id or uuid()
        callback = InboundCallback.objects.create(
            request_id=request_id,
            callback_type=CallbackType.PATIENT_SHARE_OPEN_ORDER,
            payload=self.share_open_order_payload(),
            headers={"REQUEST-ID": request_id},
        )
        if age_seconds:
            InboundCallback.objects.filter(pk=callback.pk).update(
                created_date=timezone.now() - timedelta(seconds=age_seconds)
            )
            callback.refresh_from_db()
        return callback

    @patch(GATEWAY_ON_SHARE_OPEN_ORDER)
    def test_expired_callback_is_failed_without_replying(self, mock_gateway):
        self.create_charge_item()
        callback = self.store_open_order_callback(
            age_seconds=abdm_settings.ABDM_CALLBACK_RESPONSE_WINDOW + 1
        )

        process_inbound_callback.apply(args=[callback.pk], throw=False)

        callback.refresh_from_db()
        mock_gateway.assert_not_called()
        self.assertEqual(callback.status, CallbackStatus.FAILED)
        self.assertEqual(callback.attempts, 0)
        self.assertIn("expired", callback.error_message)
        self.assertFalse(
            PaymentOrder.objects.filter(
                open_order_request_id=callback.request_id
            ).exists()
        )

    @patch(GATEWAY_ON_SHARE_OPEN_ORDER)
    def test_callback_within_window_is_replied_to(self, mock_gateway):
        self.create_charge_item()
        callback = self.store_open_order_callback(
            age_seconds=abdm_settings.ABDM_CALLBACK_RESPONSE_WINDOW - 5
        )

        process_inbound_callback.apply(args=[callback.pk], throw=False)

        callback.refresh_from_db()
        mock_gateway.assert_called_once()
        self.assertEqual(callback.status, CallbackStatus.COMPLETED)

    @patch(GATEWAY_ON_SHARE_OPEN_ORDER)
    def test_timed_out_correlated_reply_is_not_sent_again(self, mock_gateway):
        mock_gateway.side_effect = requests.ReadTimeout("gateway slow")
        self.create_charge_item()
        callback = self.store_open_order_callback()

        result = process_inbound_callback.apply(args=[callback.pk], throw=False)

        callback.refresh_from_db()
        # the first attempt may have reached the gateway; a retry would be a duplicate
        self.assertEqual(mock_gateway.call_count, 1)
        self.assertEqual(callback.attempts, 1)
        self.assertEqual(callback.status, CallbackStatus.FAILED)
        self.assertIn("ReadTimeout", callback.error_message)
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, CallbackProcessingError)

    def test_timed_out_uncorrelated_callback_is_still_retried(self):
        handler = Mock(side_effect=requests.ReadTimeout("gateway slow"))
        callback = InboundCallback.objects.create(
            request_id=uuid(),
            callback_type=CallbackType.CONSENT_REQUEST_HIP_NOTIFY,
            payload={},
        )

        with patch(
            "abdm.tasks.process_inbound_callback._dispatch_table",
            return_value={
                CallbackType.CONSENT_REQUEST_HIP_NOTIFY: (EmptySerializer, handler)
            },
        ):
            result = process_inbound_callback.apply(args=[callback.pk], throw=False)

        callback.refresh_from_db()
        self.assertEqual(handler.call_count, process_inbound_callback.max_retries + 1)
        self.assertEqual(callback.attempts, process_inbound_callback.max_retries + 1)
        self.assertEqual(callback.status, CallbackStatus.FAILED)
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, requests.ReadTimeout)

    def test_time_boxed_types_are_the_correlated_replies(self):
        self.assertEqual(
            TIME_BOXED_CALLBACK_TYPES,
            {
                CallbackType.PATIENT_SHARE,
                CallbackType.PATIENT_SHARE_OPEN_ORDER,
                CallbackType.PATIENT_SELECTION,
                CallbackType.PATIENT_SCAN_PAY_ORDER_STATUS,
            },
        )


class CallbackDeduplicationTests(ScanPayTestBase):
    @patch(GATEWAY_ON_SHARE_OPEN_ORDER)
    def test_failed_correlated_callback_is_not_answered_twice(self, mock_gateway):
        request_id = uuid()
        payload = self.share_open_order_payload(hip_id="UNKNOWN_HIP")

        for _ in range(2):
            response = self.post_callback(SHARE_OPEN_ORDER_URL, payload, request_id)
            self.assertEqual(response.status_code, 202)

        callbacks = InboundCallback.objects.filter(request_id=request_id)
        self.assertEqual(callbacks.count(), 1)
        self.assertEqual(callbacks.get().status, CallbackStatus.FAILED)
        self.assertEqual(mock_gateway.call_count, 1)

    def test_failed_uncorrelated_callback_may_be_redelivered(self):
        request_id = uuid()
        InboundCallback.objects.create(
            request_id=request_id,
            callback_type=CallbackType.CONSENT_REQUEST_HIP_NOTIFY,
            status=CallbackStatus.FAILED,
        )

        self.assertFalse(
            _is_duplicate(request_id, CallbackType.CONSENT_REQUEST_HIP_NOTIFY)
        )

    def test_in_flight_or_completed_callbacks_are_duplicates_for_every_type(self):
        for callback_type in (
            CallbackType.CONSENT_REQUEST_HIP_NOTIFY,
            CallbackType.PATIENT_SHARE_OPEN_ORDER,
        ):
            for callback_status in (
                CallbackStatus.PENDING,
                CallbackStatus.PROCESSING,
                CallbackStatus.COMPLETED,
            ):
                request_id = uuid()
                InboundCallback.objects.create(
                    request_id=request_id,
                    callback_type=callback_type,
                    status=callback_status,
                )
                self.assertTrue(_is_duplicate(request_id, callback_type))

    def test_missing_request_id_is_never_a_duplicate(self):
        self.assertFalse(_is_duplicate(None, CallbackType.PATIENT_SHARE_OPEN_ORDER))


class ReplyTaskRetryTests(ScanPayTestBase):
    def run_until_settled(self, task, payload):
        return task.apply(args=[payload], throw=False)

    def test_correlated_scan_pay_replies_do_not_retry_transient_failures(self):
        cases = [
            (scan_pay_on_share_open_order, "patient__on_share_open_order"),
            (scan_pay_on_selection, "patient__on_selection"),
            (scan_pay_on_order_status, "patient__scan_pay_on_order_status"),
        ]
        for task, gateway_method in cases:
            with (
                self.subTest(task=task.name),
                patch(
                    f"abdm.service.v3.gateway.GatewayService.{gateway_method}",
                    side_effect=requests.ConnectionError("reset"),
                ) as mock_gateway,
            ):
                result = self.run_until_settled(task, {"request_id": uuid()})

                self.assertEqual(mock_gateway.call_count, 1)
                self.assertTrue(result.failed())
                self.assertIsInstance(result.result, requests.ConnectionError)

    def test_scan_pay_notify_still_retries_transient_failures(self):
        with patch(
            "abdm.service.v3.gateway.GatewayService.patient__scan_pay_notify",
            side_effect=requests.ConnectionError("reset"),
        ) as mock_gateway:
            result = self.run_until_settled(scan_pay_notify, {"request_id": uuid()})

        # notify is HIP-initiated with a fresh REQUEST-ID, so repeating it is safe
        self.assertEqual(mock_gateway.call_count, MAX_RETRIES + 1)
        self.assertTrue(result.failed())

    def test_scan_and_share_on_share_does_not_retry_transient_failures(self):
        with patch(
            "abdm.service.v3.gateway.GatewayService.patient_share__on_share",
            side_effect=requests.ConnectionError("reset"),
        ) as mock_gateway:
            result = self.run_until_settled(
                patient_share_on_share, {"request_id": uuid()}
            )

        self.assertEqual(mock_gateway.call_count, 1)
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, requests.ConnectionError)


class ReconcilePollerScheduleTests(ScanPayTestBase):
    def test_poller_runs_at_low_priority_and_expires_before_the_next_run(self):
        setup_periodic_tasks(current_app)

        entry = current_app.conf.beat_schedule["reconcile_pending_payment_orders"]
        interval = abdm_settings.ABDM_SCAN_AND_PAY_POLLING_INTERVAL

        self.assertEqual(entry["schedule"], interval)
        self.assertEqual(entry["options"]["priority"], LOW_PRIORITY)
        self.assertEqual(entry["options"]["expires"], interval)
