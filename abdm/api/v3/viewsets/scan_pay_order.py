import logging

import requests
from django.core.exceptions import ImproperlyConfigured
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from pydantic import BaseModel, Field
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.payment_order import PaymentOrderSerializer
from abdm.models.payment_order import (
    PAYMENT_ORDER_PAID_STATUSES,
    PAYMENT_ORDER_PENDING_STATUSES,
    PaymentOrder,
    PaymentOrderStatus,
)
from abdm.service.v3.payment_providers import (
    close_payment_order,
    create_payment_reconciliation,
    get_provider,
)
from abdm.settings import plugin_settings as settings
from care.emr.api.viewsets.base import emr_exception_handler
from care.facility.models.facility import Facility
from care.security.authorization.base import AuthorizationController
from care.utils.lock import ObjectLocked
from care.utils.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)

READ_PERMISSION = "can_read_payment_reconciliation_in_facility"
WRITE_PERMISSION = "can_write_payment_reconciliation_in_facility"


class CharInFilter(filters.BaseInFilter, filters.CharFilter):
    pass


class UUIDInFilter(filters.BaseInFilter, filters.UUIDFilter):
    pass


class PaymentOrderFilters(filters.FilterSet):
    status = CharInFilter(field_name="status", lookup_expr="in")
    pending = filters.BooleanFilter(method="filter_pending")
    invoice = filters.UUIDFilter(field_name="invoice__external_id")
    invoice_in = UUIDInFilter(field_name="invoice__external_id", lookup_expr="in")
    order_number = filters.CharFilter(lookup_expr="iexact")
    invoice_number = filters.CharFilter(
        field_name="invoice__number", lookup_expr="icontains"
    )
    patient = filters.UUIDFilter(field_name="abha_number__patient__external_id")
    created_date = filters.DateTimeFromToRangeFilter(field_name="created_date")

    def filter_pending(self, queryset, name, value):
        if value:
            return queryset.filter(status__in=PAYMENT_ORDER_PENDING_STATUSES)
        return queryset.exclude(status__in=PAYMENT_ORDER_PENDING_STATUSES)


class MarkPaidRequest(BaseModel):
    reference_number: str = Field(min_length=1, max_length=100)
    note: str = ""


@extend_schema(tags=["ABDM: Scan and Pay Orders"])
class ScanPayOrderViewSet(GenericViewSet, ListModelMixin, RetrieveModelMixin):
    """Staff view of scan-and-pay orders for a facility, with manual controls."""

    serializer_class = PaymentOrderSerializer
    permission_classes = (IsAuthenticated,)
    lookup_field = "external_id"
    filter_backends = (filters.DjangoFilterBackend, OrderingFilter)
    filterset_class = PaymentOrderFilters
    ordering_fields = ["created_date", "modified_date", "payment_date"]
    ordering = ["-created_date"]

    def get_exception_handler(self):
        return emr_exception_handler

    def _authorize(self, facility, permission) -> None:
        if not AuthorizationController.call(permission, self.request.user, facility):
            raise PermissionDenied("Cannot access scan and pay orders")

    def get_queryset(self):
        queryset = PaymentOrder.objects.select_related(
            "invoice__account",
            "abha_number__patient",
            "health_facility__facility",
        )
        if self.action in ("list", "retrieve"):
            facility_id = self.request.query_params.get("facility")
            if not facility_id:
                raise ValidationError("facility is required")
            facility = get_object_or_404(Facility, external_id=facility_id)
            self._authorize(facility, READ_PERMISSION)
            return queryset.filter(health_facility__facility=facility)
        return queryset

    def get_object(self):
        order = super().get_object()
        if self.action not in ("list", "retrieve"):
            self._authorize(order.health_facility.facility, WRITE_PERMISSION)
        return order

    def _respond(self, order: PaymentOrder, **extra):
        order.refresh_from_db()
        return Response({"order": self.get_serializer(order).data, **extra})

    @extend_schema(
        description=(
            "Ask the payment provider for the order's status right now and "
            "settle or close it accordingly. `gateway_checked` tells whether the "
            "provider actually answered."
        ),
        request=None,
        responses={200: PaymentOrderSerializer},
    )
    @action(detail=True, methods=["POST"], url_path="check-status")
    def check_status(self, request, external_id=None):
        order = self.get_object()
        if order.status not in PAYMENT_ORDER_PENDING_STATUSES:
            return self._respond(
                order, gateway_checked=False, detail="Order is no longer pending"
            )
        if not order.invoice_id or not order.order_number:
            return self._respond(
                order,
                gateway_checked=False,
                detail="Order has no payment link to check",
            )
        try:
            provider = get_provider(
                order.provider or settings.ABDM_SCAN_AND_PAY_PROVIDER
            )
        except ImproperlyConfigured as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        try:
            checked = provider.reconcile_order(order)
        except ObjectLocked:
            raise
        except (requests.Timeout, requests.ConnectionError):
            return Response(
                {"detail": "Payment gateway is unreachable, try again later"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.exception(
                "Manual status check failed for scan and pay order %s",
                order.order_number,
            )
            return Response(
                {"detail": f"Payment gateway error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return self._respond(order, gateway_checked=bool(checked))

    @extend_schema(
        description=(
            "Cancel a pending order: voids its invoice so the services can be "
            "billed again and tells the PHR the payment was cancelled."
        ),
        request=None,
        responses={200: PaymentOrderSerializer},
    )
    @action(detail=True, methods=["POST"])
    def cancel(self, request, external_id=None):
        order = self.get_object()
        if not close_payment_order(order, PaymentOrderStatus.CANCELED):
            return Response(
                {"detail": "Only pending orders can be cancelled"},
                status=status.HTTP_409_CONFLICT,
            )
        return self._respond(order)

    @extend_schema(
        description=(
            "Record the payment as received without waiting for the gateway, "
            "e.g. after confirming it in the provider dashboard."
        ),
        request=MarkPaidRequest,
        responses={200: PaymentOrderSerializer},
    )
    @action(detail=True, methods=["POST"], url_path="mark-paid")
    def mark_paid(self, request, external_id=None):
        order = self.get_object()
        data = MarkPaidRequest.model_validate(request.data)
        if order.status in PAYMENT_ORDER_PAID_STATUSES:
            return Response(
                {"detail": "Order is already paid"}, status=status.HTTP_409_CONFLICT
            )
        if not order.invoice_id:
            return Response(
                {"detail": "Order has no invoice to record the payment against"},
                status=status.HTTP_409_CONFLICT,
            )
        note = f"Confirmed manually by {request.user.username}."
        if data.note.strip():
            note = f"{note} {data.note.strip()}"
        if not create_payment_reconciliation(
            order,
            data.reference_number.strip(),
            created_by=request.user,
            note=note,
        ):
            return Response(
                {"detail": "A payment with this reference is already recorded"},
                status=status.HTTP_409_CONFLICT,
            )
        return self._respond(order)
