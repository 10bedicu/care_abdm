from typing import TypedDict


class PhrSubscriptionRequestsBody(TypedDict):
    x_token: str
    status: str
    limit: int
    offset: int


class PhrSubscriptionRequestsResponse(TypedDict):
    pass


class PhrSubscriptionRequestBody(TypedDict):
    x_token: str
    request_id: str


class PhrSubscriptionRequestResponse(TypedDict):
    pass


class PhrSubscriptionArtefactBody(TypedDict):
    x_token: str
    subscription_id: str


class PhrSubscriptionArtefactResponse(TypedDict):
    pass


class PhrSubscriptionRequestApproveBody(TypedDict):
    x_token: str
    request_id: str
    subscription: dict


class PhrSubscriptionRequestApproveResponse(TypedDict):
    message: str


class PhrSubscriptionRequestDenyBody(TypedDict):
    x_token: str
    request_id: str
    reason: str


class PhrSubscriptionRequestDenyResponse(TypedDict):
    message: str


class PhrSubscriptionStatusUpdateBody(TypedDict):
    x_token: str
    subscription_id: str
    enable: bool


class PhrSubscriptionStatusUpdateResponse(TypedDict):
    message: str


class PhrSubscriptionEditBody(TypedDict):
    x_token: str
    subscription_id: str
    subscription: dict


class PhrSubscriptionEditResponse(TypedDict):
    message: str
