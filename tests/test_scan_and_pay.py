from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import jwt
import requests
from abdm.models import AbhaNumber, HealthFacility, InboundCallback, PaymentOrder
from abdm.models.inbound_callback import CallbackStatus
from abdm.models.payment_order import PaymentOrderStatus
from abdm.open_payment_link import allow_open_payment_link_mutation
from abdm.service.helper import uuid
from abdm.service.v3 import payment_providers
from abdm.service.v3 import scan_pay as scan_pay_service
from abdm.service.v3.callback_handlers.scan_pay import (
    handle_patient_selection,
    handle_patient_share_open_order,
    handle_scan_pay_order_status,
)
from abdm.service.v3.receipt import (
    RECEIPT_FILE_TYPE,
    RECEIPT_FINGERPRINT_META,
    build_receipt_context,
)
from abdm.service.v3.scan_pay import scan_pay_receipt_token
from abdm.settings import plugin_settings as abdm_settings
from abdm.tasks import scan_pay as scan_pay_tasks
from abdm.tasks.scan_pay import POLL_LOCK_KEY, reconcile_pending_payment_orders
from abdm.utils import user as abdm_user
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.utils import timezone
from model_bakery import baker

from care.emr.models.charge_item import ChargeItem
from care.emr.models.file_upload import FileUpload
from care.emr.models.invoice import Invoice
from care.emr.models.patient import PatientIdentifierConfig
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.spec import (
    AccountBillingStatusOptions,
    AccountStatusOptions,
)
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.lock import Lock, ObjectLocked
from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now

SHARE_OPEN_ORDER_URL = "/api/abdm/api/v3/patient/share/open-order"
SELECTION_URL = "/api/abdm/api/v3/patient/selection"
ORDER_STATUS_URL = "/api/abdm/api/v3/patient/scan-pay/order-status"
STORED_RECEIPT_PDF = b"%PDF-1.4 stored-receipt"


def receipt_claims(url: str) -> dict:
    parsed = urlparse(url)
    token = parse_qs(parsed.query).get("token", [None])[0]
    order_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    assert claims["sub"] == order_id
    return claims


def receipt_path(order, *, token=None) -> str:
    token = token or scan_pay_receipt_token(order)
    return (
        f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/"
        f"?{urlencode({'token': token})}"
    )


class ScanPayTestBase(CareAPITestBase):
    def setUp(self):
        # cached across tests but each test rolls back; reset to avoid stale FK.
        abdm_user.ABDM_USER = None
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.health_facility = baker.make(
            HealthFacility, facility=self.facility, hf_id="TEST_HIP"
        )
        self.patient = self.create_patient(phone_number="+919999999999")
        self.abha_number = baker.make(
            AbhaNumber,
            patient=self.patient,
            health_id="testpatient@sbx",
            abha_number="91123456789012",
        )
        self.account = baker.make(
            "emr.Account",
            facility=self.facility,
            patient=self.patient,
            status=AccountStatusOptions.active.value,
            billing_status=AccountBillingStatusOptions.open.value,
        )
        self.client.force_authenticate(user=self.user)

    def create_charge_item(self, **kwargs):
        amount = kwargs.pop("amount", 100)
        item_status = kwargs.pop("status", ChargeItemStatusOptions.billable.value)
        account = kwargs.pop("account", self.account)
        return baker.make(
            ChargeItem,
            facility=self.facility,
            patient=self.patient,
            account=account,
            status=item_status,
            title="Test Service",
            quantity=1,
            total_price=Decimal(amount),
            total_price_components=[
                {"monetary_component_type": "base", "amount": amount}
            ],
            unit_price_components=[
                {"monetary_component_type": "base", "amount": amount}
            ],
            service_resource="service_request",
            tags=[],
            **kwargs,
        )

    def share_open_order_payload(self, abha_address=None, hip_id=None):
        return {
            "intent": "OPEN_PAYMENT_ORDER",
            "metadata": {"hipId": hip_id or self.health_facility.hf_id},
            "profile": {
                "patient": {"abhaAddress": abha_address or self.abha_number.health_id}
            },
        }

    def post_callback(self, url, payload, request_id=None):
        # the callback is enqueued on commit, which the test transaction never reaches
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                url,
                payload,
                format="json",
                headers={"REQUEST-ID": request_id or uuid()},
            )


class TestScanPayInvoiceTitle(ScanPayTestBase):
    def test_title_includes_abha_address_and_local_time(self):
        issued_at = datetime(2026, 10, 20, 3, 58, tzinfo=ZoneInfo("UTC"))
        with timezone.override("Asia/Kolkata"):
            title = scan_pay_service._invoice_title("testpatient@sbx", issued_at)
        self.assertEqual(
            title,
            "Scan and Pay - testpatient@sbx - 20 Oct 2026, 9:28 AM",
        )


