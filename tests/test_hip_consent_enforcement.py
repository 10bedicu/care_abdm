from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from abdm.api.v3.serializers.hip import (
    ConsentRequestHipNotifySerializer,
    HipHealthInformationRequestSerializer,
)
from abdm.models.base import Status
from abdm.models.consent import ConsentArtefact
from abdm.service.v3.callback_handlers.hip import (
    handle_consent_request_hip_notify,
    handle_health_information_request,
)
from abdm.service.v3.gateway import GatewayService
from model_bakery import baker

from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now


class HipConsentEnforcementTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        now = care_now()
        self.consent = baker.make(
            "abdm.ConsentArtefact",
            consent_id=uuid4(),
            patient_abha=baker.make("abdm.AbhaNumber", health_id="patient@sbx"),
            status=Status.GRANTED,
            from_time=now - timedelta(days=30),
            to_time=now - timedelta(days=1),
            expiry=now + timedelta(days=30),
        )

    def request(self, **date_range):
        data = {
            "transactionId": uuid4(),
            "hiRequest": {
                "consent": {"id": self.consent.consent_id},
                "dateRange": {
                    "from": self.consent.from_time,
                    "to": self.consent.to_time,
                    **date_range,
                },
                "dataPushUrl": "https://hiu.example/push",
                "keyMaterial": {
                    "cryptoAlg": "ECDH",
                    "curve": "Curve25519",
                    "dhPublicKey": {"keyValue": "pub"},
                    "nonce": "nonce",
                },
            },
        }
        with patch("abdm.service.v3.callback_handlers.hip.GatewayService") as gateway:
            handle_health_information_request(data, {"REQUEST-ID": "req"})
        return gateway

    def assert_refused(self, gateway, code):
        on_request = gateway.data_flow__health_information__hip__on_request
        self.assertEqual(on_request.call_args.args[0]["error"]["code"], code)
        gateway.data_flow__health_information__transfer.assert_not_called()
        gateway.data_flow__health_information__notify.assert_not_called()

    def test_granted_consent_is_acknowledged_and_transferred(self):
        gateway = self.request()

        on_request = gateway.data_flow__health_information__hip__on_request
        self.assertIsNone(on_request.call_args.args[0]["error"])
        gateway.data_flow__health_information__transfer.assert_called_once()

    def test_consent_that_is_not_granted_is_refused(self):
        for status in (Status.REVOKED, Status.DENIED, Status.REQUESTED):
            with self.subTest(status=status):
                self.consent.status = status
                self.consent.save()

                self.assert_refused(self.request(), "ABDM-1062")

    def test_expired_consent_is_refused(self):
        self.consent.expiry = care_now() - timedelta(minutes=1)
        self.consent.save()
        self.assert_refused(self.request(), "ABDM-1112")

        self.consent.expiry = care_now() + timedelta(days=1)
        self.consent.status = Status.EXPIRED
        self.consent.save()
        self.assert_refused(self.request(), "ABDM-1112")

    def test_date_range_outside_the_consent_is_refused(self):
        day = timedelta(days=1)
        for date_range in (
            {"from": self.consent.from_time - day},
            {"to": self.consent.to_time + day},
            {"from": self.consent.to_time, "to": self.consent.from_time},
        ):
            with self.subTest(date_range=date_range):
                self.assert_refused(self.request(**date_range), "ABDM-1063")

    def test_refusal_is_sent_in_place_of_the_acknowledgement(self):
        error = {"code": "ABDM-1062", "message": "Consent Not granted"}
        request = MagicMock()
        request.post.return_value.status_code = 200

        with patch.object(GatewayService, "request", request):
            GatewayService.data_flow__health_information__hip__on_request(
                {"request_id": "req", "transaction_id": "txn", "error": error}
            )

        payload = request.post.call_args.args[1]
        self.assertEqual(payload["error"], error)
        self.assertEqual(payload["response"], {"requestId": "req"})
        self.assertNotIn("hiRequest", payload)

    def test_revocation_without_consent_detail_is_recorded(self):
        for status in (Status.REVOKED, Status.EXPIRED):
            with self.subTest(status=status):
                notice = {
                    "notification": {
                        "status": status,
                        "consentId": str(self.consent.consent_id),
                    }
                }
                serializer = ConsentRequestHipNotifySerializer(data=notice)
                self.assertTrue(serializer.is_valid(), serializer.errors)

                with patch(
                    "abdm.service.v3.callback_handlers.hip.GatewayService"
                ) as gateway:
                    handle_consent_request_hip_notify(
                        serializer.validated_data, {"REQUEST-ID": "req"}
                    )

                self.consent.refresh_from_db()
                self.assertEqual(self.consent.status, status)
                gateway.consent__request__hip__on_notify.assert_called_once()

    def test_grant_without_consent_detail_is_rejected(self):
        serializer = ConsentRequestHipNotifySerializer(
            data={
                "notification": {
                    "status": Status.GRANTED,
                    "consentId": str(self.consent.consent_id),
                }
            }
        )

        self.assertFalse(serializer.is_valid())

    def test_grant_stores_the_consented_date_range_and_expiry(self):
        consent_id = str(uuid4())
        serializer = ConsentRequestHipNotifySerializer(
            data={
                "notification": {
                    "status": Status.GRANTED,
                    "consentId": consent_id,
                    "signature": "signature",
                    "consentDetail": {
                        "schemaVersion": "v3",
                        "consentId": consent_id,
                        "createdAt": "2026-11-01T10:00:00.000Z",
                        "patient": {"id": "patient@sbx"},
                        "careContexts": [
                            {"patientReference": "p", "careContextReference": "c"}
                        ],
                        "purpose": {"code": "CAREMGT", "refUri": None},
                        "hip": {"id": "hip"},
                        "consentManager": {"id": "sbx"},
                        "hiTypes": ["OPConsultation"],
                        "permission": {
                            "accessMode": "VIEW",
                            "dateRange": {
                                "from": "2026-10-01T00:00:00.000Z",
                                "to": "2026-11-01T00:00:00.000Z",
                            },
                            "dataEraseAt": "2026-12-01T00:00:00.000Z",
                            "frequency": {"unit": "HOUR", "value": 1, "repeats": 0},
                        },
                    },
                }
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with (
            patch(
                "abdm.service.v3.callback_handlers.hip.get_patient_by_abha_id",
                return_value=MagicMock(abha_number=self.consent.patient_abha),
            ),
            patch("abdm.service.v3.callback_handlers.hip.GatewayService"),
            patch(
                "abdm.service.v3.callback_handlers.hip.get_or_create_abdm_user",
                return_value=self.create_user(),
            ),
        ):
            handle_consent_request_hip_notify(
                serializer.validated_data, {"REQUEST-ID": "req"}
            )

        stored = ConsentArtefact.objects.get(consent_id=consent_id)
        self.assertEqual(stored.from_time, datetime(2026, 10, 1, tzinfo=UTC))
        self.assertEqual(stored.to_time, datetime(2026, 11, 1, tzinfo=UTC))
        self.assertEqual(stored.expiry, datetime(2026, 12, 1, tzinfo=UTC))

    def test_malformed_date_range_fails_validation(self):
        def validate(date_range):
            serializer = HipHealthInformationRequestSerializer(
                data={
                    "transactionId": str(uuid4()),
                    "hiRequest": {
                        "consent": {"id": str(self.consent.consent_id)},
                        "dateRange": date_range,
                        "dataPushUrl": "https://hiu.example/push",
                        "keyMaterial": {
                            "cryptoAlg": "ECDH",
                            "curve": "Curve25519",
                            "dhPublicKey": {
                                "expiry": "2026-12-01T00:00:00.000Z",
                                "keyValue": "pub",
                            },
                            "nonce": "nonce",
                        },
                    },
                }
            )
            serializer.is_valid()
            return serializer

        for date_range in (
            None,
            {},
            {"from": "2026-10-01T00:00:00.000Z"},
            {"to": "2026-11-01T00:00:00.000Z"},
            {"from": None, "to": "2026-11-01T00:00:00.000Z"},
            {"from": "yesterday", "to": "2026-11-01T00:00:00.000Z"},
        ):
            with self.subTest(date_range=date_range):
                self.assertIn("dateRange", validate(date_range).errors["hiRequest"])

        serializer = validate(
            {"from": "2026-10-01T00:00:00", "to": "2026-11-01T05:30:00+05:30"}
        )
        self.assertEqual(
            dict(serializer.validated_data["hiRequest"]["dateRange"]),
            {
                "from": datetime(2026, 10, 1, tzinfo=UTC),
                "to": datetime(2026, 11, 1, tzinfo=UTC),
            },
        )
