from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from importlib import import_module
from unittest.mock import patch

from abdm.service.helper import (
    ABDMAPIException,
    care_context_dict_from_reference_id,
    create_questionnaire_response_care_context,
)
from abdm.utils.fhir.fhir import Fhir
from abdm.utils.fhir.resources.encounter import ENCOUNTER_CLASS_CODE_MAP
from django.test import SimpleTestCase
from model_bakery import baker

from care.emr.models.allergy_intolerance import AllergyIntolerance
from care.emr.models.condition import Condition
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.file_upload import FileUpload
from care.emr.models.immunisation import Immunization, ImmunizationRecommendation
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.models.medication_statement import MedicationStatement
from care.emr.models.observation import Observation
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.models.service_request import ServiceRequest
from care.emr.models.specimen import Specimen
from care.emr.resources.activity_definition.spec import ActivityDefinitionCategoryOptions
from care.emr.resources.allergy_intolerance.spec import (
    CategoryChoices as AllergyCategoryChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    ClinicalStatusChoices as AllergyClinicalStatusChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    CriticalityChoices as AllergyCriticalityChoices,
)
from care.emr.resources.allergy_intolerance.spec import (
    VerificationStatusChoices as AllergyVerificationStatusChoices,
)
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.condition.spec import (
    CategoryChoices as ConditionCategoryChoices,
)
from care.emr.resources.condition.spec import (
    ClinicalStatusChoices as ConditionClinicalStatusChoices,
)
from care.emr.resources.condition.spec import (
    SeverityChoices as ConditionSeverityChoices,
)
from care.emr.resources.condition.spec import (
    VerificationStatusChoices as ConditionVerificationStatusChoices,
)
from care.emr.resources.diagnostic_report.spec import DiagnosticReportStatusChoices
from care.emr.resources.encounter.constants import ClassChoices
from care.emr.resources.file_upload.spec import FileCategoryChoices, FileTypeChoices
from care.emr.resources.immunisation.immunization import (
    ImmunizationReason,
    ImmunizationStatus,
)
from care.emr.resources.immunisation.recommendation import (
    ImmunizationRecommendationForecastStatus,
)
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.medication.request.spec import (
    MedicationRequestCategory,
    MedicationRequestIntent,
    MedicationRequestPriority,
    MedicationRequestStatus,
)
from care.emr.resources.medication.statement.spec import MedicationStatementStatus
from care.emr.resources.observation.spec import ObservationStatus
from care.emr.resources.questionnaire.spec import QuestionType, SubjectType
from care.emr.resources.questionnaire_response.spec import (
    QuestionnaireResponseStatusChoices,
)
from care.emr.resources.service_request.spec import (
    ServiceRequestIntentChoices,
    ServiceRequestPriorityChoices,
    ServiceRequestStatusChoices,
)
from care.emr.resources.specimen.spec import SpecimenStatusOptions
from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now

CODE = {
    "system": "http://snomed.info/sct",
    "code": "386661006",
    "display": "Fever",
}

VACCINE = {
    "system": "http://snomed.info/sct",
    "code": "1119305005",
    "display": "COVID-19 antigen vaccine",
}

FHIR_RESOURCE_MODULES = [
    "allergy_intolerance",
    "charge_item",
    "condition",
    "diagnostic_report",
    "encounter",
    "immunization",
    "immunization_recommendation",
    "invoice",
    "medication_request",
    "medication_statement",
    "observation",
    "service_request",
    "specimen",
]


class FhirCodeMapCoverageTests(SimpleTestCase):
    def test_every_care_enum_value_has_a_fhir_mapping(self):
        for name in FHIR_RESOURCE_MODULES:
            module = import_module(f"abdm.utils.fhir.resources.{name}")
            for attr, mapping in vars(module).items():
                if not attr.endswith("_CODE_MAP") or not mapping:
                    continue
                enum_cls = type(next(iter(mapping)))
                if not issubclass(enum_cls, Enum):
                    continue
                with self.subTest(map=f"{name}.{attr}"):
                    self.assertEqual(
                        [member.value for member in enum_cls if member not in mapping],
                        [],
                    )

    def test_unmapped_value_falls_back_to_default(self):
        coding = Fhir()._coding_from_mapping(
            system="http://example.org",
            mapping={"known": ("K", "Known")},
            key="unknown",
            default="known",
        )
        self.assertEqual((coding.code, coding.display), ("K", "Known"))


class CareFhirContractTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        contents = patch(
            "care.emr.utils.file_manager.S3FilesManager.file_contents",
            return_value=("application/pdf", b"note"),
        )
        contents.start()
        self.addCleanup(contents.stop)
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.organization = self.create_facility_organization(self.facility)
        self.patient = self.create_patient(name="FHIR Patient", gender="male")
        self.encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )
        self.account = baker.make(
            "emr.Account", facility=self.facility, patient=self.patient
        )
        self.fhir = Fhir()

    def test_patient_organization_and_practitioner(self):
        patient = self.fhir._patient(self.patient)
        organization = self.fhir._organization(self.facility)
        practitioner = self.fhir._practitioner(self.user)

        self.assertEqual(patient.resource_type, "Patient")
        self.assertEqual(patient.name[0].text, self.patient.name)
        self.assertEqual(patient.identifier[0].type.coding[0].code, "MR")
        self.assertEqual(organization.resource_type, "Organization")
        self.assertEqual(organization.name, self.facility.name)
        self.assertEqual(practitioner.resource_type, "Practitioner")

    def test_encounter_uses_care_class_and_status(self):
        resource = self.fhir._encounter(self.encounter)

        self.assertEqual(resource.resource_type, "Encounter")
        self.assertEqual(
            resource.class_fhir.code,
            ENCOUNTER_CLASS_CODE_MAP[ClassChoices(self.encounter.encounter_class)][0],
        )

    def test_condition_observation_and_allergy(self):
        condition = baker.make(
            Condition,
            patient=self.patient,
            encounter=self.encounter,
            clinical_status=ConditionClinicalStatusChoices.active.value,
            verification_status=ConditionVerificationStatusChoices.confirmed.value,
            category=ConditionCategoryChoices.encounter_diagnosis.value,
            severity=ConditionSeverityChoices.mild.value,
            code=CODE,
        )
        observation = baker.make(
            Observation,
            patient=self.patient,
            encounter=self.encounter,
            status=ObservationStatus.final.value,
            value_type=QuestionType.integer.value,
            value={"value": "120"},
            main_code=CODE,
            subject_type=SubjectType.encounter.value,
            subject_id=self.encounter.external_id,
            effective_datetime=care_now(),
            note="",
        )
        allergy = baker.make(
            AllergyIntolerance,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            clinical_status=AllergyClinicalStatusChoices.active.value,
            verification_status=AllergyVerificationStatusChoices.confirmed.value,
            category=AllergyCategoryChoices.food.value,
            criticality=AllergyCriticalityChoices.low.value,
            code=CODE,
        )

        self.assertEqual(self.fhir._condition(condition).resource_type, "Condition")
        observed = self.fhir._observation(observation)
        self.assertEqual(observed.resource_type, "Observation")
        self.assertEqual(observed.code.coding[0].code, CODE["code"])
        self.assertEqual(
            self.fhir._allergy_intolerance(allergy).resource_type, "AllergyIntolerance"
        )

    def test_medication_request_and_statement(self):
        request = baker.make(
            MedicationRequest,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            status=MedicationRequestStatus.active.value,
            intent=MedicationRequestIntent.order.value,
            category=MedicationRequestCategory.outpatient.value,
            priority=MedicationRequestPriority.routine.value,
            do_not_perform=False,
            medication={"code": "387207008", "display": "Ibuprofen"},
            dosage_instruction=[],
        )
        statement = baker.make(
            MedicationStatement,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            status=MedicationStatementStatus.active.value,
            medication={"code": "387207008", "display": "Ibuprofen"},
            information_source="patient",
        )

        requested = self.fhir._medication_request(request)
        stated = self.fhir._medication_statement(statement)
        self.assertEqual(requested.resource_type, "MedicationRequest")
        self.assertEqual(stated.resource_type, "MedicationStatement")

    def test_service_request_specimen_and_diagnostic_report(self):
        service_request = baker.make(
            ServiceRequest,
            facility=self.facility,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            title="Complete blood count",
            category=ActivityDefinitionCategoryOptions.laboratory.value,
            status=ServiceRequestStatusChoices.active.value,
            intent=ServiceRequestIntentChoices.order.value,
            priority=ServiceRequestPriorityChoices.routine.value,
            code=CODE,
        )
        specimen = baker.make(
            Specimen,
            facility=self.facility,
            patient=self.patient,
            encounter=self.encounter,
            service_request=service_request,
            status=SpecimenStatusOptions.available.value,
            specimen_type=CODE,
            accession_identifier="ACC-1",
        )
        report = baker.make(
            DiagnosticReport,
            facility=self.facility,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            service_request=service_request,
            status=DiagnosticReportStatusChoices.final.value,
            category=CODE,
            code=CODE,
        )

        self.assertEqual(
            self.fhir._service_request(service_request).resource_type, "ServiceRequest"
        )
        self.assertEqual(self.fhir._specimen(specimen).resource_type, "Specimen")
        diagnostic = self.fhir._diagnostic_report(report)
        self.assertEqual(diagnostic.resource_type, "DiagnosticReport")
        self.assertEqual(diagnostic.status, "final")

    def test_document_charge_item_and_invoice(self):
        upload = baker.make(
            FileUpload,
            name="note.pdf",
            internal_name="note.pdf",
            associating_id=str(self.patient.external_id),
            file_type=FileTypeChoices.patient.value,
            file_category=FileCategoryChoices.unspecified.value,
        )
        charge_item = baker.make(
            "emr.ChargeItem",
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            title="Consultation",
            status=ChargeItemStatusOptions.billed.value,
            quantity=1,
            total_price=Decimal(100),
            total_price_components=[
                {"monetary_component_type": "base", "amount": 100}
            ],
            unit_price_components=[
                {"monetary_component_type": "base", "amount": 100}
            ],
        )
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            created_by=self.user,
            status=InvoiceStatusOptions.issued.value,
            number="INV-FHIR",
            title="Visit",
            total_net=Decimal(100),
            total_gross=Decimal(100),
            charge_items=[charge_item.id],
            issue_date=datetime.now(UTC),
        )

        document = self.fhir._document_reference(upload)
        charged = self.fhir._charge_item(charge_item)
        invoiced = self.fhir._invoice(invoice)
        self.assertEqual(document.resource_type, "DocumentReference")
        self.assertEqual(charged.resource_type, "ChargeItem")
        self.assertEqual(charged.status, "billed")
        self.assertEqual(invoiced.resource_type, "Invoice")
        self.assertEqual(invoiced.totalGross.value, 100.0)

    def make_immunization(self, **kwargs):
        return baker.make(
            Immunization,
            **{
                "patient": self.patient,
                "encounter": self.encounter,
                "facility": self.facility,
                "created_by": self.user,
                "status": ImmunizationStatus.completed.value,
                "code": VACCINE,
                "primary_source": True,
                "occurrence": care_now(),
                **kwargs,
            },
        )

    def make_recommendation(self, **kwargs):
        return baker.make(
            ImmunizationRecommendation,
            **{
                "patient": self.patient,
                "codes": [VACCINE],
                "forecast_status": ImmunizationRecommendationForecastStatus.due.value,
                "is_group": False,
                **kwargs,
            },
        )

    def test_immunization_record_carries_the_dose_and_due_recommendations(self):
        given = self.make_recommendation(
            forecast_status=ImmunizationRecommendationForecastStatus.complete.value,
            series="Primary",
            dose_number="1",
            series_number="2",
            diseases=[CODE],
        )
        next_dose = self.make_recommendation(
            series="Primary",
            dose_number="2",
            series_number="2",
            due_date=care_now().date() + timedelta(days=28),
        )
        self.make_recommendation(
            forecast_status=ImmunizationRecommendationForecastStatus.immune.value
        )
        self.make_recommendation(is_group=True, codes=[])
        product = baker.make(
            "emr.Product",
            facility=self.facility,
            batch={"lot_number": "LOT-42"},
            product_knowledge=baker.make("emr.ProductKnowledge", name="Covishield"),
        )
        immunization = self.make_immunization(
            recommendation=given,
            product=product,
            administered_by=self.user,
            dose_quantity={
                "value": "0.5",
                "unit": {
                    "system": "http://unitsofmeasure.org",
                    "code": "mL",
                    "display": "milliliter",
                },
            },
        )

        composition = self.fhir._immunization_record_composition(
            immunization, "context-1"
        )
        dose = self.fhir._immunization(immunization)
        forecast = next(
            profile
            for profile in self.fhir.cached_profiles()
            if profile.resource_type == "ImmunizationRecommendation"
        )

        self.assertEqual(composition.type.coding[0].code, "41000179103")
        self.assertEqual(
            [(entry.type, entry.reference) for entry in composition.section[0].entry],
            [
                ("Immunization", self.fhir._reference(dose).reference),
                (
                    "ImmunizationRecommendation",
                    self.fhir._reference(forecast).reference,
                ),
            ],
        )
        self.assertEqual(dose.status, "completed")
        self.assertEqual(dose.vaccineCode.coding[0].code, VACCINE["code"])
        self.assertEqual(dose.lotNumber, "LOT-42")
        self.assertEqual(dose.extension[0].valueString, "Covishield")
        self.assertEqual(dose.doseQuantity.value, Decimal("0.5"))
        self.assertEqual(dose.performer[0].function.coding[0].code, "AP")
        self.assertEqual(
            (
                dose.protocolApplied[0].doseNumberPositiveInt,
                dose.protocolApplied[0].seriesDosesPositiveInt,
                dose.protocolApplied[0].targetDisease[0].coding[0].code,
            ),
            (1, 2, CODE["code"]),
        )
        [recommended] = forecast.recommendation
        self.assertEqual(recommended.forecastStatus.coding[0].code, "due")
        self.assertEqual(recommended.doseNumberPositiveInt, 2)
        self.assertEqual(
            [
                (criterion.code.coding[0].code, criterion.value)
                for criterion in recommended.dateCriterion
            ],
            [("30980-7", next_dose.due_date)],
        )

    def test_not_done_immunization_carries_its_reason(self):
        immunization = self.make_immunization(
            status=ImmunizationStatus.not_done.value,
            reason=ImmunizationReason.PATOBJ.value,
            note="<refused>",
        )

        composition = self.fhir._immunization_record_composition(
            immunization, "context-1"
        )
        dose = self.fhir._immunization(immunization)

        self.assertEqual(dose.status, "not-done")
        self.assertEqual(dose.statusReason.coding[0].code, "PATOBJ")
        self.assertIn("&lt;refused&gt;", dose.text.div)
        self.assertEqual(
            [entry.type for entry in composition.section[0].entry], ["Immunization"]
        )

    def test_compositions_build_from_care_records(self):
        condition = baker.make(
            Condition,
            patient=self.patient,
            encounter=self.encounter,
            clinical_status=ConditionClinicalStatusChoices.active.value,
            verification_status=ConditionVerificationStatusChoices.confirmed.value,
            category=ConditionCategoryChoices.encounter_diagnosis.value,
            code=CODE,
        )
        observation = baker.make(
            Observation,
            patient=self.patient,
            encounter=self.encounter,
            status=ObservationStatus.final.value,
            value_type=QuestionType.integer.value,
            value={"value": "98"},
            main_code=CODE,
            subject_type=SubjectType.encounter.value,
            subject_id=self.encounter.external_id,
            effective_datetime=care_now(),
            note="",
        )
        request = baker.make(
            MedicationRequest,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            status=MedicationRequestStatus.active.value,
            intent=MedicationRequestIntent.order.value,
            category=MedicationRequestCategory.outpatient.value,
            priority=MedicationRequestPriority.routine.value,
            do_not_perform=False,
            medication={"code": "387207008", "display": "Ibuprofen"},
            dosage_instruction=[],
        )
        service_request = baker.make(
            ServiceRequest,
            facility=self.facility,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            title="Complete blood count",
            category=ActivityDefinitionCategoryOptions.laboratory.value,
            status=ServiceRequestStatusChoices.active.value,
            intent=ServiceRequestIntentChoices.order.value,
            priority=ServiceRequestPriorityChoices.routine.value,
            code=CODE,
        )
        report = baker.make(
            DiagnosticReport,
            facility=self.facility,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            service_request=service_request,
            status=DiagnosticReportStatusChoices.final.value,
            category=CODE,
            code=CODE,
        )
        response = baker.make(
            QuestionnaireResponse,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            subject_id=self.encounter.external_id,
        )
        observation.questionnaire_response = response
        observation.save(update_fields=["questionnaire_response"])
        invoice = baker.make(
            Invoice,
            facility=self.facility,
            patient=self.patient,
            account=self.account,
            created_by=self.user,
            status=InvoiceStatusOptions.issued.value,
            number="INV-COMP",
            title="Visit",
            total_gross=Decimal(50),
            total_net=Decimal(50),
            charge_items=[],
            issue_date=datetime.now(UTC),
        )
        upload = baker.make(
            FileUpload,
            name="record.pdf",
            internal_name="record.pdf",
            associating_id=str(self.patient.external_id),
            file_type=FileTypeChoices.patient.value,
            file_category=FileCategoryChoices.unspecified.value,
        )
        context = "context-1"

        compositions = [
            self.fhir._op_consult_composition(self.encounter, context),
            self.fhir._discharge_summary_composition(self.encounter, context),
            self.fhir._prescription_composition([request], context),
            self.fhir._wellness_composition(response, context),
            self.fhir._diagnostic_report_composition(report, context),
            self.fhir._invoice_record_composition(invoice, context),
            self.fhir._health_document_composition(upload, context),
            self.fhir._immunization_record_composition(
                self.make_immunization(), context
            ),
        ]

        self.assertTrue(condition.id)
        self.assertEqual(
            [item.resource_type for item in compositions],
            ["Composition"] * len(compositions),
        )

    def make_observation(self, status, **kwargs):
        return baker.make(
            Observation,
            patient=self.patient,
            encounter=self.encounter,
            status=status,
            value_type=QuestionType.integer.value,
            value={"value": "98"},
            main_code=CODE,
            subject_type=SubjectType.encounter.value,
            subject_id=self.encounter.external_id,
            effective_datetime=care_now(),
            note="",
            **kwargs,
        )

    def test_observation_status_uses_fhir_codes(self):
        for status, code in [
            (ObservationStatus.final, "final"),
            (ObservationStatus.amended, "amended"),
            (ObservationStatus.entered_in_error, "entered-in-error"),
        ]:
            observation = self.make_observation(status.value)
            self.assertEqual(self.fhir._observation(observation).status, code)

    def test_encounter_compositions_skip_entered_in_error_observations(self):
        kept = self.make_observation(ObservationStatus.final.value)
        self.make_observation(ObservationStatus.entered_in_error.value)

        for build in (
            self.fhir._op_consult_composition,
            self.fhir._discharge_summary_composition,
        ):
            composition = build(self.encounter, "context-1")
            section = next(
                s for s in composition.section if s.title == "Physical Examination"
            )
            self.assertEqual(
                [entry.reference for entry in section.entry],
                [self.fhir._reference(self.fhir._observation(kept)).reference],
            )

    def test_entered_in_error_questionnaire_response_is_not_shared(self):
        response = baker.make(
            QuestionnaireResponse,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            subject_id=self.encounter.external_id,
            status=QuestionnaireResponseStatusChoices.entered_in_error.value,
        )
        self.make_observation(
            ObservationStatus.entered_in_error.value,
            questionnaire_response=response,
        )
        reference = create_questionnaire_response_care_context(response)["reference"]

        with self.assertRaises(ABDMAPIException):
            self.fhir._wellness_composition(response, "context-1")
        self.assertIsNone(care_context_dict_from_reference_id(reference))

    def test_previously_unmapped_values_build(self):
        chronic = baker.make(
            Condition,
            patient=self.patient,
            encounter=self.encounter,
            clinical_status=ConditionClinicalStatusChoices.active.value,
            verification_status=ConditionVerificationStatusChoices.differential.value,
            category=ConditionCategoryChoices.chronic_condition.value,
            code=CODE,
        )
        allergy = baker.make(
            AllergyIntolerance,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            clinical_status=AllergyClinicalStatusChoices.active.value,
            verification_status=AllergyVerificationStatusChoices.presumed.value,
            category=AllergyCategoryChoices.food.value,
            criticality=AllergyCriticalityChoices.low.value,
            code=CODE,
        )
        request = baker.make(
            MedicationRequest,
            patient=self.patient,
            encounter=self.encounter,
            created_by=self.user,
            status=MedicationRequestStatus.ended.value,
            intent=MedicationRequestIntent.order.value,
            category=MedicationRequestCategory.inpatient.value,
            priority=MedicationRequestPriority.routine.value,
            do_not_perform=False,
            medication=CODE,
            dosage_instruction=[],
        )

        condition = self.fhir._condition(chronic)
        self.assertEqual(condition.category[0].coding[0].code, "problem-list-item")
        self.assertEqual(condition.verificationStatus.coding[0].code, "differential")
        self.assertEqual(
            self.fhir._allergy_intolerance(allergy).verificationStatus.coding[0].code,
            "unconfirmed",
        )
        self.assertEqual(self.fhir._medication_request(request).status, "stopped")
        self.fhir._op_consult_composition(self.encounter, "context-1")
