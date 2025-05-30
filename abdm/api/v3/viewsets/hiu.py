import json
import logging

from abdm.api.serializers.consent import ConsentRequestSerializer
from abdm.api.v3.serializers.hiu import (
    ConsentFetchSerializer,
    ConsentRequestStatusSerializer,
    DataFlowHealthInformationRequestSerializer,
    HiuConsentOnFetchSerializer,
    HiuConsentRequestNotifySerializer,
    HiuConsentRequestOnInitSerializer,
    HiuConsentRequestOnStatusSerializer,
    HiuHealthInformationOnRequestSerializer,
    HiuHealthInformationTransferSerializer,
    IdentityAuthenticationSerializer,
)
from abdm.api.viewsets.consent import ConsentViewSet
from abdm.authentication import ABDMAuthentication
from abdm.models import (
    AbhaNumber,
    ConsentArtefact,
    ConsentRequest,
    Transaction,
    TransactionType,
)
from abdm.models.base import Status
from abdm.service.v3.gateway import GatewayService
from abdm.utils.cipher import Cipher
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from care.facility.models import FileUpload

logger = logging.getLogger(__name__)


class HIUViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    serializer_action_classes = {
        "identity__authentication": IdentityAuthenticationSerializer,
        "consent__request__init": ConsentRequestSerializer,
        "consent__request__status": ConsentRequestStatusSerializer,
        "consent__fetch": ConsentFetchSerializer,
        "data_flow__health_information__request": DataFlowHealthInformationRequestSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @action(detail=False, methods=["POST"], url_path="verify_identity")
    def identity__authentication(self, request):
        validated_data = self.validate_request(request)

        abha_number = AbhaNumber.objects.filter(
            Q(abha_number=validated_data.get("abha_number"))
            | Q(patient__external_id=validated_data.get("patient"))
        ).first()

