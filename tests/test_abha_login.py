from unittest.mock import patch
from uuid import uuid4

from abdm.models import AbhaNumber
from abdm.service.v3.health_id import HealthIdService
from abdm.settings import plugin_settings as settings

from care.utils.tests.base import CareAPITestBase

SEND_OTP_URL = "/api/abdm/v3/health_id/login/send_otp/"
VERIFY_OTP_URL = "/api/abdm/v3/health_id/login/verify_otp/"
VERIFY_USER_URL = "/api/abdm/v3/health_id/login/verify_user/"


class AbhaLoginTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
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
        ):
            user_response = self.client.post(
                VERIFY_USER_URL,
                {"transaction_id": txn_id, "account_id": 0},
                format="json",
            )

        self.assertEqual(user_response.status_code, 200)
        fetch_profile.assert_called_once_with({"x_token": "phr-token"})
        account_profile.assert_not_called()

        saved = AbhaNumber.objects.get(
            health_id=f"patient.name@{settings.ABDM_CM_ID}"
        )
        self.assertEqual(saved.abha_number, "91123456789012")
        self.assertEqual(saved.name, "Patient Name")
        self.assertEqual(saved.state, "MAHARASHTRA")
        self.assertEqual(saved.pincode, "411001")
        self.assertEqual(saved.date_of_birth, "1990-04-02")
        self.assertIsNone(saved.district)
        self.assertEqual(saved.access_token, "phr-token")
        self.assertEqual(saved.refresh_token, "phr-refresh")
        self.assertTrue(user_response.data["created"])
