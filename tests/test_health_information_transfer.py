from datetime import timedelta
from unittest.mock import MagicMock, patch

from abdm.models import HealthInformationType
from abdm.service.helper import ABDMAPIException
from abdm.service.v3.gateway import (
    GatewayService,
    _care_context_belongs_to_consent,
    _care_context_fhir_bundle,
)
from django.utils import timezone
from model_bakery import baker

from care.emr.models.file_upload import FileUpload
from care.emr.models.immunisation import Immunization
from care.emr.models.medication_request import MedicationRequest
from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.emr.resources.immunisation.immunization import ImmunizationStatus
from care.emr.resources.medication.request.spec import MedicationRequestStatus
from care.utils.tests.base import CareAPITestBase
from care.utils.time_util import care_now

GOOD = "v2::encounter::good"
BAD = "v2::invoice::bad"
OPTED_OUT = {"abdm": {"link_records": False}}


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

    def transfer(self, build, check_ownership=False):
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
            patch(
                "abdm.service.v3.gateway._care_context_belongs_to_consent",
                **(
                    {"wraps": _care_context_belongs_to_consent}
                    if check_ownership
                    else {"return_value": True}
                ),
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

    def test_only_records_of_the_consent_patient_at_its_facility_are_shared(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        health_facility = baker.make(
            "abdm.HealthFacility", facility=facility, hf_id="HIP_A"
        )
        organization = self.create_facility_organization(facility)
        patient = self.create_patient()
        other_patient = self.create_patient()

        def care_context(
            encounter_patient=patient, at=facility, named=patient, **kwargs
        ):
            encounter = self.create_encounter(
                encounter_patient, at, organization, **kwargs
            )
            return {
                "patientReference": str(named.external_id),
                "careContextReference": f"v2::encounter::{encounter.external_id}",
            }

        def link(care_context, abha_address, **kwargs):
            baker.make(
                "abdm.CareContext",
                health_facility=health_facility,
                patient=patient,
                abha_address=abha_address,
                reference=care_context["careContextReference"],
                hi_type=HealthInformationType.OP_CONSULTATION,
                **kwargs,
            )

        own, linked, backfilled = care_context(), care_context(), care_context()
        link(linked, "ravi@sbx")
        link(linked, "second@sbx")
        # a backfilled link carries whatever address was stored at the time
        link(backfilled, "overwritten@sbx", legacy=True)

        linked_to_another_address = care_context()
        link(linked_to_another_address, "second@sbx")
        refused = [
            care_context(encounter_patient=other_patient),
            care_context(encounter_patient=other_patient, named=other_patient),
            care_context(at=self.create_facility(user)),
            care_context(extensions=OPTED_OUT),
            linked_to_another_address,
            {"patientReference": str(patient.external_id), "careContextReference": BAD},
        ]

        self.consent = baker.make(
            "abdm.ConsentArtefact",
            health_facility=health_facility,
            patient=patient,
            abha_address="ravi@sbx",
            care_contexts=[own, linked, backfilled, *refused],
            hi_types=[HealthInformationType.OP_CONSULTATION],
        )

        result, entries = self.transfer(
            lambda reference, bundle: bundle, check_ownership=True
        )

        self.assertEqual(
            [entry["careContextReference"] for entry in entries],
            [c["careContextReference"] for c in (own, linked, backfilled)],
        )
        self.assertEqual(
            result["errored_care_context_references"],
            [c["careContextReference"] for c in refused],
        )

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
        encounter = self.create_encounter(
            patient,
            facility,
            organization,
            **{"encounter_class": ClassChoices.amb.value, **kwargs},
        )
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

    def test_encounter_is_shared_as_the_record_type_of_its_current_class(self):
        both = [
            HealthInformationType.OP_CONSULTATION,
            HealthInformationType.DISCHARGE_SUMMARY,
        ]
        for encounter_class, hi_type, profile in (
            (ClassChoices.amb, both[0], "OPConsultRecord"),
            (ClassChoices.hh, both[0], "OPConsultRecord"),
            (ClassChoices.imp, both[1], "DischargeSummaryRecord"),
            (ClassChoices.emer, both[1], "DischargeSummaryRecord"),
            (ClassChoices.obsenc, both[1], "DischargeSummaryRecord"),
        ):
            with self.subTest(encounter_class=encounter_class):
                reference = self.encounter_reference(
                    encounter_class=encounter_class.value
                )

                # whichever types the consent allows, the class decides
                self.consent.hi_types = both
                bundle = _care_context_fhir_bundle(
                    reference, self.consent, self.last_month
                )
                composition = bundle.entry[0].resource
                self.assertTrue(composition.meta.profile[0].endswith(profile))
                self.assertEqual(composition.status, "final")

                self.consent.hi_types = [hi_type]
                self.assertIsNotNone(
                    _care_context_fhir_bundle(reference, self.consent, self.last_month)
                )

                # and it is not sent as the other type when only that is allowed
                self.consent.hi_types = [other for other in both if other != hi_type]
                self.assertIsNone(
                    _care_context_fhir_bundle(reference, self.consent, self.last_month)
                )

    def test_encounter_entered_in_error_is_shared_marked_as_such(self):
        for encounter_class, hi_type in (
            (ClassChoices.amb, HealthInformationType.OP_CONSULTATION),
            (ClassChoices.imp, HealthInformationType.DISCHARGE_SUMMARY),
        ):
            with self.subTest(encounter_class=encounter_class):
                reference = self.encounter_reference(
                    encounter_class=encounter_class.value,
                    status=StatusChoices.entered_in_error.value,
                )
                self.consent.hi_types = [hi_type]

                bundle = _care_context_fhir_bundle(
                    reference, self.consent, self.last_month
                )

                composition = bundle.entry[0].resource
                self.assertEqual(composition.status, "entered-in-error")
                # the clinical content stays
                self.assertTrue(composition.section)
                [encounter] = [
                    entry.resource
                    for entry in bundle.entry
                    if entry.resource.resource_type == "Encounter"
                ]
                self.assertEqual(encounter.status, "entered-in-error")

    def test_prescription_leaves_out_requests_of_opted_out_encounters(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        organization = self.create_facility_organization(facility)
        patient = self.create_patient()
        shared, kept_out = (
            baker.make(
                MedicationRequest,
                patient=patient,
                encounter=self.create_encounter(
                    patient, facility, organization, extensions=extensions
                ),
                status=MedicationRequestStatus.active.value,
                do_not_perform=False,
            )
            for extensions in ({}, OPTED_OUT)
        )
        reference = {
            "patientReference": str(patient.external_id),
            "careContextReference": "v2::prescription::"
            f"{timezone.localtime(shared.created_date).date()}",
        }
        self.consent.hi_types = [HealthInformationType.PRESCRIPTION]

        with patch("abdm.service.v3.gateway.Fhir") as fhir:
            _care_context_fhir_bundle(reference, self.consent, self.last_month)

            fhir.return_value.create_prescription_record.assert_called_once_with(
                [shared]
            )

            # a day on which only opted-out encounters prescribed has nothing
            shared.delete()
            fhir.reset_mock()
            self.assertIsNone(
                _care_context_fhir_bundle(reference, self.consent, self.last_month)
            )
            fhir.return_value.create_prescription_record.assert_not_called()

        self.assertFalse(kept_out.deleted)

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

    def test_immunization_is_shared_only_while_it_is_shareable(self):
        user = self.create_super_user()
        facility = self.create_facility(user)
        patient = self.create_patient(name="Vaccine Patient", gender="female")
        encounter = self.create_encounter(
            patient, facility, self.create_facility_organization(facility)
        )
        immunization = baker.make(
            Immunization,
            patient=patient,
            encounter=encounter,
            facility=facility,
            created_by=user,
            status=ImmunizationStatus.completed.value,
            code={"system": "http://snomed.info/sct", "code": "1119305005"},
            primary_source=True,
        )
        reference = {
            "patientReference": str(patient.external_id),
            "careContextReference": f"v2::immunization::{immunization.external_id}",
        }
        self.consent.hi_types = [HealthInformationType.IMMUNIZATION_RECORD]

        bundle = _care_context_fhir_bundle(reference, self.consent, self.last_month)
        self.assertEqual(bundle.entry[0].resource.type.coding[0].code, "41000179103")

        self.consent.hi_types = [HealthInformationType.OP_CONSULTATION]
        self.assertIsNone(
            _care_context_fhir_bundle(reference, self.consent, self.last_month)
        )

        self.consent.hi_types = [HealthInformationType.IMMUNIZATION_RECORD]
        for status in (ImmunizationStatus.draft, ImmunizationStatus.entered_in_error):
            Immunization.objects.filter(pk=immunization.pk).update(status=status.value)
            with self.subTest(status=status):
                self.assertIsNone(
                    _care_context_fhir_bundle(reference, self.consent, self.last_month)
                )