class TestShareOpenOrder(ScanPayTestBase):
    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_success(self, mock_gateway):
        self.create_charge_item()
        request_id = uuid()

        response = self.post_callback(
            SHARE_OPEN_ORDER_URL, self.share_open_order_payload(), request_id
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(
            PaymentOrder.objects.filter(open_order_request_id=request_id).exists()
        )

        callback = InboundCallback.objects.get(request_id=request_id)
        self.assertEqual(callback.status, CallbackStatus.COMPLETED)

        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["abha_address"], self.abha_number.health_id)
        self.assertEqual(len(payload["procedures"]), 1)
        self.assertEqual(
            payload["procedures"][0]["category"], "Laboratory and Diagnostics"
        )

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_unknown_patient(self, mock_gateway):
        response = self.post_callback(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(abha_address="unknown@sbx"),
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_rejects_address_not_in_system(self, mock_gateway):
        # same ABHA number, but under an address CARE has not stored
        self.create_charge_item()
        payload = self.share_open_order_payload(abha_address="other@sbx")
        payload["profile"]["patient"]["abhaNumber"] = self.abha_number.abha_number
        request_id = uuid()

        handle_patient_share_open_order(payload, {"REQUEST-ID": request_id})

        reply = mock_gateway.call_args[0][0]
        self.assertIn("error", reply)
        self.assertEqual(reply["abha_address"], "other@sbx")
        self.assertFalse(
            PaymentOrder.objects.filter(open_order_request_id=request_id).exists()
        )

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_no_open_orders(self, mock_gateway):
        response = self.post_callback(
            SHARE_OPEN_ORDER_URL, self.share_open_order_payload()
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_excludes_zero_amount_items(self, mock_gateway):
        self.create_charge_item(amount=0)
        unpriced_item = self.create_charge_item()
        ChargeItem.objects.filter(id=unpriced_item.id).update(total_price=None)

        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )
        self.assertIn("error", mock_gateway.call_args[0][0])

        paid_item = self.create_charge_item()
        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )

        services = mock_gateway.call_args[0][0]["procedures"][0]["services"]
        self.assertEqual(
            [service["service_id"] for service in services],
            [str(paid_item.external_id)],
        )

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_excludes_inactive_account_items(self, mock_gateway):
        inactive_account = baker.make(
            "emr.Account",
            facility=self.facility,
            patient=self.patient,
            status=AccountStatusOptions.inactive.value,
        )
        self.create_charge_item(account=inactive_account)

        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )
        self.assertIn("error", mock_gateway.call_args[0][0])

        active_item = self.create_charge_item()
        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )

        services = mock_gateway.call_args[0][0]["procedures"][0]["services"]
        self.assertEqual(
            [service["service_id"] for service in services],
            [str(active_item.external_id)],
        )

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_excludes_accounts_that_are_not_open_for_billing(
        self, mock_gateway
    ):
        billing_account = baker.make(
            "emr.Account",
            facility=self.facility,
            patient=self.patient,
            status=AccountStatusOptions.active.value,
            billing_status=AccountBillingStatusOptions.billing.value,
        )
        self.create_charge_item(account=billing_account)

        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )
        self.assertIn("error", mock_gateway.call_args[0][0])

        open_item = self.create_charge_item()
        handle_patient_share_open_order(
            self.share_open_order_payload(), {"REQUEST-ID": uuid()}
        )

        services = mock_gateway.call_args[0][0]["procedures"][0]["services"]
        self.assertEqual(
            [service["service_id"] for service in services],
            [str(open_item.external_id)],
        )

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_unknown_facility(self, mock_gateway):
        request_id = uuid()

        response = self.post_callback(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(hip_id="UNKNOWN_HIP"),
            request_id,
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

        callback = InboundCallback.objects.get(request_id=request_id)
        self.assertEqual(callback.status, CallbackStatus.FAILED)

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_is_deduplicated_by_request_id(self, mock_gateway):
        self.create_charge_item()
        request_id = uuid()

        for _ in range(2):
            response = self.post_callback(
                SHARE_OPEN_ORDER_URL, self.share_open_order_payload(), request_id
            )
            self.assertEqual(response.status_code, 202)

        self.assertEqual(
            InboundCallback.objects.filter(request_id=request_id).count(), 1
        )
        self.assertEqual(mock_gateway.call_count, 1)


class TestSelection(ScanPayTestBase):
    def create_order(self):
        return PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
        )

    def selection_payload(self, order, charge_items):
        return {
            "intent": "PAYMENT_ORDER",
            "openOrderRequestId": str(order.open_order_request_id),
            "abhaAddress": self.abha_number.health_id,
            "procedures": [
                {
                    "category": "Laboratory and Diagnostics",
                    "services": [
                        {
                            "serviceId": str(item.external_id),
                            "name": item.title,
                            "amount": float(item.total_price),
                        }
                        for item in charge_items
                    ],
                }
            ],
        }

    @patch("abdm.service.v3.scan_pay.create_scan_pay_payment_link")
    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_success(self, mock_gateway, mock_payment_link):
        mock_payment_link.return_value = {
            "order_number": "INV-1",
            "payment_link_id": "plink_test",
            "payment_url": "https://rzp.io/test",
        }
        order = self.create_order()
        charge_item = self.create_charge_item()

        response = self.post_callback(
            SELECTION_URL, self.selection_payload(order, [charge_item])
        )

        self.assertEqual(response.status_code, 202)

        order.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertEqual(order.payment_link_id, "plink_test")
        self.assertEqual(order.order_number, "INV-1")
        self.assertIsNotNone(order.invoice_id)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billed.value)
        self.assertEqual(charge_item.paid_invoice_id, order.invoice_id)

        invoice = Invoice.objects.get(id=order.invoice_id)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)
        self.assertEqual(invoice.total_gross, Decimal(100))

        payload = mock_gateway.call_args[0][0]
        self.assertEqual(
            payload["payment_bundle"]["payment_url"], "https://rzp.io/test"
        )
        self.assertEqual(payload["payment_bundle"]["amount"], 100.0)

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_rejects_unavailable_items(self, mock_gateway):
        order = self.create_order()
        charge_item = self.create_charge_item(
            status=ChargeItemStatusOptions.billed.value
        )

        response = self.post_callback(
            SELECTION_URL, self.selection_payload(order, [charge_item])
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_rejects_zero_amount_items(self, mock_gateway):
        order = self.create_order()
        free_item = self.create_charge_item(amount=0)

        handle_patient_selection(
            self.selection_payload(order, [self.create_charge_item(), free_item]),
            {"REQUEST-ID": uuid()},
        )

        order.refresh_from_db()
        free_item.refresh_from_db()
        self.assertIn("error", mock_gateway.call_args[0][0])
        self.assertIsNone(order.invoice_id)
        self.assertEqual(free_item.status, ChargeItemStatusOptions.billable.value)

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_unknown_order(self, mock_gateway):
        charge_item = self.create_charge_item()
        order = PaymentOrder(open_order_request_id=uuid())

        response = self.post_callback(
            SELECTION_URL, self.selection_payload(order, [charge_item])
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_rejects_expired_open_order(self, mock_gateway):
        order = self.create_order()
        charge_item = self.create_charge_item()
        PaymentOrder.objects.filter(pk=order.pk).update(
            created_date=care_now()
            - timedelta(seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + 60)
        )

        self.assertEqual(scan_pay_tasks.expire_abandoned_open_orders(), 1)
        handle_patient_selection(
            self.selection_payload(order, [charge_item]), {"REQUEST-ID": uuid()}
        )

        order.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.CANCELED)
        self.assertIsNone(order.invoice_id)
        self.assertIn("expired", mock_gateway.call_args[0][0]["error"]["message"])
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)

    def test_recent_open_orders_are_not_expired(self):
        order = self.create_order()

        self.assertEqual(scan_pay_tasks.expire_abandoned_open_orders(), 0)

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.OPEN_ORDER_SHARED)

    @patch("abdm.service.v3.scan_pay.create_scan_pay_payment_link")
    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_accepts_the_address_that_opened_the_order(
        self, mock_gateway, mock_payment_link
    ):
        mock_payment_link.return_value = {
            "order_number": "INV-1",
            "payment_link_id": "plink_test",
            "payment_url": "https://rzp.io/test",
        }
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            abha_address="other@sbx",
            health_facility=self.health_facility,
        )
        payload = self.selection_payload(order, [self.create_charge_item()])
        payload["abhaAddress"] = "other@sbx"

        handle_patient_selection(payload, {"REQUEST-ID": uuid()})

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertNotIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    @patch("abdm.service.v3.scan_pay.create_scan_pay_payment_link")
    def test_selection_without_payment_link_creates_no_invoice(
        self, mock_payment_link, mock_gateway
    ):
        mock_payment_link.side_effect = RuntimeError("gateway down")
        order = self.create_order()
        charge_item = self.create_charge_item()

        handle_patient_selection(
            self.selection_payload(order, [charge_item]), {"REQUEST-ID": uuid()}
        )

        order.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.OPEN_ORDER_SHARED)
        self.assertIsNone(order.invoice_id)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
        self.assertIsNone(charge_item.paid_invoice_id)
        self.assertFalse(Invoice.objects.filter(account=self.account).exists())
        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["error"]["message"], "Failed to create payment link")

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    @patch("abdm.service.v3.scan_pay.create_scan_pay_payment_link")
    def test_selection_keeps_items_selectable_after_link_failure(
        self, mock_payment_link, mock_gateway
    ):
        mock_payment_link.side_effect = [
            RuntimeError("gateway down"),
            {
                "order_number": "INV-1",
                "payment_link_id": "plink_retry",
                "payment_url": "https://rzp.io/retry",
            },
        ]
        order = self.create_order()
        charge_item = self.create_charge_item()

        for _ in range(2):
            handle_patient_selection(
                self.selection_payload(order, [charge_item]), {"REQUEST-ID": uuid()}
            )

        order.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertEqual(order.payment_link_id, "plink_retry")
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billed.value)
        self.assertEqual(charge_item.paid_invoice_id, order.invoice_id)
        self.assertEqual(Invoice.objects.filter(account=self.account).count(), 1)


