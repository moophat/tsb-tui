"""What the vault's untrusted unlock computes on this host: the passphrase's bytes and the two halves of `master_hash`
(credential-protection §4.1, Appendix A; vault-unlock step 2).

Nothing here is a primitive of its own. The passphrase goes through RFC 8265's OpaqueString profile by `precis-i18n`,
argon2id is `argon2-cffi`'s, and this module only fixes the parameters the contract fixes and the encoding of the halves:
`master_hash` = argon2id(passphrase bytes, `user_salt`) at time 3, memory 64 MiB, parallelism 4, 32 bytes; Part 1 is its
first 16 bytes and Part 2 its last 16, each as padded base64 on the wire. C7's vectors grade every step
(`tests/test_vault_crypto.py`). The derivation takes seconds, so callers run it off the UI thread.

A Python string holding the passphrase cannot be zeroed: callers drop it as soon as the halves are made, and nothing here
keeps, logs or returns anything but the halves.
"""

from __future__ import annotations

import base64

from argon2.low_level import Type, hash_secret_raw
from precis_i18n import get_profile

TIME_COST = 3
MEMORY_KIB = 65536
PARALLELISM = 4
TAG_LENGTH = 32
ARGON2_VERSION = 19


class PassphraseRefused(ValueError):
    """The OpaqueString profile refuses this passphrase (empty, or a disallowed code point such as a control
    character): the surface derives nothing. The message names no character of it."""


def passphrase_bytes(passphrase: str) -> bytes:
    """UTF-8 after RFC 8265's OpaqueString profile: non-ASCII spaces become the ASCII space, NFC composes, the profile's
    disallowed code points and the empty string are refused."""
    try:
        return get_profile("OpaqueString").enforce(passphrase).encode("utf-8")
    except UnicodeError:
        raise PassphraseRefused("the passphrase has characters the vault cannot accept") from None


def argon2id(password: bytes, salt: bytes, *, time_cost: int, memory_kib: int, parallelism: int,
             tag_length: int) -> bytes:
    return hash_secret_raw(password, salt, time_cost, memory_kib, parallelism, tag_length, Type.ID, ARGON2_VERSION)


def master_hash(passphrase_bytes_: bytes, user_salt: bytes) -> bytes:
    return argon2id(passphrase_bytes_, user_salt, time_cost=TIME_COST, memory_kib=MEMORY_KIB,
                    parallelism=PARALLELISM, tag_length=TAG_LENGTH)


def split(hash_: bytes) -> tuple[bytes, bytes]:
    """The disjoint halves, `hash[0:16] ‖ hash[16:32]`."""
    return hash_[:16], hash_[16:]


def halves_b64(passphrase: str, user_salt_b64: str) -> tuple[str, str]:
    """The passphrase as typed and the salt as the core sends it, to the two halves as the core takes them."""
    part1, part2 = split(master_hash(passphrase_bytes(passphrase), base64.b64decode(user_salt_b64)))
    return base64.b64encode(part1).decode("ascii"), base64.b64encode(part2).decode("ascii")
