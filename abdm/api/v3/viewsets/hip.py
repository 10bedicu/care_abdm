import logging
from datetime import datetime
from functools import reduce

from django.contrib.postgres.search import TrigramSimilarity
from django.core.cache import cache
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.hip import (
    ConsentRequestHipNotifySerializer,
    HipHealthInformationRequestSerializer,
    HipLinkCareContextConfirmSerializer,
    HipLinkCareContextInitSerializer,
    HipPatientCareContextDiscoverSerializer,
    HipPatientShareSerializer,
    HipTokenOnGenerateTokenSerializer,
    LinkOnCarecontextSerializer,
)
from abdm.authentication import ABDMAuthentication
from abdm.models import (
    AbhaNumber,
    ConsentArtefact,
    HealthFacility,
    Transaction,
    TransactionType,
)
from abdm.service.helper import uuid
from abdm.service.v3.gateway import GatewayService
from care.emr.models.patient import Patient
from care.emr.resources.patient.spec import GenderChoices

logger = logging.getLogger(__name__)


@extend_schema(tags=["ABDM: HIP"])
class HIPViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    @action(detail=False, methods=["POST"], url_path="link_care_context")
    def link__carecontext(self, request):
        return Response(
            {
                "detail": "All care contexts are linked automatically, and no manual intervention is required",
            },
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(tags=["ABDM: HIP Callback"])
class HIPCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "hip__token__on_generate_token": HipTokenOnGenerateTokenSerializer,
        "link__on_carecontext": LinkOnCarecontextSerializer,
        "hip__patient__care_context__discover": HipPatientCareContextDiscoverSerializer,
        "hip__link__care_context__init": HipLinkCareContextInitSerializer,
        "hip__link__care_context__confirm": HipLinkCareContextConfirmSerializer,
        "consent__request__hip__notify": ConsentRequestHipNotifySerializer,
        "hip__health_information__request": HipHealthInformationRequestSerializer,
        "hip__patient__share": HipPatientShareSerializer,
    }

    def get_patient_by_abha_id(self, abha_id: str):
        patient = Patient.objects.filter(
            Q(abha_number__abha_number=abha_id) | Q(abha_number__health_id=abha_id)
        ).first()

        if not patient and "@" in abha_id:
            # TODO: get abha number using gateway api and search patient
            pass

        return patient

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as exception:
            logger.warning(
                f"Validation failed for request data: {request.data}, "
                f"Path: {request.path}, Method: {request.method}, "
                f"Error details: {exception!s}"
            )

            raise exception

        return serializer.validated_data

    @action(detail=False, methods=["POST"], url_path="hip/token/on-generate-token")
    def hip__token__on_generate_token(self, request):
        validated_data = self.validate_request(request)

        cached_data = cache.get(
            "abdm_link_care_context__"
            + str(validated_data.get("response").get("requestId"))
        )

        if not cached_data:
            logger.warning(
                f"Request ID: {validated_data.get('response').get('requestId')!s} not found in cache"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        abha_number = AbhaNumber.objects.filter(
            abha_number=cached_data.get("abha_number")
        ).first()

        if not abha_number:
            logger.warning(
                f"ABHA Number: {cached_data.get('abha_number')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        cache.set(
            "abdm_link_token__" + abha_number.health_id,
            validated_data.get("linkToken"),
            timeout=60 * 30,
        )

        if cached_data.get("purpose") == "LINK_CARECONTEXT":
            GatewayService.link__carecontext(
                {
                    "patient": abha_number.patient,
                    "care_contexts": cached_data.get("care_contexts", []),
                    "user": request.user,
                }
            )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="link/on_carecontext")
    def link__on_carecontext(self, request):
        self.validate_request(request)

        # TODO: delete care context transaction if it failed

        # TODO: handle failed link requests

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(
        detail=False, methods=["POST"], url_path="hip/patient/care-context/discover"
    )
    def hip__patient__care_context__discover(self, request):
        validated_data = self.validate_request(request)

        patient_data = validated_data.get("patient")
        identifiers = [
            *patient_data.get("verifiedIdentifiers"),
            *patient_data.get("unverifiedIdentifiers"),
        ]

        health_id_number = next(
            filter(lambda x: x.get("type") == "ABHA_NUMBER", identifiers), {}
        ).get("value")
        patient = Patient.objects.filter(
            Q(abha_number__abha_number=health_id_number)
            | Q(abha_number__health_id=patient_data.get("id"))
        ).first()
        matched_by = "ABHA_NUMBER"

        if not patient:
            mobile = next(
                filter(lambda x: x.get("type") == "MOBILE", identifiers), {}
            ).get("value")
            patient = (
                Patient.objects.annotate(
                    similarity=TrigramSimilarity("name", patient_data.get("name"))
                )
                .filter(
                    Q(phone_number=mobile) | Q(phone_number="+91" + mobile),
                    Q(
                        date_of_birth__year__gte=patient_data.get("yearOfBirth") - 5,
                        date_of_birth__year__lte=patient_data.get("yearOfBirth") + 5,
                    )
                    | Q(year_of_birth__gte=patient_data.get("yearOfBirth")) - 5,
                    year_of_birth__lte=patient_data.get("yearOfBirth") + 5,
                    gender={"M": 1, "F": 2, "O": 3}.get(patient_data.get("gender"), 3),
                    similarity__gt=0.3,
                )
                .order_by("-similarity")
                .first()
            )
            matched_by = "MOBILE"

        if not patient:
            # TODO: handle MR matching
            pass

        GatewayService.user_initiated_linking__patient__care_context__on_discover(
            {
                "transaction_id": str(validated_data.get("transactionId")),
                "request_id": request.headers.get("REQUEST-ID"),
                "patient": patient,
                "matched_by": [matched_by],
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], url_path="hip/link/care-context/init")
    def hip__link__care_context__init(self, request):
        validated_data = self.validate_request(request)
        care_contexts = reduce(
            lambda acc, patient: acc
            + [
                context.get("referenceNumber")
                for context in patient.get("careContexts", [])
            ],
            validated_data.get("patient", []),
            [],
        )

        reference_id = uuid()
        cache.set(
            "abdm_user_initiated_linking__" + reference_id,
            {
                "reference_id": reference_id,
                # TODO: generate OTP and send it to the patient
                "otp": "000000",
                "abha_address": validated_data.get("abhaAddress"),
                "patient_id": validated_data.get("patient", [{}])[0].get(
                    "referenceNumber"
                ),
                "care_contexts": care_contexts,
            },
        )

        GatewayService.user_initiated_linking__link__care_context__on_init(
            {
                "transaction_id": str(validated_data.get("transactionId")),
                "request_id": request.headers.get("REQUEST-ID"),
                "reference_id": reference_id,
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], url_path="hip/link/care-context/confirm")
    def hip__link__care_context__confirm(self, request):
        validated_data = self.validate_request(request)

        cached_data = cache.get(
            "abdm_user_initiated_linking__"
            + validated_data.get("confirmation").get("linkRefNumber")
        )

        if not cached_data:
            logger.warning(
                f"Reference ID: {validated_data.get('confirmation').get('linkRefNumber')} not found in cache"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        if cached_data.get("otp") != validated_data.get("confirmation").get("token"):
            logger.warning(
                f"Invalid OTP: {validated_data.get('confirmation').get('token')} for Reference ID: {validated_data.get('confirmation').get('linkRefNumber')}"
            )

            return Response(status=status.HTTP_400_BAD_REQUEST)

        patient_id = cached_data.get("patient_id")
        patient = Patient.objects.filter(external_id=patient_id).first()

        if not patient:
            logger.warning(f"Patient with ID: {patient_id} not found in the database")

            return Response(status=status.HTTP_400_BAD_REQUEST)

        GatewayService.user_initiated_linking__link__care_context__on_confirm(
            {
                "request_id": request.headers.get("REQUEST-ID"),
                "patient": patient,
                "care_contexts": cached_data.get("care_contexts"),
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="consent/request/hip/notify")
    def consent__request__hip__notify(self, request):
        validated_data = self.validate_request(request)

        notification = validated_data.get("notification")
        consent_detail = notification.get("consentDetail")
        permission = consent_detail.get("permission")
        frequency = permission.get("frequency")

        patient = self.get_patient_by_abha_id(consent_detail.get("patient").get("id"))

        if not patient:
            logger.warning(
                f"Patient with ABHA ID: {consent_detail.get('patient').get('id')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        ConsentArtefact.objects.update_or_create(
            consent_id=notification.get("consentId"),
            defaults={
                "patient_abha": patient.abha_number,
                "care_contexts": consent_detail.get("careContexts"),
                "status": notification.get("status"),
                "purpose": consent_detail.get("purpose").get("code"),
                "hi_types": consent_detail.get("hiTypes"),
                "hip": consent_detail.get("hip").get("id"),
                "cm": consent_detail.get("consentManager").get("id"),
                "requester": request.user,
                "access_mode": permission.get("accessMode"),
                "from_time": permission.get("dateRange").get("fromTime"),
                "to_time": permission.get("dateRange").get("toTime"),
                "expiry": permission.get("dataEraseAt"),
                "frequency_unit": frequency.get("unit"),
                "frequency_value": frequency.get("value"),
                "frequency_repeats": frequency.get("repeats"),
                "signature": notification.get("signature"),
            },
        )

        GatewayService.consent__request__hip__on_notify(
            {
                "consent_id": str(notification.get("consentId")),
                "request_id": request.headers.get("REQUEST-ID"),
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="hip/health-information/request")
    def hip__health_information__request(self, request):
        validated_data = self.validate_request(request)

        hi_request = validated_data.get("hiRequest")
        key_material = hi_request.get("keyMaterial")

        consent = ConsentArtefact.objects.filter(
            consent_id=hi_request.get("consent").get("id")
        ).first()

        if not consent:
            logger.warning(
                f"Consent with ID: {hi_request.get('consent').get('id')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        GatewayService.data_flow__health_information__hip__on_request(
            {
                "request_id": request.headers.get("REQUEST-ID"),
                "transaction_id": str(validated_data.get("transactionId")),
            }
        )

        try:
            GatewayService.data_flow__health_information__transfer(
                {
                    "transaction_id": str(validated_data.get("transactionId")),
                    "consent": consent,
                    "url": hi_request.get("dataPushUrl"),
                    "key_material__crypto_algorithm": key_material.get("cryptoAlg"),
                    "key_material__curve": key_material.get("curve"),
                    "key_material__public_key": key_material.get("dhPublicKey").get(
                        "keyValue"
                    ),
                    "key_material__nonce": key_material.get("nonce"),
                }
            )

            GatewayService.data_flow__health_information__notify(
                {
                    "consent": consent,
                    "consent_id": str(consent.consent_id),
                    "transaction_id": str(validated_data.get("transactionId")),
                    "notifier__type": "HIP",
                    "notifier__id": request.headers.get("X-HIP-ID"),
                    "status": "TRANSFERRED",
                    "hip_id": request.headers.get("X-HIP-ID"),
                }
            )
        except Exception as exception:
            logger.error(
                f"Error occurred while transferring health information: {exception!s}"
            )

            GatewayService.data_flow__health_information__notify(
                {
                    "consent": consent,
                    "consent_id": str(consent.consent_id),
                    "transaction_id": str(validated_data.get("transactionId")),
                    "notifier__type": "HIP",
                    "notifier__id": request.headers.get("X-HIP-ID"),
                    "status": "FAILED",
                    "hip_id": request.headers.get("X-HIP-ID"),
                }
            )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["POST"], url_path="hip/patient/share")
    def hip__patient__share(self, request):
        validated_data = self.validate_request(request)

        hip_id = validated_data.get("metaData").get("hipId")
        health_facility = HealthFacility.objects.filter(hf_id=hip_id).first()

        if not health_facility:
            logger.warning(
                f"Health Facility with ID: {hip_id} not found in the database"
            )

            GatewayService.patient_share__on_share(
                {
                    "status": "FAILED",
                    "abha_address": validated_data.get("profile")
                    .get("patient")
                    .get("abhaAddress"),
                    "context": validated_data.get("metaData").get("context"),
                    "request_id": request.headers.get("REQUEST-ID"),
                }
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        patient_data = validated_data.get("profile").get("patient")
        abha_number = AbhaNumber.objects.filter(
            Q(abha_number=patient_data.get("abhaNumber"))
            | Q(health_id=patient_data.get("abhaAddress"))
        ).first()
        # TODO: consider the case of existing patient without abha number

        is_existing_patient = True
        if not abha_number:
            is_existing_patient = False

            full_address = ", ".join(
                filter(
                    lambda x: x,
                    [
                        patient_data.get("address").get("line"),
                        patient_data.get("address").get("district"),
                        patient_data.get("address").get("state"),
                        patient_data.get("address").get("pinCode"),
                    ],
                )
            )
            phone_number = (
                "+91" + patient_data.get("phoneNumber", "").replace(" ", "")[-10:]
            )
            date_of_birth = datetime.strptime(
                f"{patient_data.get('yearOfBirth')}-{patient_data.get('monthOfBirth')}-{patient_data.get('dayOfBirth')}",
                "%Y-%m-%d",
            ).date()
            patient = Patient.objects.create(
                name=patient_data.get("name"),
                gender={
                    "M": GenderChoices.male,
                    "F": GenderChoices.female,
                    "O": GenderChoices.non_binary,
                }.get(patient_data.get("gender"), "O"),
                date_of_birth=date_of_birth,
                phone_number=phone_number,
                emergency_phone_number=phone_number,
                address=full_address,
                permanent_address=full_address,
                pincode=patient_data.get("address").get("pinCode"),
                geo_organization=None,
            )

            abha_number = AbhaNumber.objects.create(
                patient=patient,
                abha_number=patient_data.get("abhaNumber"),
                health_id=patient_data.get("abhaAddress"),
                name=patient_data.get("name"),
                gender=patient_data.get("gender"),
                date_of_birth=date_of_birth,
                address=patient_data.get("address").get("line"),
                district=patient_data.get("address").get("district"),
                state=patient_data.get("address").get("state"),
                pincode=patient_data.get("address").get("pinCode"),
                mobile=patient_data.get("phoneNumber"),
            )

        # TODO: add the patient to the facility queue

        cached_data = cache.get("abdm_patient_share__" + abha_number.health_id)

        if cached_data:
            GatewayService.patient_share__on_share(
                {
                    "status": "FAILED",
                    "abha_address": abha_number.health_id,
                    "context": validated_data.get("metaData").get("context"),
                    "request_id": request.headers.get("REQUEST-ID"),
                }
            )

            return Response(status=status.HTTP_429_TOO_MANY_REQUESTS)

        token_number = len(cache.client.get_client().keys("abdm_patient_share__*")) + 1

        cache.set(
            "abdm_patient_share__" + abha_number.health_id,
            token_number,
            timeout=600,
        )

        GatewayService.patient_share__on_share(
            {
                "status": "SUCCESS",
                "abha_address": abha_number.health_id,
                "context": validated_data.get("metaData").get("context"),
                "token_number": token_number,
                "expiry": 600,
                "request_id": request.headers.get("REQUEST-ID"),
            }
        )

        Transaction.objects.create(
            reference_id=uuid(),
            type=TransactionType.SCAN_AND_SHARE,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "is_existing_patient": is_existing_patient,
                "token": str(token_number),
            },
        )

        return Response(validated_data, status=status.HTTP_200_OK)
