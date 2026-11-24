from uuid import UUID

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.abha_number import (
    AbhaNumberCreateSerializer,
    AbhaNumberSerializer,
)
from abdm.models import (
    AbhaAuthMethod,
    AbhaNumber,
    HealthFacility,
    Transaction,
    TransactionType,
)
from abdm.service.abha import (
    find_abha_number,
    health_facility_for_facility,
    record_abha_auth,
)
from abdm.service.helper import uuid
from care.security.authorization.base import AuthorizationController


def _as_uuid(value) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


@extend_schema(tags=["ABDM: ABHA Number"])
class AbhaNumberViewSet(
    GenericViewSet,
    RetrieveModelMixin,
):
    serializer_class = AbhaNumberSerializer
    model = AbhaNumber
    queryset = AbhaNumber.objects.all()
    permission_classes = (IsAuthenticated,)

    def get_serializer_class(self):
        if self.action == "create":
            return AbhaNumberCreateSerializer

        return super().get_serializer_class()

    def get_serializer_context(self):
        context = super().get_serializer_context()

        # the facility the ABHA record is being looked at from
        facility = _as_uuid(self.request.query_params.get("facility"))
        if facility:
            context["health_facility"] = HealthFacility.objects.filter(
                facility_id=facility
            ).first()

        return context

    def get_object(self):
        id = self.kwargs.get("pk")

        instance = None
        if _as_uuid(id):
            instance = self.queryset.filter(patient__external_id=id).first()
        if not instance:
            instance = find_abha_number(id)

        if not instance:
            raise Http404

        if not AuthorizationController.call(
            "can_view_patient_obj", self.request.user, instance.patient
        ):
            raise Http404

        self.check_object_permissions(self.request, instance)

        return instance

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = dict(serializer.validated_data)

        health_facility = health_facility_for_facility(profile.pop("facility"))

        instance, _, _ = record_abha_auth(
            abha_number=profile.pop("abha_number", None),
            abha_address=profile.pop("abha_address", None),
            profile=profile,
            health_facility=health_facility,
            method=AbhaAuthMethod.STAFF_VERIFICATION,
            performed_by=request.user,
        )

        Transaction.objects.create(
            reference_id=uuid(),  # using random uuid as there is no transaction id for scan_and_pull
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(instance.external_id),
                "method": "scan_and_pull",
            },
            created_by=self.request.user,
        )

        return Response(
            AbhaNumberSerializer(
                instance,
                context={
                    **self.get_serializer_context(),
                    "health_facility": health_facility,
                },
            ).data,
            status=status.HTTP_201_CREATED,
        )
