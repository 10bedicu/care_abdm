import json
import uuid
from unittest.mock import patch

from django.urls import reverse
from model_bakery import baker
from rest_framework import status

from abdm.models import AbhaNumber
from abdm.models.base import Status
from abdm.models.consent import ConsentArtefact, ConsentRequest
from abdm.utils import user as abdm_user_module
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.file_upload import FileUpload
from care.emr.resources.file_upload.spec import FileCategoryChoices, FileTypeChoices
from care.emr.utils.file_manager import S3FilesManager
from care.security.permissions.patient import PatientPermissions
from care.utils.tests.base import CareAPITestBase


class HealthInformationAPITest(CareAPITestBase):
    def setUp(self):
        super().setUp()
        abdm_user_module.ABDM_USER = None
        self.authorized_user = self.create_user()
        self.unauthorized_user = self.create_user()
        self.facility = self.create_facility(user=self.authorized_user)
        self.facility_organization = self.create_facility_organization(
            facility=self.facility
        )
        self.patient = self.create_patient()
        self.encounter = self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.facility_organization,
        )
        self.encounter.sync_organization_cache()
        self.abha_number = baker.make(
            AbhaNumber,
            patient=self.patient,
            health_id=f"health-{uuid.uuid4()}",
        )
        self.consent_id = uuid.uuid4()
        self.consent_request = ConsentRequest.objects.create(
            consent_id=self.consent_id,
            patient_abha=self.abha_number,
            encounter=self.encounter,
            status=Status.GRANTED.value,
        )
        self.consent_artefact = ConsentArtefact.objects.create(
            consent_id=uuid.uuid4(),
            consent_request=self.consent_request,
            patient_abha=self.abha_number,
            encounter=self.encounter,
            status=Status.GRANTED.value,
        )
        self.abdm_user = get_or_create_abdm_user()
        self._create_health_information_files()
        self._grant_patient_access(self.authorized_user)

    def _grant_patient_access(self, user):
        role = self.create_role_with_permissions(
            permissions=[PatientPermissions.can_list_patients.name]
        )
        self.attach_role_facility_organization_user(
            self.facility_organization, user, role
        )

    def _create_health_information_files(self):
        self.file_entries = [
            [{"content": "entry-1", "care_context_reference": "ctx-1"}],
            [{"content": "entry-2", "care_context_reference": "ctx-2"}],
        ]
        for index, entries in enumerate(self.file_entries, start=1):
            file = FileUpload(
                name=f"health-information-{index}",
                internal_name=(
                    f"{index} / 2 -- {self.consent_artefact.external_id}.json"
                ),
                associating_id=str(self.consent_request.external_id),
                file_type=FileTypeChoices.patient.value,
                file_category=FileCategoryChoices.unspecified.value,
                upload_completed=True,
                created_by=self.abdm_user,
            )
            file.save(skip_internal_name=True)
            entries_by_internal_name = getattr(self, "_entries_by_internal_name", {})
            entries_by_internal_name[file.internal_name] = entries
            self._entries_by_internal_name = entries_by_internal_name

    def _mock_file_contents(self, file_obj):
        entries = self._entries_by_internal_name[file_obj.internal_name]
        return ("application/json", json.dumps(entries).encode())

    def _get_url(self, pk):
        return reverse("abdm__health_information-detail", kwargs={"pk": pk})

    def test_retrieve_unauthenticated(self):
        response = self.client.get(
            self._get_url(self.consent_request.external_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_unauthorized(self):
        self.client.force_authenticate(user=self.unauthorized_user)
        response = self.client.get(
            self._get_url(self.consent_request.external_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("permission", response.data["detail"].lower())

    def test_retrieve_unknown_id(self):
        self.client.force_authenticate(user=self.authorized_user)
        response = self.client.get(
            self._get_url(uuid.uuid4()),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch.object(S3FilesManager, "file_contents")
    def test_retrieve_authorized_aggregates_files(self, mock_file_contents):
        mock_file_contents.side_effect = self._mock_file_contents
        self.client.force_authenticate(user=self.authorized_user)
        response = self.client.get(
            self._get_url(self.consent_request.external_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"],
            self.file_entries[0] + self.file_entries[1],
        )

    @patch.object(S3FilesManager, "file_contents")
    def test_retrieve_by_consent_request_id(self, mock_file_contents):
        mock_file_contents.side_effect = self._mock_file_contents
        self.client.force_authenticate(user=self.authorized_user)
        response = self.client.get(
            self._get_url(self.consent_request.external_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 2)

    @patch.object(S3FilesManager, "file_contents")
    def test_retrieve_by_consent_artefact_id(self, mock_file_contents):
        mock_file_contents.side_effect = self._mock_file_contents
        self.client.force_authenticate(user=self.authorized_user)
        response = self.client.get(
            self._get_url(self.consent_artefact.external_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 2)
