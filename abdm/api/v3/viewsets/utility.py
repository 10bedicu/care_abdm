import json
import logging
from importlib import resources

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: Utility"])
class UtilityViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    def get_state_districts(self):
        try:
            with (
                resources.files("abdm.data")
                .joinpath("state_districts.json")
                .open() as f
            ):
                return json.load(f)
        except Exception:
            logger.error("Error loading state_districts.json")
            raise

    @action(detail=False, methods=["get"])
    def states(self, request):
        data = self.get_state_districts()

        states = [
            {"state_code": state["state_code"], "state_name": state["state_name"]}
            for state in data["states"]
        ]
        return Response(states)

    @action(
        detail=False,
        methods=["get"],
        url_path="states/(?P<state_code>[^/.]+)/districts",
    )
    def districts(self, request, state_code=None):
        data = self.get_state_districts()

        state_code = int(state_code)
        state = next((s for s in data["states"] if s["state_code"] == state_code), None)

        if not state:
            return Response(
                {"detail": "State not found"}, status=status.HTTP_404_NOT_FOUND
            )

        return Response(state["districts"])
