# account-administration — manage TSB login accounts

| | |
|---|---|
| **ID** | account-administration |
| **Purpose** | An account administrator manages login accounts and every principal may change its own password. |
| **Status** | E4b |
| **Corpus clauses** | Contract §1, §2, §4, §13; Storage §5, §6, §9; credential-protection §5.5, §7.2, §7.3, §8.2; External Study §1.3 |
| **Seams** | C1, C2, C5, C6 |
| **Schemas** | `openapi/tsb.openapi.json` account paths and schemas; `schemas/users.schema.json`; `schemas/policy.schema.json`; `schemas/config.schema.json`; `schemas/ws-frames.schema.json`; `schemas/error.schema.json`; `schemas/audit-event.schema.json` |

Paths are relative to `contract/`. Fixtures are under `fixtures/`.

## Path

SF-CO → CO-BB → CO-DK → CO-BB → SF-CO; an account act may end attached clients on SF-CO with close 4017.

List follows steps 1–2 and returns 200 `AccountList`, ordered by username ascending, with no audit or write.
Create checks the name's absence in step 2. An already-disabled disable returns 204 immediately after step 2, touching no audit, record or login;
all other state refusals also stop there. Only successful mutating acts perform steps 3–6. The current-password
proof is not subject to the newly set password minimum.

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `GET /accounts`, `POST /accounts`, `POST /accounts/{username}/disable`, `POST /accounts/{username}/enable`, `POST /accounts/{username}/password`, `POST /auth/password` | account.admin for the first five; any authenticated principal for own change | Every write authenticates, then checks Origin before account or request-input reads, requires account.admin for administrator operations before account lookup, and validates the username and password request. | 401 `unauthenticated`; foreign Origin or missing account.admin 403 `forbidden`; invalid shape, reserved `shared`, non-UTF-8, empty, >1024 or below `auth.account_password_min_length` 422 `invalid_request` | requesting surface |
| 2 | CO-BB | BB → CO | bbolt transaction | `users.schema.json` account lookup; core authorization and state checks | core | account.admin is checked per request. Own change proves the current password using the login failed-login limiter. For a mutating act, all state checks run in the same bbolt write transaction as steps 3–4: check and set are one indivisible operation. Own change replaces only the hash it proved against. | unknown target 404 `user_not_found`; duplicate 409 `user_exists`; self-disable 409 `account_self_disable`; enable already enabled 409 `account_already_enabled`; administrator set-password on disabled 409 `account_disabled`; wrong own current password 403 `password_change_failed`, never 401; past limiter 429 `rate_limited` | requesting surface |
| 3 | CO-DK | CO → DK | append-only audit JSONL | `account_event` | core | For create, disable, enable, administrator password set and owner password change, write the event inside the bbolt write transaction before committing the record, following the vault audit precedent. The event names account (`user`) and actor and never a password or hash. | unwritable audit 500 `internal_error`; nothing commits. Refusals have no account event; wrong current-password proof writes `auth.password_change_failed`, naming the user. | audit log and caller on failure |
| 4 | CO-BB | CO → BB | bbolt write | `users.schema.json` record | core | Create writes enabled with a hash. Disable empties the legacy hash and sets disabled. Enable writes a new hash and clears disabled. Administrator password set changes an enabled account's hash; own change writes the proved account's new hash. | state refusals are checked in step 2 inside this same transaction, before any audit or write | — |
| 5 | — | CO → CO | — | end affected logins | core | Before the successful response is sent, disable and either new password operation complete ending every affected login: cookie, unlock, tickets, alert streams, device logins and attached clients. Terminal sessions, trusted devices, vault, credentials and grants remain. Own change ends every other login, not the caller's. A vault unlock in flight cannot install after its login ends. | — | affected surfaces; sessions remain |
| 6 | SF-CO | CO → SF | HTTPS / WebSocket | 201 `AccountCreated`, 204, or close 4017 | core | Create reports policy permissions already held by the name as `GET /auth/me` would. A client whose login ended MUST NOT auto-reattach after 4017 [C2/account-login-ended-close-4017]; an older client reads it as 4010. A device-login socket closes 4017 without a result frame; no session exists yet [C2/account-device-login-ended-close-4017]. | — | requesting surface |
| 7 | SF-CO | SF → CO | HTTPS | `POST /auth/login` with a disabled account | anyone | Disabled accounts perform the same password hashing work as a wrong password and answer 401 `invalid_credentials`, with no cookie. `auth.login_failed` detail.reason is `disabled`. | 401 `invalid_credentials` | requesting surface |

