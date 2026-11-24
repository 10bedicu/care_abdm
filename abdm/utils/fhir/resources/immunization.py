from datetime import UTC, datetime
from html import escape

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.immunization import (
    Immunization,
    ImmunizationPerformer,
    ImmunizationProtocolApplied,
)
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.immunisation import Immunization as ImmunizationModel
from care.emr.models.immunisation import (
    ImmunizationRecommendation as ImmunizationRecommendationModel,
)
from care.emr.resources.immunisation.immunization import (
    ImmunizationReason,
    ImmunizationStatus,
)

IMMUNIZATION_STATUS_CODE_MAP = {
    ImmunizationStatus.completed: "completed",
    ImmunizationStatus.not_done: "not-done",
    ImmunizationStatus.entered_in_error: "entered-in-error",
    # FHIR has no draft status, and a draft dose has not been given
    ImmunizationStatus.draft: "not-done",
}

IMMUNIZATION_STATUS_REASON_CODE_MAP = {
    ImmunizationReason.IMMUNE: ("IMMUNE", "immunity"),
    ImmunizationReason.MEDPREC: ("MEDPREC", "medical precaution"),
    ImmunizationReason.OSTOCK: ("OSTOCK", "product out of stock"),
    ImmunizationReason.PATOBJ: ("PATOBJ", "patient objection"),
    ImmunizationReason.PHILISOP: ("PHILISOP", "philosophical objection"),
    ImmunizationReason.RELIG: ("RELIG", "religious objection"),
    ImmunizationReason.VACEFF: ("VACEFF", "vaccine efficacy concerns"),
    ImmunizationReason.VACSAF: ("VACSAF", "vaccine safety concerns"),
}

BRAND_NAME_EXTENSION_URL = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/BrandName"


def positive_int_or_string(field: str, value: str | None) -> dict:
    """Map a free-text dose/series count to FHIR's PositiveInt or String choice."""
    value = (value or "").strip()
    if not value:
        return {}
    if value.isdecimal() and int(value) > 0:
        return {f"{field}PositiveInt": int(value)}
    return {f"{field}String": value}


def _coding_text(coding: dict | None) -> str | None:
    if not coding:
        return None
    return coding.get("display") or coding.get("code")


class ImmunizationMixin:
    @cache_profiles(Immunization.get_resource_type())
    def _immunization(self, immunization: ImmunizationModel):
        id = str(immunization.external_id)
        occurrence = immunization.occurrence or immunization.created_date
        product = immunization.product
        brand_name = (
            getattr(product.product_knowledge, "name", None) if product else None
        )
        lot_number = (product.batch or {}).get("lot_number") if product else None
        expiration_date = (
            product.expiration_date.date().isoformat()
            if product and product.expiration_date
            else None
        )
        status_reason = IMMUNIZATION_STATUS_REASON_CODE_MAP.get(immunization.reason)
        dose = immunization.dose_quantity or {}
        dose_unit = dose.get("unit") or {}
        dose_text = (
            f"{dose['value']} {_coding_text(dose_unit) or ''}".strip()
            if dose.get("value") is not None
            else None
        )

        div_parts = [
            f"<p><b>Vaccine:</b> {escape(_coding_text(immunization.code) or 'Immunization')}</p>",
            f"<p><b>Status:</b> {escape(immunization.status)}</p>",
            f"<p><b>Occurrence:</b> {occurrence.isoformat()}</p>",
        ]
        if status_reason:
            div_parts.append(f"<p><b>Status Reason:</b> {status_reason[1]}</p>")
        if brand_name:
            div_parts.append(f"<p><b>Brand:</b> {escape(brand_name)}</p>")
        if lot_number:
            div_parts.append(f"<p><b>Lot Number:</b> {escape(lot_number)}</p>")
        if dose_text:
            div_parts.append(f"<p><b>Dose:</b> {escape(dose_text)}</p>")
        if _coding_text(immunization.route):
            div_parts.append(
                f"<p><b>Route:</b> {escape(_coding_text(immunization.route))}</p>"
            )
        if _coding_text(immunization.site):
            div_parts.append(
                f"<p><b>Site:</b> {escape(_coding_text(immunization.site))}</p>"
            )
        if immunization.note:
            div_parts.append(f"<p><b>Note:</b> {escape(immunization.note)}</p>")

        return Immunization(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Immunization"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(div_parts)
                + "</div>",
            ),
            extension=[Extension(url=BRAND_NAME_EXTENSION_URL, valueString=brand_name)]
            if brand_name
            else None,
            identifier=[Identifier(value=id)],
            status=IMMUNIZATION_STATUS_CODE_MAP.get(immunization.status, "not-done"),
            statusReason=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/v3-ActReason",
                        code=status_reason[0],
                        display=status_reason[1],
                    )
                ],
                text=status_reason[1],
            )
            if status_reason
            else None,
            vaccineCode=self._coding_to_codable_concept(immunization.code),
            patient=self._reference(self._patient(immunization.patient)),
            encounter=self._reference(self._encounter(immunization.encounter)),
            occurrenceDateTime=occurrence.isoformat(),
            recorded=immunization.created_date.isoformat(),
            primarySource=immunization.primary_source,
            location=Reference(display=immunization.location.name)
            if immunization.location
            else None,
            lotNumber=lot_number or None,
            expirationDate=expiration_date,
            site=self._coding_to_codable_concept(immunization.site or None),
            route=self._coding_to_codable_concept(immunization.route or None),
            doseQuantity=Quantity(
                value=dose["value"],
                unit=_coding_text(dose_unit),
                system=dose_unit.get("system"),
                code=dose_unit.get("code") if dose_unit.get("system") else None,
            )
            if dose.get("value") is not None
            else None,
            performer=[
                ImmunizationPerformer(
                    function=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0443",
                                code="AP",
                                display="Administering Provider",
                            )
                        ],
                        text="Administering Provider",
                    ),
                    actor=self._reference(
                        self._practitioner(immunization.administered_by)
                    ),
                )
            ]
            if immunization.administered_by
            else None,
            note=[Annotation(text=immunization.note)] if immunization.note else None,
            isSubpotent=immunization.is_subpotent,
            subpotentReason=[CodeableConcept(text=immunization.subpotent_reason)]
            if immunization.subpotent_reason
            else None,
            protocolApplied=self._immunization_protocol_applied(
                immunization.recommendation
            ),
        )

    def _immunization_protocol_applied(
        self, recommendation: ImmunizationRecommendationModel | None
    ):
        dose_number = positive_int_or_string(
            "doseNumber", recommendation.dose_number if recommendation else None
        )
        # FHIR requires a dose number on every protocolApplied entry
        if not dose_number:
            return None

        return [
            ImmunizationProtocolApplied(
                series=recommendation.series or None,
                targetDisease=[
                    self._coding_to_codable_concept(disease)
                    for disease in recommendation.diseases or []
                ]
                or None,
                **dose_number,
                **positive_int_or_string("seriesDoses", recommendation.series_number),
            )
        ]
