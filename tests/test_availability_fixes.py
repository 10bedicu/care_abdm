"""Guards for the three availability fixes.

The shared property: ABDM being slow, unreachable, or rejecting us must not hold a
request open, must not multiply load on the gateway, and must not corrupt the
Transaction ledger the nightly sweep relies on.
"""

from unittest.mock import patch

import jwt
import requests
from abdm.authentication import ABDMAuthentication
from abdm.settings import plugin_settings as abdm_settings
from abdm.tasks.link_care_context import (
    RETRY_FOR as LINK_RETRY_FOR,
)
from abdm.tasks.link_care_context import (
    enqueue_link_care_context,
    link_care_context,
)
from abdm.tasks.process_inbound_callback import (
    RETRY_FOR as CALLBACK_RETRY_FOR,
)
from django.core.cache import cache
from django.test import TestCase, override_settings

CERT_URL = "https://apis.abdm.gov.in/gateway/v3/certs"
JWK = {"kty": "RSA", "kid": "test", "n": "abc", "e": "AQAB"}
CARE_CONTEXT = {
    "reference": "v2::invoice::7c9f1e5a",
    "display": "Invoice TR-1-26 on 2026-08-06 06:02:28",
    "hi_type": "Invoice",
}


# The project cache is Redis with IGNORE_EXCEPTIONS=True, which silently degrades to
# no-op when Redis is unreachable -- that would make these assertions measure the test
# environment rather than the caching logic.
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "abdm-availability-tests",
        }
    }
)
class GatewayJwkCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.auth = ABDMAuthentication()

    def test_jwk_is_fetched_once_and_reused(self):
        with patch("abdm.authentication.requests.get") as get:
            get.return_value.json.return_value = {"keys": [JWK]}

            self.assertEqual(self.auth.gateway_jwk(CERT_URL), JWK)
            self.assertEqual(self.auth.gateway_jwk(CERT_URL), JWK)

            # uncached, this ran on every inbound callback
            get.assert_called_once()

    def test_fetch_is_bounded_by_a_timeout(self):
        with patch("abdm.authentication.requests.get") as get:
            get.return_value.json.return_value = {"keys": [JWK]}

            self.auth.gateway_jwk(CERT_URL)

            self.assertEqual(
                get.call_args.kwargs["timeout"], abdm_settings.ABDM_REQUEST_TIMEOUT
            )

    def test_nothing_is_cached_when_the_gateway_errors(self):
        with patch("abdm.authentication.requests.get") as get:
            get.return_value.raise_for_status.side_effect = requests.HTTPError("503")

            with self.assertRaises(requests.HTTPError):
                self.auth.gateway_jwk(CERT_URL)

            get.return_value.raise_for_status.side_effect = None
            get.return_value.json.return_value = {"keys": [JWK]}

            self.assertEqual(self.auth.gateway_jwk(CERT_URL), JWK)

    def test_rotated_key_refetches_instead_of_failing_until_the_ttl_expires(self):
        rotated = {**JWK, "kid": "rotated"}

        with (
            patch("abdm.authentication.requests.get") as get,
            patch.object(ABDMAuthentication, "decode") as decode,
        ):
            get.return_value.json.side_effect = [{"keys": [JWK]}, {"keys": [rotated]}]
            decode.side_effect = [jwt.InvalidTokenError("stale key"), {"sub": "abdm"}]

            result = self.auth.open_id_authenticate(CERT_URL, "a.token")

            self.assertEqual(result, {"sub": "abdm"})
            self.assertEqual(get.call_count, 2)
            self.assertEqual(decode.call_args_list[1].args[1], rotated)


class RetryScopeTests(TestCase):
    def test_only_transient_failures_are_retried(self):
        # retrying an ABDM rejection multiplies load on an already-degraded gateway
        for retry_for in (LINK_RETRY_FOR, CALLBACK_RETRY_FOR):
            self.assertNotIn(Exception, retry_for)
            self.assertIn(requests.Timeout, retry_for)
            self.assertIn(requests.ConnectionError, retry_for)

    def test_link_task_declares_the_narrowed_scope(self):
        self.assertEqual(link_care_context.autoretry_for, LINK_RETRY_FOR)


class ReferenceIdTests(TestCase):
    def test_reference_id_is_fixed_at_enqueue_so_retries_reuse_one_transaction(self):
        with patch("abdm.tasks.link_care_context.link_care_context") as task:
            enqueue_link_care_context(
                patient_external_id="0f1d2c3b-4a59-6879-8a9b-0c1d2e3f4a5b",
                care_context=CARE_CONTEXT,
                hf_id="IN1410000017_4",
            )

            reference_id = task.apply_async.call_args.kwargs["kwargs"]["reference_id"]
            self.assertTrue(reference_id)

    def test_reference_id_reaches_the_gateway(self):
        with (
            patch("abdm.tasks.link_care_context.Patient") as patient_model,
            patch("abdm.tasks.link_care_context.GatewayService") as gateway,
        ):
            patient_model.objects.filter.return_value.first.return_value = object()

            link_care_context.run(
                reference_id="ref-1",
                patient_external_id="0f1d2c3b-4a59-6879-8a9b-0c1d2e3f4a5b",
                care_context=CARE_CONTEXT,
                hf_id="IN1410000017_4",
            )

            payload = gateway.link__carecontext.call_args.args[0]
            self.assertEqual(payload["reference_id"], "ref-1")
