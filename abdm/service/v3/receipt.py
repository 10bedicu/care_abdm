import base64
import hashlib
import io
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import segno
from dateutil.relativedelta import relativedelta
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from abdm.service.v3.payment_providers.reconciliation import PaymentOrderLock
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.charge_item import ChargeItem
from care.emr.models.file_upload import FileUpload
from care.emr.models.patient import (
    PatientIdentifierConfig,
    PatientIdentifierConfigCache,
)
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.emr.resources.file_upload.spec import FileCategoryChoices
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.payment_reconciliation.spec import (
    PaymentReconciliationOutcomeOptions,
    PaymentReconciliationStatusOptions,
)

logger = logging.getLogger(__name__)

RECEIPT_FILE_TYPE = "scan_pay_receipt"
RECEIPT_FINGERPRINT_META = "receipt_fingerprint"

_MONEY = Decimal("0.01")
_ADULT_YEARS = 18
_YEAR_AND_MONTH_MIN_YEARS = 2
_MONTH_AND_DAY_MIN_MONTHS = 12
_WEEK_MIN_DAYS = 29
_DAYS_PER_WEEK = 7
_GROUP_TAIL = 3
_ORDINAL_TEEN_START = 11
_ORDINAL_TEEN_END = 13
_URL_RE = re.compile(r"https?://\S+")
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
_FULL_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_GENDERS = {
    "male": "Male",
    "female": "Female",
    "non_binary": "Non-binary",
    "transgender": "Transgender",
}
_PAYMENT_METHODS = {
    "cash": "Cash",
    "ccca": "Credit Card",
    "cchk": "Credit Check",
    "cdac": "Credit Account",
    "chck": "Check",
    "ddpo": "Direct Deposit",
    "debc": "Debit Card",
}
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "templates" / "abdm"

_WATERMARKS = {
    InvoiceStatusOptions.cancelled.value: ("Cancelled", "red"),
    InvoiceStatusOptions.entered_in_error.value: ("Entered in Error", "red"),
    InvoiceStatusOptions.draft.value: ("Draft", "gray"),
}


def build_receipt_context(order) -> dict:
    """Context for the scan-and-pay receipt, matching the invoice print."""
    invoice = order.invoice
    facility = order.health_facility.facility
    patient = order.abha_number.patient
    items = _charge_items(invoice)
    tax_columns = _tax_columns(items)
    payments, credit_notes = _payments(invoice)
    components = _component_list(invoice.total_price_components)
    total_net = Decimal(0) if invoice.locked else invoice.total_net
    total_gross = Decimal(0) if invoice.locked else invoice.total_gross

    return {
        "facility_name": facility.name,
        "facility_address": facility.address,
        "facility_phone": facility.phone_number,
        "invoice_number": (invoice.number or "").strip()
        or (invoice.title or "").strip()
        or str(invoice.external_id)[:8],
        "issue_date": _format_issue_date(invoice.issue_date),
        "patient_name": patient.name,
        "patient_meta": _patient_meta(patient),
        "identifiers": _official_identifiers(patient),
        "address": _patient_address(patient),
        "tax_columns": tax_columns,
        "rows": [
            _charge_row(item, index, tax_columns)
            for index, item in enumerate(items, start=1)
        ],
        "colspan": 6 + len(tax_columns),
        "summary_lines": _summary_lines(components),
        "net_amount": _format_amount(total_net, symbol=True),
        "total": _format_amount(total_gross, symbol=True),
        "payments": payments,
        "total_received": _format_amount(
            sum((payment["raw_amount"] for payment in payments), Decimal(0)),
            symbol=True,
        ),
        "credit_notes": credit_notes,
        "total_credit_notes": _format_amount(
            sum((note["raw_amount"] for note in credit_notes), Decimal(0)),
            symbol=True,
        ),
        "payment_terms": invoice.payment_terms,
        "cancelled_reason": invoice.cancelled_reason,
        "created_by": _format_user_name(invoice.created_by),
        "generated_on": _format_generated_on(timezone.now()),
        "location": payments[0]["location"] if payments else None,
        "watermark": _watermark(invoice.status),
        "logo_url": (_ASSETS_DIR / "care_logo.svg").as_uri(),
        "font_400": (_ASSETS_DIR / "fonts" / "figtree-400.woff").as_uri(),
        "font_500": (_ASSETS_DIR / "fonts" / "figtree-500.woff").as_uri(),
        "font_600": (_ASSETS_DIR / "fonts" / "figtree-600.woff").as_uri(),
        "font_700": (_ASSETS_DIR / "fonts" / "figtree-700.woff").as_uri(),
        "qr_url": _qr_data_uri(patient.external_id),
    }


