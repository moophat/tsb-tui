"""The passphrase's bytes and argon2id, against C7's published vectors (phase 12 § B; vault-unlock step 2).

Why it matters (Rule 9): the vault opens only if the TUI derives exactly the bytes every other surface derives. A passphrase
that is normalised differently (a non-ASCII space kept, a decomposed character left decomposed) or argon2id run with other
parameters yields a hash that opens nothing, with no error to say why: the user is told "wrong passphrase" for a right one.
So the tests are the contract's vectors, not values computed by the code under test:

* `kat-opaquestring` and `chain-passphrase-bytes`: RFC 8265's OpaqueString profile, including the refusals (empty, a control
  character), where the surface derives nothing.
* `kat-argon2id-reference`: every case of the reference implementation's own test file, at the parameters each names.
  `kat-argon2id-rfc9106` is not run: it sets argon2's secret K and associated data X, which argon2-cffi's raw hash cannot
  take (its own file says an implementation that cannot checks the reference file instead).
* `chain-master-hash` and `chain-split`: the vault's own parameters (t=3, 64 MiB, p=4, 32 bytes), the halves as the
  disjoint `hash[0:16] ‖ hash[16:32]`, and the base64 the wire carries.
"""

from __future__ import annotations

import base64

import pytest
from tsb_tui import vault_crypto
from vault_support import CHAIN, b64, vector

OPAQUE = vector("kat-opaquestring")["cases"]
REFERENCE = vector("kat-argon2id-reference")["cases"]


@pytest.mark.parametrize("case", OPAQUE, ids=[case["name"] for case in OPAQUE])
def test_the_passphrase_bytes_are_c7s_opaquestring_vector(case: dict) -> None:
    if case.get("refused"):
        with pytest.raises(vault_crypto.PassphraseRefused):
            vault_crypto.passphrase_bytes(case["passphrase"])
    else:
        assert vault_crypto.passphrase_bytes(case["passphrase"]).hex() == case["bytes"]


def test_the_chains_passphrase_gives_the_chains_bytes() -> None:
    doc = vector("chain-passphrase-bytes")
    assert vault_crypto.passphrase_bytes(doc["passphrase"]).hex() == doc["bytes"]


@pytest.mark.parametrize("case", REFERENCE, ids=[case["name"] for case in REFERENCE])
def test_argon2id_reproduces_the_reference_implementations_cases(case: dict) -> None:
    params = case["params"]
    assert params["version"] == 19
    tag = vault_crypto.argon2id(bytes.fromhex(case["password"]), bytes.fromhex(case["salt"]),
                                time_cost=params["time_cost"], memory_kib=params["memory_kib"],
                                parallelism=params["parallelism"], tag_length=params["tag_length"])
    assert tag.hex() == case["tag"]


def test_the_master_hash_is_c7s_chain_vector_at_the_contracts_parameters() -> None:
    doc = vector("chain-master-hash")
    assert doc["params"] == {"version": 19, "time_cost": 3, "memory_kib": 65536, "parallelism": 4, "tag_length": 32}
    assert vault_crypto.master_hash(bytes.fromhex(doc["passphrase_bytes"]), bytes.fromhex(doc["user_salt"])).hex() == \
        doc["master_hash"]


def test_the_split_is_two_disjoint_halves_of_c7s_vector() -> None:
    doc = vector("chain-split")
    part1, part2 = vault_crypto.split(bytes.fromhex(doc["master_hash"]))
    assert (part1.hex(), part2.hex()) == (doc["part1"], doc["part2"])
    assert part1 + part2 == bytes.fromhex(doc["master_hash"])


def test_a_passphrase_and_the_served_salt_give_the_wire_halves_of_the_chain() -> None:
    """The whole path the unlock takes: the passphrase as typed, the salt as the core sends it (padded base64), the
    halves as they go back (padded base64)."""
    part1, part2 = vault_crypto.halves_b64(CHAIN.passphrase, b64(CHAIN.salt))
    assert (part1, part2) == CHAIN.wire
    assert base64.b64decode(part1) + base64.b64decode(part2) == CHAIN.master_hash


def test_a_refused_passphrase_derives_nothing() -> None:
    with pytest.raises(vault_crypto.PassphraseRefused):
        vault_crypto.halves_b64("bell\u0007", b64(CHAIN.salt))
