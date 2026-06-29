import base64
from datetime import UTC, datetime

from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
)
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.meta import Meta
from fhir.resources.R4B.narrative import Narrative

from abdm.utils.fhir.base import cache_profiles
from care.emr.models.file_upload import FileUpload as FileUploadModel


class DocumentReferenceMixin:
    @cache_profiles(DocumentReference.get_resource_type())
    def _document_reference(self, file: FileUploadModel):
        id = str(file.external_id)

        doc_ref_div_parts = [
            f"<p><b>Document:</b> {file.name or file.internal_name}</p>"
        ]
        doc_ref_div_parts.append("<p><b>Status:</b> current</p>")
        if file.file_type:
            doc_ref_div_parts.append(f"<p><b>Type:</b> {file.file_type}</p>")
        if file.file_category:
            doc_ref_div_parts.append(f"<p><b>Category:</b> {file.file_category}</p>")

        return DocumentReference(
            id=id,
            meta=Meta(
                versionId="1",
                lastUpdated=datetime.now(UTC).isoformat(),
                profile=[
                    "https://nrces.in/ndhm/fhir/r4/StructureDefinition/DocumentReference"
                ],
            ),
            text=Narrative(
                status="generated",
                div='<div xmlns="http://www.w3.org/1999/xhtml">'
                + "".join(doc_ref_div_parts)
                + "</div>",
            ),
            identifier=[Identifier(value=id)],
            status="current",
            type=CodeableConcept(text=file.internal_name.split(".")[0]),
            content=[DocumentReferenceContent(attachment=self._attachment(file))],
            author=[self._reference(self._practitioner(file.created_by))],
        )

    def _attachment(self, file: FileUploadModel):
        content_type, content = file.files_manager.file_contents(file)

        return Attachment(
            contentType=content_type,
            data=base64.b64encode(content),
            title=file.name,
            creation=file.created_date.isoformat(),
        )
