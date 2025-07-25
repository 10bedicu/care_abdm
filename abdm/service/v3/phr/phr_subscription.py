from logging import getLogger
from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    cm_id,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.phr.phr_subscription import (
    PhrSubscriptionArtefactBody,
    PhrSubscriptionArtefactResponse,
    PhrSubscriptionEditBody,
    PhrSubscriptionEditResponse,
    PhrSubscriptionRequestApproveBody,
    PhrSubscriptionRequestApproveResponse,
    PhrSubscriptionRequestBody,
    PhrSubscriptionRequestDenyBody,
    PhrSubscriptionRequestDenyResponse,
    PhrSubscriptionRequestResponse,
    PhrSubscriptionRequestsBody,
    PhrSubscriptionRequestsResponse,
    PhrSubscriptionStatusUpdateBody,
    PhrSubscriptionStatusUpdateResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)


ABDM_HIU_ID = "IN3210000018"


class PhrSubscriptionService:
    request = Request(f"{settings.ABDM_GATEWAY_URL}/subscription-requests")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        logger.error(f"SUBSCRIPTION REQUEST ERROR: {error}")

        if isinstance(error, list):
            return PhrSubscriptionService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrSubscriptionService.handle_error(error["error"])

        # { message: "error message" }cursor .
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
            response = PhrSubscriptionService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrSubscriptionService.request.post(
                path, payload, headers=default_headers
            )
        elif method.upper() == "PUT":
            response = PhrSubscriptionService.request.put(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != expected_status:
            raise ABDMAPIException(
                detail=PhrSubscriptionService.handle_error(response.json())
            )

        response_json = response.json()

        if ("error" in response_json and response_json["error"] is not None) or (
            isinstance(response_json, list)
            and len(response_json) > 0
            and "error" in response_json[0]
        ):
            raise ABDMAPIException(
                detail=PhrSubscriptionService.handle_error(response_json)
            )

        return response

    @staticmethod
    def phr__subscription__requests(
        data: PhrSubscriptionRequestsBody,
    ) -> PhrSubscriptionRequestsResponse:
        return PhrSubscriptionService._make_request(
            "GET",
            "/v3/requests",
            params={
                "limit": data.get("limit"),
                "offset": data.get("offset"),
                "status": data.get("status"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__request(
        data: PhrSubscriptionRequestBody,
    ) -> PhrSubscriptionRequestResponse:
        return PhrSubscriptionService._make_request(
            "GET",
            f"/v3/request/{data.get('request_id')}",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__artefact(
        data: PhrSubscriptionArtefactBody,
    ) -> PhrSubscriptionArtefactResponse:
        return PhrSubscriptionService._make_request(
            "GET",
            f"/v3/{data.get('subscription_id')}",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__request__approve(
        data: PhrSubscriptionRequestApproveBody,
    ) -> PhrSubscriptionRequestApproveResponse:
        return PhrSubscriptionService._make_request(
            "POST",
            f"/v3/{data.get('request_id')}/approve",
            payload=data.get("subscription"),
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__request__deny(
        data: PhrSubscriptionRequestDenyBody,
    ) -> PhrSubscriptionRequestDenyResponse:
        return PhrSubscriptionService._make_request(
            "POST",
            f"/v3/{data.get('request_id')}/deny",
            payload={
                "reason": data.get("reason"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__status__update(
        data: PhrSubscriptionStatusUpdateBody,
    ) -> PhrSubscriptionStatusUpdateResponse:
        base_path = "enable" if data.get("enable") else "disable"

        return PhrSubscriptionService._make_request(
            "POST",
            f"/v3/{base_path}/{data.get('subscription_id')}",
            payload={},
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__subscription__edit(
        data: PhrSubscriptionEditBody,
    ) -> PhrSubscriptionEditResponse:
        return PhrSubscriptionService._make_request(
            "PUT",
            f"/v3/patients/{data.get('subscription_id')}",
            payload={
                "hiuId": ABDM_HIU_ID,
                "subscriptionEditAndApprovalRequest": data.get("subscription"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    # SUBSCRIPTION REQUEST CALLBACK SERVICES
    @staticmethod
    def phr__subscription__request__init(
        data: dict,
    ) -> dict:
        response = PhrSubscriptionService._make_request(
            "POST",
            "/v3/init",
            payload=data,
            expected_status=202,
        )

        logger.info(f"SUBSCRIPTION REQUEST INIT RESPONSE: {response.json()}")

        return {}

    @staticmethod
    def phr__subscription__request__on__notify(
        data: dict,
    ) -> dict:
        PhrSubscriptionService._make_request(
            "POST",
            "/v3/hiu/on-notify",
            payload=data,
        )

        return {}
