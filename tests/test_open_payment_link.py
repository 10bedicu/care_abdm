from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from abdm.models import AbhaNumber, HealthFacility
from abdm.models.payment_order import PaymentOrder, PaymentOrderStatus
from abdm.open_payment_link import format_editable_at
from abdm.service.v3.payment_providers import (
    balance_scan_pay_invoice,
    cancel_scan_pay_invoice,
)
from abdm.settings import plugin_settings as abdm_settings
from django.urls import reverse
from model_bakery import baker
from rest_framework.exceptions import ValidationError

from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.utils.tests.base import CareAPITestBase


class TestOpenScanPayPaymentLink(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.health_facility = baker.make(
            HealthFacility, facility=self.facility, hf_id="TEST_HIP"
        )
        self.patient = self.create_patient()
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
        self._seq = 0

    def make_invoice(self, status=InvoiceStatusOptions.issued.value):
        self._seq += 1
        item = baker.make(
            ChargeItem,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=ChargeItemStatusOptions.billed.value,
            title="Service",
            quantity=Decimal("1"),
            total_price=Decimal("100"),
            unit_price_components=[
                {
                    "monetary_component_type": "base",
                    "amount": "100.00",
                    "currency": "INR",
                }
            ],
            tags=[],
        )
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            status=status,
            number=f"INV-ABDM-{self._seq}",
            title="Consultation",
            total_gross=Decimal("100"),
            charge_items=[item.id],
        )
        item.paid_invoice = invoice
        item.save(update_fields=["paid_invoice"])
        return invoice, item

    def open_link(self, invoice, status=PaymentOrderStatus.PAYMENT_INITIATED):
        return PaymentOrder.objects.create(
            open_order_request_id=uuid4(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            invoice=invoice,
            status=status,
            order_number=invoice.number,
            amount=invoice.total_gross,
        )

    def invoice_url(self, invoice):
        return reverse(
            "invoice-detail",
            kwargs={
                "facility_external_id": self.facility.external_id,
                "external_id": invoice.external_id,
            },
        )

    def cancel_url(self, invoice):
        return reverse(
            "invoice-cancel-invoice",
            kwargs={
                "facility_external_id": self.facility.external_id,
                "external_id": invoice.external_id,
            },
        )

    def charge_item_url(self, item):
        return reverse(
            "charge_item-detail",
            kwargs={
                "facility_external_id": self.facility.external_id,
                "external_id": item.external_id,
            },
        )

    def assert_blocked(self, response, order):
        self.assertEqual(response.status_code, 400, response.data)
        message = response.data["errors"][0]["msg"]
        self.assertIn("active payment link", message)
        deadline = order.created_date + timedelta(
            seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE
        )
        self.assertIn(f"until about {format_editable_at(deadline)}", message)

    def test_open_link_blocks_writes_and_allows_read(self):
        for status in (
            PaymentOrderStatus.PAYMENT_INITIATED,
            PaymentOrderStatus.PENDING,
        ):
            with self.subTest(status=status):
                invoice, item = self.make_invoice()
                order = self.open_link(invoice, status)

                self.assert_blocked(
                    self.client.post(
                        self.cancel_url(invoice),
                        {"reason": "cancelled"},
                        format="json",
                    ),
                    order,
                )
                invoice.refresh_from_db()
                item.refresh_from_db()
                self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)
                self.assertEqual(item.status, ChargeItemStatusOptions.billed.value)

                self.assert_blocked(
                    self.client.put(
                        self.invoice_url(invoice),
                        {
                            "status": InvoiceStatusOptions.issued.value,
                            "title": "Changed",
                        },
                        format="json",
                    ),
                    order,
                )
                invoice.refresh_from_db()
                self.assertEqual(invoice.title, "Consultation")

                retrieve = self.client.get(self.invoice_url(invoice))
                self.assertEqual(retrieve.status_code, 200)

                item.title = "Changed item"
                with self.assertRaises(ValidationError) as raised:
                    item.save()
                self.assertIn("active payment link", str(raised.exception))

    def test_charge_item_update_returns_400(self):
        invoice, item = self.make_invoice(InvoiceStatusOptions.draft.value)
        item.status = ChargeItemStatusOptions.billable.value
        item.save(update_fields=["status"])
        order = self.open_link(invoice)

        response = self.client.put(
            self.charge_item_url(item),
            {
                "title": "Changed item",
                "status": ChargeItemStatusOptions.billable.value,
                "quantity": "1",
                "unit_price_components": [
                    {
                        "monetary_component_type": "base",
                        "amount": "100.00",
                        "currency": "INR",
                    }
                ],
            },
            format="json",
        )

        self.assert_blocked(response, order)
        item.refresh_from_db()
        self.assertEqual(item.title, "Service")

    def test_expired_link_says_edit_shortly(self):
        invoice, _ = self.make_invoice()
        order = self.open_link(invoice)
        PaymentOrder.objects.filter(pk=order.pk).update(
            created_date=order.created_date
            - timedelta(seconds=abdm_settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE + 60)
        )

        response = self.client.post(
            self.cancel_url(invoice), {"reason": "cancelled"}, format="json"
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            "You can edit it shortly, once the payment link expires.",
            response.data["errors"][0]["msg"],
        )

    def test_recording_payment_is_blocked_while_link_is_open(self):
        invoice, _item = self.make_invoice()
        order = self.open_link(invoice)

        response = self.client.post(
            reverse(
                "payment_reconciliation-list",
                kwargs={"facility_external_id": self.facility.external_id},
            ),
            {
                "account": str(self.account.external_id),
                "target_invoice": str(invoice.external_id),
                "reconciliation_type": "payment",
                "status": "active",
                "kind": "deposit",
                "issuer_type": "patient",
                "outcome": "complete",
                "payment_datetime": "2026-10-20T10:25",
                "method": "cash",
                "tendered_amount": "100.00",
                "returned_amount": "0.00",
            },
            format="json",
        )

        self.assert_blocked(response, order)
        self.assertFalse(
            PaymentReconciliation.objects.filter(target_invoice=invoice).exists()
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PaymentOrderStatus.PAYMENT_INITIATED)

    def test_closed_link_allows_cancel(self):
        for status in (
            PaymentOrderStatus.SUCCESS,
            PaymentOrderStatus.EXPIRED,
            PaymentOrderStatus.CANCELED,
        ):
            with self.subTest(status=status):
                invoice, item = self.make_invoice()
                self.open_link(invoice, status)

                response = self.client.post(
                    self.cancel_url(invoice), {"reason": "cancelled"}, format="json"
                )

                self.assertEqual(response.status_code, 200, response.data)
                invoice.refresh_from_db()
                item.refresh_from_db()
                self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
                self.assertEqual(item.status, ChargeItemStatusOptions.billable.value)

    def test_settlement_can_still_change_the_invoice(self):
        paid_invoice, paid_item = self.make_invoice()
        self.open_link(paid_invoice)

        self.assertTrue(balance_scan_pay_invoice(paid_invoice))
        paid_invoice.refresh_from_db()
        paid_item.refresh_from_db()
        self.assertEqual(paid_invoice.status, InvoiceStatusOptions.balanced.value)
        self.assertEqual(paid_item.status, ChargeItemStatusOptions.paid.value)

        cancelled_invoice, cancelled_item = self.make_invoice()
        self.open_link(cancelled_invoice)

        self.assertTrue(
            cancel_scan_pay_invoice(cancelled_invoice, "Scan and pay payment failed")
        )
        cancelled_invoice.refresh_from_db()
        cancelled_item.refresh_from_db()
        self.assertEqual(
            cancelled_invoice.status, InvoiceStatusOptions.cancelled.value
        )
        self.assertEqual(cancelled_item.status, ChargeItemStatusOptions.billable.value)
        self.assertIsNone(cancelled_item.paid_invoice_id)
