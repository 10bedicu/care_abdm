from datetime import datetime

from django.core.cache import cache
from django.db import transaction
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
    AbhaCreateAuthInitViaFaceSerializer,
    AbhaCreateCapturePIDViaFaceSerializer,
    AbhaCreateEnrolAbhaAddressSerializer,
    AbhaCreateLinkMobileNumberSerializer,
    AbhaCreateSendAadhaarOtpSerializer,
    AbhaCreateVerifyAadhaarBioSerializer,
    AbhaCreateVerifyAadhaarDemographicsSerializer,
    AbhaCreateVerifyAadhaarFaceSerializer,
    AbhaCreateVerifyAadhaarOtpSerializer,
    AbhaCreateVerifyMobileOtpSerializer,
    AbhaLoginCheckAuthMethodsSerializer,
    AbhaLoginSendOtpSerializer,
    AbhaLoginVerifyOtpSerializer,
    AbhaLoginVerifyUserSerializer,
    LinkAbhaNumberAndPatientSerializer,
)
from abdm.models import AbhaAuthMethod, AbhaNumber, Transaction, TransactionType
from abdm.service.abha import (
    find_abha_number,
    health_facility_for_facility,
    latest_valid_auth,
    record_abha_auth,
)
from abdm.service.helper import validate_and_format_date
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings
from abdm.tasks.link_care_context import enqueue_link_patient_care_contexts
from abdm.utils.patient_identifier import ensure_abdm_patient_identifier
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.patient import Patient
from care.security.authorization.base import AuthorizationController

ABHA_LOGIN_CACHE_KEY = "abdm_abha_login__{transaction_id}"
ABHA_LOGIN_CACHE_TTL = 60 * 5  # 5 minutes


def mask_abha_number(value):
    if not value:
        return value

    visible = value[-4:]
    masked = "".join("X" if char.isdigit() else char for char in value[:-4])
    return masked + visible


def with_cm_id(abha_address):
    abha_address = (abha_address or "").strip()
    suffix = f"@{settings.ABDM_CM_ID}"
    if abha_address.endswith(suffix):
        return abha_address
    return f"{abha_address}{suffix}"


def abha_login_identifier(login_type, value):
    value = (value or "").strip()
    if login_type == "abha-number":
        # ABDM validates the decrypted loginId as NN-NNNN-NNNN-NNNN. Bare digits
        # are rejected with {"loginId": "LoginId is invalid"}.
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) == 14:
            return f"{digits[:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:]}"
        return value
    if login_type == "abha-address":
        return with_cm_id(value)
    return value


def auth_method_for_enrolment(is_new) -> AbhaAuthMethod:
    return (
        AbhaAuthMethod.ABHA_CREATION if is_new else AbhaAuthMethod.STAFF_VERIFICATION
    )


def profile_fields_from_enrolment(profile, is_new):
    date_of_birth = datetime.strptime(profile.get("dob"), "%d-%m-%Y")  # noqa: DTZ007

    return {
        "abha_number": profile.get("ABHANumber"),
        "abha_address": (profile.get("phrAddress") or [None])[0],
        "name": " ".join(
            filter(
                lambda x: (x or "").strip(),
                [
                    profile.get("firstName"),
                    profile.get("middleName"),
                    profile.get("lastName"),
                ],
            )
        ),
        "first_name": profile.get("firstName"),
        "middle_name": profile.get("middleName"),
        "last_name": profile.get("lastName"),
        "gender": profile.get("gender"),
        "date_of_birth": validate_and_format_date(
            date_of_birth.year, date_of_birth.month, date_of_birth.day
        ),
        "address": profile.get("address"),
        "district": profile.get("districtName"),
        "state": profile.get("stateName"),
        "pincode": profile.get("pinCode"),
        "email": profile.get("email"),
        "mobile": profile.get("mobile"),
        "profile_photo": profile.get("photo"),
        "new": is_new,
    }


def profile_fields_from_account(profile):
    return {
        "abha_number": profile.get("ABHANumber"),
        "abha_address": profile.get("preferredAbhaAddress"),
        "name": profile.get("name"),
        "first_name": profile.get("firstName"),
        "middle_name": profile.get("middleName"),
        "last_name": profile.get("lastName"),
        "gender": profile.get("gender"),
        "date_of_birth": validate_and_format_date(
            profile.get("yearOfBirth"),
            profile.get("monthOfBirth"),
            profile.get("dayOfBirth"),
        ),
        "address": profile.get("address"),
        "district": profile.get("districtName"),
        "state": profile.get("stateName"),
        "pincode": profile.get("pincode"),
        "email": profile.get("email") or None,
        "mobile": profile.get("mobile"),
        "profile_photo": profile.get("profilePhoto"),
    }


