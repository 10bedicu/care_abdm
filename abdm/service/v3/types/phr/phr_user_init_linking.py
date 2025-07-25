from typing import Literal, TypedDict

from abdm.models import HealthInformationType


class Hip(TypedDict):
    id: str
    name: str | None
    type: str | None


class DiscoveryIdentifiers(TypedDict):
    type: list[Literal["MOBILE", "MR", "ABHA_NUMBER", "ABHA_ADDRESS"]]
    value: str


class CareContext(TypedDict):
    referenceNumber: str
    display: str


class Patient(TypedDict):
    referenceNumber: str
    display: str
    hiType: HealthInformationType
    careContexts: list[CareContext]
    count: int


class PhrUserInitLinkingCareContextLinksBody(TypedDict):
    x_token: str


class PhrUserInitLinkingCareContextLinksResponse(TypedDict):
    patient: Patient


class PhrUserInitLinkingCareContextDiscoverBody(TypedDict):
    x_token: str
    hip: Hip
    unverified_identifiers: list[DiscoveryIdentifiers]


class PhrUserInitLinkingCareContextDiscoverResponse(TypedDict):
    pass


class PhrUserInitLinkingCareContextInitBody(TypedDict):
    x_token: str
    transaction_id: str
    patient: list[Patient]


class PhrUserInitLinkingCareContextInitResponse(TypedDict):
    pass


class PhrUserInitLinkingCareContextConfirmBody(TypedDict):
    x_token: str
    link_ref_number: str
    token: str


class PhrUserInitLinkingCareContextConfirmResponse(TypedDict):
    pass
