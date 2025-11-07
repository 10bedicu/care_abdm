from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    encrypt_message,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.health_id import (
    PhrWebLoginAbhaSearchBody,
    PhrWebLoginAbhaSearchResponse,
    ProfileLoginVerifyUserResponse,
)
from abdm.service.v3.types.phr.health_id import (
    PhrEnrollmentAbhaAddressExistsBody,
    PhrEnrollmentAbhaAddressSuggestionBody,
    PhrEnrollmentAbhaAddressSuggestionResponse,
    PhrEnrollmentEnrolAbhaAddressBody,
    PhrEnrollmentEnrolAbhaAddressResponse,
    PhrEnrollmentRequestOtpBody,
    PhrEnrollmentRequestOtpResponse,
    PhrEnrollmentVerifyOtpBody,
    PhrEnrollmentVerifyOtpResponse,
    PhrLoginRequestOtpBody,
    PhrLoginRequestOtpResponse,
    PhrLoginVerifyOtpBody,
    PhrLoginVerifyOtpResponse,
    PhrLoginVerifyPasswordBody,
    PhrLoginVerifyPasswordResponse,
    PhrLoginVerifyUserBody,
)
from abdm.settings import plugin_settings as settings


class PhrHealthIdService:
    request = Request(f"{settings.ABDM_ABHA_URL}/v3")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return PhrHealthIdService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return PhrHealthIdService.handle_error(error["error"])

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
    ):
        default_headers = {
            "REQUEST-ID": uuid(),
            "TIMESTAMP": timestamp(),
        }
        if headers:
            default_headers.update(headers)

        if method.upper() == "GET":
            response = PhrHealthIdService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrHealthIdService.request.post(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response

    @staticmethod
    def phr__enrollment__request__otp(
        data: PhrEnrollmentRequestOtpBody,
    ) -> PhrEnrollmentRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(
                data.get("value"), data.get("type") != "abha-number"
            ),
            "otpSystem": data.get("otp_system"),
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/enrollment/request/otp",
            payload,
        ).json()

    @staticmethod
    def phr__enrollment__verify__otp(
        data: PhrEnrollmentVerifyOtpBody,
    ) -> PhrEnrollmentVerifyOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "txnId": data.get("transaction_id"),
                    "otpValue": encrypt_message(
                        data.get("otp"),
                        "abha-login" not in data.get("scope"),
                    ),
                },
            },
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/enrollment/verify",
            payload,
        ).json()

    @staticmethod
    def phr__enrollment__abha_address__suggestion(
        data: PhrEnrollmentAbhaAddressSuggestionBody,
    ) -> PhrEnrollmentAbhaAddressSuggestionResponse:
        payload = {
            "txnId": data.get("transaction_id"),
            "firstName": data.get("first_name"),
            "lastName": data.get("last_name"),
            "yearOfBirth": data.get("year_of_birth"),
            "monthOfBirth": data.get("month_of_birth"),
            "dayOfBirth": data.get("day_of_birth"),
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/enrollment/suggestion",
            payload,
        ).json()

    @staticmethod
    def phr__enrollment__abha_address__exists(
        data: PhrEnrollmentAbhaAddressExistsBody,
    ) -> bool:
        try:
            response = PhrHealthIdService._make_request(
                "GET",
                "/phr/app/enrollment/isExists",
                params={"abhaAddress": data.get("abha_address")},
            )
            return response.content.decode().strip().lower() == "true"
        except (UnicodeDecodeError, AttributeError):
            return False

    @staticmethod
    def phr__enrollment__enrol__abha_address(
        data: PhrEnrollmentEnrolAbhaAddressBody,
    ) -> PhrEnrollmentEnrolAbhaAddressResponse:
        phr_details = data.get("phr_details", {})
        encrypt_fields = {"email", "mobile", "password"}

        phr_payload = {**phr_details}

        for field in encrypt_fields:
            phr_payload[field] = (
                encrypt_message(phr_details.get(field), is_phr=True)
                if phr_details.get(field)
                else ""
            )

        payload = {
            "phrDetails": phr_payload,
            "txnId": data.get("transaction_id"),
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/enrollment/enrol",
            payload,
        ).json()

    @staticmethod
    def phr__login__request__otp(
        data: PhrLoginRequestOtpBody,
    ) -> PhrLoginRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(
                data.get("value"), data.get("type") != "abha-number"
            ),
            "otpSystem": data.get("otp_system"),
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/login/request/otp",
            payload,
        ).json()

    @staticmethod
    def phr__login__verify__otp(
        data: PhrLoginVerifyOtpBody,
    ) -> PhrLoginVerifyOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "txnId": data.get("transaction_id"),
                    "otpValue": encrypt_message(
                        data.get("otp"),
                        "abha-login" not in data.get("scope"),
                    ),
                },
            },
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/login/verify",
            payload,
        ).json()

    @staticmethod
    def phr__login__verify__password(
        data: PhrLoginVerifyPasswordBody,
    ) -> PhrLoginVerifyPasswordResponse:
        payload = {
            "scope": data.get("scope"),
            "authData": {
                "authMethods": ["password"],
                "password": {
                    "abhaAddress": data.get("abha_address"),
                    "password": encrypt_message(data.get("password"), is_phr=True),
                },
            },
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/login/verify",
            payload,
        ).json()

    @staticmethod
    def phr__login__verify__user(
        data: PhrLoginVerifyUserBody,
    ) -> ProfileLoginVerifyUserResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
            "txnId": data.get("transaction_id"),
        }

        headers = {
            "T-token": f"Bearer {data.get('t_token', '')}",
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/login/verify/user",
            payload,
            headers=headers,
        ).json()

    @staticmethod
    def phr__login_search_auth_methods(
        data: PhrWebLoginAbhaSearchBody,
    ) -> PhrWebLoginAbhaSearchResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
        }

        return PhrHealthIdService._make_request(
            "POST",
            "/phr/app/login/search",
            payload,
        ).json()
