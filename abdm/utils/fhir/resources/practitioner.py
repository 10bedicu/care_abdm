from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.practitioner import Practitioner

from abdm.utils.fhir.base import cache_profiles
from care.emr.resources.patient.spec import GenderChoices
from care.emr.resources.user.spec import UserRetrieveSpec
from care.users.models import User as UserModel

GENDER_MAP = {
    GenderChoices.male: "male",
    GenderChoices.female: "female",
    GenderChoices.non_binary: "other",
    GenderChoices.transgender: "other",
}


class PractitionerMixin:
    @cache_profiles(Practitioner.get_resource_type())
    def _practitioner(self, user: UserModel):
        user_spec = UserRetrieveSpec.serialize(user)
        id = str(user_spec.id)

        practitioner_div_parts = [
            f"<p><b>Name:</b> {user.full_name or user.username}</p>"
        ]
        if user_spec.gender:
            practitioner_div_parts.append(f"<p><b>Gender:</b> {user_spec.gender}</p>")
        if user.date_of_birth:
            practitioner_div_parts.append(
                f"<p><b>Date of Birth:</b> {user.date_of_birth}</p>"
            )
        if user_spec.phone_number:
            practitioner_div_parts.append(
                f"<p><b>Phone:</b> {user_spec.phone_number}</p>"
            )
        if user_spec.email:
            practitioner_div_parts.append(f"<p><b>Email:</b> {user_spec.email}</p>")

        return Practitioner(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Practitioner"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(practitioner_div_parts)
                + "</div>",
            ),
            identifier=[
                Identifier(
                    value=id,
                    type=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0203",
                                code="PRN",
                                display="Provider number",
                            )
                        ],
                        text="Provider number",
                    ),
                )
            ],
            name=[HumanName(text=user.full_name or user.username)],
            telecom=[
                *(
                    [ContactPoint(system="phone", value=user_spec.phone_number)]
                    if user_spec.phone_number
                    else []
                ),
                *(
                    [ContactPoint(system="email", value=user_spec.email)]
                    if user_spec.email
                    else []
                ),
            ],
            gender=GENDER_MAP.get(user_spec.gender, "unknown")
            if user_spec.gender
            else None,
            birthDate=user.date_of_birth,
        )
