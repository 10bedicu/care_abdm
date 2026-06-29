from datetime import UTC, datetime

from django.db.models import Q
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import ABDMAPIException, uuid
from care.emr.models.observation import Observation as ObservationModel
from care.emr.models.questionnaire import (
    QuestionnaireResponse as QuestionnaireResponseModel,
)


class WellnessCompositionMixin:
    def _wellness_composition(
        self,
        questionnaire_response: QuestionnaireResponseModel,
        care_context_id: str,
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
