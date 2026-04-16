from datetime import UTC, datetime

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.patient import Patient

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.patient import Patient as PatientModel
from care.emr.resources.patient.spec import PatientRetrieveSpec


class PatientMixin:
    @cache_profiles(Patient.get_resource_type())
    def _patient(self, patient: PatientModel):
        patient_spec = PatientRetrieveSpec.serialize(patient)
        id = str(patient_spec.id)

        address = []
        if patient_spec.address:
            address.append(
                Address(
                    line=[patient_spec.address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                )
            )
        if (
            patient_spec.permanent_address
            and patient_spec.permanent_address != patient_spec.address
        ):
            address.append(
                Address(
                    line=[patient_spec.permanent_address],
                    postalCode=patient_spec.pincode,
                    country="IN",
                )
            )

        patient_div_parts = [f"<p><b>Name:</b> {patient_spec.name}</p>"]
        if patient_spec.gender:
            patient_div_parts.append(f"<p><b>Gender:</b> {patient_spec.gender}</p>")
        birth_date = (
            getattr(patient.abha_number, "parsed_date_of_birth", None)
            if patient.abha_number
            else None
        )
        if birth_date:
            patient_div_parts.append(f"<p><b>Date of Birth:</b> {birth_date}</p>")
        if patient_spec.phone_number:
            patient_div_parts.append(
                f"<p><b>Phone:</b> {patient_spec.phone_number}</p>"
            )
        if patient_spec.emergency_phone_number:
            patient_div_parts.append(
                f"<p><b>Emergency Phone:</b> {patient_spec.emergency_phone_number}</p>"
            )
        if patient_spec.address:
            patient_div_parts.append(f"<p><b>Address:</b> {patient_spec.address}</p>")

        return Patient(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=["https://nrces.in/ndhm/fhir/r4/StructureDefinition/Patient"],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(patient_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            name=[HumanName(text=patient_spec.name)],
            telecom=[
                *(
                    [ContactPoint(system="phone", value=patient_spec.phone_number)]
                    if patient_spec.phone_number
                    else []
                ),
                *(
                    [
                        ContactPoint(
                            system="phone",
                            value=patient_spec.emergency_phone_number,
                        )
                    ]
                    if patient_spec.emergency_phone_number
                    else []
                ),
            ],
            gender=patient_spec.gender,
            birthDate=patient.abha_number.parsed_date_of_birth,
            address=address or None,
        )
