from decimal import Decimal
from unittest.mock import patch

import requests
from abdm.models import PaymentOrder
from abdm.models.payment_order import PaymentOrderStatus
from abdm.service.helper import uuid
from abdm.service.v3 import payment_providers
from abdm.settings import plugin_settings as abdm_settings
from model_bakery import baker

from care.emr.locks.billing import InvoiceLock
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from tests.test_scan_and_pay import ScanPayTestBase

ORDERS_URL = "/api/abdm/v3/scan-pay/orders/"
PROVIDER = "orders_test"


class ScanPayOrderTestBase(ScanPayTestBase):
    def setUp(self):
        super().setUp()
        self.reconcile_calls = []
        self.reconcile = lambda order: payment_providers.create_payment_reconciliation(
            order, "gw-ref", amount="100"
        )
        base = self

        class OrdersTestProvider(payment_providers.PaymentProvider):
            name = PROVIDER

            def create_payment_link(self, invoice):
                raise NotImplementedError

            def reconcile_order(self, order):
                base.reconcile_calls.append(order.order_number)
                return base.reconcile(order)

        payment_providers.register_provider(OrdersTestProvider)
        self.addCleanup(payment_providers.unregister_provider, PROVIDER)

    def make_order(
        self,
        order_status=PaymentOrderStatus.PAYMENT_INITIATED,
        number="ORD-1",
        with_invoice=True,
        **fields,
    ):
        invoice = charge_item = None
        if with_invoice:
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
                number=number,
                charge_items=[charge_item.id],
            )
            charge_item.paid_invoice = invoice
            charge_item.save(update_fields=["paid_invoice"])
        fields.setdefault("provider", PROVIDER)
        fields.setdefault("amount", Decimal(100))
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            order_number=number,
            status=order_status,
            **fields,
        )
        return order, invoice, charge_item

    def detail_url(self, order, action=""):
        return f"{ORDERS_URL}{order.external_id}/{action}"

    def post(self, order, action, body=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                self.detail_url(order, action), body or {}, format="json"
            )


class TestScanPayOrderList(ScanPayOrderTestBase):
    def test_requires_facility(self):
        response = self.client.get(ORDERS_URL)

        self.assertEqual(response.status_code, 400)

    def test_lists_facility_orders_with_invoice_and_patient(self):
        order, invoice, _ = self.make_order()
        other_facility = self.create_facility(self.user)
        other_hf = baker.make(
            "abdm.HealthFacility", facility=other_facility, hf_id="OTHER_HIP"
        )
        PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=other_hf,
            order_number="ELSEWHERE",
        )

        response = self.client.get(ORDERS_URL, {"facility": self.facility.external_id})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], str(order.external_id))
        self.assertTrue(row["is_pending"])
        self.assertEqual(row["provider"], PROVIDER)
        self.assertEqual(row["invoice"]["id"], str(invoice.external_id))
        self.assertEqual(row["invoice"]["number"], "ORD-1")
        self.assertEqual(row["patient"]["name"], self.patient.name)
        self.assertEqual(row["abha_address"], self.abha_number.health_id)
        self.assertIsNone(row["receipt_url"])

    def test_filters(self):
        pending, invoice, _ = self.make_order(number="PEND")
        paid, _, _ = self.make_order(PaymentOrderStatus.SUCCESS, number="PAID")
        failed, _, _ = self.make_order(PaymentOrderStatus.FAIL, number="FAILED")
        base = {"facility": self.facility.external_id}

        def ids(params):
            response = self.client.get(ORDERS_URL, {**base, **params})
            self.assertEqual(response.status_code, 200, response.data)
            return {row["id"] for row in response.data["results"]}

        self.assertEqual(ids({"pending": "true"}), {str(pending.external_id)})
        self.assertEqual(
            ids({"pending": "false"}),
            {str(paid.external_id), str(failed.external_id)},
        )
        self.assertEqual(
            ids({"status": "SUCCESS,FAIL"}),
            {str(paid.external_id), str(failed.external_id)},
        )
        self.assertEqual(
            ids({"invoice": str(invoice.external_id)}), {str(pending.external_id)}
        )
        self.assertEqual(ids({"invoice_number": "pai"}), {str(paid.external_id)})

    def test_paid_order_exposes_receipt(self):
        order, _, _ = self.make_order(PaymentOrderStatus.SUCCESS)

        response = self.client.get(ORDERS_URL, {"facility": self.facility.external_id})

        self.assertIn(
            str(order.external_id), response.data["results"][0]["receipt_url"]
        )

    def test_requires_read_permission(self):
        self.make_order()
        self.client.force_authenticate(user=self.create_user())

        response = self.client.get(ORDERS_URL, {"facility": self.facility.external_id})

        self.assertEqual(response.status_code, 403)