Requirements (fixtures are reference, provider tests owed):

- The core MUST require a valid cookie on every operation [C1/account-list-unauthenticated-401;
  C1/account-create-unauthenticated-401; C1/account-disable-unauthenticated-401;
  C1/account-enable-unauthenticated-401; C1/account-password-set-unauthenticated-401;
  C1/account-password-change-own-unauthenticated-401]. It MUST check `account.admin` on the five administrator
  operations, before account lookup, with no record write or audit on refusal [C1/account-list-forbidden-403;
  C1/account-create-forbidden-403; C1/account-disable-forbidden-403; C1/account-enable-forbidden-403;
  C1/account-password-set-forbidden-403]. Every valid login may perform own change without that permission
  [C1/account-password-change-own].
- The core MUST list only `AccountSummary` fields, ordered by username ascending [C1/account-list]; create an enabled account and report the
  name's already granted permissions [C1/account-create; C1/account-create-existing-permissions]; refuse a reused
  name [C1/account-create-name-exists-409; C1/account-create-disabled-name-exists-409]. Names have the shared
  username shape; reserved `shared` is case-insensitive, following the existing host seed's refusal
  [C1/account-name-shape-422; C1/account-name-reserved-422]. The core MUST enforce the new-password minimum and
  the existing 1024-character maximum and UTF-8 rule [C1/account-password-too-short-422;
  C1/account-password-too-long-422; C1/account-password-utf8-422; C1/account-password-empty-422].
- The core MUST implement the three ruled transitions: already-disabled disable returns 204 with no mutation,
  audit or login end [C1/account-disable-already-disabled]; already-enabled enable refuses with 409
  `account_already_enabled`, never setting its password [C1/account-enable-already-enabled-409]; administrator
  password set on disabled refuses 409 `account_disabled` with no audit or change [C1/account-password-set-disabled-409].
  It MUST refuse self-disable [C1/account-disable-self-409] and unknown target names [C1/account-disable-unknown-404;
  C1/account-enable-unknown-404; C1/account-password-unknown-404].
- The core MUST perform step 2's state checks inside the same bbolt write transaction as the write: two
  concurrent creates of one name leave only one record and the second answers 409 `user_exists`
  [C1/account-create-concurrent-name]. Neither administrator password set nor own change may overwrite a
  disabled account's voided hash when racing a disable [C1/account-password-set-racing-disable;
  C1/account-password-change-racing-disable]. Own change MUST replace only the hash it proved against
  [C1/account-password-change-proof-replaced]. No vault unlock in flight may install after its login ends
  [C7/account-inflight-unlock-not-installed]. These follow the device-login cap's single check-and-set and
  device-trust's in-flight unlock protection.
- The core MUST void the stored hash on disable; enable MUST set a new password, never restore the old one
  [C1/account-disable; C1/account-enable; C6/users-record]. A pre-E4b record MUST load enabled with its hash
  unchanged [C6/users-old-record-loads-enabled]. New writes carry disabled explicitly. Hashes are argon2id PHC,
  following the existing seed, and disabled writes use the empty string. A pre-E4b core cannot log a disabled
  account in: its hash parser returns `ErrHashFormat`, so the login answers 500 rather than 401. This rollback
  guarantee does not cover a deploy rollback whose old `manage.sh` re-seeds a seeded login: that seed writes a
  fresh hash and drops the disabled flag, reopening the login with its file password.
- The core MUST complete ending every affected login before sending the successful response: cookie void, vault unlock purged,
  unredeemed session and device-login tickets void, alert streams ended, device logins in progress ended through
  the existing end-login path (disconnect their workers and release their cap places), attached clients close
  4017 (a device-login socket sends no result frame), terminal sessions continue [C1/account-disable;
  C1/account-password-set; C2/account-login-ended-close-4017; C2/account-device-login-ended-close-4017].
  The own-change caller's login and everything it holds MUST stay; all its other logins MUST end the same way
  [C1/account-password-change-own]. Administrator set-password on oneself MUST end the caller too
  [C1/account-password-set-self]. Trusted devices, vault records, credentials and grants MUST stay untouched
  [C1/account-disable; C1/account-password-set; C1/account-enable; C1/account-password-change-own].
