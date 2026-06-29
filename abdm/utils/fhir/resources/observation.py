from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.observation import (
    Observation,
    ObservationComponent,
    ObservationReferenceRange,
)
from fhir.resources.R4B.quantity import Quantity

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.observation import Observation as ObservationModel
from care.emr.resources.observation.spec import ObservationReadSpec


def is_float(value):
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def _fhir_observation_reference_range(rrange) -> ObservationReferenceRange:
    if isinstance(rrange, dict):
        min_val = rrange.get("min")
        max_val = rrange.get("max")
    else:
        min_val = rrange.min
        max_val = rrange.max
    return ObservationReferenceRange(
        low=Quantity(value=min_val) if min_val is not None else None,
        high=Quantity(value=max_val) if max_val is not None else None,
    )


class ObservationMixin:
    @cache_profiles(Observation.get_resource_type())
    def _observation(self, observation: ObservationModel):
        id = str(observation.external_id)
        observation_spec = ObservationReadSpec.serialize(observation)

        obs_code_display = (
            observation_spec.main_code.get("display")
            or observation_spec.main_code.get("code")
            if observation_spec.main_code
            else "Observation"
        )
        obs_div_parts = [f"<p><b>Observation:</b> {obs_code_display}</p>"]
        obs_div_parts.append(f"<p><b>Status:</b> {observation_spec.status}</p>")
        obs_div_parts.append(
            f"<p><b>Effective Date:</b> {observation_spec.effective_datetime.date()}</p>"
        )

        obs_value = observation_spec.value
        if isinstance(obs_value, dict):
            raw_value = obs_value.get("value")
            unit_info = obs_value.get("unit", {})
            unit_display = (
                unit_info.get("display") if isinstance(unit_info, dict) else None
            )
            coding_info = obs_value.get("coding")
            if raw_value is not None and unit_display:
                obs_div_parts.append(f"<p><b>Value:</b> {raw_value} {unit_display}</p>")
            elif raw_value is not None:
                obs_div_parts.append(f"<p><b>Value:</b> {raw_value}</p>")
            elif coding_info:
                coding_display = coding_info.get("display") or coding_info.get(
                    "code", ""
                )
                obs_div_parts.append(f"<p><b>Value:</b> {coding_display}</p>")

        obs_interpretation = observation_spec.interpretation
        if obs_interpretation:
            interp_text = (
                obs_interpretation
                if isinstance(obs_interpretation, str)
                else obs_interpretation.get("text") or obs_interpretation.get("code")
            )
            if interp_text:
                obs_div_parts.append(f"<p><b>Interpretation:</b> {interp_text}</p>")
        if observation_spec.note:
            obs_div_parts.append(f"<p><b>Note:</b> {observation_spec.note}</p>")

        return Observation(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Observation"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(obs_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=observation_spec.status,
            category=[
                CodeableConcept(
                    coding=[Coding(**observation_spec.category)]
                    if isinstance(observation_spec.category, dict)
                    else None,
                    text=observation_spec.category.get("display")
                    if isinstance(observation_spec.category, dict)
                    else observation_spec.category
                    if isinstance(observation_spec.category, str)
                    else None,
                )
            ]
            if observation_spec.category
            else None,
            code=CodeableConcept(
                coding=[Coding(**observation_spec.main_code)],
                text=observation_spec.main_code.get("display"),
            )
            if observation_spec.main_code
            else CodeableConcept(**observation_spec.alternate_coding),
            valueString=observation_spec.value.get("value")
            if observation_spec.value.get("value")
            and not (
                observation_spec.value.get("unit")
                and is_float(observation_spec.value.get("value"))
            )
            and not observation_spec.value.get("coding")
            else None,
            valueCodeableConcept=CodeableConcept(
                coding=[Coding(**observation_spec.value.get("coding"))],
                text=observation_spec.value.get("coding", {}).get("display"),
            )
            if observation_spec.value.get("coding")
            else None,
            valueQuantity=Quantity(
                value=observation_spec.value.get("value"),
                unit=observation_spec.value.get("unit", {}).get("display"),
                system=observation_spec.value.get("unit", {}).get("system"),
                code=observation_spec.value.get("unit", {}).get("code"),
            )
            if observation_spec.value.get("unit")
            and is_float(observation_spec.value.get("value"))
            else None,
            effectiveDateTime=observation_spec.effective_datetime.isoformat(),
            method=CodeableConcept(
                coding=[Coding(**observation_spec.method)],
                text=observation_spec.method.get("display"),
            )
            if observation_spec.method
            else None,
            bodySite=CodeableConcept(
                coding=[Coding(**observation_spec.body_site)],
                text=observation_spec.body_site.get("display"),
            )
            if observation_spec.body_site
            else None,
            referenceRange=[
                _fhir_observation_reference_range(rrange)
                for rrange in observation_spec.reference_range
            ],
            encounter=self._reference(self._encounter(observation.encounter))
            if observation.encounter
            else None,
            note=[Annotation(text=observation_spec.note)]
            if observation_spec.note
            else None,
            # interpretation=[CodeableConcept(text=observation_spec.interpretation)]
            # if observation_spec.interpretation
            # and isinstance(observation_spec.interpretation, str)
            # else None,
            component=[
                ObservationComponent(
                    code=CodeableConcept(
                        coding=[Coding(**component.get("code"))],
                        text=component.get("code", {}).get("display"),
                    )
                    if component.get("code")
                    else None,
                    valueString=component.get("value", {}).get("value")
                    if component.get("value", {}).get("value")
                    and not (
                        component.get("value", {}).get("unit")
                        and is_float(component.get("value", {}).get("value"))
                    )
                    and not component.get("value", {}).get("coding")
                    else None,
                    valueCodeableConcept=CodeableConcept(
                        coding=[Coding(**component.get("value", {}).get("coding"))],
                        text=component.get("value", {})
                        .get("coding", {})
                        .get("display"),
                    )
                    if component.get("value", {}).get("coding")
                    else None,
                    valueQuantity=Quantity(
                        value=component.get("value", {}).get("value"),
                        unit=component.get("value", {}).get("unit", {}).get("display"),
                        system=component.get("value", {}).get("unit", {}).get("system"),
                        code=component.get("value", {}).get("unit", {}).get("code"),
                    )
                    if component.get("value", {}).get("unit")
                    and is_float(component.get("value", {}).get("value"))
                    else None,
                    interpretation=[
                        CodeableConcept(
                            coding=[Coding(**component.get("interpretation"))]
                            if isinstance(component.get("interpretation"), dict)
                            else None,
                            text=component.get("interpretation", {}).get("display")
                            if isinstance(component.get("interpretation"), dict)
                            else component.get("interpretation")
                            if isinstance(component.get("interpretation"), str)
                            else None,
                        )
                    ]
                    if component.get("interpretation")
                    else None,
                    referenceRange=[
                        _fhir_observation_reference_range(rrange)
                        for rrange in component.get("reference_range", [])
                    ],
                )
                for component in observation_spec.component
            ],
        )
