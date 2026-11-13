from decimal import Decimal
from unittest.mock import patch

from abdm.models.payment_order import PaymentOrder, PaymentOrderStatus
from abdm.service.helper import uuid
from abdm.service.v3 import scan_pay as scan_pay_service
from abdm.service.v3.payment_providers.reconciliation import (
    balance_scan_pay_invoice,
    cancel_scan_pay_invoice,
    create_payment_reconciliation,
)
from abdm.utils import user as abdm_user

from care.emr.models.charge_item import ChargeItem
from care.emr.models.facility_config import FacilityMonetoryConfig
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.account.sync_items import rebalance_account_task
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from tests.test_scan_and_pay import ScanPayTestBase

PAYMENT_LINK = {
    "order_number": "INV-CONTRACT",
    "payment_link_id": "plink_contract",
    "payment_url": "https://pay.example/contract",
    "amount": Decimal(100),
}


class CareBillingContractTests(ScanPayTestBase):
    def setUp(self):
        super().setUp()
        abdm_user.ABDM_USER = None
        FacilityMonetoryConfig.objects.update_or_create(
            facility=self.facility,
            defaults={"invoice_number_expression": "f'INV-{invoice_count}'"},
        )

    def open_order(self):
        return PaymentOrder.objects.create(
            open_order_request_id=uuid(),
            abha_number=self.abha_number,
            health_facility=self.health_facility,
            status=PaymentOrderStatus.OPEN_ORDER_SHARED,
        )

    @patch(
        "abdm.service.v3.scan_pay.create_scan_pay_payment_link",
        return_value=PAYMENT_LINK,
    )
    def test_invoice_create_uses_care_locks_and_syncs_the_total(self, _payment_link):
        charge_item = self.create_charge_item(amount=100)
        order = self.open_order()

        invoice, payment = scan_pay_service.create_scan_pay_invoice_with_payment_link(
            [charge_item],
            self.account,
            self.facility,
            self.patient,
            self.abha_number.health_id,
            order,
        )

        charge_item.refresh_from_db()
        self.assertTrue(invoice.number)
        self.assertEqual(invoice.status, InvoiceStatusOptions.issued.value)
        self.assertEqual(invoice.total_gross, Decimal(100))
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billed.value)
        self.assertEqual(charge_item.paid_invoice_id, invoice.id)
        self.assertEqual(payment["amount"], invoice.total_gross)
        self.assertEqual(order.invoice_id, invoice.id)

    @patch(
        "abdm.service.v3.scan_pay.create_scan_pay_payment_link",
        return_value=PAYMENT_LINK,
    )
    def test_cancel_releases_charge_items_under_the_account_lock(self, _payment_link):
        charge_item = self.create_charge_item()
        invoice, _payment = scan_pay_service.create_scan_pay_invoice_with_payment_link(
            [charge_item],
            self.account,
            self.facility,
            self.patient,
            self.abha_number.health_id,
            self.open_order(),
        )

        self.assertTrue(cancel_scan_pay_invoice(invoice, "payment failed"))

        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.cancelled.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.billable.value)
        self.assertIsNone(charge_item.paid_invoice_id)

    @patch(
        "abdm.service.v3.scan_pay.create_scan_pay_payment_link",
        return_value=PAYMENT_LINK,
    )
    def test_balance_marks_billed_items_paid_under_the_invoice_lock(self, _payment_link):
        charge_item = self.create_charge_item()
        invoice, _payment = scan_pay_service.create_scan_pay_invoice_with_payment_link(
            [charge_item],
            self.account,
            self.facility,
            self.patient,
            self.abha_number.health_id,
            self.open_order(),
        )

        self.assertTrue(balance_scan_pay_invoice(invoice))

        invoice.refresh_from_db()
        charge_item.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatusOptions.balanced.value)
        self.assertEqual(charge_item.status, ChargeItemStatusOptions.paid.value)
        self.assertEqual(charge_item.paid_invoice_id, invoice.id)
        self.assertIsNotNone(charge_item.paid_on)

    @patch(
        "abdm.service.v3.scan_pay.create_scan_pay_payment_link",
        return_value=PAYMENT_LINK,
    )
    def test_rebalance_counts_the_invoice_and_not_open_charge_items(self, _payment_link):
        billed = self.create_charge_item(amount=100)
        order = self.open_order()
        invoice, _payment = scan_pay_service.create_scan_pay_invoice_with_payment_link(
            [billed],
            self.account,
            self.facility,
            self.patient,
            self.abha_number.health_id,
            order,
        )
        open_item = self.create_charge_item(amount=40, title="Still open")
        order.refresh_from_db()

        self.assertTrue(
            create_payment_reconciliation(order, "gw-ref", amount=invoice.total_gross)
        )
        rebalance_account_task.run(self.account.id)

        self.account.refresh_from_db()
        self.assertTrue(
            PaymentReconciliation.objects.filter(
                target_invoice=invoice, reference_number="gw-ref"
            ).exists()
        )
        self.assertEqual(self.account.total_gross, invoice.total_gross)
        self.assertEqual(self.account.total_paid, invoice.total_gross)
        self.assertEqual(self.account.total_billable_charge_items, open_item.total_price)
        self.assertNotEqual(self.account.total_gross, Decimal(140))
        self.assertEqual(
            ChargeItem.objects.get(id=open_item.id).status,
            ChargeItemStatusOptions.billable.value,
        )
