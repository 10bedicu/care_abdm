import json
import logging

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.models import Transaction, TransactionType
from abdm.models.consent import ConsentArtefact, ConsentRequest
from abdm.settings import plugin_settings as settings
from care.emr.models.file_upload import FileUpload
from care.emr.resources.file_upload.spec import FileCategoryChoices, FileTypeChoices
from care.security.authorization.base import AuthorizationController

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: Health Information"])
class HealthInformationViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    def _get_consent_for_pk(self, pk):
        consent = ConsentArtefact.objects.filter(external_id=pk).first()
        if consent is None:
            consent = ConsentRequest.objects.filter(external_id=pk).first()
        if consent is None:
            return None, None

        patient = consent.patient_abha.patient if consent.patient_abha else None
        return consent, patient

    def retrieve(self, request, pk):
        _, patient = self._get_consent_for_pk(pk)
        if patient is None:
            raise NotFound("No Health Information found for the given id")

        if not AuthorizationController.call(
            "can_view_patient_obj", request.user, patient
        ):
            raise PermissionDenied(
                "You do not have permission to view this patient's health information"
            )

        files = FileUpload.objects.filter(
            Q(internal_name__contains=f"{pk}.json") | Q(associating_id=pk),
            file_type=FileTypeChoices.patient.value,
            file_category=FileCategoryChoices.unspecified.value,
            upload_completed=True,
            created_by__username=settings.ABDM_USERNAME,
        )

        if files.count() == 0:
            raise NotFound("No Health Information found for the given id")

        if files.count() == 1 and files.first().is_archived:
            archived_file = files.first()
            raise NotFound(
                detail={
                    "is_archived": True,
                    "archived_reason": archived_file.archive_reason,
                    "archived_time": archived_file.archived_datetime,
                    "detail": (
                        f"This file has been archived as {archived_file.archive_reason} "
                        f"at {archived_file.archived_datetime}"
                    ),
                }
            )

        files = files.filter(is_archived=False)

        data = []
        for file in files:
            _, content = file.files_manager.file_contents(file)
            data.extend(json.loads(content))

        Transaction.objects.create(
            reference_id=pk,  # consent_arefact.external_id | consent_request.external_id
            type=TransactionType.ACCESS_DATA,
            created_by=request.user,
        )

        return Response({"data": data}, status=status.HTTP_200_OK)
