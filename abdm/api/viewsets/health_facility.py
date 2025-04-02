from celery import shared_task
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.health_facility import HealthFacilitySerializer
from abdm.models import HealthFacility
from abdm.service.v3.facility import FacilityService
from abdm.settings import plugin_settings as settings
from care.emr.models.organization import FacilityOrganizationUser


@shared_task
def register_health_facility_as_service(facility_external_id):
    health_facility = HealthFacility.objects.filter(
        facility__external_id=facility_external_id
    ).first()

    if not health_facility:
        return [False, "Health Facility Not Found"]

    if health_facility.registered:
        return [True, None]

    response = FacilityService.add_update_service(
        {
            "health_facility": health_facility,
        }
    )

    if response.status_code == 200:
        data = response.json()[0]

        if "error" in data:
            if (
                data["error"].get("code") == "2500"
                and settings.ABDM_CLIENT_ID in data["error"].get("message")
                and "already associated" in data["error"].get("message")
            ):
                health_facility.registered = True
                health_facility.save()
                return [True, None]

            return [
                False,
                data["error"].get("message", "Error while registering HIP as service"),
            ]

        if "servicesLinked" in data:
            health_facility.registered = True
            health_facility.save()
            return [True, None]

    return [False, None]


@extend_schema(tags=["ABDM: Health Facility"])
class HealthFacilityViewSet(
    GenericViewSet,
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
):
    serializer_class = HealthFacilitySerializer
    model = HealthFacility
    queryset = HealthFacility.objects.all()
    permission_classes = (IsAuthenticated,)
    lookup_field = "facility__external_id"

    def get_queryset(self):
        queryset = self.queryset
        facilities = [
            organization.organization.facility
            for organization in FacilityOrganizationUser.objects.filter(
                user=self.request.user
            ).select_related("organization__facility")
        ]

        return queryset.filter(facility__in=facilities)

    @action(detail=True, methods=["POST"])
    def register_service(self, request, facility__external_id):
        [registered, error] = register_health_facility_as_service(facility__external_id)

        if error:
            return Response({"detail": error}, status=400)

        return Response({"registered": registered})

    def perform_create(self, serializer):
        instance = serializer.save()
        register_health_facility_as_service.delay(instance.facility.external_id)

    def perform_update(self, serializer):
        serializer.validated_data["registered"] = False
        instance = serializer.save()
        register_health_facility_as_service.delay(instance.facility.external_id)
