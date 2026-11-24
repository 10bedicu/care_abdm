from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import requests
from abdm.api.v3.serializers.hip import HipTokenOnGenerateTokenSerializer
from abdm.models import (
    AbhaAuth,
    AbhaAuthInvalidationReason,
    AbhaAuthMethod,
    AbhaNumber,
    CallbackStatus,
    CallbackType,
    CareContext,
    CareContextStatus,
    CareContextVia,
    HealthFacility,
    HealthInformationType,
    InboundCallback,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from abdm.service.abha import latest_valid_auth
from abdm.service.helper import uuid
from abdm.service.linking import (
    discoverable_care_contexts,
    is_linking_opted_out,
    link_care_contexts,
)
from abdm.service.v3.callback_handlers import CallbackProcessingError
from abdm.service.v3.callback_handlers.hip import (
    handle_link_care_context_confirm,
    handle_link_care_context_init,
    handle_link_on_carecontext,
    handle_patient_care_context_discover,
    handle_patient_share,
    handle_patients_sms_on_notify,
    handle_token_on_generate_token,
)
from abdm.service.v3.gateway import GatewayService
from abdm.settings import plugin_settings as abdm_settings
from abdm.tasks.retry_failed_care_contexts import retry_failed_care_contexts
from abdm.utils import token as token_utils
from abdm.utils import user as abdm_user
from abdm.utils.callback import without_personal_data
from django.core.cache import cache
from django.utils import timezone
from model_bakery import baker

from care.emr.models.charge_item import ChargeItem
from care.emr.models.diagnostic_report import DiagnosticReport
from care.emr.models.encounter import Encounter
from care.emr.models.invoice import Invoice
from care.emr.models.medication_request import MedicationRequest
from care.emr.resources.diagnostic_report.spec import DiagnosticReportStatusChoices
from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.emr.resources.invoice.spec import InvoiceStatusOptions
from care.emr.resources.medication.request.spec import MedicationRequestStatus
from care.utils.tests.base import CareAPITestBase

GENERATE_TOKEN = "/v3/token/generate-token"
LINK_CARE_CONTEXT = "/hip/v3/link/carecontext"
LINK_CONTEXT_NOTIFY = "/hip/v3/link/context/notify"
SMS_NOTIFY = "/hip/v3/link/patient/links/sms/notify2"
OPTED_OUT = {"abdm": {"link_records": False}}
ALREADY_LINKED = {
    "code": "ABDM-1056: ",
    "message": "This care contexts has been ALREADY linked",
}
DUPLICATE_LINK_REQUEST = {"code": "ABDM-9999", "message": "Duplicate HIP link request"}
ON_DISCOVER = "/user-initiated-linking/v3/patient/care-context/on-discover"
ON_INIT = "/user-initiated-linking/v3/link/care-context/on-init"
ON_CONFIRM = "/user-initiated-linking/v3/link/care-context/on-confirm"


class LinkingTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        # the token request throttle and the cached ABDM user outlive a test
        cache.clear()
        abdm_user.ABDM_USER = None
        token_utils.SCAN_AND_SHARE_TOKEN_CATEGORY.clear()

        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.health_facility = baker.make(
            HealthFacility, facility=self.facility, hf_id="HIP_A"
        )
        self.organization = self.create_facility_organization(self.facility)
        self.patient = self.create_patient(name="Ravi Kumar")
        self.abha_number = baker.make(
            AbhaNumber,
            patient=self.patient,
            abha_number="91-1111-2222-3333",
            name="Ravi Kumar",
            gender="M",
            date_of_birth="1990-01-15",
        )
        self.encounter = self.completed_encounter()
        self.reference = f"v2::encounter::{self.encounter.external_id}"

        self.gateway = MagicMock()
        self.gateway.post.return_value.status_code = 202
        gateway = patch.object(GatewayService, "request", self.gateway)
        gateway.start()
        self.addCleanup(gateway.stop)

    def completed_encounter(self, patient=None, facility=None, **kwargs):
        return self.create_encounter(
            patient or self.patient,
            facility or self.facility,
            self.organization,
            status=StatusChoices.completed.value,
            **kwargs,
        )

    def auth(self, abha_address="ravi@sbx", health_facility=None, **kwargs):
        return baker.make(
            AbhaAuth,
            abha_number=self.abha_number,
            health_facility=health_facility or self.health_facility,
            abha_address=abha_address,
            method=AbhaAuthMethod.SCAN_AND_SHARE,
            **kwargs,
        )

    def auth_with_token(self, **kwargs):
        return self.auth(
            link_token="link-token",
            link_token_expires_at=timezone.now() + timedelta(days=90),
            **kwargs,
        )

    def posts(self, path):
        return [call for call in self.gateway.post.call_args_list if call.args[0] == path]

    def linked_references(self, link_request):
        return [
            care_context["referenceNumber"]
            for group in link_request.args[1]["patient"]
            for care_context in group["careContexts"]
        ]

    def refuse_links(self, error_for):
        """Answer link requests with the error ``error_for`` gives for their care
        contexts, and accept them when it gives none."""

        def post(path, payload=None, **kwargs):
            response = MagicMock(status_code=202)
            if path == LINK_CARE_CONTEXT:
                error = error_for(
                    [
                        care_context["referenceNumber"]
                        for group in payload["patient"]
                        for care_context in group["careContexts"]
                    ]
                )
                if error:
                    response = MagicMock(status_code=400)
                    response.json.return_value = {"error": error}
            return response

        self.gateway.post.side_effect = post

    def link_callback(self, request_id, error=None):
        handle_link_on_carecontext(
            {
                "abhaAddress": "ravi@sbx",
                **({"error": error} if error else {}),
                "response": {"requestId": request_id},
            },
            {},
        )

    def medication_request(self, encounter=None, status=None, **kwargs):
        return baker.make(
            MedicationRequest,
            patient=self.patient,
            encounter=encounter or self.encounter,
            status=status or MedicationRequestStatus.active.value,
            do_not_perform=False,
            **kwargs,
        )

    def ledger_row(self, reference, hi_type, status=CareContextStatus.LINKED, **kwargs):
        return baker.make(
            CareContext,
            health_facility=self.health_facility,
            patient=self.patient,
            reference=reference,
            hi_type=hi_type,
            status=status,
            **{"abha_address": "ravi@sbx", **kwargs},
        )

    def receive_link_token(self, **payload):
        serializer = HipTokenOnGenerateTokenSerializer(
            data={"response": {"requestId": uuid()}, **payload}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        handle_token_on_generate_token(serializer.validated_data, {"X-HIP-ID": "HIP_A"})


class LinkCareContextsTests(LinkingTestBase):
    def test_without_an_auth_at_the_facility_nothing_is_linked(self):
        elsewhere = baker.make(
            HealthFacility, facility=self.create_facility(self.user), hf_id="HIP_B"
        )
        self.auth_with_token(health_facility=elsewhere)

        with patch("abdm.service.linking.notify_patient_without_auth") as notify:
            link_care_contexts(self.patient, self.health_facility)

        notify.assert_called_once_with(self.patient, self.health_facility)
        self.assertFalse(CareContext.objects.exists())
        self.gateway.post.assert_not_called()

    def test_care_context_goes_from_pending_to_requested_to_linked(self):
        auth = self.auth()

        link_care_contexts(self.patient, self.health_facility)
        link_care_contexts(self.patient, self.health_facility)

        # no token yet: one is asked for, once, and the care context waits
        row = CareContext.objects.get()
        self.assertEqual(row.status, CareContextStatus.PENDING)
        self.assertEqual(row.reference, self.reference)
        self.assertEqual(row.abha_auth, auth)
        self.assertEqual(row.abha_address, "ravi@sbx")
        self.assertEqual(row.via, CareContextVia.HIP_INITIATED)
        [token_request] = self.posts(GENERATE_TOKEN)
        self.assertEqual(token_request.kwargs["headers"]["X-HIP-ID"], "HIP_A")
        self.assertEqual(token_request.args[1]["abhaAddress"], "ravi@sbx")
        self.assertEqual(token_request.args[1]["abhaNumber"], "91111122223333")
        self.assertEqual(self.posts(LINK_CARE_CONTEXT), [])

        expiry = datetime(2027, 3, 1, tzinfo=UTC)
        link_token = jwt.encode({"exp": int(expiry.timestamp())}, "key", "HS256")
        self.receive_link_token(abhaAddress="Ravi@sbx", linkToken=link_token)

        auth.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(auth.link_token, link_token)
        self.assertEqual(auth.link_token_expires_at, expiry)
        self.assertEqual(row.status, CareContextStatus.REQUESTED)
        [link_request] = self.posts(LINK_CARE_CONTEXT)
        headers = link_request.kwargs["headers"]
        self.assertEqual(headers["X-LINK-TOKEN"], link_token)
        self.assertEqual(headers["X-HIP-ID"], "HIP_A")
        self.assertEqual(headers["REQUEST-ID"], row.request_id)
        self.assertEqual(link_request.args[1]["abhaAddress"], "ravi@sbx")
        [linked] = link_request.args[1]["patient"]
        self.assertEqual(linked["referenceNumber"], str(self.patient.external_id))
        self.assertEqual(linked["hiType"], row.hi_type)
        self.assertEqual(linked["careContexts"][0]["referenceNumber"], self.reference)

        audit = Transaction.objects.get(reference_id=row.request_id)
        self.assertEqual(audit.type, TransactionType.LINK_CARE_CONTEXT)
        self.assertEqual(audit.status, TransactionStatus.INITIATED)
        self.assertEqual(audit.created_by, abdm_user.get_or_create_abdm_user())
        self.assertEqual(
            audit.meta_data,
            {
                "abha_number": str(self.abha_number.external_id),
                "type": "hip_initiated_linking",
                "care_contexts": [self.reference],
                "hf_id": "HIP_A",
            },
        )

        for _ in range(2):  # ABDM repeats callbacks
            handle_link_on_carecontext(
                {"abhaAddress": "ravi@sbx", "response": {"requestId": row.request_id}},
                {},
            )

            row.refresh_from_db()
            audit.refresh_from_db()
            self.assertEqual(row.status, CareContextStatus.LINKED)
            self.assertEqual(audit.status, TransactionStatus.COMPLETED)

    def test_reference_already_in_the_ledger_is_not_sent_again(self):
        self.auth_with_token()
        link_care_contexts(self.patient, self.health_facility, user=self.user)
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 1)
        self.assertEqual(Transaction.objects.get().created_by, self.user)

        # requested, linked, and under a newer auth for a different address
        link_care_contexts(self.patient, self.health_facility)
        CareContext.objects.update(status=CareContextStatus.LINKED)
        link_care_contexts(self.patient, self.health_facility)
        self.auth_with_token(abha_address="second@sbx")
        link_care_contexts(self.patient, self.health_facility)

        self.assertEqual(CareContext.objects.count(), 1)
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 1)

        # a record that becomes ready later goes to the address now in use
        later = self.completed_encounter()
        link_care_contexts(self.patient, self.health_facility)

        self.assertEqual(
            CareContext.objects.get(
                reference=f"v2::encounter::{later.external_id}"
            ).abha_address,
            "second@sbx",
        )
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 2)

    def test_only_records_of_the_patient_at_the_facility_are_linked(self):
        self.auth_with_token()
        other_patient = self.create_patient()
        other_facility = self.create_facility(self.user)
        care_contexts = [
            {
                "reference": f"v2::encounter::{encounter.external_id}",
                "display": "Encounter",
                "hi_type": HealthInformationType.OP_CONSULTATION,
            }
            for encounter in (
                self.encounter,
                self.completed_encounter(patient=other_patient),
                self.completed_encounter(facility=other_facility),
            )
        ]

        link_care_contexts(self.patient, self.health_facility, care_contexts)

        self.assertEqual(CareContext.objects.get().reference, self.reference)

    def test_link_refused_for_its_token_gets_a_new_token(self):
        auth = self.auth_with_token()
        refused = MagicMock(status_code=400)
        refused.json.return_value = {
            "error": {"code": "ABDM-1026", "message": "Invalid Link Token"}
        }
        self.gateway.post.side_effect = lambda path, *args, **kwargs: (
            refused if path == LINK_CARE_CONTEXT else MagicMock(status_code=202)
        )

        link_care_contexts(self.patient, self.health_facility)

        auth.refresh_from_db()
        row = CareContext.objects.get()
        self.assertIsNone(auth.link_token)
        self.assertIsNone(auth.invalidated_at)
        self.assertEqual(row.status, CareContextStatus.PENDING)
        self.assertIsNone(row.request_id)
        self.assertEqual(len(self.posts(GENERATE_TOKEN)), 1)
        self.assertEqual(Transaction.objects.get().status, TransactionStatus.FAILED)

    def test_refused_link_token_request_invalidates_the_auths_for_that_address(self):
        other_address = self.auth(abha_address="second@sbx")
        earlier, auth = self.auth(), self.auth()
        link_care_contexts(self.patient, self.health_facility)

        self.receive_link_token(
            abhaAddress="ravi@sbx",
            error={"code": "ABDM-1207", "message": "Demographic details mismatch"},
        )

        for refused in (earlier, auth):
            refused.refresh_from_db()
            self.assertEqual(
                refused.invalidation_reason,
                AbhaAuthInvalidationReason.LINK_TOKEN_REFUSED,
            )
        other_address.refresh_from_db()
        self.assertIsNone(other_address.invalidated_at)
        # the care context is free to be linked once the patient verifies again
        self.assertEqual(CareContext.objects.get().status, CareContextStatus.FAILED)
        self.assertEqual(self.posts(LINK_CARE_CONTEXT), [])

        with self.assertRaises(CallbackProcessingError):
            self.receive_link_token(abhaAddress="unknown@sbx", linkToken="link-token")

    def test_failed_link_is_sent_again_by_the_nightly_retry(self):
        self.auth_with_token()
        link_care_contexts(self.patient, self.health_facility)
        row = CareContext.objects.get()
        first_request = row.request_id

        handle_link_on_carecontext(
            {
                "abhaAddress": "ravi@sbx",
                "error": {"code": "ABDM-9999", "message": "Unable to process"},
                "response": {"requestId": first_request},
            },
            {},
        )

        row.refresh_from_db()
        self.assertEqual(row.status, CareContextStatus.FAILED)
        self.assertEqual(
            Transaction.objects.get(reference_id=first_request).status,
            TransactionStatus.FAILED,
        )

        retry_failed_care_contexts()

        row.refresh_from_db()
        self.assertEqual(row.status, CareContextStatus.REQUESTED)
        self.assertNotEqual(row.request_id, first_request)
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 2)

    def test_care_contexts_are_sent_twenty_to_a_request(self):
        auth = self.auth_with_token()
        references = [f"v2::file_upload::{uuid()}" for _ in range(21)]
        for reference in references:
            baker.make(
                CareContext,
                health_facility=self.health_facility,
                patient=self.patient,
                abha_auth=auth,
                abha_address=auth.abha_address,
                reference=reference,
                hi_type=HealthInformationType.RECORD_ARTIFACT,
            )

        with patch(
            "abdm.service.linking.care_context_dict_from_reference_id",
            side_effect=lambda reference: {
                "reference": reference,
                "display": "File",
                "hi_type": HealthInformationType.RECORD_ARTIFACT,
            },
        ):
            retry_failed_care_contexts()

        requests = self.posts(LINK_CARE_CONTEXT)
        self.assertEqual(
            [request.args[1]["patient"][0]["count"] for request in requests], [20, 1]
        )
        request_ids = {request.kwargs["headers"]["REQUEST-ID"] for request in requests}
        self.assertEqual(len(request_ids), 2)
        self.assertEqual(
            set(CareContext.objects.values_list("request_id", flat=True)), request_ids
        )

    def test_scan_and_share_links_what_was_waiting_for_an_auth(self):
        link_care_contexts(self.patient, self.health_facility)
        self.assertFalse(CareContext.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):
            handle_patient_share(
                {
                    "metaData": {"hipId": "HIP_A", "context": "counter"},
                    "profile": {
                        "patient": {
                            "abhaAddress": "ravi@sbx",
                            "abhaNumber": "91-1111-2222-3333",
                            "name": "Ravi Kumar",
                            "gender": "M",
                            "yearOfBirth": 1990,
                            "monthOfBirth": 1,
                            "dayOfBirth": 15,
                            "phoneNumber": "9999999998",
                            "address": {"line": "1 Road", "pinCode": "682001"},
                        }
                    },
                },
                {"REQUEST-ID": uuid()},
            )

        row = CareContext.objects.get()
        self.assertEqual(row.reference, self.reference)
        self.assertEqual(
            row.abha_auth, latest_valid_auth(self.abha_number, self.health_facility)
        )
        self.assertEqual(len(self.posts(GENERATE_TOKEN)), 1)


