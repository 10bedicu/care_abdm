from datetime import UTC, datetime

from fhir.resources.R4B.chargeitem import ChargeItem
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.quantity import Quantity

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.charge_item import ChargeItem as ChargeItemModel
from care.emr.resources.charge_item.spec import (
    ChargeItemReadSpec,
    ChargeItemStatusOptions,
)

CHARGE_ITEM_STATUS_CODE_MAP = {
    ChargeItemStatusOptions.billable: "billable",
    ChargeItemStatusOptions.not_billable: "not-billable",
    ChargeItemStatusOptions.aborted: "aborted",
    ChargeItemStatusOptions.billed: "billed",
    ChargeItemStatusOptions.paid: "billed",
    ChargeItemStatusOptions.entered_in_error: "entered-in-error",
}

NDHM_BILLING_CODES_SYSTEM = (
    "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-billing-codes"
)


def _ndhm_billing_others() -> CodeableConcept:
    return CodeableConcept(
        coding=[Coding(system=NDHM_BILLING_CODES_SYSTEM, code="99", display="Others")],
        text="Others",
    )


class ChargeItemMixin:
    @cache_profiles(ChargeItem.get_resource_type())
    def _charge_item(self, charge_item: ChargeItemModel):
        charge_item_spec = ChargeItemReadSpec.serialize(charge_item)
        id = str(charge_item_spec.id)

        charge_item_div_parts = [f"<p><b>Title:</b> {charge_item_spec.title}</p>"]
        if charge_item_spec.code:
            code_display = charge_item_spec.code.get(
                "display"
            ) or charge_item_spec.code.get("code", "")
            if code_display:
                charge_item_div_parts.append(f"<p><b>Code:</b> {code_display}</p>")
        charge_item_div_parts.append(f"<p><b>Status:</b> {charge_item_spec.status}</p>")
        if charge_item_spec.quantity is not None:
            charge_item_div_parts.append(
                f"<p><b>Quantity:</b> {charge_item_spec.quantity}</p>"
            )

        return ChargeItem(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ChargeItem"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(charge_item_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=CHARGE_ITEM_STATUS_CODE_MAP.get(charge_item_spec.status, "billable"),
            code=self._coding_to_codable_concept(charge_item_spec.code)
            if charge_item_spec.code
            else _ndhm_billing_others(),
            subject=self._reference(self._patient(charge_item.patient)),
            performer=[
                {
                    "actor": self._reference(
                        self._practitioner(charge_item.performer_actor)
                    )
                }
            ]
            if charge_item.performer_actor
            else None,
            quantity=Quantity(value=float(charge_item_spec.quantity))
            if charge_item_spec.quantity is not None
            else None,
            productCodeableConcept=self._coding_to_codable_concept(
                charge_item_spec.code
            )
            if charge_item_spec.code
            else CodeableConcept(text=charge_item_spec.title),
        )
