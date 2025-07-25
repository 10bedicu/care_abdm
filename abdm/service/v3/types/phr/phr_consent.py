from typing import Literal, TypedDict


class PhrConsentRequestsBody(TypedDict):
    x_token: str
    limit: int
    offset: int
    status: list[Literal["ALL", "REQUESTED", "EXPIRED", "REVOKED", "GRANTED", "DENIED"]]


class PhrConsentRequestsResponse(TypedDict):
    pass


class PhrConsentRequestBody(TypedDict):
    x_token: str
    request_id: str


class PhrConsentRequestResponse(TypedDict):
    pass


class PhrConsentArtefactsBody(TypedDict):
    x_token: str
    limit: int
    offset: int
    status: list[Literal["ALL", "REQUESTED", "EXPIRED", "REVOKED", "GRANTED", "DENIED"]]


class PhrConsentArtefactsResponse(TypedDict):
    pass


class PhrConsentRequestArtefactsBody(TypedDict):
    x_token: str
    request_id: str


class PhrConsentRequestArtefactsResponse(TypedDict):
    pass


class PhrConsentArtefactBody(TypedDict):
    x_token: str
    artefact_id: str


class PhrConsentArtefactResponse(TypedDict):
    pass


class PhrConsentRequestApproveBody(TypedDict):
    x_token: str
    request_id: str
    consents: list[dict]


class PhrConsentRequestApproveResponse(TypedDict):
    message: str
    consents: list[str]


class PhrConsentRequestDenyBody(TypedDict):
    x_token: str
    request_id: str
    reason: str


class PhrConsentRequestDenyResponse(TypedDict):
    status: str


class PhrConsentRequestRevokeBody(TypedDict):
    x_token: str
    consents: list[str]


class PhrConsentRequestRevokeResponse(TypedDict):
    message: str


class PhrConsentAutoApproveSetupBody(TypedDict):
    x_token: str


class PhrConsentAutoApproveSetupResponse(TypedDict):
    pass


class PhrConsentAutoApproveUpdateBody(TypedDict):
    x_token: str
    auto_approve_request_id: str
    enable: bool


class PhrConsentAutoApproveUpdateResponse(TypedDict):
    message: str
