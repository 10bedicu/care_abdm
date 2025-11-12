from logging import getLogger

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.phr_gateway import PhrGatewayPatientShareSerializer
from abdm.authentication import (
    ABDMAuthentication,
    IsPhrAuthenticated,
    PhrCustomAuthentication,
)
from abdm.service.phr_helper import get_phr_access_token, transform_phr_links_data
from abdm.service.v3.phr.phr_gateway import PhrGatewayService

logger = getLogger(__name__)


@extend_schema(tags=["PHR Gateway"])
class PhrGatewayViewSet(GenericViewSet):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]

    serializer_action_classes = {
        "phr_gateway__patient__share": PhrGatewayPatientShareSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @property
    def x_token(self):
        return get_phr_access_token(self.request.user.abha_address)

    @action(detail=False, methods=["get"], url_path="patient/links")
    def phr_gateway__patient__links(self, request):
        links = PhrGatewayService.phr__gateway__patient__links(
            {
                "x_token": self.x_token,
            }
        )

        return Response(transform_phr_links_data(links), status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="providers")
    def phr_gateway__providers(self, request):
        name = request.query_params.get("name", "")

        if not name:
            return Response([], status=status.HTTP_200_OK)

        providers = PhrGatewayService.phr__gateway__providers(
            {
                "name": name,
            }
        )

        return Response(providers, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="provider/(?P<provider_id>[^/.]+)")
    def phr_gateway__provider(self, request, provider_id):
        provider = PhrGatewayService.phr__gateway__provider(
            {
                "id": provider_id,
            }
        )

        return Response(provider, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="govt_programs")
    def phr_gateway__govt__programs(self, request):
        govt_programs = PhrGatewayService.phr__gateway__govt__programs()

        return Response(govt_programs, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="health_lockers")
    def phr_gateway__health__lockers(self, request):
        name = request.query_params.get("name", "")

        if not name:
            return Response([], status=status.HTTP_200_OK)

        health_lockers = PhrGatewayService.phr__gateway__health__lockers(
            {
                "name": name,
            }
        )

        return Response(health_lockers, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="patient/share")
    def phr_gateway__patient__share(self, request):
        validated_data = self.validate_request(request)

        PhrGatewayService.phr__gateway__patient_share__share(
            {
                "x_token": self.x_token,
                "hip_id": validated_data.get("hip_id"),
                "context": validated_data.get("context"),
                "hpr_id": validated_data.get("hpr_id"),
                "latitude": validated_data.get("latitude"),
                "longitude": validated_data.get("longitude"),
                "abha_address": self.request.user.abha_address,
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="patient/tokens")
    def phr_gateway__patient__tokens(self, request):
        tokens = (
            PhrGatewayService.phr__gateway__patient_share__profile__get_token_details(
                {
                    "x_token": self.x_token,
                    "limit": request.query_params.get("limit", 10),
                }
            )
        )

        return Response(tokens, status=status.HTTP_200_OK)


@extend_schema(tags=["PHR Gateway Callbacks"])
class PhrGatewayCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    @action(detail=False, methods=["POST"], url_path="hiu/patient/on-share")
    def phr_gateway__hiu__patient__on_share(self, request):
        data = request.data

        if data.get("acknowledgement", {}).get("status") == "SUCCESS":
            # TODO: send push notification to the user
            pass

        return Response(status=status.HTTP_200_OK)