def _charge_items(invoice) -> list[dict]:
    if invoice.status == InvoiceStatusOptions.draft.value:
        live = {
            item.id: item
            for item in ChargeItem.objects.filter(id__in=invoice.charge_items)
        }
        return [
            _normalize_item(live[item_id])
            for item_id in invoice.charge_items
            if item_id in live
        ]
    return [
        _normalize_item(item) for item in _component_list(invoice.charge_items_copy)
    ]


def _normalize_item(item) -> dict:
    if isinstance(item, dict):
        return {
            "title": item.get("title") or "",
            "quantity": item.get("quantity"),
            "unit_price_components": _component_list(item.get("unit_price_components")),
            "total_price_components": _component_list(
                item.get("total_price_components")
            ),
            "total_price": item.get("total_price"),
        }
    return {
        "title": item.title or "",
        "quantity": item.quantity,
        "unit_price_components": _component_list(item.unit_price_components),
        "total_price_components": _component_list(item.total_price_components),
        "total_price": item.total_price,
    }


def _component_list(value) -> list:
    return value if isinstance(value, list) else []


def _of_type(components, component_type) -> list:
    return [
        component
        for component in components
        if component.get("monetary_component_type") == component_type
    ]


def _code(component) -> str | None:
    code = component.get("code")
    if isinstance(code, dict):
        return code.get("code") or None
    return None


def _display(component) -> str | None:
    code = component.get("code")
    if isinstance(code, dict):
        return code.get("display") or None
    return None


def _tax_columns(items) -> list[str]:
    columns = []
    for item in items:
        for component in _of_type(item["unit_price_components"], "tax"):
            code = _code(component)
            if code and code not in columns:
                columns.append(code)
    return columns


def _charge_row(item, index, tax_columns) -> dict:
    base = next(iter(_of_type(item["unit_price_components"], "base")), None)
    discount_total = sum(
        (
            _decimal(component.get("amount")) or Decimal(0)
            for component in _of_type(item["total_price_components"], "discount")
        ),
        Decimal(0),
    )
    return {
        "index": index,
        "title": item["title"],
        "unit_price": _format_amount(base.get("amount") if base else None),
        "quantity": _format_quantity(item["quantity"]),
        "discount": _format_amount(discount_total),
        "discount_details": [
            _format_component(component, symbol=False)
            for component in _of_type(item["unit_price_components"], "discount")
        ],
        "taxes": [_tax_cell(item, code) for code in tax_columns],
        "total": _format_amount(item["total_price"]),
    }


def _tax_cell(item, code) -> dict:
    total = next(
        (
            component
            for component in item["total_price_components"]
            if _code(component) == code
        ),
        None,
    )
    unit = next(
        (
            component
            for component in item["unit_price_components"]
            if _code(component) == code
        ),
        None,
    )
    amount = total.get("amount") if total else None
    return {
        "amount": _format_amount(amount) if total else "-",
        "detail": _format_component(unit, symbol=False) if total and unit else "",
    }


def _summary_lines(components) -> list[dict]:
    lines = [
        {
            "label": f"{_display(component) or 'Base Amount'}:",
            "value": _format_component(component, symbol=True),
            "muted": False,
        }
        for component in _of_type(components, "base")
    ]
    for component_type, kind, sign in (
        ("surcharge", "Surcharge", "+"),
        ("discount", "Discount", "-"),
        ("tax", "Tax", "+"),
    ):
        for component in _of_type(components, component_type):
            display = _display(component)
            label = f"{display} ({kind})" if display else f"({kind})"
            lines.append(
                {
                    "label": label,
                    "value": f"{sign} {_format_component(component, symbol=True)}",
                    "muted": True,
                }
            )
    return lines


def _payments(invoice) -> tuple[list[dict], list[dict]]:
    payments = []
    credit_notes = []
    queryset = PaymentReconciliation.objects.filter(
        target_invoice=invoice,
        outcome=PaymentReconciliationOutcomeOptions.complete.value,
        status=PaymentReconciliationStatusOptions.active.value,
    ).select_related("location")
    for payment in queryset.order_by("payment_datetime", "id"):
        row = {
            "id": str(payment.external_id),
            "paid_at": _format_payment_date(payment.payment_datetime),
            "method": _PAYMENT_METHODS.get(payment.method, payment.method),
            "reference": payment.reference_number or "",
            "amount": _format_amount(payment.amount),
            "raw_amount": payment.amount or Decimal(0),
            "location": payment.location.name if payment.location_id else None,
        }
        if payment.is_credit_note:
            credit_notes.append(row)
        else:
            payments.append(row)
    return payments, credit_notes