- Own change MUST prove the current password through the login failed-login limiter, answer a wrong proof 403
  `password_change_failed` and write `auth.password_change_failed` naming the user; a limited request answers
  429 `rate_limited`. Neither changes the record or logins
  [C1/account-password-change-wrong-current-403; C1/account-password-change-limited-429;
  C6/event-password-change-failed]. The limiter's still-open
  terms remain login open item 3's; this protocol does not select them.
- The core MUST write each successful mutating API act's one `account_event` before committing, naming target account
  as `user` and caller as `actor`, never a password or hash [C6/event-account-create; C6/event-account-disable;
  C6/event-account-enable; C6/event-account-password-set; C6/event-account-password-changed]. An unwritable audit
  line MUST answer 500 `internal_error` and commit nothing, leaving logins standing [C1/account-create-audit-unwritable-500;
  C1/account-disable-audit-unwritable-500; C1/account-enable-audit-unwritable-500;
  C1/account-password-set-audit-unwritable-500; C1/account-password-change-own-audit-unwritable-500].
- Host recovery, with the core stopped, re-passwords and re-enables an account and MUST write `account.recover`
  with `user` naming the recovered account and `actor` absent. Seed replace mode MUST write the same event when
  it replaces a disabled rig login and re-enables it: seed replace is a host act, like recovery
  [C6/event-account-recover]. Neither event carries a password or hash.
- Every account write MUST authenticate first, then refuse a foreign Origin before account or request-input reads [C1/account-cross-origin-403].
  Passwords MUST travel only in request bodies, never logs, audit, echoes or storage except as a hash
  [C1/account-password-never-echoed]. A disabled login MUST answer exactly like wrong credentials, with the same
  hashing work and no cookie, and write `auth.login_failed` with `detail.reason: disabled`
  [C1/account-disabled-login-401; C6/event-login-failed-disabled].

The default new-password minimum is 15 characters, following
[NIST SP 800-63B-4](https://doi.org/10.6028/NIST.SP.800-63B-4) §3.1.1.2 item 1: single-factor passwords have a
minimum of 15 characters.
The schema range is 1–1024; the minimum knob cannot exceed the login maximum. A changed value is a non-timer
configuration change and follows config-reload step 2's restart rule. Existing passwords are not rechecked.

## Refusals and who is told

Refusals are checked in this order: 401 for no valid cookie; 403 for foreign Origin; 403 for the missing `account.admin` permission or own-change gate; 422 for input shape and name/password rules; 404 for an unknown name; 409 for account state; 403 `password_change_failed` for a wrong current password; then 500 for an unwritable audit line.

Only the requesting surface learns an account response. Names are never reused. Disable is idempotent: a retry against an already disabled account returns 204 without changing the record, ending a login or writing an audit line. Enable is not idempotent: an already enabled account returns 409 `account_already_enabled`; its supplied password is not set. Administrator password set is accepted only for an enabled account; a disabled account returns 409 `account_disabled`. Setting an administrator password on an enabled account is distinct from enable. An administrator cannot disable its own account. A user may change its own password by proving the current password; a wrong proof is 403, never 401.

No error message echoes a supplied name or password. The current-password proof may be shorter than the new minimum: existing passwords are not rechecked. Passwords are UTF-8 request-body values only, never logged, audited, echoed or stored except as a hash. A foreign Origin is rejected before any account read. An account administrator has the power of the strongest login; recovery of a locked-out administrator is a host act, never this API. A disable, an administrator's password set and an own change's purge of the other logins all purge the affected vault administrator's unlocks and stop the user-unlocked organisation credentials they stored (credential-protection §8.2).

## Security considerations

- The permission is as strong as the strongest login: an administrator can set any enabled account's password
  and then act as it. Account administration grants no rights through the API; rights stay in the host's policy.
- A disable, an administrator's password set and an own change's purge of the other logins all stop the vault
  administrator's stored user-unlocked organisation credentials when the affected unlocks are purged
  (credential-protection §8.2). The login password and vault passphrase are separate proofs (§5.5);
  trusted devices, vault, credentials and grants stay on account acts.
- Recovery of a locked-out administrator is a host act, never an API (Contract §4). This protocol adds no host
  command surface. Account operations are public API capabilities with no privileged path (Contract §2).

## Open items

OPEN — The failed-login limiter's threshold, window, keys and correct-password handling remain login open item 3. Whether a policy reload removing a principal ends its logins remains login open item 6; effects on already attached clients remain config-reload open item 1. Private-target consequences remain E3b's seam.

## Picture

pending