class TestPaymentNotify(ScanPayTestBase):
    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_payment_reconciliation_triggers_notify(self, mock_task):
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-1",
        )
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-1",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        with (
            self.captureOnCommitCallbacks(execute=True),
            allow_open_payment_link_mutation(),
        ):
            baker.make(
                PaymentReconciliation,
                facility=self.facility,
                account=self.account,
                target_invoice=invoice,
                reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
                status=PaymentReconciliationStatusOptions.active.value,
                amount=Decimal(100),
                tendered_amount=Decimal(100),
                returned_amount=Decimal(0),
                reference_number="pay_test",
            )

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        self.assertEqual(order.transaction_id, "pay_test")
        self.assertIsNotNone(order.payment_date)

        payload = mock_task.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "SUCCESS")
        self.assertEqual(payload["hip_id"], self.health_facility.hf_id)
        receipt_url = payload["acknowledgement"]["payment_receipt_link"]
        self.assertIn(str(order.external_id), receipt_url)
        claims = receipt_claims(receipt_url)
        self.assertEqual(claims["sub"], str(order.external_id))
        self.assertEqual(claims["type"], "scan_pay_receipt")
        self.assertNotIn("exp", claims)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_notify_waits_for_commit(self, mock_task):
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-COMMIT",
        )
        PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-COMMIT",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        with (
            self.captureOnCommitCallbacks() as callbacks,
            allow_open_payment_link_mutation(),
        ):
            baker.make(
                PaymentReconciliation,
                facility=self.facility,
                account=self.account,
                target_invoice=invoice,
                reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
                status=PaymentReconciliationStatusOptions.active.value,
                amount=Decimal(100),
                tendered_amount=Decimal(100),
                returned_amount=Decimal(0),
                reference_number="pay_commit",
            )

        # the PHR is only told once the payment is durably recorded
        mock_task.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_partial_payment_notifies_pending(self, mock_task):
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-2",
        )
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-2",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        with (
            self.captureOnCommitCallbacks(execute=True),
            allow_open_payment_link_mutation(),
        ):
            baker.make(
                PaymentReconciliation,
                facility=self.facility,
                account=self.account,
                target_invoice=invoice,
                reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
                status=PaymentReconciliationStatusOptions.active.value,
                amount=Decimal(40),
                tendered_amount=Decimal(40),
                returned_amount=Decimal(0),
                reference_number="pay_partial",
            )

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PENDING)
        payload = mock_task.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "PENDING")
        self.assertIsNone(payload["acknowledgement"]["payment_receipt_link"])


