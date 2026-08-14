import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

BLOCK_SIZE = 16  # AES block size in bytes


def import_key(merchant_key: str) -> bytes:
    return merchant_key.encode("utf-8")[:BLOCK_SIZE]


def encrypt(merchant_key: str, plaintext: str) -> str:
    key = import_key(merchant_key)
    cipher = AES.new(key, AES.MODE_CBC, key)
    return base64.b64encode(
        cipher.encrypt(pad(plaintext.encode("utf-8"), BLOCK_SIZE))
    ).decode()


def decrypt(merchant_key: str, ciphertext_b64: str) -> str:
    key = import_key(merchant_key)
    cipher = AES.new(key, AES.MODE_CBC, key)
    return unpad(cipher.decrypt(base64.b64decode(ciphertext_b64)), BLOCK_SIZE).decode(
        "utf-8"
    )


def checksum(plaintext: str) -> str:
    return hashlib.sha512(plaintext.encode("utf-8")).hexdigest()

