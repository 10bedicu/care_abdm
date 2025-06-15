from typing import Literal, TypedDict

from abdm.service.v3.types.health_id import Account, Token, User


class PhrEnrollmentRequestOtpBody(TypedDict):
    scope: list[
        Literal["abha-login", "aadhaar-verify", "mobile-verify", "abha-address-enroll"]
    ]
    type: str
    value: str
    otp_system: str


class PhrEnrollmentRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class PhrEnrollmentVerifyOtpBody(TypedDict):
    scope: list[
        Literal["abha-login", "aadhaar-verify", "mobile-verify", "abha-address-enroll"]
    ]
    transaction_id: str
    otp: str


class PhrEnrollmentVerifyOtpResponse(TypedDict):
    txnId: str
    message: str
    authResult: Literal["success", "failure"]
    users: list[User]
    tokens: Token
    accounts: list[Account]


class PhrEnrollmentAbhaAddressSuggestionBody(TypedDict):
    transaction_id: str
    first_name: str
    last_name: str
    year_of_birth: str
    month_of_birth: str
    day_of_birth: str


class PhrEnrollmentAbhaAddressSuggestionResponse(TypedDict):
    abhaAddressList: list[str]
    txnId: str


class PhrEnrollmentAbhaAddressExistsBody(TypedDict):
    abha_address: str


class PhrEnrollmentAbhaAddressExistsResponse(TypedDict):
    pass


class PhrDetails(TypedDict):
    abhaAddress: str
    address: str
    dayOfBirth: str
    districtCode: str
    districtName: str
    email: str
    profilePhoto: str
    firstName: str
    gender: str
    lastName: str
    middleName: str
    mobile: str
    monthOfBirth: str
    password: str
    pinCode: str
    stateCode: str
    stateName: str
    yearOfBirth: str


class PhrEnrollmentEnrolAbhaAddressBody(TypedDict):
    phr_details: PhrDetails
    transaction_id: str


class PhrEnrollmentEnrolAbhaAddressResponse(TypedDict):
    message: str
    phrDetails: dict
    tokens: Token
    txnId: str


class PhrLoginRequestOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "abha-address-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    type: Literal["abha-address", "abha-number", "mobile-number"]
    value: str
    otp_system: Literal["aadhaar", "abdm"]


class PhrLoginRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class PhrLoginVerifyOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "abha-address-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    transaction_id: str
    otp: str


class PhrLoginVerifyOtpResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]
    txnId: str
    users: list[User]
    tokens: Token


class PhrLoginVerifyPasswordBody(TypedDict):
    scope: list[Literal["abha-address-login", "password-verify"]]
    abha_address: str
    password: str


class PhrLoginVerifyPasswordResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]
    tokens: Token
    users: list[User]


class PhrLoginVerifyUserBody(TypedDict):
    abha_address: str
    transaction_id: str
    t_token: str
