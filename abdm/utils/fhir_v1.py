import base64
from datetime import UTC, datetime
from functools import wraps
from typing import Literal, TypedDict

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.careplan import CarePlan
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
)
from fhir.resources.R4B.dosage import Dosage
from fhir.resources.R4B.encounter import Encounter, EncounterDiagnosis
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medication import Medication
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.observation import Observation, ObservationComponent
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from abdm.models import HealthFacility
from abdm.service.helper import uuid  # TODO: stop using random uuid
from abdm.settings import plugin_settings as settings
from care.facility.models import (
    BaseModel,
    ConditionVerificationStatus,
    ConsultationDiagnosis,
    DailyRound,
    Facility,
    FileUpload,
    FrequencyEnum,
    InvestigationSession,
    InvestigationValue,
    MedibaseMedicine,
    PatientConsultation,
    PatientRegistration,
    Prescription,
)
from care.users.models import User

care_identifier = settings.BACKEND_DOMAIN


class Fhir:
    def __init__(self):
        self._profiles = {}
        self._resource_id_url_map = {}

    @staticmethod
    def cache_profiles(resource_type: str):
        def decorator(func):
            @wraps(func)
            def wrapper(self, model_instance: BaseModel, *args, **kwargs):
                if not hasattr(model_instance, "external_id"):
                    raise AttributeError(
                        f"{model_instance.__class__.__name__} does not have 'external_id' attribute"
                    )

                cache_key_prefix = (
                    kwargs["cache_key_prefix"] if "cache_key_prefix" in kwargs else ""
                )
                cache_key_id = str(model_instance.external_id)
                cache_key_suffix = (
                    kwargs["cache_key_suffix"] if "cache_key_suffix" in kwargs else ""
                )
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
    def _patient(self, patient: PatientRegistration):
        id = str(patient.external_id)
        name = patient.name
        gender = patient.gender
        dob = patient.abha_number.parsed_date_of_birth

        return Patient(
            id=id,
            identifier=[Identifier(value=id)],
            name=[HumanName(text=name)],
            gender="male" if gender == 1 else "female" if gender == 2 else "other",
            birthDate=dob,
            managingOrganization=self._reference(self._organization(patient.facility)),
        )

    @cache_profiles(Practitioner.get_resource_type())
    def _practitioner(self, user: User):
        id = str(user.external_id)
        name = f"{user.first_name} {user.last_name}"

        return Practitioner(
            id=id,
            identifier=[Identifier(value=id)],
            name=[HumanName(text=name)],
        )

    @cache_profiles(Organization.get_resource_type())
    def _organization(self, facility: Facility):
        id = str(facility.external_id)
        health_facility = HealthFacility.objects.filter(facility=facility).first()
        name = facility.name
        phone = facility.phone_number
        address = facility.address
        local_body = facility.local_body.name
        district = facility.district.name
        state = facility.state.name
        pincode = facility.pincode

        return Organization(
            id=id,
            identifier=[
                Identifier(
                    system=(
                        "https://facility.ndhm.gov.in"
                        if health_facility
                        else f"{care_identifier}/facility"
                    ),
                    value=health_facility.hf_id if health_facility else id,
                )
            ],
            name=name,
            telecom=[ContactPoint(system="phone", value=phone)],
            address=[
                Address(
                    line=[address, local_body],
                    district=district,
                    state=state,
                    postalCode=pincode,
                    country="INDIA",
                )
            ],
        )

    @cache_profiles(Condition.get_resource_type())
    def _condition(self, diagnosis: ConsultationDiagnosis):
        id = str(diagnosis.external_id)
        verification_status = ConditionVerificationStatus(diagnosis.verification_status)
        label = diagnosis.diagnosis.label
        code = diagnosis.diagnosis.icd11_id

        return Condition(
            id=id,
            identifier=[Identifier(value=id)],
            category=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/condition-category",
                            code="encounter-diagnosis",
                            display="Encounter Diagnosis",
                        )
                    ],
                    text="Encounter Diagnosis",
                )
            ],
            verificationStatus=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/condition-ver-status",
                        code=verification_status.value,
                        display=verification_status.label.title(),
                    )
                ]
            ),
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://id.who.int/icd/release/11/mms",
                        code=code,
                        display=label,
                    )
                ],
                text=label,
            ),
            subject=self._reference(self._patient(diagnosis.consultation.patient)),
        )

    @cache_profiles(Encounter.get_resource_type())
    def _encounter(
        self, consultation: PatientConsultation, include_diagnosis: bool = False
    ):
        id = str(consultation.external_id)
        status = "finished" if consultation.discharge_date else "in-progress"
        period_start = consultation.encounter_date.isoformat()
        period_end = (
            consultation.discharge_date.isoformat()
            if consultation.discharge_date
            else None
        )

        return Encounter(
            **{
                "id": id,
                "identifier": [Identifier(value=id)],
                "status": status,
                "class": Coding(
                    system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    code="IMP",  # TODO: "AMB" for ambulatory / outpatient
                    display="Inpatient Encounter",
                ),
                "subject": self._reference(self._patient(consultation.patient)),
                "period": Period(start=period_start, end=period_end),
                "diagnosis": (
                    list(
                        map(
                            lambda consultation_diagnosis: EncounterDiagnosis(
                                condition=self._reference(
                                    self._condition(consultation_diagnosis)
                                )
                            ),
                            consultation.diagnoses.all(),  # type: ignore
                        )
                    )
                    if include_diagnosis
                    else None
                ),
            }
        )

    @cache_profiles(Observation.get_resource_type())
    def _observation(
        self,
        model: DailyRound | InvestigationValue,
        title: str | dict,
        value: str | list | dict | None,
        date: datetime,
        category: (
            Literal[
                "social-history",
                "vital-signs",
                "imaging",
                "laboratory",
                "procedure",
                "survey",
                "therapy",
                "activity",
            ]
            | None
        ) = None,
        cache_key_suffix: str = "",
    ):
        if isinstance(value, list):
            value = list(
                filter(
                    lambda x: (isinstance(x["value"], dict) and x["value"].get("value"))
                    or (isinstance(x["value"], str) and x),
                    value,
                )
            )

        if (
            not value
            or (isinstance(value, list) and not value)
            or (isinstance(value, dict) and not value.get("value"))
            or (isinstance(value, str) and not value)
        ):
            return None

        category_code_display_map = {
            "social-history": "Social History",
            "vital-signs": "Vital Signs",
            "imaging": "Imaging",
            "laboratory": "Laboratory",
            "procedure": "Procedure",
            "survey": "Survey",
            "therapy": "Therapy",
            "activity": "Activity",
        }

        id = f"{model.external_id!s}{cache_key_suffix}"

        return Observation(
            id=id,
            identifier=[Identifier(value=id)],
            status="final",
            effectiveDateTime=date,
            code=CodeableConcept(
                coding=[Coding(**title)] if isinstance(title, dict) else None,
                text=title.get("display") if isinstance(title, dict) else title,
            ),
            category=(
                [
                    CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/observation-category",
                                code=category,
                                display=category_code_display_map.get(category),
                            )
                        ],
                        text=category_code_display_map.get(category),
                    )
                ]
                if category
                else None
            ),
            valueQuantity=Quantity(**value) if isinstance(value, dict) else None,
            component=(
                list(
                    map(
                        lambda component: ObservationComponent(
                            code=CodeableConcept(
                                coding=(
                                    [Coding(**component["title"])]
                                    if isinstance(component["title"], dict)
                                    else None
                                ),
                                text=(
                                    component["title"].get("display")
                                    if isinstance(component["title"], dict)
                                    else component["title"]
                                ),
                            ),
                            valueQuantity=(
                                Quantity(**component["value"])
                                if isinstance(component["value"], dict)
                                else None
                            ),
                            valueString=(
                                str(component["value"])
                                if not (
                                    isinstance(component["value"], list)
                                    or isinstance(component["value"], dict)
                                )
                                else None
                            ),
                        ),
                        value,
                    )
                )
                if isinstance(value, list)
                else None
            ),
            valueString=(
                str(value)
                if not (isinstance(value, list) or isinstance(value, dict))
                else None
            ),
            subject=self._reference(self._patient(model.consultation.patient)),
        )

    def _observations_from_daily_round(
        self, daily_round: DailyRound, category: str = "all"
    ):
        date = daily_round.taken_at.isoformat()

        vital_signs = [
            {
                "title": {
                    "display": "Body surface temperature",
                    "system": "http://loinc.org",
                    "code": "61008-9",
                },
                "value": {
                    "value": daily_round.temperature,
                    "unit": "°F",
                    "system": "http://unitsofmeasure.org",
                    "code": "°F",
                },
                "category": "vital-signs",
                "cache_key_suffix": ".temperature",
            },
            {
                "title": {
                    "display": "Respiratory rate",
                    "system": "http://loinc.org",
                    "code": "9279-1",
                },
                "value": {
                    "value": daily_round.resp,
                    "unit": "breaths/min",
                    "system": "http://unitsofmeasure.org",
                    "code": "/min",
                },
                "category": "vital-signs",
                "cache_key_suffix": ".resp",
            },
            {
                "title": {
                    "display": "Heart rate",
                    "system": "http://loinc.org",
                    "code": "8867-4",
                },
                "value": {
                    "value": daily_round.pulse,
                    "unit": "beats/min",
                    "system": "http://unitsofmeasure.org",
                    "code": "/min",
                },
                "category": "vital-signs",
                "cache_key_suffix": ".pulse",
            },
            {
                "title": {
                    "display": "Oxygen saturation in Arterial blood",
                    "system": "http://loinc.org",
                    "code": "2708-6",
                },
                "value": {
                    "value": daily_round.ventilator_spo2,
                    "unit": "%",
                    "system": "http://unitsofmeasure.org",
                    "code": "%",
                },
                "category": "vital-signs",
                "cache_key_suffix": ".spo2",
            },
            {
                "title": {
                    "display": "Blood pressure panel with all children optional",
                    "system": "http://loinc.org",
                    "code": "85354-9",
                },
                "value": [
                    {
                        "title": {
                            "system": "http://loinc.org",
                            "code": "8480-6",
                            "display": "Systolic blood pressure",
                        },
                        "value": {
                            "value": daily_round.bp.get("systolic"),
                            "unit": "mm[Hg]",
                            "system": "http://unitsofmeasure.org",
                            "code": "mm[Hg]",
                        },
                    },
                    {
                        "title": {
                            "system": "http://loinc.org",
                            "code": "8462-4",
                            "display": "Diastolic blood pressure",
                        },
                        "value": {
                            "value": daily_round.bp.get("diastolic"),
                            "unit": "mm[Hg]",
                            "system": "http://unitsofmeasure.org",
                            "code": "mm[Hg]",
                        },
                    },
                ],
                "category": "vital-signs",
                "cache_key_suffix": ".bp",
            },
            {
                "title": "Ventilator readings",
                "value": [
                    {
                        "title": "Mode",
                        "value": DailyRound.VentilatorModeType(
                            daily_round.ventilator_mode
                            or DailyRound.VentilatorModeType.UNKNOWN
                        )
                        .name.replace("_", " ")
                        .capitalize(),
                    },
                    {
                        "title": "Interface",
                        "value": DailyRound.VentilatorInterfaceType(
                            daily_round.ventilator_interface
                            or DailyRound.VentilatorInterfaceType.UNKNOWN
                        )
                        .name.replace("_", " ")
                        .capitalize(),
                    },
                    {
                        "title": "PEEP (Positive End-Expiratory Pressure)",
                        "value": {
                            "value": daily_round.ventilator_peep,
                            "unit": "cmH2O",
                            "code": "cm[H2O]",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "PIP (Peak Inspiratory Pressure)",
                        "value": {
                            "value": daily_round.ventilator_pip,
                            "unit": "cmH2O",
                            "code": "cm[H2O]",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Mean Airway Pressure",
                        "value": {
                            "value": daily_round.ventilator_mean_airway_pressure,
                            "unit": "cmH2O",
                            "code": "cm[H2O]",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Respiratory Rate",
                        "value": {
                            "value": daily_round.ventilator_resp_rate,
                            "unit": "breaths/min",
                            "code": "/min",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Pressure Support",
                        "value": {
                            "value": daily_round.ventilator_pressure_support,
                            "unit": "cmH2O",
                            "code": "cm[H2O]",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Tidal Volume",
                        "value": {
                            "value": daily_round.ventilator_tidal_volume,
                            "unit": "mL",
                            "code": "mL",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Oxygen Modality",
                        "value": DailyRound.VentilatorOxygenModalityType(
                            daily_round.ventilator_oxygen_modality
                            or DailyRound.VentilatorOxygenModalityType.UNKNOWN
                        )
                        .name.replace("_", " ")
                        .capitalize(),
                    },
                    {
                        "title": "Oxygen Modality Oxygen Rate",
                        "value": {
                            "value": daily_round.ventilator_oxygen_modality_oxygen_rate,
                            "unit": "L/min",
                            "code": "L/min",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "Oxygen Modality Flow Rate",
                        "value": {
                            "value": daily_round.ventilator_oxygen_modality_flow_rate,
                            "unit": "L/min",
                            "code": "L/min",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "FiO2 (Fraction of Inspired Oxygen)",
                        "value": {
                            "value": daily_round.ventilator_fio2,
                            "unit": "%",
                            "code": "%",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                    {
                        "title": "SpO2 (Oxygen Saturation)",
                        "value": {
                            "value": daily_round.ventilator_spo2,
                            "unit": "%",
                            "code": "%",
                            "system": "http://unitsofmeasure.org",
                        },
                    },
                ],
                "category": "vital-signs",
                "cache_key_suffix": ".ventilator",
            },
        ]
        body_measurement = []
        physical_activity = []
        general_assessment = []
        women_health = []
        lifestyle = []
        others = []  # TODO: add remaining fields

        observations = []

        if category in ["vital_signs", "all"]:
            observations.extend(vital_signs)
        if category in ["body_measurement", "all"]:
            observations.extend(body_measurement)
        if category in ["physical_activity", "all"]:
            observations.extend(physical_activity)
        if category in ["general_assessment", "all"]:
            observations.extend(general_assessment)
        if category in ["women_health", "all"]:
            observations.extend(women_health)
        if category in ["lifestyle", "all"]:
            observations.extend(lifestyle)
        if category in ["others", "all"]:
            observations.extend(others)

        return list(
            filter(
                lambda profile: profile is not None,
                map(
                    lambda observation: self._observation(
                        daily_round, date=date, **observation
                    ),
                    observations,
                ),
            )
        )

    @cache_profiles(DiagnosticReport.get_resource_type())
    def _diagnostic_report(self, investigation_session: InvestigationSession):
        id = str(investigation_session.external_id)
        investigation_values = InvestigationValue.objects.filter(
            session=investigation_session
        )

        if not investigation_values.exists():
            return None

        return DiagnosticReport(
            id=id,
            status="final",
            code=CodeableConcept(text="Investigation/Test Results"),
            result=list(
                map(
                    lambda investigation: self._reference(
                        self._observation(
                            investigation,
                            title=investigation.investigation.name,
                            value=(
                                investigation.notes
                                if investigation.value is None
                                else {
                                    "value": investigation.value,
                                    "unit": investigation.investigation.unit,
                                }
                            ),
                            date=investigation.created_date.isoformat(),
                        )
                    ),
                    investigation_values,
                )
            ),
            subject=self._reference(
                self._patient(investigation_values.first().consultation.patient)
            ),
            performer=[
                self._reference(
                    self._organization(
                        investigation_values.first().consultation.facility
                    )
                )
            ],
            resultsInterpreter=[
                self._reference(self._practitioner(investigation_session.created_by))
            ],
            conclusion="Refered to Doctor.",
        )

    @cache_profiles(Medication.get_resource_type())
    def _medication(self, medicine: MedibaseMedicine):
        id = str(medicine.external_id)

        return Medication(
            id=id,
            identifier=[Identifier(value=id)],
            code=CodeableConcept(text=medicine.name),
        )

    @cache_profiles(MedicationRequest.get_resource_type())
    def _medication_request(self, prescription: Prescription):
        def status(prescription: Prescription):
            if prescription.discontinued:
                return "stopped"

            # TODO: expand this

            return "unknown"

        def dosage_text(prescription: Prescription):
            text = f"{prescription.base_dosage} {FrequencyEnum[prescription.frequency].value}"

            if prescription.days:
                text += f" for {prescription.days}"

            return text

        id = str(prescription.external_id)

        return MedicationRequest(
            id=id,
            identifier=[Identifier(value=id)],
            status=status(prescription),
            intent="order",
            authoredOn=prescription.created_date.isoformat(),
            dosageInstruction=[Dosage(text=dosage_text(prescription))],
            note=[Annotation(text=prescription.notes)] if prescription.notes else None,
            medicationReference=self._reference(
                self._medication(prescription.medicine)
            ),
            subject=self._reference(self._patient(prescription.consultation.patient)),
            requester=self._reference(self._practitioner(prescription.prescribed_by)),
        )

    @cache_profiles(DocumentReference.get_resource_type())
    def _document_reference(self, file: FileUpload):
        id = str(file.external_id)
        content_type, content = file.file_contents()

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
            author=[self._reference(self._practitioner(file.uploaded_by))],
        )

    class ProcedureType(TypedDict):
        time: str | None
        frequency: str | None
        procedure: str
        repetitive: bool
        notes: str | None

    @cache_profiles(Procedure.get_resource_type())
    def _procedure(
        self,
        consultation: PatientConsultation,
        procedure: ProcedureType,
        cache_key_suffix: str = "",
    ):
        id = f"{consultation.external_id!s}{cache_key_suffix}"

        return Procedure(
            id=id,
            identifier=[Identifier(value=id)],
            status="completed",
            code=CodeableConcept(
                text=procedure["procedure"],
            ),
            subject=self._reference(self._patient(consultation.patient)),
            performedDateTime=(
                f"{procedure['time']}:00+05:30" if not procedure["repetitive"] else None
            ),
            performedString=(
                f"Every {procedure['frequency']}" if procedure["repetitive"] else None
            ),
        )

    @cache_profiles(CarePlan.get_resource_type())
    def _care_plan(self, consultation: PatientConsultation):
        id = str(consultation.external_id)

        return CarePlan(
            id=id,
            identifier=[Identifier(value=id)],
            status="completed",
            intent="plan",
            title="Care Plan",
            description="This includes Treatment Summary, Prescribed Medication, General Notes and Special Instructions",
            period=Period(
                start=consultation.encounter_date.isoformat(),
                end=(
                    consultation.discharge_date.isoformat()
                    if consultation.discharge_date
                    else None
                ),
            ),
            note=[
                Annotation(text=item)
                for item in [
                    consultation.treatment_plan,
                    consultation.consultation_notes,
                    consultation.special_instruction,
                ]
                if item
            ],
            subject=self._reference(self._patient(consultation.patient)),
        )

    def _wellness_composition(self, daily_round: DailyRound):
        id = str(daily_round.external_id)

        return Composition(
            id=id,
            identifier=Identifier(value=id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="https://projecteka.in/sct",
                        display="Wellness Record",
                    )
                ]
            ),
            title="Wellness Record",
            date=datetime.now(UTC).isoformat(),
            section=list(
                filter(
                    lambda section: section.entry and len(section.entry) > 0,
                    [
                        CompositionSection(
                            title="Vital Signs",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "vital_signs"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Body Measurement",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "body_measurement"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Physical Activity",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "physical_activity"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="General Assessment",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "general_assessment"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Women Health",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "women_health"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Lifestyle",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "lifestyle"
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Others",
                            entry=list(
                                map(
                                    lambda observation_profile: self._reference(
                                        observation_profile
                                    ),
                                    self._observations_from_daily_round(
                                        daily_round, "others"
                                    ),
                                )
                            ),
                        ),
                    ],
                )
            ),
            subject=self._reference(self._patient(daily_round.consultation.patient)),
            encounter=self._reference(self._encounter(daily_round.consultation)),
            author=[self._reference(self._practitioner(daily_round.created_by))],
        )

    def _diagnostic_report_composition(self, investigation: InvestigationSession):
        id = str(investigation.external_id)
        date = investigation.created_date.isoformat()
        investigation_values = InvestigationValue.objects.filter(session=investigation)

        if not investigation_values.exists():
            return None

        return Composition(
            id=id,
            identifier=Identifier(value=id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="https://projecteka.in/sct",
                        code="721981007",
                        display="Diagnostic Report",
                    )
                ]
            ),
            title="Diagnostic Report",
            date=date,
            section=[
                CompositionSection(
                    title="Investigation Results",
                    entry=[self._reference(self._diagnostic_report(investigation))],
                ),
            ],
            subject=self._reference(
                self._patient(investigation_values.first().consultation.patient)
            ),
            encounter=self._reference(
                self._encounter(investigation_values.first().consultation)
            ),
            author=[self._reference(self._practitioner(investigation.created_by))],
        )

    def _prescription_composition(self, prescriptions: list[Prescription]):
        id = f"prescriptions-on-{prescriptions[0].created_date.date().isoformat()}"

        return Composition(
            id=id,
            identifier=Identifier(value=id),
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
                    entry=list(
                        map(
                            lambda prescription: self._reference(
                                self._medication_request(prescription)
                            ),
                            prescriptions,
                        )
                    ),
                )
            ],
            subject=self._reference(
                self._patient(prescriptions[0].consultation.patient)
            ),
            encounter=self._reference(self._encounter(prescriptions[0].consultation)),
            author=[
                self._reference(
                    self._organization(prescriptions[0].consultation.facility)
                )
            ],
        )

    def _discharge_summary_composition(self, consultation: PatientConsultation):
        id = str(consultation.external_id)

        return Composition(
            id=id,
            identifier=Identifier(value=id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="https://projecteka.in/sct",
                        code="373942005",
                        display="Discharge Summary Record",
                    )
                ]
            ),
            title="Discharge Summary Document",
            date=datetime.now(UTC).isoformat(),
            section=list(
                filter(
                    lambda section: section.entry and len(section.entry) > 0,
                    [
                        CompositionSection(
                            title="Medications",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="721981007",
                                        display="Diagnostic studies report",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda prescription: self._reference(
                                        self._medication_request(prescription)
                                    ),
                                    Prescription.objects.filter(
                                        consultation=consultation
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Document Reference",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="373942005",
                                        display="Discharge summary",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda file: self._reference(
                                        self._document_reference(file)
                                    ),
                                    FileUpload.objects.filter(
                                        associating_id=consultation.external_id
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Procedures",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="1003640003",
                                        display="History of past procedure section",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda procedure: self._reference(
                                        self._procedure(
                                            consultation,
                                            procedure,
                                            cache_key_suffix=f".{procedure['procedure'].replace('_', '-').replace(' ', '-')}",
                                        )
                                    ),
                                    consultation.procedure,
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Care Plan",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="734163000",
                                        display="Care plan",
                                    )
                                ]
                            ),
                            entry=[self._reference(self._care_plan(consultation))],
                        ),
                    ],
                )
            ),
            subject=self._reference(self._patient(consultation.patient)),
            encounter=self._reference(
                self._encounter(consultation, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(consultation.facility))],
        )

    def _op_consultation_composition(self, consultation: PatientConsultation):
        id = str(consultation.external_id)

        return Composition(
            id=id,
            identifier=Identifier(value=id),
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
            title="OP Consultation Document",
            date=datetime.now(UTC).isoformat(),
            section=list(
                filter(
                    lambda section: section.entry and len(section.entry) > 0,
                    [
                        CompositionSection(
                            title="Medications",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="721981007",
                                        display="Diagnostic studies report",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda prescription: self._reference(
                                        self._medication_request(prescription)
                                    ),
                                    Prescription.objects.filter(
                                        consultation=consultation
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Document Reference",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="373942005",
                                        display="Discharge summary",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda file: self._reference(
                                        self._document_reference(file)
                                    ),
                                    FileUpload.objects.filter(
                                        associating_id=consultation.external_id
                                    ),
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Procedures",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="1003640003",
                                        display="History of past procedure section",
                                    )
                                ]
                            ),
                            entry=list(
                                map(
                                    lambda procedure: self._reference(
                                        self._procedure(
                                            consultation,
                                            procedure,
                                            cache_key_suffix=f".{procedure['procedure'].replace('_', '-').replace(' ', '-')}",
                                        )
                                    ),
                                    consultation.procedure,
                                )
                            ),
                        ),
                        CompositionSection(
                            title="Care Plan",
                            code=CodeableConcept(
                                coding=[
                                    Coding(
                                        system="http://snomed.info/sct",
                                        code="734163000",
                                        display="Care plan",
                                    )
                                ]
                            ),
                            entry=[self._reference(self._care_plan(consultation))],
                        ),
                    ],
                )
            ),
            subject=self._reference(self._patient(consultation.patient)),
            encounter=self._reference(
                self._encounter(consultation, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(consultation.facility))],
        )

    def _bundle_entry(self, resource: Resource):
        return BundleEntry(fullUrl=self._reference_url(resource), resource=resource)

    def create_wellness_record(self, daily_round: DailyRound):
        id = uuid()
        now = datetime.now(UTC).isoformat()
        last_updated = daily_round.modified_date.isoformat()

        return Bundle(
            id=id,
            identifier=Identifier(
                value=id, system=f"{care_identifier}/bundle"
            ),  # TODO: use a id that is in the system
            type="document",
            timestamp=now,
            meta=Meta(lastUpdated=last_updated),
            entry=[
                self._bundle_entry(self._wellness_composition(daily_round)),
                *list(
                    map(
                        lambda profile: self._bundle_entry(profile),
                        self.cached_profiles(),
                    )
                ),
            ],
        )

    def create_diagnostic_report_record(self, investigation: InvestigationSession):
        id = uuid()
        now = datetime.now(UTC).isoformat()
        last_updated = investigation.modified_date.isoformat()

        return Bundle(
            id=id,
            identifier=Identifier(value=id, system=f"{care_identifier}/bundle"),
            type="document",
            timestamp=now,
            meta=Meta(lastUpdated=last_updated),
            entry=[
                self._bundle_entry(self._diagnostic_report_composition(investigation)),
                *list(
                    map(
                        lambda profile: self._bundle_entry(profile),
                        self.cached_profiles(),
                    )
                ),
            ],
        )

    def create_prescription_record(self, prescriptions: list[Prescription]):
        id = uuid()
        now = datetime.now(UTC).isoformat()
        last_updated = now  # TODO: use the greatest modified date of the prescriptions

        return Bundle(
            id=id,
            identifier=Identifier(value=id, system=f"{care_identifier}/bundle"),
            type="document",
            timestamp=now,
            meta=Meta(lastUpdated=last_updated),
            entry=[
                self._bundle_entry(self._prescription_composition(prescriptions)),
                *list(
                    map(
                        lambda profile: self._bundle_entry(profile),
                        self.cached_profiles(),
                    )
                ),
            ],
        )

    def create_discharge_summary_record(self, consultation: PatientConsultation):
        id = uuid()
        now = datetime.now(UTC).isoformat()
        last_updated = consultation.modified_date.isoformat()

        return Bundle(
            id=id,
            identifier=Identifier(value=id, system=f"{care_identifier}/bundle"),
            type="document",
            timestamp=now,
            meta=Meta(lastUpdated=last_updated),
            entry=[
                self._bundle_entry(self._discharge_summary_composition(consultation)),
                *list(
                    map(
                        lambda profile: self._bundle_entry(profile),
                        self.cached_profiles(),
                    )
                ),
            ],
        )

    def create_op_consultation_record(self, consultation: PatientConsultation):
        id = uuid()
        now = datetime.now(UTC).isoformat()
        last_updated = consultation.modified_date.isoformat()

        return Bundle(
            id=id,
            identifier=Identifier(value=id, system=f"{care_identifier}/bundle"),
            type="document",
            timestamp=now,
            meta=Meta(lastUpdated=last_updated),
            entry=[
                self._bundle_entry(self._discharge_summary_composition(consultation)),
                *list(
                    map(
                        lambda profile: self._bundle_entry(profile),
                        self.cached_profiles(),
                    )
                ),
            ],
        )
