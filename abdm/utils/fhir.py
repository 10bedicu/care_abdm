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
from fhir.resources.R4B.encounter import Encounter, EncounterDiagnosis
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation
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
from abdm.service.helper import uuid
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
from care.emr.resources.allergy_intolerance.spec import AllergyIntoleranceReadSpec
from care.emr.resources.base import Coding as CodingSpec
from care.emr.resources.condition.spec import ConditionReadSpec
from care.emr.resources.encounter.spec import EncounterRetrieveSpec
from care.emr.resources.facility.spec import FacilityRetrieveSpec
from care.emr.resources.medication.request.spec import (
    DosageInstruction as DosageInstructionSpec,
)
from care.emr.resources.medication.request.spec import MedicationRequestReadSpec
from care.emr.resources.medication.statement.spec import MedicationStatementReadSpec
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

    @cache_profiles(Patient.get_resource_type())
    def _patient(self, patient: PatientModel):
        patient_spec = PatientRetrieveSpec.serialize(patient)
        id = str(patient_spec.id)

        return Patient(
            id=id,
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
            birthDate=patient.abha_number.date_of_birth,
            address=[
                Address(
                    line=[patient_spec.address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                ),
                Address(
                    line=[patient_spec.permanent_address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                ),
            ],
        )

    @cache_profiles(Practitioner.get_resource_type())
    def _practitioner(self, user: UserModel):
        user_spec = UserRetrieveSpec.serialize(user)
        id = str(user_spec.id)

        return Practitioner(
            id=id,
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
                        ]
                    ),
                )
            ],
            name=[HumanName(text=user.full_name)],
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
        id = health_facility.hf_id if health_facility else str(facility_spec.id)

        return Organization(
            id=id,
            identifier=[
                Identifier(
                    system=(
                        "https://facility.ndhm.gov.in"
                        if health_facility
                        else f"{CARE_IDENTIFIER_SYSTEM}/facility"
                    ),
                    value=id,
                    type=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0203",
                                code="FI",
                                display="Facility ID",
                            )
                        ]
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
                    ]
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

        return Condition(
            id=id,
            identifier=[Identifier(value=id)],
            category=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/condition-category",
                            code=condition_spec.category,
                        )
                    ],
                )
            ],
            verificationStatus=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/condition-ver-status",
                        code=condition_spec.verification_status,
                    )
                ]
            ),
            code=CodeableConcept(
                coding=[Coding(**condition_spec.code)],
            ),
            subject=self._reference(self._patient(condition.patient)),
        )

    @cache_profiles(Encounter.get_resource_type())
    def _encounter(self, encounter: EncounterModel, include_diagnosis: bool = False):
        encounter_spec = EncounterRetrieveSpec.serialize(encounter)
        id = str(encounter_spec.id)

        return Encounter(
            **{
                "id": id,
                "identifier": [Identifier(value=id)],
                "status": encounter_spec.status,
                "class": Coding(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    code=encounter_spec.encounter_class,
                ),
                "subject": self._reference(self._patient(encounter.patient)),
                "priority": CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/v3-ActPriority",
                            code=encounter_spec.priority,
                        )
                    ]
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
            }
        )

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

        return CodeableConcept(coding=[self._coding(coding)])

    @cache_profiles(MedicationRequest.get_resource_type())
    def _medication_request(self, request: MedicationRequestModel):
        request_spec = MedicationRequestReadSpec.serialize(request)
        id = str(request_spec.id)

        return MedicationRequest(
            id=id,
            identifier=[Identifier(value=id)],
            status=request_spec.status,
            intent=request_spec.intent,
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
                            type=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://terminology.hl7.org/CodeSystem/dose-rate-type",
                                        code=dosage_spec.dose_and_rate.type,
                                    )
                                ]
                            ),
                            doseRange=Range(
                                low=dosage_spec.dose_and_rate.dose_range.low,
                                high=dosage_spec.dose_and_rate.dose_range.high,
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
                coding=[Coding(**request_spec.medication)],
            ),
            subject=self._reference(self._patient(request.patient)),
            requester=self._reference(self._practitioner(request.created_by)),
        )

    @cache_profiles(MedicationStatement.get_resource_type())
    def _medication_statement(self, statement: MedicationStatementModel):
        statement_spec = MedicationStatementReadSpec.serialize(statement)
        id = str(statement_spec.id)

        return MedicationStatement(
            id=id,
            identifier=[Identifier(value=id)],
            status=statement_spec.status,
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

        return DocumentReference(
            id=id,
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

        return AllergyIntolerance(
            id=id,
            identifier=[Identifier(value=id)],
            verificationStatus=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                        code=allergy_spec.verification_status,
                    )
                ]
            ),
            clinicalStatus=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        code=allergy_spec.clinical_status,
                    )
                ]
            ),
            category=[allergy_spec.category] if allergy_spec.category else None,
            criticality=allergy_spec.criticality,
            code=CodeableConcept(
                coding=[Coding(**allergy_spec.code)],
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

        return Observation(
            id=id,
            identifier=[Identifier(value=id)],
            status=observation_spec.status,
            category=[CodeableConcept(coding=[Coding(**observation_spec.category)])]
            if observation_spec.category
            else None,
            code=CodeableConcept(coding=[Coding(**observation_spec.main_code)])
            if observation_spec.main_code
            else CodeableConcept(**observation_spec.alternate_coding),
            valueString=observation_spec.value.get("value")
            if observation_spec.value.get("value")
            else None,
            valueCodeableConcept=CodeableConcept(
                coding=[Coding(**observation_spec.value.get("value_code"))]
            )
            if observation_spec.value.get("value_code")
            else None,
            valueQuantity=Quantity(
                value=observation_spec.value.get("value_quantity", {}).get("value"),
                unit=observation_spec.value.get("value_quantity", {})
                .get("unit", {})
                .get("display"),
                system=observation_spec.value.get("value_quantity", {})
                .get("unit", {})
                .get("system"),
                code=observation_spec.value.get("value_quantity", {})
                .get("unit", {})
                .get("code"),
            )
            if observation_spec.value.get("value_quantity")
            else None,
            effectiveDateTime=observation_spec.effective_datetime.isoformat(),
            method=CodeableConcept(coding=[Coding(**observation_spec.method)])
            if observation_spec.method
            else None,
            bodySite=CodeableConcept(coding=[Coding(**observation_spec.body_site)])
            if observation_spec.body_site
            else None,
            referenceRange=[
                Range(**rrange) for rrange in observation_spec.reference_range
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
        )

    def _prescription_composition(
        self, requests: list[MedicationRequestModel], care_context_id: str
    ):
        return Composition(
            id=care_context_id,
            identifier=Identifier(
                value=care_context_id, system=f"{CARE_IDENTIFIER_SYSTEM}/composition"
            ),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="https://projecteka.in/sct",
                        code="440545006",
                        display="Prescription record",
                    )
                ]
            ),
            title="Prescription",
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
                        ]
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
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="https://projecteka.in/sct",
                        code="371530004",
                        display="Clinical consultation report",
                    )
                ]
            ),
            title="Medications",
            date=datetime.now(UTC).isoformat(),
            section=list(
                filter(
                    lambda section: section.entry and len(section.entry) > 0,
                    [
                        CompositionSection(
                            title="Chief Complaints",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="422843007",
                                        display="Chief complaint section",
                                    )
                                ]
                            ),
                            entry=[
                                self._reference(self._condition(condition))
                                for condition in ConditionModel.objects.filter(
                                    encounter=encounter
                                )
                            ],
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
                                ]
                            ),
                            entry=[
                                self._reference(self._observation(observation))
                                for observation in ObservationModel.objects.filter(
                                    encounter=encounter
                                ).exclude(Q(main_code__isnull=True) | Q(main_code={}))
                            ],
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
                                ]
                            ),
                            entry=[
                                self._reference(self._allergy_intolerance(allergy))
                                for allergy in AllergyIntoleranceModel.objects.filter(
                                    encounter=encounter
                                )
                            ],
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
                                ]
                            ),
                            entry=[
                                *[
                                    self._reference(self._medication_request(request))
                                    for request in MedicationRequestModel.objects.filter(
                                        encounter=encounter
                                    )
                                ],
                                *[
                                    self._reference(
                                        self._medication_statement(statement)
                                    )
                                    for statement in MedicationStatementModel.objects.filter(
                                        encounter=encounter
                                    )
                                ],
                            ],
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
                                ]
                            ),
                            entry=[
                                self._reference(self._document_reference(file))
                                for file in FileUploadModel.objects.filter(
                                    associating_id=encounter.external_id
                                )
                            ],
                        ),
                    ],
                )
            ),
            subject=self._reference(self._patient(encounter.patient)),
            encounter=self._reference(
                self._encounter(encounter, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(encounter.facility))],
        )

    def _bundle_entry(self, resource: Resource):
        return BundleEntry(fullUrl=self._reference_url(resource), resource=resource)

    def create_prescription_record(
        self,
        prescriptions: list[MedicationRequestModel],
        care_context_id: str = uuid(),
    ):
        return Bundle(
            id=care_context_id,
            identifier=Identifier(
                value=care_context_id, system=f"{CARE_IDENTIFIER_SYSTEM}/bundle"
            ),
            type="document",
            timestamp=datetime.now(UTC).isoformat(),
            entry=[
                self._bundle_entry(
                    self._prescription_composition(prescriptions, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
        )

    def create_op_consult_record(
        self, encounter: EncounterModel, care_context_id: str = uuid()
    ):
        return Bundle(
            id=care_context_id,
            identifier=Identifier(
                value=care_context_id, system=f"{CARE_IDENTIFIER_SYSTEM}/bundle"
            ),
            type="document",
            timestamp=datetime.now(UTC).isoformat(),
            entry=[
                self._bundle_entry(
                    self._op_consult_composition(encounter, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
        )
