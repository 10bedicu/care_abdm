from typing import Literal, TypedDict

from abdm.service.v3.types.health_id import Token, User


class PhrRequestTokenBody(TypedDict):
    r_token: str


class PhrRequestTokenResponse(TypedDict):
    tokens: Token


class PhrProfileSwitchBody(TypedDict):
    x_token: str


class PhrProfileSwitchResponse(TypedDict):
    tokens: Token
    txnId: str
    users: list[User]


class PhrProfileSwitchVerifyUserBody(TypedDict):
    abha_address: str
    transaction_id: str
    t_token: str


class PhrProfileSwitchVerifyUserResponse(TypedDict):
    token: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int
    switchProfileEnabled: bool


class PhrProfileRequestOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "abha-address-profile",
            "mobile-verify",
            "aadhaar-verify",
            "email-verify",
        ]
    ]
    type: Literal["abha-number", "mobile-number", "email"]
    value: str
    otp_system: Literal["aadhaar", "abdm"]
    x_token: str


class PhrProfileRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class PhrProfileVerifyOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "abha-address-profile",
            "mobile-verify",
            "aadhaar-verify",
            "email-verify",
        ]
    ]
    transaction_id: str
    otp: str
    x_token: str


class PhrProfileVerifyOtpResponse(TypedDict):
    txnId: str
    message: str
    authResult: Literal["success", "failure"]


class PhrProfileLinkDelinkBody(TypedDict):
    action: Literal["LINK", "DE_LINK"]
    transaction_id: str
    x_token: str


class PhrProfileLinkDelinkResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]


class PhrSelectPreferredAbhaBody(TypedDict):
    transaction_id: str
    x_token: str


class PhrSelectPreferredAbhaResponse(TypedDict):
    abhaAddress: str
    status: Literal["ACTIVE"]


class PhrDetails(TypedDict):
    address: str
    firstName: str
    middleName: str
    lastName: str
    gender: Literal["M", "F", "O"]
    dayOfBirth: str
    monthOfBirth: str
    yearOfBirth: str
    stateCode: str
    stateName: str
    districtCode: str
    districtName: str
    pinCode: str
    profilePhoto: str


class PhrProfileUpdateBody(TypedDict):
    x_token: str
    profile_data: PhrDetails


class PhrProfileUpdateResponse(TypedDict):
    pass


class PhrProfileResetPasswordBody(TypedDict):
    x_token: str
    abha_address: str
    password: str


class PhrProfileResetPasswordResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]


class PhrProfileLogoutBody(TypedDict):
    x_token: str


class PhrProfileLogoutResponse(TypedDict):
    message: str
