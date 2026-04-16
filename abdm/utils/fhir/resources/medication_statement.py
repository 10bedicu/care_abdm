from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.dosage import Dosage
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.period import Period

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.medication_statement import (
    MedicationStatement as MedicationStatementModel,
)
from care.emr.resources.medication.statement.spec import (
    MedicationStatementReadSpec,
    MedicationStatementStatus,
)

MEDICATION_STATEMENT_STATUS_CODE_MAP = {
    MedicationStatementStatus.active: "active",
    MedicationStatementStatus.completed: "completed",
    MedicationStatementStatus.entered_in_error: "entered-in-error",
    MedicationStatementStatus.intended: "intended",
    MedicationStatementStatus.stopped: "stopped",
    MedicationStatementStatus.on_hold: "on-hold",
    MedicationStatementStatus.unknown: "unknown",
    MedicationStatementStatus.not_taken: "not-taken",
}


class MedicationStatementMixin:
    @cache_profiles(MedicationStatement.get_resource_type())
    def _medication_statement(self, statement: MedicationStatementModel):
        statement_spec = MedicationStatementReadSpec.serialize(statement)
        id = str(statement_spec.id)

        med = statement_spec.medication
        if isinstance(med, dict):
            med_stmt_name = (
                med.get("display") or med.get("code") or "Medication Statement"
            )
        else:
            med_stmt_name = (
                getattr(med, "display", None)
                or getattr(med, "code", None)
                or "Medication Statement"
            )

        ep = statement_spec.effective_period
        ep_start = (
            ep.get("start") if isinstance(ep, dict) else getattr(ep, "start", None)
        )
        ep_end = ep.get("end") if isinstance(ep, dict) else getattr(ep, "end", None)

        med_stmt_div_parts = [f"<p><b>Medication:</b> {med_stmt_name}</p>"]
        med_stmt_div_parts.append(f"<p><b>Status:</b> {statement_spec.status}</p>")
        if statement_spec.dosage_text:
            med_stmt_div_parts.append(
                f"<p><b>Dosage:</b> {statement_spec.dosage_text}</p>"
            )
        if ep_start:
            med_stmt_div_parts.append(f"<p><b>Effective From:</b> {ep_start}</p>")
        if ep_end:
            med_stmt_div_parts.append(f"<p><b>Effective To:</b> {ep_end}</p>")
        if statement_spec.note:
            med_stmt_div_parts.append(f"<p><b>Note:</b> {statement_spec.note}</p>")

        return MedicationStatement(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationStatement"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(med_stmt_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=MEDICATION_STATEMENT_STATUS_CODE_MAP.get(
                statement_spec.status, "unknown"
            ),
            medicationCodeableConcept=self._coding_to_codable_concept(
                statement_spec.medication
            ),
            dosage=[Dosage(text=statement_spec.dosage_text)]
            if statement_spec.dosage_text
            else None,
            effectivePeriod=Period(**statement_spec.effective_period)
            if statement_spec.effective_period
            else None,
            subject=self._reference(self._patient(statement.patient)),
            note=[Annotation(text=statement_spec.note)]
            if statement_spec.note
            else None,
        )
