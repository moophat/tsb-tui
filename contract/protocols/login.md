# login — TSB's own login

| | |
|---|---|
| **ID** | login |
| **Purpose** | Prove who a principal is with TSB's own password login, hold the resulting login session as a cookie, and end it. |
| **Status** | built (E0; the cross-origin refusal declared at contract-v0.2, E1). Grows E4b (account-administration, disabled login and open item 6); E4 (the vault release): steps 13–17, the login knobs, the resolved lock settings. |
| **Corpus clauses** | Contract §1, §2, §4, §13; Frontend §8 (surface attachment), §10.2, §10.3 (post-v1); Storage §5, §6, §9; credential Amendment 2026-09-14, §3.2, §5.5, §7.2, §11.1 (E4 steps) |
| **Seams** | C1, C6 |
| **Schemas** (contract-v0.3) | `openapi/tsb.openapi.json`: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, the top-level `security`, `#/components/securitySchemes/session`, `#/components/schemas/LoginRequest`, `#/components/schemas/Principal`, `#/components/schemas/Me`, `#/components/responses/CrossOrigin`, `#/components/responses/Unauthenticated`, `#/components/responses/InvalidRequest`; `error.schema.json` `x-codes`; `audit-event.schema.json#/$defs/auth_event`; `common.schema.json#/$defs/username`; from the vault release `#/components/schemas/LockSettings` (on `Principal` and `Me`), `POST /auth/bind`, `POST /auth/screen-unlock`, `config.schema.json#/properties/auth`, `ws-frames.schema.json#/x-close-codes` 4015 |

Words used here. A **principal** is a TSB login name (conventions V9). A **login session** is the state the core
keeps from a successful `POST /auth/login` until logout or expiry ("the login session on the server", OpenAPI
`POST /auth/logout`). The **cookie** `tsb_session` is the opaque string that names one login session; the surface
sends it back on each request. Its attributes: **HttpOnly** (page script cannot read it), **Secure** (sent only over
HTTPS), **SameSite=Strict** (a browser never attaches it to a request another site starts). The **Origin header** is
the browser's statement of which site a request comes from. An **audit line** is one JSON object appended to the
audit log on disk (Storage §6). **bbolt** is the core's embedded database file (Storage §5). The **alert stream** is
`GET /alerts`, a long-lived HTTPS response of operator alerts (operator-alerts). Fixtures are cited as `<seam>/<name>`, under `fixtures/`.

## Path

login: SF-CO → CO-BB → CO-DK → SF-CO · use: SF-CO · logout: SF-CO → (operator-alerts) → SF-CO · expiry: (operator-alerts) ·
E4: bind: SF-CO → (device-trust) · re-check: (screen-lock) · revocation: (device-trust) → SF-CO · logout and expiry: (vault-unlock)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `POST /auth/login`, body `LoginRequest` | anyone: the operation's `security` is empty, no cookie needed | none yet | 403 `forbidden` (`CrossOrigin`) when the Origin header names another origin, before any credential is read; 422 `invalid_request` (`InvalidRequest`) when the body is not a `LoginRequest` | the surface |
| 2 | — | CO → CO | — | the failed-login limit | the core | unspecified: what counts as too many failed logins, over what window, keyed by what (open item 3) | 429 `rate_limited`, "too many failed logins; try later" (error `x-codes`) | the surface |
| 3 | CO-BB | CO → BB | bbolt read | the account: `users.schema.json`, username, password hash and disabled flag (E4b; Storage §5) | the core | none | — | — |
| 4 | — | CO → CO | — | the password check | the core | a verdict; an unknown name, a wrong password and a disabled account are one outcome; disabled performs the same hashing work [C1/account-disabled-login-401] | 401 `invalid_credentials`, no cookie (step 6) | — |
| 5 | CO-DK | CO → DK | file append (audit JSONL) | `auth_event`: `auth.login`, or `auth.login_failed` naming the attempted name; from E4b a disabled account has `detail.reason: disabled` [C6/event-login-failed-disabled] | the core | one audit line | — | the audit log (Storage §6) |
| 6 | SF-CO | CO → SF | HTTPS | 200 `Principal` with `Set-Cookie: tsb_session` (HttpOnly, Secure, SameSite=Strict, Path=/); or 401 `invalid_credentials` with no cookie | the core | on 200, a login session exists, named by the cookie. From the vault release `Principal` carries `locks`, the lock settings that apply to the principal now, from the core's per-user resolver (H5 (b)) [C1/principal-lock-settings] | — | the requesting surface only |
| 7 | SF-CO | SF → CO | HTTPS | any operation under the top-level `security` (all but `GET /healthz`, `POST /auth/login`, `GET /ws/attach`), carrying the cookie; `GET /auth/me` → `Me`: the `Principal`'s `username` and, from the release that adds `SessionCreateRequest.login`, `recording.login_input` (session-open step 22), and from the vault release `locks` [C1/principal-lock-settings], and optional `permissions`, the principal’s granted permission names whatever its filters [C1/me-permissions] | whoever presents the cookie acts as its principal | the core resolves the cookie to its principal; what that principal is allowed to do is `policy.yaml`'s (Storage §9) and each protocol's; the attach ticket is minted under → session-attach | 401 `unauthenticated` (`Unauthenticated`) without a valid cookie | the surface |
| 8 | SF-CO | SF → CO | HTTPS | `POST /auth/logout`, carrying the cookie | the cookie's principal | none yet | 403 `forbidden` (`CrossOrigin`), before the login session ends; 401 `unauthenticated` | the surface |
| 9 | — | CO → CO | — | end the login session | the core | the cookie is invalid from now on, whoever presents it. Login sessions live in the core's memory (open item 2, settled), so a core restart ends every one [C1/core-restart-ends-logins] From Release B the core also ends the principal's device logins under this login (device-login, how a device login ends): each gets `disconnect`, `device_login_result` `cancelled` and close 4016, and gives its cap place back [owed] | — | — |
| 10 | SF-CO | CO → SF | HTTPS | → operator-alerts: that login session's alert stream, if open, ends | the core | stream closed | — | the surface holding the stream |
| 11 | SF-CO | CO → SF | HTTPS | 204, cookie cleared (OpenAPI `logout` summary) | the core | none | — | the surface; no audit line (the `auth_event` actions name no logout) |
| 12 | — | CO → CO | — | → operator-alerts | the core | the login session expires at the first of `config.yaml` `auth.login_lifetime` (from the login) and `auth.login_idle` (since its last request) (open item 1, answered: DEFAULT 36), and its alert stream ends as in step 10 [C5/config-e4-auth-knobs] Expiry ends the principal's device logins under it the same way as step 9 [owed] | — | later requests get step 7's 401 |
| 13 | — | CO → CO | — | → device-trust step 12 | the core | A revocation of the device a login is tied to ends that login, under `policy.yaml` `devices.revocation` `end_logins`, the default (credential Amendment 2026-09-14; 5.40c; Q8 (a)): the cookie is void, its vault unlock purged, its unredeemed tickets voided, its alert streams ended, its attached clients closed 4015 `device_revoked`; the terminal sessions live on. Under `next_connection` the login stays and its next new connection is refused [C7/revoke-default-ends-logins; C7/revoke-switch-next-connection] A device login under such a login ends with close 4015 and no result frame: no session exists, so 4015's "the session lives on" has nothing to apply to [owed] | — | the device's logins' attached clients (4015) and surfaces (401 from then on) |
| 14 | — | CO → CO | — | → vault-unlock step 10 | the core | Logout and expiry purge the login's vault unlock (credential §11.1, "Clear memory on logout"): `encryption_key`, master_hash and any decrypted material [owed: U2's and U6's tests, phase 16] | — | — |
| 15 | SF-CO | SF → CO | HTTPS | → device-trust step 7: `POST /auth/bind` | the principal, an untied login | On a trusted device, after the password login, a signed `login.bind` challenge ties the login to the device, once; a registration made by an untied login ties it too. An untied login is an untrusted-mode login (DEFAULT 39) [C1/login-bind; C7/bind-once] | 403 `device_not_trusted`; 409 `challenge_failed` | the surface |
| 16 | SF-CO | SF → CO | HTTPS | → screen-lock: `POST /auth/screen-unlock` | the principal | The screen lock's password re-check shares step 2's failed-login limiter, so its terms (open item 3) govern both doors (DEFAULT 67) [C1/screen-unlock-limiter] | 403 `screen_unlock_failed`, never 401; 429 `rate_limited` | the surface |
| 17 | SF-CO | SF → CO | HTTPS | → vault-lock: `POST /auth/activity`, `POST /auth/lock` | the principal | the activity signal and "lock now" ride the login | 401 `unauthenticated` | the surface |
| 18 *(E4b)* | — | CO → CO | — | → account-administration steps 5–6 | the core | Disable and administrator password set end every affected login; own change keeps its caller and ends the others. Before the successful account response is sent, the cookie is void, vault unlock purged, unredeemed tickets voided, alert streams ended, device logins ended and attached clients closed 4017 `account_login_ended`. A device-login socket sends no result frame; no session exists yet. Terminal sessions live on [C1/account-disable; C1/account-password-set; C1/account-password-change-own; C2/account-login-ended-close-4017; C2/account-device-login-ended-close-4017]. | — | affected clients (4017) and surfaces (401 from then on) |