class AlreadyLinkedTests(LinkingTestBase):
    def test_already_linked_on_the_callback_is_linked_and_not_asked_for_again(self):
        self.auth_with_token()
        link_care_contexts(self.patient, self.health_facility)
        row = CareContext.objects.get()

        for _ in range(2):  # ABDM repeats callbacks
            self.link_callback(row.request_id, ALREADY_LINKED)

            row.refresh_from_db()
            self.assertEqual(row.status, CareContextStatus.LINKED)
            self.assertEqual(
                Transaction.objects.get().status, TransactionStatus.COMPLETED
            )

        retry_failed_care_contexts()
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 1)

    def test_other_message_of_the_already_linked_code_is_a_failure(self):
        self.auth_with_token()
        link_care_contexts(self.patient, self.health_facility)
        row = CareContext.objects.get()

        self.link_callback(
            row.request_id,
            {"code": "ABDM-1056", "message": "Invalid Link Reference Number"},
        )

        row.refresh_from_db()
        self.assertEqual(row.status, CareContextStatus.FAILED)

    def test_duplicate_link_request_for_one_care_context_is_linked(self):
        self.auth_with_token()
        self.refuse_links(lambda references: DUPLICATE_LINK_REQUEST)

        link_care_contexts(self.patient, self.health_facility)

        row = CareContext.objects.get()
        self.assertEqual(row.status, CareContextStatus.LINKED)
        self.assertEqual(
            Transaction.objects.get(reference_id=row.request_id).status,
            TransactionStatus.COMPLETED,
        )

        # the same refusal, as ABDM's error list has it
        self.refuse_links(
            lambda references: {
                "code": "ABDM-1090",
                "message": "duplicate HIP link request",
            }
        )
        later = self.completed_encounter()
        link_care_contexts(self.patient, self.health_facility)

        self.assertEqual(
            CareContext.objects.get(
                reference=f"v2::encounter::{later.external_id}"
            ).status,
            CareContextStatus.LINKED,
        )

        retry_failed_care_contexts()
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 2)

    def test_duplicate_link_request_for_several_is_sent_one_at_a_time(self):
        self.auth_with_token()
        self.completed_encounter()
        self.completed_encounter()
        self.refuse_links(
            lambda references: (
                DUPLICATE_LINK_REQUEST if self.reference in references else None
            )
        )

        link_care_contexts(self.patient, self.health_facility)

        link_requests = self.posts(LINK_CARE_CONTEXT)
        self.assertEqual(
            [len(self.linked_references(request)) for request in link_requests],
            [3, 1, 1, 1],
        )
        request_ids = [r.kwargs["headers"]["REQUEST-ID"] for r in link_requests]
        self.assertEqual(len(set(request_ids)), 4)
        self.assertEqual(
            Transaction.objects.get(reference_id=request_ids[0]).status,
            TransactionStatus.FAILED,
        )
        statuses = dict(CareContext.objects.values_list("reference", "status"))
        self.assertEqual(statuses.pop(self.reference), CareContextStatus.LINKED)
        self.assertEqual(set(statuses.values()), {CareContextStatus.REQUESTED})

        # nothing is left to go round again
        retry_failed_care_contexts()
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 4)

    def test_already_linked_callback_for_several_asks_for_each_on_its_own(self):
        self.auth_with_token()
        other = f"v2::encounter::{self.completed_encounter().external_id}"
        link_care_contexts(self.patient, self.health_facility)
        [together] = self.posts(LINK_CARE_CONTEXT)
        first_request = together.kwargs["headers"]["REQUEST-ID"]

        for _ in range(2):  # ABDM repeats callbacks
            self.link_callback(first_request, ALREADY_LINKED)

        self.assertEqual(
            [
                len(self.linked_references(request))
                for request in self.posts(LINK_CARE_CONTEXT)
            ],
            [2, 1, 1],
        )
        rows = {row.reference: row for row in CareContext.objects.all()}
        self.assertEqual(
            {row.status for row in rows.values()}, {CareContextStatus.REQUESTED}
        )
        self.assertNotIn(first_request, {row.request_id for row in rows.values()})
        self.assertEqual(
            Transaction.objects.get(reference_id=first_request).status,
            TransactionStatus.FAILED,
        )

        self.link_callback(rows[self.reference].request_id, ALREADY_LINKED)
        self.link_callback(rows[other].request_id)

        self.assertEqual(
            set(CareContext.objects.values_list("status", flat=True)),
            {CareContextStatus.LINKED},
        )
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 3)

    def test_duplicate_answer_to_a_request_sent_again_waits_for_its_callback(self):
        self.auth_with_token()
        self.gateway.post.side_effect = requests.ConnectionError("gateway down")
        with self.assertRaises(requests.ConnectionError):
            link_care_contexts(self.patient, self.health_facility)
        row = CareContext.objects.get()
        self.assertEqual(row.status, CareContextStatus.PENDING)

        # the first attempt may have reached ABDM after all
        self.refuse_links(lambda references: DUPLICATE_LINK_REQUEST)
        link_care_contexts(self.patient, self.health_facility)

        [first, again] = self.posts(LINK_CARE_CONTEXT)
        self.assertEqual(
            again.kwargs["headers"]["REQUEST-ID"], first.kwargs["headers"]["REQUEST-ID"]
        )
        row.refresh_from_db()
        self.assertEqual(row.status, CareContextStatus.REQUESTED)
        self.assertEqual(Transaction.objects.get().status, TransactionStatus.INITIATED)


