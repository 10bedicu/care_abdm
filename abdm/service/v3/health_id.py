from typing import Any

from abdm.service.helper import (
    ABDMAPIException,
    benefit_name,
    encrypt_message,
    timestamp,
    uuid,
)
from abdm.service.request import Request
from abdm.service.v3.types.health_id import (
    EnrollmentAuthByAbdmBody,
    EnrollmentAuthByAbdmResponse,
    EnrollmentEnrolAbhaAddressBody,
    EnrollmentEnrolAbhaAddressResponse,
    EnrollmentEnrolByAadhaarViaDemographicsBody,
    EnrollmentEnrolByAadhaarViaDemographicsResponse,
    EnrollmentEnrolByAadhaarViaOtpBody,
    EnrollmentEnrolByAadhaarViaOtpResponse,
    EnrollmentEnrolSuggestionBody,
    EnrollmentEnrolSuggestionResponse,
    EnrollmentRequestOtpBody,
    EnrollmentRequestOtpResponse,
    PhrEnrollmentAbhaAddressExistsBody,
    PhrEnrollmentAbhaAddressSuggestionBody,
    PhrEnrollmentAbhaAddressSuggestionResponse,
    PhrEnrollmentEnrolAbhaAddressBody,
    PhrEnrollmentEnrolAbhaAddressResponse,
    PhrEnrollmentRequestOtpBody,
    PhrEnrollmentRequestOtpResponse,
    PhrEnrollmentVerifyOtpBody,
    PhrEnrollmentVerifyOtpResponse,
    PhrWebLoginAbhaRequestOtpBody,
    PhrWebLoginAbhaRequestOtpResponse,
    PhrWebLoginAbhaSearchBody,
    PhrWebLoginAbhaSearchResponse,
    PhrWebLoginAbhaVerifyBody,
    PhrWebLoginAbhaVerifyResponse,
    ProfileAccountAbhaCardBody,
    ProfileAccountAbhaCardResponse,
    ProfileAccountBody,
    ProfileAccountResponse,
    ProfileLoginRequestOtpBody,
    ProfileLoginRequestOtpResponse,
    ProfileLoginVerifyBody,
    ProfileLoginVerifyResponse,
    ProfileLoginVerifyUserBody,
    ProfileLoginVerifyUserResponse,
)
from abdm.settings import plugin_settings as settings


