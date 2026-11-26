import logging

from django.db.models import Q
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.consent import ConsentRequestSerializer
from abdm.models import AbhaNumber
from abdm.models.abha_auth import normalize_abha_address
from abdm.models.consent import ConsentRequest
from abdm.service.abha import health_facility_for_facility, latest_valid_auth
from abdm.service.v3.gateway import GatewayService
from care.emr.models.organization import FacilityOrganizationUser
from config.auth_views import CaptchaRequiredException
from config.ratelimit import USER_READABLE_RATE_LIMIT_TIME, ratelimit

logger = logging.getLogger(__name__)


class ConsentRequestFilter(filters.FilterSet):
    patient = filters.UUIDFilter(method="filter_patient")
    encounter = filters.UUIDFilter(field_name="encounter__external_id")
    abha_address = filters.CharFilter(method="filter_abha_address")
    ordering = filters.OrderingFilter(
        fields=(
            "created_date",
            "updated_date",
        )
    )

    class Meta:
        model = ConsentRequest
        fields = ["patient", "encounter", "abha_address", "purpose", "hiu"]

    def filter_patient(self, queryset, name, value):
        # requests raised before auths existed may have no auth to go by
        return queryset.filter(
            Q(abha_auth__abha_number__patient__external_id=value)
            | Q(abha_auth__isnull=True, encounter__patient__external_id=value)
        )

    def filter_abha_address(self, queryset, name, value):
        return queryset.filter(
            abha_auth__abha_address=normalize_abha_address(value)
        )


@extend_schema(tags=["ABDM: Consent"])
class ConsentViewSet(GenericViewSet, ListModelMixin, RetrieveModelMixin):
    serializer_class = ConsentRequestSerializer
    model = ConsentRequest
    queryset = ConsentRequest.objects.all()
    permission_classes = (IsAuthenticated,)
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = ConsentRequestFilter

    def get_queryset(self):
        queryset = self.queryset
        facilities = [
            organization.organization.facility
            for organization in FacilityOrganizationUser.objects.filter(
                user=self.request.user
            ).select_related("organization__facility")
        ]

        return queryset.filter(encounter__facility__in=facilities).distinct()

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        encounter = serializer.validated_data["encounter"]
        health_facility = health_facility_for_facility(encounter.facility.external_id)

        # a consent request is raised against the ABHA address the patient last
        # verified at this facility, never one verified somewhere else
        abha_auth = latest_valid_auth(
            AbhaNumber.objects.filter(patient_id=encounter.patient_id).first(),
            health_facility,
        )
        if not abha_auth:
            return Response(
                {
                    "detail": "The patient's ABHA has not been verified at this facility, Please verify it to request records",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if ratelimit(request, "consent__create", [abha_auth.abha_address]):
            raise CaptchaRequiredException(
                detail={
                    "status": 429,
                    "detail": f"Request limit reached. Try after {USER_READABLE_RATE_LIMIT_TIME}",
                },
                code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        consent = ConsentRequest(
            **serializer.validated_data, requester=request.user, abha_auth=abha_auth
        )

        GatewayService.consent__request__init(
            {
                "consent": consent,
            }
        )
        consent.save()

        return Response(
            ConsentRequestSerializer(
                consent, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )
