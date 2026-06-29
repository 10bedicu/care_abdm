from datetime import UTC, datetime

from fhir.resources.R4B.address import Address
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative
from fhir.resources.R4B.organization import Organization

from abdm.models.health_facility import HealthFacility as HealthFacilityModel
from abdm.settings import plugin_settings as settings
from abdm.utils.fhir.base import cache_profiles
from care.emr.resources.facility.spec import FacilityRetrieveSpec
from care.facility.models import Facility as FacilityModel

CARE_IDENTIFIER_SYSTEM = settings.BACKEND_DOMAIN


class OrganizationMixin:
    @cache_profiles(Organization.get_resource_type())
    def _organization(self, facility: FacilityModel):
        health_facility = HealthFacilityModel.objects.filter(facility=facility).first()
        facility_spec = FacilityRetrieveSpec.serialize(facility)
        id = str(facility_spec.id)
        hf_id = health_facility.hf_id if health_facility else None

        organization_div_parts = [
            f"<p><b>Name:</b> {facility_spec.name}</p>",
            "<p><b>Type:</b> Healthcare Provider</p>",
        ]
        if facility_spec.phone_number:
            organization_div_parts.append(
                f"<p><b>Phone:</b> {facility_spec.phone_number}</p>"
            )
        if facility_spec.address:
            address_text = facility_spec.address
            if facility_spec.pincode:
                address_text += f", {facility_spec.pincode}"
            organization_div_parts.append(f"<p><b>Address:</b> {address_text}, IN</p>")

        return Organization(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/Organization"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(organization_div_parts)
                + "</div>",
            ),
            identifier=[
                Identifier(
                    system=(
                        "https://facility.ndhm.gov.in"
                        if hf_id
                        else f"{CARE_IDENTIFIER_SYSTEM}/facility"
                    ),
                    value=hf_id or id,
                    type=CodeableConcept(
                        coding=[
                            Coding(
                                system="http://terminology.hl7.org/CodeSystem/v2-0203",
                                code="FI",
                                display="Facility ID",
                            )
                        ],
                        text="Facility ID",
                    ),
                )
            ],
            type=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/organization-type",
                            code="prov",
                            display="Healthcare Provider",
                        )
                    ],
                    text="Healthcare Provider",
                )
            ],
            name=facility_spec.name,
            telecom=[
                *(
                    [ContactPoint(system="phone", value=facility_spec.phone_number)]
                    if facility_spec.phone_number
                    else []
                )
            ],
            address=[
                Address(
                    line=[facility_spec.address],
                    postalCode=facility_spec.pincode,
                    country="IN",
                )
            ],
        )
