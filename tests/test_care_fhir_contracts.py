from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from abdm.utils.fhir.fhir import Fhir
from abdm.utils.fhir.resources.encounter import ENCOUNTER_CLASS_CODE_MAP
from model_bakery import baker

from care.emr.models.allergy_intolerance import AllergyIntolerance
from care.emr.models.condition import Condition
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.file_upload import FileUpload
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
        ]

        self.assertTrue(condition.id)
        self.assertEqual(
            [item.resource_type for item in compositions],
            ["Composition"] * len(compositions),
        )
