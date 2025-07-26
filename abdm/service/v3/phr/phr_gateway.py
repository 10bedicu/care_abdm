from logging import getLogger
from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    cm_id,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.phr.phr_gateway import (
    PhrGatewayPatientLinksBody,
    PhrGatewayPatientLinksResponse,
    PhrGatewayProviderBody,
    PhrGatewayProviderResponse,
    PhrGatewayProvidersBody,
    PhrGatewayProvidersResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)

ABDM_HIU_ID = "IN3210000018"


class PhrGatewayService:
    request = Request(f"{settings.ABDM_GATEWAY_URL}")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return PhrGatewayService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrGatewayService.handle_error(error["error"])

        # { message: "error message" }
        if "message" in error:
            return error["message"]

        # { field_name: "error message" }
        if isinstance(error, dict) and len(error) >= 1:
            error.pop("code", None)
            error.pop("timestamp", None)
            return "".join(list(map(lambda x: str(x), list(error.values()))))

        return "Unknown error occurred at ABDM's end while processing the request. Please try again later."

    @staticmethod
    def _make_request(
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        expected_status: int = 200,
    ):
        default_headers = {
            "REQUEST-ID": uuid(),
            "TIMESTAMP": timestamp(),
            "X-CM-ID": cm_id(),
        }
        if headers:
            default_headers.update(headers)

        if method.upper() == "GET":
            response = PhrGatewayService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrGatewayService.request.post(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != expected_status:
            raise ABDMAPIException(
                detail=PhrGatewayService.handle_error(response.json())
            )

        return response

    @staticmethod
    def phr__gateway__patient__links(
        data: PhrGatewayPatientLinksBody,
    ) -> PhrGatewayPatientLinksResponse:
        response = PhrGatewayService._make_request(
            "GET",
            "/hip/v3/link/patient/links",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        )
        return response.json()

    @staticmethod
    def phr__gateway__providers(
        data: PhrGatewayProvidersBody,
    ) -> PhrGatewayProvidersResponse:
        response = PhrGatewayService._make_request(
            "GET",
            f"/gateway/v3/providers?name={data.get('name', '')}",
        )
        return response.json()

    @staticmethod
    def phr__gateway__provider(
        data: PhrGatewayProviderBody,
    ) -> PhrGatewayProviderResponse:
        response = PhrGatewayService._make_request(
            "GET",
            f"/gateway/v3/providers/{data.get('id', '')}",
        )
        return response.json()

    @staticmethod
    def phr__gateway__govt__programs() -> PhrGatewayProvidersResponse:
        response = PhrGatewayService._make_request(
            "GET",
            "/gateway/v3/govt-programs",
        )
        return response.json()

    @staticmethod
    def phr__gateway__health__lockers(
        data: PhrGatewayProvidersBody,
    ) -> PhrGatewayProvidersResponse:
        response = PhrGatewayService._make_request(
            "GET",
            f"/gateway/v3/health-lockers?name={data.get('name', '')}",
        )
        return response.json()