def _official_identifiers(patient) -> list[dict]:
    identifiers = []
    for entry in patient.instance_identifiers or []:
        config_id = entry.get("config")
        if not config_id:
            continue
        try:
            spec = PatientIdentifierConfigCache.get_config(config_id)
        except PatientIdentifierConfig.DoesNotExist:
            continue
        except Exception:
            logger.warning(
                "Skipping unreadable patient identifier %s on receipt",
                config_id,
                exc_info=True,
            )
            continue
        config = spec.get("config") or {}
        if config.get("use") == "official" and not config.get("auto_maintained"):
            identifiers.append(
                {"label": config.get("display") or "", "value": entry.get("value")}
            )
    return identifiers


def _patient_meta(patient) -> str:
    gender = _GENDERS.get(patient.gender, "")
    age = _format_patient_age(patient)
    return ", ".join(part for part in (gender, age) if part)


def _format_patient_age(patient) -> str:
    end = (
        patient.deceased_datetime.date()
        if patient.deceased_datetime
        else timezone.localdate()
    )
    if not patient.date_of_birth:
        if not patient.year_of_birth:
            return ""
        years = relativedelta(end, date(patient.year_of_birth, 1, 1)).years
        return f"{patient.year_of_birth} ({years}y)"

    delta = relativedelta(end, patient.date_of_birth)
    years, months, days = delta.years, delta.months, delta.days
    total_months = years * _MONTH_AND_DAY_MIN_MONTHS + months
    total_days = (end - patient.date_of_birth).days
    if years >= _ADULT_YEARS:
        label = f"{years}y"
    elif years >= _YEAR_AND_MONTH_MIN_YEARS:
        label = f"{years}y" if months == 0 else f"{years}y {months}m"
    elif total_months >= _MONTH_AND_DAY_MIN_MONTHS:
        label = f"{total_months}m" if days == 0 else f"{total_months}m {days}d"
    elif total_days >= _WEEK_MIN_DAYS:
        weeks, remaining = divmod(total_days, _DAYS_PER_WEEK)
        label = f"{weeks}w" if remaining == 0 else f"{weeks}w {remaining}d"
    else:
        label = f"{total_days}d"
    return label


def _patient_address(patient) -> str:
    return _URL_RE.sub("", patient.address or "").strip()


def _format_user_name(user) -> str:
    if not user:
        return "-"
    parts = [user.prefix, user.first_name, user.last_name, user.suffix]
    name = " ".join(part.strip() for part in parts if part and part.strip())
    return name or user.username or "-"


def _watermark(status) -> dict | None:
    watermark = _WATERMARKS.get(status)
    if not watermark:
        return None
    text, color = watermark
    return {"text": text, "color": color}


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _format_quantity(value) -> str:
    amount = _decimal(value)
    if amount is None:
        return "-"
    return f"{amount.quantize(_MONEY):.2f}"


def _format_amount(value, *, symbol=False) -> str:
    amount = _decimal(value)
    if amount is None:
        return "-"
    quantized = amount.quantize(_MONEY)
    sign = "-" if quantized < 0 else ""
    whole, fraction = f"{abs(quantized):.2f}".split(".")
    if len(whole) > _GROUP_TAIL:
        head, tail = whole[:-_GROUP_TAIL], whole[-_GROUP_TAIL:]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        whole = ",".join(reversed(groups)) + "," + tail
    formatted = f"{sign}{whole}.{fraction}"
    return f"₹{formatted}" if symbol else formatted


def _format_component(component, *, symbol) -> str:
    if component.get("amount") is not None:
        return _format_amount(component.get("amount"), symbol=symbol)
    if component.get("factor") is not None:
        return f"{_format_amount(component.get('factor'), symbol=False)}%"
    return "-"


def _local(value: datetime | None):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value)


def _clock(value: datetime, *, padded_hour: bool) -> str:
    hour = value.strftime("%I")
    if not padded_hour:
        hour = hour.lstrip("0") or "12"
    return f"{hour}:{value.strftime('%M')} {value.strftime('%p')}"


def _format_issue_date(value) -> str:
    local = _local(value)
    if not local:
        return "-"
    return (
        f"{local.day:02d} {_MONTHS[local.month - 1]}, {local.year} "
        f"{_clock(local, padded_hour=False)}"
    )


def _format_payment_date(value) -> str:
    local = _local(value)
    if not local:
        return "-"
    return (
        f"{local.day} {_MONTHS[local.month - 1]} {local.year}, "
        f"{_clock(local, padded_hour=True)}"
    )


def _format_generated_on(value) -> str:
    local = _local(value)
    return (
        f"{_FULL_MONTHS[local.month - 1]} {_ordinal(local.day)}, {local.year} at "
        f"{_clock(local, padded_hour=False)}"
    )


