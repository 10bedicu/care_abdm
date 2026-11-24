from unittest.mock import patch
from uuid import uuid4

from abdm.models import AbhaAuth, AbhaAuthMethod, HealthFacility
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings
from model_bakery import baker

from care.utils.tests.base import CareAPITestBase

VERIFY_AADHAAR_OTP_URL = "/api/abdm/v3/health_id/create/verify_aadhaar_otp/"
ENROL_ABHA_ADDRESS_URL = "/api/abdm/v3/health_id/create/enrol_abha_address/"
SEND_OTP_URL = "/api/abdm/v3/health_id/login/send_otp/"
VERIFY_OTP_URL = "/api/abdm/v3/health_id/login/verify_otp/"
VERIFY_USER_URL = "/api/abdm/v3/health_id/login/verify_user/"


class AbhaLoginTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.health_facility = baker.make(
            HealthFacility, facility=self.facility, hf_id="LOGIN_HIP"
        )
        self.client.force_authenticate(user=self.user)

    def test_abha_number_otp_uses_profile_login(self):
        txn_id = str(uuid4())

        with patch.object(
            HealthIdService,
            "profile__login__request__otp",
            return_value={"txnId": txn_id, "message": "OTP sent"},
        ) as send_otp:
            response = self.client.post(
                SEND_OTP_URL,
                {
                    "type": "abha-number",
                    "value": "91123456789012",
                    "otp_system": "aadhaar",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        send_otp.assert_called_once_with(
            {
                "scope": ["abha-login", "aadhaar-verify"],
                "type": "abha-number",
                "value": "91-1234-5678-9012",
                "otp_system": "aadhaar",
            }
        )

    def test_abha_address_login_saves_profile_from_phr_token(self):
        txn_id = str(uuid4())

        with patch.object(
            HealthIdService,
            "phr__web__login__abha__request__otp",
            return_value={"txnId": txn_id, "message": "OTP sent"},
        ) as send_otp:
            send_response = self.client.post(
                SEND_OTP_URL,
                {
                    "type": "abha-address",
                    "value": "patient.name",
                    "otp_system": "abdm",
                },
                format="json",
            )

        self.assertEqual(send_response.status_code, 200)
        send_otp.assert_called_once_with(
            {
                "scope": ["abha-address-login", "mobile-verify"],
                "type": "abha-address",
                "otp_system": "abdm",
                "value": f"patient.name@{settings.ABDM_CM_ID}",
            }
        )

        with patch.object(
            HealthIdService,
            "phr__web__login__abha__verify",
            return_value={
                "authResult": "success",
                "message": "OTP verified successfully",
                "tokens": {
                    "token": "phr-token",
                    "refreshToken": "phr-refresh",
                    "expiresIn": 1800,
                    "refreshExpiresIn": 1296000,
                },
                "users": [
                    {
                        "abhaAddress": f"patient.name@{settings.ABDM_CM_ID}",
                        "abhaNumber": "91123456789012",
                        "fullName": "Patient Name",
                        "profilePhoto": "cGhvdG8=",
                        "status": "ACTIVE",
                    }
                ],
            },
        ):
            verify_response = self.client.post(
                VERIFY_OTP_URL,
                {
                    "type": "abha-address",
                    "otp": "123456",
                    "otp_system": "abdm",
                    "transaction_id": txn_id,
                },
                format="json",
            )

        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.data["accounts"][0]["name"], "Patient Name")

        with (
            patch.object(
                HealthIdService,
                "phr__web__login__profile__abha_profile",
                return_value={
                    "abhaAddress": f"patient.name@{settings.ABDM_CM_ID}",
                    "abhaNumber": "91123456789012",
                    "fullName": "Patient Name",
                    "firstName": "Patient",
                    "middleName": "",
                    "lastName": "Name",
                    "gender": "F",
                    "yearOfBirth": "1990",
                    "monthOfBirth": "4",
                    "dayOfBirth": "2",
                    "address": "Pune",
                    "stateName": "MAHARASHTRA",
                    "pinCode": "411001",
                    "email": "patient@example.com",
                    "mobile": "9999999999",
                    "profilePhoto": "cGhvdG8=",
                },
            ) as fetch_profile,
            patch.object(HealthIdService, "profile__account") as account_profile,
            self.captureOnCommitCallbacks(execute=True),
        ):
            user_response = self.client.post(
                VERIFY_USER_URL,
                {
                    "transaction_id": txn_id,
                    "account_id": 0,
                    "facility": str(self.facility.external_id),
                },
                format="json",
            )

        self.assertEqual(user_response.status_code, 200)
        fetch_profile.assert_called_once_with({"x_token": "phr-token"})
        account_profile.assert_not_called()

        auth = AbhaAuth.objects.get(abha_address=f"patient.name@{settings.ABDM_CM_ID}")
        self.assertEqual(auth.health_facility, self.health_facility)
        self.assertEqual(auth.method, AbhaAuthMethod.STAFF_VERIFICATION)
        self.assertEqual(auth.performed_by, self.user)
        self.assertEqual(
            user_response.data["abha_number"]["abha_address"], auth.abha_address
        )
        self.assertEqual(
            user_response.data["abha_number"]["auth"]["id"], str(auth.external_id)
        )
        self.assertNotIn("health_id", user_response.data["abha_number"])

        saved = auth.abha_number
        self.assertEqual(saved.abha_number, "91123456789012")
        self.assertEqual(saved.name, "Patient Name")
        self.assertEqual(saved.state, "MAHARASHTRA")
        self.assertEqual(saved.pincode, "411001")
        self.assertEqual(saved.date_of_birth, "1990-04-02")
        self.assertIsNone(saved.district)
        self.assertEqual(saved.access_token, "phr-token")
        self.assertEqual(saved.refresh_token, "phr-refresh")
        self.assertTrue(user_response.data["created"])

    def test_verified_abha_needs_a_registered_health_facility(self):
        unregistered = self.create_facility(self.user)

        for facility in ({}, {"facility": str(unregistered.external_id)}):
            with (
                self.subTest(facility=facility),
                patch.object(HealthIdService, "profile__account") as account_profile,
            ):
                response = self.client.post(
                    VERIFY_USER_URL,
                    {"transaction_id": str(uuid4()), "account_id": 0, **facility},
                    format="json",
                )

                self.assertEqual(response.status_code, 400)
                account_profile.assert_not_called()

    def test_new_abha_and_the_address_enrolled_for_it_are_auths_of_one_creation(self):
        txn_id = str(uuid4())
        facility = {"facility": str(self.facility.external_id)}
        default_address = f"91123456789012@{settings.ABDM_CM_ID}"
        chosen_address = f"patient.name@{settings.ABDM_CM_ID}"

        with (
            patch.object(
                HealthIdService,
                "enrollment__enrol__byAadhaar__via_otp",
                return_value={
                    "txnId": txn_id,
                    "isNew": True,
                    "tokens": {"token": "x-token", "refreshToken": "refresh"},
                    "ABHAProfile": {
                        "ABHANumber": "91-1234-5678-9012",
                        "phrAddress": [default_address],
                        "firstName": "Patient",
                        "middleName": "",
                        "lastName": "Name",
                        "gender": "F",
                        "dob": "02-04-1990",
                    },
                },
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            created = self.client.post(
                VERIFY_AADHAAR_OTP_URL,
                {
                    "transaction_id": txn_id,
                    "otp": "123456",
                    "mobile": "9999999999",
                    **facility,
                },
                format="json",
            )

        self.assertEqual(created.status_code, 200, created.data)
        self.assertTrue(created.data["created"])
        self.assertEqual(created.data["abha_number"]["abha_address"], default_address)

        with (
            patch.object(
                HealthIdService,
                "enrollment__enrol__abha_address",
                return_value={
                    "txnId": txn_id,
                    "healthIdNumber": "91-1234-5678-9012",
                    "preferredAbhaAddress": chosen_address,
                },
            ),
            patch.object(
                HealthIdService,
                "profile__account",
                return_value={
                    "ABHANumber": "91-1234-5678-9012",
                    "preferredAbhaAddress": chosen_address,
                    "name": "Patient Name",
                    "gender": "F",
                    "yearOfBirth": "1990",
                    "monthOfBirth": "4",
                    "dayOfBirth": "2",
                },
            ) as account_profile,
            self.captureOnCommitCallbacks(execute=True),
        ):
            enrolled = self.client.post(
                ENROL_ABHA_ADDRESS_URL,
                {"transaction_id": txn_id, "abha_address": "patient.name", **facility},
                format="json",
            )

        self.assertEqual(enrolled.status_code, 200, enrolled.data)
        account_profile.assert_called_once_with({"x_token": "x-token"})
        self.assertEqual(enrolled.data["abha_number"]["abha_address"], chosen_address)
        self.assertEqual(
            enrolled.data["abha_number"]["id"], created.data["abha_number"]["id"]
        )
        self.assertEqual(
            list(
                AbhaAuth.objects.order_by("created_date").values_list(
                    "abha_address", "method", "health_facility"
                )
            ),
            [
                (address, AbhaAuthMethod.ABHA_CREATION, self.health_facility.id)
                for address in (default_address, chosen_address)
            ],
        )
