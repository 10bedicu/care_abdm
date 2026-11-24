import base64
import os
from dataclasses import dataclass
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from fastecdsa import curve, keys
from fastecdsa.point import Point

# Referenced from https://github.com/dimagi/pyfidelius/blob/master/fidelius.py

# Java's Bouncycastle version of the curve 25519
#   Only difference between BC and fastcecdsa is the value of curve.gy
#   See https://github.com/bcgit/bc-java/blob/main/core/src/main/java/org/bouncycastle/crypto/ec/CustomNamedCurves.java#L99
#   See the same file in below commit to find the hex decoded value of gx and gy
#   https://github.com/bcgit/bc-java/commit/baefa5e3417c83299aa91be528aed6c23e23474c
BC25519 = curve.Curve(
    "BC25519",
    curve.W25519.p,
    curve.W25519.a,
    curve.W25519.b,
    curve.W25519.q,
    curve.W25519.gx,
    0x20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9,
    # This is an OID specific to this implementation
    #   taken as the next one from fastecdsa OIDs
    b"\x01\x03\x06\x01\x04\x01\x97U\x05\x01",
)

fixed_prefix_bytes = base64.b64decode(
    "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB///////////////////////////////////"
    "//////7TBEBCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0J"
    "e0Je0Je0JgtenHcQyGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLH"
    "dI0UySPU1+bXxhsinpxaJ+ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAAQ="
)


