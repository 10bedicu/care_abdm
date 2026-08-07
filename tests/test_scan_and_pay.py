from decimal import Decimal
from unittest.mock import patch

from abdm.models import AbhaNumber, HealthFacility, InboundCallback, PaymentOrder
from abdm.models.inbound_callback import CallbackStatus
from abdm.models.payment_order import PaymentOrderStatus
from abdm.service.helper import uuid
from model_bakery import baker

from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationStatusOptions,
    PaymentReconciliationTypeOptions,
)
from care.utils.tests.base import CareAPITestBase

SHARE_OPEN_ORDER_URL = "/api/abdm/api/v3/patient/share/open-order"
SELECTION_URL = "/api/abdm/api/v3/patient/selection"
ORDER_STATUS_URL = "/api/abdm/api/v3/patient/scan-pay/order-status"


class ScanPayTestBase(CareAPITestBase):
    def setUp(self):
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
            "emr.Account", facility=self.facility, patient=self.patient
        )
        self.client.force_authenticate(user=self.user)

    def create_charge_item(self, **kwargs):
        amount = kwargs.pop("amount", 100)
        item_status = kwargs.pop("status", ChargeItemStatusOptions.billable.value)
        return baker.make(
            ChargeItem,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
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


class TestShareOpenOrder(ScanPayTestBase):
    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_success(self, mock_gateway):
        self.create_charge_item()
        request_id = uuid()

        response = self.client.post(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(),
            format="json",
            headers={"REQUEST-ID": request_id},
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
        response = self.client.post(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(abha_address="unknown@sbx"),
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_no_open_orders(self, mock_gateway):
        response = self.client.post(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(),
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_share_open_order")
    def test_share_open_order_unknown_facility(self, mock_gateway):
        request_id = uuid()

        response = self.client.post(
            SHARE_OPEN_ORDER_URL,
            self.share_open_order_payload(hip_id="UNKNOWN_HIP"),
            format="json",
            headers={"REQUEST-ID": request_id},
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
            response = self.client.post(
                SHARE_OPEN_ORDER_URL,
                self.share_open_order_payload(),
                format="json",
                headers={"REQUEST-ID": request_id},
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

    @patch("abdm.service.v3.callback_handlers.scan_pay.create_scan_pay_payment_link")
    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_success(self, mock_gateway, mock_payment_link):
        mock_payment_link.return_value = {
            "id": "plink_test",
            "short_url": "https://rzp.io/test",
        }
        order = self.create_order()
        charge_item = self.create_charge_item()

        response = self.client.post(
            SELECTION_URL,
            self.selection_payload(order, [charge_item]),
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)

        order.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)
        self.assertEqual(order.payment_link_id, "plink_test")
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

        response = self.client.post(
            SELECTION_URL,
            self.selection_payload(order, [charge_item]),
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])

    @patch("abdm.service.v3.gateway.GatewayService.patient__on_selection")
    def test_selection_unknown_order(self, mock_gateway):
        charge_item = self.create_charge_item()
        order = PaymentOrder(open_order_request_id=uuid())

        response = self.client.post(
            SELECTION_URL,
            self.selection_payload(order, [charge_item]),
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])


class TestPaymentNotify(ScanPayTestBase):
    @patch("abdm.signals.scan_pay.scan_pay_notify.delay")
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

    @patch("abdm.signals.scan_pay.scan_pay_notify.delay")
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

        response = self.client.post(
            ORDER_STATUS_URL,
            {
                "queryStatus": {
                    "orderNumber": "INV-3",
                    "abhaAddress": self.abha_number.health_id,
                    "openOrderRequestId": str(order.open_order_request_id),
                }
            },
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        payload = mock_gateway.call_args[0][0]
        self.assertEqual(payload["acknowledgement"]["status"], "PENDING")

    @patch("abdm.service.v3.gateway.GatewayService.patient__scan_pay_on_order_status")
    def test_order_status_unknown_order(self, mock_gateway):
        response = self.client.post(
            ORDER_STATUS_URL,
            {
                "queryStatus": {
                    "orderNumber": "INV-404",
                    "abhaAddress": self.abha_number.health_id,
                    "openOrderRequestId": uuid(),
                }
            },
            format="json",
            headers={"REQUEST-ID": uuid()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertIn("error", mock_gateway.call_args[0][0])


class TestReceipt(ScanPayTestBase):
    def test_receipt_not_available_for_unpaid_order(self):
        order = PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            status=PaymentOrderStatus.PAYMENT_INITIATED,
        )

        response = self.client.get(f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/")
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

        response = self.client.get(f"/api/abdm/v3/scan-pay/receipt/{order.external_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
