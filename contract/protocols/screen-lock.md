# screen-lock — a client hides its sessions after its user's screen-lock time; the login password resumes it

| | |
|---|---|
| **ID** | screen-lock |
| **Purpose** | Let each client hide session content and block input after its user has been idle there for the screen-lock time, sessions running on, and resume only with the login password, checked by the core. |
| **Status** | E4 (the vault release); enforced by the surface in v1, a guardrail |
| **Corpus clauses** | credential §5.5; Frontend §10.2, §10.4 |
| **Seams** | C1, C6 |
| **Schemas** (this release) | `openapi/tsb.openapi.json`: `POST /auth/screen-unlock` (`ScreenUnlockRequest`), `#/components/schemas/LockSettings` (`screen_lock_after`, on `Principal` and `Me`); `error.schema.json` `x-codes` `screen_unlock_failed`, `rate_limited`; `audit-event.schema.json#/$defs/auth_event` (`auth.screen_unlock_failed`) |

credential = `srs/credential-protection-model.md`; Frontend = `srs/frontend-extensibility-model.md`. Fixtures:
`<seam>/<name>` under `contract/fixtures/`.

**Words used here.** A **client** is one surface instance (one TUI process, one browser tab's shell); the screen lock is
each client's own (OPEN-12, ruled). The **re-check** is the core checking the login password of the login the cookie
names; it neither creates nor ends a login. The **sender** of a headless client is whoever gives it its commands
(a script, a harness, an agent). A **look** is a reading of the screen the client answered its sender with, which a
later typed send names.

**A client with no screen of its own**, such as the TUI driven headless (`tsb-tui drive`), locks like every client.
Once `screen_lock_after` passes with no command from its sender, it voids every look it holds, answers no session
content and sends no typed input until the core re-checks the login password (steps 3–5). It reads that password again
from the file or descriptor its login came from, never from a command, and keeps no copy. After the re-check its sender
looks again before a typed send. The core changes nothing.

## Path

SF → SF → SF-CO → CO-DK

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `POST /auth/login` → `Principal.locks`, or `GET /auth/me` → `Me.locks` | the principal | the surface learns its user's `screen_lock_after`, the value the core's resolver gives, never a local one [C1/principal-lock-settings] | — | the surface |
| 2 | — | SF → SF | — | — | the surface | After `screen_lock_after` seconds with no input from its user (a headless client: no command from its sender), the client hides session content and blocks input, a headless client as the paragraph above says; its sessions run on, and the client stays attached. "Lock now" from this client (→ vault-lock step 6) locks its screen at once | — | the operator; for a headless client, its sender |
| 3 | SF-CO | SF → CO | HTTPS | `POST /auth/screen-unlock` `{password}` | the principal | the core checks the login password of the login the cookie names | 401 `unauthenticated` without a valid cookie; 422 `invalid_request` without a password | — |
| 4 | SF-CO | CO → SF | HTTPS | 204; or 403 `screen_unlock_failed`; or 429 `rate_limited` | the core | 204: the client resumes. A wrong password is 403, **never 401**, so the surface keeps its login and the login keeps its vault unlock (DEFAULT 67) [C1/screen-unlock; C1/screen-unlock-wrong-403]. It is counted by the login's failed-login limiter, the one `POST /auth/login` uses (login step 2), and past it is 429 `rate_limited`; it is never counted by the passphrase limiter [C1/screen-unlock-limiter] | — | the surface |
| 5 | CO-DK | CO → DK | file append (audit JSONL) | `auth_event` `auth.screen_unlock_failed`, naming the principal | the core | a refused re-check is audited as a refused login is [C6/event-screen-unlock-failed] | — | the audit log |

Requirements, by step:

- 1 — The core MUST send `locks` on the login answer and `GET /auth/me` [C1/principal-lock-settings].
- 2 — A surface MUST take the screen-lock time from the core, never from a local value; MUST hide session content and
  block input once it passes; MUST resume only on a 204 from the re-check. A headless client MUST void every look it
  holds when it passes, MUST send no typed input after the 204 until its sender has looked again, MUST read the
  password again from the file or descriptor its login came from, and MUST NOT take it from a command. "Lock now"
  locks a headless client the same way. The passphrase MUST NOT resume a screen lock, and the screen-lock password MUST NOT unlock the
  vault (credential §5.5). Device trust exempts nothing. Graded for the TUI by U7's tests and U11's TUI rig
  (owed; phase 16 names them); for headless drive by U7's drive unit tests and the lab rig's drive-gate step 9 (owed).
- 3–4 — The core MUST answer a wrong password 403 `screen_unlock_failed` and leave the login and its unlock as they
  were, and MUST count it in the login's limiter, not the passphrase's [C1/screen-unlock-wrong-403;
  C1/screen-unlock-limiter].
- 5 — The core MUST write `auth.screen_unlock_failed` for a refused re-check [C6/event-screen-unlock-failed].

## Refusals and who is told

- Only the requesting surface learns the answer; the audit log holds each refusal. A 403 here is not a lost login: a
  surface MUST NOT log out on it.

## Security considerations

- **In v1 the surface enforces the screen lock alone: a guardrail** (Frontend §10.2, §10.4). A cookie holder can call
  the API directly whatever a client's screen shows or its headless screen lock guards; a refusal by the core of a
  screen-locked login is a later item.
- **A headless re-check proves the source, not the sender**: whoever can read the file or descriptor passes it. The
  login's password and its file are the client's own to protect (credential §1.3).
- **The re-check shares the login's guess budget**: one budget per user whichever door, so a screen-lock typo slows the
  user's next login by the backoff (DEFAULT 67).
- **Two locks, two proofs** (credential §5.5): seeing a session the user is already logged in to is the login's
  business; reaching a vault credential is the passphrase's.

## Open items

1. **The limiter's terms** are login's open item 3, still open; they govern both doors.
2. **An OTP**, once MFA exists, joins the password (credential §5.5, §11.2); a later item.

## Picture

pending
