from datetime import UTC, datetime

from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.diagnostic_report import DiagnosticReport as DiagnosticReportModel
from care.emr.models.file_upload import FileUpload as FileUploadModel
from care.emr.resources.diagnostic_report.spec import (
    DiagnosticReportRetrieveSpec,
    DiagnosticReportStatusChoices,
)

DIAGNOSTIC_REPORT_STATUS_CODE_MAP = {
    DiagnosticReportStatusChoices.registered: "registered",
    DiagnosticReportStatusChoices.partial: "partial",
    DiagnosticReportStatusChoices.preliminary: "preliminary",
    DiagnosticReportStatusChoices.final: "final",
}


class DiagnosticReportMixin:
    @cache_profiles(DiagnosticReport.get_resource_type())
    def _diagnostic_report(self, diagnostic_report: DiagnosticReportModel):
        diagnostic_report_spec = DiagnosticReportRetrieveSpec.serialize(
            diagnostic_report
        )
        id = str(diagnostic_report_spec.id)

        files = FileUploadModel.objects.filter(
            associating_id=diagnostic_report.external_id
        )

        diagnostic_report_code_display = diagnostic_report_spec.code.get(
            "display"
        ) or diagnostic_report_spec.code.get("code", "")
        diagnostic_report_div_parts = [
            f"<p><b>Diagnostic Report:</b> {diagnostic_report_code_display}</p>"
        ]
        if diagnostic_report_spec.category:
            diagnostic_report_category_display = diagnostic_report_spec.category.get(
                "display"
            ) or diagnostic_report_spec.category.get("code", "NA")
            diagnostic_report_div_parts.append(
                f"<p><b>Category:</b> {diagnostic_report_category_display}</p>"
            )
        if diagnostic_report_spec.conclusion:
            diagnostic_report_div_parts.append(
                f"<p><b>Conclusion:</b> {diagnostic_report_spec.conclusion}</p>"
            )

        return DiagnosticReport(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DiagnosticReportLab"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(diagnostic_report_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            basedOn=[
                self._reference(
                    self._service_request(diagnostic_report.service_request)
                ),
            ],
            status=DIAGNOSTIC_REPORT_STATUS_CODE_MAP.get(
                diagnostic_report_spec.status, "final"
            ),
            category=[self._coding_to_codable_concept(diagnostic_report_spec.category)]
            if diagnostic_report_spec.category
            else None,
            code=self._coding_to_codable_concept(diagnostic_report_spec.code),
            subject=self._reference(self._patient(diagnostic_report.patient)),
            encounter=self._reference(self._encounter(diagnostic_report.encounter)),
            issued=diagnostic_report.modified_date.isoformat(),
            performer=[
                self._reference(self._practitioner(diagnostic_report.created_by))
            ],
            resultsInterpreter=[
                self._reference(self._practitioner(diagnostic_report.created_by))
            ],
            specimen=[
                self._reference(self._specimen(specimen))
                for specimen in diagnostic_report.service_request.specimen_set.all()
            ],
            result=[
                self._reference(self._observation(observation))
                for observation in diagnostic_report.observation_set.all()
            ],
            conclusion=diagnostic_report_spec.conclusion
            if diagnostic_report_spec.conclusion
            else None,
            presentedForm=[self._attachment(file) for file in files] if files else None,
        )
