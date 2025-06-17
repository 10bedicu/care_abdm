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


class PhrProfileCardBody(TypedDict):
    x_token: str


class PhrProfileCardResponse(TypedDict):
    pass


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


class PhrProfileLinkDelinkResponse(TypedDict):
    authResult: Literal["success", "failure"]
    message: str


class PhrSelectPreferredAbhaBody(TypedDict):
    transaction_id: str


class PhrSelectPreferredAbhaResponse(TypedDict):
    abhaAddress: str
    status: Literal["ACTIVE"]


class PhrProfileLogoutResponse(TypedDict):
    message: str
    timestamp: str