class OptOutTests(LinkingTestBase):
    def care_context(self, reference, hi_type=HealthInformationType.OP_CONSULTATION):
        return {"reference": reference, "display": "Record", "hi_type": hi_type}

    def linked_references_in_ledger(self):
        return set(CareContext.objects.values_list("reference", flat=True))

    def test_opted_out_encounter_is_never_linked_or_offered(self):
        self.auth_with_token()
        opted_out = self.completed_encounter(extensions=OPTED_OUT)
        report = baker.make(
            DiagnosticReport,
            patient=self.patient,
            encounter=opted_out,
            facility=self.facility,
            service_request=self.create_service_request(
                self.patient, self.facility, opted_out
            ),
            status=DiagnosticReportStatusChoices.final.value,
        )
        kept_out = [
            f"v2::encounter::{opted_out.external_id}",
            f"v2::diagnostic_report::{report.external_id}",
        ]
        self.assertTrue(is_linking_opted_out(opted_out))
        self.assertFalse(is_linking_opted_out(self.encounter))

        self.assertEqual(
            [
                care_context["reference"]
                for care_context in discoverable_care_contexts(
                    self.patient, self.health_facility, "ravi@sbx"
                )
            ],
            [self.reference],
        )

        link_care_contexts(self.patient, self.health_facility)
        link_care_contexts(
            self.patient,
            self.health_facility,
            [self.care_context(reference) for reference in kept_out],
        )

        self.assertEqual(self.linked_references_in_ledger(), {self.reference})

    def test_prescription_is_linked_while_one_of_its_encounters_is_not_opted_out(self):
        self.auth_with_token()
        opted_out = self.completed_encounter(extensions=OPTED_OUT)
        request = self.medication_request(encounter=opted_out)
        prescription = self.care_context(
            f"v2::prescription::{request.created_date.date()}",
            HealthInformationType.PRESCRIPTION,
        )

        link_care_contexts(self.patient, self.health_facility, [prescription])
        link_care_contexts(self.patient, self.health_facility)

        self.assertEqual(self.linked_references_in_ledger(), {self.reference})

        # the same day, at a visit that was not opted out
        self.medication_request()
        link_care_contexts(self.patient, self.health_facility, [prescription])

        self.assertEqual(
            self.linked_references_in_ledger(),
            {self.reference, prescription["reference"]},
        )

    def test_invoice_with_a_charge_of_an_opted_out_encounter_is_not_linked(self):
        self.auth_with_token()
        opted_out = self.completed_encounter(extensions=OPTED_OUT)
        account = baker.make(
            "emr.Account",
            facility=self.facility,
            patient=self.patient,
            primary_encounter=self.encounter,
        )

        def invoice(charged_to):
            charge_item = baker.make(
                ChargeItem,
                facility=self.facility,
                patient=self.patient,
                account=account,
                encounter=charged_to,
            )
            return baker.make(
                Invoice,
                facility=self.facility,
                patient=self.patient,
                account=account,
                status=InvoiceStatusOptions.issued.value,
                charge_items=[charge_item.id],
            )

        mixed, own = invoice(opted_out), invoice(self.encounter)

        link_care_contexts(self.patient, self.health_facility)
        link_care_contexts(
            self.patient,
            self.health_facility,
            [
                self.care_context(
                    f"v2::invoice::{mixed.external_id}", HealthInformationType.INVOICE
                )
            ],
        )

        self.assertEqual(
            self.linked_references_in_ledger(),
            {self.reference, f"v2::invoice::{own.external_id}"},
        )


