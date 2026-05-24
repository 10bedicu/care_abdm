from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.invoice import (
    Invoice,
    InvoiceLineItem,
    InvoiceLineItemPriceComponent,
    InvoiceParticipant,
)
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.money import Money
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from abdm.utils.fhir.resources.charge_item import _ndhm_billing_others
from care.emr.models.charge_item import ChargeItem as ChargeItemModel
from care.emr.models.invoice import Invoice as InvoiceModel
from care.emr.resources.invoice.spec import InvoiceReadSpec, InvoiceStatusOptions

INVOICE_STATUS_CODE_MAP = {
    InvoiceStatusOptions.draft: "draft",
    InvoiceStatusOptions.issued: "issued",
    InvoiceStatusOptions.balanced: "balanced",
    InvoiceStatusOptions.cancelled: "cancelled",
    InvoiceStatusOptions.entered_in_error: "entered-in-error",
}


class InvoiceMixin:
    def _invoice_line_item(
        self, sequence: int, charge_item: ChargeItemModel
    ) -> InvoiceLineItem:
        components = (
            charge_item.total_price_components
            or charge_item.unit_price_components
            or []
        )
        price_components = []
        for component in components:
            amount = component.get("amount")
            if amount is None:
                continue
            code = component.get("code") or {}
            price_components.append(
                InvoiceLineItemPriceComponent(
                    type=component.get("monetary_component_type") or "informational",
                    code=CodeableConcept(coding=[Coding(**code)]) if code else None,
                    amount=Money(value=float(amount), currency="INR"),
                )
            )

        return InvoiceLineItem(
            sequence=sequence,
            chargeItemReference=self._reference(self._charge_item(charge_item)),
            priceComponent=price_components or None,
        )

    @cache_profiles(Invoice.get_resource_type())
    def _invoice(self, invoice: InvoiceModel):
        invoice_spec = InvoiceReadSpec.serialize(invoice)
        id = str(invoice_spec.id)

        line_items = []
        charge_items = (
            ChargeItemModel.objects.filter(id__in=invoice.charge_items)
            .select_related("patient", "encounter", "performer_actor")
            .order_by("id")
        )
        for sequence, charge_item in enumerate(charge_items, start=1):
            line_items.append(self._invoice_line_item(sequence, charge_item))

        participants = []
        if invoice.created_by:
            participants.append(
                InvoiceParticipant(
                    actor=self._reference(self._practitioner(invoice.created_by))
                )
            )

        invoice_div_parts = [
            f"<p><b>Number:</b> {invoice_spec.number or id}</p>",
            f"<p><b>Status:</b> {invoice_spec.status}</p>",
        ]
        if invoice_spec.title:
            invoice_div_parts.append(f"<p><b>Title:</b> {invoice_spec.title}</p>")

        return Invoice(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Invoice"],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(invoice_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=invoice_spec.number or id)],
            status=INVOICE_STATUS_CODE_MAP.get(invoice_spec.status, "issued"),
            type=_ndhm_billing_others(),
            subject=self._reference(self._patient(invoice.patient)),
            date=(invoice.issue_date or invoice.created_date).isoformat(),
            participant=participants or None,
            issuer=self._reference(self._organization(invoice.facility)),
            lineItem=line_items or None,
            paymentTerms=invoice.payment_terms or None,
            totalNet=Money(value=float(invoice.total_net or 0), currency="INR"),
            totalGross=Money(value=float(invoice.total_gross or 0), currency="INR"),
        )
