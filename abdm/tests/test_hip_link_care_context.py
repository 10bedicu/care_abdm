import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from abdm.api.v3.viewsets.hip import HIPCallbackViewSet
from abdm.models.base import HealthInformationType
from abdm.service.helper import ABDMAPIException
from abdm.settings import plugin_settings
from abdm.utils.link_otp import (
    LinkOtpDeliveryError,
    _failure_count_key,
    create_link_session,
    get_active_session,
    get_link_session,
    hash_otp,
    increment_failure_count,
    set_active_session,
    set_lockout,
)
from care.utils.tests.base import CareAPITestBase


@override_settings(
    USE_SMS=True,
    SMS_BACKEND="care.utils.sms.backend.console.ConsoleBackend",
)
class HipLinkCareContextOtpTestCase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.create_user(username="abdm_user_internal")
        self.patient = self.create_patient(
            phone_number="+919876543210",
            blood_group="O+",
        )
        self.reference_id = str(uuid.uuid4())
        self.transaction_id = str(uuid.uuid4())
        self.factory = APIRequestFactory()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _init_payload(self):
        return {
            "transactionId": self.transaction_id,
            "abhaAddress": "user@abdm",
            "patient": [
                {
                    "referenceNumber": str(self.patient.external_id),
                    "careContexts": [{"referenceNumber": "ctx-1"}],
                    "hiType": HealthInformationType.OP_CONSULTATION,
                    "count": 1,
                }
            ],
        }

    def _confirm_payload(self, token: str, link_ref_number: str | None = None):
        return {
            "confirmation": {
                "linkRefNumber": link_ref_number or self.reference_id,
                "token": token,
            }
        }

    def _post_init(self):
        request = self.factory.post(
            "/api/abdm/api/v3/hip/link/care-context/init",
            self._init_payload(),
            format="json",
        )
        request.META["HTTP_REQUEST_ID"] = str(uuid.uuid4())
        force_authenticate(request, user=self.user)
        view = HIPCallbackViewSet.as_view({"post": "hip__link__care_context__init"})
        return view(request)

    def _post_confirm(self, token: str, link_ref_number: str | None = None):
        request = self.factory.post(
            "/api/abdm/api/v3/hip/link/care-context/confirm",
            self._confirm_payload(token, link_ref_number),
            format="json",
        )
        request.META["HTTP_REQUEST_ID"] = str(uuid.uuid4())
        request.META["HTTP_X_HIP_ID"] = "hip-1"
        force_authenticate(request, user=self.user)
        view = HIPCallbackViewSet.as_view({"post": "hip__link__care_context__confirm"})
        return view(request)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_caches_hashed_otp_and_calls_on_init(self, mock_on_init):
        with patch("abdm.utils.link_otp.uuid4", return_value=self.reference_id):
            response = self._post_init()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_on_init.assert_called_once()

        cached = get_link_session(self.reference_id)
        self.assertIsNotNone(cached)
        self.assertIn("otp_hash", cached)
        self.assertNotIn("otp", cached)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_confirm"
    )
    def test_confirm_rejects_wrong_otp(self, mock_on_confirm):
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id=str(self.patient.external_id),
            care_contexts=["ctx-1"],
        )

        response = self._post_confirm("999999")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_on_confirm.assert_not_called()
        cached = get_link_session(self.reference_id)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["failed_attempts"], 1)

    def test_confirm_rejects_blank_token(self):
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id=str(self.patient.external_id),
            care_contexts=["ctx-1"],
        )

        response = self._post_confirm("")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_confirm"
    )
    def test_confirm_accepts_correct_otp_and_deletes_cache(self, mock_on_confirm):
        otp = "123456"
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp(otp),
            abha_address="user@abdm",
            patient_id=str(self.patient.external_id),
            care_contexts=["ctx-1"],
        )

        response = self._post_confirm(otp)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_on_confirm.assert_called_once()
        self.assertIsNone(get_link_session(self.reference_id))

    @override_settings(USE_SMS=False)
    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_fails_closed_when_sms_disabled(self, mock_on_init):
        response = self._post_init()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        mock_on_init.assert_not_called()
        self.assertIsNone(get_link_session(self.reference_id))

    def test_init_rejects_patient_without_phone(self):
        patient = self.create_patient(phone_number="", blood_group="O+")
        payload = self._init_payload()
        payload["patient"][0]["referenceNumber"] = str(patient.external_id)

        request = self.factory.post(
            "/api/abdm/api/v3/hip/link/care-context/init",
            payload,
            format="json",
        )
        request.META["HTTP_REQUEST_ID"] = str(uuid.uuid4())
        force_authenticate(request, user=self.user)
        view = HIPCallbackViewSet.as_view({"post": "hip__link__care_context__init"})
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_VERIFY_ATTEMPTS", 3)
    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_FAILURES", 100)
    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_confirm"
    )
    def test_confirm_deletes_session_after_max_wrong_attempts(self, mock_on_confirm):
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id=str(self.patient.external_id),
            care_contexts=["ctx-1"],
        )

        for _ in range(2):
            response = self._post_confirm("999999")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self._post_confirm("999999")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Too many wrong attempts", response.data["detail"])
        mock_on_confirm.assert_not_called()
        self.assertIsNone(get_link_session(self.reference_id))

    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_SENDS_PER_WINDOW", 1)
    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_rate_limits_repeated_sends(self, mock_on_init):
        with patch("abdm.utils.link_otp.uuid4", side_effect=["ref-1", "ref-2"]):
            first = self._post_init()
            second = self._post_init()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(mock_on_init.call_count, 1)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_supersedes_previous_session(self, mock_on_init):
        old_reference = "old-reference"
        new_reference = "new-reference"

        with patch("abdm.utils.link_otp.uuid4", side_effect=[old_reference, new_reference]):
            self._post_init()
            response = self._post_init()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(get_link_session(old_reference))
        self.assertIsNotNone(get_link_session(new_reference))

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_returns_429_when_patient_is_locked_out(self, mock_on_init):
        set_lockout(str(self.patient.external_id))

        response = self._post_init()

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        mock_on_init.assert_not_called()

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    @patch(
        "abdm.utils.link_otp.send_link_otp",
        side_effect=[
            LinkOtpDeliveryError("Failed to send OTP SMS"),
            None,
        ],
    )
    def test_init_rolls_back_send_counter_on_sms_failure(self, mock_send, mock_on_init):
        with patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_SENDS_PER_WINDOW", 1):
            first = self._post_init()
            second = self._post_init()

        self.assertEqual(first.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        mock_on_init.assert_called_once()

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_confirm"
    )
    def test_confirm_clears_failure_counter_on_success(self, mock_on_confirm):
        patient_id = str(self.patient.external_id)
        increment_failure_count(patient_id)
        otp = "123456"
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp(otp),
            abha_address="user@abdm",
            patient_id=patient_id,
            care_contexts=["ctx-1"],
        )

        response = self._post_confirm(otp)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIsNone(cache.get(_failure_count_key(patient_id)))

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_confirm"
    )
    def test_confirm_keeps_session_when_on_confirm_fails(self, mock_on_confirm):
        otp = "123456"
        patient_id = str(self.patient.external_id)
        create_link_session(
            self.reference_id,
            otp_hash=hash_otp(otp),
            abha_address="user@abdm",
            patient_id=patient_id,
            care_contexts=["ctx-1"],
        )
        set_active_session(patient_id, self.reference_id)
        mock_on_confirm.side_effect = ABDMAPIException()

        response = self._post_confirm(otp)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIsNotNone(get_link_session(self.reference_id))
        self.assertEqual(get_active_session(patient_id), self.reference_id)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    @patch("abdm.utils.link_otp.send_link_otp")
    @patch(
        "abdm.utils.link_otp.create_link_session",
        side_effect=RuntimeError("cache unavailable"),
    )
    def test_init_does_not_send_sms_when_cache_write_fails(
        self, mock_cache, mock_send, mock_on_init
    ):
        with self.assertRaises(RuntimeError):
            self._post_init()

        mock_send.assert_not_called()
        mock_on_init.assert_not_called()

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    @patch(
        "abdm.utils.link_otp.send_link_otp",
        side_effect=[
            None,
            LinkOtpDeliveryError("Failed to send OTP SMS"),
        ],
    )
    def test_init_restores_previous_session_when_sms_fails_after_new_cache(
        self, mock_send, mock_on_init
    ):
        old_reference = "old-reference"
        new_reference = "new-reference"

        with patch("abdm.utils.link_otp.uuid4", side_effect=[old_reference, new_reference]):
            first = self._post_init()
            second = self._post_init()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIsNotNone(get_link_session(old_reference))
        self.assertIsNone(get_link_session(new_reference))
        self.assertEqual(
            get_active_session(str(self.patient.external_id)),
            old_reference,
        )
        self.assertEqual(mock_on_init.call_count, 1)

    @patch(
        "abdm.api.v3.viewsets.hip.GatewayService.user_initiated_linking__link__care_context__on_init"
    )
    def test_init_cleans_new_session_when_on_init_fails(self, mock_on_init):
        mock_on_init.side_effect = ABDMAPIException()

        with patch("abdm.utils.link_otp.uuid4", return_value=self.reference_id):
            response = self._post_init()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIsNone(get_link_session(self.reference_id))
        self.assertIsNone(get_active_session(str(self.patient.external_id)))
