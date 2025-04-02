from typing import TypedDict, List, Optional, Union

from abdm.models import HealthcareProviderRegistry


class RegisterProviderBody(TypedDict):
    name: str
    email: str
    mobile: str
    # Additional fields as required by HPR API


class RegisterProviderResponse(TypedDict):
    hpr_id: str
    # Additional fields from the HPR API response


class VerifyProviderBody(TypedDict):
    hpr_id: str
    # Additional verification fields


class VerifyProviderResponse(TypedDict):
    status: str
    # Additional verification response fields


class GetProviderDetailsBody(TypedDict):
    hpr_id: str


class GetProviderDetailsResponse(TypedDict):
    name: str
    email: str
    mobile: str
    status: str
    # Additional provider details


class CreateHprIdBody(TypedDict):
    """
    Request body for creating an HPR ID
    """
    idType: str  # "hpr_id"
    domainName: str  # "@hpr.abdm"
    email: str  # Must be encrypted
    firstName: str
    middleName: Optional[str]
    lastName: str
    password: str  # Must be encrypted
    profilePhoto: Optional[str]  # Base64 encoded string
    stateCode: str
    districtCode: str
    pincode: str
    txnId: str
    hprId: str  # "username@hpr.abdm"
    hpCategoryCode: int
    hpSubCategoryCode: int
    notifyUser: bool


class GetTokenBody(TypedDict):
    """
    Request body for getting a token
    """
    txnId: str
    mobileNumber: Optional[str]


class CreateHprIdResponse(TypedDict):
    """
    Response from creating an HPR ID
    """
    token: str
    hprId: str
    status: bool
    message: str


class GetCertificateResponse(TypedDict):
    """
    Response from getting the public certificate
    """
    certificate: str
    status: bool


class GatewayTokenBody(TypedDict):
    """
    Request body for getting a token from the gateway
    """
    clientId: str
    clientSecret: str


class GatewayTokenResponse(TypedDict):
    """
    Response from getting a token from the gateway
    """
    accessToken: str
    expiresIn: int
    refreshExpiresIn: int
    refreshToken: str
    tokenType: str


class GenerateAadhaarOtpBody(TypedDict):
    """
    Request body for generating OTP on Aadhaar registered mobile
    """
    aadhaar: str


class GenerateAadhaarOtpResponse(TypedDict):
    """
    Response from generating OTP on Aadhaar registered mobile
    """
    txnId: str
    mobileNumber: str


class VerifyAadhaarOtpBody(TypedDict):
    """
    Request body for verifying OTP sent to Aadhaar registered mobile
    """
    domainName: str
    idType: str
    otp: str
    restrictions: str
    txnId: str


class VerifyAadhaarOtpResponse(TypedDict):
    """
    Response from verifying OTP sent to Aadhaar registered mobile
    """
    txnId: str
    mobileNumber: Optional[str]
    photo: Optional[str]
    gender: str
    name: str
    email: Optional[str]
    pincode: str
    birthdate: str
    careOf: Optional[str]
    house: str
    street: str
    landmark: str
    locality: Optional[str]
    villageTownCity: str
    subDist: Optional[str]
    district: str
    state: str
    postOffice: Optional[str]
    address: str


class CheckHprIdExistBody(TypedDict):
    """
    Request body for checking if an HPR ID exists
    """
    txnId: str
    preverifiedCheck: bool


class CheckHprIdExistResponse(TypedDict):
    """
    Response from checking if an HPR ID exists
    """
    token: str
    hprIdNumber: str
    categoryId: int
    subCategoryId: int
    new: bool 