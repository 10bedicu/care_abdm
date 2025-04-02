from datetime import datetime

from django.db.models import Q
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.abha_number import AbhaNumberSerializer
from abdm.api.v3.serializers.health_id import (
    AbhaCreateAbhaAddressSuggestionSerializer,
    AbhaCreateEnrolAbhaAddressSerializer,
    AbhaCreateLinkMobileNumberSerializer,
    AbhaCreateSendAadhaarOtpSerializer,
    AbhaCreateVerifyAadhaarOtpSerializer,
    AbhaCreateVerifyMobileOtpSerializer,
    AbhaLoginCheckAuthMethodsSerializer,
    AbhaLoginSendOtpSerializer,
    AbhaLoginVerifyOtpSerializer,
    LinkAbhaNumberAndPatientSerializer,
)
from abdm.models import AbhaNumber, Transaction, TransactionType
from abdm.service.helper import generate_care_contexts_for_existing_data
from abdm.service.v3.gateway import GatewayService
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings
from care.emr.models.patient import Patient
from care.security.authorization.base import AuthorizationController


@extend_schema(tags=["ABDM: Health ID"])
class HealthIdViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    serializer_action_classes = {
        "abha_create__send_aadhaar_otp": AbhaCreateSendAadhaarOtpSerializer,
        "abha_create__verify_aadhaar_otp": AbhaCreateVerifyAadhaarOtpSerializer,
        "abha_create__link_mobile_number": AbhaCreateLinkMobileNumberSerializer,
        "abha_create__verify_mobile_otp": AbhaCreateVerifyMobileOtpSerializer,
        "abha_create__abha_address_suggestion": AbhaCreateAbhaAddressSuggestionSerializer,
        "abha_create__enrol_abha_address": AbhaCreateEnrolAbhaAddressSerializer,
        "abha_login__send_otp": AbhaLoginSendOtpSerializer,
        "abha_login__verify_otp": AbhaLoginVerifyOtpSerializer,
        "abha_login__check_auth_methods": AbhaLoginCheckAuthMethodsSerializer,
        "link_abha_number_and_patient": LinkAbhaNumberAndPatientSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @action(detail=False, methods=["post"], url_path="link_patient")
    def link_abha_number_and_patient(self, request):
        validated_data = self.validate_request(request)

        patient = Patient.objects.filter(
            external_id=validated_data.get("patient")
        ).first()