class DeepLinkSmsTests(LinkingTestBase):
    def setUp(self):
        super().setUp()
        self.patient.phone_number = "+919876543210"
        self.patient.save(update_fields=["phone_number"])

    def link(self):
        link_care_contexts(self.patient, self.health_facility)

    def test_sms_is_sent_once_a_day_to_a_patient_without_an_auth_here(self):
        self.link()

        [sms] = self.posts(SMS_NOTIFY)
        self.assertEqual(
            sms.args[1],
            {
                "notification": {
                    "phoneNo": "9876543210",
                    "hip": {"id": "HIP_A", "name": self.facility.name},
                }
            },
        )
        self.assertEqual(
            set(sms.kwargs["headers"]), {"REQUEST-ID", "TIMESTAMP", "X-CM-ID"}
        )
        self.assertFalse(CareContext.objects.exists())

        # a second record that day, and the first one again
        self.completed_encounter()
        self.link()
        self.link()
        self.assertEqual(len(self.posts(SMS_NOTIFY)), 1)

        tomorrow = timezone.localdate() + timedelta(days=1)
        with patch("abdm.service.linking.timezone.localdate", return_value=tomorrow):
            self.link()
            self.link()
        self.assertEqual(len(self.posts(SMS_NOTIFY)), 2)

    def test_sms_is_not_sent_when_the_setting_is_off(self):
        with patch.object(abdm_settings, "ABDM_DEEP_LINK_SMS_ENABLED", new=False):
            self.link()

        self.assertEqual(self.posts(SMS_NOTIFY), [])

    def test_sms_is_not_sent_for_an_opted_out_encounter(self):
        Encounter.objects.filter(pk=self.encounter.pk).update(extensions=OPTED_OUT)

        self.link()
        link_care_contexts(
            self.patient,
            self.health_facility,
            [
                {
                    "reference": self.reference,
                    "display": "Encounter",
                    "hi_type": HealthInformationType.OP_CONSULTATION,
                }
            ],
        )

        self.assertEqual(self.posts(SMS_NOTIFY), [])

    def test_sms_is_not_sent_without_a_mobile_number(self):
        for phone_number in ("", "12345", "+914842345678", "+14155550123"):
            with self.subTest(phone_number=phone_number):
                self.patient.phone_number = phone_number
                self.patient.save(update_fields=["phone_number"])

                self.link()

                self.assertEqual(self.posts(SMS_NOTIFY), [])

    def test_sms_is_not_sent_to_a_patient_with_an_auth_here(self):
        self.auth_with_token()

        self.link()

        self.assertEqual(self.posts(SMS_NOTIFY), [])
        self.assertEqual(len(self.posts(LINK_CARE_CONTEXT)), 1)

    def test_sms_that_cannot_be_sent_does_not_fail_linking_and_is_not_repeated(self):
        refused = MagicMock(status_code=400)
        refused.json.return_value = {
            "error": {"code": "ABDM-1006", "message": "Bad Request"}
        }
        for failure in (requests.ConnectionError("gateway down"), [refused]):
            with self.subTest(failure=failure):
                cache.clear()
                self.gateway.post.reset_mock()
                self.gateway.post.side_effect = failure

                with self.assertLogs("abdm.service.linking", level="WARNING") as logs:
                    self.link()
                self.assertNotIn("9876543210", "\n".join(logs.output))

                self.gateway.post.side_effect = None
                self.link()
                self.assertEqual(len(self.posts(SMS_NOTIFY)), 1)

    def test_sms_outcome_is_logged_without_personal_data(self):
        self.link()
        [sms] = self.posts(SMS_NOTIFY)
        response = {"requestId": sms.kwargs["headers"]["REQUEST-ID"]}

        with self.assertLogs("abdm.service.linking", level="WARNING") as logs:
            for _ in range(2):  # ABDM repeats callbacks
                handle_patients_sms_on_notify(
                    {
                        "acknowledgement": {"status": "ERRORED"},
                        "error": {
                            "code": "ABDM-1024",
                            "message": "Dependent service unavailable for 9876543210",
                        },
                        "response": response,
                    },
                    {},
                )
        output = "\n".join(logs.output)
        self.assertIn(str(self.patient.external_id), output)
        self.assertIn("ABDM-1024", output)
        self.assertNotIn("9876543210", output)

        with self.assertNoLogs("abdm.service.linking", level="WARNING"):
            handle_patients_sms_on_notify(
                {"acknowledgement": {"status": "SUCCESS"}, "response": response}, {}
            )
            # a body of a shape ABDM's page does not describe
            handle_patients_sms_on_notify({}, {})


class LinkedCareContextChangeTests(LinkingTestBase):
    def setUp(self):
        super().setUp()
        Encounter.objects.filter(pk=self.encounter.pk).update(
            encounter_class=ClassChoices.amb.value
        )
        self.encounter.refresh_from_db()

    def save(self, instance, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            for field, value in fields.items():
                setattr(instance, field, value)
            instance.save()

    def prescription_of(self, medication_request):
        return f"v2::prescription::{medication_request.created_date.date()}"

    def test_medication_request_added_to_a_linked_prescription_is_notified(self):
        prescription = self.prescription_of(self.medication_request())
        self.ledger_row(prescription, HealthInformationType.PRESCRIPTION)

        with self.captureOnCommitCallbacks(execute=True):
            self.medication_request(status=MedicationRequestStatus.draft.value)
        self.assertEqual(self.posts(LINK_CONTEXT_NOTIFY), [])

        with self.captureOnCommitCallbacks(execute=True):
            self.medication_request()
            # written in one go, they are one change
            self.medication_request()

        [notify] = self.posts(LINK_CONTEXT_NOTIFY)
        notification = notify.args[1]["notification"]
        self.assertEqual(notification["patient"], {"id": "ravi@sbx"})
        self.assertEqual(
            notification["careContext"],
            {
                "patientReference": str(self.patient.external_id),
                "careContextReference": prescription,
            },
        )
        self.assertEqual(notification["hiTypes"], [HealthInformationType.PRESCRIPTION])
        self.assertEqual(notification["hip"], {"id": "HIP_A"})
        self.assertIn("date", notification)
        self.assertEqual(notify.kwargs["headers"]["X-HIP-ID"], "HIP_A")
        self.assertEqual(self.posts(LINK_CARE_CONTEXT), [])

    def test_prescription_linked_by_another_encounter_is_notified(self):
        prescription = self.prescription_of(self.medication_request())
        self.ledger_row(prescription, HealthInformationType.PRESCRIPTION)
        # linked from the patient's PHR app as well, under another address
        self.ledger_row(
            prescription,
            HealthInformationType.PRESCRIPTION,
            abha_address="second@sbx",
            via=CareContextVia.USER_INITIATED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.medication_request(encounter=self.completed_encounter())

        self.assertEqual(
            {
                notify.args[1]["notification"]["patient"]["id"]
                for notify in self.posts(LINK_CONTEXT_NOTIFY)
            },
            {"ravi@sbx", "second@sbx"},
        )

    def test_prescription_change_that_is_not_shared_is_not_notified(self):
        prescription = self.prescription_of(self.medication_request())
        row = self.ledger_row(
            prescription,
            HealthInformationType.PRESCRIPTION,
            status=CareContextStatus.REQUESTED,
        )

        # not linked yet: the link will carry it
        with self.captureOnCommitCallbacks(execute=True):
            self.medication_request()

        CareContext.objects.filter(pk=row.pk).update(status=CareContextStatus.LINKED)
        with self.captureOnCommitCallbacks(execute=True):
            self.medication_request(
                encounter=self.completed_encounter(extensions=OPTED_OUT)
            )

        self.assertEqual(self.posts(LINK_CONTEXT_NOTIFY), [])

    def test_linked_encounter_that_changes_record_type_is_notified(self):
        auth = self.auth_with_token()
        row = self.ledger_row(
            self.reference, HealthInformationType.OP_CONSULTATION, abha_auth=auth
        )

        # another class of the same record type
        self.save(self.encounter, encounter_class=ClassChoices.vr.value)
        self.assertEqual(self.posts(LINK_CONTEXT_NOTIFY), [])

        refused = MagicMock(status_code=400)
        refused.json.return_value = {
            "error": {"code": "ABDM-1006", "message": "No links found"}
        }
        self.gateway.post.side_effect = [refused]
        self.save(self.encounter, encounter_class=ClassChoices.imp.value)
        row.refresh_from_db()
        self.assertEqual(row.hi_type, HealthInformationType.OP_CONSULTATION)

        self.gateway.post.side_effect = None
        self.save(self.encounter, encounter_class=ClassChoices.amb.value)
        self.save(self.encounter, encounter_class=ClassChoices.emer.value)

        [_, notify] = self.posts(LINK_CONTEXT_NOTIFY)
        notification = notify.args[1]["notification"]
        self.assertEqual(notification["patient"], {"id": "ravi@sbx"})
        self.assertEqual(
            notification["careContext"]["careContextReference"], self.reference
        )
        self.assertEqual(
            notification["hiTypes"], [HealthInformationType.DISCHARGE_SUMMARY]
        )
        self.assertEqual(notify.kwargs["headers"]["X-HIP-ID"], "HIP_A")
        row.refresh_from_db()
        self.assertEqual(row.hi_type, HealthInformationType.DISCHARGE_SUMMARY)

        # the status of an encounter does not make it another record
        self.save(self.encounter, status=StatusChoices.entered_in_error.value)
        self.assertEqual(len(self.posts(LINK_CONTEXT_NOTIFY)), 2)

    def test_encounter_is_linked_as_the_record_type_of_its_class_when_sent(self):
        self.auth()
        link_care_contexts(self.patient, self.health_facility)
        row = CareContext.objects.get()
        self.assertEqual(row.hi_type, HealthInformationType.OP_CONSULTATION)

        # while the link waits for its token
        self.save(self.encounter, encounter_class=ClassChoices.imp.value)
        self.receive_link_token(abhaAddress="ravi@sbx", linkToken="link-token")

        [link_request] = self.posts(LINK_CARE_CONTEXT)
        self.assertEqual(
            link_request.args[1]["patient"][0]["hiType"],
            HealthInformationType.DISCHARGE_SUMMARY,
        )
        row.refresh_from_db()
        self.assertEqual(row.hi_type, HealthInformationType.DISCHARGE_SUMMARY)
        self.assertEqual(self.posts(LINK_CONTEXT_NOTIFY), [])


class OnNotifyCallbackTests(LinkingTestBase):
    def test_on_notify_callbacks_are_stored_once_and_processed(self):
        self.client.force_authenticate(user=self.user)

        for path, callback_type in (
            ("links/context/on-notify", CallbackType.LINKS_CONTEXT_ON_NOTIFY),
            ("patients/sms/on-notify", CallbackType.PATIENTS_SMS_ON_NOTIFY),
        ):
            with self.subTest(callback_type=callback_type):
                request_id = uuid()
                for _ in range(2):  # ABDM repeats callbacks
                    with (
                        self.captureOnCommitCallbacks(execute=True),
                        self.assertLogs("abdm.api.v3.viewsets.hip", "DEBUG") as logs,
                    ):
                        response = self.client.post(
                            f"/api/abdm/api/v3/{path}",
                            {
                                "acknowledgement": {"status": "SUCCESS"},
                                "response": {"requestId": uuid()},
                                "somethingNew": {"phoneNo": "9876543210"},
                            },
                            format="json",
                            headers={"REQUEST-ID": request_id},
                        )
                    self.assertEqual(response.status_code, 202)
                    # what arrived is on record, without what could name a person
                    self.assertIn("somethingNew", logs.output[0])
                    self.assertNotIn("9876543210", logs.output[0])

                callback = InboundCallback.objects.get(request_id=request_id)
                self.assertEqual(callback.callback_type, callback_type)
                self.assertEqual(callback.status, CallbackStatus.COMPLETED)

    def test_callback_body_is_logged_without_personal_data(self):
        self.assertEqual(
            without_personal_data(
                {
                    "acknowledgement": {"status": "SUCCESS"},
                    "notification": {
                        "phoneNo": "9876543210",
                        "patient": {"id": "ravi@sbx"},
                        "hip": {"id": "HIP_A", "name": "Clinic"},
                        "receivers": [{"abhaAddress": "ravi@sbx", "sent": True}],
                        # in a field that does not say what it holds
                        "detail": ["sent to +91 98765 43210", "for ravi@sbx", "ok"],
                        "date": "2024-05-09T10:34:00.387Z",
                    },
                    "response": {"requestId": "6f0b4665-a915-4c92-aa36-65afb4a2cd71"},
                }
            ),
            {
                "acknowledgement": {"status": "SUCCESS"},
                "notification": {
                    "phoneNo": "[redacted]",
                    "patient": "[redacted]",
                    "hip": {"id": "HIP_A", "name": "[redacted]"},
                    "receivers": [{"abhaAddress": "[redacted]", "sent": True}],
                    "detail": ["[redacted]", "[redacted]", "ok"],
                    "date": "2024-05-09T10:34:00.387Z",
                },
                "response": {"requestId": "6f0b4665-a915-4c92-aa36-65afb4a2cd71"},
            },
        )


class ReadinessSignalTests(LinkingTestBase):
    def setUp(self):
        super().setUp()
        enqueue = patch("abdm.signals.register_care_contexts.enqueue_link_care_context")
        self.enqueue = enqueue.start()
        self.addCleanup(enqueue.stop)
        # whether a record is linked is no longer for the signals to decide
        self.patient = self.create_patient()

    def save(self, instance, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            for field, value in fields.items():
                setattr(instance, field, value)
            instance.save()

    def linked_references(self):
        return [
            call.kwargs["care_context"]["reference"]
            for call in self.enqueue.call_args_list
        ]

    def test_encounter_is_linked_when_it_ends(self):
        with self.captureOnCommitCallbacks(execute=True):
            encounter = self.create_encounter(
                self.patient,
                self.facility,
                self.organization,
                status=StatusChoices.in_progress.value,
            )
        self.save(encounter, priority="routine")
        self.assertEqual(self.linked_references(), [])

        self.save(encounter, status=StatusChoices.completed.value)
        self.save(encounter, status=StatusChoices.discharged.value)

        self.assertEqual(
            self.linked_references(), [f"v2::encounter::{encounter.external_id}"]
        )
        self.assertEqual(self.enqueue.call_args.kwargs["hf_id"], "HIP_A")
        self.assertEqual(
            self.enqueue.call_args.kwargs["patient_external_id"],
            str(self.patient.external_id),
        )

    def test_encounter_created_already_over_is_linked(self):
        for status in (StatusChoices.completed, StatusChoices.discharged):
            with self.captureOnCommitCallbacks(execute=True):
                encounter = self.create_encounter(
                    self.patient,
                    self.facility,
                    self.organization,
                    status=status.value,
                )
            self.save(encounter, priority="routine")

            self.assertEqual(
                self.linked_references()[-1], f"v2::encounter::{encounter.external_id}"
            )
        self.assertEqual(len(self.linked_references()), 2)

    def test_diagnostic_report_created_final_is_linked(self):
        encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )
        with self.captureOnCommitCallbacks(execute=True):
            report = baker.make(
                DiagnosticReport,
                patient=self.patient,
                encounter=encounter,
                facility=self.facility,
                service_request=self.create_service_request(
                    self.patient, self.facility, encounter
                ),
                status=DiagnosticReportStatusChoices.final.value,
            )
        self.save(report, conclusion="Normal")

        self.assertEqual(
            self.linked_references(), [f"v2::diagnostic_report::{report.external_id}"]
        )

    def test_diagnostic_report_is_linked_when_it_is_final(self):
        encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )
        with self.captureOnCommitCallbacks(execute=True):
            report = baker.make(
                DiagnosticReport,
                patient=self.patient,
                encounter=encounter,
                facility=self.facility,
                service_request=self.create_service_request(
                    self.patient, self.facility, encounter
                ),
                status=DiagnosticReportStatusChoices.preliminary.value,
            )
        self.assertEqual(self.linked_references(), [])

        self.save(report, status=DiagnosticReportStatusChoices.final.value)
        self.save(report, conclusion="Normal")

        self.assertEqual(
            self.linked_references(), [f"v2::diagnostic_report::{report.external_id}"]
        )

    def test_prescription_is_linked_by_the_first_request_that_is_not_a_draft(self):
        encounter = self.create_encounter(
            self.patient, self.facility, self.organization
        )

        def medication_request(status):
            with self.captureOnCommitCallbacks(execute=True):
                return baker.make(
                    MedicationRequest,
                    patient=self.patient,
                    encounter=encounter,
                    status=status,
                    do_not_perform=False,
                )

        draft = medication_request(MedicationRequestStatus.draft.value)
        another_draft = medication_request(MedicationRequestStatus.draft.value)
        self.assertEqual(self.linked_references(), [])

        self.save(draft, status=MedicationRequestStatus.active.value)
        self.save(draft, note="After food")
        self.save(another_draft, status=MedicationRequestStatus.active.value)
        medication_request(MedicationRequestStatus.active.value)

        self.assertEqual(
            self.linked_references(),
            [f"v2::prescription::{draft.created_date.date()}"],
        )


class UserInitiatedLinkingTests(LinkingTestBase):
    def discover(self, abha_address, identifiers=()):
        handle_patient_care_context_discover(
            {
                "transactionId": uuid(),
                "patient": {
                    "id": abha_address,
                    "name": "Ravi Kumar",
                    "gender": "M",
                    "yearOfBirth": 1990,
                    "verifiedIdentifiers": list(identifiers),
                    "unverifiedIdentifiers": [],
                },
            },
            {"REQUEST-ID": uuid(), "X-HIP-ID": "HIP_A"},
        )
        payload = self.posts(ON_DISCOVER)[-1].args[1]
        return payload, {
            care_context["referenceNumber"]
            for group in payload.get("patient", [])
            for care_context in group["careContexts"]
        }

    def test_discovery_offers_ready_records_not_yet_linked_to_the_address(self):
        auth = self.auth()
        self.create_encounter(
            self.patient,
            self.facility,
            self.organization,
            status=StatusChoices.in_progress.value,
        )
        unlinked = self.completed_encounter()
        baker.make(
            CareContext,
            health_facility=self.health_facility,
            patient=self.patient,
            abha_auth=auth,
            abha_address=auth.abha_address,
            reference=self.reference,
            hi_type=HealthInformationType.OP_CONSULTATION,
            status=CareContextStatus.LINKED,
        )

        # the patient is known by the address verified here, in any letter case
        payload, offered = self.discover("Ravi@sbx")

        self.assertEqual(payload["matchedBy"], ["ABHA_NUMBER"])
        self.assertEqual(offered, {f"v2::encounter::{unlinked.external_id}"})

        # under another address of the same person nothing is linked yet
        _, offered = self.discover(
            "second@sbx", [{"type": "ABHA_NUMBER", "value": "91111122223333"}]
        )

        self.assertEqual(
            offered, {self.reference, f"v2::encounter::{unlinked.external_id}"}
        )

    def test_patient_without_abha_links_only_the_records_they_picked(self):
        patient = self.create_patient()
        picked = self.completed_encounter(patient=patient)
        self.completed_encounter(patient=patient)
        reference = f"v2::encounter::{picked.external_id}"

        handle_link_care_context_init(
            {
                "transactionId": uuid(),
                "abhaAddress": "Walkin@sbx",
                "patient": [
                    {
                        "referenceNumber": str(patient.external_id),
                        "careContexts": [{"referenceNumber": reference}],
                        "hiType": HealthInformationType.OP_CONSULTATION,
                        "count": 1,
                    }
                ],
            },
            {"REQUEST-ID": uuid()},
        )
        link_reference = self.posts(ON_INIT)[0].args[1]["link"]["referenceNumber"]

        for _ in range(2):  # ABDM repeats callbacks
            handle_link_care_context_confirm(
                {"confirmation": {"linkRefNumber": link_reference, "token": "000000"}},
                {"REQUEST-ID": uuid(), "X-HIP-ID": "HIP_A"},
            )

        row = CareContext.objects.get()
        self.assertEqual(row.reference, reference)
        self.assertEqual(row.patient, patient)
        self.assertEqual(row.abha_address, "walkin@sbx")
        self.assertEqual(row.status, CareContextStatus.LINKED)
        self.assertEqual(row.via, CareContextVia.USER_INITIATED)
        self.assertIsNone(row.abha_auth)

        # linking from the PHR app is not an auth, and writes no identity
        self.assertFalse(AbhaAuth.objects.exists())
        self.assertFalse(AbhaNumber.objects.filter(patient=patient).exists())

        confirmed = self.posts(ON_CONFIRM)[0]
        [group] = confirmed.args[1]["patient"]
        self.assertEqual(group["careContexts"][0]["referenceNumber"], reference)
        audit = Transaction.objects.get(
            reference_id=confirmed.kwargs["headers"]["REQUEST-ID"]
        )
        self.assertEqual(audit.type, TransactionType.LINK_CARE_CONTEXT)
        self.assertEqual(
            audit.meta_data,
            {
                "patient": str(patient.external_id),
                "type": "patient_initiated_linking",
                "care_contexts": [reference],
                "hf_id": "HIP_A",
            },
        )