class HealthIdService:
    request = Request(f"{settings.ABDM_ABHA_URL}/v3")

    @staticmethod
    def handle_error(error: dict[str, Any] | str) -> str:
        if isinstance(error, list):
            return HealthIdService.handle_error(error[0])

        if isinstance(error, str):
            return error

        # { error: { message: "error message" } }
        if "error" in error:
            return HealthIdService.handle_error(error["error"])

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
    def enrollment__enrol__byAadhaar__via_demographics(
        data: EnrollmentEnrolByAadhaarViaDemographicsBody,
    ) -> EnrollmentEnrolByAadhaarViaDemographicsResponse:
        if not benefit_name():
            raise ABDMAPIException(
                detail="This action is not allowed. This action is only available for selected entities."
            )

        payload = {
            "authData": {
                "authMethods": ["demo_auth"],
                "demo_auth": {
                    "txnId": data.get("transaction_id", ""),
                    "aadhaarNumber": encrypt_message(data.get("aadhaar_number", "")),
                    "name": data.get("name", ""),
                    "gender": data.get("gender", ""),
                    "dateOfBirth": data.get("date_of_birth", ""),
                    "stateCode": data.get("state_code", ""),
                    "districtCode": data.get("district_code", ""),
                    "address": data.get("address", None),
                    "pinCode": data.get("pin_code", None),
                    "mobile": data.get("mobile", None),
                    "profilePhoto": data.get("profile_photo", None),
                },
            },
            "consent": {"code": "abha-enrollment", "version": "1.4"},
        }

        path = "/enrollment/enrol/byAadhaar"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "BENEFIT-NAME": benefit_name(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def enrollment__request__otp(
        data: EnrollmentRequestOtpBody,
    ) -> EnrollmentRequestOtpResponse:
        payload = {
            "txnId": data.get("transaction_id", ""),
            "scope": data.get("scope", []),
            "loginHint": data.get("type", ""),
            "loginId": encrypt_message(data.get("value", "")),
            "otpSystem": {"aadhaar": "aadhaar", "mobile": "abdm", "": ""}[
                data.get("type", "")
            ],
        }

        path = "/enrollment/request/otp"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def enrollment__enrol__byAadhaar__via_otp(
        data: EnrollmentEnrolByAadhaarViaOtpBody,
    ) -> EnrollmentEnrolByAadhaarViaOtpResponse:
        payload = {
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "timeStamp": timestamp(),
                    "txnId": data.get("transaction_id", ""),
                    "otpValue": encrypt_message(data.get("otp", "")),
                    "mobile": data.get("mobile", ""),
                },
            },
            "consent": {"code": "abha-enrollment", "version": "1.4"},
        }

        path = "/enrollment/enrol/byAadhaar"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def enrollment__auth__byAbdm(
        data: EnrollmentAuthByAbdmBody,
    ) -> EnrollmentAuthByAbdmResponse:
        payload = {
            "scope": data.get("scope", []),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "timeStamp": timestamp(),
                    "txnId": data.get("transaction_id", ""),
                    "otpValue": encrypt_message(data.get("otp", "")),
                },
            },
        }

        path = "/enrollment/auth/byAbdm"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def enrollment__enrol__suggestion(
        data: EnrollmentEnrolSuggestionBody,
    ) -> EnrollmentEnrolSuggestionResponse:
        path = "/enrollment/enrol/suggestion"
        response = HealthIdService.request.get(
            path,
            headers={
                "TRANSACTION_ID": data.get("transaction_id"),
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def enrollment__enrol__abha_address(
        data: EnrollmentEnrolAbhaAddressBody,
    ) -> EnrollmentEnrolAbhaAddressResponse:
        payload = {
            "txnId": data.get("transaction_id", ""),
            "abhaAddress": data.get("abha_address", ""),
            "preferred": data.get("preferred", 1),
        }

        path = "/enrollment/enrol/abha-address"
        response = HealthIdService.request.post(
            path, payload, headers={"REQUEST-ID": uuid(), "TIMESTAMP": timestamp()}
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def profile__login__request__otp(
        data: ProfileLoginRequestOtpBody,
    ) -> ProfileLoginRequestOtpResponse:
        payload = {
            "scope": data.get("scope", []),
            "loginHint": data.get("type", ""),
            "loginId": encrypt_message(data.get("value", "")),
            "otpSystem": data.get("otp_system", ""),
        }

        path = "/profile/login/request/otp"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def profile__login__verify(
        data: ProfileLoginVerifyBody,
    ) -> ProfileLoginVerifyResponse:
        payload = {
            "scope": data.get("scope", []),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "txnId": data.get("transaction_id", ""),
                    "otpValue": encrypt_message(data.get("otp", "")),
                },
            },
        }

        path = "/profile/login/verify"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def phr__web__login__abha__request__otp(
        data: PhrWebLoginAbhaRequestOtpBody,
    ) -> PhrWebLoginAbhaRequestOtpResponse:
        payload = {
            "scope": data.get("scope", []),
            "loginHint": data.get("type", ""),
            "loginId": encrypt_message(data.get("value", "")),
            "otpSystem": data.get("otp_system", ""),
        }

        path = "/phr/web/login/abha/request/otp"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def phr__web__login__abha__verify(
        data: PhrWebLoginAbhaVerifyBody,
    ) -> PhrWebLoginAbhaVerifyResponse:
        payload = {
            "scope": data.get("scope", []),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "txnId": data.get("transaction_id", ""),
                    "otpValue": encrypt_message(data.get("otp", "")),
                },
            },
        }

        path = "/phr/web/login/abha/verify"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def phr__web__login__abha__search(
        data: PhrWebLoginAbhaSearchBody,
    ) -> PhrWebLoginAbhaSearchResponse:
        payload = {
            "abhaAddress": data.get("abha_address", ""),
        }

        path = "/phr/web/login/abha/search"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def profile__login__verify__user(
        data: ProfileLoginVerifyUserBody,
    ) -> ProfileLoginVerifyUserResponse:
        payload = {
            "ABHANumber": data.get("abha_number", ""),
            "txnId": data.get("transaction_id", ""),
        }

        path = "/profile/login/verify/user"
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "T-TOKEN": f"Bearer {data.get('t_token', '')}",
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def profile__account(data: ProfileAccountBody) -> ProfileAccountResponse:
        path = "/profile/account"
        response = HealthIdService.request.get(
            path,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-TOKEN": f"Bearer {data.get('x_token', '')}",
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def profile__account__abha_card(
        data: ProfileAccountAbhaCardBody,
    ) -> ProfileAccountAbhaCardResponse:
        path = "/profile/account/abha-card"
        response = HealthIdService.request.get(
            path,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-TOKEN": f"Bearer {data.get('x_token', '')}",
            },
        )

        if (
            response.status_code == 401
            and response.json().get("message") == "X-token expired"
        ):
            # TODO: refresh token and retry (implement refresh api once it's available)
            raise ABDMAPIException(
                detail="This action is can't be performed now. This action is available only till 15 minutes after linking abha number."
            )

        if response.status_code != 202:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.content

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
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

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
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

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
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()

    @staticmethod
    def phr__enrollment__abha_address__exists(
        data: PhrEnrollmentAbhaAddressExistsBody,
    ) -> bool:
        path = "/phr/app/enrollment/isExists"
        response = HealthIdService.request.get(
            path,
            params={"abhaAddress": data.get("abha_address")},
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

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
        response = HealthIdService.request.post(
            path,
            payload,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
            },
        )

        if response.status_code != 200:
            raise ABDMAPIException(detail=HealthIdService.handle_error(response.json()))

        return response.json()
