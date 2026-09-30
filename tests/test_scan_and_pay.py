from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from abdm.models import AbhaNumber, HealthFacility, InboundCallback, PaymentOrder
from abdm.models.inbound_callback import CallbackStatus
from abdm.models.payment_order import PaymentOrderStatus
from abdm.service.helper import uuid
from abdm.service.v3 import payment_providers
from abdm.service.v3 import scan_pay as scan_pay_service
from abdm.service.v3.callback_handlers.scan_pay import (
    handle_patient_selection,
    handle_patient_share_open_order,
    handle_scan_pay_order_status,
)
from abdm.settings import plugin_settings as abdm_settings
from abdm.tasks.scan_pay import reconcile_pending_payment_orders
from abdm.utils import user as abdm_user
from django.core.exceptions import ImproperlyConfigured
from model_bakery import baker

from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.spec import AccountStatusOptions
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.lock import ObjectLocked
from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now

SHARE_OPEN_ORDER_URL = "/api/abdm/api/v3/patient/share/open-order"
SELECTION_URL = "/api/abdm/api/v3/patient/selection"
ORDER_STATUS_URL = "/api/abdm/api/v3/patient/scan-pay/order-status"


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
    def test_replies_echo_the_requesting_abha_address(self, mock_gateway):
        # patient known under another address; matched via ABHA number
        payload = self.share_open_order_payload(abha_address="other@sbx")
        payload["profile"]["patient"]["abhaNumber"] = self.abha_number.abha_number

        handle_patient_share_open_order(payload, {"REQUEST-ID": uuid()})
        no_orders = mock_gateway.call_args[0][0]
        self.assertIn("error", no_orders)
        self.assertEqual(no_orders["abha_address"], "other@sbx")

        self.create_charge_item()
        request_id = uuid()
        handle_patient_share_open_order(payload, {"REQUEST-ID": request_id})

        shared = mock_gateway.call_args[0][0]
        self.assertEqual(shared["abha_address"], "other@sbx")
        order = PaymentOrder.objects.get(open_order_request_id=request_id)
        self.assertEqual(order.abha_number, self.abha_number)
        self.assertEqual(order.abha_address, "other@sbx")
        self.assertEqual(order.requesting_abha_address, "other@sbx")
        self.assertEqual(
            scan_pay_service.build_scan_pay_acknowledgement(order)["abha_address"],
            "other@sbx",
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

        with self.captureOnCommitCallbacks(execute=True):
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
        self.assertIn("receipt", payload["acknowledgement"]["payment_receipt_link"])

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

        with self.captureOnCommitCallbacks() as callbacks:
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

        with self.captureOnCommitCallbacks(execute=True):
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
    def test_late_payment_after_void_is_recorded_and_flagged(self, mock_task):
        order = self.make_order()
        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)
        order.refresh_from_db()
        self.assertEqual(
            order.invoice.status, InvoiceStatusOptions.entered_in_error.value
        )

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.reconcile_payment_order("INV-REC", "late-ref")

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        reconciliation = PaymentReconciliation.objects.get(target_invoice=order.invoice)
        self.assertIn("voided", reconciliation.note)
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
    def test_receipt_not_available_for_unpaid_order(self):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        response = self.client.get(
            f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/"
        )
        self.assertEqual(response.status_code, 404)

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

        response = self.client.get(
            f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")


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
        self.assertEqual(payload["hip_id"], self.health_facility.hf_id)
        self.assertEqual(kwargs["transaction_meta"]["status"], "FAIL")

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

    def test_closing_voids_invoice_and_releases_charge_items(self):
        order, invoice, charge_item = self.make_invoiced_order()

        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)
        self.assertEqual(invoice.status, InvoiceStatusOptions.entered_in_error.value)
        self.assertIn("failed", invoice.cancelled_reason)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
        self.assertIsNone(charge_item.paid_invoice_id)

    def test_cancelling_records_reason(self):
        order, invoice, _ = self.make_invoiced_order()

        payment_providers.close_payment_order(order, PaymentOrderStatus.CANCELED)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.entered_in_error.value)
        self.assertIn("cancelled", invoice.cancelled_reason)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_closing_keeps_invoice_that_received_a_payment(self, mock_task):
        order, invoice, charge_item = self.make_invoiced_order()
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

    def test_stale_order_sweep_voids_invoice(self):
        order, invoice, charge_item = self.make_invoiced_order()
        stale = care_now() - timedelta(
            seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + 60
        )
        PaymentOrder.objects.filter(pk=order.pk).update(created_date=stale)

        reconcile_pending_payment_orders()

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.FAIL)
        self.assertEqual(invoice.status, InvoiceStatusOptions.entered_in_error.value)
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

    def make_stale_order(self):
        order, invoice, charge_item = self.make_invoiced_order()
        PaymentOrder.objects.filter(pk=order.pk).update(
            provider="sweep_test",
            created_date=care_now()
            - timedelta(seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + 60),
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
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)

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

    def test_sweep_skips_order_being_settled_elsewhere(self):
        self.register_sweep_provider(lambda order: None)
        order, invoice = self.make_stale_order()

        with payment_providers.PaymentOrderLock(order):
            reconcile_pending_payment_orders()

        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