        if not abha_number:
            return Response(
                {"detail": "No Abha Number Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = GatewayService.identity__authentication({"abha_number": abha_number})

        return Response(
            {
                "status": result.get("authenticated"),
                "abha_address": result.get("abhaAddress"),
                "transaction_id": result.get("transactionId"),
                "requestId": result.get("response").get("requestId"),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["POST"], url_path="create_consent_request")
    def consent__request__init(self, request):
        return ConsentViewSet().create(request)

    @action(detail=False, methods=["POST"], url_path="consent_request_status")
    def consent__request__status(self, request):
        validated_data = self.validate_request(request)

        consent = ConsentRequest.objects.filter(
            external_id=validated_data.get("consent_request")
        ).first()

        if not consent:
            return Response(
                {"detail": "No Consent Request Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        GatewayService.consent__request__status(
            {
                "consent": consent,
            }
        )

        return Response(
            {"detail": "Consent Request Status Initiated"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["POST"], url_path="fetch_consent_artefact")
    def consent__fetch(self, request):
        validated_data = self.validate_request(request)

        artefacts = ConsentArtefact.objects.filter(
            Q(external_id=validated_data.get("consent_artefact"))
            | Q(consent_request__external_id=validated_data.get("consent_request"))
        )

        if len(artefacts) == 0:
            return Response(
                {"detail": "No Consent Artefact Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        for artefact in artefacts:
            GatewayService.consent__fetch(
                {
                    "artefact": artefact,
                }
            )

        return Response(
            {"detail": "Consent Artefact Fetch Initiated"},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["POST"], url_path="request_health_information")
    def data_flow__health_information__request(self, request):
        validated_data = self.validate_request(request)

        artefact = ConsentArtefact.objects.filter(
            external_id=validated_data.get("consent_artefact")
        ).first()

        if not artefact:
            return Response(
                {"detail": "No Consent Artefact Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        GatewayService.data_flow__health_information__request(
            {
                "artefact": artefact,
            }
        )

        return Response(
            {"detail": "Health Information Request Initiated"},
            status=status.HTTP_202_ACCEPTED,
        )


class HIUCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    serializer_action_classes = {
        "hiu__consent__request__on_init": HiuConsentRequestOnInitSerializer,
        "hiu__consent__request__on_status": HiuConsentRequestOnStatusSerializer,
        "hiu__consent__request__notify": HiuConsentRequestNotifySerializer,
        "hiu__consent__on_fetch": HiuConsentOnFetchSerializer,
        "hiu__health_information__on_request": HiuHealthInformationOnRequestSerializer,
        "hiu__health_information__transfer": HiuHealthInformationTransferSerializer,
    }

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
                f"Error details: {str(exception)}"
            )

            raise exception

        return serializer.validated_data

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/on-init")
    def hiu__consent__request__on_init(self, request):
        validated_data = self.validate_request(request)
        request_id = validated_data.get("response").get("requestId")

        consent = ConsentRequest.objects.filter(external_id=request_id).first()

        if not consent:
            logger.warning(f"Consent Request: {request_id} not found in the database")

            return Response(status=status.HTTP_404_NOT_FOUND)

        if "consentRequest" in validated_data and validated_data.get("consentRequest"):
            consent_id = validated_data.get("consentRequest").get("id")

            consent.consent_id = consent_id
            consent.save()

        if "error" in validated_data and validated_data.get("error"):
            logger.warning(
                f"Consent Request: {request_id}, Error in Consent Request while On Init: {validated_data.get('error').get('message')}"
            )

        return Response(
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/on-status")
    def hiu__consent__request__on_status(self, request):
        validated_data = self.validate_request(request)

        consent_request = validated_data.get("consentRequest")
        consent_status = consent_request.get("status")
        consent_artefacts = consent_request.get("consentArtefacts")

        consent = ConsentRequest.objects.filter(
            consent_id=consent_request.get("id")
        ).first()

        if not consent:
            logger.warning(
                f"Consent Request: {consent_request.get('id')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        if consent_status != Status.DENIED:
            for artefact in consent_artefacts:
                consent_artefact = ConsentArtefact.objects.filter(
                    external_id=artefact.get("id")
                ).first()

                if not consent_artefact:
                    consent_artefact = ConsentArtefact.objects.create(
                        external_id=artefact.get("id"),
                        consent_request=consent,
                        **consent.consent_details_dict(),
                    )

                consent_artefact.status = consent_status
                consent_artefact.save()

        consent.status = consent_status
        consent.save()

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], url_path="hiu/consent/request/notify")
    def hiu__consent__request__notify(self, request):
        validated_data = self.validate_request(request)

        notification = validated_data.get("notification")
        consent_status = notification.get("status")
        consent_artefacts = notification.get("consentArtefacts", [])

        consent = ConsentRequest.objects.filter(
            consent_id=notification.get("consentRequestId")
        ).first()

        if not consent:
            logger.warning(
                f"Consent Request: {notification.get('consentRequestId')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        if consent_status != Status.DENIED:
            for artefact in consent_artefacts:
                consent_artefact = ConsentArtefact.objects.filter(
                    external_id=artefact.get("id")
                ).first()

                if not consent_artefact:
                    consent_artefact = ConsentArtefact.objects.create(
                        external_id=artefact.get("id"),
                        consent_request=consent,
                        **consent.consent_details_dict(),
                    )

                consent_artefact.status = consent_status
                consent_artefact.save()

        consent.status = consent_status
        consent.save()

        if consent_status == Status.GRANTED:
            GatewayService.consent__request__hiu__on_notify(
                {
                    "consent": consent,
                    "request_id": request.headers.get("REQUEST-ID"),
                }
            )

            for artefact in consent.consent_artefacts.all():
                GatewayService.consent__fetch(
                    {
                        "artefact": artefact,
                    }
                )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], url_path="hiu/consent/on-fetch")
    def hiu__consent__on_fetch(self, request):
        validated_data = self.validate_request(request)

        consent = validated_data.get("consent")
        consent_detail = consent.get("consentDetail")

        # updating an existing consent artefact
        (artefact, _) = ConsentArtefact.objects.update_or_create(
            external_id=consent_detail.get("consentId"),
            defaults={
                "hip": consent_detail.get("hip", {}).get("id"),
                "hiu": consent_detail.get("hiu", {}).get("id"),
                "cm": consent_detail.get("consentManager", {}).get("id"),
                "care_contexts": consent_detail.get("careContexts", []),
                "hi_types": consent_detail.get("hiTypes", []),
                "status": consent.get("status"),
                "access_mode": consent_detail.get("permission").get("accessMode"),
                "from_time": consent_detail.get("permission")
                .get("dateRange")
                .get("from"),
                "to_time": consent_detail.get("permission").get("dateRange").get("to"),
                "expiry": consent_detail.get("permission").get("dataEraseAt"),
                "frequency_unit": consent_detail.get("permission")
                .get("frequency")
                .get("unit"),
                "frequency_value": consent_detail.get("permission")
                .get("frequency")
                .get("value"),
                "frequency_repeats": consent_detail.get("permission")
                .get("frequency")
                .get("repeats"),
                "signature": consent.get("signature"),
            },
        )

        GatewayService.data_flow__health_information__request(
            {
                "artefact": artefact,
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["POST"], url_path="hiu/health-information/on-request"
    )
    def hiu__health_information__on_request(self, request):
        validated_data = self.validate_request(request)

        if "hiRequest" in validated_data:
            artefact = ConsentArtefact.objects.filter(
                consent_id=validated_data.get("response").get("requestId")
            ).first()

            if not artefact:
                logger.warning(
                    f"Consent Artefact: {validated_data.get('response').get('requestId')} not found in the database"
                )

                return Response(status=status.HTTP_404_NOT_FOUND)

            artefact.consent_id = validated_data.get("hiRequest").get("transactionId")
            artefact.save()

        if "error" in validated_data:
            logger.warning(
                f"Consent Artefact: {validated_data.get('response').get('requestId')}, Error in Health Information Request: {validated_data.get('error')}"
            )

        return Response(status=status.HTTP_202_ACCEPTED)

    @action(
        detail=False,
        methods=["POST"],
        url_path="hiu/health-information/transfer",
    )
    def hiu__health_information__transfer(self, request):
        validated_data = self.validate_request(request)

        key_material = validated_data.get("keyMaterial")

        artefact = ConsentArtefact.objects.filter(
            consent_id=validated_data.get("transactionId")
        ).first()

        if not artefact:
            logger.warning(
                f"Consent Artefact: {validated_data.get('transactionId')} not found in the database"
            )

            return Response(status=status.HTTP_404_NOT_FOUND)

        cipher = Cipher(
            external_public_key=key_material.get("dhPublicKey").get("keyValue"),
            external_nonce=key_material.get("nonce"),
            internal_private_key=artefact.key_material_private_key,
            internal_public_key=artefact.key_material_public_key,
            internal_nonce=artefact.key_material_nonce,
        )

        entries = []
        for entry in validated_data.get("entries"):
            if "content" in entry:
                entries.append(
                    {
                        "content": cipher.decrypt(entry.get("content")),
                        "care_context_reference": entry.get("careContextReference"),
                    }
                )

            if "link" in entry:
                # TODO: handle link entry (link to raw data)
                pass

        file = FileUpload(
            internal_name=f"{validated_data.get('pageNumber')} / {validated_data.get('pageCount')} -- {artefact.external_id}.json",
            file_type=FileUpload.FileType.ABDM_HEALTH_INFORMATION.value,
            associating_id=artefact.consent_request.external_id,
        )
        file.put_object(json.dumps(entries), ContentType="application/json")
        file.upload_completed = True
        file.save()

        Transaction.objects.create(
            reference_id=validated_data.get("transactionId"),
            type=TransactionType.EXCHANGE_DATA,
            meta_data={
                "consent_artefact": str(artefact.external_id),
                "is_incoming": True,
            },
        )

        GatewayService.data_flow__health_information__notify(
            {
                "consent": artefact,
                "consent_id": str(artefact.artefact_id),
                "transaction_id": str(artefact.transaction_id),
                "notifier__type": "HIU",
                "notifier__id": artefact.hiu,
                "status": "TRANSFERRED",
                "hip_id": artefact.hip,
            }
        )

        return Response(status=status.HTTP_202_ACCEPTED)
