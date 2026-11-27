# device-trust — trust a device by its key pair, prove it by challenge, revoke it

| | |
|---|---|
| **ID** | device-trust |
| **Purpose** | Register a device's public key on the principal's allowlist after the passphrase proves itself, tie a login to that device once, prove the device for one operation by a signed single-use challenge, list devices, and revoke one so that it can never unlock or regenerate Part 2 again and its logins end. |
| **Status** | E4 (the vault release) |
| **Corpus clauses** | credential Amendment 2026-09-14 (as N1 item 3 amends it), §3.1, §3.3, §4.2, §5.2, §5.3, §5.4, §6, §7.1, §7.2, §7.3, §9, Appendix B; decisions 5.18, 5.40c, 5.41; Storage §9 |
| **Seams** | C7, C1, C2 (the close), C6 |
| **Schemas** (this release) | `openapi/tsb.openapi.json`: `POST /devices/challenges` (`DeviceChallengeRequest`, `DeviceChallenge`), `POST /devices` (`DeviceRegisterRequest`, `Device`), `GET /devices` (`DeviceList`), `DELETE /devices/{device_id}`, `POST /auth/bind` (`LoginBindRequest`); `vault.schema.json` (`device_record`, `challenge_operation`, `login_handle`, `challenge_id`, `public_key`, `signature`, `x-associated-data` `device_challenge`, `x-redis`); `common.schema.json#/$defs/device_id`, `#/$defs/device_name`; `policy.schema.json` (`devices`, `permission`); `config.schema.json#/properties/vault`; `audit-event.schema.json#/$defs/device_event`; `ws-frames.schema.json#/x-close-codes` 4015 |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`). Fixtures:
`<seam>/<name>` under `contract/fixtures/`. credential = `srs/credential-protection-model.md`.

**Words used here.**

- **Device.** One browser profile (or, later, one native surface) for one TSB user, named by `device_id`, SHA-256 over
  stable facts of the device (credential §4.2), not a secret. Keyed on the server by (`user_id`, `device_id`), so two
  users can trust one browser profile, each with their own slot (DEFAULT 57).
- **Device key.** An ECDSA P-256 key pair the surface makes at registration; the browser keeps the private half in
  IndexedDB as a non-extractable key. The server stores the public half (SPKI) in the `devices` bucket; its presence
  is the **allowlist**.
- **Challenge.** 32 random bytes the core issues for one operation and one device, which the device signs to prove it
  holds the key now, here, for this. Operations: `device.register`, `login.bind`, `vault.unlock`, `part2.regenerate`.
- **Login handle.** The core's name for the login inside the signed bytes, returned with each challenge; never the
  cookie (HttpOnly, so page script cannot sign it).
- **Tied login.** A login bound once to one device, by a bind or by a registration it made while untied (DEFAULT 39).
  A trusted unlock and a Part 2 regeneration come only from the login's tied device.

## Path

register: SF-CO → CO-BB → CO-DK → CO-BB → CO-RD → SF-CO · bind: SF-CO → CO-BB · list: SF-CO → CO-BB ·
revoke: SF-CO → CO-DK → CO-BB → CO-RD → (login) → SF-CO · sweep: CO-BB → CO-DK → CO-BB → CO-RD

## The challenge

Normative for every operation that takes one (D3; DEFAULT 3, 47):

1. **Issue** `POST /devices/challenges {operation, device_id}` → `{challenge_id, challenge, login_handle, expires_in}`:
   32 bytes from a CSPRNG, padded base64; issued for exactly one operation; `expires_in` 1 to 60 seconds, the value of
   the knob `vault.challenge_ttl` (V7) [C7/challenge-shape; C7/challenge-answer-shape]. The core records the
   `device_id`, the login and the operation it issued for, and builds the signed bytes from that record only.
2. **Checks at issue**, before any signature work: for `vault.unlock`, `part2.regenerate` and `login.bind` the device
   MUST be on the allowlist, checked first, else 403 `device_not_trusted` [C7/challenge-device-not-trusted-first]; for
   `vault.unlock` and `part2.regenerate` it MUST be the login's tied device [C7/challenge-untied-device-refused]; for
   `login.bind` the login MUST be untied [C7/bind-once]; for `device.register` the allowlist does not apply, and a tied
   login may name only its own `device_id` [C7/register-other-device-refused]. Every refusal but the allowlist's is
   409 `challenge_failed`.
3. **The signed bytes**: the fields `"tsb.device.challenge.v1"`, `challenge` (32 raw bytes), the user, `device_id` (32
   raw bytes), the login handle and the operation, each as a 16-bit big-endian length then its bytes
   (`vault.schema.json` `x-associated-data`). ECDSA P-256 with SHA-256; the signature in IEEE P1363 form, `r ‖ s`, 64
   bytes; the key SPKI DER. One worked example and one verification, in hex [C7/chain-challenge-signed-bytes]; no
   signature vector, since ECDSA signing is randomised. Any valid P-256 key is accepted, software keys included
   (DEFAULT 58) [C7/kat-ecdsa-p256-p1363].
4. **At verification** every failure — expired, spent, voided, another operation, another login, another device,
   another user, a bad signature — is 409 `challenge_failed`: an answer never says which check failed
   [C7/replay-expired; C7/replay-spent; C7/replay-cross-login; C7/replay-cross-device; C7/replay-cross-user]. A
   signature made for one operation never verifies for another [C7/replay-unlock-to-regenerate]. A verified challenge
   moves the device's `last_used_at` (DEFAULT 60). Register, bind, unlock and regeneration hold the (user, device)
   lock from verification to install (DEFAULT 48).
5. **Consumed** atomically on the first verification whatever its outcome, so two concurrent answers cannot both use
   it and a wrong signature cannot be retried [C7/replay-concurrent]; at most four outstanding per login, a fifth
   voiding the oldest (DEFAULT 5); held in core memory only, so a core restart voids every one.
6. **Consumer**: a surface finishes every derivation before it requests the challenge, then signs and sends at once.
   It clears its stored device key and sealed Part 1 on `device_not_trusted` only, never on `challenge_failed` or
   `part2_missing`.

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | the challenge (above) | the principal | a challenge recorded for one operation, one device, this login | per "The challenge" 2 | the surface |
| 2 | SF-CO | SF → CO | HTTPS | **register** `POST /devices` `{challenge_id, part1, part2, public_key, signature, device_name}`; the device is the one the `device.register` challenge named (credential §5.2, §5.3, as N1 item 6 amends them) | the principal | none yet | 422 `invalid_request`: a key that is not an uncompressed P-256 SPKI or not on the curve, a device name past 64 characters or holding a control character [C7/device-register] | the surface |
| 3 | CO-BB | BB → CO | bbolt read | `vault_user` | the core | Under the (user, device) lock: verify and consume the challenge; `part1 ‖ part2` MUST open the set | no sealed vault (none, or one minted and not sealed): 409 `vault_not_initialized` [C7/register-needs-init; C7/unlock-on-minted-vault-not-initialized]; halves that do not open it: 409 `unlock_failed`, **counted** (the key is not yet on the allowlist), and nothing stored [C7/register-proves-passphrase]; past the passphrase limit: 429 `rate_limited`; the challenge: 409 `challenge_failed` | the surface |
| 4 | CO-DK, CO-BB | CO → DK, CO → BB | audit append inside a bbolt write | `device_event` `device.register`; `device_record` | the core | The line first, inside the write transaction (DEFAULT 49); the record with `device_pubkey`, `registered_at`, `last_used_at`; re-registering a revoked `device_id` writes it afresh | the line cannot be written: 500 `internal_error`, nothing committed [C7/audit-unwritable-refuses-act] | the audit log [C6/event-device-register] |
| 5 | CO-RD | CO → RD | a Redis command | Part 2 at `vault:part2:{user_id}:{device_id}`, TTL `vault.part2_ttl` | the core | an untied login is tied to the device [C7/register-ties-login]; the login holds an unlock, replacing any (DEFAULT 69) [C7/register-holds-unlock] | — | — |
| 6 | SF-CO | CO → SF | HTTPS | 201 `Device` | the core | — | — | the surface |
| 7 | SF-CO | SF → CO | HTTPS | **bind** `POST /auth/bind` `{challenge_id, signature}` for a `login.bind` challenge | the principal, untied login | the login is tied to the device, once, and never re-tied [C1/login-bind] | 403 `device_not_trusted`; 409 `challenge_failed` (a tied login is refused at issue) | the surface |
| 8 | SF-CO | SF → CO | HTTPS | **list** `GET /devices` (`?user=` for another principal) → `DeviceList` | the principal (own); a vault administrator (any) | — | `?user=` naming another principal without `vault.admin`: 403 `forbidden` [C7/device-list]. Never a key [C7/device-list-shape] | the surface |
| 9 | SF-CO | SF → CO | HTTPS | **revoke** `DELETE /devices/{device_id}` (`?user=` for an administrator) | the device's principal; a vault administrator | — | another principal's device asked about without `vault.admin`, or no such device: 404 `device_not_found`, as an absent one (OPEN-1.6 (a)) [C7/device-revoke] | the surface |
| 10 | CO-DK, CO-BB | CO → DK, CO → BB | audit append inside a bbolt write | `device_event` `device.revoke` (reason `user` or `admin`); `device_record` without `device_pubkey`, with `revoked_at` and `revoke_reason` | the core | Under the (user, device) lock (DEFAULT 48): the line first, then in the same bbolt write the key deleted and the record marked revoked (credential §7.2 step 1) | the line cannot be written: refused, 500 `internal_error`, nothing changed | the audit log [C6/event-device-revoke] |
| 11 | CO-RD | CO → RD | a Redis command | delete Part 2 | the core | credential §7.2 step 2. Redis not answering: the revocation still succeeds once bbolt has committed and the logins have ended, and the failure is logged loudly; the Part 2 left behind is unreachable and expires by its TTL (DEFAULT 48) [C7/revoke-redis-down-succeeds] | — | the core's log |
| 12 | — | CO → CO | — | → login (the one login-ending function) | the core | Under `policy.yaml` `devices.revocation` `end_logins`, the default and the value when absent: every login tied to the device ends — its cookie is void (401 from then on), its vault unlock purged, its unredeemed tickets voided, its alert streams ended — and its attached clients close with **4015 `device_revoked`**, which forbids auto-reattach; the terminal sessions live on (credential §10.2; Q8 (a)) [C7/revoke-default-ends-logins]. Under `next_connection`: the logins stay, and each one's next create on a new connection is refused 403 `device_not_trusted` at the allowlist check, a reuse still opening (5.40c) [C7/revoke-switch-next-connection]. An unlock or regeneration in flight cannot install afterwards [C7/revoke-inflight-unlock-not-installed]; the revoked device's challenges fail and Part 2 is never regenerated for it, even with the right passphrase [C7/revoked-device-challenge-fails; C7/part2-regenerate-revoked-refused] | — | the device's logins' surfaces (401) and attached clients (4015) |
| 13 | SF-CO | CO → SF | HTTPS | 204 | the core | — | — | the revoking surface |
| 14 | CO-BB, CO-DK | BB → CO, CO → DK | bbolt read; audit; bbolt write | **the inactivity sweep**, on the vault lock's ticker | the core | A device whose `last_used_at` is older than `policy.yaml` `devices.inactive_after` (0 off) is revoked as steps 10–12 revoke one, reason `inactive`, with no actor (credential §7.1; DEFAULTs 15, 60) [C6/event-device-revoke] | — | the audit log |

Requirements, by step:

- 1, the challenge — as its six points say, each with its fixture.
- 2–6 — The core MUST store a device and its Part 2 only after the halves open the set, MUST count a failed
  registration, MUST tie an untied registering login and hold an unlock on it [C7/device-register;
  C7/register-proves-passphrase; C7/register-needs-init; C7/register-ties-login; C7/register-holds-unlock].
- 7 — The core MUST tie a login at most once [C7/bind-once; C1/login-bind].
- 8 — The core MUST NOT return a device key in any answer [C7/device-list-shape].
- 9–13 — The core MUST write the line first and refuse the revocation when it cannot; MUST delete the key and mark the
  record in one bbolt write; MUST end the device's logins and close their clients 4015 under the default policy, or
  refuse their next new connection under the switch [C7/revoke-default-ends-logins; C7/revoke-switch-next-connection;
  C7/revoke-redis-down-succeeds; C7/revoke-inflight-unlock-not-installed].
- 12, consumers — A client closed 4015 MUST NOT auto-reattach (conventions V3); a surface whose requests answer 401
  logs in again. Owed.
- 14 — The core MUST revoke a device past the inactivity bound. Owed beyond the event's shape (phase 8's sweep test).

## Refusals and who is told

- `device_not_trusted` is 403 and the only code on which a browser drops its key; `challenge_failed` is 409, one code
  for every other challenge failure; `device_not_found` is 404 and also answers another principal's device; none is
  401. An act whose audit line cannot be written is refused as a login is (500).
- A revocation tells the device's logins' surfaces by the 401 they get from then on and the attached clients by 4015;
  an E3 client meets 4015 as an unknown 4xxx, which it reads as 4010 (README "Senders and receivers"). The audit log
  holds every registration and revocation, with who and why.

## Security considerations

- **Non-extractability is the browser's promise.** The core cannot tell a non-extractable browser key from a software
  key, and accepts any valid P-256 key; every unlock still needs the passphrase (DEFAULT 58; N1 item 3).
- **Revocation stops the device, not a person holding the login password.** Such a person can still log in elsewhere,
  reattach to the principal's live sessions and open new shells on connections already up (a reuse sends no
  credential). That is credential §7.3's case: revoke, and change the password (at E4 outside TSB: accounts stay
  seeded) (DEFAULT 40; N1 item 7). The protocol, the UI copy and the tests claim no more than that.
- **One storage slot per TSB user on a browser profile**, so two users can trust one profile; each one's sealed Part 1
  then sits where the other could test passphrase guesses offline, the exposure one user already has (credential §6.1;
  DEFAULT 57; N1 item 4).
- **The device proof guards the unlock only**; the cookie stays unbound (→ vault-unlock, Security considerations).
- **`device_id` is not a secret** (credential §4.2): it names the device and salts `storage_key`, and stops no attacker
  who holds bbolt.

## Open items

1. **The facts behind `device_id`** and their canonical form are the vault client's plan (U8c, OPEN-14.3); the contract
   fixes the digest and the wire form only [C7/chain-device-id].
2. **A trusted TUI** and the desktop surface's key storage (credential §10.4) are designed later; at this release the
   TUI on the broker box is an untrusted-mode surface (DEFAULT 64).

## Picture

pending
