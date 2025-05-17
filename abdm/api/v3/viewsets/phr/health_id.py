from datetime import datetime

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.serializers.abha_number import AbhaNumberSerializer
from abdm.api.v3.serializers.health_id import (
    PhrEnrollmentAbhaAddressExistsSerializer,
    PhrEnrollmentAbhaAddressSuggestionSerializer,
    PhrEnrollmentEnrolAbhaAddressSerializer,
    PhrEnrollmentSendOtpSerializer,
    PhrEnrollmentVerifyOtpSerializer,
)
from abdm.models import AbhaNumber, Transaction, TransactionType
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings


class PhrEnrollmentViewSet(GenericViewSet):
    permission_classes = []

    serializer_action_classes = {
        "phr_enrollment__send_otp": PhrEnrollmentSendOtpSerializer,
        "phr_enrollment__verify_otp": PhrEnrollmentVerifyOtpSerializer,
        "phr_enrollment__abha_address_suggestion": PhrEnrollmentAbhaAddressSuggestionSerializer,
        "phr_enrollment__abha_address_exists": PhrEnrollmentAbhaAddressExistsSerializer,
        "phr_enrollment__enrol_abha_address": PhrEnrollmentEnrolAbhaAddressSerializer,
    }

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]

        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return serializer.validated_data

    @action(detail=False, methods=["post"], url_path="phr/create/send_otp")
    def phr_enrollment__send_otp(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")

        if login_hint == "abha-number":
            scope = ["abha-login"]
            scope.append(
                "aadhaar-verify" if otp_system == "aadhaar" else "mobile-verify"
            )
        else:
            scope = ["abha-address-enroll", "mobile-verify"]

        result = HealthIdService.phr__enrollment__request__otp(
            {
                "scope": scope,
                "type": validated_data.get("type"),
                "value": validated_data.get("value"),
                "otp_system": validated_data.get("otp_system"),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="phr/create/verify_otp")
    def phr_enrollment__verify_otp(self, request):
        validated_data = self.validate_request(request)

        login_hint = validated_data.get("type")
        otp_system = validated_data.get("otp_system")

        if login_hint == "abha-number":
            scope = ["abha-login"]
            scope.append(
                "aadhaar-verify" if otp_system == "aadhaar" else "mobile-verify"
            )
        else:
            scope = ["abha-address-enroll", "mobile-verify"]

        result = HealthIdService.phr__enrollment__verify__otp(
            {
                "scope": scope,
                "transaction_id": str(validated_data.get("transaction_id")),
                "otp": validated_data.get("otp"),
            }
        )

        accounts = result.get("accounts", [])
        if result.get("authResult") == "success" and accounts:
            account = accounts[0]
            token = result.get("tokens", {})

            (abha_number, created) = AbhaNumber.objects.update_or_create(
                abha_number=account.get("ABHANumber"),
                defaults={
                    "abha_number": account.get("ABHANumber"),
                    "health_id": account.get("preferredAbhaAddress"),
                    "name": account.get("name"),
                    "first_name": account.get("firstName", ""),
                    "middle_name": account.get("middleName", ""),
                    "last_name": account.get("lastName", ""),
                    "gender": account.get("gender"),
                    "date_of_birth": str(
                        datetime.strptime(
                            f"{account.get('yearOfBirth')}-{account.get('monthOfBirth')}-{account.get('dayOfBirth')}",
                            "%Y-%m-%d",
                        )
                    )[0:10],
                    "address": account.get("address"),
                    "district": account.get("districtName"),
                    "state": account.get("stateName"),
                    "pincode": account.get("pincode"),
                    "mobile": account.get("mobile"),
                    "profile_photo": account.get("profilePhoto"),
                    "access_token": token.get("token"),
                    "refresh_token": token.get("refreshToken"),
                },
            )

            Transaction.objects.create(
                reference_id=str(validated_data.get("transaction_id")),
                type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
                meta_data={
                    "abha_number": str(abha_number.external_id),
                    "method": "link_via_otp",
                    "type": "abha-number" if login_hint == "abha-number" else "mobile",
                    "system": otp_system,
                },
            )

            return Response(
                {
                    "transaction_id": result.get("txnId"),
                    "detail": result.get("message"),
                    "abha_number": AbhaNumberSerializer(abha_number).data,
                    "users": result.get("users"),
                    "created": created,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "detail": result.get("message"),
                "users": result.get("users"),
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False, methods=["post"], url_path="phr/create/abha_address_suggestion"
    )
    def phr_enrollment__abha_address_suggestion(self, request):
        validated_data = self.validate_request(request)

        result = HealthIdService.phr__enrollment__abha_address__suggestion(
            {
                "transaction_id": str(validated_data.get("transaction_id")),
                "first_name": validated_data.get("first_name"),
                "last_name": validated_data.get("last_name", ""),
                "year_of_birth": validated_data.get("year_of_birth"),
                "month_of_birth": validated_data.get("month_of_birth", ""),
                "day_of_birth": validated_data.get("day_of_birth", ""),
            }
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "abha_addresses": result.get("abhaAddressList", []),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="phr/create/abha_address_exists")
    def phr_enrollment__abha_address_exists(self, request):
        validated_data = self.validate_request(request)

        abha_address = validated_data.get("abha_address")

        if not abha_address.endswith(f"@{settings.ABDM_CM_ID}"):
            abha_address = f"{abha_address}@{settings.ABDM_CM_ID}"

        exists = HealthIdService.phr__enrollment__abha_address__exists(
            {
                "abha_address": abha_address,
            }
        )

        return Response(
            exists,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="phr/create/enrol_abha_address")
    def phr_enrollment__enrol_abha_address(self, request):
        validated_data = self.validate_request(request)

        phr_details = validated_data.get("phr_details")
        phr_details_camel = {
            "abhaAddress": phr_details.get("abha_address"),
            "address": phr_details.get("address"),
            "dayOfBirth": phr_details.get("day_of_birth", ""),
            "districtCode": phr_details.get("district_code"),
            "districtName": phr_details.get("district_name"),
            "email": phr_details.get("email", ""),
            "profilePhoto": phr_details.get("profile_photo", ""),
            "firstName": phr_details.get("first_name"),
            "gender": phr_details.get("gender"),
            "lastName": phr_details.get("last_name", ""),
            "middleName": phr_details.get("middle_name", ""),
            "mobile": phr_details.get("mobile"),
            "monthOfBirth": phr_details.get("month_of_birth", ""),
            "password": phr_details.get("password"),
            "pinCode": phr_details.get("pin_code"),
            "stateCode": phr_details.get("state_code"),
            "stateName": phr_details.get("state_name"),
            "yearOfBirth": phr_details.get("year_of_birth"),
        }

        result = HealthIdService.phr__enrollment__enrol__abha_address(
            {
                "phr_details": phr_details_camel,
                "transaction_id": str(validated_data.get("transaction_id")),
            }
        )

        abha_addresses = result.get("phrDetails", {}).get("abhaAddress", [])

        abha_number = AbhaNumber.objects.filter(health_id__in=abha_addresses).first()

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
            },
        )

        Transaction.objects.create(
            reference_id=str(validated_data.get("transaction_id")),
            type=TransactionType.CREATE_ABHA_ADDRESS,
            meta_data={
                "abha_number": str(abha_number.external_id),
            },
        )

        return Response(
            {
                "transaction_id": result.get("txnId"),
                "health_id": profile_result.get("ABHANumber"),
                "preferred_abha_address": profile_result.get("preferredAbhaAddress"),
                "abha_number": AbhaNumberSerializer(abha_number).data,
                "created": created,
            },
            status=status.HTTP_200_OK,
        )
