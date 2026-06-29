from datetime import UTC, datetime

from django.db.models import Q
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import uuid
from care.emr.models.allergy_intolerance import (
    AllergyIntolerance as AllergyIntoleranceModel,
)
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


def _empty_reason():
    return CodeableConcept(
        coding=[
            Coding(
                system="http://terminology.hl7.org/CodeSystem/list-empty-reason",
                code="notstarted",
                display="Not Started",
            )
        ],
        text="Not Started",
    )


class OPConsultCompositionMixin:
    def _op_consult_composition(self, encounter: EncounterModel, care_context_id: str):
        conditions = ConditionModel.objects.filter(encounter=encounter)
        observations = ObservationModel.objects.filter(encounter=encounter).exclude(
            Q(main_code__isnull=True) | Q(main_code={})
        )
        allergies = AllergyIntoleranceModel.objects.filter(encounter=encounter)
        med_requests = MedicationRequestModel.objects.filter(encounter=encounter)
        med_statements = MedicationStatementModel.objects.filter(encounter=encounter)
        files = FileUploadModel.objects.filter(associating_id=encounter.external_id)

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
                        for condition in conditions
                    ],
                    emptyReason=_empty_reason() if conditions.count() == 0 else None,
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
                        for observation in observations
                    ],
                    emptyReason=_empty_reason() if observations.count() == 0 else None,
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
                        for allergy in allergies
                    ],
                    emptyReason=_empty_reason() if allergies.count() == 0 else None,
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
                            for request in med_requests
                        ],
                        *[
                            self._reference(self._medication_statement(statement))
                            for statement in med_statements
                        ],
                    ],
                    emptyReason=_empty_reason()
                    if med_requests.count() == 0 and med_statements.count() == 0
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
                        for file in files
                    ],
                    emptyReason=_empty_reason() if files.count() == 0 else None,
                ),
            ],
            subject=self._reference(self._patient(encounter.patient)),
            encounter=self._reference(
                self._encounter(encounter, include_diagnosis=True)
            ),
            author=[self._reference(self._organization(encounter.facility))],
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
