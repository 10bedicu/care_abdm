from rest_framework import serializers

from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PAYMENT_ORDER_PENDING_STATUSES,
    PaymentOrder,
)
from abdm.service.v3.scan_pay import scan_pay_receipt_link


class PaymentOrderSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="external_id", read_only=True)
    abha_address = serializers.CharField(
        source="requesting_abha_address", read_only=True
    )
    invoice = serializers.SerializerMethodField()
    patient = serializers.SerializerMethodField()
    receipt_url = serializers.SerializerMethodField()
    is_pending = serializers.SerializerMethodField()

    class Meta:
        model = PaymentOrder
        fields = (
            "id",
            "status",
            "is_pending",
            "provider",
            "order_number",
            "amount",
            "abha_address",
            "transaction_id",
            "payment_date",
            "invoice",
            "patient",
            "receipt_url",
            "created_date",
            "modified_date",
        )
        read_only_fields = fields

    def get_invoice(self, obj):
        invoice = obj.invoice
        if not invoice:
            return None
        return {
            "id": str(invoice.external_id),
            "number": invoice.number,
            "title": invoice.title,
            "status": invoice.status,
            "total_gross": str(invoice.total_gross),
        }

    def get_patient(self, obj):
        patient = obj.abha_number.patient
        if not patient:
            return None
        return {"id": str(patient.external_id), "name": patient.name}

    def get_receipt_url(self, obj):
        if obj.status in PAYMENT_ORDER_PAID_STATUSES and obj.invoice_id:
            return scan_pay_receipt_link(obj)
        return None

    def get_is_pending(self, obj):
        return obj.status in PAYMENT_ORDER_PENDING_STATUSES
