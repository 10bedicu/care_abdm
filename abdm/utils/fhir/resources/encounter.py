from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.encounter import (
    Encounter,
    EncounterDiagnosis,
    EncounterHospitalization,
)
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.period import Period

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.condition import Condition as ConditionModel
from care.emr.models.encounter import Encounter as EncounterModel
from care.emr.resources.encounter.constants import (
    AdmitSourcesChoices as EncounterAdmitSourceChoices,
)
from care.emr.resources.encounter.constants import ClassChoices as EncounterClassChoices
from care.emr.resources.encounter.constants import (
    DietPreferenceChoices as EncounterDietPreferenceChoices,
)
from care.emr.resources.encounter.constants import (
    DischargeDispositionChoices as EncounterDischargeDispositionChoices,
)
from care.emr.resources.encounter.constants import EncounterPriorityChoices
from care.emr.resources.encounter.constants import (
    StatusChoices as EncounterStatusChoices,
)
from care.emr.resources.encounter.spec import EncounterRetrieveSpec, HospitalizationSpec

ENCOUNTER_CLASS_CODE_MAP = {
    EncounterClassChoices.amb: ("AMB", "Ambulatory"),
    EncounterClassChoices.emer: ("EMER", "Emergency"),
    EncounterClassChoices.hh: ("HH", "Home Health"),
    EncounterClassChoices.imp: ("IMP", "Inpatient"),
    EncounterClassChoices.obsenc: ("OBSENC", "Observation Encounter"),
    EncounterClassChoices.vr: ("VR", "Virtual"),
}

ENCOUNTER_PRIORITY_CODE_MAP = {
    EncounterPriorityChoices.ASAP: ("A", "ASAP"),
    EncounterPriorityChoices.callback_results: ("CR", "Callback Results"),
    EncounterPriorityChoices.callback_for_scheduling: (
        "EL",
        "Callback for Scheduling",
    ),
    EncounterPriorityChoices.elective: ("EL", "Elective"),
    EncounterPriorityChoices.emergency: ("EM", "Emergency"),
    EncounterPriorityChoices.preop: ("P", "Preop"),
    EncounterPriorityChoices.as_needed: ("PRN", "As Needed"),
    EncounterPriorityChoices.routine: ("R", "Routine"),
    EncounterPriorityChoices.rush_reporting: ("RR", "Rush Reporting"),
    EncounterPriorityChoices.stat: ("S", "Stat"),
    EncounterPriorityChoices.timing_critical: ("T", "Timing Critical"),
    EncounterPriorityChoices.use_as_directed: ("UD", "Use as Directed"),
    EncounterPriorityChoices.urgent: ("UR", "Urgent"),
}

ENCOUNTER_STATUS_CODE_MAP = {
    EncounterStatusChoices.planned: "planned",
    EncounterStatusChoices.in_progress: "in-progress",
    EncounterStatusChoices.on_hold: "onleave",
    EncounterStatusChoices.discharged: "finished",
    EncounterStatusChoices.completed: "finished",
    EncounterStatusChoices.cancelled: "cancelled",
    EncounterStatusChoices.discontinued: "discontinued",
    EncounterStatusChoices.entered_in_error: "entered-in-error",
    EncounterStatusChoices.unknown: "unknown",
}

ENCOUNTER_ADMIT_SOURCE_CODE_MAP = {
    EncounterAdmitSourceChoices.hosp_trans: (
        "hosp-trans",
        "Transferred from other hospital",
    ),
    EncounterAdmitSourceChoices.emd: ("emd", "From accident/emergency department"),
    EncounterAdmitSourceChoices.outp: ("outp", "From outpatient department"),
    EncounterAdmitSourceChoices.born: ("born", "Born in hospital"),
    EncounterAdmitSourceChoices.gp: ("gp", "General Practitioner referral"),
    EncounterAdmitSourceChoices.mp: (
        "mp",
        "Medical Practitioner/physician referral",
    ),
    EncounterAdmitSourceChoices.nursing: ("nursing", "From nursing home"),
    EncounterAdmitSourceChoices.psych: ("psych", "From psychiatric hospital"),
    EncounterAdmitSourceChoices.rehab: ("rehab", "From rehabilitation facility"),
    EncounterAdmitSourceChoices.other: ("other", "Other"),
}

ENCOUNTER_DISCHARGE_DISPOSITION_CODE_MAP = {
    EncounterDischargeDispositionChoices.home: ("home", "Home"),
    EncounterDischargeDispositionChoices.alt_home: ("alt_home", "Alternative Home"),
    EncounterDischargeDispositionChoices.other_hcf: (
        "other_hcf",
        "Other Healthcare Facility",
    ),
    EncounterDischargeDispositionChoices.hosp: ("hosp", "Hospice"),
    EncounterDischargeDispositionChoices.long: ("long", "Long-term Care"),
    EncounterDischargeDispositionChoices.aadvice: (
        "aadvice",
        "Left Against Advice",
    ),
    EncounterDischargeDispositionChoices.exp: ("exp", "Expired"),
    EncounterDischargeDispositionChoices.psy: ("psy", "Psychiatric Hospital"),
    EncounterDischargeDispositionChoices.rehab: ("rehab", "Rehabilitation"),
    EncounterDischargeDispositionChoices.snf: ("snf", "Skilled Nursing Facility"),
    EncounterDischargeDispositionChoices.oth: ("oth", "Other"),
}