def _qr_data_uri(value) -> str:
    qr = segno.make(str(value), error="m")
    out = io.BytesIO()
    qr.save(out, kind="svg", xmldecl=False, svgns=True, nl=False, scale=4, border=0)
    encoded = base64.b64encode(out.getvalue()).decode()
    return f"data:image/svg+xml;base64,{encoded}"


def receipt_fingerprint(order) -> str:
    """Hash the receipt details that should replace a stored PDF.

    The printed age, the generated-on clock, and the logo and fonts are left
    out, so opening the same bill later does not rebuild it.
    """
    invoice = order.invoice
    facility = order.health_facility.facility
    patient = order.abha_number.patient
    payments = PaymentReconciliation.objects.filter(
        target_invoice=invoice,
        outcome=PaymentReconciliationOutcomeOptions.complete.value,
        status=PaymentReconciliationStatusOptions.active.value,
    ).select_related("location")
    payload = {
        "status": invoice.status,
        "cancelled_reason": invoice.cancelled_reason or "",
        "payment_terms": invoice.payment_terms or "",
        "locked": invoice.locked,
        "total_net": str(invoice.total_net),
        "total_gross": str(invoice.total_gross),
        "charge_items_copy": invoice.charge_items_copy,
        "total_price_components": invoice.total_price_components,
        "facility_name": facility.name,
        "facility_address": facility.address or "",
        "facility_phone": facility.phone_number or "",
        "invoice_number": (invoice.number or "").strip()
        or (invoice.title or "").strip()
        or str(invoice.external_id)[:8],
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else "",
        "patient_name": patient.name,
        "patient_gender": patient.gender or "",
        "patient_date_of_birth": (
            patient.date_of_birth.isoformat() if patient.date_of_birth else ""
        ),
        "patient_year_of_birth": patient.year_of_birth or "",
        "patient_address": _patient_address(patient),
        "identifiers": _official_identifiers(patient),
        "created_by": _format_user_name(invoice.created_by),
        "payments": [
            {
                "id": str(payment.external_id),
                "amount": str(payment.amount),
                "method": payment.method,
                "reference": payment.reference_number or "",
                "is_credit_note": payment.is_credit_note,
                "paid_at": (
                    payment.payment_datetime.isoformat()
                    if payment.payment_datetime
                    else ""
                ),
                "location": payment.location.name if payment.location_id else "",
            }
            for payment in payments.order_by("payment_datetime", "id")
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def receipt_file(order):
    """Stored receipt, rendered again when the invoice changed."""
    with PaymentOrderLock(order):
        fingerprint = receipt_fingerprint(order)
        file = _current_receipt_file(order)
        if (
            file is None
            or (file.meta or {}).get(RECEIPT_FINGERPRINT_META) != fingerprint
        ):
            file, _pdf = _store_receipt(order, file, fingerprint)
        return file


def receipt_signed_url(order) -> str:
    """A short-lived download URL for the stored receipt."""
    file = receipt_file(order)
    return file.files_manager.read_signed_url(file)


def _current_receipt_file(order):
    return (
        FileUpload.objects.filter(
            file_type=RECEIPT_FILE_TYPE,
            associating_id=str(order.external_id),
            upload_completed=True,
        )
        .order_by("-created_date")
        .first()
    )


def _receipt_filename(order) -> str:
    number = (order.order_number or str(order.external_id)).strip()
    return f"receipt-{number}.pdf"


def _render_receipt_pdf(order) -> bytes:
    html = render_to_string(
        "abdm/scan_and_pay_receipt.html",
        build_receipt_context(order),
    )
    return HTML(string=html).write_pdf()


def _store_receipt(order, existing, fingerprint: str):
    pdf = _render_receipt_pdf(order)
    created = existing is None
    file = existing
    if created:
        file = FileUpload(
            name=_receipt_filename(order),
            file_type=RECEIPT_FILE_TYPE,
            file_category=FileCategoryChoices.unspecified.value,
            associating_id=str(order.external_id),
            created_by=get_or_create_abdm_user(),
            upload_completed=False,
            meta={"mime_type": "application/pdf"},
        )
        file.save()
    try:
        file.files_manager.put_object(file, pdf, ContentType="application/pdf")
    except Exception:
        if created:
            file.delete()
        raise
    meta = dict(file.meta or {})
    meta["mime_type"] = "application/pdf"
    meta[RECEIPT_FINGERPRINT_META] = fingerprint
    file.meta = meta
    file.upload_completed = True
    file.save(update_fields=["meta", "upload_completed", "modified_date"])
    return file, pdf


def _ordinal(day: int) -> str:
    if _ORDINAL_TEEN_START <= day % 100 <= _ORDINAL_TEEN_END:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
