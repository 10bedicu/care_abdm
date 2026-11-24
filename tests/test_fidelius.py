import base64
import dataclasses
import unittest

from fastecdsa import keys

from abdm.utils.fidelius import (
    BC25519,
    CryptoController,
    DecryptionRequest,
    EncryptionRequest,
    KeyMaterial,
)

# The test keys are from Java version of fidelius-cli
#   https://github.com/mgrmtech/fidelius-cli/blob/main/README.md
TEST_PRIVATE_KEY = "DMxHPri8d7IT23KgLk281zZenMfVHSdeamq0RhwlIBk="
TEST_PUBLIC_KEY = "BAheD5rUqTy4V5xR4/6HWmYpopu5CO+KO8BECS0udNqUTSNo91TIqIIy1A4Vh+F94c+n9vAcwXU2bGcfsI5f69Y="
TEST_X509_PUBLIC_KEY = "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQIXg+a1Kk8uFecUeP+h1pmKaKbuQjvijvARAktLnTalE0jaPdUyKiCMtQOFYfhfeHPp/bwHMF1NmxnH7COX+vW"

ENCRYPTION_REQUEST = EncryptionRequest(
    sender_nonce="lmXgblZwotx+DfBgKJF0lZXtAXgBEYr5khh79Zytr2Y=",
    requester_nonce="6uj1RdDUbcpI3lVMZvijkMC8Te20O4Bcyz0SyivX8Eg=",
    sender_private_key="AYhVZpbVeX4KS5Qm/W0+9Ye2q3rnVVGmqRICmseWni4=",
    requester_public_key=TEST_PUBLIC_KEY,
    string_to_encrypt="Wormtail should never have been Potter cottage's secret keeper.",
)

DECRYPTION_REQUEST = DecryptionRequest(
    sender_nonce="lmXgblZwotx+DfBgKJF0lZXtAXgBEYr5khh79Zytr2Y=",
    requester_nonce="6uj1RdDUbcpI3lVMZvijkMC8Te20O4Bcyz0SyivX8Eg=",
    requester_private_key=TEST_PRIVATE_KEY,
    sender_public_key="BABVt+mpRLMXiQpIfEq6bj8hlXsdtXIxLsspmMgLNI1SR5mHgDVbjHO2A+U4QlMddGzqyEidzm1AkhtSxSO2Ahg=",
    encrypted_data="pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw==",
)


class TestUnits(unittest.TestCase):
    def test_curve_BC25519(self):
        # Test that key pair produced from BouncyCastle
        #   Java matches the one produced by our custom
        #   curve BC25519.
        private_key_base64 = TEST_PRIVATE_KEY
        private_key_int = int.from_bytes(
            base64.b64decode(private_key_base64.encode("utf-8")),
            byteorder="big",
        )
        public_key = keys.get_public_key(private_key_int, BC25519)
        public_key_base64 = KeyMaterial.encode_public_key_to_base64(public_key)
        self.assertEqual(
            public_key_base64,
            TEST_PUBLIC_KEY,
        )

    def test_private_key_encoding_decoding(self):
        private_key_int = CryptoController.decode_base64_to_private_key(
            TEST_PRIVATE_KEY
        )
        self.assertEqual(
            private_key_int,
            5788682699176295281730350068232349311395990232835367296300538348913375387673,
        )
        self.assertEqual(
            KeyMaterial.encode_private_key_to_base64(private_key_int),
            TEST_PRIVATE_KEY,
        )

    def test_public_key_encoding_decoding(self):
        public_key_base64 = "BAheD5rUqTy4V5xR4/6HWmYpopu5CO+KO8BECS0udNqUTSNo91TIqIIy1A4Vh+F94c+n9vAcwXU2bGcfsI5f69Y="
        public_key_point = CryptoController.decode_base64_to_public_key(
            public_key_base64
        )
        self.assertEqual(
            KeyMaterial.encode_public_key_to_base64(public_key_point),
            public_key_base64,
        )

    def test_compute_shared_secret(self):
        shared_secret = CryptoController.compute_shared_secret(
            DECRYPTION_REQUEST.requester_private_key,
            DECRYPTION_REQUEST.sender_public_key,
        )
        self.assertEqual(
            shared_secret,
            "HZbc9a4h9kMAReILN5VtvbSYHWQpfIcrZ9pWHlQZUHs=",
        )
        self.assertEqual(
            shared_secret,
            CryptoController.compute_shared_secret(
                ENCRYPTION_REQUEST.sender_private_key,
                ENCRYPTION_REQUEST.requester_public_key,
            ),
        )


class TestIntegration(unittest.TestCase):
    def test_key_generation(self):
        # Test that key pair generated is a valid ECDSA curve 25519 key
        key_material = KeyMaterial.generate()
        private_key = key_material.private_key
        public_key = key_material.public_key

        private_key_int = CryptoController.decode_base64_to_private_key(private_key)
        public_key_point = keys.get_public_key(private_key_int, BC25519)
        self.assertEqual(
            KeyMaterial.encode_public_key_to_base64(public_key_point),
            public_key,
        )

    def test_encryption(self):
        self.assertEqual(
            CryptoController.encrypt(ENCRYPTION_REQUEST),
            "pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw==",
        )

    def test_base64_encryption(self):
        json_message = '{"a": "b"}'
        base64_encryption_request = dataclasses.replace(ENCRYPTION_REQUEST)
        base64_encryption_request.string_to_encrypt_base64 = base64.b64encode(
            json_message.encode()
        ).decode()

        self.assertEqual(
            CryptoController.encrypt(base64_encryption_request),
            CryptoController.encrypt(ENCRYPTION_REQUEST),
        )

    def test_decryption(self):
        self.assertEqual(
            CryptoController.decrypt(DECRYPTION_REQUEST),
            "Wormtail should never have been Potter cottage's secret keeper.",
        )

    def test_encryption_x509(self):
        x509_encryption_request = dataclasses.replace(ENCRYPTION_REQUEST)
        x509_encryption_request.requester_public_key = TEST_X509_PUBLIC_KEY
        self.assertEqual(
            CryptoController.encrypt(x509_encryption_request),
            "pzMvVZNNVtJzqPkkxcCbBUWgDEBy/mBXIeT2dJWI16ZAQnnXUb9lI+S4k8XK6mgZSKKSRIHkcNvJpllnBg548wUgavBa0vCRRwdL6kY6Yw==",
        )

    def test_decryption_x509(self):
        x509_decryption_request = dataclasses.replace(DECRYPTION_REQUEST)
        x509_decryption_request.sender_public_key = "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQAVbfpqUSzF4kKSHxKum4/IZV7HbVyMS7LKZjICzSNUkeZh4A1W4xztgPlOEJTHXRs6shInc5tQJIbUsUjtgIY"
        self.assertEqual(
            CryptoController.decrypt(x509_decryption_request),
            "Wormtail should never have been Potter cottage's secret keeper.",
        )

    def test_decrypt_rejects_tampered_ciphertext(self):
        encrypted_bytes = bytearray(base64.b64decode(DECRYPTION_REQUEST.encrypted_data))
        encrypted_bytes[0] ^= 0x01
        tampered_request = dataclasses.replace(
            DECRYPTION_REQUEST,
            encrypted_data=base64.b64encode(bytes(encrypted_bytes)).decode("utf-8"),
        )

        with self.assertRaises(ValueError):
            CryptoController.decrypt(tampered_request)


if __name__ == "__main__":
    unittest.main()
