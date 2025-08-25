import base64
import json
from logging import getLogger

import magic
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from abdm.authentication import IsPhrAuthenticated, PhrCustomAuthentication
from abdm.models import ConsentArtefact, Transaction, TransactionType
from abdm.models.base import Status
from abdm.settings import plugin_settings
from care.emr.api.viewsets.base import (
    EMRBaseViewSet,
    EMRListMixin,
    EMRRetrieveMixin,
)
from care.emr.api.viewsets.file_upload import FileUploadFilter
from care.emr.models.file_upload import FileUpload
from care.emr.resources.file_upload.spec import (
    FileCategoryChoices,
    FileTypeChoices,
    FileUploadCreateSpec,
    FileUploadListSpec,
    FileUploadRetrieveSpec,
)

logger = getLogger(__name__)


@extend_schema(tags=["PHR Health Records"])
class PhrHealthRecordsViewSet(EMRBaseViewSet, EMRListMixin, EMRRetrieveMixin):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]
    database_model = FileUpload
    pydantic_retrieve_model = FileUploadRetrieveSpec
    pydantic_read_model = FileUploadListSpec
    filterset_class = FileUploadFilter
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        if self.action == "list":
            file_type = self.request.GET.get("file_type")
            associating_id = self.request.GET.get("associating_id")

            if not file_type or not associating_id:
                raise ValidationError("file_type and associating_id are required")

            return (
                super()
                .get_queryset()
                .filter(
                    file_type=file_type,
                    associating_id=associating_id,
                    upload_completed=True,
                )
            )

        return super().get_queryset()

    @action(detail=False, methods=["post"], url_path="upload/file")
    def phr_health__records_upload_file(self, request):
        file_name = request.data.get("original_name")
        file_data = request.data.get("file_data")

        if not file_name or not file_data:
            raise ValidationError(
                "Missing required fields: 'original_name' or 'file_data'"
            )

        try:
            file_content = base64.b64decode(file_data)
        except Exception as e:
            error = "Invalid base64-encoded file data"
            raise ValidationError(error) from e

        uploaded_file = ContentFile(file_content, name=file_name)

        max_file_size = settings.MAX_FILE_UPLOAD_SIZE * 1024 * 1024
        if uploaded_file.size > max_file_size:
            error = f"File size exceeds the limit of {max_file_size / (1024 * 1024)}MB"
            raise ValidationError(error)

        try:
            mime_type = magic.from_buffer(file_content[:2048], mime=True)
        except Exception as e:
            error = "Error detecting file type."
            raise ValidationError(error) from e

        if mime_type not in settings.ALLOWED_MIME_TYPES:
            error = f"File type '{mime_type}' is not allowed"
            raise ValidationError(error)

        request_data = {
            "original_name": file_name,
            "name": request.data.get("name"),
            "associating_id": request.data.get("associating_id"),
            "file_type": request.data.get("file_type"),
            "file_category": request.data.get("file_category"),
            "mime_type": mime_type,
        }

        with transaction.atomic():
            file_upload = FileUploadCreateSpec(**request_data).de_serialize()
            file_upload.save()

            try:
                file_upload.files_manager.put_object(file_upload, uploaded_file)
                file_upload.upload_completed = True
                file_upload.save(skip_internal_name=True)
            except Exception as e:
                error_msg = "Failed to upload file to storage"
                raise ValidationError(error_msg) from e

        # TODO: RAISE A CARE CONTEXT REQUEST

        return Response(status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        name = request.data.get("name")
        if not name:
            raise ValidationError("name is required")

        obj = self.get_object()
        obj.name = name
        obj.save(update_fields=["name"])
        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="linked/(?P<pk>[^/.]+)")
    def phr_linked__health__records(self, request, pk):
        consent = (
            ConsentArtefact.objects.filter(
                Q(hip_id=pk),
                Q(patient_abha_address=request.user.abha_address),
                Q(status=Status.GRANTED.value),
            )
            .order_by("-created_date")
            .first()
        )

        files = FileUpload.objects.filter(
            Q(internal_name__contains=f"{pk}.json") | Q(associating_id=pk),
            file_type=FileTypeChoices.patient.value,
            file_category=FileCategoryChoices.unspecified.value,
            upload_completed=True,
            created_by__username=plugin_settings.ABDM_USERNAME,
        )

        if files.count() == 0:
            return Response(
                {"detail": "No Health Information found for the given id"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if files.count() == 1 and files.first().is_archived:
            return Response(
                {
                    "is_archived": True,
                    "archived_reason": files.first().archive_reason,
                    "archived_time": files.first().archived_datetime,
                    "detail": f"This file has been archived as {files.first().archive_reason} at {files.first().archived_datetime}",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        files = files.filter(is_archived=False)

        contents = []
        for file in files:
            if file.upload_completed:
                _, content = file.files_manager.file_contents(file)
                contents.extend(content)

        Transaction.objects.create(
            reference_id=pk,  # consent_arefact.external_id | consent_request.external_id
            type=TransactionType.ACCESS_DATA,
            created_by=request.user.abha_address,
        )

        return Response({"data": json.loads(content)}, status=status.HTTP_200_OK)
