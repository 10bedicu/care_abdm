from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.specimen import Specimen

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.specimen import Specimen as SpecimenModel
from care.emr.resources.specimen.spec import SpecimenRetrieveSpec, SpecimenStatusOptions

SPECIMEN_STATUS_CODE_MAP = {
    SpecimenStatusOptions.draft: "draft",
    SpecimenStatusOptions.available: "available",
    SpecimenStatusOptions.unavailable: "unavailable",
    SpecimenStatusOptions.unsatisfactory: "unsatisfactory",
    SpecimenStatusOptions.entered_in_error: "entered-in-error",
}


class SpecimenMixin:
    @cache_profiles(Specimen.get_resource_type())
    def _specimen(self, specimen: SpecimenModel):
        specimen_spec = SpecimenRetrieveSpec.serialize(specimen)
        id = str(specimen_spec.id)

        specimen_type_display = (
            specimen_spec.specimen_type.get("display")
            or specimen_spec.specimen_type.get("code", "")
            if specimen_spec.specimen_type
            else ""
        )
        specimen_div_parts = [
            f"<p><b>Specimen:</b> {specimen_type_display or 'Specimen'}</p>",
            f"<p><b>Status:</b> {specimen_spec.status}</p>",
        ]
        if specimen_spec.accession_identifier:
            specimen_div_parts.append(
                f"<p><b>Accession Identifier:</b> {specimen_spec.accession_identifier}</p>"
            )
        if specimen_spec.received_time:
            rt = specimen_spec.received_time
            received_display = rt.isoformat() if hasattr(rt, "isoformat") else str(rt)
            specimen_div_parts.append(
                f"<p><b>Received Time:</b> {received_display}</p>"
            )
        for condition in specimen_spec.condition or []:
            if not isinstance(condition, dict):
                continue
            cond_display = condition.get("display") or condition.get("code", "")
            if cond_display:
                specimen_div_parts.append(
                    f"<p><b>Specimen Condition:</b> {cond_display}</p>"
                )
        if specimen_spec.note:
            specimen_div_parts.append(f"<p><b>Note:</b> {specimen_spec.note}</p>")

        return Specimen(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Specimen"],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(specimen_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            accessionIdentifier=Identifier(value=specimen_spec.accession_identifier),
            status=SPECIMEN_STATUS_CODE_MAP.get(specimen_spec.status, "available"),
            type=self._coding_to_codable_concept(specimen_spec.specimen_type),
            subject=self._reference(self._patient(specimen.patient)),
            request=[self._reference(self._service_request(specimen.service_request))],
            receivedTime=specimen_spec.received_time
            if specimen_spec.received_time
            else None,
            condition=[
                self._coding_to_codable_concept(condition)
                for condition in specimen_spec.condition
            ],
            note=[Annotation(text=specimen_spec.note)] if specimen_spec.note else None,
        )
