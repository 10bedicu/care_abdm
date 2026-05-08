from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.servicerequest import ServiceRequest

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.service_request import ServiceRequest as ServiceRequestModel
from care.emr.resources.activity_definition.spec import (
    ActivityDefinitionCategoryOptions,
)
from care.emr.resources.service_request.spec import (
    ServiceRequestIntentChoices,
    ServiceRequestPriorityChoices,
    ServiceRequestRetrieveSpec,
    ServiceRequestStatusChoices,
)

SERVICE_REQUEST_STATUS_CODE_MAP = {
    ServiceRequestStatusChoices.draft: "draft",
    ServiceRequestStatusChoices.active: "active",
    ServiceRequestStatusChoices.on_hold: "on-hold",
    ServiceRequestStatusChoices.revoked: "revoked",
    ServiceRequestStatusChoices.ended: "completed",
    ServiceRequestStatusChoices.completed: "completed",
    ServiceRequestStatusChoices.entered_in_error: "entered-in-error",
}


SERVICE_REQUEST_INTENT_CODE_MAP = {
    ServiceRequestIntentChoices.proposal: "proposal",
    ServiceRequestIntentChoices.plan: "plan",
    ServiceRequestIntentChoices.directive: "directive",
    ServiceRequestIntentChoices.order: "order",
}


SERVICE_REQUEST_PRIORITY_CODE_MAP = {
    ServiceRequestPriorityChoices.routine: "routine",
    ServiceRequestPriorityChoices.urgent: "urgent",
    ServiceRequestPriorityChoices.asap: "asap",
    ServiceRequestPriorityChoices.stat: "stat",
}


SERVICE_REQUEST_CATEGORY_CODE_MAP = {
    ActivityDefinitionCategoryOptions.laboratory: ("108252007", "Laboratory procedure"),
    ActivityDefinitionCategoryOptions.imaging: ("363679005", "Imaging"),
    ActivityDefinitionCategoryOptions.counselling: ("409063005", "Counselling"),
    ActivityDefinitionCategoryOptions.education: ("409073007", "Education"),
    ActivityDefinitionCategoryOptions.surgical_procedure: (
        "387713003",
        "Surgical procedure",
    ),
}


class ServiceRequestMixin:
    @cache_profiles(ServiceRequest.get_resource_type())
    def _service_request(self, service_request: ServiceRequestModel):
        service_request_spec = ServiceRequestRetrieveSpec.serialize(service_request)
        id = str(service_request_spec.id)

        service_request_div_parts = [
            f"<p><b>Service Request:</b> {service_request_spec.title}</p>"
        ]
        service_request_div_parts.append(
            f"<p><b>Status:</b> {service_request_spec.status}</p>"
        )
        service_request_div_parts.append(
            f"<p><b>Intent:</b> {service_request_spec.intent}</p>"
        )
        if service_request_spec.priority:
            service_request_div_parts.append(
                f"<p><b>Priority:</b> {service_request_spec.priority}</p>"
            )
        if service_request_spec.category:
            service_request_div_parts.append(
                f"<p><b>Category:</b> {service_request_spec.category}</p>"
            )
        if service_request_spec.code:
            code_display = service_request_spec.code.get(
                "display"
            ) or service_request_spec.code.get("code", "")
            if code_display:
                service_request_div_parts.append(f"<p><b>Code:</b> {code_display}</p>")
        if service_request_spec.body_site:
            body_site_display = service_request_spec.body_site.get(
                "display"
            ) or service_request_spec.body_site.get("code", "")
            if body_site_display:
                service_request_div_parts.append(
                    f"<p><b>Body Site:</b> {body_site_display}</p>"
                )
        if service_request_spec.occurance:
            service_request_div_parts.append(
                f"<p><b>Occurrence:</b> {service_request_spec.occurance.isoformat()}</p>"
            )
        if service_request_spec.patient_instruction:
            service_request_div_parts.append(
                f"<p><b>Patient Instruction:</b> {service_request_spec.patient_instruction}</p>"
            )
        if service_request_spec.do_not_perform:
            service_request_div_parts.append("<p><b>Do Not Perform:</b> Yes</p>")
        if service_request_spec.note:
            service_request_div_parts.append(
                f"<p><b>Note:</b> {service_request_spec.note}</p>"
            )

        return ServiceRequest(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ServiceRequest"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(service_request_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=SERVICE_REQUEST_STATUS_CODE_MAP.get(
                service_request_spec.status, "unknown"
            ),
            intent=SERVICE_REQUEST_INTENT_CODE_MAP.get(
                service_request_spec.intent, "order"
            ),
            priority=SERVICE_REQUEST_PRIORITY_CODE_MAP.get(
                service_request_spec.priority, "routine"
            ),
            category=[
                self._concept_from_mapping(
                    system="http://snomed.info/sct",
                    mapping=SERVICE_REQUEST_CATEGORY_CODE_MAP,
                    key=service_request_spec.category,
                    default=ActivityDefinitionCategoryOptions.laboratory.value,
                )
            ]
            if service_request_spec.category
            else None,
            code=self._coding_to_codable_concept(service_request_spec.code),
            subject=self._reference(self._patient(service_request.patient)),
            encounter=self._reference(self._encounter(service_request.encounter)),
            occurrenceDateTime=service_request_spec.occurance.isoformat()
            if service_request_spec.occurance
            else None,
            requester=self._reference(self._practitioner(service_request.requester))
            if service_request.requester
            else self._reference(self._practitioner(service_request.created_by)),
            patientInstruction=service_request_spec.patient_instruction
            if service_request_spec.patient_instruction
            else None,
            bodySite=[self._coding_to_codable_concept(service_request_spec.body_site)]
            if service_request_spec.body_site
            else None,
            note=[Annotation(text=service_request_spec.note)]
            if service_request_spec.note
            else None,
            doNotPerform=service_request_spec.do_not_perform
            if service_request_spec.do_not_perform
            else None,
        )