class TestCreatePaymentReconciliation(ScanPayTestBase):
    def make_order(self, amount=Decimal(100), **fields):
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-REC",
        )
        return PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-REC",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
            amount=amount,
            **fields,
        )

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    @patch(
        "abdm.service.v3.payment_providers.reconciliation.rebalance_account_task.delay"
    )
    def test_records_once_and_rebalances_on_commit(self, mock_rebalance, mock_task):
        order = self.make_order()

        with self.captureOnCommitCallbacks(execute=True):
            first = payment_providers.create_payment_reconciliation(order, "ref1")
            second = payment_providers.create_payment_reconciliation(order, "ref1")
            by_number = payment_providers.reconcile_payment_order("INV-REC", "ref2")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(by_number, order)
        self.assertEqual(
            PaymentReconciliation.objects.filter(target_invoice=order.invoice).count(),
            1,
        )
        mock_rebalance.assert_called_once_with(self.account.id)
        mock_task.assert_called_once()

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_stale_snapshots_cannot_double_credit(self, _task):
        order = self.make_order()
        snapshot = PaymentOrder.objects.get(pk=order.pk)

        payment_providers.create_payment_reconciliation(order, "ref1")
        recorded = payment_providers.create_payment_reconciliation(snapshot, "ref2")

        self.assertFalse(recorded)
        self.assertEqual(
            PaymentReconciliation.objects.filter(target_invoice=order.invoice).count(),
            1,
        )

    def test_settlement_in_progress_elsewhere_raises(self):
        order = self.make_order()

        with (
            payment_providers.PaymentOrderLock(order),
            self.assertRaises(ObjectLocked),
        ):
            payment_providers.create_payment_reconciliation(order, "ref1")

        self.assertFalse(
            PaymentReconciliation.objects.filter(target_invoice=order.invoice).exists()
        )

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_records_confirmed_amount_not_invoice_total(self, _task):
        order = self.make_order()
        Invoice.objects.filter(pk=order.invoice_id).update(total_gross=Decimal(150))

        payment_providers.create_payment_reconciliation(order, "ref1", amount="100.00")

        reconciliation = PaymentReconciliation.objects.get(target_invoice=order.invoice)
        self.assertEqual(reconciliation.amount, Decimal(100))
        self.assertNotIn("Needs review", reconciliation.note)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_falls_back_to_order_amount_and_flags_mismatch(self, _task):
        order = self.make_order(amount=Decimal(80))

        payment_providers.create_payment_reconciliation(order, "ref1")
        reconciliation = PaymentReconciliation.objects.get(target_invoice=order.invoice)
        self.assertEqual(reconciliation.amount, Decimal(80))

        other = self.make_order()
        payment_providers.create_payment_reconciliation(other, "ref2", amount="60")
        reconciliation = PaymentReconciliation.objects.get(target_invoice=other.invoice)
        self.assertEqual(reconciliation.amount, Decimal(60))
        self.assertIn("amount mismatch", reconciliation.note)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_late_payment_after_cancel_is_recorded_and_flagged(self, mock_task):
        order = self.make_order()
        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)
        order.refresh_from_db()
        self.assertEqual(order.invoice.status, InvoiceStatusOptions.cancelled.value)

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.reconcile_payment_order("INV-REC", "late-ref")

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        reconciliation = PaymentReconciliation.objects.get(target_invoice=order.invoice)
        self.assertIn("cancelled", reconciliation.note)
        mock_task.assert_called_once()


