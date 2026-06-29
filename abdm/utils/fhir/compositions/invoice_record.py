from datetime import UTC, datetime

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.composition import (
    Composition,
    CompositionAttester,
    CompositionSection,
)
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta

from abdm.service.helper import uuid
from care.emr.models.invoice import Invoice as InvoiceModel


class InvoiceRecordCompositionMixin:
    def _invoice_record_composition(self, invoice: InvoiceModel, care_context_id: str):
        invoice_resource = self._invoice(invoice)
        primary_encounter = getattr(invoice.account, "primary_encounter", None)

        organization = self._organization(invoice.facility)
        author_user = invoice.created_by
        author = (
            self._reference(self._practitioner(author_user))
            if author_user
            else self._reference(organization)
        )

        section_title = invoice.title or "Invoice Details"

        return Composition(
            id=care_context_id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InvoiceRecord"
                ],
            ),
            identifier=Identifier(value=care_context_id),
            status="final",
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://snomed.info/sct",
                        code="371530004",
                        display="Invoice Record",
                    )
                ],
                text="Invoice Record",
            ),
            subject=self._reference(self._patient(invoice.patient)),
            encounter=self._reference(self._encounter(primary_encounter))
            if primary_encounter
            else None,
            date=(invoice.issue_date or invoice.modified_date).isoformat(),
            author=[author],
            title=invoice.title or "Invoice Record",
            custodian=self._reference(organization),
            attester=[
                CompositionAttester(
                    mode="official", party=self._reference(organization)
                )
            ],
            section=[
                CompositionSection(
                    title=section_title,
                    entry=[self._reference(invoice_resource, reference_type="Invoice")],
                )
            ],
        )

    def create_invoice_record(
        self,
        invoice: InvoiceModel,
        care_context_id: str = uuid(),
    ):
        return self._bundle(
            entries=[
                self._bundle_entry(
                    self._invoice_record_composition(invoice, care_context_id)
                ),
                *[self._bundle_entry(profile) for profile in self.cached_profiles()],
            ],
            care_context_id=care_context_id,
        )
