from logging import getLogger
from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    cm_id,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.phr.user_init_linking import (
    PhrUserInitLinkingCareContextConfirmBody,
    PhrUserInitLinkingCareContextConfirmResponse,
    PhrUserInitLinkingCareContextDiscoverBody,
    PhrUserInitLinkingCareContextDiscoverResponse,
    PhrUserInitLinkingCareContextInitBody,
    PhrUserInitLinkingCareContextInitResponse,
    PhrUserInitLinkingCareContextLinksBody,
    PhrUserInitLinkingCareContextLinksResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)

ABDM_HIU_ID = "IN3210000018"


class PhrUserInitLinkingService:
    request = Request(f"{settings.ABDM_GATEWAY_URL}")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return PhrUserInitLinkingService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrUserInitLinkingService.handle_error(error["error"])

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
        expected_status: int = 202,
    ):
        default_headers = {
            "REQUEST-ID": uuid(),
            "TIMESTAMP": timestamp(),
            "X-CM-ID": cm_id(),
        }
        if headers:
            default_headers.update(headers)

        if method.upper() == "GET":
            response = PhrUserInitLinkingService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrUserInitLinkingService.request.post(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != expected_status:
            raise ABDMAPIException(
                detail=PhrUserInitLinkingService.handle_error(response.json())
            )

        return response

    @staticmethod
    def phr__user_initiated_linking__care_context__links(
        data: PhrUserInitLinkingCareContextLinksBody,
    ) -> PhrUserInitLinkingCareContextLinksResponse:
        response = PhrUserInitLinkingService._make_request(
            "GET",
            "/hip/v3/link/patient/links",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
            expected_status=200,
        )
        return response.json()

    @staticmethod
    def phr__user_initiated_linking__care_context__discover(
        data: PhrUserInitLinkingCareContextDiscoverBody,
    ) -> PhrUserInitLinkingCareContextDiscoverResponse:
        headers = {
            "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            "X-HIU-ID": ABDM_HIU_ID,
        }

        PhrUserInitLinkingService._make_request(
            "POST",
            "/user-initiated-linking/v3/patient/care-context/discover",
            payload={
                "hip": data.get("hip"),
                "unverifiedIdentifiers": data.get("unverified_identifiers"),
            },
            headers=headers,
        )

        return {}

    @staticmethod
    def phr__user_initiated_linking__care_context__init(
        data: PhrUserInitLinkingCareContextInitBody,
    ) -> PhrUserInitLinkingCareContextInitResponse:
        headers = {
            "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            "X-HIU-ID": ABDM_HIU_ID,
        }

        PhrUserInitLinkingService._make_request(
            "POST",
            "/user-initiated-linking/v3/link/care-context/init",
            payload={
                "transactionId": data.get("transaction_id"),
                "patient": data.get("patient"),
            },
            headers=headers,
        )

        return {}

    @staticmethod
    def phr__user_initiated_linking__care_context__confirm(
        data: PhrUserInitLinkingCareContextConfirmBody,
    ) -> PhrUserInitLinkingCareContextConfirmResponse:
        headers = {
            "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            "X-HIU-ID": ABDM_HIU_ID,
        }

        PhrUserInitLinkingService._make_request(
            "POST",
            "/user-initiated-linking/v3/link/care-context/confirm",
            payload={
                "linkRefNumber": data.get("link_ref_number"),
                "token": data.get("token"),
            },
            headers=headers,
        )

        return {}
