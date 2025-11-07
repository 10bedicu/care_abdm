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
    hiu_id: str
    subscription: dict


class PhrSubscriptionEditResponse(TypedDict):
    message: str


class PhrSubscribedLockersBody(TypedDict):
    x_token: str


class PatientLocker(TypedDict):
    id: int
    lockerId: str
    lockerName: str
    dateCreated: str
    dateModified: str


PhrSubscribedLockersResponse = list[PatientLocker]


class PhrSubscribedLockerBody(TypedDict):
    x_token: str
    locker_id: str


class PhrSubscribedLockerResponse(TypedDict):
    pass


class PhrSubscriptionRequestInitBody(TypedDict):
    abha_address: str


class PhrSubscriptionRequestInitResponse(TypedDict):
    pass


class PhrSubscriptionRequestOnNotifyBody(TypedDict):
    request_id: str
    subscription_request_id: str


class PhrSubscriptionRequestOnNotifyResponse(TypedDict):
    pass


class PhrSubscriptionOnNotifyBody(TypedDict):
    request_id: str
    event_id: str


class PhrSubscriptionOnNotifyResponse(TypedDict):
    pass
