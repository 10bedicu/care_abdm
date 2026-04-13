import base64
from datetime import UTC, datetime
from functools import wraps

from django.db.models import Q
from fhir.resources.R4B.address import Address
from fhir.resources.R4B.allergyintolerance import AllergyIntolerance
from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
)
from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.duration import Duration
from fhir.resources.R4B.encounter import (
    Encounter,
    EncounterDiagnosis,
    EncounterHospitalization,
)
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.observation import (
    Observation,
    ObservationComponent,
    ObservationReferenceRange,
)
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.range import Range
from fhir.resources.R4B.ratio import Ratio
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource
from fhir.resources.R4B.timing import Timing, TimingRepeat

from abdm.models.health_facility import HealthFacility as HealthFacilityModel
from abdm.service.helper import ABDMAPIException, uuid
from abdm.settings import plugin_settings as settings
from care.emr.models.allergy_intolerance import (
    AllergyIntolerance as AllergyIntoleranceModel,
)
from care.emr.models.base import EMRBaseModel
from care.emr.models.condition import Condition as ConditionModel
from care.emr.models.encounter import Encounter as EncounterModel
from care.emr.models.file_upload import FileUpload as FileUploadModel
from care.emr.models.medication_request import (
    MedicationRequest as MedicationRequestModel,
)
from care.emr.models.medication_statement import (
    MedicationStatement as MedicationStatementModel,
)
from care.emr.models.observation import Observation as ObservationModel
from care.emr.models.patient import Patient as PatientModel
from care.emr.models.questionnaire import (
    QuestionnaireResponse as QuestionnaireResponseModel,
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
from care.emr.resources.common.coding import Coding as CodingSpec
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
from care.emr.resources.encounter.spec import EncounterRetrieveSpec
from care.emr.resources.facility.spec import FacilityRetrieveSpec
from care.emr.resources.file_upload.spec import FileTypeChoices
from care.emr.resources.medication.request.spec import (
    DosageInstruction as DosageInstructionSpec,
)
from care.emr.resources.medication.request.spec import (
    DoseType as MedicationRequestDoseType,
)
from care.emr.resources.medication.request.spec import (
    MedicationRequestCategory,
    MedicationRequestIntent,
    MedicationRequestPriority,
    MedicationRequestReadSpec,
    MedicationRequestStatus,
)
from care.emr.resources.medication.request.spec import (
    StatusReason as MedicationRequestStatusReason,
)
from care.emr.resources.medication.statement.spec import (
    MedicationStatementReadSpec,
    MedicationStatementStatus,
)
from care.emr.resources.observation.spec import ObservationReadSpec
from care.emr.resources.patient.spec import PatientRetrieveSpec
from care.emr.resources.user.spec import UserRetrieveSpec
from care.facility.models import Facility as FacilityModel
from care.users.models import User as UserModel

CARE_IDENTIFIER_SYSTEM = settings.BACKEND_DOMAIN


class Fhir:
    def __init__(self):
        self._profiles = {}
        self._resource_id_url_map = {}

    @staticmethod
    def cache_profiles(resource_type: str):
        def decorator(func):
            @wraps(func)
            def wrapper(self, model_instance: EMRBaseModel, *args, **kwargs):
                if not hasattr(model_instance, "external_id"):
                    err = f"{model_instance.__class__.__name__} does not have 'external_id' attribute"
                    raise AttributeError(err)

                cache_key_prefix = kwargs.get("cache_key_prefix", "")
                cache_key_id = str(model_instance.external_id)
                cache_key_suffix = kwargs.get("cache_key_suffix", "")
                cache_key = f"{resource_type}/{cache_key_prefix}{cache_key_id}{cache_key_suffix}"

                if cache_key in self._profiles:
                    return self._profiles[cache_key]

                result = func(self, model_instance, *args, **kwargs)

                self._profiles[cache_key] = result
                self._resource_id_url_map[cache_key] = uuid()
                return result

            return wrapper

        return decorator

    def cached_profiles(self):
        return list(
            filter(lambda profile: profile is not None, self._profiles.values())
        )

    def _reference_url(self, resource: Resource = None):
        if resource is None:
            return ""

        key = f"{resource.resource_type}/{resource.id}"
        return f"urn:uuid:{self._resource_id_url_map.get(key, uuid())}"

    def _reference(self, resource: Resource = None):
        if resource is None:
            return None

        return Reference(reference=self._reference_url(resource))

    def _coding(self, coding: CodingSpec | None):
        if coding is None:
            return None

        return Coding(
            code=coding.code,
            display=coding.display,
            system=coding.system,
        )

    def _coding_to_codable_concept(self, coding: CodingSpec | None):
        if coding is None:
            return None

        fhir_coding = self._coding(coding)
        return CodeableConcept(coding=[fhir_coding], text=fhir_coding.display)

    def _concept_from_mapping(
        self, system: str, mapping: dict[str, tuple[str, str]], key: str, default: str
    ):
        coding = self._coding_from_mapping(system, mapping, key, default)
        return CodeableConcept(coding=[coding], text=coding.display)

    def _coding_from_mapping(
        self, system: str, mapping: dict[str, tuple[str, str]], key: str, default: str
    ):
        coding = mapping.get(key)
        if not mapping:
            coding = mapping.get(default)

        return Coding(
            system=system,
            code=coding[0],
            display=coding[1] or None,
        )

    @cache_profiles(Patient.get_resource_type())
    def _patient(self, patient: PatientModel):
        patient_spec = PatientRetrieveSpec.serialize(patient)
        id = str(patient_spec.id)

        address = []
        if patient_spec.address:
            address.append(
                Address(
                    line=[patient_spec.address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                )
            )
        if (
            patient_spec.permanent_address
            and patient_spec.permanent_address != patient_spec.address
        ):
            address.append(
                Address(
                    line=[patient_spec.permanent_address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                )
            )

        patient_div_parts = [f"<p><b>Name:</b> {patient_spec.name}</p>"]
        if patient_spec.gender:
            patient_div_parts.append(f"<p><b>Gender:</b> {patient_spec.gender}</p>")
        birth_date = getattr(patient.abha_number, "parsed_date_of_birth", None) if patient.abha_number else None
        if birth_date:
            patient_div_parts.append(f"<p><b>Date of Birth:</b> {birth_date}</p>")
        if patient_spec.phone_number:
            patient_div_parts.append(f"<p><b>Phone:</b> {patient_spec.phone_number}</p>")
        if patient_spec.emergency_phone_number:
            patient_div_parts.append(f"<p><b>Emergency Phone:</b> {patient_spec.emergency_phone_number}</p>")
        if patient_spec.address:
            patient_div_parts.append(f"<p><b>Address:</b> {patient_spec.address}</p>")

        return Patient(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(patient_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            name=[HumanName(text=patient_spec.name)],
            telecom=[
                *(
                    [ContactPoint(system="phone", value=patient_spec.phone_number)]
                    if patient_spec.phone_number
                    else []
                ),
                *(
                    [
                        ContactPoint(
                            system="phone", value=patient_spec.emergency_phone_number
                        )
                    ]
                    if patient_spec.emergency_phone_number
                    else []
                ),
            ],
            gender=patient_spec.gender,
            birthDate=patient.abha_number.parsed_date_of_birth,
            address=address or None,
        )

    @cache_profiles(Practitioner.get_resource_type())
    def _practitioner(self, user: UserModel):
        user_spec = UserRetrieveSpec.serialize(user)
        id = str(user_spec.id)

        practitioner_div_parts = [f"<p><b>Name:</b> {user.full_name or user.username}</p>"]
        if user_spec.gender:
            practitioner_div_parts.append(f"<p><b>Gender:</b> {user_spec.gender}</p>")
        if user.date_of_birth:
            practitioner_div_parts.append(f"<p><b>Date of Birth:</b> {user.date_of_birth}</p>")
        if user_spec.phone_number:
            practitioner_div_parts.append(f"<p><b>Phone:</b> {user_spec.phone_number}</p>")
        if user_spec.email:
            practitioner_div_parts.append(f"<p><b>Email:</b> {user_spec.email}</p>")

        return Practitioner(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(practitioner_div_parts) + "</div>",
            ),
            identifier=[
                Identifier(
                    value=id,
                    type=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0203",
                                code="PRN",
                                display="Provider number",
                            )
                        ],
                        text="Provider number",
                    ),
                )
            ],
            name=[HumanName(text=user.full_name or user.username)],
            telecom=[
                *(
                    [ContactPoint(system="phone", value=user_spec.phone_number)]
                    if user_spec.phone_number
                    else []
                ),
                *(
                    [ContactPoint(system="email", value=user_spec.email)]
                    if user_spec.email
                    else []
                ),
            ],
            gender=user_spec.gender,
            birthDate=user.date_of_birth,
        )

    @cache_profiles(Organization.get_resource_type())
    def _organization(self, facility: FacilityModel):
        health_facility = HealthFacilityModel.objects.filter(facility=facility).first()
        facility_spec = FacilityRetrieveSpec.serialize(facility)
        id = str(facility_spec.id)
        hf_id = health_facility.hf_id if health_facility else None

        organization_div_parts = [
            f"<p><b>Name:</b> {facility_spec.name}</p>",
            "<p><b>Type:</b> Healthcare Provider</p>",
        ]
        if facility_spec.phone_number:
            organization_div_parts.append(f"<p><b>Phone:</b> {facility_spec.phone_number}</p>")
        if facility_spec.address:
            address_text = facility_spec.address
            if facility_spec.pincode:
                address_text += f", {facility_spec.pincode}"
            organization_div_parts.append(f"<p><b>Address:</b> {address_text}, IN</p>")

        return Organization(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(organization_div_parts) + "</div>",
            ),
            identifier=[
                Identifier(
                    system=(
                        "https://facility.ndhm.gov.in"
                        if hf_id
                        else f"{CARE_IDENTIFIER_SYSTEM}/facility"
                    ),
                    value=hf_id or id,
                    type=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0203",
                                code="FI",
                                display="Facility ID",
                            )
                        ],
                        text="Facility ID",
                    ),
                )
            ],
            type=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/organization-type",
                            code="prov",
                            display="Healthcare Provider",
                        )
                    ],
                    text="Healthcare Provider",
                )
            ],
            name=facility_spec.name,
            telecom=[
                *(
                    [ContactPoint(system="phone", value=facility_spec.phone_number)]
                    if facility_spec.phone_number
                    else []
                )
            ],
            address=[
                Address(
                    line=[facility_spec.address],
                    postalCode=facility_spec.pincode,
                    country="IN",
                )
            ],
        )

    @cache_profiles(Condition.get_resource_type())
    def _condition(self, condition: ConditionModel):
        condition_spec = ConditionReadSpec.serialize(condition)
        id = str(condition_spec.id)

        condition_category_code_map = {
            ConditionCategoryChoices.problem_list_item: (
                "problem-list-item",
                "Problem List Item",
            ),
            ConditionCategoryChoices.encounter_diagnosis: (
                "encounter-diagnosis",
                "Encounter Diagnosis",
            ),
        }

        condition_verification_status_code_map = {
            ConditionVerificationStatusChoices.unconfirmed: (
                "unconfirmed",
                "Unconfirmed",
            ),
            ConditionVerificationStatusChoices.provisional: (
                "provisional",
                "Provisional",
            ),
            ConditionVerificationStatusChoices.confirmed: (
                "confirmed",
                "Confirmed",
            ),
            ConditionVerificationStatusChoices.refuted: (
                "refuted",
                "Refuted",
            ),
            ConditionVerificationStatusChoices.entered_in_error: (
                "entered-in-error",
                "Entered in Error",
            ),
        }

        condition_clinical_status_code_map = {
            ConditionClinicalStatusChoices.active: (
                "active",
                "Active",
            ),
            ConditionClinicalStatusChoices.recurrence: (
                "recurrence",
                "Recurrence",
            ),
            ConditionClinicalStatusChoices.relapse: (
                "relapse",
                "Relapse",
            ),
            ConditionClinicalStatusChoices.inactive: (
                "inactive",
                "Inactive",
            ),
            ConditionClinicalStatusChoices.remission: (
                "remission",
                "Remission",
            ),
            ConditionClinicalStatusChoices.resolved: (
                "resolved",
                "Resolved",
            ),
            ConditionClinicalStatusChoices.unknown: (
                "unknown",
                "Unknown",
            ),
        }

        condition_severity_code_map = {
            ConditionSeverityChoices.mild: (
                "255604002",
                "Mild",
            ),
            ConditionSeverityChoices.moderate: (
                "6736007",
                "Moderate",
            ),
            ConditionSeverityChoices.severe: (
                "24484000",
                "Severe",
            ),
        }

        condition_code_display = condition_spec.code.get("display") or condition_spec.code.get("code", "")
        condition_div_parts = [f"<p><b>Condition:</b> {condition_code_display}</p>"]
        if condition_spec.category:
            condition_div_parts.append(f"<p><b>Category:</b> {condition_spec.category}</p>")
        if condition_spec.clinical_status:
            condition_div_parts.append(f"<p><b>Clinical Status:</b> {condition_spec.clinical_status}</p>")
        if condition_spec.verification_status:
            condition_div_parts.append(f"<p><b>Verification Status:</b> {condition_spec.verification_status}</p>")
        if condition_spec.severity:
            condition_div_parts.append(f"<p><b>Severity:</b> {condition_spec.severity}</p>")
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
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(condition_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            category=[
                self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/condition-category",
                    mapping=condition_category_code_map,
                    key=condition_spec.category,
                    default=ConditionCategoryChoices.problem_list_item.value,
                )
            ],
            verificationStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/condition-ver-status",
                mapping=condition_verification_status_code_map,
                key=condition_spec.verification_status,
                default=ConditionVerificationStatusChoices.unconfirmed.value,
            ),
            clinicalStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/condition-clinical",
                mapping=condition_clinical_status_code_map,
                key=condition_spec.clinical_status,
                default=ConditionClinicalStatusChoices.active.value,
            )
            if condition_spec.clinical_status
            else None,
            severity=self._concept_from_mapping(
                system="http://snomed.info/sct",
                mapping=condition_severity_code_map,
                key=condition_spec.severity,
                default=ConditionSeverityChoices.moderate.value,
            )
            if condition_spec.severity
            else None,
            code=CodeableConcept(
                coding=[Coding(**condition_spec.code)],
                text=condition_spec.code.get("display"),
            ),
            subject=self._reference(self._patient(condition.patient)),
        )

    @cache_profiles(Encounter.get_resource_type())
    def _encounter(self, encounter: EncounterModel, include_diagnosis: bool = False):
        encounter_spec = EncounterRetrieveSpec.serialize(encounter)
        id = str(encounter_spec.id)

        encounter_class_code_map = {
            EncounterClassChoices.amb: (
                "AMB",
                "Ambulatory",
            ),
            EncounterClassChoices.emer: (
                "EMER",
                "Emergency",
            ),
            EncounterClassChoices.hh: (
                "HH",
                "Home Health",
            ),
            EncounterClassChoices.imp: (
                "IMP",
                "Inpatient",
            ),
            EncounterClassChoices.obsenc: (
                "OBSENC",
                "Observation Encounter",
            ),
            EncounterClassChoices.vr: (
                "VR",
                "Virtual",
            ),
        }

        encounter_priority_code_map = {
            EncounterPriorityChoices.ASAP: (
                "A",
                "ASAP",
            ),
            EncounterPriorityChoices.callback_results: (
                "CR",
                "Callback Results",
            ),
            EncounterPriorityChoices.callback_for_scheduling: (
                "EL",
                "Callback for Scheduling",
            ),
            EncounterPriorityChoices.elective: (
                "EL",
                "Elective",
            ),
            EncounterPriorityChoices.emergency: (
                "EM",
                "Emergency",
            ),
            EncounterPriorityChoices.preop: (
                "P",
                "Preop",
            ),
            EncounterPriorityChoices.as_needed: (
                "PRN",
                "As Needed",
            ),
            EncounterPriorityChoices.routine: (
                "R",
                "Routine",
            ),
            EncounterPriorityChoices.rush_reporting: (
                "RR",
                "Rush Reporting",
            ),
            EncounterPriorityChoices.stat: (
                "S",
                "Stat",
            ),
            EncounterPriorityChoices.timing_critical: (
                "T",
                "Timing Critical",
            ),
            EncounterPriorityChoices.use_as_directed: (
                "UD",
                "Use as Directed",
            ),
            EncounterPriorityChoices.urgent: (
                "UR",
                "Urgent",
            ),
        }

        encounter_status_code_map = {
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

        encounter_admit_source_code_map = {
            EncounterAdmitSourceChoices.hosp_trans: (
                "hosp-trans",
                "Transferred from other hospital",
            ),
            EncounterAdmitSourceChoices.emd: (
                "emd",
                "From accident/emergency department",
            ),
            EncounterAdmitSourceChoices.outp: (
                "outp",
                "From outpatient department",
            ),
            EncounterAdmitSourceChoices.born: (
                "born",
                "Born in hospital",
            ),
            EncounterAdmitSourceChoices.gp: (
                "gp",
                "General Practitioner referral",
            ),
            EncounterAdmitSourceChoices.mp: (
                "mp",
                "Medical Practitioner/physician referral",
            ),
            EncounterAdmitSourceChoices.nursing: (
                "nursing",
                "From nursing home",
            ),
            EncounterAdmitSourceChoices.psych: (
                "psych",
                "From psychiatric hospital",
            ),
            EncounterAdmitSourceChoices.rehab: (
                "rehab",
                "From rehabilitation facility",
            ),
            EncounterAdmitSourceChoices.other: (
                "other",
                "Other",
            ),
        }

        encounter_discharge_disposition_code_map = {
            EncounterDischargeDispositionChoices.home: (
                "home",
                "Home",
            ),
            EncounterDischargeDispositionChoices.alt_home: (
                "alt_home",
                "Alternative Home",
            ),
            EncounterDischargeDispositionChoices.other_hcf: (
                "other_hcf",
                "Other Healthcare Facility",
            ),
            EncounterDischargeDispositionChoices.hosp: (
                "hosp",
                "Hospice",
            ),
            EncounterDischargeDispositionChoices.long: (
                "long",
                "Long-term Care",
            ),
            EncounterDischargeDispositionChoices.aadvice: (
                "aadvice",
                "Left Against Advice",
            ),
            EncounterDischargeDispositionChoices.exp: (
                "exp",
                "Expired",
            ),
            EncounterDischargeDispositionChoices.psy: (
                "psy",
                "Psychiatric Hospital",
            ),
            EncounterDischargeDispositionChoices.rehab: (
                "rehab",
                "Rehabilitation",
            ),
            EncounterDischargeDispositionChoices.snf: (
                "snf",
                "Skilled Nursing Facility",
            ),
            EncounterDischargeDispositionChoices.oth: (
                "oth",
                "Other",
            ),
        }

        encounter_diet_preference_code_map = {
            EncounterDietPreferenceChoices.vegetarian: (
                "vegetarian",
                "Vegetarian",
            ),
            EncounterDietPreferenceChoices.dairy_free: (
                "dairy-free",
                "Dairy Free",
            ),
            EncounterDietPreferenceChoices.nut_free: (
                "nut-free",
                "Nut Free",
            ),
            EncounterDietPreferenceChoices.gluten_free: (
                "gluten-free",
                "Gluten Free",
            ),
            EncounterDietPreferenceChoices.vegan: (
                "vegan",
                "Vegan",
            ),
            EncounterDietPreferenceChoices.halal: (
                "halal",
                "Halal",
            ),
            EncounterDietPreferenceChoices.kosher: (
                "kosher",
                "Kosher",
            ),
            EncounterDietPreferenceChoices.none: (
                "none",
                "None",
            ),
        }

        period = encounter_spec.period
        period_start = period.get("start") if isinstance(period, dict) else getattr(period, "start", None)
        period_end = period.get("end") if isinstance(period, dict) else getattr(period, "end", None)

        encounter_div_parts = [f"<p><b>Status:</b> {encounter_spec.status}</p>"]
        encounter_div_parts.append(f"<p><b>Class:</b> {encounter_spec.encounter_class}</p>")
        encounter_div_parts.append(f"<p><b>Priority:</b> {encounter_spec.priority}</p>")
        if period_start:
            encounter_div_parts.append(f"<p><b>Start:</b> {period_start}</p>")
        if period_end:
            encounter_div_parts.append(f"<p><b>End:</b> {period_end}</p>")
        if encounter_spec.external_identifier:
            encounter_div_parts.append(f"<p><b>External ID:</b> {encounter_spec.external_identifier}</p>")
        if encounter_spec.discharge_summary_advice:
            encounter_div_parts.append(f"<p><b>Discharge Advice:</b> {encounter_spec.discharge_summary_advice}</p>")

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
                    div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(encounter_div_parts) + "</div>",
                ),
                "identifier": [Identifier(value=id)],
                "status": encounter_status_code_map.get(
                    encounter_spec.status, "unknown"
                ),
                "class": self._coding_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    mapping=encounter_class_code_map,
                    key=encounter_spec.encounter_class,
                    default=EncounterClassChoices.amb.value,
                ),
                "subject": self._reference(self._patient(encounter.patient)),
                "priority": self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActPriority",
                    mapping=encounter_priority_code_map,
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
                    re_admission=CodeableConcept(
                        coding=[
                            Coding(
                                code="R",
                                system="http://terminology.hl7.org/CodeSystem/v2-0092",
                                display="Re-admission",
                            )
                        ],
                        text="Re-admission",
                    )
                    if encounter_spec.hospitalization.re_admission
                    else None,
                    admitSource=self._concept_from_mapping(
                        system="http://terminology.hl7.org/CodeSystem/admit-source",
                        mapping=encounter_admit_source_code_map,
                        key=encounter_spec.hospitalization.admit_source,
                        default=EncounterAdmitSourceChoices.other.value,
                    )
                    if encounter_spec.hospitalization.admit_source
                    else None,
                    dischargeDisposition=self._concept_from_mapping(
                        system="http://terminology.hl7.org/CodeSystem/discharge-disposition",
                        mapping=encounter_discharge_disposition_code_map,
                        key=encounter_spec.hospitalization.discharge_disposition,
                        default=EncounterDischargeDispositionChoices.home.value,
                    )
                    if encounter_spec.hospitalization.discharge_disposition
                    else None,
                    dietPreference=[
                        self._concept_from_mapping(
                            system="http://terminology.hl7.org/CodeSystem/diet",
                            mapping=encounter_diet_preference_code_map,
                            key=encounter_spec.hospitalization.diet_preference,
                            default=EncounterDietPreferenceChoices.none.value,
                        )
                    ]
                    if encounter_spec.hospitalization.diet_preference
                    else None,
                )
                if encounter_spec.hospitalization
                else None,
            }
        )

    @cache_profiles(MedicationRequest.get_resource_type())
    def _medication_request(self, request: MedicationRequestModel):
        request_spec = MedicationRequestReadSpec.serialize(request)
        id = str(request_spec.id)

        medication_request_status_code_map = {
            MedicationRequestStatus.active: "active",
            MedicationRequestStatus.on_hold: "on-hold",
            MedicationRequestStatus.cancelled: "cancelled",
            MedicationRequestStatus.completed: "completed",
            MedicationRequestStatus.entered_in_error: "entered-in-error",
            MedicationRequestStatus.stopped: "stopped",
            MedicationRequestStatus.draft: "draft",
            MedicationRequestStatus.unknown: "unknown",
        }

        medication_request_intent_code_map = {
            MedicationRequestIntent.proposal: "proposal",
            MedicationRequestIntent.plan: "plan",
            MedicationRequestIntent.order: "order",
            MedicationRequestIntent.original_order: "original-order",
            MedicationRequestIntent.reflex_order: "reflex-order",
            MedicationRequestIntent.filler_order: "filler-order",
            MedicationRequestIntent.instance_order: "instance-order",
        }

        medication_request_status_reason_code_map = {
            MedicationRequestStatusReason.alt_choice: (
                "altchoice",
                "Try another treatment first",
            ),
            MedicationRequestStatusReason.clarif: (
                "clarif",
                "Prescription requires clarification",
            ),
            MedicationRequestStatusReason.drughigh: ("drughigh", "Drug level too high"),
            MedicationRequestStatusReason.hospadm: ("hospadm", "Admission to hospital"),
            MedicationRequestStatusReason.labint: ("labint", "Lab interference issues"),
            MedicationRequestStatusReason.non_avail: (
                "non-avail",
                "Patient not available",
            ),
            MedicationRequestStatusReason.preg: (
                "preg",
                "Parent is pregnant/breast feeding",
            ),
            MedicationRequestStatusReason.salg: ("salg", "Allergy"),
            MedicationRequestStatusReason.sddi: (
                "sddi",
                "Drug interacts with another drug",
            ),
            MedicationRequestStatusReason.sdupther: ("sdupther", "Duplicate therapy"),
            MedicationRequestStatusReason.sintol: ("sintol", "Suspected intolerance"),
            MedicationRequestStatusReason.surg: (
                "surg",
                "Patient scheduled for surgery",
            ),
            MedicationRequestStatusReason.washout: (
                "washout",
                "Waiting for old drug to wash out",
            ),
        }

        medication_request_priority_code_map = {
            MedicationRequestPriority.routine: "routine",
            MedicationRequestPriority.urgent: "urgent",
            MedicationRequestPriority.asap: "asap",
            MedicationRequestPriority.stat: "stat",
        }

        medication_request_category_code_map = {
            MedicationRequestCategory.inpatient: ("inpatient", "Inpatient"),
            MedicationRequestCategory.outpatient: ("outpatient", "Outpatient"),
            MedicationRequestCategory.community: ("community", "Community"),
            MedicationRequestCategory.discharge: ("discharge", "Discharge"),
        }

        medication_request_dosage_and_rate_type_code_map = {
            MedicationRequestDoseType.calculated: ("calculated", "Calculated"),
            MedicationRequestDoseType.ordered: ("ordered", "Ordered"),
        }

        medication_name = (
            request_spec.requested_product.get("name")
            if request_spec.requested_product
            else None
        ) or (
            request_spec.medication.get("display") or request_spec.medication.get("code")
            if request_spec.medication
            else None
        ) or "Medication Request"

        med_req_div_parts = [f"<p><b>Medication:</b> {medication_name}</p>"]
        med_req_div_parts.append(f"<p><b>Status:</b> {request_spec.status}</p>")
        med_req_div_parts.append(f"<p><b>Intent:</b> {request_spec.intent}</p>")
        if request_spec.priority:
            med_req_div_parts.append(f"<p><b>Priority:</b> {request_spec.priority}</p>")
        if request_spec.category:
            med_req_div_parts.append(f"<p><b>Category:</b> {request_spec.category}</p>")
        if request_spec.status_reason:
            med_req_div_parts.append(f"<p><b>Status Reason:</b> {request_spec.status_reason}</p>")
        if request_spec.note:
            med_req_div_parts.append(f"<p><b>Note:</b> {request_spec.note}</p>")

        return MedicationRequest(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationRequest"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(med_req_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=medication_request_status_code_map.get(
                request_spec.status, "unknown"
            ),
            statusReason=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/medicationrequest-status-reason",
                mapping=medication_request_status_reason_code_map,
                key=request_spec.status_reason,
                default=MedicationRequestStatusReason.alt_choice.value,
            )
            if request_spec.status_reason
            else None,
            intent=medication_request_intent_code_map.get(request_spec.intent, "order"),
            category=[
                self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/medicationrequest-category",
                    mapping=medication_request_category_code_map,
                    key=request_spec.category,
                    default=MedicationRequestCategory.inpatient.value,
                )
            ],
            priority=medication_request_priority_code_map.get(
                request_spec.priority, "routine"
            ),
            authoredOn=request_spec.created_date.isoformat(),
            dosageInstruction=[
                Dosage(
                    sequence=dosage_spec.sequence,
                    text=dosage_spec.text,
                    patientInstruction=dosage_spec.patient_instruction,
                    additionalInstruction=[
                        self._coding_to_codable_concept(instruction)
                        for instruction in dosage_spec.additional_instruction
                    ]
                    if dosage_spec.additional_instruction
                    else None,
                    asNeededCodeableConcept=self._coding_to_codable_concept(
                        dosage_spec.as_needed_for
                    ),
                    timing=Timing(
                        repeat=TimingRepeat(
                            frequency=dosage_spec.timing.repeat.frequency,
                            period=dosage_spec.timing.repeat.period,
                            periodUnit=dosage_spec.timing.repeat.period_unit,
                            boundsDuration=Duration(
                                value=dosage_spec.timing.repeat.bounds_duration.value,
                                unit=dosage_spec.timing.repeat.bounds_duration.unit,
                            )
                            if dosage_spec.timing.repeat.bounds_duration
                            else None,
                        )
                        if dosage_spec.timing.repeat
                        else None,
                        code=self._coding_to_codable_concept(dosage_spec.timing.code),
                    )
                    if dosage_spec.timing
                    else None,
                    site=self._coding_to_codable_concept(dosage_spec.site),
                    route=self._coding_to_codable_concept(dosage_spec.route),
                    method=self._coding_to_codable_concept(dosage_spec.method),
                    doseAndRate=[
                        DosageDoseAndRate(
                            type=self._concept_from_mapping(
                                system="http://terminology.hl7.org/CodeSystem/dose-rate-type",
                                mapping=medication_request_dosage_and_rate_type_code_map,
                                key=dosage_spec.dose_and_rate.type,
                                default=MedicationRequestDoseType.ordered.value,
                            ),
                            doseRange=Range(
                                low=Quantity(
                                    value=dosage_spec.dose_and_rate.dose_range.low.value,
                                    unit=dosage_spec.dose_and_rate.dose_range.low.unit.display,
                                    system=dosage_spec.dose_and_rate.dose_range.low.unit.system,
                                    code=dosage_spec.dose_and_rate.dose_range.low.unit.code,
                                )
                                if dosage_spec.dose_and_rate.dose_range.low
                                else None,
                                high=Quantity(
                                    value=dosage_spec.dose_and_rate.dose_range.high.value,
                                    unit=dosage_spec.dose_and_rate.dose_range.high.unit.display,
                                    system=dosage_spec.dose_and_rate.dose_range.high.unit.system,
                                    code=dosage_spec.dose_and_rate.dose_range.high.unit.code,
                                )
                                if dosage_spec.dose_and_rate.dose_range.high
                                else None,
                            )
                            if dosage_spec.dose_and_rate.dose_range
                            else None,
                            doseQuantity=Quantity(
                                value=dosage_spec.dose_and_rate.dose_quantity.value,
                                unit=dosage_spec.dose_and_rate.dose_quantity.unit.display,
                                system=dosage_spec.dose_and_rate.dose_quantity.unit.system,
                                code=dosage_spec.dose_and_rate.dose_quantity.unit.code,
                            )
                            if dosage_spec.dose_and_rate.dose_quantity
                            else None,
                        )
                    ],
                    maxDosePerPeriod=Ratio(
                        numerator=Quantity(
                            value=dosage_spec.max_dose_per_period.low.value,
                            unit=dosage_spec.max_dose_per_period.low.unit.display,
                            system=dosage_spec.max_dose_per_period.low.unit.system,
                            code=dosage_spec.max_dose_per_period.low.unit.code,
                        )
                        if dosage_spec.max_dose_per_period.low
                        else None,
                        denominator=Quantity(
                            value=dosage_spec.max_dose_per_period.high.value,
                            unit=dosage_spec.max_dose_per_period.high.unit.display,
                            system=dosage_spec.max_dose_per_period.high.unit.system,
                            code=dosage_spec.max_dose_per_period.high.unit.code,
                        )
                        if dosage_spec.max_dose_per_period.high
                        else None,
                    )
                    if dosage_spec.max_dose_per_period
                    else None,
                )
                for dosage in request_spec.dosage_instruction
                for dosage_spec in [DosageInstructionSpec(**dosage)]
            ],
            note=[Annotation(text=request_spec.note)] if request_spec.note else None,
            medicationCodeableConcept=CodeableConcept(
                coding=[
                    Coding(
                        **(
                            request_spec.medication
                            or (
                                request_spec.requested_product.get("code", {})
                                if request_spec.requested_product
                                else {}
                            )
                        )
                    )
                ],
                text=request_spec.requested_product.get("name")
                if request_spec.requested_product
                else (request_spec.medication or {}).get("display"),
            ),
            subject=self._reference(self._patient(request.patient)),
            requester=self._reference(self._practitioner(request.created_by)),
        )

    @cache_profiles(MedicationStatement.get_resource_type())
    def _medication_statement(self, statement: MedicationStatementModel):
        statement_spec = MedicationStatementReadSpec.serialize(statement)
        id = str(statement_spec.id)

        medication_statement_status_code_map = {
            MedicationStatementStatus.active: "active",
            MedicationStatementStatus.completed: "completed",
            MedicationStatementStatus.entered_in_error: "entered-in-error",
            MedicationStatementStatus.intended: "intended",
            MedicationStatementStatus.stopped: "stopped",
            MedicationStatementStatus.on_hold: "on-hold",
            MedicationStatementStatus.unknown: "unknown",
            MedicationStatementStatus.not_taken: "not-taken",
        }

        med = statement_spec.medication
        if isinstance(med, dict):
            med_stmt_name = med.get("display") or med.get("code") or "Medication Statement"
        else:
            med_stmt_name = getattr(med, "display", None) or getattr(med, "code", None) or "Medication Statement"

        ep = statement_spec.effective_period
        ep_start = ep.get("start") if isinstance(ep, dict) else getattr(ep, "start", None)
        ep_end = ep.get("end") if isinstance(ep, dict) else getattr(ep, "end", None)

        med_stmt_div_parts = [f"<p><b>Medication:</b> {med_stmt_name}</p>"]
        med_stmt_div_parts.append(f"<p><b>Status:</b> {statement_spec.status}</p>")
        if statement_spec.dosage_text:
            med_stmt_div_parts.append(f"<p><b>Dosage:</b> {statement_spec.dosage_text}</p>")
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
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(med_stmt_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=medication_statement_status_code_map.get(
                statement_spec.status, "unknown"
            ),
            medicationCodeableConcept=self._coding_to_codable_concept(
                statement_spec.medication
            ),
            dosage=[
                Dosage(
                    text=statement_spec.dosage_text,
                )
            ]
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

    @cache_profiles(DocumentReference.get_resource_type())
    def _document_reference(self, file: FileUploadModel):
        id = str(file.external_id)
        content_type, content = file.files_manager.file_contents(file)

        doc_ref_div_parts = [f"<p><b>Document:</b> {file.name or file.internal_name}</p>"]
        doc_ref_div_parts.append("<p><b>Status:</b> current</p>")
        if file.file_type:
            doc_ref_div_parts.append(f"<p><b>Type:</b> {file.file_type}</p>")
        if file.file_category:
            doc_ref_div_parts.append(f"<p><b>Category:</b> {file.file_category}</p>")

        return DocumentReference(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentReference"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(doc_ref_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status="current",
            type=CodeableConcept(text=file.internal_name.split(".")[0]),
            content=[
                DocumentReferenceContent(
                    attachment=Attachment(
                        contentType=content_type, data=base64.b64encode(content)
                    )
                )
            ],
            author=[self._reference(self._practitioner(file.created_by))],
        )

    @cache_profiles(AllergyIntolerance.get_resource_type())
    def _allergy_intolerance(self, allergy: AllergyIntoleranceModel):
        id = str(allergy.external_id)
        allergy_spec = AllergyIntoleranceReadSpec.serialize(allergy)

        allergy_intolerance_clinical_status_code_map = {
            AllergyIntoleranceClinicalStatusChoices.active: (
                "active",
                "Active",
            ),
            AllergyIntoleranceClinicalStatusChoices.inactive: (
                "inactive",
                "Inactive",
            ),
            AllergyIntoleranceClinicalStatusChoices.resolved: (
                "resolved",
                "Resolved",
            ),
        }

        allergy_intolerance_verification_status_code_map = {
            AllergyIntoleranceVerificationStatusChoices.unconfirmed: (
                "unconfirmed",
                "Unconfirmed",
            ),
            AllergyIntoleranceVerificationStatusChoices.confirmed: (
                "confirmed",
                "Confirmed",
            ),
            AllergyIntoleranceVerificationStatusChoices.refuted: (
                "refuted",
                "Refuted",
            ),
            AllergyIntoleranceVerificationStatusChoices.entered_in_error: (
                "entered-in-error",
                "Entered in Error",
            ),
        }

        allergy_intolerance_category_code_map = {
            AllergyIntoleranceCategoryChoices.food: "food",
            AllergyIntoleranceCategoryChoices.medication: "medication",
            AllergyIntoleranceCategoryChoices.environment: "environment",
            AllergyIntoleranceCategoryChoices.biologic: "biologic",
        }

        allergy_intolerance_criticality_code_map = {
            AllergyIntoleranceCriticalityChoices.low: "low",
            AllergyIntoleranceCriticalityChoices.high: "high",
            AllergyIntoleranceCriticalityChoices.unable_to_assess: "unable-to-assess",
        }

        allergy_code_display = allergy_spec.code.get("display") or allergy_spec.code.get("code", "")
        allergy_div_parts = [f"<p><b>Allergen:</b> {allergy_code_display}</p>"]
        if allergy_spec.allergy_intolerance_type:
            allergy_div_parts.append(f"<p><b>Type:</b> {allergy_spec.allergy_intolerance_type}</p>")
        if allergy_spec.category:
            allergy_div_parts.append(f"<p><b>Category:</b> {allergy_spec.category}</p>")
        if allergy_spec.criticality:
            allergy_div_parts.append(f"<p><b>Criticality:</b> {allergy_spec.criticality}</p>")
        if allergy_spec.clinical_status:
            allergy_div_parts.append(f"<p><b>Clinical Status:</b> {allergy_spec.clinical_status}</p>")
        if allergy_spec.verification_status:
            allergy_div_parts.append(f"<p><b>Verification Status:</b> {allergy_spec.verification_status}</p>")
        if allergy_spec.recorded_date:
            allergy_div_parts.append(f"<p><b>Recorded Date:</b> {allergy_spec.recorded_date.date()}</p>")
        if allergy_spec.last_occurrence:
            allergy_div_parts.append(f"<p><b>Last Occurrence:</b> {allergy_spec.last_occurrence.date()}</p>")
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
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(allergy_div_parts) + "</div>",
            ),
            identifier=[Identifier(value=id)],
            verificationStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                mapping=allergy_intolerance_verification_status_code_map,
                key=allergy_spec.verification_status,
                default=AllergyIntoleranceVerificationStatusChoices.unconfirmed.value,
            ),
            clinicalStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                mapping=allergy_intolerance_clinical_status_code_map,
                key=allergy_spec.clinical_status,
                default=AllergyIntoleranceClinicalStatusChoices.active.value,
            ),
            category=[allergy_intolerance_category_code_map.get(allergy_spec.category)]
            if allergy_spec.category
            else None,
            criticality=allergy_intolerance_criticality_code_map.get(
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
            onsetDateTime=allergy_spec.onset.onset_datetime.isoformat()
            if allergy_spec.onset.get("onset_datetime")
            else None,
            onsetAge=allergy_spec.onset.onset_age
            if allergy_spec.onset.get("onset_age")
            else None,
            onsetString=allergy_spec.onset.onset_string
            if allergy_spec.onset.get("onset_string")
            else None,
            patient=self._reference(self._patient(allergy.patient)),
            encounter=self._reference(self._encounter(allergy.encounter)),
            recorder=self._reference(self._practitioner(allergy.created_by)),
            note=[Annotation(text=allergy_spec.note)] if allergy_spec.note else None,
        )

    @cache_profiles(Observation.get_resource_type())
    def _observation(self, observation: ObservationModel):
        id = str(observation.external_id)
        observation_spec = ObservationReadSpec.serialize(observation)

        obs_code_display = (
            observation_spec.main_code.get("display") or observation_spec.main_code.get("code")
            if observation_spec.main_code
            else "Observation"
        )
        obs_div_parts = [f"<p><b>Observation:</b> {obs_code_display}</p>"]
        obs_div_parts.append(f"<p><b>Status:</b> {observation_spec.status}</p>")
        obs_div_parts.append(f"<p><b>Effective Date:</b> {observation_spec.effective_datetime.date()}</p>")

        obs_value = observation_spec.value
        if isinstance(obs_value, dict):
            raw_value = obs_value.get("value")
            unit_info = obs_value.get("unit", {})
            unit_display = unit_info.get("display") if isinstance(unit_info, dict) else None
            coding_info = obs_value.get("coding")
            if raw_value is not None and unit_display:
                obs_div_parts.append(f"<p><b>Value:</b> {raw_value} {unit_display}</p>")
            elif raw_value is not None:
                obs_div_parts.append(f"<p><b>Value:</b> {raw_value}</p>")
            elif coding_info:
                coding_display = coding_info.get("display") or coding_info.get("code", "")
                obs_div_parts.append(f"<p><b>Value:</b> {coding_display}</p>")

        obs_interpretation = observation_spec.interpretation
        if obs_interpretation:
            interp_text = obs_interpretation if isinstance(obs_interpretation, str) else obs_interpretation.get("text") or obs_interpretation.get("code")
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
                div='<div xmlns="http://www.w3.org/1999/xhtml">' + "".join(obs_div_parts) + "</div>",
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
            and not observation_spec.value.get("unit")
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
                ObservationReferenceRange(
                    low=Quantity(
                        value=rrange.min,
                    )
                    if rrange.min
                    else None,
                    high=Quantity(
                        value=rrange.max,
                    )
                    if rrange.max
                    else None,
                )
                for rrange in observation_spec.reference_range
            ],
            encounter=self._reference(self._encounter(observation.encounter))
            if observation.encounter
            else None,
            note=[Annotation(text=observation_spec.note)]
            if observation_spec.note
            else None,
            interpretation=CodeableConcept(
                text=observation_spec.interpretation,
            )
            if observation_spec.interpretation
            else None,
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
                    and not component.get("value", {}).get("unit")
                    and not component.get("value", {}).get("coding")
                    else None,
                    valueCodeableConcept=CodeableConcept(
                        coding=[Coding(**component.get("value", {}).get("coding"))],
                        text=component.get("value", {}).get("coding", {}).get("display"),
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
                        ObservationReferenceRange(
                            low=Quantity(
                                value=rrange.get("min"),
                            )
                            if rrange.get("min")
                            else None,
                            high=Quantity(
                                value=rrange.get("max"),
                            )
                            if rrange.get("max")
                            else None,
                        )
                        for rrange in component.get("reference_range", [])
                    ],
                )
                for component in observation_spec.component
            ],
        )

    def _prescription_composition(
        self, requests: list[MedicationRequestModel], care_context_id: str
    ):
        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/PrescriptionRecord"
                ],
            ),
            identifier=Identifier(
                value=care_context_id, system=f"{CARE_IDENTIFIER_SYSTEM}/composition"
            ),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://snomed.info/sct",
                        code="440545006",
                        display="Prescription record",
                    )
                ],
                text="Prescription record",
            ),
            title="Prescription Records",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title="Prescription record",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="https://projecteka.in/sct",
                                code="440545006",
                                display="Prescription record",
                            )
                        ],
                        text="Prescription record",
                    ),
                    entry=[
                        self._reference(self._medication_request(request))
                        for request in requests
                    ],
                )
            ],
            subject=self._reference(self._patient(requests[0].patient)),
            encounter=self._reference(self._encounter(requests[0].encounter)),
            author=[
                self._reference(self._organization(requests[0].encounter.facility))
            ],
        )

    def _op_consult_composition(self, encounter: EncounterModel, care_context_id: str):
        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/OPConsultRecord"
                ],
            ),
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://snomed.info/sct",
                        code="371530004",
                        display="Clinical consultation report",
                    )
                ],
                text="Clinical consultation report",
            ),
            title="Consultation Report",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title="Chief Complaints",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="422843007",
                                display="Chief complaint section",
                            )
                        ],
                        text="Chief complaint section",
                    ),
                    entry=[
                        self._reference(self._condition(condition))
                        for condition in ConditionModel.objects.filter(
                            encounter=encounter
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if ConditionModel.objects.filter(encounter=encounter).count() == 0
                    else None,
                ),
                CompositionSection(
                    title="Physical Examination",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="425044008",
                                display="Physical exam section",
                            )
                        ],
                        text="Physical exam section",
                    ),
                    entry=[
                        self._reference(self._observation(observation))
                        for observation in ObservationModel.objects.filter(
                            encounter=encounter
                        ).exclude(Q(main_code__isnull=True) | Q(main_code={}))
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if ObservationModel.objects.filter(encounter=encounter)
                    .exclude(Q(main_code__isnull=True) | Q(main_code={}))
                    .count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Allergies",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="722446000",
                                display="Allergy record",
                            )
                        ],
                        text="Allergy record",
                    ),
                    entry=[
                        self._reference(self._allergy_intolerance(allergy))
                        for allergy in AllergyIntoleranceModel.objects.filter(
                            encounter=encounter
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if AllergyIntoleranceModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Medications",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="721912009",
                                display="Medication summary document",
                            )
                        ],
                        text="Medication summary document",
                    ),
                    entry=[
                        *[
                            self._reference(self._medication_request(request))
                            for request in MedicationRequestModel.objects.filter(
                                encounter=encounter
                            )
                        ],
                        *[
                            self._reference(self._medication_statement(statement))
                            for statement in MedicationStatementModel.objects.filter(
                                encounter=encounter
                            )
                        ],
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if MedicationRequestModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    and MedicationStatementModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Document Reference",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="371530004",
                                display="Clinical consultation report",
                            )
                        ],
                        text="Clinical consultation report",
                    ),
                    entry=[
                        self._reference(self._document_reference(file))
                        for file in FileUploadModel.objects.filter(
                            associating_id=encounter.external_id
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if FileUploadModel.objects.filter(
                        associating_id=encounter.external_id
                    ).count()
                    == 0
                    else None,
                ),
            ],
            subject=self._reference(self._patient(encounter.patient)),
            encounter=self._reference(
                self._encounter(encounter, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(encounter.facility))],
        )

    def _discharge_summary_composition(
        self, encounter: EncounterModel, care_context_id: str
    ):
        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DischargeSummaryRecord"
                ],
            ),
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://snomed.info/sct",
                        code="373942005",
                        display="Discharge summary",
                    )
                ],
                text="Discharge summary",
            ),
            title="Discharge Summary",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title="Chief Complaints",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="422843007",
                                display="Chief complaint section",
                            )
                        ],
                        text="Chief complaint section",
                    ),
                    entry=[
                        self._reference(self._condition(condition))
                        for condition in ConditionModel.objects.filter(
                            encounter=encounter
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if ConditionModel.objects.filter(encounter=encounter).count() == 0
                    else None,
                ),
                CompositionSection(
                    title="Physical Examination",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="425044008",
                                display="Physical exam section",
                            )
                        ],
                        text="Physical exam section",
                    ),
                    entry=[
                        self._reference(self._observation(observation))
                        for observation in ObservationModel.objects.filter(
                            encounter=encounter
                        ).exclude(Q(main_code__isnull=True) | Q(main_code={}))
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if ObservationModel.objects.filter(encounter=encounter)
                    .exclude(Q(main_code__isnull=True) | Q(main_code={}))
                    .count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Allergies",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="722446000",
                                display="Allergy record",
                            )
                        ],
                        text="Allergy record",
                    ),
                    entry=[
                        self._reference(self._allergy_intolerance(allergy))
                        for allergy in AllergyIntoleranceModel.objects.filter(
                            encounter=encounter
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if AllergyIntoleranceModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Medications",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="721912009",
                                display="Medication summary document",
                            )
                        ],
                        text="Medication summary document",
                    ),
                    entry=[
                        *[
                            self._reference(self._medication_request(request))
                            for request in MedicationRequestModel.objects.filter(
                                encounter=encounter
                            )
                        ],
                        *[
                            self._reference(self._medication_statement(statement))
                            for statement in MedicationStatementModel.objects.filter(
                                encounter=encounter
                            )
                        ],
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if MedicationRequestModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    and MedicationStatementModel.objects.filter(
                        encounter=encounter
                    ).count()
                    == 0
                    else None,
                ),
                CompositionSection(
                    title="Document Reference",
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://snomed.info/sct",
                                code="371530004",
                                display="Clinical consultation report",
                            )
                        ],
                        text="Clinical consultation report",
                    ),
                    entry=[
                        self._reference(self._document_reference(file))
                        for file in FileUploadModel.objects.filter(
                            associating_id=encounter.external_id
                        )
                    ],
                    emptyReason=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                code="notstarted",
                                display="Not Started",
                            )
                        ],
                        text="Not Started",
                    )
                    if FileUploadModel.objects.filter(
                        associating_id=encounter.external_id
                    ).count()
                    == 0
                    else None,
                ),
            ],
            subject=self._reference(self._patient(encounter.patient)),
            encounter=self._reference(
                self._encounter(encounter, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(encounter.facility))],
        )

    def _health_document_composition(self, file: FileUploadModel, care_context_id: str):
        if file.file_type not in (FileTypeChoices.patient, FileTypeChoices.encounter):
            raise ABDMAPIException(
                "File type must be either patient or encounter to create health document composition"
            )

        patient = PatientModel.objects.filter(
            Q(external_id=file.associating_id)
            | Q(encounter__external_id=file.associating_id)
        ).first()

        if not patient:
            raise ABDMAPIException(
                "Patient not found for the given file associating_id"
            )

        encounter = EncounterModel.objects.filter(
            external_id=file.associating_id
        ).first()

        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/HealthDocumentRecord"
                ],
            ),
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://snomed.info/sct",
                        code="419891008",
                        display="Record artifact",
                    )
                ],
                text="Record artifact",
            ),
            title="Health Document",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title=file.name,
                    entry=[self._reference(self._document_reference(file))],
                ),
            ],
            subject=self._reference(self._patient(patient)),
            encounter=self._reference(
                self._encounter(encounter, include_diagnosis=True)
            )
            if encounter
            else None,
            author=[self._reference(self._practitioner(file.created_by))],
        )

    def _wellness_composition(
        self, questionnaire_response: QuestionnaireResponseModel, care_context_id: str
    ):
        observations = ObservationModel.objects.filter(
            questionnaire_response=questionnaire_response,
        ).filter(
            Q(main_code__isnull=False) & ~Q(main_code={})
            | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
        )

        if not observations:
            raise ABDMAPIException(
                "No observations found for the given questionnaire response"
            )

        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/WellnessRecord"
                ],
            ),
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(text="Wellness Record"),
            title="Wellness Record",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title="Other Observations",
                    entry=[
                        self._reference(self._observation(observation))
                        for observation in observations
                    ],
                ),
            ],
            subject=self._reference(self._patient(questionnaire_response.patient)),
            encounter=self._reference(
                self._encounter(
                    questionnaire_response.encounter, include_diagnosis=True
                )
            )
            if questionnaire_response.encounter
            else None,
            author=[
                self._reference(self._practitioner(questionnaire_response.created_by))
            ],
        )

    def _bundle(self, entries: list[BundleEntry], care_context_id: str = uuid()):
        return Bundle(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentBundle"
                ],
                security=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                        code="V",
                        display="very restricted",
                    )
                ],
            ),
            identifier=Identifier(
                value=care_context_id, system=f"{CARE_IDENTIFIER_SYSTEM}/bundle"
            ),
            type="document",
            timestamp=datetime.now(UTC).isoformat(),
            entry=entries,
        )

    def _bundle_entry(self, resource: Resource):
        return BundleEntry(fullUrl=self._reference_url(resource), resource=resource)

    def create_prescription_record(
        self,
        prescriptions: list[MedicationRequestModel],
        care_context_id: str = uuid(),
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._prescription_composition(prescriptions, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )

    def create_op_consult_record(
        self, encounter: EncounterModel, care_context_id: str = uuid()
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._op_consult_composition(encounter, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )

    def create_discharge_summary_record(
        self, encounter: EncounterModel, care_context_id: str = uuid()
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._discharge_summary_composition(encounter, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )

    def create_health_document_record(
        self, file: FileUploadModel, care_context_id: str = uuid()
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._health_document_composition(file, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )

    def create_wellness_record(
        self,
        questionnaire_response: QuestionnaireResponseModel,
        care_context_id: str = uuid(),
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._wellness_composition(questionnaire_response, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )
