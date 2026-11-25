# vault-lock — the core purges a user's vault unlocks on idle, at the hard cap, or on "lock now"

| | |
|---|---|
| **ID** | vault-lock |
| **Purpose** | Keep a vault unlock only while its user is active and never past the hard cap: one last-active time per user across all their surfaces, the activity signal, "lock now", and the purge. |
| **Status** | E4 (the vault release) |
| **Corpus clauses** | credential §5.5, §10.2, §3.2; Storage §10.2 (the user's own lock settings, a later layer); Frontend §10.4 |
| **Seams** | C1 |
| **Schemas** (this release) | `openapi/tsb.openapi.json`: `POST /auth/activity`, `POST /auth/lock`, `#/components/schemas/LockSettings` (on `Principal` and `Me`); `config.schema.json#/properties/locks`; `error.schema.json` `x-codes` `vault_locked` |

credential = `srs/credential-protection-model.md`. Fixtures: `<seam>/<name>` under `contract/fixtures/`.

**Words used here.** An **unlock** is what one login holds after a passphrase entry (→ vault-unlock). The **vault
lock** is the core's: lock on idle (yes or no), the vault idle timer, and the hard cap, the three knobs of credential
§5.5 (`config.yaml` `locks.*`). A user's **last-active time** is one clock per user in the core. The **resolver** is the
core's one reader of lock settings per user (credential §5.5, "Per-user settings"); today it returns the global
`config.yaml` values.

## Path

SF-CO → (CO)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | — | CO → CO | — | → byte-relay | the core | The user's typing into any of their sessions (client input bytes) moves the user's last-active time | — | — |
| 2 | SF-CO | SF → CO | HTTPS | `POST /auth/activity` → 204 | the principal | Moves the user's last-active time; the core counts at most one per login per `locks.activity_interval` and ignores the rest, never refusing one (DEFAULT 34). A surface sends it while its user is active, at most at the interval `LockSettings.activity_interval` gives [C1/activity-signal; C1/activity-signal-rate] | 401 `unauthenticated` | — |
| 3 | — | CO → CO | — | — | the core | A successful unlock or Part 2 regeneration moves the user's last-active time (DEFAULT 32) | — | — |
| 4 | — | CO → CO | — | the idle purge | the core | With lock on idle, once the user has been idle for the vault idle timer, the core purges every unlock the user holds, from every login (credential §5.5) [C7/vault-lock-idle-two-logins] | — | the user's next create on a new connection gets 409 `vault_locked`; when the user is the storing administrator of a `user_unlocked` org credential and no login of theirs holds an unlock any more, a grantee's next create on a new connection with it gets 409 `org_credential_locked` (credential §8.2) |
| 5 | — | CO → CO | — | the hard cap | the core | Each unlock is purged at the hard cap counted from the passphrase entry that made it, however active the user and whether or not lock on idle is on; a trusted device locks too [C7/vault-lock-hard-cap] | — | as step 4 |
| 6 | SF-CO | SF → CO | HTTPS | `POST /auth/lock` → 204 | the principal | **Lock now**: every unlock the caller's user holds is purged, from any of its logins; the calling client locks its own screen as well (→ screen-lock) (M7) [C1/lock-now; C7/lock-now-purges] | 401 `unauthenticated` | the calling surface |
| 7 | — | CO → CO | — | the purge | the core | An unlock past the idle timer or the hard cap is refused at its next use and swept within one second of the bound (DEFAULT 31). The purge reaches `encryption_key`, master_hash and any decrypted material. An established session is untouched (credential §10.2) [C7/lock-now-purges] | — | — |

Requirements, by step:

- 1–3 — The core MUST keep one last-active time per user, moved by typing, by a counted activity signal and by an
  unlock or regeneration, and MUST NOT refuse an activity signal [C1/activity-signal; C1/activity-signal-rate].
- 4–7 — The core MUST purge every unlock of an idle user (lock on idle) and each unlock at its hard cap, refuse it at
  use past either bound, and leave every established session running [C7/vault-lock-idle-two-logins;
  C7/vault-lock-hard-cap; C7/lock-now-purges].
- Settings — The core MUST read the lock settings only through its per-user resolver and MUST send the caller's on
  `POST /auth/login` and `GET /auth/me` (`locks`) [C1/principal-lock-settings; C1/lock-settings-shape]. Config load
  MUST refuse a vault idle timer above the hard cap, and a zero or negative duration (config.schema.json; graded by
  U5's tests, owed) [C5/config-e4-knobs].

## Refusals and who is told

- The activity signal and "lock now" refuse nothing but a missing login (401). A purge tells nobody at once: the next
  create that needs this login's unlock is answered 409 `vault_locked`, and the surface asks for the passphrase; the next
  create on a `user_unlocked` org credential whose storing administrator no longer holds an unlock on any login is
  answered 409 `org_credential_locked`, and the surface says the administrator must unlock, never asking for the
  operator's passphrase (credential §8.2).

## Security considerations

- **The signal is a guardrail; the hard cap is the guarantee** (credential §5.5): a surface that reports activity falsely
  keeps the vault open no longer than the hard cap.
- **Idle is per user, an unlock per login**: activity on any surface of the user keeps every unlock of the user, and
  idle on all of them purges them all.
- **The vault lock never touches an established session**: no attach, replay, reattach, reuse or core restart reads the
  vault (credential §10.2).

## Open items

1. **The resolver's later layers** — an administrator's per-user value in `policy.yaml` (which may be longer) and the
   user's own shorter value in `defaults.yaml` (Storage §10.2) — are later items; at this release the resolver returns
   the global values.

## Picture

pending
