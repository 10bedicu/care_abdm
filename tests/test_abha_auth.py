from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from abdm.models import (
    AbhaAuth,
    AbhaAuthInvalidationReason,
    AbhaAuthMethod,
    AbhaNumber,
    ConsentArtefact,
    ConsentRequest,
    HealthFacility,
    HealthInformationType,
    Transaction,
    TransactionType,
)
from abdm.models.base import Status
from abdm.service.abha import (
    abha_number_for_address,
    latest_valid_auth,
    patient_for_abha,
    record_abha_auth,
)
from abdm.service.v3.callback_handlers.hiu import handle_consent_request_notify
from abdm.service.v3.gateway import GatewayService
from abdm.utils import user as abdm_user
from django.utils import timezone
from model_bakery import baker

from care.emr.models.patient import PatientIdentifier
from care.utils.tests.base import CareAPITestBase

ABHA_NUMBER = "91-1111-2222-3333"
OTHER_ABHA_NUMBER = "91-4444-5555-6666"

ABHA_NUMBER_URL = "/api/abdm/abha_number/"
CONSENT_URL = "/api/abdm/consent/"
LINK_PATIENT_URL = "/api/abdm/v3/health_id/link_patient/"
VERIFY_IDENTITY_URL = "/api/abdm/v3/hiu/verify_identity/"


class AbhaAuthTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        # cached across tests but each test rolls back; reset to avoid stale FK.
        abdm_user.ABDM_USER = None
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.health_facility = baker.make(
            HealthFacility, facility=self.facility, hf_id="HIP_A"
        )
        self.other_facility = self.create_facility(self.user)
        self.other_health_facility = baker.make(
            HealthFacility, facility=self.other_facility, hf_id="HIP_B"
        )
        self.patient = self.create_patient()
        self.client.force_authenticate(user=self.user)

    def authenticate(self, abha_number, abha_address, health_facility=None, **kwargs):
        # the lock on the number and the address is released on commit
        with self.captureOnCommitCallbacks(execute=True):
            return record_abha_auth(
                abha_number=abha_number,
                abha_address=abha_address,
                profile={"name": "Ravi", "gender": "M", "date_of_birth": "1990-01-15"},
                health_facility=health_facility or self.health_facility,
                method=AbhaAuthMethod.STAFF_VERIFICATION,
                **kwargs,
            )

    def post(self, url, body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, body, format="json")


