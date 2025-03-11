import logging

from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.consent import ConsentRequestSerializer
from abdm.models.consent import ConsentRequest
from abdm.service.v3.gateway import GatewayService
from care.emr.models.organization import FacilityOrganizationUser
from config.auth_views import CaptchaRequiredException
from config.ratelimit import USER_READABLE_RATE_LIMIT_TIME, ratelimit

logger = logging.getLogger(__name__)


class ConsentRequestFilter(filters.FilterSet):
    patient = filters.UUIDFilter(field_name="patient_abha__patient__external_id")
    health_id = filters.CharFilter(field_name="patient_abha__health_id")
    ordering = filters.OrderingFilter(
        fields=(
            "created_date",
            "updated_date",
        )
    )
    facility = filters.UUIDFilter(
        field_name="patient_abha__patient__facility__external_id"
    )

    class Meta:
        model = ConsentRequest
        fields = ["patient", "health_id", "purpose"]


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

        return queryset.filter(requester__facility__in=facilities).distinct()

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if ratelimit(
            request, "consent__create", [serializer.validated_data["patient_abha"]]
        ):
            raise CaptchaRequiredException(
                detail={
                    "status": 429,
                    "detail": f"Request limit reached. Try after {USER_READABLE_RATE_LIMIT_TIME}",
                },
                code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        consent = ConsentRequest(**serializer.validated_data, requester=request.user)

        GatewayService.consent__request__init(
            {
                "consent": consent,
            }
        )
        consent.save()

        return Response(
            ConsentRequestSerializer(consent).data, status=status.HTTP_201_CREATED
        )
