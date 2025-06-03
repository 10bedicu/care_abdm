from typing import Literal, TypedDict


class LocalizedDetails(TypedDict):
    name: str
    stateName: str
    districtName: str
    villageName: str
    wardName: str
    townName: str
    gender: str
    localizedLabels: dict[str, str]


class ABHAProfileFull(TypedDict):
    ABHANumber: str
    preferredAbhaAddress: str
    mobile: str
    firstName: str
    middleName: str
    lastName: str
    name: str
    yearOfBirth: str
    dayOfBirth: str
    monthOfBirth: str
    gender: Literal["M", "F", "O"]
    profilePhoto: str
    status: str
    stateCode: str
    districtCode: str
    pincode: str
    address: str
    kycPhoto: str
    stateName: str
    districtName: str
    subdistrictName: str
    townName: str
    authMethods: list[str]
    tags: dict[str, str]
    kycVerified: bool
    localizedDetails: LocalizedDetails
    createdDate: str


class ABHAProfile(TypedDict):
    ABHANumber: str
    abhaStatus: Literal["ACTIVE"]
    abhaType: Literal["STANDARD"]
    address: str
    districtCode: str
    districtName: str
    dob: str
    firstName: str
    gender: Literal["M", "F", "O"]
    lastName: str
    middleName: str
    mobile: str
    photo: str
    phrAddress: list[str]
    pinCode: str
    stateCode: str
    stateName: str


class Token(TypedDict):
    expiresIn: int
    refreshExpiresIn: int
    refreshToken: str
    token: str


class Account(TypedDict):
    ABHANumber: str
    preferredAbhaAddress: str | None
    name: str | None
    gender: Literal["M", "F", "O"] | None
    dob: str | None
    status: Literal["ACTIVE"] | None
    profilePhoto: str | None
    kycVerified: bool | None


class User(TypedDict):
    abhaAddress: str
    fullName: str
    abhaNumber: str
    status: str
    kycStatus: str


class EnrollmentRequestOtpBody(TypedDict):
    transaction_id: str | None
    scope: list[Literal["abha-enrol", "dl-flow", "mobile-verify", "email-verify"]]
    type: Literal["aadhaar", "mobile"]
    value: str


class EnrollmentRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class EnrollmentEnrolByAadhaarViaOtpBody(TypedDict):
    transaction_id: str
    otp: str
    mobile: str


class EnrollmentEnrolByAadhaarViaDemographicsBody(TypedDict):
    transaction_id: str
    aadhaar_number: str
    name: str
    date_of_birth: str
    gender: Literal["M", "F", "O"]
    state_code: str
    district_code: str
    address: str | None
    pin_code: str | None
    mobile: str | None
    profile_photo: str | None


class EnrollmentEnrolByAadhaarViaOtpResponse(TypedDict):
    ABHAProfile: ABHAProfile
    isNew: bool
    message: str
    tokens: Token
    txnId: str


class EnrollmentEnrolByAadhaarViaDemographicsResponse(TypedDict):
    healthIdNumber: str
    healthId: str
    mobile: str
    firstName: str
    middleName: str
    lastName: str
    name: str
    yearOfBirth: str
    dayOfBirth: str
    monthOfBirth: str
    gender: Literal["M", "F", "O"]
    profilePhoto: str
    stateCode: str
    districtCode: str
    pincode: str
    address: str
    stateName: str
    districtName: str
    kycVerified: str
    token: str
    jwtResponse: Token
    status: Literal["ACTIVE"]
    new: bool


class EnrollmentAuthByAbdmBody(TypedDict):
    scope: list[Literal["abha-enrol", "dl-flow", "mobile-verify", "email-verify"]]
    transaction_id: str
    otp: str


class EnrollmentAuthByAbdmResponse(TypedDict):
    accounts: list[Account]
    message: str
    authResult: Literal["success", "failure"]
    txnId: str


class EnrollmentEnrolSuggestionBody(TypedDict):
    transaction_id: str


class EnrollmentEnrolSuggestionResponse(TypedDict):
    abhaAddressList: list[str]
    txnId: str


class EnrollmentEnrolAbhaAddressBody(TypedDict):
    transaction_id: str
    abha_address: str
    preferred: int


class EnrollmentEnrolAbhaAddressResponse(TypedDict):
    healthIdNumber: str
    preferredAbhaAddress: str
    txnId: str


class ProfileLoginRequestOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    type: Literal["aadhaar", "mobile", "abha-number"]
    value: str
    otp_system: Literal["aadhaar", "abdm"]


class ProfileLoginRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class ProfileLoginVerifyBody(TypedDict):
    scope: list[
        Literal[
            "abha-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    transaction_id: str
    otp: str


class ProfileLoginVerifyResponse(TypedDict):
    txnId: str
    authResult: Literal["success", "failed"]
    message: str
    token: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int
    accounts: list[Account]


class PhrWebLoginAbhaRequestOtpBody(TypedDict):
    scope: list[
        Literal[
            "abha-address-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    type: Literal["abha-address"]
    value: str
    otp_system: Literal["aadhaar", "abdm"]


class PhrWebLoginAbhaRequestOtpResponse(TypedDict):
    txnId: str
    message: str


class PhrWebLoginAbhaVerifyBody(TypedDict):
    scope: list[
        Literal[
            "abha-address-login",
            "aadhaar-verify",
            "mobile-verify",
        ]
    ]
    transaction_id: str
    otp: str


class PhrWebLoginAbhaVerifyResponse(TypedDict):
    message: str
    authResult: Literal["success", "failure"]
    users: list[User]
    tokens: Token


class PhrWebLoginAbhaSearchBody(TypedDict):
    abha_address: str


class PhrWebLoginAbhaSearchResponse(TypedDict):
    healthIdNumber: str
    abhaAddress: str
    authMethods: list[Literal["AADHAAR_OTP", "MOBILE_OTP", "DEMOGRAPHICS"]]
    blockedAuthMethods: list[Literal["AADHAAR_OTP", "MOBILE_OTP", "DEMOGRAPHICS"]]
    status: Literal["ACTIVE"]
    message: str | None
    fullName: str
    mobile: str


class ProfileLoginVerifyUserBody(TypedDict):
    abha_number: str
    transaction_id: str
    t_token: str


class ProfileLoginVerifyUserResponse(TypedDict):
    token: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int


class ProfileAccountBody(TypedDict):
    x_token: str


class ProfileAccountResponse(TypedDict, ABHAProfileFull):
    pass


class ProfileAccountAbhaCardBody(TypedDict):
    x_token: str


class ProfileAccountAbhaCardResponse(TypedDict):
    pass


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
