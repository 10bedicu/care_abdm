from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from abdm.models.payment_order import latest_open_payment_link
from abdm.settings import plugin_settings as settings
from care.emr.models.charge_item import ChargeItem
from care.emr.models.invoice import Invoice
from care.utils.time_util import care_now

_allow_mutation: ContextVar[bool] = ContextVar(
    "allow_open_payment_link_mutation", default=False
)

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@contextmanager
def allow_open_payment_link_mutation():
    """Let scan-and-pay settlement change an invoice while its link is still open."""
    token = _allow_mutation.set(True)
    try:
        yield
    finally:
        _allow_mutation.reset(token)


def ensure_staff_may_edit(invoice_id: int) -> None:
    """Reject a staff write while a scan-and-pay link for this invoice is open."""
    if _allow_mutation.get():
        return
    message = blocked_edit_message(invoice_id)
    if message:
        raise ValidationError(message)


def format_editable_at(moment) -> str:
    """Local clock time, matching other scan-and-pay timestamps."""
    local = timezone.localtime(moment)
    hour = local.strftime("%I").lstrip("0") or "12"
    return (
        f"{local.day:02d} {_MONTHS[local.month - 1]} {local.year}, "
        f"{hour}:{local.strftime('%M')} {local.strftime('%p')}"
    )


def _editable_clause(created) -> str:
    """When staff can edit again, based on the order's maximum age."""
    if not created:
        return "and cannot be changed."
    deadline = created + timedelta(seconds=settings.ABDM_SCAN_AND_PAY_ORDER_MAX_AGE)
    if deadline <= care_now():
        return (
            "and cannot be changed. You can edit it shortly, once the payment link "
            "expires."
        )
    return f"and cannot be changed until about {format_editable_at(deadline)}."


def blocked_edit_message(invoice_id: int, *, charge_item: bool = False) -> str | None:
    order = latest_open_payment_link(invoice_id)
    if order is None:
        return None
    subject = (
        "Charge item is linked to an invoice with an active payment link"
        if charge_item
        else "Invoice has an active payment link"
    )
    return f"{subject} {_editable_clause(order.created_date)}"


@receiver(pre_save, sender=Invoice)
def block_invoice_changes_while_payment_link_is_open(sender, instance, **kwargs):
    if _allow_mutation.get() or instance._state.adding or not instance.pk:
        return
    message = blocked_edit_message(instance.pk)
    if message:
        raise ValidationError(message)


@receiver(pre_save, sender=ChargeItem)
def block_charge_item_changes_while_payment_link_is_open(sender, instance, **kwargs):
    if _allow_mutation.get() or instance._state.adding or not instance.paid_invoice_id:
        return
    message = blocked_edit_message(instance.paid_invoice_id, charge_item=True)
    if message:
        raise ValidationError(message)
