from typing import Literal, TypedDict

from abdm.models import HealthInformationType


class CareContext(TypedDict):
    referenceNumber: str
    display: str


class Patient(TypedDict):
    referenceNumber: str
    display: str
    hiType: HealthInformationType
    careContexts: list[CareContext]
    count: int


class ProviderIdentifier(TypedDict):
    id: str
    name: str


class Provider(TypedDict):
    identifier: ProviderIdentifier
    facilityType: list[Literal["HIP", "HIU", "HEALTH_LOCKER"]]


class PhrGatewayPatientLinksBody(TypedDict):
    x_token: str


class PhrGatewayPatientLinksResponse(TypedDict):
    patient: Patient


class PhrGatewayProvidersBody(TypedDict):
    name: str


PhrGatewayProvidersResponse = list[Provider]


class PhrGatewayProviderBody(TypedDict):
    id: str


PhrGatewayProviderResponse = Provider


class PhrGatewayPatientShareShareBody(TypedDict):
    x_token: str
    hip_id: str
    context: str
    hpr_id: str | None
    latitude: float | None
    longitude: float | None
    abha_address: str


class PhrGatewayPatientShareShareResponse(TypedDict):
    pass


class PhrGatewayPatientShareProfileGetTokenDetailsBody(TypedDict):
    x_token: str
    limit: int | None
    page: int | None


class Token(TypedDict):
    id: int
    patientId: str
    tokenNumber: str
    hipId: str
    hipName: str
    hipAddress: str
    expiresIn: str
    clientId: str
    dateCreated: str
    counterCode: str


PhrGatewayPatientShareProfileGetTokenDetailsResponse = list[Token]
