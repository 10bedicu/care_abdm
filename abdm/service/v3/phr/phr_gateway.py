from logging import getLogger
from typing import Any

from abdm.models.abha_number import AbhaNumber
from abdm.service.helper import ABDMAPIException, cm_id, timestamp, uuid
from abdm.service.request import Request
from abdm.service.v3.types.phr.phr_gateway import (
    PhrGatewayPatientLinksBody,
    PhrGatewayPatientLinksResponse,
    PhrGatewayPatientShareProfileGetTokenDetailsBody,
    PhrGatewayPatientShareProfileGetTokenDetailsResponse,
    PhrGatewayPatientShareShareBody,
    PhrGatewayPatientShareShareResponse,
    PhrGatewayProviderBody,
    PhrGatewayProviderResponse,
    PhrGatewayProvidersBody,
    PhrGatewayProvidersResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)


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

    @staticmethod
    def phr__gateway__patient_share__share(
        data: PhrGatewayPatientShareShareBody,
    ) -> PhrGatewayPatientShareShareResponse:
        if not data.get("abha_address"):
            raise ABDMAPIException(detail="ABHA Address is required.")

        if not data.get("hip_id"):
            raise ABDMAPIException(detail="HIP ID is required.")

        if not data.get("context"):
            raise ABDMAPIException(detail="Context is required.")

        abha_number = AbhaNumber.objects.filter(
            phr_health_id=data.get("abha_address")
        ).first()

        if not abha_number:
            raise ABDMAPIException(detail="ABHA Number not found.")

        year, month, day = None, None, None
        if abha_number.date_of_birth:
            year, month, day = abha_number.date_of_birth.split("-")
            month = None if month == "00" else month
            day = None if day == "00" else day

        payload = {
            "intent": "PROFILE_SHARE",
            "metaData": {
                "hipId": data.get("hip_id", ""),
                "context": data.get("context", ""),
                "hprId": data.get("hpr_id", None),
                "latitude": data.get("latitude", None),
                "longitude": data.get("longitude", None),
            },
            "profile": {
                "patient": {
                    "abhaNumber": abha_number.abha_number,
                    "abhaAddress": abha_number.phr_health_id,
                    "name": abha_number.name,
                    "gender": abha_number.gender,
                    "dayOfBirth": day,
                    "monthOfBirth": month,
                    "yearOfBirth": year,
                    "address": {
                        "line": abha_number.address,
                        "district": abha_number.district,
                        "state": abha_number.state,
                        "pincode": abha_number.pincode,
                    },
                    "phoneNumber": abha_number.mobile,
                }
            },
        }

        PhrGatewayService._make_request(
            "POST",
            "/patient-share/v3/share",
            payload=payload,
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
                "X-HIU-ID": settings.PHR_HF_ID,
            },
            expected_status=202,
        )

        return {}

    @staticmethod
    def phr__gateway__patient_share__profile__get_token_details(
        data: PhrGatewayPatientShareProfileGetTokenDetailsBody,
    ) -> PhrGatewayPatientShareProfileGetTokenDetailsResponse:
        response = PhrGatewayService._make_request(
            "GET",
            "/patient-share/v3/profile/getTokenDetails",
            headers={
                "X-AUTH-TOKEN": f"{data.get('x_token', '')}",
            },
            params={
                "limit": data.get("limit", 10),
            },
        )
        return response.json()