class TestOrderStatus(ScanPayTestBase):
    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_success(self, mock_gateway):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            order_number="INV-3",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        response = self.post_callback(
            ORDER_STATUS_URL,
            {
                "queryStatus": {
                    "orderNumber": "INV-3",
                    "abhaAddress": self.abha_number.health_id,
                    "openOrderRequestId": str(order.open_order_request_id),
                }
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "PENDING")

    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_unknown_order(self, mock_gateway):
        response = self.post_callback(
            ORDER_STATUS_URL,
            {
                "queryStatus": {
                    "orderNumber": "INV-404",
                    "abhaAddress": self.abha_number.health_id,
                    "openOrderRequestId": uuid(),
                }
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    def query_status(self, order):
        return {
            "queryStatus": {
                "orderNumber": order.order_number,
                "abhaAddress": self.abha_number.health_id,
                "openOrderRequestId": order.open_order_request_id,
            }
        }

    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_for_failed_order_carries_error(self, mock_gateway):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            order_number="INV-FAILED",
            status=PaymentOrderStatus.FAIL,
        )

        handle_scan_pay_order_status(self.query_status(order), {"REQUEST-ID": uuid()})

        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "FAIL")
        self.assertEqual(payload["error"]["code"], "ABDM-9999")
        self.assertIn("failed", payload["error"]["message"].lower())

    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_for_expired_order_reports_fail(self, mock_gateway):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            order_number="INV-EXPIRED",
            status=PaymentOrderStatus.EXPIRED,
        )

        handle_scan_pay_order_status(self.query_status(order), {"REQUEST-ID": uuid()})

        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "FAIL")
        self.assertEqual(payload["error"]["code"], "ABDM-9999")
        self.assertIn("expired", payload["error"]["message"].lower())

    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_for_pending_order_has_no_error(self, mock_gateway):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            order_number="INV-PENDING",
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        handle_scan_pay_order_status(self.query_status(order), {"REQUEST-ID": uuid()})

        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "PENDING")
        self.assertIsNone(payload["error"])


