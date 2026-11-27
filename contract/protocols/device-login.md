# device-login — answer the device's own SSH login questions in the pane, before any session exists

| | |
|---|---|
| **ID** | device-login |
| **Purpose** | A create in prompt mode opens no session. It answers 202 with a one-time ticket; the pane redeems it, the worker asks the device's own SSH login questions (host key, banner, keyboard-interactive rounds, password), the operator answers them in the pane, and when the device accepts the same socket continues as an ordinary attach to the new session. |
| **Status** | E4 (Release B of login-at-connect: C3 0.7, C1 202 and 429, C2 `device_login_*`; every fixture published e3 and required green e5); the picture is pending. |
| **Corpus clauses** | credential §5.5, §8.2, §10.1, §10.2, §10.4; open_decisions §5; Contract §4, §6, §10, §12, §15.3; session model §1, §5, §7, §11; Frontend §10.4 |
| **Seams** | C1, C2, C3, C5, C6, C8 |
| **Schemas** | OpenAPI `createSession`, `#/components/schemas/SessionLogin`, `SessionLoginAuth`, `DeviceLoginPending`; `ws-frames.schema.json` (`device_login_attach`, `device_login_hostkey_answer`, `device_login_answer`, `device_login_cancel`, `device_login_hostkey`, `device_login_prompt`, `device_login_result`, `request_refused`, `x-attach-order`, `x-close-codes` 4016); `ipc-control.schema.json` (`connect`, `auth`, `hostkey_ask`, `hostkey_answer`, `auth_prompt`, `auth_answer`, `auth_failed`, `error`, `disconnect`); `common.schema.json` (`login_prompt_id`, `login_attempt`, `login_prompt_item`, `login_answers`); `config.schema.json` (`worker.login_timeout`, `worker.device_logins_max`); `inventory-target.schema.json` (`options.login_timeout`); `audit-event.schema.json` (`target_auth_event`, `target_hostkey_refused_event`, `session_connect`); `continuity-state.schema.json` (`connection_record.login`); `ttyrec.schema.json` (codes 6 and 7, `x-login-line`); `artifacts-collected.schema.json` |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`). Fixtures are cited as
`<seam>/<name>`, under `contract/fixtures/`; every one this protocol cites is published e3 and required green e5, and a
**reference** one is graded by a provider test that does not exist yet (**owed**: `fixtures/graded-by.json`). Corpus
files, under `srs/`: Contract = `architecture-contract.md`, credential = `credential-protection-model.md`, session model
= `session-model-and-lifecycle.md`, Frontend = `frontend-extensibility-model.md`.

device-login is entered from session-open step 1 when the request's login is `kind: prompt`. It ends in session-open's
steps 12 to 21 (the session is opened as for any new connection), and the socket then continues as session-attach from
`attached`. It is **not** TSB's own login (login), which this document calls **the TSB login**.

**Words used here.**

- **Device login.** The authentication a create in prompt mode runs before any session exists: from the 202 to its
  result. On C2 its frames are `device_login_*`; on C3 its messages are `hostkey_ask`, `hostkey_answer`, `auth_prompt`
  and `auth_answer`, beside `auth` and `auth_failed`. It is not a session (session model §1): no channel exists before
  `connected`, it is not listed, and it has no recording of its own.
- **Round.** One keyboard-interactive challenge (RFC 4256 `SSH_MSG_USERAUTH_INFO_REQUEST`), one password request, or the
  banner. **Display-only:** a banner, or a round with no prompts, which the worker answers itself with no round trip.
- **Attempt.** One try at the device's authentication, numbered by the worker from 1 (common `login_attempt`): it rises
  only when the device refuses an answer, never on a partial success, so a password followed by a code is one attempt
  (OPEN-R5 (a)). There is no other field for the signal.
- **Probe.** A key-only connection the worker opens to fetch the device's host key before anything is authenticated: the
  SSH key exchange, offering public-key host-key algorithms and no certificate algorithm, then a close. No user name and
  no `SSH_MSG_USERAUTH_REQUEST` crosses it.
- **The ordered queue.** One first-in, first-out queue per device login, which carries every event of its connection to
  the device login in arrival order (step 10 and "The ordered queue").
- **Remember.** The user's own separate store of a login into their personal vault, after the device accepts: the
  surface's, through C7 `POST /vault/credentials`. It is not part of this protocol (Security considerations).

## Path

SF-CO → (worker-placement) → CO-PW → PW-TG (the probe) → CO-PW → SF-CO → CO-DK → CO-PW → PW-TG (the real connect) → CO-PW
→ SF-CO → (session-open steps 12 to 21) → SF-CO

## Steps

Step numbers are fixed where the contract fixes an order. A MUST names the fixture that grades it, or says **owed**.

| n | edge | from → to | message | effect | refusal, or how it ends |
|---|---|---|---|---|---|
| 1 | — | SF → SF | `GET /auth/me`, already read | When `recording.login_input` is true the surface MUST warn, in its own words, that the user name, the device's prompt text and every answer will be recorded, before it asks `login as:`; the operator goes on or stops. No fixed text. | The operator stops: nothing is sent. |
| 2 | — | SF → SF | — | The surface asks `login as:` in TSB's own words, outside any "device login" frame, and the operator types the target account name. Nothing has been sent. | — |
| 3 | SF-CO | SF → CO | `POST /sessions` `{target_id, connection: new, term, cols, rows, login: {user, auth: {kind: prompt}, recording_acknowledged}}` | The prompt branch carries no secret [C1/session-create-device-login-202]. | As session-open step 1: 401; 422 by shape (a `prompt` login on `reuse`, or beside `credential_ref`), a `user` past its bound. A core older than Release B answers 422: its `SessionLoginAuth` is closed. |
| 4 | — | CO → CO | — | session-open steps 2, 2a, 2b, unchanged: the gates, ssh only, consent. **Join, before anyone types** (OQ5 (a)): the principal MUST also be allowed to join a session on the target (`session.join.ro` or `session.join.rw`), because a device login becomes an attach [C1/session-create-device-login-no-join-403]. A prompt login is a login: no stored credential is resolved and no vault is touched (session-open step 3). The revocation switch is checked. | 404; 403 `forbidden` (no `session.create`, or no join permission); 422 `invalid_request` (not ssh) [C1/session-create-device-login-not-ssh-422]; 409 `login_recording_not_acknowledged` [C1/session-create-device-login-unacknowledged-409]; 403 `device_not_trusted`. |
| 5 | — | CO → CO | — | **The cap** (OPEN-R3 (a)): the principal's device logins in progress MUST be fewer than `config.yaml` `worker.device_logins_max` (1 to 64). The check and the registration of the new device login are one step under one lock, so two creates at once never both take the last place. A device login holds its place from its 202 until it ends: by any row of "How a device login ends", by its unattached expiry, or by becoming an attach at step 21. Each of those runs the device login's one end function, which gives the place back exactly once [C1/session-create-device-login-limit-429]. | 429 `rate_limited`, fixed text "too many device logins in progress; finish or cancel one". |
| 6 | CO-FS | FS → CO | the supervisor manifests | **The version check:** some creatable supervisor MUST write `proto_version` 0.7 or later. Nothing is placed [C1/session-create-device-login-needs-v0.7-503]. | 503 `worker_unavailable`, fixed text "no worker at proto_version 0.7 or later: the workers predate device logins". |
| 7 | SF-CO | CO → SF | 202 `DeviceLoginPending {device_login_id, ticket, expires_in}` | The core holds the device login in memory only: principal, the TSB login's token, target, user, the creator's term and size, the knob's value as read at start, the user name's receipt stamp. The ticket is one-time, bound to (principal, device login, TSB login), and lives `expires_in` seconds, the ticket lifetime (1 to 60). Nothing is listed, placed, dialled, recorded or audited [C1/session-create-device-login-202]. | An unattached device login ends when its ticket's lifetime does, and gives its place back [C2/device-login-unattached-expires]. |
| 8 | SF-CO | SF → CO | `GET /ws/attach`, first frame `device_login_attach {t, v: 1, ticket, device_login_id}` | The checks run in `x-attach-order`'s manner: not a valid first frame, or none in time, 4000; ticket unknown, expired or spent, 4001 (burned on first presentation) [C2/device-login-ticket-once]; a ticket of the other kind or for another device login, 4003 [C2/device-login-ticket-kinds]; the principal no longer sees the target, or no longer holds `session.create` or a join permission (a policy reload since the 202), 4003 [C2/device-login-permission-lost]; the device login already ended, 4016; the TSB login ended by a revocation, 4015, or by an account act, 4017. On accept **a reader runs on the socket from that moment** for its whole life, so the heartbeat's pongs are read while the person reads a banner or fetches a code, and the heartbeat is armed at once. The socket is tracked under the TSB login as an attached client is (login steps 9 to 14). | As listed. A refusal ends the device login; nothing was placed or dialled. |
| 9 | — | CO → CO | → worker-placement | The revocation switch is checked again. A worker is placed with the floor 0.7. | Placement fails, or `worker_created` says below 0.7: `device_login_result {outcome: worker_unavailable}`, close 4016. |
| 10 | CO-PW | CO → PW | dial, then `connect` (ssh) `{user, auth: {kind: prompt}, login_timeout, timers, buffer_bytes, input_frame_max}` | Only to a worker at 0.7 or later [C3/device-login-version-gate]. `login_timeout` is the target's `options.login_timeout` or `worker.login_timeout`. The core waits at most `login_timeout` plus one `heartbeat_interval` for the terminal answer (step 18). **One ordered queue:** from here every event of this connection, its terminal answer included, reaches the device login in arrival order (see below). Until the terminal answer the core sends on this connection only `hostkey_answer`, `auth_answer`, `disconnect` and `ping` (C3 rule 2) [C3/device-login-sequence]. A worker MUST refuse a prompt-mode connect that lacks `login_timeout`, `bad_request` [C3/device-login-connect-without-login-timeout-refused]. | The send fails: `worker_unavailable`, 4016. |
| 11 | PW-TG | PW → TG | address check, then **the probe** | The address check as today: resolve once, refuse a deny-listed address. If `known_hosts` holds no key for the target, the worker opens the probe to the first checked address that accepts. The probe sends no user name and no authentication request [C3/hostkey-ask-before-any-auth]. A probed key that `known_hosts` marks `@revoked` is refused there, before any ask [C3/hostkey-revoked-refused-before-ask]. The real connect (step 14) dials the address the probe reached, so the key the operator accepts is the key of the host the login goes to. A target with a key on record skips to step 14. | `error target_address_refused`; the probe fails, or the key is revoked: `error` with no code. Each → `connect_failed`, 4016. |
| 12 | CO-PW, SF-CO | PW → CO → SF | `hostkey_ask {event, conn_id, kind: first_use, fingerprint}` → `device_login_hostkey {t, v, kind, fingerprint}` | The pane shows the fingerprint in TSB's own words and asks Accept or Refuse. `kind` has one value at this release; the changed-key acceptance of a later release adds a value, not a message. A surface that meets a `kind` it does not know MUST send `device_login_cancel`, say it is too old for this device login, and never offer Accept for it (conventions V5) [C2/device-login-hostkey-first-use]. | The bound runs (step 18). |
| 13 | SF-CO, CO-DK, CO-PW | SF → CO → PW | `device_login_hostkey_answer {t, v, accept}` → audit → `hostkey_answer {cmd, accept}` | Accept: the core MUST write `target.hostkey_accepted` (user = the principal, target, fingerprint, `conn_id`; no session yet), then relay; the worker records the key under its flock, shared, one per device. Refuse: the core MUST write `target.hostkey_refused`, then relay; the worker records nothing [C6/event-target-hostkey-refused]. **The audit line records the operator's decision.** It is written before the relay. Whether the key ends up recorded is the worker's: a different key recorded meanwhile ends the login `hostkey_changed`, and the accepted line stays as the record of what the operator decided. **When the core cannot write `target.hostkey_accepted`** (ruled 2026-11-01 15:00, on derived 23: the old rule that the decision goes on is overruled), the acceptance MUST be refused and the login goes no further: the core sends the worker `disconnect` (C3 rule 3), never `hostkey_answer {accept: false}`, because the operator accepted and the failure is TSB's own; the pane is told `device_login_result` `outcome: connect_failed` and the socket closes 4016; the core logs the audit failure and writes no `target.hostkey_refused`, since nobody refused; and the worker records no key [C3/hostkey-audit-unwritable-disconnects; C2/device-login-hostkey-audit-unwritable]. A refusal whose audit line cannot be written is logged and the refusal goes on: a refusal records nothing, and the ruling changed only the acceptance. | Refused: `error hostkey_refused` → `hostkey_refused`, 4016 [C3/hostkey-refused]. A different key recorded meanwhile: `error hostkey_changed` → `hostkey_changed`, 4016. An answer with no ask outstanding: `request_refused {request: device_login_hostkey_answer, reason: not_pending}`, and nothing crosses C3 [C2/device-login-answer-refused]. |
| 14 | PW-TG | PW → TG | **the real connect** | TCP to the checked address (the probe's, when there was a probe), then key exchange checked against `known_hosts` with nothing recorded: in prompt mode a key not on record is refused. Then user authentication as `user`, with `preferred_auth` keyboard-interactive then password: no client key, no agent, no stored password, `kbdint_auth` on, and asyncssh's own login timer off, so the worker's bound is the one clock. | A key not on record: `error hostkey_changed` → `hostkey_changed`, 4016. |
| 15 | CO-PW, SF-CO | PW → CO → SF | `auth_prompt` → `device_login_prompt` | One frame per round, relayed unread (Contract §6): the core checks only kinds, counts and lengths. A banner carries `text` only; a password request carries `attempt` and `prompt_id` and no device text, so the pane shows its own `Password:` outside the "device login" frame; a keyboard-interactive round carries `attempt`, `name`, `instruction`, `prompts` and `prompt_id` exactly when `prompts` is not empty. A surface that meets a `method` it does not know MUST send `device_login_cancel` and say it is too old for this device login (conventions V5) [C2/device-login-relay]. | Past 32 rounds, or past 64 KiB of device text and answers together in this login: the core sends `disconnect` → `too_long`, 4016. A method the core does not know, or an event out of turn (C3 rule 7): `disconnect` → `connect_failed`, 4016. |
| 16 | SF-CO, CO-PW | SF → CO → PW | `device_login_answer {t, v, prompt_id, answers}` → `auth_answer {cmd, prompt_id, answers}` | The core relays only an answer that names the outstanding `prompt_id`, has one string per prompt (one for `password`) and holds no string past 1024 bytes of UTF-8. An empty string is an answer: a keyboard-interactive "press Enter" prompt takes one. With the knob on and consent given, the core holds a copy for the recording (step 20). The answers cross C2 and C3 only. | Another `prompt_id`, or none outstanding: `request_refused {request: device_login_answer, reason: not_pending}`. A wrong count or an over-long answer: `request_refused {…, reason: invalid}`. Either way the prompt stays outstanding and nothing crosses C3 [C2/device-login-answer-refused]. |
| 17 | PW-TG | PW → TG | the device decides | A wrong answer usually brings the device's next request, in a higher attempt. A partial success (RFC 4252 §5.1) brings the device's next method in the same attempt [C3/device-login-partial-success]. TSB retries and counts nothing on its own; the device counts. | — |
| 18 | CO-PW | PW → CO | terminal answer: `connected`, or `auth_failed {code}`, or `error {code}` | Exactly one per `connect` (C3 rules 2 and 6). `auth_failed.code` is `refused` (the device has no method left), `login_timeout` (the worker's bound) or `password_change_requested` [C3/auth-failed-codes]. The core acts on the code and never on `reason` (V4). Because the queue is ordered, every round that came before the terminal answer has been relayed, and held for the recording, before the core acts on it. | `refused` → `refused`; `login_timeout` → `timeout`; `password_change_requested` → `password_change_requested`; `error hostkey_changed` → `hostkey_changed`; `error hostkey_refused` → `hostkey_refused`; any other `error`, `bad_request` included (C3 rule 6) → `connect_failed` [C3/device-login-bad-answer-ends]; a control-socket EOF → `worker_unavailable` (no re-dial during a device login); the core's own wait expired → it sends `disconnect`, `timeout`. Each is followed by close 4016. |
| 19 | CO-DK | CO → DK | `target.auth_failed {user, target, detail: {login: prompted, attempts, conn_id}}` | Written when the device login ends without `connected` and `attempts` ≥ 1. `attempts` is the number of distinct attempt numbers for which the core relayed an answer; with the worker's numbering every one of them failed. A login abandoned with no answer sent writes nothing [C6/event-target-auth-failed-prompted]. | — |
| 20 | — | CO → CO | → session-open steps 12 to 20 | On `connected` the core first looks for a `device_login_cancel` the socket has read. It then checks again that the principal sees the target, holds `session.create` and may join it. Then it opens the session as for any new connection: catch-up, pinger and connection record (none of which ran when `connected` arrived), then `open_shell` with the create's term and size, clamped. `open` checks for a cancel once more, just before the durable record. Then come the recording, with the login frames, TSB's own prompts (code 8) among them, at its head when the knob is on and consent was given [C8/recording-device-login-frames]; the handshake to `live`; the records; and `session.connect` with `detail.login: prompted`, `login_recorded`, and `refused_attempts` when it is ≥ 1 [C6/event-session-connect-prompted]. The connection record keeps `credential_ref` `supplied` and gains `login: prompted` [C6/connection-record-login]. | A permission lost meanwhile: `disconnect`, `forbidden`, 4016, nothing audited. A cancel read in time: see "How a device login ends". The open fails: the core sends `disconnect`, `connect_failed`, 4016. |
| 21 | SF-CO | CO → SF | `device_login_result {t, v, outcome: connected}`, then `attached` … | The device login ends and gives its cap place back. The same socket continues in `x-attach-order` from `attached`: `prefix`, the dump, `clients`, the wiggle, live output, with `may_control` as `attach` computes it, and `session.join` audited. The surface shows the session's recording warning before any input (session-attach CR6). If the socket is gone, nothing is sent and the session lives on detached, owned and listed, as a 201 nobody read does [C2/device-login-after-connected]. | — |
| 22 | — | SF → SF | Remember | The surface's own, after `connected`: Security considerations. | — |

## Reusing a prompted connection

A connection a device login opened may carry another session for the principal that opened it (Contract §15.3: reusing
the connection reuses the authentication, so a TACACS+ or one-time-code login is paid once). Two ways reach it, both on a
`reuse` create with no login, and both decided at the head of session-open step 3, before that step's vault refusal, its
check that a credential resolves and its candidates: the core resolves no stored credential for it (a reuse sends no
credential, credential §10.2), touches no vault, and opens the session on it.

- **By `connection_id`.** The principal names it. No refusal of step 3 answers it, so a target no stored credential resolves
  for, a choice among several, or a mapping to a missing entry does not make it a 409.
- **Offered back.** The create names no connection and carries no `credential_ref`. The core gathers the principal's own
  prompted connections to the target, still up, and when there is at least one, no refusal of step 3 answers either. The
  candidates are those connections and, when exactly one stored credential resolves, the connections opened with it; one
  candidate is reused, several answer 409 `connection_choice_required` listing them in `connection_ids`. With two or more
  stored credentials resolving and no choice, the prompted connections alone are the candidates: a caller who wants a new
  connection asks for `new` and meets the credential choice then. With no prompted connection, step 3 runs as it does for
  any request.

A prompted connection is offered only to the principal who opened it, and is no candidate beside a `credential_ref`: a
`reuse` naming it together with `credential_ref` is 422 (no stored credential opened it), judged before any 409. It
lingers like any connection (`connection_linger`). A connection opened with a supplied login stays never reusable
[C1/reuse-prompted-connection-by-id; C1/reuse-prompted-connection-offered-to-its-principal].

- **The audit of a reuse.** A session on a reused prompted connection writes `session.connect` with `detail.login:
  prompted`, `login_recorded: false` and no `refused_attempts`. A rebuild at restart writes the same, reading `login` from
  the connection record. A rebuilt line for the device login's own session cannot carry `refused_attempts`, which is not
  persisted: an accepted gap, the one place a rebuilt line differs from the live one.
- **After a core restart.** A restart restores a connection's owner from its sessions' records only. A lingering prompted
  connection with no session left comes back with no owner: it is reusable by nobody and lingers out its
  `connection_linger`. One that still carries a session comes back with that session's owner and stays reusable by them
  alone [C6/connection-record-login].

## What crosses for one round

An `auth_prompt` (C3) and its `device_login_prompt` (C2, the same fields without `conn_id`) is one of three methods:

- **`banner`**: the RFC 4252 §5.4 `SSH_MSG_USERAUTH_BANNER` text. Display-only: no `prompt_id`, no `attempt`, no answer.
- **`keyboard-interactive`**: `{attempt, name, instruction, prompts: [{text, echo}], prompt_id}`. `prompt_id` is present
  exactly when `prompts` is not empty. A round with no prompts is display-only: the worker answers it itself.
- **`password`**: `{attempt, prompt_id}`. A password request carries no device text, so the pane shows its own `Password:`
  outside the "device login" frame.

`prompt_id` is the worker's per-connection integer, relayed as it is (common `login_prompt_id`). Bounds, in characters:
banner 16384, `name` 1024, `instruction` 4096, a prompt's text 1024, 0 to 16 prompts a round; past one the worker ends
the login `error not_supported` → `connect_failed`. Device text that is not UTF-8 ends the login inside asyncssh, and the
worker answers an `error` with no code: nothing is relayed.

**The attempt number** (common `login_attempt`; C3 rule 8; OPEN-R5 (a)). It starts at 1. The worker raises it by one
only when the device refuses (`SSH_MSG_USERAUTH_FAILURE`, partial success false) a request in which the worker gave an
answer: a password, or a keyboard-interactive response to a round with prompts. A partial success leaves it as it is, and
so does a failure of a request with no answer (the opening `none`, a round with no prompts). Three readers rely on it:
the pane says "The device refused the answer." only before a prompt whose attempt is higher than the last one answered;
`target.auth_failed` `attempts` counts every answered attempt; `session.connect` `refused_attempts` counts the answered
attempts below the last. A two-factor login (a password, then a code) is therefore one attempt, and its success records no
refusal. `tsbc`'s control-sequence check grades the part a capture shows: the number starts at 1, never goes down, rises
by one at most, and only after an answer was relayed [C3/device-login-partial-success].

## The ordered queue

The worker's lines arrive on one control socket and are read one at a time. A provider (the core) MUST keep that order up
to the person:

- **One queue.** A prompt-mode connection has one first-in, first-out queue for its device login. The core appends every
  event of that connection to it until the terminal answer: `hostkey_ask`, `auth_prompt`, `connected`, `auth_failed`,
  `error`, `connection_lost`, and the control socket's end as a last entry. It appends without blocking and signals the
  device login.
- **Every event reaches the device login in arrival order, the terminal answer included,** and a round before it MUST be
  relayed to the pane, and held for the recording, before the core acts on the terminal answer
  [C2/device-login-order, owed].
- **Nothing on `connected` until the device login acts.** On a prompt-mode connection's `connected` the core only moves
  its state and appends. The catch-up, the pinger and the connection record run when the device login acts on it
  (step 20), and never for a device login that has already ended.
- **In order, one at a time.** The device login takes the entries in order and finishes each before the next: it writes
  each frame to the socket and holds each piece for the recording.
- **Bounded.** The core counts rounds and the device text's bytes as it appends, and the device login adds each answer's
  bytes as it relays it, against one shared count. Past 32 rounds or 64 KiB in all, the device login ends `too_long`; the
  queue never holds more than 36 entries.
- **Late events.** A device-login event that arrives after the terminal answer, or on a connection not in prompt mode, is
  logged as a worker fault and ignored.

**Who may answer.** Only the principal that made the create, on the one socket that redeemed the device login's one-time
ticket. There is no control token before a session exists, and no other client can see a device login: it is not a
session, it is not listed, and its ticket was handed to its creator alone. Before `attached` the core ignores every other
client frame on that socket. A `device_login_*` frame after the device login's end, or after `device_login_result
{connected}`, is ignored, as an unknown `t` is.

**The two cancel points.** A `device_login_cancel` is honoured when the device login takes `connected` and once more inside
`open`, just before the durable record: `open` then withdraws the session the way it withdraws a failed one, leaving
nothing. After that the session exists and the cancel is too late, as one that crosses the result would be. A lost socket
never withdraws: the session lives on detached [C2/device-login-after-connected, owed].

## What the recording holds

With `recording.enabled` and `recording.login_input` both on at start and `recording_acknowledged` true on the create, the
session's recording opens with these frames, in this order, stamped on the core's input clock when the core received each
piece, the clock never decreasing. Nothing of the device login is held or recorded otherwise [C8/recording-device-login-frames].

1. Code 8, the fixed line `login as:` (TSB's own prompt), then code 7, the user name typed at it, both stamped when the
   create arrived.
2. Code 6: the banner's text, if one came.
3. For each keyboard-interactive round, in order: code 6 for `name`, then code 6 for `instruction`, each when not empty;
   then, interleaved, code 6 for a prompt's text and code 7 for its answer. A prompt's code-6 frame is written even when its
   text is empty, as the tag alone, so every keyboard-interactive answer follows its own prompt's frame. A prompt written
   after an earlier answer of its round carries that answer's stamp, since the input clock never decreases. These prompts
   are the device's own: a keyboard-interactive round has no code-8 frame.
4. For a password round: code 8, the fixed line `Password:` (TSB's own prompt), then code 7, the answer. A code-7 frame
   after a code-8 `Password:` is a password round's; a `Password:` the device sent is code 6 and never TSB's.
5. The host-key decision is not recorded; it is audited (step 13).

**The source mark** (OPEN-R4, ruled by the operator 2026-11-01 21:53). TSB's own prompt text has a recording code of its
own, 8, with its own fixed tag and the form of the login line (`x-own-prompt-line`). Every code-6 frame is therefore text
the device sent, and no code-6 line can carry TSB's words: the device's own "Password:" cannot pass as TSB's. Code 7 stays
the answers and code 6 the device's text; code 8 carries no secret, so a collection keeps it. Code 8 is written only where
the other login frames are, under both knobs and the operator's consent. A supplied login (Release A) is never asked at a
prompt and has no code-8 frame. A reader refuses code 8 where it does not know it (V1), as a v0.2 reader refused code 5.

## Bounds

| Bound | Value |
|---|---|
| Unattached, from the 202 to `device_login_attach` | the ticket lifetime, `expires_in` (1 to 60 seconds) |
| The device login, from `connect` to the terminal answer | `login_timeout`: the target's `options.login_timeout` (integer seconds), else `config.yaml` `worker.login_timeout`; 10 s to 30 min |
| The core's wait for the terminal answer | `login_timeout` plus one `heartbeat_interval`, so the worker's `login_timeout` arrives first |
| Device logins in progress per principal | `worker.device_logins_max`, 1 to 64; checked and registered atomically, released once at every end |
| One device login, held or not | 32 rounds (every `auth_prompt` counts), and 64 KiB of the device's text and the answers together, in bytes of UTF-8; past either, `too_long` |
| One answer | 0 to 1024 bytes of UTF-8 |

The device's own grace time (sshd's `LoginGraceTime`) runs only on the real connect, after the operator has read and
accepted the fingerprint on the probe.

## How a device login ends

Every row runs the device login's one end function. That function gives the cap's place back once, drops anything held for
the recording and voids the ticket if it is unredeemed. Except for the device-revocation and account-act rows, it
sends `device_login_result` when the socket is still there, then closes 4016 `device_login_ended` (no session exists, so a client MUST NOT reattach, and it may start a new device login:
conventions V3). The worker's connection is ended by `disconnect`, by its own bound, or by the rule that a prompt-mode
connect never outlives its control peer (C3 rule 4). **The core never waits for the worker's `connection_lost`**: the
result and the close go at once. A `connection_lost` or a control-socket end that comes later only removes the connection.
A `connected` that arrives after the core sent `disconnect` is dropped as an abandoned connection's is, with no record, no
pinger and no second `disconnect`.

| Cause | Core | Result, close | Audit |
|---|---|---|---|
| The pane sends `device_login_cancel`, or closes its socket, before the core acts on `connected` | `disconnect` | `cancelled`, 4016 | `target.auth_failed` when `attempts` ≥ 1 |
| The pane sends `device_login_cancel` after the core took `connected`, and the core reads it before `open`'s last check | `open` withdraws the session, leaving nothing; `disconnect` | `cancelled`, 4016 | none: the device accepted, and no session exists |
| A `device_login_cancel` read after `open`'s last check | ignored, as a frame after the result | the session opens: `connected`, then the attach | as for any session |
| The pane's socket closes after the core took `connected` | nothing: the open goes on | no result (the socket is gone); the session lives on detached, owned and listed | as for any session |
| A policy reload removes the principal's view of the target, `session.create` or join | before the attach: nothing to end; at `connected`: `disconnect` | 4003 at `device_login_attach`; `forbidden`, 4016 at `connected` | none |
| The TSB login logs out or expires | `disconnect` | `cancelled`, 4016 | `target.auth_failed` when `attempts` ≥ 1 |
| The TSB login is ended by a device revocation (login steps 9 to 14) | `disconnect` | close 4015, no result | as above |
| The TSB login is ended by an account act (login step 18) [C2/account-device-login-ended-close-4017] | `disconnect` | close 4017, no result | as above |
| A missed WebSocket pong (`ws_heartbeat`, armed at step 8) | `disconnect` | close 4016 | as above |
| The core's own wait expired; `too_long`; an unknown value or an event out of turn (C3 rule 7) | `disconnect` | `timeout`, `too_long` or `connect_failed`; 4016 | as above |
| The core stops | closes its control sockets; the 0.7 worker drops the connection | `cancelled`, 4016 | as above, written before the stop |
| The core restarts unannounced | — (the device login lived in memory) | the socket is gone | none: nothing was left to write |
| The worker dies (control-socket EOF) | — | `worker_unavailable`, 4016 | as above |
| The ticket's lifetime ends with no `device_login_attach` | nothing (nothing was placed) | no socket, nothing sent | none |

A socket close and a `connected` that cross are both possible orders; the rule is which one the device login takes first.
The same holds for a cancel [C2/device-login-cancel, owed; C3/device-login-disconnect-cancels, owed;
C3/device-login-peer-gone-drops, owed].

## Refusals and who is told

| What | Who is told | How |
|---|---|---|
| a refused create (steps 3 to 6) | the creating surface | the status of step 3 to 6; no audit event, as no refused create is audited |
| a bad first frame, a bad ticket, a lost permission at the attach (step 8) | the socket | close 4000, 4001, 4003; 4016 if it already ended; 4015 if the TSB login was revoked; 4017 if an account act ended it; nothing sent first |
| an answer the core will not relay (step 16) | the creator's socket | `request_refused` `not_pending` or `invalid`; the prompt stays outstanding |
| a refused or changed host key (steps 13, 14) | the pane, the audit | `device_login_result` `hostkey_refused` or `hostkey_changed`; `target.hostkey_refused` for a refusal |
| a refused login, or a bound that passed (step 18) | the pane, the audit | `device_login_result` `refused`, `timeout` or `password_change_requested`; `target.auth_failed` with `attempts` when an answer was given |
| any other failure of the connect | the pane | `connect_failed`; the worker's `reason` is for the log and is never shown or parsed |

The only audit events of a device login are `target.hostkey_accepted` and `target.hostkey_refused` (step 13),
`target.auth_failed` (step 19) and, once a session opens, `session.connect` (step 20). None carries an answer or the
device's reason text.

## Security considerations

- **Answers cross only C2 `device_login_answer` and C3 `auth_answer`.** Never a data socket, the ring, the VT grid, a log,
  a manifest, the spool, or a recording unless `recording.login_input` is on and consent was given (V10; session-open
  Security considerations). `answers` is a credential-named key: a collection holds only the synthetic constant for each
  string, and `tsbc`'s self-check refuses any other (`answers` in `CREDENTIAL_KEYS`, read as "each string of the list").
  The core's payload for the recording is held only while the knob is on, and dropped at the end function.
- **Device text is plain text** in a frame marked "device login". A surface never renders it as markup, and never lets it
  stand in TSB's own chrome: TSB's own questions (`login as:`, the password method's `Password:`, the host-key question,
  Remember) sit outside that frame. TSB's own `login as:` and `Password:` are recorded as sidecar code 8, and only while
  `recording.enabled` and `recording.login_input` are both on and the create acknowledged the recording; the device's text
  is code 6 and the answers code 7 under the same conditions. The host-key question and its answer, and the Remember
  control, are never recorded (the host-key decision is audited). A password request carries no device text for that reason.
- **Who may answer:** the creator, on the one socket that redeemed the ticket (steps 7, 8). The ticket is one-time, bound to
  (principal, device login, TSB login), never in a URL, and of its own kind: a session ticket does not open a device login.
- **The probe sends no user name** and no authentication request, so the device sees no login name until the operator has
  accepted its key, and the key the operator accepts is the key of the address the real connect dials.
- **Remember** is the user's own separate store of a login into their personal vault, through C7 `POST /vault/credentials`
  (OQ8): the create, the device login and the core store nothing of a login (V10). It is offered by the surface only on a
  `password` round, never on a keyboard-interactive answer, because a one-time code is never stored; it is not part of this
  protocol's wire.
- **The password method's change request** ends the login `password_change_requested` (C3 `auth_failed.code`), while a PAM
  password change asked through keyboard-interactive rounds is relayed and done in the pane.
- **Nothing here matches a prompt in the byte stream** (Contract §6; V13): the worker relays SSH's own authentication
  messages, never the bytes of a session.

## Open items

1. **Ruled (2026-11-01 21:53, the operator): the source mark in the recording (OPEN-R4).** TSB's own prompt text, `login as:`
   and a password round's `Password:`, is sidecar code 8, a fixed line with a tag of its own (`x-own-prompt-line`), written in
   the head before the code-7 answer it asks for. Code 6 is only text the device sent. Options not taken: a second tag in
   the code-6 line (it changes Release A's published pattern), and TSB writing none of its own text.
2. **The corpus line** (credential §10.2, OQ8) awaits the operator's go and his authoring in the vault. Without it the
   contract's own wording (V10, `SessionLogin`, session-open) stands alone.
3. **Ruled (2026-11-01 15:00, a DEFAULT, veto by line): a host-key acceptance whose audit line cannot be written.** The core sends
   the worker `disconnect`, never `hostkey_answer {accept: false}`; the pane is told `connect_failed` and the socket closes
   4016; the core logs the failure and writes no `target.hostkey_refused`; the worker records no key (step 13).
4. **Whether a supplied-login connection becomes reusable by id, as a prompted one is,** is open (session-open's open
   item); at this release it stays never reusable.
5. **The changed-key acceptance** (`target.hostkey.clear`; a later `kind`) and a rate limiter on refused logins are later
   items; the messages are shaped so that they add a value, not a message.

## Picture

Pending until the generator exists (README, "A protocol document", item 7).
