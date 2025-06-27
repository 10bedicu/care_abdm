from logging import getLogger
from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    encrypt_message,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.health_id import (
    ProfileAccountBody,
    ProfileAccountResponse,
)
from abdm.service.v3.types.phr.profile import (
    PhrProfileLinkDelinkBody,
    PhrProfileLinkDelinkResponse,
    PhrProfileLogoutBody,
    PhrProfileLogoutResponse,
    PhrProfileRequestOtpBody,
    PhrProfileRequestOtpResponse,
    PhrProfileResetPasswordBody,
    PhrProfileResetPasswordResponse,
    PhrProfileSwitchBody,
    PhrProfileSwitchResponse,
    PhrProfileSwitchVerifyUserBody,
    PhrProfileSwitchVerifyUserResponse,
    PhrProfileUpdateBody,
    PhrProfileUpdateResponse,
    PhrProfileVerifyOtpBody,
    PhrProfileVerifyOtpResponse,
    PhrRequestTokenBody,
    PhrRequestTokenResponse,
    PhrSelectPreferredAbhaBody,
    PhrSelectPreferredAbhaResponse,
)
from abdm.settings import plugin_settings as settings

logger = getLogger(__name__)


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
        }
        if headers:
            default_headers.update(headers)

        if method.upper() == "GET":
            response = PhrProfileService.request.get(
                path, params=params, headers=default_headers
            )
        elif method.upper() == "POST":
            response = PhrProfileService.request.post(
                path, payload, headers=default_headers
            )
        else:
            raise ABDMAPIException(f"Unsupported HTTP method: {method}")

        if response.status_code != expected_status:
            raise ABDMAPIException(
                detail=PhrProfileService.handle_error(response.json())
            )

        return response

    @staticmethod
    def phr__request__token(
        data: PhrRequestTokenBody,
    ) -> PhrRequestTokenResponse:
        headers = {
            "R-TOKEN": f"Bearer {data.get('r_token', '')}",
        }

        response = PhrProfileService._make_request(
            "GET",
            "/phr/app/login/profile/request/token",
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile(data: ProfileAccountBody) -> ProfileAccountResponse:
        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "GET",
            "/phr/app/login/profile",
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__switch(data: PhrProfileSwitchBody) -> PhrProfileSwitchResponse:
        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "GET",
            "/phr/app/login/profile/switch-profile",
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__switch__verify_user(
        data: PhrProfileSwitchVerifyUserBody,
    ) -> PhrProfileSwitchVerifyUserResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
            "txnId": data.get("transaction_id"),
        }

        headers = {
            "T-TOKEN": f"Bearer {data.get('t_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/verify/switch-profile/user",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr_profile__card(data: ProfileAccountBody) -> bytes:
        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "GET",
            "/phr/app/login/profile/phrCard",
            headers=headers,
            expected_status=202,
        )

        return response.content

    @staticmethod
    def phr__profile__request__otp(
        data: PhrProfileRequestOtpBody,
    ) -> PhrProfileRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(
                data.get("value"), data.get("type") != "abha-number"
            ),
            "otpSystem": data.get("otp_system"),
        }

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/request/otp",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__verify__otp(
        data: PhrProfileVerifyOtpBody,
    ) -> PhrProfileVerifyOtpResponse:
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

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/verify",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__link__delink(
        data: PhrProfileLinkDelinkBody,
    ) -> PhrProfileLinkDelinkResponse:
        action = data.get("action")
        payload = {
            "action": action,
            "transactionId": data.get("transaction_id"),
        }

        path = (
            "/phr/app/login/profile/link"
            if action == "LINK"
            else "/phr/app/login/profile/de-link"
        )

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            path,
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__select__preferred__abha(
        data: PhrSelectPreferredAbhaBody,
    ) -> PhrSelectPreferredAbhaResponse:
        payload = {
            "transactionId": data.get("transaction_id"),
        }

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/set-preffered/abha-address",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__update(
        data: PhrProfileUpdateBody,
    ) -> PhrProfileUpdateResponse:
        payload = data.get("profile_data")

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/updateProfile",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__reset__password(
        data: PhrProfileResetPasswordBody,
    ) -> PhrProfileResetPasswordResponse:
        payload = {
            "scope": ["abha-address-profile", "password-verify"],
            "authData": {
                "authMethods": ["password"],
                "password": {
                    "abhaAddress": data.get("abha_address"),
                    "password": encrypt_message(data.get("password"), is_phr=True),
                },
            },
        }

        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "POST",
            "/phr/app/login/profile/verify",
            payload,
            headers=headers,
        )

        return response.json()

    @staticmethod
    def phr__profile__logout(
        data: PhrProfileLogoutBody,
    ) -> PhrProfileLogoutResponse:
        headers = {
            "X-token": f"Bearer {data.get('x_token', '')}",
        }

        response = PhrProfileService._make_request(
            "GET",
            "/phr/app/login/profile/request/logout",
            headers=headers,
        )

        return response.json()