class TestCheckStatus(ScanPayOrderTestBase):
    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_settles_and_balances_when_provider_reports_paid(self, mock_notify):
        order, invoice, charge_item = self.make_order()

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["gateway_checked"])
        self.assertEqual(response.data["order"]["status"], "SUCCESS")
        self.assertEqual(self.reconcile_calls, ["ORD-1"])
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.balanced.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.paid.value)
        self.assertEqual(charge_item.paid_invoice_id, invoice.id)
        self.assertIsNotNone(charge_item.paid_on)
        mock_notify.assert_called_once()

    def test_reports_when_gateway_did_not_answer(self):
        order, _, _ = self.make_order()
        self.reconcile = lambda order: False

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["gateway_checked"])
        self.assertEqual(response.data["order"]["status"], "PAYMENT_INITIATED")

    def test_settled_order_skips_provider(self):
        order, _, _ = self.make_order(PaymentOrderStatus.SUCCESS)

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["gateway_checked"])
        self.assertIn("no longer pending", response.data["detail"])
        self.assertEqual(self.reconcile_calls, [])

    def test_unreachable_gateway_is_502(self):
        order, _, _ = self.make_order()

        def unreachable(order):
            raise requests.Timeout("read timed out")

        self.reconcile = unreachable

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 502)
        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)

    def test_provider_error_is_502(self):
        order, _, _ = self.make_order()

        def broken(order):
            raise RuntimeError("bad response")

        self.reconcile = broken

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 502)
        self.assertIn("bad response", response.data["detail"])

    def test_unregistered_provider_is_503(self):
        order, _, _ = self.make_order(provider="not_installed")

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 503)

    def test_order_being_settled_elsewhere_is_423(self):
        order, _, _ = self.make_order()

        with payment_providers.PaymentOrderLock(order):
            response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 423)

    def test_requires_write_permission(self):
        order, _, _ = self.make_order()
        self.client.force_authenticate(user=self.create_user())

        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reconcile_calls, [])


class TestCancel(ScanPayOrderTestBase):
    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_cancels_pending_order_and_voids_invoice(self, mock_notify):
        order, invoice, charge_item = self.make_order()

        response = self.post(order, "cancel/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["order"]["status"], "CANCELED")
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.entered_in_error.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
        payload = mock_notify.call_args.args[0]
        self.assertEqual(payload["acknowledgement"]["status"], "CANCELED")

    def test_non_pending_order_is_409(self):
        order, invoice, _ = self.make_order(PaymentOrderStatus.SUCCESS)

        response = self.post(order, "cancel/")

        self.assertEqual(response.status_code, 409)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)

    def test_requires_write_permission(self):
        order, _, _ = self.make_order()
        self.client.force_authenticate(user=self.create_user())

        response = self.post(order, "cancel/")

        self.assertEqual(response.status_code, 403)


class TestMarkPaid(ScanPayOrderTestBase):
    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_records_payment_by_staff(self, mock_notify):
        order, invoice, _ = self.make_order()

        response = self.post(
            order,
            "mark-paid/",
            {"reference_number": " UTR123 ", "note": "Seen in bank portal"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["order"]["status"], "SUCCESS")
        reconciliation = PaymentReconciliation.objects.get(target_invoice=invoice)
        self.assertEqual(reconciliation.reference_number, "UTR123")
        self.assertEqual(reconciliation.created_by, self.user)
        self.assertEqual(reconciliation.amount, Decimal(100))
        self.assertIn(
            f"Confirmed manually by {self.user.username}", reconciliation.note
        )
        self.assertIn("Seen in bank portal", reconciliation.note)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.balanced.value)
        mock_notify.assert_called_once()

    def test_requires_reference(self):
        order, _, _ = self.make_order()

        response = self.post(order, "mark-paid/", {"reference_number": ""})

        self.assertEqual(response.status_code, 400)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_paid_order_is_409(self, _notify):
        order, _, _ = self.make_order()
        self.post(order, "mark-paid/", {"reference_number": "once"})

        response = self.post(order, "mark-paid/", {"reference_number": "twice"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            PaymentReconciliation.objects.filter(target_invoice=order.invoice).count(),
            1,
        )

    def test_order_without_invoice_is_409(self):
        order, _, _ = self.make_order(
            PaymentOrderStatus.OPEN_ORDER_SHARED, with_invoice=False
        )

        response = self.post(order, "mark-paid/", {"reference_number": "ref"})

        self.assertEqual(response.status_code, 409)

    def test_requires_write_permission(self):
        order, _, _ = self.make_order()
        self.client.force_authenticate(user=self.create_user())

        response = self.post(order, "mark-paid/", {"reference_number": "ref"})

        self.assertEqual(response.status_code, 403)


class TestAutoBalance(ScanPayOrderTestBase):
    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_can_be_disabled(self, _notify):
        order, invoice, charge_item = self.make_order()

        with (
            patch.object(
                abdm_settings, "ABDM_SCAN_AND_PAY_AUTO_BALANCE_INVOICE", new=False
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            payment_providers.create_payment_reconciliation(order, "ref")

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billed.value)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_partial_payment_leaves_invoice_issued(self, _notify):
        order, invoice, _ = self.make_order()

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.create_payment_reconciliation(order, "ref", amount="40")

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PENDING)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_locked_invoice_does_not_block_settlement(self, _notify):
        order, invoice, _ = self.make_order()

        with InvoiceLock(invoice), self.captureOnCommitCallbacks(execute=True):
            recorded = payment_providers.create_payment_reconciliation(order, "ref")

        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertTrue(recorded)
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)

    @patch("abdm.tasks.scan_pay.scan_pay_notify.delay")
    def test_voided_invoice_is_left_alone(self, _notify):
        order, invoice, charge_item = self.make_order()
        payment_providers.close_payment_order(order, PaymentOrderStatus.FAIL)

        with self.captureOnCommitCallbacks(execute=True):
            payment_providers.create_payment_reconciliation(order, "late")

        order.refresh_from_db()
        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.SUCCESS)
        self.assertEqual(invoice.status, InvoiceStatusOptions.entered_in_error.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
