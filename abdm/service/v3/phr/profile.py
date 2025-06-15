from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.health_id import (
    ProfileAccountBody,
    ProfileAccountResponse,
)
from abdm.settings import plugin_settings as settings


class PhrProfileService:
    request = Request(f"{settings.ABDM_ABHA_URL}/v3")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return PhrProfileService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrProfileService.handle_error(error["error"])

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
    def phr__profile(data: ProfileAccountBody) -> ProfileAccountResponse:
        path = "/phr/app/login/profile"
        response = PhrProfileService.request.get(
            path,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-token": f"Bearer {data.get('x_token', '')}",
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrProfileService.handle_error(response.json())
            )

        return response.json()
