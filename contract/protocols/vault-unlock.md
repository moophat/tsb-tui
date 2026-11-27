# vault-unlock — unlock a principal's vault for one login, and wipe it

| | |
|---|---|
| **ID** | vault-unlock |
| **Purpose** | Create a principal's vault in two calls (mint the salts, then seal the empty set); turn a passphrase the surface holds into an unlock the core holds for one login, under the vault lock, so new connections can be established with the principal's stored credentials; and let a vault administrator wipe a vault whose passphrase is lost. |
| **Status** | E4 (the vault release) |
| **Corpus clauses** | credential §2, §3, §4.1, §4.2, §5.1, §5.4, §5.5, §7.4, §8.1, §9, §10.2, §11.1, §12, Appendix A, Appendix B; Contract §1, §4, §12; open_decisions §5 |
| **Seams** | C7, C1, C6 |
| **Schemas** (this release) | `openapi/tsb.openapi.json`: `GET /vault/salt` and `POST /vault/salt` (`VaultSalt`), `POST /vault/init` (`VaultInitRequest`), `POST /vault/unlock` (`VaultUnlockRequest`), `POST /vault/part2` (`Part2RegenerateRequest`), `DELETE /vault/users/{username}`, `POST /devices/challenges` (`DeviceChallengeRequest`, `DeviceChallenge`), `#/components/responses/PassphraseLimited`, `DeviceNotTrusted`, `VaultAdminForbidden`, `ActNotAudited`; `vault.schema.json` (`vault_user`, `credential_set`, `x-redis`, `x-associated-data`, `b64_16`, `signature`); `vault-vectors.schema.json`; `error.schema.json` `x-codes`; `audit-event.schema.json#/$defs/vault_wipe_event`, `#/$defs/device_event`; `config.schema.json#/properties/vault`, `#/properties/locks` |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`). Fixtures are cited as
`<seam>/<name>`, under `contract/fixtures/`. Corpus files, under `srs/`: credential = `credential-protection-model.md`,
Contract = `architecture-contract.md`, Storage = `storage-and-discovery-architecture.md`.

**Words used here.**

- **Passphrase.** The principal's vault secret, distinct from the login password. It lives in the operator's head and,
  for one derivation, in the surface's memory; it never crosses a wire or reaches a disk (credential §12).
- **master_hash, Part 1, Part 2.** master_hash = argon2id(passphrase bytes, `user_salt`), 32 bytes; Part 1 is its first
  16 bytes, Part 2 its last 16 (credential §4.1, Appendix A). The passphrase's bytes are UTF-8 after RFC 8265's
  OpaqueString profile [C7/kat-opaquestring; C7/chain-passphrase-bytes].
- **encryption_key.** PBKDF2-HMAC-SHA256(master_hash, `salt2`, 100,000), derived by the core only; it opens the
  principal's sealed credential set [C7/chain-encryption-key].
- **The set, the index.** One AES-256-GCM blob per principal holding every personal credential, and beside it in the
  same bbolt record a plaintext list of each entry's reference and kind (`vault_user`). The GCM tag is what checks a
  passphrase: halves that do not open the set are a wrong passphrase.
- **Minted, sealed.** A vault is minted when the core has stored its two salts and nothing else; it is sealed when the
  empty set has been sealed under the key the passphrase derives. Only a sealed vault can be unlocked. A minted vault
  does not expire; a wipe removes it.
- **An unlock.** What the core holds for one login after a passphrase entry: `encryption_key`, never the plaintext set
  (DEFAULT 33), under the vault lock (→ vault-lock). It belongs to the login that made it (credential §5.5).
- **Trusted, untrusted.** A trusted surface has registered its device (→ device-trust): it keeps a sealed Part 1 and a
  device key, and only Part 1 crosses the wire. An untrusted surface keeps nothing and sends both halves (credential
  §5.1). A login is tied to one device once (→ device-trust); an untied login is an untrusted-mode login.
- **Bytes on the wire** are padded base64; `device_id` is base64url. Vector files write bytes in hex (conventions,
  encodings).

## Path

init: SF-CO → CO-BB → SF-CO → CO-BB · unlock: SF-CO → CO-BB → CO-RD → CO-BB → (session-open) · wipe: SF-CO → CO-DK → (device-trust) → CO-BB → CO-FS ·
ends: (vault-lock), (login), (device-trust)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `GET /vault/salt` → `VaultSalt` | the principal (cookie) | none: the read writes nothing, so a GET never creates a vault (OPEN-1.3 (c)). It answers `initialized` true and the salt for a sealed vault; `initialized` false and the salt for a minted one; `initialized` false and no salt when there is none [C7/salt-read; C7/salt-read-shape] | 401 `unauthenticated` | the surface |
| 1b | SF-CO, CO-BB | SF → CO, CO → BB | HTTPS; a bbolt write | **the mint**, initialisation's first call: `POST /vault/salt`, no body → `VaultSalt` | the principal; the login alone (DEFAULT 46) | The first call stores fresh `user_salt` and `salt2`, 16 bytes each from a CSPRNG, in a `vault_user` record that is minted, not sealed, and answers `user_salt` with `initialized` false. A repeat while the vault is unsealed answers the same salts and writes nothing [C7/salt-mint; C7/salt-mint-repeat-same-salts]. No audit line: no credential is stored, and credential §8.4 has no such event [C7/mint-and-init-write-no-audit-line] (ruling 2026-10-29 21:07) | 401; on a sealed vault 409 `vault_exists` [C7/salt-mint] | the surface |
| 2 | — | SF → SF | — | — | the surface | The surface turns the passphrase into bytes (OpaqueString), derives master_hash and splits it; a trusted surface also derives `storage_key` = argon2id(passphrase bytes, `device_id`) and opens its sealed Part 1 (credential §5.4) [C7/chain-master-hash; C7/chain-split; C7/chain-storage-key; C7/chain-part1-seal]. Every derivation finishes before a challenge is requested (step 3), because a challenge lives seconds and argon2id at 64 MiB takes seconds | a passphrase the profile refuses: the surface derives nothing [C7/kat-opaquestring] | — |
| 3 | SF-CO | SF → CO | HTTPS | one of: **init**, initialisation's second call, `POST /vault/init` `{part1, part2}` (DEFAULT 2), the halves derived from the minted salt; **untrusted unlock** `POST /vault/unlock` `{mode: untrusted, part1, part2}`; **trusted unlock** `{mode: trusted, challenge_id, part1, signature}` with a `vault.unlock` challenge issued for this login's tied device (→ device-trust, the challenge); **regeneration** `POST /vault/part2` `{challenge_id, part1, part2, signature}` with a `part2.regenerate` challenge from the tied device (credential §9, §7.2 step 3) | the principal | none yet | 422 `invalid_request` (not the shape) [C7/unlock-untrusted; C7/part2-regenerate; C7/init]; init with no minted salt: 409 `vault_not_minted` [C7/init-before-mint-refused]; init on a sealed vault: 409 `vault_exists` [C7/init]; challenge refusals per device-trust: 403 `device_not_trusted`, 409 `challenge_failed` | the surface |
| 4 | CO-BB, CO-RD | BB → CO, RD → CO | bbolt read; a Redis command | `vault_user` (`user_salt`, `salt2`, the set); for a trusted unlock, Part 2 at `vault:part2:{user_id}:{device_id}` (`x-redis`) | the core | none | no sealed vault, none at all or one minted and not sealed: 409 `vault_not_initialized`, not counted [C7/unlock-on-minted-vault-not-initialized]. A trusted unlock that finds no Part 2: 409 `part2_missing`, **not counted**, and never read as `device_not_trusted`: the surface regenerates (step 3) with the passphrase it holds (DEFAULT 43) [C7/part2-missing-409-uncounted] | the surface |
| 5 | — | CO → CO | — | — | the core | Reconstruct master_hash = part1 ‖ part2, derive `encryption_key`, open the set (GCM). **Init** opens nothing: it seals a set with no entry under that key and writes it, with an empty index, into the minted record in one bbolt write, and the vault is sealed; it writes no audit line [C7/init; C7/mint-and-init-write-no-audit-line]. Past the principal's passphrase limit (`vault.unlock_attempts` in `vault.unlock_window`), an untrusted unlock is refused before any of it | a failed tag: 409 `unlock_failed` with `attempts_remaining`, counted **per principal** across its logins (DEFAULTs 14, 45) [C7/unlock-untrusted]; past the limit: 429 `rate_limited` until the window passes [C7/unlock-attempt-limit]. A correctly signed trusted unlock or regeneration is never counted and never refused by the limit [C7/trusted-unlock-never-counted] | the surface |
| 6 | — | CO → CO | — | — | the core | Hold the unlock on this login, under the vault lock, recording its device for a trusted one; it **replaces** any unlock the login held, its hard cap counted from this passphrase entry (DEFAULT 69); init holds one too (DEFAULT 35). The install happens only into a login still alive, and for a trusted unlock or regeneration under the (user, device) lock held since the challenge's verification (DEFAULT 48) [C7/revoke-inflight-unlock-not-installed]. The user's last-active time moves (DEFAULT 32). master_hash is dropped | — | — |
| 7 | CO-RD | CO → RD | a Redis command | Part 2, TTL `vault.part2_ttl` | the core | A trusted unlock renews Part 2's TTL; a regeneration stores Part 2 with a fresh TTL, only for the login's tied device, on the allowlist, and only when the halves opened the set (D4) [C7/part2-ttl-renewed; C7/part2-regenerate-revoked-refused]. An untrusted unlock writes nothing to Redis (credential §5.1) | — | — |
| 8 | SF-CO | CO → SF | HTTPS | 204 | the core | the login holds an unlock. No credential is ever returned to a surface (credential §5, as N1 item 11 words §11.1) | — | the surface |
| 9 | — | CO → CO | — | → session-open step 3 | the core | A create on a **new** connection whose personal credential needs an unlock uses this login's; another login of the same user is answered 409 `vault_locked` until it unlocks itself (credential §5.5) [C7/vault-locked-other-login; C1/session-create-vault-locked-409]. A `user_unlocked` org credential uses its storing administrator's unlock instead, held by any login of that administrator; the caller's own unlock never matters for it, and while no login of the administrator holds one the create is answered 409 `org_credential_locked` (credential §8.2; ruling 2026-10-30 00:58) [C1/session-create-org-credential-locked-409]. A reuse needs no unlock [C7/reuse-on-locked-vault]. No attach, replay, reattach or restart reads the vault (credential §10.2) | — | — |
| 10 | — | CO → CO | — | → vault-lock; → login; → device-trust | the core | The unlock ends: at the vault idle timer (lock on idle), at its hard cap, on "lock now" (→ vault-lock); at the login's logout or expiry (login step 14); when the device the login is tied to is revoked (→ device-trust step 5); at a wipe (step 11); at a core restart, which ends every login (credential §3.2). Ending it purges `encryption_key`, master_hash and any decrypted material | — | — |
| 11 | SF-CO | SF → CO | HTTPS | `DELETE /vault/users/{username}` | a vault administrator (`policy.yaml` `vault.admin`) | **The wipe** (D16; DEFAULT 46), in order: the `vault.wipe` line first (step 12); every device of the user revoked as device-trust step 5 revokes one, reason `admin`, the records kept and marked; the user's `vault_user` record deleted (salts, set, index), minted or sealed [C7/vault-wipe-removes-minted]; every unlock of the user purged; every Layer 2a mapping of the user cleared (O30), in the order step 12 gives. Nothing is recovered: the user's next salt read answers initialized false and the user initialises again with the login alone [C7/vault-wipe; C7/vault-wipe-fresh-start]. Live sessions are untouched (credential §10.2) | 403 `forbidden` without `vault.admin`; 404 `user_not_found`; 409 `vault_wipe_blocked` when the user is the storing administrator of any user-unlocked org credential, `credential_refs` naming them, until each is deleted (its grants revoked first) or stored again by another administrator (O31) [C7/vault-wipe] | the administrator |
| 12 | CO-DK | CO → DK | file append (audit JSONL) | `vault_wipe_event` (`vault.wipe`: the user, the administrator, the cleared targets), then a `device_event` per revoked device | the core | written inside the bbolt write transaction, before its commit; a line that cannot be written refuses the wipe, 500 `internal_error`, nothing committed (DEFAULT 49) [C6/event-vault-wipe; C7/audit-unwritable-refuses-act]. **The order with `profiles.yaml`** (O30; ruling 2026-10-29 21:07), for a wipe and for a personal credential's delete alike: the audit line, then the rewrite of `users/<name>/profiles.yaml` without the mappings to the removed references (temporary file and rename, Storage §10.6), then the bbolt commit. A commit that fails after the rewrite leaves the credential live and unmapped, never a mapping to a missing reference, and is logged [C7/credential-delete-clears-mappings] | — | readers of the audit log |

Requirements, by step:

- 1 — The core MUST answer the salt read without writing anything: `initialized` false with the salt for a minted
  vault, with no salt for a principal with none [C7/salt-read].
- 1b, 3, 5 — The core MUST mint once and answer the same salts on a repeat while unsealed, refuse a mint or an init on a
  sealed vault with `vault_exists` and an init with no mint with `vault_not_minted`, and seal only from the minted
  salts; neither call writes an audit line [C7/salt-mint; C7/salt-mint-repeat-same-salts; C7/init;
  C7/init-before-mint-refused; C7/mint-and-init-write-no-audit-line]. An unlock or a registration on a minted vault
  MUST be `vault_not_initialized` [C7/unlock-on-minted-vault-not-initialized].
- 2 — A surface MUST derive with the parameters of credential Appendix A and the encoding of the chain vectors, and
  MUST finish every derivation before it requests a challenge. Graded for the browser by U8c's tests and U11's browser
  rig against the C7 vectors, for the TUI by U7's tests (owed; phase 16 names them).
- 3–5 — The core MUST refuse a wrong passphrase with `unlock_failed`, count it per principal, and refuse past the limit
  with `rate_limited`; it MUST NOT count a correctly signed trusted unlock or regeneration, nor a `part2_missing`
  answer [C7/unlock-untrusted; C7/unlock-attempt-limit; C7/trusted-unlock-never-counted; C7/part2-missing-409-uncounted].
- 6 — The core MUST hold the unlock on the login that made it, replacing any it held, and MUST NOT install an unlock
  into a login that has ended or after the device's revocation purged it [C7/register-holds-unlock;
  C7/revoke-inflight-unlock-not-installed].
- 7 — The core MUST accept a regenerated Part 2 only when the halves open the set, only for the login's tied device on
  the allowlist [C7/part2-regenerate; C7/part2-regenerate-revoked-refused].
- 8 — The core MUST NOT return a decrypted credential, a key or a Part to any surface. Owed: no fixture can see every
  answer; the canary rig (U11) scans for it.
- 9 — Another login of the same user MUST be answered `vault_locked` until it unlocks itself
  [C7/vault-locked-other-login]; a `user_unlocked` org credential MUST be used while any login of its storing
  administrator holds an unlock, whatever the caller's own, and MUST be answered `org_credential_locked` while none does
  [C1/session-create-org-credential-locked-409]; a reuse MUST NOT need an unlock [C7/reuse-on-locked-vault].
- 11, 12 — The wipe MUST write its line first and MUST be refused when it cannot; it MUST refuse while it would strand a
  user-unlocked org credential; it MUST remove a minted vault too [C7/vault-wipe; C7/vault-wipe-fresh-start;
  C7/vault-wipe-removes-minted; C6/event-vault-wipe]. A wipe or a delete MUST order the audit line, the
  `profiles.yaml` rewrite and the bbolt commit as step 12 says [C7/credential-delete-clears-mappings].

## Refusals and who is told

- Every refusal is the error body (V4) and names no host detail (V11). `unlock_failed`, `part2_missing`,
  `vault_not_initialized`, `vault_not_minted`, `vault_exists`, `challenge_failed`, `vault_wipe_blocked` and, on session create,
  `org_credential_locked` are 409; `device_not_trusted` is 403; none is
  401, which a surface reads as a lost login (OPEN-1.4, ruled). A surface of an earlier release meets these codes by
  their status.
- Only the requesting surface learns of an unlock or a refusal. An unlock writes no audit line: vault unlock and lock
  events wait for credential §11.2. Neither the mint nor init writes one. The wipe is audited (step 12).

## Security considerations

- **The passphrase is never sent.** Untrusted mode sends the whole master_hash over TLS and keeps nothing on the
  device, and the core then holds the unlock up to the hard cap (credential §5.1). Trusted mode sends Part 1 only
  (credential §5.4).
- **What device trust buys and does not** (D13): only Part 1 crosses the wire; the device key proves which device is
  unlocking; revoking the device ends its logins (5.40c). It buys no fewer prompts: every unlock, trusted or not, lasts
  until the vault lock. It does not lock the user out: a revoked device's user can still unlock in untrusted mode.
- **The device proof guards the unlock only**: after an unlock, the login cookie alone opens connections with the
  unlocked credentials and writes to the vault until the vault locks (30 minutes idle, 8 hours at most by default).
  The cookie stays unbound (decision 5.41; DEFAULT 41; N1 items 1–2).
- **A password holder can block a user's untrusted unlocks** for the limiter's window by spending the attempts
  (DEFAULT 45): accepted, since keying the limit per login would multiply the guess budget.
- **Against a holder of bbolt the passphrase through argon2id is the only protection**: passphrase + bbolt decrypts
  that user's credentials, and bbolt alone lets an attacker test guesses offline at argon2id cost (credential §6 as
  corrected). Every NO row of credential §6.1–§6.3 is a test [C7/breach-l … C7/breach-lrb, twelve fixtures].
- **A passphrase typed into a client running inside a TSB session is recorded by that session** (open_decisions §5):
  every TUI passphrase prompt says so (DEFAULT 62).
- **The wipe destroys, it does not recover**: a forgotten passphrase stays unrecoverable (credential §7.4, as N1 item
  8 amends it); only a vault administrator may wipe, audited first.

## Open items

1. **How a vault is initialised** — *ruled 2026-10-29 21:07 (G1):* two calls, the mint (`POST /vault/salt`, step 1b)
   and init (`POST /vault/init`, step 3), which the plan's one-call init could not be once OPEN-1.3 (c) had init mint
   the salt its halves are derived from.
2. **O30's order and a mapping left behind** — *ruled 2026-10-29 21:07 (G2):* the audit line, then the `profiles.yaml`
   rewrite, then the bbolt commit (step 12); a mapping whose reference is not in the index (a file edited or restored by
   hand) makes session create answer 409 `mapped_credential_missing`, naming it, never a silent skip (session-open
   step 3).
3. **Passphrase change.** No flow (DEFAULT 18; credential has none); a known gap.
4. **Vault unlock and lock audit events** wait for credential §11.2.

## Picture

pending
