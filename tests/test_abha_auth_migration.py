from importlib import import_module
from unittest.mock import patch
from uuid import uuid4

from abdm.models import (
    AbhaAuth,
    AbhaAuthMethod,
    AbhaNumber,
    CareContext,
    CareContextStatus,
    CareContextVia,
    ConsentArtefact,
    ConsentRequest,
    PaymentOrder,
)
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from model_bakery import baker

from care.emr.models.encounter import Encounter
from care.emr.models.patient import Patient
from care.facility.models.facility import Facility
from care.users.models import User

BEFORE = [("abdm", "0010_paymentorder_status_index")]
AFTER = [("abdm", "0013_remove_health_id_and_patient_abha")]
DATA_MIGRATION = "abdm.migrations.0012_migrate_abha_addresses_to_auths"

LINK_CARE_CONTEXT = 4
INITIATED, COMPLETED, FAILED, CANCELLED = 1, 2, 3, 4


class AbhaAuthDataMigrationTests(TransactionTestCase):
    """Runs the data migration forward on rows shaped as they were before it."""

    def setUp(self):
        # whatever happens here, the test database is left at the latest migration
        self.addCleanup(self.migrate_to_latest)
        self.old = self.migrate(BEFORE)

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_model(self, name):
        return self.old.get_model("abdm", name)

    def patient_with_abha(self, health_id, encounters_at=(), **abha_number):
        # only the plugin is migrated back, so core rows use the current models
        patient = baker.make(Patient)
        for facility in encounters_at:
            baker.make(Encounter, patient=patient, facility=facility)
        return self.old_model("AbhaNumber").objects.create(
            health_id=health_id, patient_id=patient.id, **abha_number
        )

    def link_transaction(self, abha_number, status, care_contexts, **meta_data):
        return self.old_model("Transaction").objects.create(
            reference_id=str(uuid4()),
            type=LINK_CARE_CONTEXT,
            status=status,
            meta_data={
                "abha_number": str(abha_number.external_id),
                "type": "hip_initiated_linking",
                "care_contexts": care_contexts,
                **meta_data,
            },
        )

    def test_old_rows_are_carried_over_to_auths_and_the_care_context_ledger(self):  # noqa: PLR0915
        user = baker.make(User)
        facility_a, facility_b, unregistered = (
            baker.make(Facility, created_by=user) for _ in range(3)
        )
        health_facilities = self.old_model("HealthFacility").objects
        hip_a = health_facilities.create(
            hf_id="HIP_A", facility_id=facility_a.external_id
        )
        hip_b = health_facilities.create(
            hf_id="HIP_B", facility_id=facility_b.external_id
        )

        ravi = self.patient_with_abha(
            "Ravi@SBX", [facility_a, unregistered], abha_number="91-1111-2222-3333"
        )
        never_seen = self.patient_with_abha("never.seen@sbx")
        self.patient_with_abha(None, [facility_a])
        self.patient_with_abha("deleted@sbx", [facility_a], deleted=True)
        no_patient = self.old_model("AbhaNumber").objects.create(
            health_id="nopatient@sbx"
        )

        admission = Encounter.objects.get(patient_id=ravi.patient_id, facility=facility_a)
        Encounter.objects.filter(pk=admission.pk).update(encounter_class="imp")

        payment_orders = self.old_model("PaymentOrder").objects
        blank_order = payment_orders.create(
            open_order_request_id=uuid4(),
            abha_number_id=ravi.id,
            health_facility_id=hip_a.id,
        )
        addressed_order = payment_orders.create(
            open_order_request_id=uuid4(),
            abha_number_id=ravi.id,
            health_facility_id=hip_a.id,
            abha_address="presented@sbx",
        )

        consent_requests = self.old_model("ConsentRequest").objects
        consent_artefacts = self.old_model("ConsentArtefact").objects
        by_encounter = consent_requests.create(
            consent_id=uuid4(),
            patient_abha_id=ravi.health_id,
            encounter_id=admission.external_id,
        )
        by_hiu = consent_requests.create(
            consent_id=uuid4(), patient_abha_id=never_seen.health_id, hiu="HIP_B"
        )
        unresolved = consent_requests.create(
            consent_id=uuid4(), patient_abha_id=no_patient.health_id, hiu="GONE"
        )
        as_hiu = consent_artefacts.create(
            patient_abha_id=ravi.health_id,
            consent_request_id=by_encounter.consent_id,
            hip="SOME_OTHER_HIP",
        )
        as_hip = consent_artefacts.create(patient_abha_id=ravi.health_id, hip="HIP_B")
        of_unresolved = consent_artefacts.create(
            patient_abha_id=no_patient.health_id,
            consent_request_id=unresolved.consent_id,
            hip="HIP_A",
        )

        encounter_reference = f"v2::encounter::{admission.external_id}"
        file_reference = f"v2::file_upload::{uuid4()}"
        invoice_reference = f"v2::invoice::{uuid4()}"
        completed = self.link_transaction(
            ravi,
            COMPLETED,
            [encounter_reference, "v2::prescription::2026-01-01"],
            hf_id="HIP_A",
        )
        initiated = self.link_transaction(
            ravi,
            INITIATED,
            [encounter_reference, file_reference, "v1::consultation::old"],
            hf_id="HIP_A",
        )
        failed = self.link_transaction(
            never_seen, FAILED, [invoice_reference], hf_id="HIP_B"
        )
        by_patient = self.link_transaction(
            ravi,
            COMPLETED,
            ["consultation-before-versions"],
            hf_id="HIP_A",
            type="patient_initiated_linking",
        )
        self.link_transaction(ravi, CANCELLED, ["v2::invoice::a"], hf_id="HIP_A")
        self.link_transaction(ravi, COMPLETED, ["v2::invoice::b"])
        self.link_transaction(ravi, COMPLETED, ["v2::invoice::c"], hf_id="GONE")
        self.link_transaction(no_patient, COMPLETED, ["v2::invoice::d"], hf_id="HIP_A")

        # small batches, so that every table here is walked in more than one
        with patch.object(import_module(DATA_MIGRATION), "BATCH_SIZE", 2):
            self.migrate(AFTER)

        with connection.cursor() as cursor:
            columns = {
                column.name
                for table in ("abdm_abhanumber", "abdm_consentrequest")
                for column in connection.introspection.get_table_description(
                    cursor, table
                )
            }
        self.assertFalse(columns & {"health_id", "patient_abha_id"})

        # one legacy auth for each registered facility the patient has been to,
        # and for the facility a consent request or a link was made from
        auths = {
            (auth.abha_number_id, auth.health_facility_id): auth
            for auth in AbhaAuth.objects.all()
        }
        self.assertEqual(
            set(auths), {(ravi.id, hip_a.id), (never_seen.id, hip_b.id)}
        )
        ravi_auth = auths[(ravi.id, hip_a.id)]
        never_seen_auth = auths[(never_seen.id, hip_b.id)]
        self.assertEqual(ravi_auth.abha_address, "ravi@sbx")
        self.assertEqual(ravi_auth.method, AbhaAuthMethod.LEGACY)
        self.assertIsNone(ravi_auth.invalidated_at)
        self.assertEqual(
            AbhaNumber.objects.get(pk=ravi.id).abha_number, "91-1111-2222-3333"
        )

        self.assertEqual(
            PaymentOrder.objects.get(pk=blank_order.pk).abha_address, "Ravi@SBX"
        )
        self.assertEqual(
            PaymentOrder.objects.get(pk=addressed_order.pk).abha_address,
            "presented@sbx",
        )

        self.assertEqual(
            ConsentRequest.objects.get(pk=by_encounter.pk).abha_auth, ravi_auth
        )
        self.assertEqual(
            ConsentRequest.objects.get(pk=by_hiu.pk).abha_auth, never_seen_auth
        )
        self.assertIsNone(ConsentRequest.objects.get(pk=unresolved.pk).abha_auth)

        as_hiu = ConsentArtefact.objects.get(pk=as_hiu.pk)
        as_hip = ConsentArtefact.objects.get(pk=as_hip.pk)
        of_unresolved = ConsentArtefact.objects.get(pk=of_unresolved.pk)
        self.assertEqual(
            (as_hiu.abha_address, as_hiu.patient_id, as_hiu.health_facility_id),
            ("ravi@sbx", ravi.patient_id, hip_a.id),
        )
        self.assertEqual(
            (as_hip.abha_address, as_hip.patient_id, as_hip.health_facility_id),
            ("ravi@sbx", ravi.patient_id, hip_b.id),
        )
        self.assertEqual(
            (
                of_unresolved.abha_address,
                of_unresolved.patient_id,
                of_unresolved.health_facility_id,
            ),
            ("nopatient@sbx", None, None),
        )

        care_contexts = {
            care_context.reference: care_context
            for care_context in CareContext.objects.all()
        }
        self.assertEqual(
            set(care_contexts),
            {
                encounter_reference,
                "v2::prescription::2026-01-01",
                file_reference,
                invoice_reference,
                "consultation-before-versions",
            },
        )
        self.assertTrue(all(row.legacy for row in care_contexts.values()))

        # a link that completed is not reopened by a later attempt at it
        linked = care_contexts[encounter_reference]
        self.assertEqual(linked.status, CareContextStatus.LINKED)
        self.assertEqual(linked.request_id, completed.reference_id)
        self.assertEqual(linked.via, CareContextVia.HIP_INITIATED)
        self.assertEqual(linked.hi_type, "DischargeSummary")
        self.assertEqual(linked.abha_auth, ravi_auth)
        self.assertEqual(linked.abha_address, "ravi@sbx")
        self.assertEqual(linked.patient_id, ravi.patient_id)
        self.assertEqual(linked.health_facility_id, hip_a.id)
        self.assertEqual(
            care_contexts["v2::prescription::2026-01-01"].hi_type, "Prescription"
        )

        pending = care_contexts[file_reference]
        self.assertEqual(pending.status, CareContextStatus.PENDING)
        self.assertEqual(pending.request_id, initiated.reference_id)
        self.assertEqual(pending.hi_type, "HealthDocumentRecord")
        self.assertEqual(pending.abha_auth, ravi_auth)

        retried = care_contexts[invoice_reference]
        self.assertEqual(retried.status, CareContextStatus.PENDING)
        self.assertEqual(retried.request_id, failed.reference_id)
        self.assertEqual(retried.abha_auth, never_seen_auth)
        self.assertEqual(retried.patient_id, never_seen.patient_id)

        from_phr = care_contexts["consultation-before-versions"]
        self.assertEqual(from_phr.status, CareContextStatus.LINKED)
        self.assertEqual(from_phr.via, CareContextVia.USER_INITIATED)
        self.assertEqual(from_phr.request_id, by_patient.reference_id)
        self.assertEqual(from_phr.hi_type, "OPConsultation")
        self.assertIsNone(from_phr.abha_auth)
