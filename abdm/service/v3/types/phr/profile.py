from typing import Literal, TypedDict

from abdm.service.v3.types.health_id import Token, User
from care_abdm.abdm.service.v3.types.phr.health_id import PhrDetails


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


class PhrProfileUpdateData(TypedDict):
    address: str
    first_name: str
    middle_name: str | None
    last_name: str | None
    gender: Literal["M", "F", "O"]
    day_of_birth: str | None
    month_of_birth: str | None
    year_of_birth: str
    state_code: str
    state_name: str
    district_code: str
    district_name: str
    pincode: str
    profile_photo: str | None


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
    timestamp: str


class PhrProfileLogoutResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]
