from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.condition import Condition as ConditionModel
from care.emr.resources.condition.spec import (
    CategoryChoices as ConditionCategoryChoices,
)
from care.emr.resources.condition.spec import (
    ClinicalStatusChoices as ConditionClinicalStatusChoices,
)
from care.emr.resources.condition.spec import ConditionReadSpec
from care.emr.resources.condition.spec import (
    SeverityChoices as ConditionSeverityChoices,
)
from care.emr.resources.condition.spec import (
    VerificationStatusChoices as ConditionVerificationStatusChoices,
)

CONDITION_CATEGORY_CODE_MAP = {
    ConditionCategoryChoices.problem_list_item: (
        "problem-list-item",
        "Problem List Item",
    ),
    ConditionCategoryChoices.encounter_diagnosis: (
        "encounter-diagnosis",
        "Encounter Diagnosis",
    ),
}

CONDITION_VERIFICATION_STATUS_CODE_MAP = {
    ConditionVerificationStatusChoices.unconfirmed: ("unconfirmed", "Unconfirmed"),
    ConditionVerificationStatusChoices.provisional: ("provisional", "Provisional"),
    ConditionVerificationStatusChoices.confirmed: ("confirmed", "Confirmed"),
    ConditionVerificationStatusChoices.refuted: ("refuted", "Refuted"),
    ConditionVerificationStatusChoices.entered_in_error: (
        "entered-in-error",
        "Entered in Error",
    ),
}

CONDITION_CLINICAL_STATUS_CODE_MAP = {
    ConditionClinicalStatusChoices.active: ("active", "Active"),
    ConditionClinicalStatusChoices.recurrence: ("recurrence", "Recurrence"),
    ConditionClinicalStatusChoices.relapse: ("relapse", "Relapse"),
    ConditionClinicalStatusChoices.inactive: ("inactive", "Inactive"),
    ConditionClinicalStatusChoices.remission: ("remission", "Remission"),
    ConditionClinicalStatusChoices.resolved: ("resolved", "Resolved"),
    ConditionClinicalStatusChoices.unknown: ("unknown", "Unknown"),
}

CONDITION_SEVERITY_CODE_MAP = {
    ConditionSeverityChoices.mild: ("255604002", "Mild"),
    ConditionSeverityChoices.moderate: ("6736007", "Moderate"),
    ConditionSeverityChoices.severe: ("24484000", "Severe"),
}


class ConditionMixin:
    @cache_profiles(Condition.get_resource_type())
    def _condition(self, condition: ConditionModel):
        condition_spec = ConditionReadSpec.serialize(condition)
        id = str(condition_spec.id)

        condition_code_display = condition_spec.code.get(
            "display"
        ) or condition_spec.code.get("code", "")
        condition_div_parts = [f"<p><b>Condition:</b> {condition_code_display}</p>"]
        if condition_spec.category:
            condition_div_parts.append(
                f"<p><b>Category:</b> {condition_spec.category}</p>"
            )
        if condition_spec.clinical_status:
            condition_div_parts.append(
                f"<p><b>Clinical Status:</b> {condition_spec.clinical_status}</p>"
            )
        if condition_spec.verification_status:
            condition_div_parts.append(
                f"<p><b>Verification Status:</b> {condition_spec.verification_status}</p>"
            )
        if condition_spec.severity:
            condition_div_parts.append(
                f"<p><b>Severity:</b> {condition_spec.severity}</p>"
            )
        if condition_spec.note:
            condition_div_parts.append(f"<p><b>Note:</b> {condition_spec.note}</p>")

        return Condition(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition"],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(condition_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            category=[
                self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/condition-category",
                    mapping=CONDITION_CATEGORY_CODE_MAP,
                    key=condition_spec.category,
                    default=ConditionCategoryChoices.problem_list_item.value,
                )
            ],
            verificationStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/condition-ver-status",
                mapping=CONDITION_VERIFICATION_STATUS_CODE_MAP,
                key=condition_spec.verification_status,
                default=ConditionVerificationStatusChoices.unconfirmed.value,
            ),
            clinicalStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/condition-clinical",
                mapping=CONDITION_CLINICAL_STATUS_CODE_MAP,
                key=condition_spec.clinical_status,
                default=ConditionClinicalStatusChoices.active.value,
            )
            if condition_spec.clinical_status
            else None,
            severity=self._concept_from_mapping(
                system="http://snomed.info/sct",
                mapping=CONDITION_SEVERITY_CODE_MAP,
                key=condition_spec.severity,
                default=ConditionSeverityChoices.moderate.value,
            )
            if condition_spec.severity
            else None,
            code=CodeableConcept(
                coding=[Coding(**condition_spec.code)],
                text=condition_spec.code.get("display"),
            ),
            recordedDate=condition_spec.created_date.isoformat(),
            onsetDateTime=condition_spec.onset.get("onset_datetime")
            if condition_spec.onset.get("onset_datetime")
            else None,
            onsetAge=condition_spec.onset.get("onset_age")
            if condition_spec.onset.get("onset_age")
            else None,
            onsetString=condition_spec.onset.get("onset_string")
            if condition_spec.onset.get("onset_string")
            else None,
            abatementDateTime=condition_spec.abatement.get("abatement_datetime")
            if condition_spec.abatement.get("abatement_datetime")
            else None,
            abatementAge=condition_spec.abatement.get("abatement_age")
            if condition_spec.abatement.get("abatement_age")
            else None,
            abatementString=condition_spec.abatement.get("abatement_string")
            if condition_spec.abatement.get("abatement_string")
            else None,
            note=[Annotation(text=condition_spec.note)]
            if condition_spec.note
            else None,
            subject=self._reference(self._patient(condition.patient)),
        )
