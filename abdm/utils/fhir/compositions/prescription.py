from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import uuid
from abdm.settings import plugin_settings as settings
from care.emr.models.medication_request import (
    MedicationRequest as MedicationRequestModel,
)

CARE_IDENTIFIER_SYSTEM = settings.BACKEND_DOMAIN


class PrescriptionCompositionMixin:
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
                value=care_context_id,
                system=f"{CARE_IDENTIFIER_SYSTEM}/composition",
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