@dataclass(frozen=True)
class KeyMaterial:
    private_key: str
    public_key: str
    x509_public_key: str
    nonce: str

    @classmethod
    def generate(cls):
        private_key, public_key = keys.gen_keypair(BC25519)
        return cls._encode(private_key, public_key)

    @classmethod
    def generate_for_private_key(cls, private_key: int):
        public_key = keys.get_public_key(private_key, BC25519)
        return cls._encode(private_key, public_key)

    @classmethod
    def _encode(cls, private_key: int, public_key: Point):
        private_key_base64 = cls.encode_private_key_to_base64(private_key)
        public_key_base64 = cls.encode_public_key_to_base64(public_key)
        x509_public_key_base64 = cls.encode_x509_public_key_to_base64(public_key)
        nonce_base64 = cls.generate_base64_nonce()
        return cls(
            private_key_base64, public_key_base64, x509_public_key_base64, nonce_base64
        )

    @classmethod
    def encode_private_key_to_base64(cls, key: int):
        return base64.b64encode(
            key.to_bytes((key.bit_length() + 7) // 8, byteorder="big")
        ).decode("utf-8")

    @classmethod
    def encode_public_key_to_base64(cls, key: Point):
        x_bytes = key.x.to_bytes((key.x.bit_length() + 7) // 8, byteorder="big")
        y_bytes = key.y.to_bytes((key.y.bit_length() + 7) // 8, byteorder="big")
        return base64.b64encode(
            # 04 indicates uncompressed form
            b"\x04"
            + x_bytes
            + y_bytes
        ).decode("utf-8")

    @classmethod
    def encode_x509_public_key_to_base64(cls, key: Point):
        # Adds Java Bouncy Castle X509 format prefix
        x_bytes = key.x.to_bytes((key.x.bit_length() + 7) // 8, byteorder="big")
        y_bytes = key.y.to_bytes((key.y.bit_length() + 7) // 8, byteorder="big")
        return base64.b64encode(
            fixed_prefix_bytes + x_bytes + y_bytes
        ).decode("utf-8")

    @classmethod
    def generate_base64_nonce(cls):
        nonce = os.urandom(32)
        return base64.b64encode(nonce).decode("utf-8")


@dataclass
class EncryptionRequest:
    sender_nonce: str
    requester_nonce: str
    sender_private_key: str
    requester_public_key: str
    string_to_encrypt: str
    string_to_encrypt_base64: Optional[str] = None

    def __post_init__(self):
        if self.string_to_encrypt_base64:
            error = "Pass only one of " "string_to_encrypt or string_to_encrypt_base64"
            assert not self.string_to_encrypt, error
            self.string_to_encrypt = base64.b64decode(
                self.string_to_encrypt_base64
            ).decode()


@dataclass
class DecryptionRequest:
    sender_nonce: str
    requester_nonce: str
    requester_private_key: str
    sender_public_key: str
    encrypted_data: str


class CryptoController:

    @classmethod
    def encrypt(cls, encryption_request: EncryptionRequest):
        sender_nonce = base64.b64decode(encryption_request.sender_nonce)
        requester_nonce = base64.b64decode(encryption_request.requester_nonce)

        # Calculate IV and salt from nonces
        xor_of_nonces = bytes(a ^ b for a, b in zip(sender_nonce, requester_nonce))
        iv = xor_of_nonces[-12:]
        salt = xor_of_nonces[:20]

        shared_secret = cls.compute_shared_secret(
            encryption_request.sender_private_key,
            encryption_request.requester_public_key,
        )
        aes_encryption_key = cls.sha256_hkdf(salt, shared_secret, 32)
        string_bytes = encryption_request.string_to_encrypt.encode("utf-8")

        cipher = AES.new(aes_encryption_key, AES.MODE_GCM, iv)
        encrypted_data, tag = cipher.encrypt_and_digest(string_bytes)
        return base64.b64encode(encrypted_data + tag).decode("utf-8")

    @classmethod
    def decrypt(cls, decryption_request: DecryptionRequest):
        sender_nonce = base64.b64decode(decryption_request.sender_nonce)
        requester_nonce = base64.b64decode(decryption_request.requester_nonce)

        # Calculate IV and salt from nonces
        xor_of_nonces = bytes(a ^ b for a, b in zip(sender_nonce, requester_nonce))
        iv = xor_of_nonces[-12:]
        salt = xor_of_nonces[:20]

        shared_secret = cls.compute_shared_secret(
            decryption_request.requester_private_key,
            decryption_request.sender_public_key,
        )
        aes_encryption_key = cls.sha256_hkdf(salt, shared_secret, 32)
        encrypted_bytes = base64.b64decode(decryption_request.encrypted_data)
        ciphertext = encrypted_bytes[:-16]
        tag = encrypted_bytes[-16:]

        cipher = AES.new(aes_encryption_key, AES.MODE_GCM, iv)
        decrypted_string = cipher.decrypt_and_verify(ciphertext, tag)

        return decrypted_string.decode("utf-8")

    @classmethod
    def decode_base64_to_private_key(cls, encoded_key) -> int:
        key_bytes = base64.b64decode(encoded_key.encode("utf-8"))
        return int.from_bytes(key_bytes, byteorder="big")

    @classmethod
    def decode_base64_to_public_key(cls, encoded_key) -> Point:
        key_bytes = base64.b64decode(encoded_key.encode("utf-8"))
        if len(key_bytes) == 65:
            # Ensure the key is in uncompressed form (starts with 0x04)
            if key_bytes[0] != 0x04:
                raise ValueError("Invalid public key format")
            x_bytes = key_bytes[1:33]  # 32 bytes for x-coordinate
            y_bytes = key_bytes[33:]  # 32 bytes for y-coordinate
        else:  # Assumes x509 format
            x_bytes = key_bytes[-64:-32]
            y_bytes = key_bytes[-32:]
        x = int.from_bytes(x_bytes, byteorder="big")
        y = int.from_bytes(y_bytes, byteorder="big")
        return Point(x, y, curve=BC25519)

    @classmethod
    def compute_shared_secret(cls, sender_private_key, requester_public_key):
        private_key_int = cls.decode_base64_to_private_key(sender_private_key)
        public_key_point = cls.decode_base64_to_public_key(requester_public_key)
        return KeyMaterial.encode_private_key_to_base64(
            (private_key_int * public_key_point).x
        )

    @classmethod
    def sha256_hkdf(cls, salt, shared_secret, key_length_in_bytes):
        # Decode the initial key material from base64
        shared_secret_bytes = base64.b64decode(shared_secret)
        # Perform HKDF using SHA-256
        encryption_key = HKDF(shared_secret_bytes, key_length_in_bytes, salt, SHA256)
        return encryption_key