The contract does not order steps 5 and 6 (open item 4).

Requirements, by step:

- 1 — The core MUST refuse `POST /auth/login` and `POST /auth/logout` whose Origin header names another origin with
  403 `forbidden`, before it reads a credential or ends a login session (`CrossOrigin`). Graded by
  `C1/auth-cross-origin-403` (reference: the core's own test, required-green e1).
- 1 — The core MUST refuse a login body that is not a `LoginRequest` with 422 `invalid_request`. Owed.
- 2 — A login the core refuses for too many failed logins MUST be answered 429 `rate_limited`. Owed.
- 4, 6 — The core MUST answer an unknown name, a wrong password and, from E4b, a disabled account alike, 401 `invalid_credentials`, and MUST NOT set
  a cookie on that answer (OpenAPI `/auth/login` 401). Graded by `C1/auth-login-bad-password` for the wrong
  password; the unknown name is owed. E4b disabled accounts: [C1/account-disabled-login-401].
- 5 — The core MUST write `auth.login` for a login it accepts and `auth.login_failed`, naming the attempted name, for
  a password check it refuses (audit-event top; `C1/auth-login-bad-password` description). The line's shape is
  graded by `C6/event-vocabulary`; that each login yields its line is owed.
- 6 — On a correct name and password the core MUST answer 200 with the `Principal` and set `tsb_session` HttpOnly,
  Secure and SameSite=Strict. Graded by `C1/auth-login-ok`.
- 7 — The core MUST answer 401 `unauthenticated` to any operation under the top-level `security` that arrives
  without a valid cookie, and MUST answer `GET /auth/me` with the cookie's principal. Graded by
  `C1/session-create-unauthenticated-401`, `C1/alerts-unauthenticated-401`, `C1/output-page-401` (required-green
  e5) and `C1/auth-login-ok` (step 2).
- 8 — The core MUST answer `POST /auth/logout` without a valid cookie with 401 `unauthenticated`. Owed.
- 9, 11 — The core MUST end the login session on the server, so the cookie is refused afterwards whoever presents
  it, and answer 204. Graded by `C1/auth-logout`.
- 10, 12 — Ending the alert stream on logout and on expiry is operator-alerts's requirement, graded by `C1/alerts-stream`
  (reference, required-green e1).
- Consumer — A surface MUST hold the cookie in memory only and write it nowhere (`#/components/securitySchemes/session`;
  Frontend §8, §10.3). Owed: no fixture can see where a client keeps it.

- 12 — The core MUST end a login at the first of `auth.login_lifetime` and `auth.login_idle` [owed: U2's tests,
  phase 16].
- 9 — A cookie from before a core restart MUST be refused afterwards [C1/core-restart-ends-logins].
- 13 — Under the default policy a revocation MUST end the device's logins and close their attached clients 4015
  [C7/revoke-default-ends-logins].
- 6, 7 — The core MUST send `locks` on the login answer and on `GET /auth/me` [C1/principal-lock-settings].

## Refusals and who is told

- The 401 on a login does not say whether the account exists or is disabled (OpenAPI `/auth/login` 401; error `x-codes`
  `invalid_credentials`).
- A refused password check writes `auth.login_failed`. Whether a 403, 422 or 429 refusal writes an audit line is
  unspecified.
- Every refusal body is `error.schema.json`; its `code` is the contract and a surface that meets an unknown code
  acts on the HTTP status (conventions V4). No message names anything of the host (conventions V11).
- Nobody but the requesting surface learns of a login, a refusal or a logout while it happens: no frame or stream in
  the contract announces one. The audit log holds the logins and refusals.
- Logout ends no terminal session: no end reason names it (`audit-event.schema.json#/$defs/session_end`; session
  model §12). What logout does to the principal's attached WebSocket clients and unused tickets is unspecified
  (open item 5); a revocation's end of a login, by contrast, closes that login's attached clients 4015 (step 13).
- A refused screen-lock re-check writes `auth.screen_unlock_failed` (screen-lock step 5).

## Security considerations

- **The cookie.** HttpOnly, Secure, SameSite=Strict, Path=/ (OpenAPI `POST /auth/login` 200 `Set-Cookie`). It is
  bound to no installation at v1 (`#/components/securitySchemes/session`; Frontend §10.3, post-v1), so whoever holds
  it acts as the principal until logout or expiry; "a copied cookie dies with the logout" (`C1/auth-logout`). TSB's
  own surfaces are therefore direct topology, and an input prefilter on one is a guardrail, never a control
  (Contract §13; Frontend §8, §10.2).
- **Same origin.** The Origin check is declared on `POST /auth/login` and `POST /auth/logout` only (`CrossOrigin`;
  error `x-codes` `forbidden`): a page elsewhere cannot log a browser in or out. Every other cookie-authenticated
  request relies on SameSite=Strict, which keeps the cookie off requests another site starts (`C1/auth-login-ok`
  description). A request with no Origin header is not refused by this rule: `C1/auth-login-ok` logs in without
  one. The WebSocket takes no cookie; its one-time ticket is session-attach's.
- **The limiter.** The contract declares only the 429 `rate_limited` answer. The corpus's "5 attempts per hour"
  (credential §11.1, Appendix B) limits vault passphrase attempts (vault-unlock step 5), not this login; the screen
  lock's re-check shares this login's limiter, never the passphrase's.
- **CSRF.** The built measures are the CSRF measures: the SameSite=Strict cookie and the Origin check (Q10 (ii); N1 item
  12 words credential §11.1 so). Every vault, device and lock write is in the core's same-origin route group; a GET
  writes nothing (the salt read included).
- **The device proof guards the vault unlock only**: after it, the cookie alone uses the unlock until the vault locks;
  the cookie stays unbound (decision 5.41; DEFAULT 41).
- **Transport.** The core sits behind a TLS-terminating proxy (OpenAPI `servers`); Secure keeps the cookie off plain
  HTTP. The reference client refuses `http://` except to a loopback host (contract README "Client").
- **The host boundary.** A login grants sessions and targets, never a shell, file or configuration of the TSB host
  (Contract §4). The TUI logs in like any other consumer: there is no privileged path (Contract §2). `Principal` is
  a closed shape naming the principal only, and refusal messages carry no host detail (conventions V11). A login
  name is path-safe because it becomes a directory name (`common.schema.json#/$defs/username`; Storage §10.2).

## Open items

1. **Login session lifetime** — *answered by the vault release:* two knobs, `auth.login_lifetime` and `auth.login_idle`;
   a login ends at the first (DEFAULT 36; step 12).
2. **A core restart** — *settled by the corpus, 2026-10-28:* login sessions live in the core's memory, so a core
   restart ends every one (credential §3.2; Storage §5 no longer lists web sessions in bbolt). The contract requires
   only that logout invalidates the cookie on the server; the restart end's fixture is C1/core-restart-ends-logins.
3. **The limiter's terms.** What counts as a failed login, the threshold and window, whether it keys on the name or
   the client, and whether a correct password is refused while limited.
4. **Audit and answer order.** Whether the audit line is written before the 200 or 401 goes out, and whether a login
   whose `auth.login` line cannot be written is refused.
5. **Logout and attachments.** What logout does to the principal's attached WebSocket clients and to tickets minted
   under the login session (→ session-attach; the ticket is bound to principal and session, CHANGELOG contract-v0.1 C1).
6. **Accounts** — *first half answered at E4b:* account-administration creates accounts and changes their passwords,
   disables and enables them. Disable and administrator password set end every login; own change keeps the caller
   and ends the others. The end voids the cookie, unlock, tickets, alert streams, device logins and attached clients
   (close 4017), while terminal sessions run on [C1/account-disable; C1/account-password-set;
   C1/account-password-change-own; C2/account-login-ended-close-4017]. An account with no policy block can log in
   and holds no permissions [C1/account-create]. Whether a policy reload removing a principal ends its login
   sessions remains OPEN, as do the already-attached-client effects in config-reload open item 1.
7. **Session fixation.** Credential §11.1's MVP checklist lists "Session fixation protection"; the contract does not
   say a login always gets a fresh cookie, and no ruling says whether it must. The checklist's "CSRF tokens" half is
   answered: the SameSite=Strict cookie and the Origin check are the CSRF measures (Q10 (ii); Security
   considerations).
8. **The broker's own origin.** How the core knows the origin it compares the Origin header with, behind its
   TLS-terminating proxy, is unspecified.

## Picture

pending
