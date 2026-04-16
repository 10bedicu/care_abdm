from datetime import UTC, datetime

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.duration import Duration
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.range import Range
from fhir.resources.R4B.ratio import Ratio
from fhir.resources.R4B.timing import Timing, TimingRepeat

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.medication_request import (
    MedicationRequest as MedicationRequestModel,
)
from care.emr.resources.medication.request.spec import (
    DosageInstruction as DosageInstructionSpec,
)
from care.emr.resources.medication.request.spec import (
    DoseType as MedicationRequestDoseType,
)
from care.emr.resources.medication.request.spec import (
    MedicationRequestCategory,
    MedicationRequestIntent,
    MedicationRequestPriority,
    MedicationRequestReadSpec,
    MedicationRequestStatus,
)
from care.emr.resources.medication.request.spec import (
    StatusReason as MedicationRequestStatusReason,
)

MEDICATION_REQUEST_STATUS_CODE_MAP = {
    MedicationRequestStatus.active: "active",
    MedicationRequestStatus.on_hold: "on-hold",
    MedicationRequestStatus.cancelled: "cancelled",
    MedicationRequestStatus.completed: "completed",
    MedicationRequestStatus.entered_in_error: "entered-in-error",
    MedicationRequestStatus.stopped: "stopped",
    MedicationRequestStatus.draft: "draft",
    MedicationRequestStatus.unknown: "unknown",
}

MEDICATION_REQUEST_INTENT_CODE_MAP = {
    MedicationRequestIntent.proposal: "proposal",
    MedicationRequestIntent.plan: "plan",
    MedicationRequestIntent.order: "order",
    MedicationRequestIntent.original_order: "original-order",
    MedicationRequestIntent.reflex_order: "reflex-order",
    MedicationRequestIntent.filler_order: "filler-order",
    MedicationRequestIntent.instance_order: "instance-order",
}

MEDICATION_REQUEST_STATUS_REASON_CODE_MAP = {
    MedicationRequestStatusReason.alt_choice: (
        "altchoice",
        "Try another treatment first",
    ),
    MedicationRequestStatusReason.clarif: (
        "clarif",
        "Prescription requires clarification",
    ),
    MedicationRequestStatusReason.drughigh: ("drughigh", "Drug level too high"),
    MedicationRequestStatusReason.hospadm: ("hospadm", "Admission to hospital"),
    MedicationRequestStatusReason.labint: ("labint", "Lab interference issues"),
    MedicationRequestStatusReason.non_avail: ("non-avail", "Patient not available"),
    MedicationRequestStatusReason.preg: (
        "preg",
        "Parent is pregnant/breast feeding",
    ),
    MedicationRequestStatusReason.salg: ("salg", "Allergy"),
    MedicationRequestStatusReason.sddi: (
        "sddi",
        "Drug interacts with another drug",
    ),
    MedicationRequestStatusReason.sdupther: ("sdupther", "Duplicate therapy"),
    MedicationRequestStatusReason.sintol: ("sintol", "Suspected intolerance"),
    MedicationRequestStatusReason.surg: (
        "surg",
        "Patient scheduled for surgery",
    ),
    MedicationRequestStatusReason.washout: (
        "washout",
        "Waiting for old drug to wash out",
    ),
}

MEDICATION_REQUEST_PRIORITY_CODE_MAP = {
    MedicationRequestPriority.routine: "routine",
    MedicationRequestPriority.urgent: "urgent",
    MedicationRequestPriority.asap: "asap",
    MedicationRequestPriority.stat: "stat",
}

MEDICATION_REQUEST_CATEGORY_CODE_MAP = {
    MedicationRequestCategory.inpatient: ("inpatient", "Inpatient"),
    MedicationRequestCategory.outpatient: ("outpatient", "Outpatient"),
    MedicationRequestCategory.community: ("community", "Community"),
    MedicationRequestCategory.discharge: ("discharge", "Discharge"),
}

