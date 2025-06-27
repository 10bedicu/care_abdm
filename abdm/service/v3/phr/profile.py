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
    PhrProfileCardBody,
    PhrProfileCardResponse,
    PhrProfileLinkDelinkBody,
    PhrProfileLinkDelinkResponse,
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
    def phr__request__token(
        data: PhrRequestTokenBody,
    ) -> PhrRequestTokenResponse:
        path = "/phr/app/login/profile/request/token"

        response = PhrProfileService.request.get(
            path,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "R-TOKEN": f"Bearer {data.get('r_token', '')}",
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrProfileService.handle_error(response.json())
            )

        return response.json()

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

    @staticmethod
    def phr__profile__switch(data: PhrProfileSwitchBody) -> PhrProfileSwitchResponse:
        path = "/phr/app/login/profile/switch-profile"

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

    @staticmethod
    def phr__profile__switch__verify_user(
        data: PhrProfileSwitchVerifyUserBody,
    ) -> PhrProfileSwitchVerifyUserResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
            "txnId": data.get("transaction_id"),
        }

        path = "/phr/app/login/profile/verify/switch-profile/user"
        response = PhrProfileService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "T-TOKEN": f"Bearer {data.get('t_token', '')}",
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrProfileService.handle_error(response.json())
            )

        return response.json()

    @staticmethod
    def phr_profile__card(data: PhrProfileCardBody) -> PhrProfileCardResponse:
        path = "/phr/app/login/profile/phrCard"
        response = PhrProfileService.request.get(
            path,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-token": f"Bearer {data.get('x_token', '')}",
            },
        )

        if response.status_code != 202:
            raise ABDMAPIException(
                detail=PhrProfileService.handle_error(response.json())
            )

        return response.content

    @staticmethod
    def phr__profile__request__otp(
        data: PhrProfileRequestOtpBody,
    ) -> PhrProfileRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(data.get("value")),
            "otpSystem": data.get("otp_system"),
        }

        path = "/phr/app/login/profile/request/otp"
        response = PhrProfileService.request.post(
            path,
            payload,
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
                    "otpValue": encrypt_message(data.get("otp")),
                },
            },
        }
        path = "/phr/app/login/profile/verify"
        response = PhrProfileService.request.post(
            path,
            payload,
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

    @staticmethod
    def phr__profile__link__delink(
        data: PhrProfileLinkDelinkBody,
    ) -> PhrProfileLinkDelinkResponse:
        action = data.get("action")
        payload = {
            "action": action,
            "transactionId": data.get("transaction_id"),
        }

        if action == "LINK":
            path = "/phr/app/login/profile/link"
        else:
            path = "/phr/app/login/profile/de-link"

        response = PhrProfileService.request.post(
            path,
            payload,
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

    @staticmethod
    def phr__profile__select__preferred__abha(
        data: PhrSelectPreferredAbhaBody,
    ) -> PhrSelectPreferredAbhaResponse:
        payload = {
            "transactionId": data.get("transaction_id"),
        }
        path = "/phr/app/login/profile/set-preffered/abha-address"
        response = PhrProfileService.request.post(
            path,
            payload,
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

    @staticmethod
    def phr__profile__update(
        data: PhrProfileUpdateBody,
    ) -> PhrProfileUpdateResponse:
        payload = data.get("profile_data")

        path = "/phr/app/login/profile/updateProfile"
        response = PhrProfileService.request.post(
            path,
            payload,
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
                    "password": encrypt_message(data.get("password")),
                },
            },
        }
        path = "/phr/app/login/profile/verify"
        response = PhrProfileService.request.post(
            path,
            payload,
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

    @staticmethod
    def phr__profile__logout(
        data: ProfileAccountBody,
    ) -> ProfileAccountResponse:
        path = "/phr/app/login/profile/request/logout"
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