def profile_fields_from_abha_address(profile):
    return {
        "abha_number": profile.get("abhaNumber"),
        "abha_address": profile.get("abhaAddress"),
        "name": profile.get("fullName"),
        "first_name": profile.get("firstName"),
        "middle_name": profile.get("middleName"),
        "last_name": profile.get("lastName"),
        "gender": profile.get("gender"),
        "date_of_birth": validate_and_format_date(
            profile.get("yearOfBirth"),
            profile.get("monthOfBirth"),
            profile.get("dayOfBirth"),
        ),
        "address": profile.get("address"),
        "district": None,
        "state": profile.get("stateName"),
        "pincode": profile.get("pinCode"),
        "email": profile.get("email") or None,
        "mobile": profile.get("mobile"),
        "profile_photo": profile.get("profilePhoto"),
    }


@extend_schema(tags=["ABDM: Health ID"])
class HealthIdViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)

    serializer_action_classes = {
        "abha_create__verify_aadhaar_bio": AbhaCreateVerifyAadhaarBioSerializer,
        "abha_create__auth_init_via_face": AbhaCreateAuthInitViaFaceSerializer,
        "abha_create__capture_pid_via_face": AbhaCreateCapturePIDViaFaceSerializer,
        "abha_create__verify_aadhaar_face": AbhaCreateVerifyAadhaarFaceSerializer,
        "abha_create__verify_aadhaar_demographics": AbhaCreateVerifyAadhaarDemographicsSerializer,
        "abha_create__send_aadhaar_otp": AbhaCreateSendAadhaarOtpSerializer,
        "abha_create__verify_aadhaar_otp": AbhaCreateVerifyAadhaarOtpSerializer,
        "abha_create__link_mobile_number": AbhaCreateLinkMobileNumberSerializer,
        "abha_create__verify_mobile_otp": AbhaCreateVerifyMobileOtpSerializer,
        "abha_create__abha_address_suggestion": AbhaCreateAbhaAddressSuggestionSerializer,
        "abha_create__enrol_abha_address": AbhaCreateEnrolAbhaAddressSerializer,
        "abha_login__send_otp": AbhaLoginSendOtpSerializer,
        "abha_login__verify_otp": AbhaLoginVerifyOtpSerializer,
        "abha_login__verify_user": AbhaLoginVerifyUserSerializer,
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

    def abha_number_data(self, abha_number, health_facility):
        return AbhaNumberSerializer(
            abha_number,
            context={**self.get_serializer_context(), "health_facility": health_facility},
        ).data

    def record_enrolment(self, request, transaction_id, result, health_facility, method):
        """Record the ABHA an Aadhaar enrolment returned, and the auth it stands for."""
        profile = profile_fields_from_enrolment(
            result.get("ABHAProfile"), result.get("isNew")
        )
        token = result.get("tokens")

        abha_number, _, created = record_abha_auth(
            abha_number=profile.get("abha_number"),
            abha_address=profile.get("abha_address"),
            profile=profile,
            health_facility=health_facility,
            method=auth_method_for_enrolment(result.get("isNew")),
            performed_by=request.user,
            tokens={
                "access_token": token.get("token"),
                "refresh_token": token.get("refreshToken"),
            },
        )

        Transaction.objects.create(
            reference_id=str(transaction_id),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": method,
            },
            created_by=request.user,
        )

        return abha_number, created

    @action(detail=False, methods=["post"], url_path="link_patient")
    def link_abha_number_and_patient(self, request):
        validated_data = self.validate_request(request)

        patient = Patient.objects.filter(
            external_id=validated_data.get("patient")
        ).first()

        if not patient or not AuthorizationController.call(
            "can_create_patient", self.request.user
        ):
            return Response(
                {
                    "detail": "Patient not found or you do not have permission to access the patient",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        health_facility = health_facility_for_facility(validated_data.get("facility"))

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

        # an auth made before the ABHA had an address still proves who it belongs to
        if not latest_valid_auth(abha_number, health_facility, require_address=False):
            return Response(
                {
                    "detail": "This ABHA has not been verified at this facility, Please verify it again",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        abdm_user = get_or_create_abdm_user()

        with transaction.atomic():
            abha_number = AbhaNumber.objects.select_for_update().get(pk=abha_number.pk)

            # the one to one constraint counts soft-deleted ABHA records too
            if (
                AbhaNumber._base_manager.filter(patient=patient)  # noqa: SLF001
                .exclude(pk=abha_number.pk)
                .exists()
            ):
                return Response(
                    {
                        "detail": "This patient is linked to one ABHA number, and the one just verified is different",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if abha_number.patient_id and abha_number.patient_id != patient.id:
                return Response(
                    {
                        "detail": "ABHA Number already linked to a patient",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # a patient who already holds this ABHA record is verifying it again
            if not abha_number.patient_id:
                abha_number.patient = patient
                abha_number.save(update_fields=["patient"])

                if abha_number.abha_number:
                    ensure_abdm_patient_identifier(
                        patient,
                        system=settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_SYSTEM,
                        display=settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_DISPLAY,
                        value=abha_number.abha_number,
                        created_by=abdm_user,
                    )

                patient.build_instance_identifiers()
                patient.save()

        transaction.on_commit(
            lambda: enqueue_link_patient_care_contexts(
                patient, health_facility, user=request.user
            )
        )

        return Response(
            self.abha_number_data(abha_number, health_facility),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/verify_aadhaar_bio")
    def abha_create__verify_aadhaar_bio(self, request):
        validated_data = self.validate_request(request)
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        result = HealthIdService.enrollment__enrol__byAadhaar__via_bio(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "aadhaar": validated_data.get("aadhaar"),
                "mobile": validated_data.get("mobile"),
                "fingerprint_pid": validated_data.get("fingerprint_pid"),
            }
        )

        abha_number, created = self.record_enrolment(
            request,
            validated_data.get("transaction_id"),
            result,
            health_facility,
            "create_via_aadhaar_bio",
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_number": self.abha_number_data(abha_number, health_facility),
                "created": created,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/auth_init_via_face")
    def abha_create__auth_init_via_face(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__enrol__auth_init__via_face({})

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/capture_pid_via_face")
    def abha_create__capture_pid_via_face(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.enrollment__enrol__capturePID__via_face(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        return Response(
            {
                "status": result.get("status"),
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create/verify_aadhaar_face")
    def abha_create__verify_aadhaar_face(self, request):
        validated_data = self.validate_request(request)
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        result = HealthIdService.enrollment__enrol__byAadhaar__via_face(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "aadhaar": validated_data.get("aadhaar"),
                "mobile": validated_data.get("mobile"),
            }
        )

        abha_number, created = self.record_enrolment(
            request,
            validated_data.get("transaction_id"),
            result,
            health_facility,
            "create_via_aadhaar_face",
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_number": self.abha_number_data(abha_number, health_facility),
                "created": created,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False, methods=["post"], url_path="create/verify_aadhaar_demographics"
    )
    def abha_create__verify_aadhaar_demographics(self, request):
        validated_data = self.validate_request(request)
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        result = HealthIdService.enrollment__enrol__byAadhaar__via_demographics(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "aadhaar_number": validated_data.get("aadhaar"),
                "name": validated_data.get("name"),
                "gender": validated_data.get("gender"),
                "date_of_birth": validated_data.get("date_of_birth").strftime(
                    "%d-%m-%Y"
                ),
                "state_code": validated_data.get("state_code"),
                "district_code": validated_data.get("district_code"),
                "address": validated_data.get("address"),
                "pin_code": validated_data.get("pin_code"),
                "mobile": validated_data.get("mobile"),
                "profile_photo": validated_data.get("profile_photo"),
            }
        )

        abha_profile = result
        token = result.get("jwtResponse")
        abha_number, _, created = record_abha_auth(
            abha_number=abha_profile.get("healthIdNumber"),
            abha_address=abha_profile.get("healthId"),
            profile={
                "name": abha_profile.get("name"),
                "first_name": abha_profile.get("firstName"),
                "middle_name": abha_profile.get("middleName"),
                "last_name": abha_profile.get("lastName"),
                "gender": abha_profile.get("gender"),
                "date_of_birth": validate_and_format_date(
                    abha_profile.get("yearOfBirth"),
                    abha_profile.get("monthOfBirth"),
                    abha_profile.get("dayOfBirth"),
                ),
                "address": abha_profile.get("address"),
                "district": abha_profile.get("districtName"),
                "state": abha_profile.get("stateName"),
                "pincode": abha_profile.get("pincode"),
                "email": abha_profile.get("email"),
                "mobile": abha_profile.get("mobile"),
                "profile_photo": abha_profile.get("profilePhoto"),
                "new": result.get("new"),
            },
            health_facility=health_facility,
            method=auth_method_for_enrolment(result.get("new")),
            performed_by=request.user,
            tokens={
                "access_token": token.get("token"),
                "refresh_token": token.get("refreshToken"),
            },
        )

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "create_via_aadhaar_demographics",
            },
            created_by=request.user,
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_number": self.abha_number_data(abha_number, health_facility),
                "created": created,
            },
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
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        result = HealthIdService.enrollment__enrol__byAadhaar__via_otp(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "otp": validated_data.get("otp"),
                "mobile": validated_data.get("mobile"),
            }
        )

        abha_number, created = self.record_enrolment(
            request,
            validated_data.get("transaction_id"),
            result,
            health_facility,
            "create_via_aadhaar_otp",
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
                "abha_number": self.abha_number_data(abha_number, health_facility),
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
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        result = HealthIdService.enrollment__enrol__abha_address(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "abha_address": validated_data.get("abha_address"),
                "preferred": 1,
            }
        )

        abha_number = find_abha_number(result.get("healthIdNumber"))

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
        profile = profile_fields_from_account(profile_result)

        # the address is enrolled as part of the verification that came before
        # it, so the auth for it is of the same kind
        previous_auth = latest_valid_auth(
            abha_number, health_facility, require_address=False
        )
        abha_number, _, _ = record_abha_auth(
            abha_number=profile.get("abha_number") or abha_number.abha_number,
            abha_address=profile.get("abha_address")
            or result.get("preferredAbhaAddress"),
            profile=profile,
            health_facility=health_facility,
            method=previous_auth.method
            if previous_auth and previous_auth.method != AbhaAuthMethod.LEGACY
            else auth_method_for_enrolment(abha_number.new),
            performed_by=request.user,
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
                "abha_number": self.abha_number_data(abha_number, health_facility),
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

        value = abha_login_identifier(type, validated_data.get("value"))

        if type == "abha-address":
            scope.insert(0, "abha-address-login")
            result = HealthIdService.phr__web__login__abha__request__otp(
                {
                    "scope": scope,
                    "type": "abha-address",
                    "otp_system": otp_system,
                    "value": value,
                }
            )
        else:
            scope.insert(0, "abha-login")
            result = HealthIdService.profile__login__request__otp(
                {
                    "scope": scope,
                    "type": type,
                    "value": value,
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
        transaction_id = str(validated_data.get("transaction_id"))

        scope = []

        if otp_system == "aadhaar":
            scope.append("aadhaar-verify")
        elif otp_system == "abdm":
            scope.append("mobile-verify")

        login_state = {
            "type": type,
            "otp_system": otp_system,
            "transaction_id": transaction_id,
        }

        if type == "abha-address":
            scope.insert(0, "abha-address-login")
            result = HealthIdService.phr__web__login__abha__verify(
                {
                    "scope": scope,
                    "transaction_id": transaction_id,
                    "otp": validated_data.get("otp"),
                }
            )

            if result.get("authResult") in ("failed", "failure"):
                return Response(
                    {
                        "transaction_id": result.get("txnId") or transaction_id,
                        "detail": result.get("message"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            tokens = result.get("tokens") or {}
            login_state.update(
                {
                    "txn_id": result.get("txnId") or transaction_id,
                    "access_token": tokens.get("token"),
                    "refresh_token": tokens.get("refreshToken"),
                    "requires_user_verification": False,
                    "accounts": [
                        {
                            "abha_number": user.get("abhaNumber"),
                            "preferred_abha_address": user.get("abhaAddress"),
                            "name": user.get("fullName"),
                            "profile_photo": user.get("profilePhoto"),
                        }
                        for user in (result.get("users") or [])
                    ],
                }
            )
        else:
            scope.insert(0, "abha-login")
            result = HealthIdService.profile__login__verify(
                {
                    "scope": scope,
                    "transaction_id": transaction_id,
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

            accounts = [
                {
                    "abha_number": account.get("ABHANumber"),
                    "preferred_abha_address": account.get("preferredAbhaAddress"),
                    "name": account.get("name"),
                    "gender": account.get("gender"),
                    "date_of_birth": account.get("dob"),
                    "profile_photo": account.get("profilePhoto"),
                }
                for account in (result.get("accounts") or [])
            ]

            if type == "mobile":
                login_state.update(
                    {
                        "txn_id": result.get("txnId"),
                        "t_token": result.get("token"),
                        "requires_user_verification": True,
                        "accounts": accounts,
                    }
                )
            else:
                login_state.update(
                    {
                        "txn_id": result.get("txnId"),
                        "access_token": result.get("token"),
                        "refresh_token": result.get("refreshToken"),
                        "requires_user_verification": False,
                        "accounts": accounts,
                    }
                )

        # Some flows do not return a list of accounts to choose from, in which
        # case we proceed with a single implicit account.
        if not login_state.get("accounts"):
            login_state["accounts"] = [{}]

        cache.set(
            ABHA_LOGIN_CACHE_KEY.format(transaction_id=transaction_id),
            login_state,
            ABHA_LOGIN_CACHE_TTL,
        )

        return Response(
            {
                "transaction_id": transaction_id,
                "accounts": [
                    {
                        "id": index,
                        "abha_number": mask_abha_number(account.get("abha_number")),
                        "preferred_abha_address": account.get("preferred_abha_address"),
                        "name": account.get("name"),
                        "gender": account.get("gender"),
                        "date_of_birth": account.get("date_of_birth"),
                        "profile_photo": account.get("profile_photo"),
                    }
                    for index, account in enumerate(login_state["accounts"])
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/verify_user")
    def abha_login__verify_user(self, request):
        validated_data = self.validate_request(request)
        health_facility = health_facility_for_facility(validated_data.get("facility"))

        transaction_id = str(validated_data.get("transaction_id"))
        account_id = validated_data.get("account_id")

        cache_key = ABHA_LOGIN_CACHE_KEY.format(transaction_id=transaction_id)
        login_state = cache.get(cache_key)

        if not login_state:
            return Response(
                {
                    "detail": "Login session has expired, Please verify the OTP again",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        accounts = login_state.get("accounts") or []

        if account_id >= len(accounts):
            return Response(
                {
                    "detail": "Invalid account selected, Please try again",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_account = accounts[account_id]

        access_token = login_state.get("access_token")
        refresh_token = login_state.get("refresh_token")

        if login_state.get("requires_user_verification"):
            user_verification_result = HealthIdService.profile__login__verify__user(
                {
                    "t_token": login_state.get("t_token"),
                    "abha_number": selected_account.get("abha_number"),
                    "transaction_id": login_state.get("txn_id"),
                }
            )

            access_token = user_verification_result.get("token")
            refresh_token = user_verification_result.get("refreshToken")

        if not access_token:
            return Response(
                {
                    "detail": "Unable to verify OTP, Please try again later",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if login_state.get("type") == "abha-address":
            profile = profile_fields_from_abha_address(
                HealthIdService.phr__web__login__profile__abha_profile(
                    {"x_token": access_token}
                )
            )
        else:
            profile = profile_fields_from_account(
                HealthIdService.profile__account({"x_token": access_token})
            )

        abha_number, _, created = record_abha_auth(
            abha_number=profile.get("abha_number"),
            abha_address=profile.get("abha_address"),
            profile=profile,
            health_facility=health_facility,
            method=AbhaAuthMethod.STAFF_VERIFICATION,
            performed_by=request.user,
            tokens={"access_token": access_token, "refresh_token": refresh_token},
        )

        Transaction.objects.create(
            reference_id=login_state.get("txn_id"),
            type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "method": "link_via_otp",
                "type": login_state.get("type"),
                "system": login_state.get("otp_system"),
            },
            created_by=request.user,
        )

        cache.delete(cache_key)

        return Response(
            {
                "abha_number": self.abha_number_data(abha_number, health_facility),
                "created": created,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="login/check_auth_methods")
    def abha_login__check_auth_methods(self, request):
        validated_data = self.validate_request(request)

        abha_address = with_cm_id(validated_data.get("abha_address"))

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

        abha_number = find_abha_number(abha_id)

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
