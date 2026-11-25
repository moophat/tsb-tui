"""Verify synthetic vendor vectors with an independent test-only implementation.

The fixture ciphertext was generated using Go's AES and Blowfish implementations and
the Go bcrypt_pbkdf construction, allowing the documented empty configuration password.
This checker implements those primitives in Python rather than using the generator.
"""

import json
import unittest

from . import CONTRACT
from ._vendor_password_reference import (
    Blowfish,
    aes_cbc_decrypt,
    decode_legacy,
    decode_v2,
)


class VendorPasswordVectors(unittest.TestCase):
    def test_primitive_known_answers(self):
        bf = Blowfish(bytes(8))
        left, right = bf.block(0, 0)
        self.assertEqual((left.to_bytes(4, "big") + right.to_bytes(4, "big")).hex(), "4ef997456198dd78")
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        ciphertext = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd6")
        self.assertEqual(aes_cbc_decrypt(key, iv, ciphertext).hex(), "6bc1bee22e409f96e93d7e117393172a")

    def test_each_decoder_and_unreadable_case(self):
        fixture = json.loads((CONTRACT / "fixtures/C5/import-vendor-password-known-answers.json").read_text())
        seen = set()
        for entry in fixture["case"]["instances"]:
            case = entry["doc"]
            variant, ciphertext = case["variant"], bytes.fromhex(case["cipher_bytes"])
            with self.subTest(variant=variant, note=entry["note"]):
                def decode(ciphertext=ciphertext, variant=variant):
                    return decode_legacy(ciphertext) if variant == "legacy" else decode_v2(ciphertext, variant)
                if case["outcome"] == "readable":
                    self.assertEqual(decode(), case["plaintext"])
                    seen.add(variant)
                else:
                    with self.assertRaises((ValueError, UnicodeError)):
                        decode()
        self.assertEqual(seen, {"legacy", "02", "03"})


if __name__ == "__main__":
    unittest.main()
