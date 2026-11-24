from unittest.mock import MagicMock, patch

from abdm.models import HealthInformationType
from abdm.service.helper import ABDMAPIException
from abdm.service.v3.gateway import GatewayService, _care_context_fhir_bundle
from model_bakery import baker

from care.utils.tests.base import CareAPITestBase

GOOD = "v2::encounter::good"
BAD = "v2::invoice::bad"


class HealthInformationTransferTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.consent = baker.prepare(
            "abdm.ConsentArtefact",
            care_contexts=[
                {"patientReference": "p", "careContextReference": GOOD},
                {"patientReference": "p", "careContextReference": BAD},
            ],
            hi_types=[
                HealthInformationType.OP_CONSULTATION,
                HealthInformationType.INVOICE,
            ],
        )

    def transfer(self, build):
        bundle = MagicMock()
        bundle.json.return_value = "{}"
        cipher = MagicMock(key_to_share="key", internal_nonce="nonce")
        cipher.encrypt.return_value = {"data": "encrypted"}

        def fake_build(care_context, consent):
            return build(care_context["careContextReference"], bundle)

        with (
            patch("abdm.service.v3.gateway.Cipher", return_value=cipher),
            patch("abdm.service.v3.gateway.Request") as request,
            patch("abdm.service.v3.gateway.requests.post") as post,
            patch(
                "abdm.service.v3.gateway._care_context_fhir_bundle",
                side_effect=fake_build,
            ),
        ):
            request.return_value.auth_header.return_value = {}
            post.return_value.ok = True
            self.post = post
            result = GatewayService.data_flow__health_information__transfer(
                {
                    "transaction_id": "txn",
                    "consent": self.consent,
                    "url": "https://hiu.example/push",
                    "key_material__crypto_algorithm": "ECDH",
                    "key_material__curve": "Curve25519",
                    "key_material__public_key": "pub",
                    "key_material__nonce": "nonce",
                }
            )
        return result, post.call_args.kwargs["json"]["entries"]

    def test_failing_care_context_does_not_abort_transfer(self):
        def build(reference, bundle):
            if reference == BAD:
                raise RuntimeError
            return bundle

        result, entries = self.transfer(build)

        self.assertEqual([entry["careContextReference"] for entry in entries], [GOOD])
        self.assertEqual(result["errored_care_context_references"], [BAD])

    def test_skipped_care_context_is_not_reported_as_errored(self):
        result, entries = self.transfer(
            lambda reference, bundle: bundle if reference == GOOD else None
        )

        self.assertEqual([entry["careContextReference"] for entry in entries], [GOOD])
        self.assertEqual(result["errored_care_context_references"], [])

    def test_transfer_fails_when_no_care_context_could_be_built(self):
        def build(reference, bundle):
            raise RuntimeError

        with self.assertRaises(ABDMAPIException):
            self.transfer(build)
        self.post.assert_not_called()

    def notify(self, status, errored, notifier_type="HIP"):
        with patch.object(GatewayService.request, "post") as post:
            post.return_value.status_code = 202
            GatewayService.data_flow__health_information__notify(
                {
                    "consent": self.consent,
                    "consent_id": "consent",
                    "transaction_id": "txn",
                    "notifier__type": notifier_type,
                    "notifier__id": "hip",
                    "status": status,
                    "hip_id": "hip",
                    "errored_care_context_references": errored,
                }
            )
        responses = post.call_args.args[1]["notification"]["statusNotification"][
            "statusResponses"
        ]
        return {
            r["careContextReference"]: (r["hiStatus"], r["description"])
            for r in responses
        }

    def test_notify_reports_errored_care_contexts(self):
        self.assertEqual(
            self.notify("TRANSFERRED", [BAD]),
            {
                GOOD: ("DELIVERED", "TRANSFERRED"),
                BAD: ("ERRORED", "Failed to generate health information"),
            },
        )

    def test_notify_reports_every_care_context_errored_on_failed_session(self):
        self.assertEqual(
            self.notify("FAILED", []),
            {GOOD: ("ERRORED", "FAILED"), BAD: ("ERRORED", "FAILED")},
        )

    def test_hiu_notify_uses_hiu_status_values(self):
        self.assertEqual(
            self.notify("RECEIVED", [], notifier_type="HIU"),
            {GOOD: ("OK", "RECEIVED"), BAD: ("OK", "RECEIVED")},
        )
        self.assertEqual(
            self.notify("FAILED", [], notifier_type="HIU"),
            {GOOD: ("ERRORED", "FAILED"), BAD: ("ERRORED", "FAILED")},
        )

    def test_bundle_dispatch_builds_requested_types_only(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        organization = self.create_facility_organization(facility)
        patient = self.create_patient(name="Transfer Patient", gender="female")
        encounter = self.create_encounter(patient, facility, organization)
        reference = {
            "patientReference": str(patient.external_id),
            "careContextReference": f"v2::encounter::{encounter.external_id}",
        }

        bundle = _care_context_fhir_bundle(reference, self.consent)
        self.assertEqual(bundle.entry[0].resource.resource_type, "Composition")

        self.consent.hi_types = [HealthInformationType.PRESCRIPTION]
        self.assertIsNone(_care_context_fhir_bundle(reference, self.consent))
