from datetime import UTC, datetime

from django.db.models import Q
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import ABDMAPIException, uuid
from care.emr.models.encounter import Encounter as EncounterModel
from care.emr.models.file_upload import FileUpload as FileUploadModel
from care.emr.models.patient import Patient as PatientModel
from care.emr.resources.file_upload.spec import FileTypeChoices


class HealthDocumentCompositionMixin:
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