ENCOUNTER_DIET_PREFERENCE_CODE_MAP = {
    EncounterDietPreferenceChoices.vegetarian: ("vegetarian", "Vegetarian"),
    EncounterDietPreferenceChoices.dairy_free: ("dairy-free", "Dairy Free"),
    EncounterDietPreferenceChoices.nut_free: ("nut-free", "Nut Free"),
    EncounterDietPreferenceChoices.gluten_free: ("gluten-free", "Gluten Free"),
    EncounterDietPreferenceChoices.vegan: ("vegan", "Vegan"),
    EncounterDietPreferenceChoices.halal: ("halal", "Halal"),
    EncounterDietPreferenceChoices.kosher: ("kosher", "Kosher"),
    EncounterDietPreferenceChoices.none: ("none", "None"),
}


class EncounterMixin:
    @cache_profiles(Encounter.get_resource_type())
    def _encounter(self, encounter: EncounterModel, include_diagnosis: bool = False):
        encounter_spec = EncounterRetrieveSpec.serialize(encounter)
        id = str(encounter_spec.id)

        hospitalization = encounter_spec.hospitalization
        if isinstance(hospitalization, dict):
            hospitalization = (
                HospitalizationSpec.model_validate(hospitalization)
                if hospitalization
                else None
            )

        period = encounter_spec.period
        period_start = (
            period.get("start")
            if isinstance(period, dict)
            else getattr(period, "start", None)
        )
        period_end = (
            period.get("end")
            if isinstance(period, dict)
            else getattr(period, "end", None)
        )

        encounter_div_parts = [f"<p><b>Status:</b> {encounter_spec.status}</p>"]
        encounter_div_parts.append(
            f"<p><b>Class:</b> {encounter_spec.encounter_class}</p>"
        )
        encounter_div_parts.append(f"<p><b>Priority:</b> {encounter_spec.priority}</p>")
        if period_start:
            encounter_div_parts.append(f"<p><b>Start:</b> {period_start}</p>")
        if period_end:
            encounter_div_parts.append(f"<p><b>End:</b> {period_end}</p>")
        if encounter_spec.external_identifier:
            encounter_div_parts.append(
                f"<p><b>External ID:</b> {encounter_spec.external_identifier}</p>"
            )
        if encounter_spec.discharge_summary_advice:
            encounter_div_parts.append(
                f"<p><b>Discharge Advice:</b> {encounter_spec.discharge_summary_advice}</p>"
            )

        return Encounter(
            **{
                "id": id,
                "meta": Meta(
                    versionId="1",
                    lastUpdated=datetime.now(UTC).isoformat(),
                    profile=[
                        "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Encounter"
                    ],
                ),
                "text": Narrative(
                    status="generated",
                    div='<div xmlns="http://www.w3.org/1999/xhtml">'
                    + "".join(encounter_div_parts)
                    + "</div>",
                ),
                "identifier": [Identifier(value=id)],
                "status": ENCOUNTER_STATUS_CODE_MAP.get(
                    encounter_spec.status, "unknown"
                ),
                "class": self._coding_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    mapping=ENCOUNTER_CLASS_CODE_MAP,
                    key=encounter_spec.encounter_class,
                    default=EncounterClassChoices.amb.value,
                ),
                "subject": self._reference(self._patient(encounter.patient)),
                "priority": self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActPriority",
                    mapping=ENCOUNTER_PRIORITY_CODE_MAP,
                    key=encounter_spec.priority,
                    default=EncounterPriorityChoices.ASAP.value,
                ),
                "period": Period(**encounter_spec.period),
                "diagnosis": (
                    [
                        EncounterDiagnosis(
                            condition=self._reference(
                                self._condition(encounter_condition)
                            )
                        )
                        for encounter_condition in ConditionModel.objects.filter(
                            encounter=encounter
                        )
                    ]
                    if include_diagnosis
                    else None
                ),
                "hospitalization": EncounterHospitalization(
                    reAdmission=CodeableConcept(
                        coding=[
                            Coding(
                                code="R",
                                system="http://terminology.hl7.org/CodeSystem/v2-0092",
                                display="Re-admission",
                            )
                        ],
                        text="Re-admission",
                    )
                    if hospitalization.re_admission
                    else None,
                    admitSource=self._concept_from_mapping(
                        system="http://terminology.hl7.org/CodeSystem/admit-source",
                        mapping=ENCOUNTER_ADMIT_SOURCE_CODE_MAP,
                        key=hospitalization.admit_source,
                        default=EncounterAdmitSourceChoices.other.value,
                    )
                    if hospitalization.admit_source
                    else None,
                    dischargeDisposition=self._concept_from_mapping(
                        system="http://terminology.hl7.org/CodeSystem/discharge-disposition",
                        mapping=ENCOUNTER_DISCHARGE_DISPOSITION_CODE_MAP,
                        key=hospitalization.discharge_disposition,
                        default=EncounterDischargeDispositionChoices.home.value,
                    )
                    if hospitalization.discharge_disposition
                    else None,
                    dietPreference=[
                        self._concept_from_mapping(
                            system="http://terminology.hl7.org/CodeSystem/diet",
                            mapping=ENCOUNTER_DIET_PREFERENCE_CODE_MAP,
                            key=hospitalization.diet_preference,
                            default=EncounterDietPreferenceChoices.none.value,
                        )
                    ]
                    if hospitalization.diet_preference
                    else None,
                )
                if hospitalization
                else None,
            }
        )
