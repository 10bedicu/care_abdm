from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from abdm.settings import plugin_settings
from abdm.utils.link_otp import (
    LinkOtpAttemptsExceeded,
    LinkOtpDeliveryError,
    LinkOtpSendRateLimited,
    LinkOtpThrottled,
    _failure_count_key,
    check_and_increment_send_count,
    clear_failure_count,
    create_link_session,
    decrement_send_count,
    delete_link_session,
    delete_link_session_and_active,
    finalize_link_otp_confirmation,
    get_active_session,
    get_link_session,
    handle_failed_verify,
    hash_otp,
    init_link_otp,
    is_locked_out,
    send_link_otp,
    set_active_session,
    set_lockout,
    verify_otp,
)


class LinkOtpUtilsTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def test_verify_otp_accepts_correct_value(self):
        otp = "654321"
        self.assertTrue(verify_otp(otp, hash_otp(otp)))

    def test_verify_otp_rejects_wrong_value(self):
        self.assertFalse(verify_otp("111111", hash_otp("654321")))

    def test_verify_otp_rejects_blank_or_missing_hash(self):
        self.assertFalse(verify_otp("", hash_otp("654321")))
        self.assertFalse(verify_otp("654321", None))

    def test_session_crud_round_trip(self):
        reference_id = "ref-123"
        create_link_session(
            reference_id,
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
        )

        cached = get_link_session(reference_id)
        self.assertEqual(cached["otp_hash"], hash_otp("123456"))
        self.assertEqual(cached["failed_attempts"], 0)
        self.assertNotIn("otp", cached)

        delete_link_session(reference_id)
        self.assertIsNone(get_link_session(reference_id))

    @override_settings(USE_SMS=False)
    def test_send_link_otp_fails_when_sms_disabled(self):
        with self.assertRaises(LinkOtpDeliveryError):
            send_link_otp("+919876543210", "123456")

    @override_settings(USE_SMS=True)
    @patch("abdm.utils.link_otp.sms.send_text_message", return_value=0)
    def test_send_link_otp_fails_when_dispatch_returns_zero(self, mock_send):
        with self.assertRaises(LinkOtpDeliveryError):
            send_link_otp("+919876543210", "123456")
        mock_send.assert_called_once()

    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_SENDS_PER_WINDOW", 2)
    def test_send_count_enforces_window_cap(self):
        check_and_increment_send_count("patient-1")
        check_and_increment_send_count("patient-1")
        with self.assertRaises(LinkOtpSendRateLimited):
            check_and_increment_send_count("patient-1")

    def test_decrement_send_count_rolls_back_counter(self):
        with patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_SENDS_PER_WINDOW", 1):
            check_and_increment_send_count("patient-1")
            with self.assertRaises(LinkOtpSendRateLimited):
                check_and_increment_send_count("patient-1")

        decrement_send_count("patient-1")
        check_and_increment_send_count("patient-1")

    def test_clear_failure_count_clears_lockout(self):
        set_lockout("patient-1")
        clear_failure_count("patient-1")
        self.assertFalse(is_locked_out("patient-1"))
        self.assertIsNone(cache.get(_failure_count_key("patient-1")))

    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_VERIFY_ATTEMPTS", 3)
    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_FAILURES", 10)
    def test_handle_failed_verify_invalidates_session_at_cap(self):
        session = {
            "reference_id": "ref-1",
            "patient_id": "patient-1",
            "otp_hash": hash_otp("123456"),
            "failed_attempts": 2,
        }
        create_link_session(
            "ref-1",
            otp_hash=session["otp_hash"],
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
            failed_attempts=2,
        )
        set_active_session("patient-1", "ref-1")

        with self.assertRaises(LinkOtpAttemptsExceeded):
            handle_failed_verify(session)

        self.assertIsNone(get_link_session("ref-1"))
        self.assertIsNone(get_active_session("patient-1"))

    def test_active_session_helpers(self):
        set_active_session("patient-1", "ref-1")
        self.assertEqual(get_active_session("patient-1"), "ref-1")

        create_link_session(
            "ref-1",
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
        )
        delete_link_session_and_active("ref-1", "patient-1")

        self.assertIsNone(get_link_session("ref-1"))
        self.assertIsNone(get_active_session("patient-1"))

    def test_delete_link_session_and_active_leaves_other_active_pointer(self):
        set_active_session("patient-1", "ref-2")
        create_link_session(
            "ref-1",
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
        )

        delete_link_session_and_active("ref-1", "patient-1")

        self.assertIsNone(get_link_session("ref-1"))
        self.assertEqual(get_active_session("patient-1"), "ref-2")

    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_VERIFY_ATTEMPTS", 5)
    @patch.object(plugin_settings, "ABDM_LINK_OTP_MAX_FAILURES", 2)
    def test_handle_failed_verify_locks_out_patient(self):
        session = {
            "reference_id": "ref-1",
            "patient_id": "patient-1",
            "otp_hash": hash_otp("123456"),
            "failed_attempts": 0,
        }
        create_link_session(
            "ref-1",
            otp_hash=session["otp_hash"],
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
        )

        handle_failed_verify(session)
        with self.assertRaises(LinkOtpThrottled):
            handle_failed_verify(get_link_session("ref-1"))

        self.assertTrue(is_locked_out("patient-1"))
        self.assertIsNone(get_link_session("ref-1"))

    @override_settings(
        USE_SMS=True,
        SMS_BACKEND="care.utils.sms.backend.console.ConsoleBackend",
    )
    @patch("abdm.utils.link_otp.uuid4", return_value="ref-init")
    def test_init_link_otp_creates_session_before_sending_sms(self, _mock_uuid):
        reference_id = init_link_otp(
            patient_id="patient-1",
            phone_number="+919876543210",
            abha_address="user@abdm",
            care_contexts=["ctx-1"],
        )

        self.assertEqual(reference_id, "ref-init")
        cached = get_link_session("ref-init")
        self.assertIsNotNone(cached)
        self.assertEqual(get_active_session("patient-1"), "ref-init")

    @override_settings(
        USE_SMS=True,
        SMS_BACKEND="care.utils.sms.backend.console.ConsoleBackend",
    )
    @patch("abdm.utils.link_otp.uuid4", return_value="new-ref")
    @patch("abdm.utils.link_otp.send_link_otp", side_effect=LinkOtpDeliveryError("fail"))
    def test_init_link_otp_rolls_back_on_sms_failure(self, mock_send, _mock_uuid):
        create_link_session(
            "old-ref",
            otp_hash=hash_otp("111111"),
            abha_address="user@abdm",
            patient_id="patient-1",
            care_contexts=["ctx-1"],
        )
        set_active_session("patient-1", "old-ref")

        with self.assertRaises(LinkOtpDeliveryError):
            init_link_otp(
                patient_id="patient-1",
                phone_number="+919876543210",
                abha_address="user@abdm",
                care_contexts=["ctx-1"],
            )

        mock_send.assert_called_once()
        self.assertIsNone(get_link_session("new-ref"))
        self.assertIsNotNone(get_link_session("old-ref"))
        self.assertEqual(get_active_session("patient-1"), "old-ref")

    def test_finalize_link_otp_confirmation_clears_session_and_counters(self):
        patient_id = "patient-1"
        create_link_session(
            "ref-1",
            otp_hash=hash_otp("123456"),
            abha_address="user@abdm",
            patient_id=patient_id,
            care_contexts=["ctx-1"],
        )
        set_active_session(patient_id, "ref-1")
        set_lockout(patient_id)

        finalize_link_otp_confirmation(reference_id="ref-1", patient_id=patient_id)

        self.assertIsNone(get_link_session("ref-1"))
        self.assertIsNone(get_active_session(patient_id))
        self.assertFalse(is_locked_out(patient_id))

    def tearDown(self):
        cache.clear()
