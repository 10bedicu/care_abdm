from datetime import UTC, datetime
from html import escape

from django.conf import settings
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.practitioner import Practitioner, PractitionerQualification
from fhir.resources.R4B.reference import Reference

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


def _qualifications(user: UserModel):
    displays = {
        (degree.get("system"), degree.get("code")): degree.get("display")
        for degree in settings.USER_QUALIFICATION_DEGREES
    }
    qualifications = []
    for qualification in user.qualifications or []:
        degree = qualification.get("degree") or {}
        if not degree.get("code") or not degree.get("system"):
            continue
        qualifications.append(
            {
                "system": degree["system"],
                "code": degree["code"],
                "display": displays.get((degree["system"], degree["code"]))
                or degree["code"],
                "registration_number": (
                    qualification.get("registration_number") or ""
                ).strip(),
                "issuer": (qualification.get("issuer") or "").strip(),
            }
        )
    return qualifications


def _registration_identifier(registration_number: str, issuer: str):
    return Identifier(
        type=CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/v2-0203",
                    code="LN",
                    display="License number",
                )
            ],
            text="License number",
        ),
        value=registration_number,
        assigner=Reference(display=issuer) if issuer else None,
    )


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

        qualifications = _qualifications(user)
        for qualification in qualifications:
            details = ", ".join(
                filter(
                    None,
                    [
                        f"Reg. No. {qualification['registration_number']}"
                        if qualification["registration_number"]
                        else None,
                        qualification["issuer"],
                    ],
                )
            )
            practitioner_div_parts.append(
                f"<p><b>Qualification:</b> {escape(qualification['display'])}"
                + (f" ({escape(details)})" if details else "")
                + "</p>"
            )

        # One council registration often covers several degrees, so list it once
        registrations = list(
            dict.fromkeys(
                (qualification["registration_number"], qualification["issuer"])
                for qualification in qualifications
                if qualification["registration_number"]
            )
        )

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
                ),
                *[
                    _registration_identifier(registration_number, issuer)
                    for registration_number, issuer in registrations
                ],
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
            qualification=[
                PractitionerQualification(
                    identifier=[Identifier(value=qualification["registration_number"])]
                    if qualification["registration_number"]
                    else None,
                    code=CodeableConcept(
                        coding=[
                            Coding(
                                system=qualification["system"],
                                code=qualification["code"],
                                display=qualification["display"],
                            )
                        ],
                        text=qualification["display"],
                    ),
                    issuer=Reference(display=qualification["issuer"])
                    if qualification["issuer"]
                    else None,
                )
                for qualification in qualifications
            ]
            or None,
        )