        if not AuthorizationController.call(
            "can_write_patient_obj", self.request.user, patient
        ):
            return Response(
                {
                    "detail": "Patient not found or you do not have permission to access the patient",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if hasattr(patient, "abha_number"):
            return Response(
                {
                    "detail": "Patient already linked to an ABHA Number",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        abha_number = AbhaNumber.objects.filter(
            external_id=validated_data.get("abha_number")
        ).first()

        if not abha_number:
            return Response(
                {
                    "detail": "ABHA Number not found",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if abha_number.patient is not None:
            return Response(
                {
                    "detail": "ABHA Number already linked to a patient",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        abha_number.patient = patient
        abha_number.save()

        care_contexts = generate_care_contexts_for_existing_data(patient)
        if len(care_contexts) > 0:
            GatewayService.link__carecontext(
                {
                    "patient": patient,
                    "care_contexts": care_contexts,
                    "user": request.user,
                }
            )

        return Response(
            AbhaNumberSerializer(abha_number).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/send_aadhaar_otp")
    def abha_create__send_aadhaar_otp(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__request__otp(
            {
                "scope": ["abha-enrol"],
                "transaction_id": str(validated_data.get("transaction_id", "")),
                "type": "aadhaar",
                "value": validated_data.get("aadhaar"),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/verify_aadhaar_otp")
    def abha_create__verify_aadhaar_otp(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__enrol__byAadhaar(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "otp": validated_data.get("otp"),
                "mobile": validated_data.get("mobile"),
            }
        )

        abha_profile = result.get("ABHAProfile")
        token = result.get("tokens")
        (abha_number, created) = AbhaNumber.objects.update_or_create(
            abha_number=abha_profile.get("ABHANumber"),
            defaults={
                "abha_number": abha_profile.get("ABHANumber"),
                "health_id": abha_profile.get("phrAddress", [None])[0],
                "name": " ".join(
                    list(
                        filter(
                            lambda x: x.strip(),
                            [
                                abha_profile.get("firstName"),
                                abha_profile.get("middleName"),
                                abha_profile.get("lastName"),
                            ],
                        )
                    )
                ),
                "first_name": abha_profile.get("firstName"),
                "middle_name": abha_profile.get("middleName"),
                "last_name": abha_profile.get("lastName"),
                "gender": abha_profile.get("gender"),
                "date_of_birth": datetime.strptime(
                    abha_profile.get("dob"), "%d-%m-%Y"
                ).strftime("%Y-%m-%d"),
                "address": abha_profile.get("address"),
                "district": abha_profile.get("districtName"),
                "state": abha_profile.get("stateName"),
                "pincode": abha_profile.get("pinCode"),
                "email": abha_profile.get("email"),
                "mobile": abha_profile.get("mobile"),
                "profile_photo": abha_profile.get("photo"),
                "new": result.get("isNew"),
                "access_token": token.get("token"),
                "refresh_token": token.get("refreshToken"),
            },
        )

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "create_via_aadhaar_otp",
            },
            created_by=request.user,
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
                "abha_number": AbhaNumberSerializer(abha_number).data,
                "created": created,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/link_mobile_number")
    def abha_create__link_mobile_number(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__request__otp(
            {
                "scope": ["abha-enrol", "mobile-verify"],
                "type": "mobile",
                "value": validated_data.get("mobile"),
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/verify_mobile_otp")
    def abha_create__verify_mobile_otp(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__auth__byAbdm(
            {
                "scope": ["abha-enrol", "mobile-verify"],
                "transaction_id": str(validated_data.get("transaction_id")),
                "otp": validated_data.get("otp"),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/abha_address_suggestion")
    def abha_create__abha_address_suggestion(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__enrol__suggestion(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_addresses": result.get("abhaAddressList"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/enrol_abha_address")
    def abha_create__enrol_abha_address(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__enrol__abha_address(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "abha_address": validated_data.get("abha_address"),
                "preferred": 1,
            }
        )

        abha_number = AbhaNumber.objects.filter(
            abha_number=result.get("healthIdNumber")
        ).first()

        if not abha_number:
            return Response(
                {
                    "detail": "Couldn't enroll abha address, ABHA Number not found, Please try again later",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = HealthIdService.profile__account(
            {"x_token": abha_number.access_token}
        )

        (abha_number, _) = AbhaNumber.objects.update_or_create(
            abha_number=profile_result.get("ABHANumber"),
            defaults={
                "abha_number": profile_result.get("ABHANumber"),
                "health_id": profile_result.get("preferredAbhaAddress"),
                "name": profile_result.get("name"),
                "first_name": profile_result.get("firstName"),
                "middle_name": profile_result.get("middleName"),
                "last_name": profile_result.get("lastName"),
                "gender": profile_result.get("gender"),
                "date_of_birth": str(
                    datetime.strptime(
                        f"{profile_result.get('yearOfBirth')}-{profile_result.get('monthOfBirth')}-{profile_result.get('dayOfBirth')}",
                        "%Y-%m-%d",
                    )
                )[0:10],
                "address": profile_result.get("address"),
                "district": profile_result.get("districtName"),
                "state": profile_result.get("stateName"),
                "pincode": profile_result.get("pincode"),
                "email": profile_result.get("email"),
                "mobile": profile_result.get("mobile"),
                "profile_photo": profile_result.get("profilePhoto"),
            },
        )

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_ABHA_ADDRESS,
            meta_data={
                "abha_number": str(abha_number.external_id),
            },
            created_by=request.user,
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "health_id": result.get("healthIdNumber"),
                "preferred_abha_address": result.get("preferredAbhaAddress"),
                "abha_number": AbhaNumberSerializer(abha_number).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/send_otp")
    def abha_login__send_otp(self, request):
        validated_data = self.validate_request(request)

        otp_system = validated_data.get("otp_system")
        type = validated_data.get("type")

        scope = []

        if otp_system == "aadhaar":
            scope.append("aadhaar-verify")
        elif otp_system == "abdm":
            scope.append("mobile-verify")

        if type == "abha-address":
            scope.insert(0, "abha-address-login")
            result = HealthIdService.phr__web__login__abha__request__otp(
                {
                    "scope": scope,
                    "type": "abha-address",
                    "otp_system": otp_system,
                    "value": validated_data.get("value"),
                }
            )
        else:
            scope.insert(0, "abha-login")
            result = HealthIdService.profile__login__request__otp(
                {
                    "scope": scope,
                    "type": type,
                    "value": validated_data.get("value"),
                    "otp_system": otp_system,
                }
            )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/verify_otp")
    def abha_login__verify_otp(self, request):
        validated_data = self.validate_request(request)

        type = validated_data.get("type")
        otp_system = validated_data.get("otp_system")

        scope = []

        if otp_system == "aadhaar":
            scope.append("aadhaar-verify")
        elif otp_system == "abdm":
            scope.append("mobile-verify")

        token = None

        if type == "abha-address":
            scope.insert(0, "abha-address-login")
            result = HealthIdService.phr__web__login__abha__verify(
                {
                    "scope": scope,
                    "transaction_id": str(validated_data.get("transaction_id")),
                    "otp": validated_data.get("otp"),
                }
            )

            token = {
                "txn_id": result.get("txnId"),
                "access_token": result.get("token"),
                "refresh_token": result.get("refreshToken"),
            }
        else:
            scope.insert(0, "abha-login")
            result = HealthIdService.profile__login__verify(
                {
                    "scope": scope,
                    "transaction_id": str(validated_data.get("transaction_id")),
                    "otp": validated_data.get("otp"),
                }
            )

            if result.get("authResult") == "failed":
                return Response(
                    {
                        "transaction_id": result.get("txnId"),
                        "detail": result.get("message"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if type == "mobile":
                user_verification_result = HealthIdService.profile__login__verify__user(
                    {
                        "t_token": result.get("token"),
                        "abha_number": result.get("accounts")[0].get("ABHANumber"),
                        "transaction_id": result.get("txnId"),
                    }
                )

                token = {
                    "txn_id": result.get("txnId"),
                    "access_token": user_verification_result.get("token"),
                    "refresh_token": user_verification_result.get("refreshToken"),
                }
            else:
                token = {
                    "txn_id": result.get("txnId"),
                    "access_token": result.get("token"),
                    "refresh_token": result.get("refreshToken"),
                }

        if not token:
            return Response(
                {
                    "detail": "Unable to verify OTP, Please try again later",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_result = HealthIdService.profile__account(
            {"x_token": token.get("access_token")}
        )

        (abha_number, created) = AbhaNumber.objects.update_or_create(
            abha_number=profile_result.get("ABHANumber"),
            defaults={
                "abha_number": profile_result.get("ABHANumber"),
                "health_id": profile_result.get("preferredAbhaAddress"),
                "name": profile_result.get("name"),
                "first_name": profile_result.get("firstName"),
                "middle_name": profile_result.get("middleName"),
                "last_name": profile_result.get("lastName"),
                "gender": profile_result.get("gender"),
                "date_of_birth": str(
                    datetime.strptime(
                        f"{profile_result.get('yearOfBirth')}-{profile_result.get('monthOfBirth')}-{profile_result.get('dayOfBirth')}",
                        "%Y-%m-%d",
                    )
                )[0:10],
                "address": profile_result.get("address"),
                "district": profile_result.get("districtName"),
                "state": profile_result.get("stateName"),
                "pincode": profile_result.get("pincode"),
                "email": profile_result.get("email"),
                "mobile": profile_result.get("mobile"),
                "profile_photo": profile_result.get("profilePhoto"),
                "access_token": token.get("access_token"),
                "refresh_token": token.get("refresh_token"),
            },
        )

        Transaction.objects.create(
            reference_id=token.get("txn_id"),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "link_via_otp",
                "type": type,
                "system": otp_system,
            },
            created_by=request.user,
        )

        return Response(
            {"abha_number": AbhaNumberSerializer(abha_number).data, "created": created},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/check_auth_methods")
    def abha_login__check_auth_methods(self, request):
        validated_data = self.validate_request(request)

        abha_address = validated_data.get("abha_address")

        if not abha_address.endswith(f"@{settings.ABDM_CM_ID}"):
            abha_address = f"{abha_address}@{settings.ABDM_CM_ID}"

        result = HealthIdService.phr__web__login__abha__search(
            {
                "abha_address": abha_address,
            }
        )

        return Response(
            {
                "abha_number": result.get("healthIdNumber"),
                "auth_methods": result.get("authMethods"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="abha_card")
    def abha_card(self, request):
        abha_id = request.query_params.get("abha_id")

        if not abha_id:
            return Response(
                {
                    "detail": "ABHA ID is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        abha_number = AbhaNumber.objects.filter(
            Q(abha_number=abha_id) | Q(health_id=abha_id)
        ).first()

        if not abha_number:
            return Response(
                {
                    "detail": "ABHA Number not found for the given ABHA ID",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        abha_card = HealthIdService.profile__account__abha_card(
            {"x_token": abha_number.access_token}
        )

        return HttpResponse(
            abha_card,
            content_type="image/png",
            status=status.HTTP_200_OK,
        )
