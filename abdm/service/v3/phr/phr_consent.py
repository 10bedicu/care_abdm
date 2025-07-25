from logging import getLogger
from typing import Any

from abdm.models.base import HealthInformationType
from abdm.service.helper import (
    ABDMAPIException,
    cm_id,
    get_default_abdm_period,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.phr.phr_consent import (
    PhrConsentArtefactBody,
    PhrConsentArtefactResponse,
    PhrConsentArtefactsBody,
    PhrConsentArtefactsResponse,
    PhrConsentAutoApproveSetupBody,
    PhrConsentAutoApproveSetupResponse,
    PhrConsentAutoApproveUpdateBody,
    PhrConsentAutoApproveUpdateResponse,
    PhrConsentRequestApproveBody,
    PhrConsentRequestApproveResponse,
    PhrConsentRequestArtefactsBody,
    PhrConsentRequestArtefactsResponse,
    PhrConsentRequestBody,
    PhrConsentRequestDenyBody,
    PhrConsentRequestDenyResponse,
    PhrConsentRequestResponse,
    PhrConsentRequestRevokeBody,
    PhrConsentRequestRevokeResponse,
    PhrConsentRequestsBody,
    PhrConsentRequestsResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)


class PhrConsentService:
    request = Request(f"{settings.ABDM_GATEWAY_URL}")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return PhrConsentService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrConsentService.handle_error(error["error"])

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
            response = PhrConsentService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrConsentService.request.post(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != expected_status:
            raise ABDMAPIException(
                detail=PhrConsentService.handle_error(response.json())
            )

        response_json = response.json()

        if ("error" in response_json and response_json["error"] is not None) or (
            isinstance(response_json, list)
            and len(response_json) > 0
            and "error" in response_json[0]
        ):
            raise ABDMAPIException(detail=PhrConsentService.handle_error(response_json))

        return response

    @staticmethod
    def phr__consent__requests(
        data: PhrConsentRequestsBody,
    ) -> PhrConsentRequestsResponse:
        return PhrConsentService._make_request(
            "GET",
            "/consent/v3/request",
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
    def phr__consent__request(
        data: PhrConsentRequestBody,
    ) -> PhrConsentRequestResponse:
        return PhrConsentService._make_request(
            "GET",
            f"/consent/v3/request/{data.get('request_id')}",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__artefacts(
        data: PhrConsentArtefactsBody,
    ) -> PhrConsentArtefactsResponse:
        return PhrConsentService._make_request(
            "GET",
            "/consent/v3/artefact",
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
    def phr__consent__request__artefacts(
        data: PhrConsentRequestArtefactsBody,
    ) -> PhrConsentRequestArtefactsResponse:
        return PhrConsentService._make_request(
            "GET",
            f"/consent/v3/artefact/request/{data.get('request_id')}",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__artefact(
        data: PhrConsentArtefactBody,
    ) -> PhrConsentArtefactResponse:
        return PhrConsentService._make_request(
            "GET",
            f"/consent/v3/artefact/{data.get('artefact_id')}",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__request__approve(
        data: PhrConsentRequestApproveBody,
    ) -> PhrConsentRequestApproveResponse:
        return PhrConsentService._make_request(
            "POST",
            f"/consent/v3/request/{data.get('request_id')}/approve",
            payload={
                "consents": data.get("consents"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__request__deny(
        data: PhrConsentRequestDenyBody,
    ) -> PhrConsentRequestDenyResponse:
        return PhrConsentService._make_request(
            "POST",
            f"/consent/v3/request/{data.get('request_id')}/deny",
            payload={
                "reason": data.get("reason"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__request__revoke(
        data: PhrConsentRequestRevokeBody,
    ) -> PhrConsentRequestRevokeResponse:
        return PhrConsentService._make_request(
            "POST",
            "/consent/v3/revoke",
            payload={
                "consents": data.get("consents"),
            },
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__auto__approve__setup(
        data: PhrConsentAutoApproveSetupBody,
    ) -> PhrConsentAutoApproveSetupResponse:
        payload = {
            "isApplicableForAllHIPs": True,
            "hiu": {"id": "sbx_001"},  # TODO: Get from config
            "includedSources": [
                {
                    "hiTypes": [hi_type.value for hi_type in HealthInformationType],
                    "purpose": {
                        "text": "Self Requested",
                        "code": "PATRQT",
                        "refUri": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
                    },
                    "period": get_default_abdm_period(),
                }
            ],
        }

        return PhrConsentService._make_request(
            "POST",
            "/consent/v3/auto/approve",
            payload,
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()

    @staticmethod
    def phr__consent__auto__approve__update(
        data: PhrConsentAutoApproveUpdateBody,
    ) -> PhrConsentAutoApproveUpdateResponse:
        base_path = f"/consent/v3/auto/approve/{data.get('auto_approve_request_id')}"
        path = f"{base_path}/enable" if data.get("enable") else f"{base_path}/disable"

        return PhrConsentService._make_request(
            "POST",
            path,
            payload={},
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
        ).json()
