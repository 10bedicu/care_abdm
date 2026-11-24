from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import Composition, CompositionSection
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import uuid
from abdm.settings import plugin_settings as settings
from care.emr.models.immunisation import Immunization as ImmunizationModel
from care.emr.models.immunisation import (
    ImmunizationRecommendation as ImmunizationRecommendationModel,
)
from care.emr.resources.immunisation.recommendation import (
    ImmunizationRecommendationForecastStatus,
)

CARE_IDENTIFIER_SYSTEM = settings.BACKEND_DOMAIN


def _immunization_record_concept():
    return CodeableConcept(
        coding=[
            Coding(
                system="http://snomed.info/sct",
                code="41000179103",
                display="Immunization record",
            )
        ],
        text="Immunization record",
    )


class ImmunizationRecordCompositionMixin:
    def _immunization_record_composition(
        self, immunization: ImmunizationModel, care_context_id: str
    ):
        due_recommendations = list(
            ImmunizationRecommendationModel.objects.filter(
                patient_id=immunization.patient_id,
                is_group=False,
                forecast_status=ImmunizationRecommendationForecastStatus.due.value,
            )
            .exclude(codes=[])
            .order_by("due_date", "created_date")
        )
        recommendation = (
            self._immunization_recommendation(
                immunization.patient, due_recommendations, immunization.facility
            )
            if due_recommendations
            else None
        )

        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ImmunizationRecord"
                ],
            ),
            identifier=Identifier(
                value=care_context_id,
                system=f"{CARE_IDENTIFIER_SYSTEM}/composition",
            ),
            status="final",
            type=_immunization_record_concept(),
            title="Immunization record",
            date=datetime.now(UTC).isoformat(),
            section=[
                CompositionSection(
                    title="Immunization record",
                    code=_immunization_record_concept(),
                    entry=[
                        self._reference(
                            self._immunization(immunization), "Immunization"
                        ),
                        *(
                            [
                                self._reference(
                                    recommendation, "ImmunizationRecommendation"
                                )
                            ]
                            if recommendation
                            else []
                        ),
                    ],
                )
            ],
            subject=self._reference(self._patient(immunization.patient)),
            encounter=self._reference(self._encounter(immunization.encounter)),
            author=[self._reference(self._organization(immunization.facility))],
        )

    def create_immunization_record(
        self, immunization: ImmunizationModel, care_context_id: str | None = None
    ):
        care_context_id = care_context_id or uuid()

        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._immunization_record_composition(immunization, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )
