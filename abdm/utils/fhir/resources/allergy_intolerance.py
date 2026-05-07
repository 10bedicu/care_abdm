from datetime import UTC, datetime

from fhir.resources.R4B.allergyintolerance import AllergyIntolerance
from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.allergy_intolerance import (
    AllergyIntolerance as AllergyIntoleranceModel,
)
from care.emr.resources.allergy_intolerance.spec import AllergyIntoleranceReadSpec
from care.emr.resources.allergy_intolerance.spec import (
    CategoryChoices as AllergyIntoleranceCategoryChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    ClinicalStatusChoices as AllergyIntoleranceClinicalStatusChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    CriticalityChoices as AllergyIntoleranceCriticalityChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    VerificationStatusChoices as AllergyIntoleranceVerificationStatusChoices,
)

ALLERGY_CLINICAL_STATUS_CODE_MAP = {
    AllergyIntoleranceClinicalStatusChoices.active: ("active", "Active"),
    AllergyIntoleranceClinicalStatusChoices.inactive: ("inactive", "Inactive"),
    AllergyIntoleranceClinicalStatusChoices.resolved: ("resolved", "Resolved"),
}

ALLERGY_VERIFICATION_STATUS_CODE_MAP = {
    AllergyIntoleranceVerificationStatusChoices.unconfirmed: (
        "unconfirmed",
        "Unconfirmed",
    ),
    AllergyIntoleranceVerificationStatusChoices.confirmed: ("confirmed", "Confirmed"),
    AllergyIntoleranceVerificationStatusChoices.refuted: ("refuted", "Refuted"),
    AllergyIntoleranceVerificationStatusChoices.entered_in_error: (
        "entered-in-error",
        "Entered in Error",
    ),
}

ALLERGY_CATEGORY_CODE_MAP = {
    AllergyIntoleranceCategoryChoices.food: "food",
    AllergyIntoleranceCategoryChoices.medication: "medication",
    AllergyIntoleranceCategoryChoices.environment: "environment",
    AllergyIntoleranceCategoryChoices.biologic: "biologic",
}

ALLERGY_CRITICALITY_CODE_MAP = {
    AllergyIntoleranceCriticalityChoices.low: "low",
    AllergyIntoleranceCriticalityChoices.high: "high",
    AllergyIntoleranceCriticalityChoices.unable_to_assess: "unable-to-assess",
}


class AllergyIntoleranceMixin:
    @cache_profiles(AllergyIntolerance.get_resource_type())
    def _allergy_intolerance(self, allergy: AllergyIntoleranceModel):
        id = str(allergy.external_id)
        allergy_spec = AllergyIntoleranceReadSpec.serialize(allergy)

        allergy_code_display = allergy_spec.code.get(
            "display"
        ) or allergy_spec.code.get("code", "")
        allergy_div_parts = [f"<p><b>Allergen:</b> {allergy_code_display}</p>"]
        if allergy_spec.allergy_intolerance_type:
            allergy_div_parts.append(
                f"<p><b>Type:</b> {allergy_spec.allergy_intolerance_type}</p>"
            )
        if allergy_spec.category:
            allergy_div_parts.append(f"<p><b>Category:</b> {allergy_spec.category}</p>")
        if allergy_spec.criticality:
            allergy_div_parts.append(
                f"<p><b>Criticality:</b> {allergy_spec.criticality}</p>"
            )
        if allergy_spec.clinical_status:
            allergy_div_parts.append(
                f"<p><b>Clinical Status:</b> {allergy_spec.clinical_status}</p>"
            )
        if allergy_spec.verification_status:
            allergy_div_parts.append(
                f"<p><b>Verification Status:</b> {allergy_spec.verification_status}</p>"
            )
        if allergy_spec.recorded_date:
            allergy_div_parts.append(
                f"<p><b>Recorded Date:</b> {allergy_spec.recorded_date.date()}</p>"
            )
        if allergy_spec.last_occurrence:
            allergy_div_parts.append(
                f"<p><b>Last Occurrence:</b> {allergy_spec.last_occurrence.date()}</p>"
            )
        if allergy_spec.note:
            allergy_div_parts.append(f"<p><b>Note:</b> {allergy_spec.note}</p>")

        return AllergyIntolerance(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/AllergyIntolerance"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(allergy_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            verificationStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                mapping=ALLERGY_VERIFICATION_STATUS_CODE_MAP,
                key=allergy_spec.verification_status,
                default=AllergyIntoleranceVerificationStatusChoices.unconfirmed.value,
            ),
            clinicalStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                mapping=ALLERGY_CLINICAL_STATUS_CODE_MAP,
                key=allergy_spec.clinical_status,
                default=AllergyIntoleranceClinicalStatusChoices.active.value,
            ),
            category=[ALLERGY_CATEGORY_CODE_MAP.get(allergy_spec.category)]
            if allergy_spec.category
            else None,
            criticality=ALLERGY_CRITICALITY_CODE_MAP.get(
                allergy_spec.criticality, "unable-to-assess"
            ),
            code=CodeableConcept(
                coding=[Coding(**allergy_spec.code)],
                text=allergy_spec.code.get("display"),
            ),
            recordedDate=allergy_spec.recorded_date.isoformat()
            if allergy.recorded_date
            else allergy_spec.created_date.isoformat(),
            lastOccurrence=allergy_spec.last_occurrence.isoformat()
            if allergy.last_occurrence
            else None,
            onsetDateTime=allergy_spec.onset.get("onset_datetime")
            if allergy_spec.onset.get("onset_datetime")
            else None,
            onsetAge=allergy_spec.onset.get("onset_age")
            if allergy_spec.onset.get("onset_age")
            else None,
            onsetString=allergy_spec.onset.get("onset_string")
            if allergy_spec.onset.get("onset_string")
            else None,
            patient=self._reference(self._patient(allergy.patient)),
            encounter=self._reference(self._encounter(allergy.encounter)),
            recorder=self._reference(self._practitioner(allergy.created_by)),
            note=[Annotation(text=allergy_spec.note)] if allergy_spec.note else None,
        )
