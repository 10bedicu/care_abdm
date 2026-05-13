from datetime import UTC, datetime

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.resource import Resource

from abdm.service.helper import uuid
from abdm.settings import plugin_settings as settings

from .base import FhirBase
from .compositions.diagnostic_report import DiagnosticReportCompositionMixin
from .compositions.discharge_summary import DischargeSummaryCompositionMixin
from .compositions.health_document import HealthDocumentCompositionMixin
from .compositions.op_consult import OPConsultCompositionMixin
from .compositions.prescription import PrescriptionCompositionMixin
from .compositions.wellness import WellnessCompositionMixin
from .resources.allergy_intolerance import AllergyIntoleranceMixin
from .resources.condition import ConditionMixin
from .resources.diagnostic_report import DiagnosticReportMixin
from .resources.document_reference import DocumentReferenceMixin
from .resources.encounter import EncounterMixin
from .resources.medication_request import MedicationRequestMixin
from .resources.medication_statement import MedicationStatementMixin
from .resources.observation import ObservationMixin
from .resources.organization import OrganizationMixin
from .resources.patient import PatientMixin
from .resources.practitioner import PractitionerMixin
from .resources.service_request import ServiceRequestMixin
from .resources.specimen import SpecimenMixin

CARE_IDENTIFIER_SYSTEM = settings.BACKEND_DOMAIN


class Fhir(
    PatientMixin,
    PractitionerMixin,
    OrganizationMixin,
    ConditionMixin,
    EncounterMixin,
    MedicationRequestMixin,
    MedicationStatementMixin,
    DocumentReferenceMixin,
    AllergyIntoleranceMixin,
    ObservationMixin,
    ServiceRequestMixin,
    SpecimenMixin,
    DiagnosticReportMixin,
    PrescriptionCompositionMixin,
    OPConsultCompositionMixin,
    DischargeSummaryCompositionMixin,
    HealthDocumentCompositionMixin,
    WellnessCompositionMixin,
    DiagnosticReportCompositionMixin,
    FhirBase,
):
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
                value=care_context_id,
                system=f"{CARE_IDENTIFIER_SYSTEM}/bundle",
            ),
            type="document",
            timestamp=datetime.now(UTC).isoformat(),
            entry=entries,
        )

    def _bundle_entry(self, resource: Resource):
        return BundleEntry(fullUrl=self._reference_url(resource), resource=resource)
