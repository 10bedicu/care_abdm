from datetime import UTC, datetime
from html import escape

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.immunizationrecommendation import (
    ImmunizationRecommendation,
    ImmunizationRecommendationRecommendation,
    ImmunizationRecommendationRecommendationDateCriterion,
)
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from abdm.utils.fhir.resources.immunization import positive_int_or_string
from care.emr.models.immunisation import (
    ImmunizationRecommendation as ImmunizationRecommendationModel,
)
from care.emr.models.patient import Patient as PatientModel
from care.emr.resources.immunisation.recommendation import (
    ImmunizationRecommendationForecastStatus,
)
from care.facility.models import Facility as FacilityModel

IMMUNIZATION_RECOMMENDATION_FORECAST_STATUS_CODE_MAP = {
    ImmunizationRecommendationForecastStatus.due: ("due", "Due"),
    ImmunizationRecommendationForecastStatus.immune: ("immune", "Immune"),
    ImmunizationRecommendationForecastStatus.contraindicated: (
        "contraindicated",
        "Contraindicated",
    ),
    ImmunizationRecommendationForecastStatus.complete: ("complete", "Complete"),
}

# LOINC codes from the FHIR immunization-recommendation-date-criterion value set
IMMUNIZATION_RECOMMENDATION_DATE_CRITERIA = (
    ("earliest_date", "30981-5", "Earliest date to give"),
    ("due_date", "30980-7", "Date vaccine due"),
    ("overdue_date", "59778-1", "Date when overdue for immunization"),
)


class ImmunizationRecommendationMixin:
    @cache_profiles(ImmunizationRecommendation.get_resource_type())
    def _immunization_recommendation(
        self,
        patient: PatientModel,
        recommendations: list[ImmunizationRecommendationModel],
        authority: FacilityModel | None = None,
    ):
        """One forecast resource per patient, holding every given recommendation."""
        id = str(patient.external_id)

        div_parts = []
        for recommendation in recommendations:
            vaccines = ", ".join(
                coding.get("display") or coding.get("code")
                for coding in recommendation.codes or []
            )
            due = (
                f" (due {recommendation.due_date.isoformat()})"
                if recommendation.due_date
                else ""
            )
            div_parts.append(
                f"<p><b>{escape(vaccines)}</b>: {escape(recommendation.forecast_status)}{due}</p>"
            )

        return ImmunizationRecommendation(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ImmunizationRecommendation"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(div_parts)
                + "</div>",
            ),
            patient=self._reference(self._patient(patient)),
            date=max(
                recommendation.modified_date for recommendation in recommendations
            ).isoformat(),
            authority=self._reference(self._organization(authority))
            if authority
            else None,
            recommendation=[
                self._immunization_recommendation_entry(recommendation)
                for recommendation in recommendations
            ],
        )

    def _immunization_recommendation_entry(
        self, recommendation: ImmunizationRecommendationModel
    ):
        diseases = recommendation.diseases or []

        return ImmunizationRecommendationRecommendation(
            vaccineCode=[
                self._coding_to_codable_concept(coding)
                for coding in recommendation.codes or []
            ]
            or None,
            # R4 allows a single target disease; a combination vaccine is identified by its vaccineCode instead
            targetDisease=self._coding_to_codable_concept(diseases[0])
            if len(diseases) == 1
            else None,
            forecastStatus=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/immunization-recommendation-status",
                mapping=IMMUNIZATION_RECOMMENDATION_FORECAST_STATUS_CODE_MAP,
                key=recommendation.forecast_status,
                default=ImmunizationRecommendationForecastStatus.due.value,
            ),
            dateCriterion=[
                ImmunizationRecommendationRecommendationDateCriterion(
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://loinc.org", code=code, display=display
                            )
                        ],
                        text=display,
                    ),
                    value=getattr(recommendation, field).isoformat(),
                )
                for field, code, display in IMMUNIZATION_RECOMMENDATION_DATE_CRITERIA
                if getattr(recommendation, field)
            ]
            or None,
            description=recommendation.description or None,
            series=recommendation.series or None,
            **positive_int_or_string("doseNumber", recommendation.dose_number),
            **positive_int_or_string("seriesDoses", recommendation.series_number),
        )
