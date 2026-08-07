"""Guards for the two properties this migration exists to provide.

1. A care context link is queued, never made inline, so an unreachable ABDM
   gateway cannot hold a request open.
2. Nothing raised inside the on_commit callback escapes -- on_commit runs outside
   the view's exception handling, so an escaping error becomes an unhandled 500
   on a write that already committed.
"""

from unittest.mock import patch

import requests
from abdm.signals.register_care_contexts import dispatch_link_care_context
from abdm.tasks.link_care_context import MAX_RETRIES, link_care_context
from django.test import TestCase

CARE_CONTEXTS = [
    {
        "reference": "v2::invoice::7c9f1e5a",
        "display": "Invoice TR-1-26 on 2026-08-06 06:02:28",
        "hi_type": "Invoice",
    }
]

SIGNALS = "abdm.signals.register_care_contexts"
TASK = "abdm.tasks.link_care_context"
HF_ID = "IN1410000017_4"


class FakePatient:
    id = 1
    external_id = "0f1d2c3b-4a59-6879-8a9b-0c1d2e3f4a5b"


# sentinel so run_task(patient=None) can mean "patient row is gone"
MISSING = object()


class DispatchLinkCareContextTests(TestCase):
    def dispatch(self, build_care_contexts):
        """Run the receiver-side helper through a commit, return the patched task."""
        with patch(f"{SIGNALS}.link_care_context") as task:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_link_care_context(
                    FakePatient(), build_care_contexts, HF_ID, user=None
                )
                # nothing may reach the broker before the transaction commits
                task.delay.assert_not_called()
            return task

    def test_queues_after_commit_instead_of_calling_abdm_inline(self):
        task = self.dispatch(lambda: CARE_CONTEXTS)

        task.delay.assert_called_once()
        kwargs = task.delay.call_args.kwargs
        self.assertEqual(kwargs["care_contexts"], CARE_CONTEXTS)
        self.assertEqual(kwargs["patient_id"], FakePatient.id)
        self.assertEqual(kwargs["hf_id"], HF_ID)
        self.assertIsNone(kwargs["user_id"])
        self.assertTrue(kwargs["reference_id"])

    def test_care_contexts_are_built_after_commit_not_at_signal_time(self):
        # pre_save receivers depend on this: pk and modified_date are set by save()
        calls = []

        def build():
            calls.append(1)
            return CARE_CONTEXTS

        self.dispatch(build)

        self.assertEqual(len(calls), 1)

    def test_empty_care_contexts_skips_dispatch(self):
        self.dispatch(list).delay.assert_not_called()

    def test_builder_error_does_not_escape_the_request(self):
        def build():
            raise ValueError("care context could not be built")

        self.dispatch(build).delay.assert_not_called()

    def test_broker_error_does_not_escape_the_request(self):
        with patch(f"{SIGNALS}.link_care_context") as task:
            task.delay.side_effect = OSError("redis is down")

            with self.captureOnCommitCallbacks(execute=True):
                dispatch_link_care_context(
                    FakePatient(), lambda: CARE_CONTEXTS, HF_ID, user=None
                )

            task.delay.assert_called_once()


class LinkCareContextTaskTests(TestCase):
    def run_task(self, side_effect=None, retries=0, patient=MISSING):
        with (
            patch(f"{TASK}.Patient") as patient_model,
            patch(f"{TASK}.GatewayService") as gateway,
            patch(f"{TASK}._mark_failed") as mark_failed,
        ):
            patient_model.objects.filter.return_value.first.return_value = (
                FakePatient() if patient is MISSING else patient
            )
            gateway.link__carecontext.side_effect = side_effect

            link_care_context.push_request(retries=retries)
            try:
                link_care_context.run(
                    reference_id="ref-1",
                    patient_id=FakePatient.id,
                    care_contexts=CARE_CONTEXTS,
                    hf_id=HF_ID,
                )
            finally:
                link_care_context.pop_request()

            return gateway, mark_failed

    def test_abdm_rejection_is_swallowed_and_marked_failed_for_the_sweeper(self):
        gateway, mark_failed = self.run_task(ValueError("Invalid display"))

        gateway.link__carecontext.assert_called_once()
        mark_failed.assert_called_once_with("ref-1")

    def test_timeout_on_final_attempt_is_marked_failed_not_retried_forever(self):
        _, mark_failed = self.run_task(requests.ReadTimeout(), retries=MAX_RETRIES)

        mark_failed.assert_called_once_with("ref-1")

    def test_missing_patient_is_a_no_op(self):
        gateway, mark_failed = self.run_task(patient=None)

        gateway.link__carecontext.assert_not_called()
        mark_failed.assert_not_called()