MEDICATION_REQUEST_DOSE_RATE_TYPE_CODE_MAP = {
    MedicationRequestDoseType.calculated: ("calculated", "Calculated"),
    MedicationRequestDoseType.ordered: ("ordered", "Ordered"),
}


class MedicationRequestMixin:
    @cache_profiles(MedicationRequest.get_resource_type())
    def _medication_request(self, request: MedicationRequestModel):
        request_spec = MedicationRequestReadSpec.serialize(request)
        id = str(request_spec.id)

        medication_name = (
            (
                request_spec.requested_product.get("name")
                if request_spec.requested_product
                else None
            )
            or (
                request_spec.medication.get("display")
                or request_spec.medication.get("code")
                if request_spec.medication
                else None
            )
            or "Medication Request"
        )

        med_req_div_parts = [f"<p><b>Medication:</b> {medication_name}</p>"]
        med_req_div_parts.append(f"<p><b>Status:</b> {request_spec.status}</p>")
        med_req_div_parts.append(f"<p><b>Intent:</b> {request_spec.intent}</p>")
        if request_spec.priority:
            med_req_div_parts.append(f"<p><b>Priority:</b> {request_spec.priority}</p>")
        if request_spec.category:
            med_req_div_parts.append(f"<p><b>Category:</b> {request_spec.category}</p>")
        if request_spec.status_reason:
            med_req_div_parts.append(
                f"<p><b>Status Reason:</b> {request_spec.status_reason}</p>"
            )
        if request_spec.note:
            med_req_div_parts.append(f"<p><b>Note:</b> {request_spec.note}</p>")

        return MedicationRequest(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/MedicationRequest"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(med_req_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status=MEDICATION_REQUEST_STATUS_CODE_MAP.get(
                request_spec.status, "unknown"
            ),
            statusReason=self._concept_from_mapping(
                system="http://terminology.hl7.org/CodeSystem/medicationrequest-status-reason",
                mapping=MEDICATION_REQUEST_STATUS_REASON_CODE_MAP,
                key=request_spec.status_reason,
                default=MedicationRequestStatusReason.alt_choice.value,
            )
            if request_spec.status_reason
            else None,
            intent=MEDICATION_REQUEST_INTENT_CODE_MAP.get(request_spec.intent, "order"),
            category=[
                self._concept_from_mapping(
                    system="http://terminology.hl7.org/CodeSystem/medicationrequest-category",
                    mapping=MEDICATION_REQUEST_CATEGORY_CODE_MAP,
                    key=request_spec.category,
                    default=MedicationRequestCategory.inpatient.value,
                )
            ],
            priority=MEDICATION_REQUEST_PRIORITY_CODE_MAP.get(
                request_spec.priority, "routine"
            ),
            authoredOn=request_spec.created_date.isoformat(),
            dosageInstruction=[
                Dosage(
                    sequence=dosage_spec.sequence,
                    text=dosage_spec.text,
                    patientInstruction=dosage_spec.patient_instruction,
                    additionalInstruction=[
                        self._coding_to_codable_concept(instruction)
                        for instruction in dosage_spec.additional_instruction
                    ]
                    if dosage_spec.additional_instruction
                    else None,
                    asNeededCodeableConcept=self._coding_to_codable_concept(
                        dosage_spec.as_needed_for
                    ),
                    timing=Timing(
                        repeat=TimingRepeat(
                            frequency=dosage_spec.timing.repeat.frequency,
                            period=dosage_spec.timing.repeat.period,
                            periodUnit=dosage_spec.timing.repeat.period_unit,
                            boundsDuration=Duration(
                                value=dosage_spec.timing.repeat.bounds_duration.value,
                                unit=dosage_spec.timing.repeat.bounds_duration.unit,
                            )
                            if dosage_spec.timing.repeat.bounds_duration
                            else None,
                        )
                        if dosage_spec.timing.repeat
                        else None,
                        code=self._coding_to_codable_concept(dosage_spec.timing.code),
                    )
                    if dosage_spec.timing
                    else None,
                    site=self._coding_to_codable_concept(dosage_spec.site),
                    route=self._coding_to_codable_concept(dosage_spec.route),
                    method=self._coding_to_codable_concept(dosage_spec.method),
                    doseAndRate=[
                        DosageDoseAndRate(
                            type=self._concept_from_mapping(
                                system="http://terminology.hl7.org/CodeSystem/dose-rate-type",
                                mapping=MEDICATION_REQUEST_DOSE_RATE_TYPE_CODE_MAP,
                                key=dosage_spec.dose_and_rate.type,
                                default=MedicationRequestDoseType.ordered.value,
                            ),
                            doseRange=Range(
                                low=Quantity(
                                    value=dosage_spec.dose_and_rate.dose_range.low.value,
                                    unit=dosage_spec.dose_and_rate.dose_range.low.unit.display,
                                    system=dosage_spec.dose_and_rate.dose_range.low.unit.system,
                                    code=dosage_spec.dose_and_rate.dose_range.low.unit.code,
                                )
                                if dosage_spec.dose_and_rate.dose_range.low
                                else None,
                                high=Quantity(
                                    value=dosage_spec.dose_and_rate.dose_range.high.value,
                                    unit=dosage_spec.dose_and_rate.dose_range.high.unit.display,
                                    system=dosage_spec.dose_and_rate.dose_range.high.unit.system,
                                    code=dosage_spec.dose_and_rate.dose_range.high.unit.code,
                                )
                                if dosage_spec.dose_and_rate.dose_range.high
                                else None,
                            )
                            if dosage_spec.dose_and_rate.dose_range
                            else None,
                            doseQuantity=Quantity(
                                value=dosage_spec.dose_and_rate.dose_quantity.value,
                                unit=dosage_spec.dose_and_rate.dose_quantity.unit.display,
                                system=dosage_spec.dose_and_rate.dose_quantity.unit.system,
                                code=dosage_spec.dose_and_rate.dose_quantity.unit.code,
                            )
                            if dosage_spec.dose_and_rate.dose_quantity
                            else None,
                        )
                    ],
                    maxDosePerPeriod=Ratio(
                        numerator=Quantity(
                            value=dosage_spec.max_dose_per_period.low.value,
                            unit=dosage_spec.max_dose_per_period.low.unit.display,
                            system=dosage_spec.max_dose_per_period.low.unit.system,
                            code=dosage_spec.max_dose_per_period.low.unit.code,
                        )
                        if dosage_spec.max_dose_per_period.low
                        else None,
                        denominator=Quantity(
                            value=dosage_spec.max_dose_per_period.high.value,
                            unit=dosage_spec.max_dose_per_period.high.unit.display,
                            system=dosage_spec.max_dose_per_period.high.unit.system,
                            code=dosage_spec.max_dose_per_period.high.unit.code,
                        )
                        if dosage_spec.max_dose_per_period.high
                        else None,
                    )
                    if dosage_spec.max_dose_per_period
                    else None,
                )
                for dosage in request_spec.dosage_instruction
                for dosage_spec in [DosageInstructionSpec(**dosage)]
            ],
            note=[Annotation(text=request_spec.note)] if request_spec.note else None,
            medicationCodeableConcept=CodeableConcept(
                coding=[
                    Coding(
                        **(
                            request_spec.medication
                            or (
                                request_spec.requested_product.get("code", {})
                                if request_spec.requested_product
                                else {}
                            )
                        )
                    )
                ],
                text=request_spec.requested_product.get("name")
                if request_spec.requested_product
                else (request_spec.medication or {}).get("display"),
            ),
            subject=self._reference(self._patient(request.patient)),
            requester=self._reference(self._practitioner(request.created_by)),
        )
