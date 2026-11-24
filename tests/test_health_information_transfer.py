from datetime import timedelta
from unittest.mock import MagicMock, patch

from abdm.models import HealthInformationType
from abdm.service.helper import ABDMAPIException
from abdm.service.v3.gateway import GatewayService, _care_context_fhir_bundle
from model_bakery import baker

from care.emr.models.file_upload import FileUpload
from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now

GOOD = "v2::encounter::good"
BAD = "v2::invoice::bad"


class HealthInformationTransferTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.last_month = {
            "from": care_now() - timedelta(days=30),
            "to": care_now() + timedelta(days=1),
        }
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

        def fake_build(care_context, consent, date_range):
            self.assertIs(date_range, self.last_month)
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
                    "date_range": self.last_month,
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
        self.assertEqual(result["skipped_care_context_references"], [])

    def test_care_context_without_data_is_reported_as_skipped(self):
        result, entries = self.transfer(
            lambda reference, bundle: bundle if reference == GOOD else None
        )

        self.assertEqual([entry["careContextReference"] for entry in entries], [GOOD])
        self.assertEqual(result["errored_care_context_references"], [])
        self.assertEqual(result["skipped_care_context_references"], [BAD])

    def test_transfer_fails_when_no_care_context_could_be_built(self):
        def build(reference, bundle):
            raise RuntimeError

        with self.assertRaises(ABDMAPIException):
            self.transfer(build)
        self.post.assert_not_called()

    def test_transfer_fails_when_there_is_nothing_to_send(self):
        with self.assertRaises(ABDMAPIException):
            self.transfer(lambda reference, bundle: None)
        self.post.assert_not_called()

    def notify(self, status, errored, notifier_type="HIP", skipped=()):
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
                    "skipped_care_context_references": list(skipped),
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

    def test_notify_reports_skipped_care_contexts_as_errored(self):
        self.assertEqual(
            self.notify("TRANSFERRED", [], skipped=[BAD]),
            {
                GOOD: ("DELIVERED", "TRANSFERRED"),
                BAD: ("ERRORED", "No health information found"),
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

    def encounter_reference(self, **kwargs):
        user = self.create_super_user()
        facility = self.create_facility(user)
        organization = self.create_facility_organization(facility)
        patient = self.create_patient(name="Transfer Patient", gender="female")
        encounter = self.create_encounter(patient, facility, organization, **kwargs)
        return {
            "patientReference": str(patient.external_id),
            "careContextReference": f"v2::encounter::{encounter.external_id}",
        }

    def test_bundle_dispatch_builds_requested_types_only(self):
        reference = self.encounter_reference()

        bundle = _care_context_fhir_bundle(reference, self.consent, self.last_month)
        self.assertEqual(bundle.entry[0].resource.resource_type, "Composition")

        self.consent.hi_types = [HealthInformationType.PRESCRIPTION]
        self.assertIsNone(
            _care_context_fhir_bundle(reference, self.consent, self.last_month)
        )

    def test_encounter_outside_the_requested_range_is_not_shared(self):
        two_months_ago = care_now() - timedelta(days=60)
        reference = self.encounter_reference(
            period={
                "start": two_months_ago.isoformat(),
                "end": (two_months_ago + timedelta(hours=1)).isoformat(),
            }
        )

        self.assertIsNone(
            _care_context_fhir_bundle(reference, self.consent, self.last_month)
        )
        self.assertIsNotNone(
            _care_context_fhir_bundle(
                reference,
                self.consent,
                {"from": two_months_ago - timedelta(days=1), "to": care_now()},
            )
        )

    def test_ongoing_encounter_overlapping_the_range_is_shared(self):
        reference = self.encounter_reference(
            period={"start": (care_now() - timedelta(days=60)).isoformat()}
        )

        self.assertIsNotNone(
            _care_context_fhir_bundle(reference, self.consent, self.last_month)
        )

    def test_record_created_outside_the_requested_range_is_not_shared(self):
        file_upload = baker.make(FileUpload)
        FileUpload.objects.filter(pk=file_upload.pk).update(
            created_date=care_now() - timedelta(days=60)
        )
        self.consent.hi_types = [HealthInformationType.RECORD_ARTIFACT]

        self.assertIsNone(
            _care_context_fhir_bundle(
                {
                    "patientReference": "p",
                    "careContextReference": f"v2::file_upload::{file_upload.external_id}",
                },
                self.consent,
                self.last_month,
            )
        )