class RecordAbhaAuthTests(AbhaAuthTestBase):
    def test_known_number_with_a_new_address_adds_an_auth_to_the_same_record(self):
        record, first, created = self.authenticate(ABHA_NUMBER, "first@sbx")
        self.assertTrue(created)

        # the same number arrives without hyphens from some ABDM calls
        again, second, created = self.authenticate("91111122223333", " Second@SBX ")

        self.assertFalse(created)
        self.assertEqual(again, record)
        self.assertEqual(AbhaNumber.objects.count(), 1)
        self.assertEqual(again.abha_number, ABHA_NUMBER)
        self.assertEqual(second.abha_address, "second@sbx")
        self.assertEqual(latest_valid_auth(record, self.health_facility), second)

        first.refresh_from_db()
        self.assertEqual(first.abha_address, "first@sbx")
        self.assertIsNone(first.invalidated_at)
        self.assertEqual(abha_number_for_address("first@sbx"), record)

    def test_address_only_record_gains_its_number(self):
        record, _, _ = self.authenticate(None, "ravi@sbx")
        self.assertIsNone(record.abha_number)

        again, _, created = self.authenticate(ABHA_NUMBER, "ravi@sbx")

        self.assertFalse(created)
        self.assertEqual(again, record)
        self.assertEqual(again.abha_number, ABHA_NUMBER)

        # an auth that carries no number never clears the stored one
        again, _, _ = self.authenticate(None, "ravi@sbx")
        self.assertEqual(again.abha_number, ABHA_NUMBER)

    def test_number_held_by_a_deleted_record_is_not_written(self):
        baker.make(AbhaNumber, abha_number=ABHA_NUMBER).delete()
        record, _, _ = self.authenticate(None, "ravi@sbx")

        for address in ("ravi@sbx", "unseen@sbx"):
            with self.subTest(address=address):
                again, _, _ = self.authenticate(ABHA_NUMBER, address)

                self.assertIsNone(again.abha_number)

        self.assertEqual(abha_number_for_address("ravi@sbx"), record)

    def test_number_wins_over_an_address_held_by_another_record(self):
        by_address, held, _ = self.authenticate(None, "ravi@sbx")
        by_number, _, _ = self.authenticate(
            ABHA_NUMBER, "other@sbx", self.other_health_facility
        )

        for _ in range(2):  # ABDM repeats callbacks
            record, auth, created = self.authenticate(ABHA_NUMBER, "ravi@sbx")

            self.assertFalse(created)
            self.assertEqual(record, by_number)
            self.assertEqual(auth.abha_address, "ravi@sbx")

        held.refresh_from_db()
        by_address.refresh_from_db()
        self.assertEqual(
            held.invalidation_reason, AbhaAuthInvalidationReason.ADDRESS_MOVED
        )
        self.assertIsNotNone(held.invalidated_at)
        self.assertIsNone(by_address.abha_number)
        self.assertEqual(abha_number_for_address("ravi@sbx"), by_number)
        self.assertIsNone(latest_valid_auth(by_address, self.health_facility))

    def test_address_moved_to_another_number_starts_a_new_record(self):
        old, held, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")
        elsewhere = baker.make(
            AbhaAuth,
            abha_number=old,
            health_facility=self.other_health_facility,
            abha_address="ravi@sbx",
            method=AbhaAuthMethod.SCAN_AND_SHARE,
        )
        kept = baker.make(
            AbhaAuth,
            abha_number=old,
            health_facility=self.health_facility,
            abha_address="still.mine@sbx",
            method=AbhaAuthMethod.SCAN_AND_SHARE,
        )

        new, _, created = self.authenticate(OTHER_ABHA_NUMBER, "ravi@sbx")

        self.assertTrue(created)
        self.assertNotEqual(new, old)
        self.assertEqual(new.abha_number, OTHER_ABHA_NUMBER)
        old.refresh_from_db()
        self.assertEqual(old.abha_number, ABHA_NUMBER)
        self.assertEqual(abha_number_for_address("ravi@sbx"), new)

        # identity is instance-wide: the address is given up at every facility
        for auth in (held, elsewhere):
            auth.refresh_from_db()
            self.assertEqual(
                auth.invalidation_reason, AbhaAuthInvalidationReason.ADDRESS_MOVED
            )
        kept.refresh_from_db()
        self.assertIsNone(kept.invalidated_at)

    def test_link_token_is_carried_forward_for_the_same_facility_and_address(self):
        record, first, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")
        expiry = timezone.now() + timedelta(days=90)
        AbhaAuth.objects.filter(pk=first.pk).update(
            link_token="link-token", link_token_expires_at=expiry
        )

        _, same, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")
        _, other_address, _ = self.authenticate(ABHA_NUMBER, "second@sbx")
        _, other_facility, _ = self.authenticate(
            ABHA_NUMBER, "ravi@sbx", self.other_health_facility
        )

        self.assertEqual(same.link_token, "link-token")
        self.assertEqual(same.link_token_expires_at, expiry)
        self.assertIsNone(other_address.link_token)
        self.assertIsNone(other_facility.link_token)

        AbhaAuth.objects.filter(abha_number=record).update(
            link_token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        _, expired, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")
        self.assertIsNone(expired.link_token)

    def test_an_auth_is_never_overwritten(self):
        _, auth, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")

        auth.abha_address = "changed@sbx"
        with self.assertRaises(ValueError):
            auth.save()
        with self.assertRaises(ValueError):
            auth.save(update_fields=["abha_address"])

        auth.link_token = "link-token"
        auth.save(update_fields=["link_token"])
        auth.refresh_from_db()
        self.assertEqual(auth.abha_address, "ravi@sbx")
        self.assertEqual(auth.link_token, "link-token")

    def test_patient_is_found_by_number_then_by_address(self):
        by_number = baker.make(AbhaNumber, abha_number=ABHA_NUMBER, patient=self.patient)
        other_patient = self.create_patient()
        by_address = baker.make(AbhaNumber, abha_number=None, patient=other_patient)
        baker.make(
            AbhaAuth,
            abha_number=by_address,
            health_facility=self.other_health_facility,
            abha_address="ravi@sbx",
            method=AbhaAuthMethod.SCAN_AND_SHARE,
        )

        self.assertEqual(patient_for_abha(abha_number="91111122223333"), self.patient)
        self.assertEqual(patient_for_abha(abha_address="RAVI@sbx"), other_patient)
        self.assertEqual(
            patient_for_abha(abha_number=by_number.abha_number, abha_address="ravi@sbx"),
            self.patient,
        )
        self.assertIsNone(patient_for_abha(abha_address="nobody@sbx"))


class AbhaNumberApiTests(AbhaAuthTestBase):
    def setUp(self):
        super().setUp()
        self.abha_number, self.auth, _ = self.authenticate(ABHA_NUMBER, "ravi@sbx")
        enqueue = patch(
            "abdm.api.v3.viewsets.health_id.enqueue_link_patient_care_contexts"
        )
        self.enqueue_linking = enqueue.start()
        self.addCleanup(enqueue.stop)

    def link_patient(self, abha_number=None, patient=None, facility=None):
        return self.post(
            LINK_PATIENT_URL,
            {
                "abha_number": str((abha_number or self.abha_number).external_id),
                "patient": str((patient or self.patient).external_id),
                "facility": str((facility or self.facility).external_id),
            },
        )

    def test_first_link_attaches_the_record_and_links_the_ready_records(self):
        response = self.link_patient()

        self.assertEqual(response.status_code, 200, response.data)
        self.abha_number.refresh_from_db()
        self.assertEqual(self.abha_number.patient, self.patient)
        self.assertTrue(
            PatientIdentifier.objects.filter(
                patient=self.patient, value=ABHA_NUMBER
            ).exists()
        )
        self.assertEqual(response.data["abha_address"], "ravi@sbx")
        self.assertEqual(response.data["auth"]["id"], str(self.auth.external_id))
        self.assertEqual(
            self.enqueue_linking.call_args.args, (self.patient, self.health_facility)
        )

    def test_verifying_again_succeeds_and_uses_the_new_auth(self):
        self.link_patient()
        _, second, _ = self.authenticate(ABHA_NUMBER, "second@sbx")

        response = self.link_patient()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["abha_address"], "second@sbx")
        self.assertEqual(response.data["auth"]["id"], str(second.external_id))
        self.assertEqual(self.enqueue_linking.call_count, 2)
        self.assertEqual(AbhaNumber.objects.get(patient=self.patient), self.abha_number)

    def test_a_different_record_is_refused(self):
        self.link_patient()
        other, _, _ = self.authenticate(OTHER_ABHA_NUMBER, "other@sbx")
        self.enqueue_linking.reset_mock()

        # the patient holds another ABHA record, and the record another patient
        for abha_number, patient in (
            (other, self.patient),
            (self.abha_number, self.create_patient()),
        ):
            with self.subTest(abha_number=abha_number.abha_number):
                response = self.link_patient(abha_number=abha_number, patient=patient)

                self.assertEqual(response.status_code, 400)

        other.refresh_from_db()
        self.assertIsNone(other.patient)
        self.assertEqual(AbhaNumber.objects.get(patient=self.patient), self.abha_number)
        self.enqueue_linking.assert_not_called()

    def test_link_needs_an_auth_made_at_that_facility(self):
        unregistered = self.create_facility(self.user)

        for facility in (self.other_facility, unregistered):
            with self.subTest(facility=facility):
                response = self.link_patient(facility=facility)

                self.assertEqual(response.status_code, 400)

        self.abha_number.refresh_from_db()
        self.assertIsNone(self.abha_number.patient)
        self.enqueue_linking.assert_not_called()

    def test_record_is_shown_from_the_facility_asked_about(self):
        self.link_patient()
        _, elsewhere, _ = self.authenticate(
            ABHA_NUMBER, "elsewhere@sbx", self.other_health_facility
        )
        unverified = self.create_facility(self.user)
        baker.make(HealthFacility, facility=unverified, hf_id="HIP_C")

        def get(key, facility):
            response = self.client.get(
                f"{ABHA_NUMBER_URL}{key}/", {"facility": str(facility.external_id)}
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn("health_id", response.data)
            return response.data

        # found by patient, ABHA number or ABHA address
        for key in (self.patient.external_id, ABHA_NUMBER, "ravi@sbx"):
            with self.subTest(key=key):
                here = get(key, self.facility)
                self.assertEqual(here["id"], str(self.abha_number.external_id))
                self.assertEqual(here["abha_address"], "ravi@sbx")
                self.assertEqual(
                    here["auth"],
                    {
                        "id": str(self.auth.external_id),
                        "abha_address": "ravi@sbx",
                        "method": AbhaAuthMethod.STAFF_VERIFICATION,
                        "created_date": here["auth"]["created_date"],
                    },
                )

        there = get(self.patient.external_id, self.other_facility)
        self.assertEqual(there["abha_address"], "elsewhere@sbx")
        self.assertEqual(there["auth"]["id"], str(elsewhere.external_id))

        # no auth there: the address last used anywhere, and no auth to act on
        nowhere = get(self.patient.external_id, unverified)
        self.assertEqual(nowhere["abha_address"], "elsewhere@sbx")
        self.assertIsNone(nowhere["auth"])

    def test_scan_and_pull_records_an_auth_at_the_facility(self):
        body = {
            "abha_number": OTHER_ABHA_NUMBER,
            "abha_address": "scanned@sbx",
            "name": "Scanned Patient",
            "gender": "F",
        }

        missing = self.post(ABHA_NUMBER_URL, body)
        self.assertEqual(missing.status_code, 400)

        response = self.post(
            ABHA_NUMBER_URL, {**body, "facility": str(self.facility.external_id)}
        )

        self.assertEqual(response.status_code, 201, response.data)
        auth = AbhaAuth.objects.get(abha_address="scanned@sbx")
        self.assertEqual(auth.health_facility, self.health_facility)
        self.assertEqual(auth.method, AbhaAuthMethod.STAFF_VERIFICATION)
        self.assertEqual(auth.performed_by, self.user)
        self.assertEqual(auth.abha_number.name, "Scanned Patient")
        self.assertEqual(response.data["abha_number"], OTHER_ABHA_NUMBER)
        self.assertEqual(response.data["auth"]["id"], str(auth.external_id))
        self.assertTrue(
            Transaction.objects.filter(
                type=TransactionType.CREATE_OR_LINK_ABHA_NUMBER,
                meta_data__abha_number=str(auth.abha_number.external_id),
                meta_data__method="scan_and_pull",
            ).exists()
        )


class ConsentRequestTests(AbhaAuthTestBase):
    def setUp(self):
        super().setUp()
        self.abha_number = baker.make(
            AbhaNumber, abha_number=ABHA_NUMBER, patient=self.patient, name="Ravi"
        )
        self.encounter = self.create_encounter(
            self.patient,
            self.facility,
            self.create_facility_organization(self.facility),
        )
        self.gateway = MagicMock()
        self.gateway.post.return_value.status_code = 202
        gateway = patch.object(GatewayService, "request", self.gateway)
        gateway.start()
        self.addCleanup(gateway.stop)

    def auth_at(self, health_facility, abha_address):
        return baker.make(
            AbhaAuth,
            abha_number=self.abha_number,
            health_facility=health_facility,
            abha_address=abha_address,
            method=AbhaAuthMethod.STAFF_VERIFICATION,
        )

    def request_consent(self, **body):
        return self.post(
            CONSENT_URL,
            {
                "encounter": str(self.encounter.external_id),
                "hi_types": [HealthInformationType.PRESCRIPTION],
                **body,
            },
        )

    def test_consent_is_requested_with_the_auth_made_at_the_encounter_facility(self):
        self.auth_at(self.health_facility, "old@sbx")
        latest = self.auth_at(self.health_facility, "ravi@sbx")
        self.auth_at(self.other_health_facility, "elsewhere@sbx")

        response = self.request_consent()

        self.assertEqual(response.status_code, 201, response.data)
        path, payload = self.gateway.post.call_args.args
        self.assertEqual(path, "/consent/v3/request/init")
        self.assertEqual(payload["consent"]["patient"], {"id": "ravi@sbx"})
        self.assertEqual(payload["consent"]["hiu"], {"id": "HIP_A"})
        self.assertEqual(
            self.gateway.post.call_args.kwargs["headers"]["X-HIU-ID"], "HIP_A"
        )

        consent = ConsentRequest.objects.get(external_id=response.data["id"])
        self.assertEqual(consent.abha_auth, latest)
        self.assertEqual(response.data["abha_address"], "ravi@sbx")
        self.assertEqual(
            response.data["abha_number_object"]["id"],
            str(self.abha_number.external_id),
        )
        self.assertNotIn("patient_abha", response.data)
        self.assertNotIn("patient_abha_object", response.data)

        for query, count in (
            ({"abha_address": "Ravi@sbx"}, 1),
            ({"abha_address": "old@sbx"}, 0),
            ({"patient": str(self.patient.external_id)}, 1),
        ):
            with self.subTest(query=query):
                listed = self.client.get(CONSENT_URL, query)
                self.assertEqual(listed.data["count"], count)

    def test_consent_needs_an_auth_made_at_the_encounter_facility(self):
        self.auth_at(self.other_health_facility, "elsewhere@sbx")

        without_auth = self.request_consent()
        without_encounter = self.post(
            CONSENT_URL, {"hi_types": [HealthInformationType.PRESCRIPTION]}
        )

        self.assertEqual(without_auth.status_code, 400)
        self.assertEqual(without_encounter.status_code, 400)
        self.gateway.post.assert_not_called()
        self.assertFalse(ConsentRequest.objects.exists())

    def test_granted_artefacts_take_their_scope_from_the_request_auth(self):
        auth = self.auth_at(self.health_facility, "ravi@sbx")
        consent = baker.make(
            ConsentRequest,
            abha_auth=auth,
            encounter=self.encounter,
            consent_id=uuid4(),
        )
        artefact_id = "0b1f2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"

        handle_consent_request_notify(
            {
                "notification": {
                    "consentRequestId": consent.consent_id,
                    "status": Status.GRANTED,
                    "consentArtefacts": [{"id": artefact_id}],
                }
            },
            {"REQUEST-ID": "req"},
        )

        artefact = ConsentArtefact.objects.get(external_id=artefact_id)
        self.assertEqual(artefact.health_facility, self.health_facility)
        self.assertEqual(artefact.abha_address, "ravi@sbx")
        self.assertEqual(artefact.patient, self.patient)
        # status, fetch and the health information request all go out as this HIU
        hiu_ids = {
            call.kwargs["headers"].get("X-HIU-ID")
            for call in self.gateway.post.call_args_list
            if call.args[0] == "/consent/v3/fetch"
        }
        self.assertEqual(hiu_ids, {"HIP_A"})

    def test_identity_is_verified_with_the_auth_made_at_the_facility(self):
        self.auth_at(self.health_facility, "ravi@sbx")
        self.gateway.post.return_value.status_code = 200
        self.gateway.post.return_value.json.return_value = {
            "authenticated": True,
            "abhaAddress": "ravi@sbx",
            "transactionId": "txn",
            "response": {"requestId": "req"},
        }

        def verify(facility):
            return self.post(
                VERIFY_IDENTITY_URL,
                {
                    "patient": str(self.patient.external_id),
                    "facility": str(facility.external_id),
                },
            )

        self.assertEqual(verify(self.other_facility).status_code, 404)
        self.gateway.post.assert_not_called()

        response = verify(self.facility)

        self.assertEqual(response.status_code, 202, response.data)
        payload = self.gateway.post.call_args.args[1]
        self.assertEqual(payload["parameters"]["abhaAddress"], "ravi@sbx")
        self.assertEqual(payload["parameters"]["abhaNumber"], "91111122223333")
        self.assertEqual(
            self.gateway.post.call_args.kwargs["headers"]["REQUESTER-ID"], "HIP_A"
        )
