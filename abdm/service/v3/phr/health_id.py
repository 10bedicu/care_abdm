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
    def phr__enrollment__request__otp(
        data: PhrEnrollmentRequestOtpBody,
    ) -> PhrEnrollmentRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(data.get("value")),
            "otpSystem": data.get("otp_system"),
        }

        path = "/phr/app/enrollment/request/otp"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

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
                    "otpValue": encrypt_message(data.get("otp")),
                },
            },
        }

        path = "/phr/app/enrollment/verify"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

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

        path = "/phr/app/enrollment/suggestion"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

    @staticmethod
    def phr__enrollment__abha_address__exists(
        data: PhrEnrollmentAbhaAddressExistsBody,
    ) -> bool:
        path = "/phr/app/enrollment/isExists"
        response = PhrHealthIdService.request.get(
            path,
            params={"abhaAddress": data.get("abha_address")},
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.content.decode().lower() == "true"

    @staticmethod
    def phr__enrollment__enrol__abha_address(
        data: PhrEnrollmentEnrolAbhaAddressBody,
    ) -> PhrEnrollmentEnrolAbhaAddressResponse:
        phr_details = data.get("phr_details", {})
        encrypt_fields = {"email", "mobile", "password"}

        phr_payload = {**phr_details}

        for field in encrypt_fields:
            phr_payload[field] = (
                encrypt_message(phr_details.get(field, ""))
                if phr_details.get(field)
                else ""
            )

        payload = {
            "phrDetails": phr_payload,
            "txnId": data.get("transaction_id"),
        }

        path = "/phr/app/enrollment/enrol"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

    @staticmethod
    def phr__login__request__otp(
        data: PhrLoginRequestOtpBody,
    ) -> PhrLoginRequestOtpResponse:
        payload = {
            "scope": data.get("scope"),
            "loginHint": data.get("type"),
            "loginId": encrypt_message(data.get("value")),
            "otpSystem": data.get("otp_system"),
        }

        path = "/phr/app/login/request/otp"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

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
                    "otpValue": encrypt_message(data.get("otp")),
                },
            },
        }
        path = "/phr/app/login/verify"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

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
                    "password": encrypt_message(data.get("password")),
                },
            },
        }
        path = "/phr/app/login/verify"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

    @staticmethod
    def phr__login__verify__user(
        data: PhrLoginVerifyUserBody,
    ) -> ProfileLoginVerifyUserResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
            "txnId": data.get("transaction_id"),
        }

        path = "/phr/app/login/verify/user"
        response = PhrHealthIdService.request.post(
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
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()

    @staticmethod
    def phr__login_search_auth_methods(
        data: PhrWebLoginAbhaSearchBody,
    ) -> PhrWebLoginAbhaSearchResponse:
        payload = {
            "abhaAddress": data.get("abha_address"),
        }

        path = "/phr/app/login/search"
        response = PhrHealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(
                detail=PhrHealthIdService.handle_error(response.json())
            )

        return response.json()
