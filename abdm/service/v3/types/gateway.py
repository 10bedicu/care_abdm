from typing import Literal, TypedDict

from abdm.models import (
    AbhaNumber,
    ConsentArtefact,
    ConsentRequest,
    HealthInformationType,
)
from care.emr.models.patient import Patient
from care.users.models import User


class CareContext(TypedDict):
    hi_type: HealthInformationType
    reference: str
    display: str


class TokenGenerateTokenBody(TypedDict):
    abha_number: AbhaNumber
    hf_id: str
    purpose: Literal["LINK_CARECONTEXT"] | None
    care_contexts: list[CareContext] | None
    reference_id: str | None


class TokenGenerateTokenResponse(TypedDict):
    pass


class LinkCarecontextBody(TypedDict):
    patient: Patient
    care_contexts: list[CareContext]
    hf_id: str
    user: User
    reference_id: str | None


class LinkCarecontextResponse(TypedDict):
    pass


class UserInitiatedLinkingPatientCareContextOnDiscoverBody(TypedDict):
    transaction_id: str
    request_id: str
    patient: Patient
    matched_by: list[Literal["MOBILE", "ABHA_NUMBER", "MR"]]
    hf_id: str


class UserInitiatedLinkingPatientCareContextOnDiscoverResponse(TypedDict):
    pass


class UserInitiatedLinkingLinkCareContextOnInitBody(TypedDict):
    transaction_id: str
    request_id: str
    reference_id: str


class UserInitiatedLinkingLinkCareContextOnInitResponse(TypedDict):
    pass


class UserInitiatedLinkingLinkCareContextOnConfirmBody(TypedDict):
    request_id: str
    patient: Patient
    care_contexts: list[str]
    hf_id: str


class UserInitiatedLinkingLinkCareContextOnConfirmResponse(TypedDict):
    pass


class ConsentRequestHipOnNotifyBody(TypedDict):
    consent_id: str
    request_id: str


class ConsentRequestHipOnNotifyResponse(TypedDict):
    pass


class DataFlowHealthInformationHipOnRequestBody(TypedDict):
    transaction_id: str
    request_id: str


class DataFlowHealthInformationHipOnRequestResponse(TypedDict):
    pass


class DataFlowHealthInformationTransferBody(TypedDict):
    url: str
    consent: ConsentArtefact
    transaction_id: str
    key_material__crypto_algorithm: str
    key_material__curve: str
    key_material__public_key: str
    key_material__nonce: str


class DataFlowHealthInformationTransferResponse(TypedDict):
    pass


class DataFlowHealthInformationNotifyBody(TypedDict):
    transaction_id: str
    consent_id: str
    consent: ConsentArtefact
    notifier__type: Literal["HIP", "HIU"]
    notifier__id: str
    status: Literal["TRANSFERRED", "FAILED"]
    hip_id: str


class DataFlowHealthInformationNotifyResponse(TypedDict):
    pass


class IdentityAuthenticationBody(TypedDict):
    abha_number: AbhaNumber


class Response(TypedDict):
    requestId: str


class IdentityAuthenticationResponse(TypedDict):
    authenticated: bool
    transactionId: str
    abhaAddress: str
    response: Response


class ConsentRequestInitBody(TypedDict):
    consent: ConsentRequest


class ConsentRequestInitResponse(TypedDict):
    pass


class ConsentRequestStatusBody(TypedDict):
    consent: ConsentRequest


class ConsentRequestStatusResponse(TypedDict):
    pass


class ConsentRequestHiuOnNotifyBody(TypedDict):
    consent: ConsentRequest
    request_id: str


class ConsentRequestHiuOnNotifyResponse(TypedDict):
    pass


class ConsentFetchBody(TypedDict):
    artefact: ConsentArtefact


class ConsentFetchResponse(TypedDict):
    pass


class DataFlowHealthInformationRequestBody(TypedDict):
    artefact: ConsentArtefact


class DataFlowHealthInformationRequestResponse(TypedDict):
    pass


class PatientShareOnShareAcknowledgement(TypedDict):
    status: Literal["SUCCESS", "FAILED"]
    abha_address: str
    context: str
    token_number: int
    expiry: int


class PatientShareOnShareError(TypedDict):
    message: str
    code: str


class PatientShareOnShareBody(TypedDict):
    acknowledgement: PatientShareOnShareAcknowledgement | None = None
    error: PatientShareOnShareError | None = None
    request_id: str


class PatientShareOnShareResponse(TypedDict):
    pass


class ScanPayService(TypedDict):
    service_id: str
    name: str
    description: str
    amount: float


class ScanPayProcedure(TypedDict):
    category: str
    services: list[ScanPayService]


class ScanPayError(TypedDict):
    message: str
    code: str


class ScanPayPaymentBundle(TypedDict):
    payment_mode: str
    payment_url: str
    order_number: str
    amount: float
    merchant_id: str
    description: str


class ScanPayAcknowledgement(TypedDict):
    status: str
    abha_address: str
    transaction_id: str | None = None
    order_number: str
    open_order_request_id: str
    payment_date: str | None = None
    payment_receipt_link: str | None = None


class PatientOnShareOpenOrderBody(TypedDict):
    abha_address: str | None = None
    patient_uid: str | None = None
    procedures: list[ScanPayProcedure] | None = None
    error: ScanPayError | None = None
    request_id: str


class PatientOnShareOpenOrderResponse(TypedDict):
    pass


class PatientOnSelectionBody(TypedDict):
    open_order_request_id: str | None = None
    abha_address: str | None = None
    procedures: list[ScanPayProcedure] | None = None
    payment_bundle: ScanPayPaymentBundle | None = None
    error: ScanPayError | None = None
    request_id: str


class PatientOnSelectionResponse(TypedDict):
    pass


class PatientScanPayNotifyBody(TypedDict):
    acknowledgement: ScanPayAcknowledgement
    hip_id: str


class PatientScanPayNotifyResponse(TypedDict):
    pass


class PatientScanPayOnOrderStatusBody(TypedDict):
    acknowledgement: ScanPayAcknowledgement | None = None
    error: ScanPayError | None = None
    request_id: str


class PatientScanPayOnOrderStatusResponse(TypedDict):
    pass