class TestReceipt(ScanPayTestBase):
    def setUp(self):
        super().setUp()
        # Receipt auth is the query token; a forced staff session would skip it.
        self.client.force_authenticate(user=None)
        self.put_patcher = patch(
            "care.emr.utils.file_manager.S3FilesManager.put_object"
        )
        self.contents_patcher = patch(
            "care.emr.utils.file_manager.S3FilesManager.file_contents",
            return_value=("application/pdf", STORED_RECEIPT_PDF),
        )
        self.put_object = self.put_patcher.start()
        self.file_contents = self.contents_patcher.start()
        self.addCleanup(self.put_patcher.stop)
        self.addCleanup(self.contents_patcher.stop)

    def test_receipt_not_available_for_unpaid_order(self):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 404)
        self.put_object.assert_not_called()

    def test_receipt_rejects_a_bad_token(self):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            status=PaymentOrderStatus.SUCCESS,
        )
        other = jwt.encode(
            {"sub": str(order.external_id), "type": "password_reset"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        token = scan_pay_receipt_token(order)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        mismatched = jwt.encode(
            {"sub": str(uuid()), "type": "scan_pay_receipt"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        self.assertEqual(
            self.client.get(receipt_path(order, token=other)).status_code, 401
        )
        self.assertEqual(
            self.client.get(receipt_path(order, token=tampered)).status_code, 401
        )
        self.assertEqual(
            self.client.get(receipt_path(order, token=mismatched)).status_code, 401
        )
        self.assertEqual(
            self.client.get(
                f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/"
            ).status_code,
            401,
        )
        self.put_object.assert_not_called()

    def test_receipt_for_paid_order(self):
        charge_item = self.create_charge_item()
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.balanced.value,
            total_gross=Decimal(100),
            number="INV-4",
            charge_items=[charge_item.id],
        )
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-4",
            status=PaymentOrderStatus.SUCCESS,
            transaction_id="pay_test",
        )

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("receipt-INV-4.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(self.put_object.call_count, 1)
        self.file_contents.assert_not_called()
        stored = FileUpload.objects.get(associating_id=str(order.external_id))
        self.assertEqual(stored.file_type, RECEIPT_FILE_TYPE)
        self.assertTrue(stored.upload_completed)
        self.assertEqual(stored.meta["mime_type"], "application/pdf")
        self.assertIn(RECEIPT_FINGERPRINT_META, stored.meta)

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, STORED_RECEIPT_PDF)
        self.assertEqual(self.put_object.call_count, 1)
        self.assertEqual(self.file_contents.call_count, 1)

    def test_receipt_for_cancelled_failed_order(self):
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-FAIL",
            charge_items=[],
        )
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-FAIL",
            status=PaymentOrderStatus.FAIL,
        )

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 404)
        self.put_object.assert_not_called()

        invoice.status = InvoiceStatusOptions.cancelled.value
        invoice.cancelled_reason = "Scan and pay payment failed (order INV-FAIL)"
        invoice.save(update_fields=["status", "cancelled_reason"])

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.put_object.call_count, 1)
        self.assertEqual(
            build_receipt_context(order)["cancelled_reason"],
            invoice.cancelled_reason,
        )

        invoice.cancelled_reason = "Replaced after the receipt was stored"
        invoice.save(update_fields=["cancelled_reason"])

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.put_object.call_count, 2)
        self.assertEqual(
            FileUpload.objects.filter(associating_id=str(order.external_id)).count(),
            1,
        )

    def test_receipt_matches_invoice_print(self):
        self.patient.name = "Sam Das"
        self.patient.gender = "male"
        self.patient.date_of_birth = date(1997, 1, 15)
        self.patient.address = "Mangarath (H), Puthenpeedika"
        identifier = baker.make(
            PatientIdentifierConfig,
            facility=None,
            status="active",
            config={
                "use": "official",
                "description": "",
                "system": "https://example.org/uhid",
                "required": False,
                "unique": True,
                "regex": ".*",
                "display": "UHID",
                "auto_maintained": False,
            },
        )
        self.patient.instance_identifiers = [
            {"config": str(identifier.external_id), "value": "20260000397"}
        ]
        self.patient.save()
        self.user.first_name = "abdm_user_internal"
        self.user.last_name = ""
        self.user.prefix = None
        self.user.suffix = None
        self.user.save(update_fields=["first_name", "last_name", "prefix", "suffix"])

        charge_item = self.create_charge_item(amount=20)
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.balanced.value,
            total_net=Decimal(20),
            total_gross=Decimal(20),
            number="INV-489-26",
            issue_date=care_now(),
            charge_items=[charge_item.id],
            charge_items_copy=[
                {
                    "title": "IP Admission\nIP Charges",
                    "quantity": "1",
                    "unit_price_components": [
                        {"monetary_component_type": "base", "amount": "20"}
                    ],
                    "total_price_components": [
                        {"monetary_component_type": "base", "amount": "20"},
                        {"monetary_component_type": "discount", "amount": "0"},
                    ],
                    "total_price": "20",
                }
            ],
            total_price_components=[
                {"monetary_component_type": "base", "amount": "20"}
            ],
            created_by=self.user,
        )
        PaymentReconciliation.objects.create(
            facility=self.facility,
            target_invoice=invoice,
            account=self.account,
            reconciliation_type="payment",
            status="active",
            kind="online",
            issuer_type="patient",
            outcome="complete",
            method="ccca",
            payment_datetime=care_now(),
            reference_number="4651842147310",
            tendered_amount=Decimal(20),
            returned_amount=Decimal(0),
            amount=Decimal(20),
        )
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-489-26",
            status=PaymentOrderStatus.SUCCESS,
        )

        context = build_receipt_context(order)
        self.assertEqual(context["invoice_number"], "INV-489-26")
        self.assertEqual(context["patient_name"], "Sam Das")
        self.assertIn("Male", context["patient_meta"])
        self.assertTrue(context["patient_meta"].endswith("y"))
        self.assertEqual(
            context["identifiers"],
            [{"label": "UHID", "value": "20260000397"}],
        )
        self.assertEqual(context["rows"][0]["title"], "IP Admission\nIP Charges")
        self.assertEqual(context["rows"][0]["unit_price"], "20.00")
        self.assertEqual(context["rows"][0]["quantity"], "1.00")
        self.assertEqual(context["rows"][0]["discount"], "0.00")
        self.assertEqual(context["rows"][0]["total"], "20.00")
        self.assertEqual(context["summary_lines"][0]["label"], "Base Amount:")
        self.assertEqual(context["summary_lines"][0]["value"], "₹20.00")
        self.assertEqual(context["total"], "₹20.00")
        self.assertEqual(context["payments"][0]["method"], "Credit Card")
        self.assertEqual(context["payments"][0]["reference"], "4651842147310")
        self.assertEqual(context["total_received"], "₹20.00")
        self.assertEqual(context["created_by"], "abdm_user_internal")

        html = render_to_string("abdm/scan_and_pay_receipt.html", context)
        self.assertIn("Bill To", html)
        self.assertIn("Total Received", html)
        self.assertIn("INV-489-26", html)

        response = self.client.get(receipt_path(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertTrue(self.put_object.call_args.args[1].startswith(b"%PDF"))


class TestPaymentProviderRegistry(ScanPayTestBase):
    def make_invoice(self):
        return baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-REG",
        )

    def test_dispatches_to_registered_provider(self):
        captured = {}

        class DummyProvider(payment_providers.PaymentProvider):
            name = "dummy_test"

            def create_payment_link(self, invoice):
                captured["invoice"] = invoice
                return {
                    "order_number": "ord1",
                    "payment_link_id": "pl1",
                    "payment_url": "https://pay/x",
                }

        payment_providers.register_provider(DummyProvider)
        self.addCleanup(payment_providers.unregister_provider, "dummy_test")

        invoice = self.make_invoice()
        with patch.object(
            scan_pay_service.settings, "ABDM_SCAN_AND_PAY_PROVIDER", "dummy_test"
        ):
            result = scan_pay_service.create_scan_pay_payment_link(invoice)

        self.assertEqual(result["payment_url"], "https://pay/x")
        self.assertEqual(result["amount"], Decimal(100))
        self.assertIs(captured["invoice"], invoice)

    def test_unknown_provider_raises(self):
        invoice = self.make_invoice()
        with (
            patch.object(
                scan_pay_service.settings,
                "ABDM_SCAN_AND_PAY_PROVIDER",
                "does_not_exist",
            ),
            self.assertRaises(ImproperlyConfigured),
        ):
            scan_pay_service.create_scan_pay_payment_link(invoice)


class TestClosePaymentOrder(ScanPayTestBase):
    def make_order(self, order_status):
        return PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            order_number="INV-CLOSE",
            status=order_status,
        )

    def make_invoiced_order(self, order_status=PaymentOrderStatus.PAYMENT_INITIATED):
        charge_item = self.create_charge_item(
            status=ChargeItemStatusOptions.billed.value
        )
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=InvoiceStatusOptions.issued.value,
            total_gross=Decimal(100),
            number="INV-CLOSE",
            charge_items=[charge_item.id],
        )
        charge_item.paid_invoice = invoice
        charge_item.save(update_fields=["paid_invoice"])
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number="INV-CLOSE",
            status=order_status,
        )
        return order, invoice, charge_item

    def test_closes_pending_order(self):
        order = self.make_order(PaymentOrderStatus.PAYMENT_INITIATED)

        changed = payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        order.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_closing_notifies_phr_after_commit(self, mock_task):
        order = self.make_order(PaymentOrderStatus.PAYMENT_INITIATED)

        with self.captureOnCommitCallbacks() as callbacks:
            payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)
        mock_task.assert_not_called()
        self.assertEqual(len(callbacks), 1)

        callbacks[0]()

        payload, kwargs = mock_task.call_args.args[0], mock_task.call_args.kwargs
        self.assertEqual(payload["acknowledgement"]["status"], "FAIL")
        self.assertEqual(payload["acknowledgement"]["order_number"], "INV-CLOSE")
        self.assertIsNone(payload["acknowledgement"]["payment_receipt_link"])
        self.assertIn("failed", payload["error"]["message"].lower())
        self.assertEqual(payload["hip_id"], self.health_facility.hf_id)
        self.assertEqual(kwargs["transaction_meta"]["status"], "FAIL")

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_expiring_reports_abdm_fail_with_reason(self, mock_task):
        order, invoice, _ = self.make_invoiced_order()

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.close_payment_order(order, PaymentOrderStatus.EXPIRED)

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.EXPIRED)
        payload, kwargs = mock_task.call_args.args[0], mock_task.call_args.kwargs
        self.assertEqual(payload["acknowledgement"]["status"], "FAIL")
        self.assertIn("expired", payload["error"]["message"].lower())
        self.assertEqual(payload["error"]["message"], invoice.cancelled_reason)
        self.assertEqual(kwargs["transaction_meta"]["status"], "EXPIRED")

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_cancelling_notifies_phr(self, mock_task):
        order = self.make_order(PaymentOrderStatus.PAYMENT_INITIATED)

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.close_payment_order(order, PaymentOrderStatus.CANCELED)

        payload = mock_task.call_args.args[0]
        self.assertEqual(payload["acknowledgement"]["status"], "CANCELED")

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_closing_a_closed_order_does_not_notify_again(self, mock_task):
        order = self.make_order(PaymentOrderStatus.FAIL)

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        mock_task.assert_not_called()

    def test_leaves_paid_order_untouched(self):
        order = self.make_order(PaymentOrderStatus.SUCCESS)

        changed = payment_providers.close_payment_order(
            order, PaymentOrderStatus.CANCELED
        )

        order.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)

    def test_closing_cancels_invoice_and_releases_charge_items(self):
        order, invoice, charge_item = self.make_invoiced_order()

        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)
        self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
        self.assertIn("failed", invoice.cancelled_reason)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
        self.assertIsNone(charge_item.paid_invoice_id)

    def test_cancelling_records_reason(self):
        order, invoice, _ = self.make_invoiced_order()

        payment_providers.close_payment_order(order, PaymentOrderStatus.CANCELED)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
        self.assertIn("cancelled", invoice.cancelled_reason)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_closing_keeps_invoice_that_received_a_payment(self, mock_task):
        order, invoice, charge_item = self.make_invoiced_order()
        with allow_open_payment_link_mutation():
            baker.make(
                PaymentReconciliation,
                facility=self.facility,
                account=self.account,
                target_invoice=invoice,
                reconciliation_type=PaymentReconciliationTypeOptions.payment.value,
                status=PaymentReconciliationStatusOptions.active.value,
                amount=Decimal(40),
                tendered_amount=Decimal(40),
                returned_amount=Decimal(0),
                reference_number="pay_partial",
            )
        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PENDING)

        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billed.value)

    def test_stale_order_sweep_cancels_invoice(self):
        order, invoice, charge_item = self.make_invoiced_order()
        stale = care_now() - timedelta(
            seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + 60
        )
        PaymentOrder.objects.filter(pk=order.pk).update(created_date=stale)

        reconcile_pending_payment_orders()

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.EXPIRED)
        self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
        self.assertIn("expired", invoice.cancelled_reason)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)

    def register_sweep_provider(self, reconcile):
        class SweepProvider(payment_providers.PaymentProvider):
            name = "sweep_test"

            def create_payment_link(self, invoice):
                raise NotImplementedError

            def reconcile_order(self, order):
                reconcile(order)

        payment_providers.register_provider(SweepProvider)
        self.addCleanup(payment_providers.unregister_provider, "sweep_test")

    def make_stale_order(self, extra_seconds=60):
        order, invoice, _ = self.make_invoiced_order()
        PaymentOrder.objects.filter(pk=order.pk).update(
            provider="sweep_test",
            created_date=care_now()
            - timedelta(
                seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + extra_seconds
            ),
        )
        return order, invoice

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_stale_order_is_polled_before_being_failed(self, _task):
        self.register_sweep_provider(
            lambda order: payment_providers.create_payment_reconciliation(
                order, "late-pay"
            )
        )
        order, invoice = self.make_stale_order()

        reconcile_pending_payment_orders()

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        self.assertEqual(invoice.status, InvoiceStatusOptions.balanced.value)

    def test_stale_order_survives_unreachable_provider(self):
        def unreachable(order):
            raise ConnectionError("gateway down")

        self.register_sweep_provider(unreachable)
        order, invoice = self.make_stale_order()

        reconcile_pending_payment_orders()

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)

    def test_unanswered_order_is_backed_off_then_polled_again(self):
        polled = []

        def unreachable(order):
            polled.append(order.order_number)
            raise requests.Timeout("read timed out")

        self.register_sweep_provider(unreachable)
        order, _ = self.make_stale_order()

        reconcile_pending_payment_orders()
        reconcile_pending_payment_orders()

        self.assertEqual(len(polled), 1)
        self.assertTrue(cache.get(scan_pay_tasks.backoff_key(order)))

        cache.delete(scan_pay_tasks.backoff_key(order))
        reconcile_pending_payment_orders()

        self.assertEqual(len(polled), 2)

    def test_answered_order_is_not_backed_off(self):
        polled = []
        self.register_sweep_provider(polled.append)
        order, _, _ = self.make_invoiced_order()
        PaymentOrder.objects.filter(pk=order.pk).update(provider="sweep_test")

        reconcile_pending_payment_orders()
        reconcile_pending_payment_orders()

        self.assertEqual(len(polled), 2)
        self.assertIsNone(cache.get(scan_pay_tasks.backoff_key(order)))

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_never_answered_order_is_failed_at_the_hard_cap(self, mock_task):
        def unreachable(order):
            raise requests.Timeout("read timed out")

        self.register_sweep_provider(unreachable)
        order, invoice = self.make_stale_order(
            extra_seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_HARD_CAP + 60
        )

        with self.captureOnCommitCallbacks(execute=True):
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)
        self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
        self.assertIn("could not be reached", invoice.cancelled_reason)
        self.assertEqual(
            mock_task.call_args.args[0]["acknowledgement"]["status"], "FAIL"
        )
        self.assertIn(
            "could not be reached",
            mock_task.call_args.args[0]["error"]["message"],
        )

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_order_for_unregistered_provider_is_failed_at_the_hard_cap(self, _task):
        order, _ = self.make_stale_order(
            extra_seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_HARD_CAP + 60
        )
        PaymentOrder.objects.filter(pk=order.pk).update(provider="not_installed")

        with self.assertLogs("abdm.tasks.scan_pay", level="ERROR"):
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)

    def test_idle_tick_does_not_take_the_lock(self):
        self.make_order(PaymentOrderStatus.SUCCESS)

        with patch("abdm.tasks.scan_pay.Lock") as mock_lock:
            reconcile_pending_payment_orders()

        mock_lock.assert_not_called()

    def test_sweep_skips_order_being_settled_elsewhere(self):
        self.register_sweep_provider(lambda order: None)
        order, _ = self.make_stale_order()

        with payment_providers.PaymentOrderLock(order):
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)

    def test_sweep_logs_unreachable_provider_without_a_traceback(self):
        def unreachable(order):
            raise requests.Timeout("read timed out")

        self.register_sweep_provider(unreachable)
        order, _ = self.make_stale_order()

        with self.assertLogs("abdm.tasks.scan_pay", level="WARNING") as logs:
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])
        self.assertIsNone(logs.records[0].exc_info)

    def test_sweep_skips_tick_while_previous_run_is_going(self):
        polled = []
        self.register_sweep_provider(polled.append)
        order, _ = self.make_stale_order()

        with Lock(POLL_LOCK_KEY):
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        self.assertEqual(polled, [])
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
