# connection-linger — close a connection left with no session, and the core's own disconnect

| | |
|---|---|
| **ID** | connection-linger |
| **Purpose** | Keep a connection with no live session up for `connection_linger`, so that a quick reopen reuses its login, then close it; and close a whole connection when the core sends `disconnect`. |
| **Status** | built (E1); steps 11–17, the core's `disconnect`, since E0. At E0 `connection_linger` is 0 (CHANGELOG v0.1) |
| **Corpus clauses** | session model §5, §11 (`connection_linger`), §12, §14; lang-arch §6.4 steps 3–4; Contract §4, §15.2, §15.3 |
| **Seams** | C3 (the control socket), C5 (the worker manifest), C6 (the connection record); C1 only through session-open |
| **Schemas** | `ipc-control.schema.json#/$defs/timer_values`, `#/$defs/connect`, `#/$defs/timers`, `#/$defs/connection_lost`, `#/$defs/disconnect`, `#/$defs/release`; `worker-manifest.schema.json`; `continuity-state.schema.json#/$defs/connection_record`; `contract/openapi/tsb.openapi.json#/components/schemas/SessionCreateRequest`; `error.schema.json` (`connection_ids`) |

Schema paths are relative to `contract/schemas/` at `contract-v0.3`; fixtures are `<seam>/<name>` under
`contract/fixtures/`. Corpus files, under `srs/`: session model = `session-model-and-lifecycle.md`,
lang-arch = `language-architecture-decision.md`, Contract = `architecture-contract.md`.

connection-linger starts when a connection's last live session ends (session-end-by-tsb, session-end-from-outside, the core present or gone), or when a connection carries
none. Closing a session never closes its connection by itself (Contract §15.2; C1/session-create-reuse-empty-connection). A telnet connection is the exception: it carries one session and closes with it (ipc-control `connection_lost` cause `single_session`).
connection-linger has no consumer step: no surface is told of a linger close.

**Words used here.**

- **`connection_linger`**: how long a connection with no live session stays up; a `config.yaml` knob with a
  per-target override, run by the worker (session model §11; `timer_values`).
- **Lingering connection**: a connection that is up, with no live session on it, while `connection_linger` runs.
- **Retained ended session**: an ended session whose ring, data socket and manifest entry the worker keeps until the
  core's `release`, because the core might not have recorded its tail. It is not a live session (`release`).
- **Spool**: the worker's on-disk record of an end it originated or observed (ipc-control top, "Spool").
- **Reuse candidate**: a connection session-open can open a new session on (`SessionCreateRequest`).
- **VTY line**: a network device's virtual terminal line; each remote login holds one while it lasts.

**Reuse.** A lingering connection is a reuse candidate: its transport is up, and a candidate need not carry a
session ("whether or not it has a session now", `SessionCreateRequest`; "a lingering one included", error
`connection_ids`) [C1/session-create-reuse-empty-connection, C1/reuse-409-lists-candidates]. A reuse opens a session
on it, which resets the timer (step 3); once closed it is never a candidate again (step 8). The choice itself → session-open.

## C3 0.8 terminal branch

[Terminal output accounting](terminal-output-accounting.md) §8 applies to C3 0.8 retained PTY ends within [its §1 scope](terminal-output-accounting.md#1-scope-and-acceptance).
Recovery hello, replay, sealing and audit writes never reset linger or hold the target login past it.
On connection closure, retained resources are disposed with the connection whether or not accounting
finished. The core writes an unknown-suffix fact after its validated durable frontier, or unrecorded
for an entirely unrecorded session; never an exact/zero suffix from a non-durable seal. No new worker
disk record is written, and a worker exits once it holds no connection
[C3/terminal-linger-not-reset; C3/terminal-worker-exits-last-connection;
C6/terminal-close-after-result-before-sync; C6/recording-integrity-unrecorded].

## Path

`connection_linger`: CO-PW → CO-PW → PW-TG → CO-PW → PW-FS → (spooled-end-finalize) → CO-BB
the core's `disconnect`: CO-PW → PW-TG → CO-PW → PW-FS → (session-end-from-outside) → CO-BB

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock`, one JSON object per line | `connect`, later `timers`, each carrying `timer_values` | the core | Gives the worker the connection's `connection_linger`: integer milliseconds, 0 = close with the last session; the contract fixes the range, never the value (`timer_values`; conventions V7) [C3/timer-values-shape]. The core MUST send the full set on every `connect` to a v0.2 or later worker [C3/connect-carries-timers], and on every change (→ config-reload; E3; owed). The worker keeps the last set it received, so the timer runs while the core is away (`timers`) [owed]. | A v0.2 worker refuses a `connect` without timers, `error` `bad_request` [C3/connect-without-timers-refused] (session-open's) | — |
| 2 | — | PW → PW | — | — | worker | Arms the timer once no live session is left on the connection (session model §11 "armed while"). A retained ended session is not a session for the timer: the worker MUST NOT let one hold the connection open (`release`; session model §11, §14) [C3/ended-retention-bounded]. A connection that never carried a session also closes by this timer (`connection_lost`); when its wait starts is unspecified (not in plan §7). | — | — |
| 3 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock` | → session-open: `open_shell` (a reuse) | the core | A new session is opened on this connection. The worker resets the timer (session model §5, §11 "reset by"). The worker MUST NOT close the connection for linger while a live session is on it [C3/linger-close, check `control-linger`]. When that session ends, step 2 arms it again: it fires after `connection_linger` "with no session" (session model §11) [owed]. Whether `open_shell` or `channel_opened` is the reset point is unspecified (not in plan §7). | — | — |
| 4 | — | PW → PW | — | — | worker | Fires after `connection_linger` with no live session; at 0, as the last live session ends (`timer_values`). The worker MUST then close the connection; a later session needs a new one (session model §11) [C3/linger-close; C3/ended-retention-bounded for 0]. It runs whether or not a core is attached (session model §11 "evaluated by: worker"; spool-entry top) [owed]. A telnet connection carries one session. It closes when that session ends or its open fails, and reports it, cause `single_session`, once the end has reached the core (ipc-control `connection_lost`); `connection_linger` never holds it after that session. One that never received `open_shell` closes by linger, as any connection that never carried a session [C3/telnet-one-session, reference, owed by the E3 worker plan; C3/linger-close]. | — | — |
| 5 | PW-TG | PW → TG | SSH (nothing for the `echo` driver) | the transport's close; its wire form is not contract | worker | Ends the target login and frees its VTY line (session model §11). The close releases every retained ended session on the connection, and the spool's numbers stand (`release`; `connection_lost`) [C3/ended-retention-bounded]. | — | the target |
| 6 | CO-PW | PW → CO | `control.sock` | `connection_lost`, `cause` `linger` | worker | `session_ids` MUST be empty: the shape forbids a listed session, retained ones included [C3/linger-close; C3/control-message-shape-v0.2]. The worker MUST NOT send it while a live session is on the connection [C3/linger-close, check `control-linger`], and MUST NOT send anything after it on that socket [C3/control-connect-openshell-sequence, check `control-sequence`]. A v0.1 core reads the unknown cause as `transport` and finds no session to end (`connection_lost`). | — | the core, when attached (Open items) |
| 7 | PW-FS | PW → FS | a file `workers/{worker_id}.json` | worker-manifest, without the connection and its retained sessions | worker | The worker MUST rewrite its manifest atomically on this change (worker-manifest top) [C5/worker-manifest-valid grades the shape; the rewrite is owed]. It MUST NOT exit while it holds another connection (lang-arch §6.4 step 4), and MUST exit once it holds none (spool-entry top) [both owed]; its supervisor reaps it (worker-placement step 7). Whether this comes before or after step 6 is not stated. | — | a restarted core (core-restart) |
| 8 | — | CO → CO | — | — | the core | The connection stops being a reuse candidate: "A connection that has closed … is never a candidate". The core MUST NOT list it in `connection_ids` or reuse it, and MUST answer a `connection_id` naming it 422 `invalid_request` (`SessionCreateRequest`) [owed]. | — | the next creating principal, through session-open |
| 9 | — | CO → CO | — | → spooled-end-finalize | the core, on its return | A retained ended session the close released was never released by the core. A core attached at its end audits it live and sends nothing on the closed connection (spool-entry top: "A core that is attached when the end happens audits it live"; session-end-from-outside steps 11–16, session-end-by-tsb steps 13–15). A returning core finalizes it through spooled-end-finalize, and the spool's numbers stand (`release`) [C3/ended-retention-bounded grades the worker's side; the core's is owed]. | — | as spooled-end-finalize says |
| 10 | CO-BB | CO → BB | a bbolt write | `connection_record` | the core | While the connection lingers, the record's `session_ids` lists the sessions on it not yet released (continuity-state) [C6/continuity-record-valid grades the shape]. What the core writes once the connection closes, removal or anything else, is unspecified (not in plan §7). | — | — |
| 11 | — | CO → CO | — | — | the core | Decides to close the whole connection. When it does is unspecified (plan §7 G-7): no northbound request causes it. | — | — |
| 12 | CO-PW | CO → PW | `control.sock` | `disconnect` | the core | Asks the worker to close the connection and every session on it (`disconnect`). The core MUST NOT send it before `connected`, and MUST NOT send any command after it on that socket [C3/control-connect-openshell-sequence, check `control-sequence`]. | — | — |
| 13 | PW-TG | PW → TG | SSH | each session's channel and the transport closed; not contract | worker | As step 5, on a connection that can still carry live sessions (`disconnect`). | — | the target |
| 14 | CO-PW | PW → CO | `control.sock` | `connection_lost`, `cause` `disconnect` | worker | The worker MUST answer `disconnect` with it [C3/control-resize-close-disconnect, check `control-channels`], listing exactly the sessions still open (`connection_lost`; lang-arch §6.4 step 3: "every session riding it") [C3/control-resize-close-disconnect; C3/connection-lost-lists-sessions, check `control-connection-lost`]; whether a session whose `close_channel` is in flight counts is not said (Open items). It MUST NOT send `cause` `disconnect` except as this answer, and MUST NOT send anything after it [C3/control-connect-openshell-sequence, check `control-sequence`]; it then closes the socket (`disconnect`) [owed]. Before it, a `link_state` that was already due may still arrive: link-liveness step 10 makes it the first event to a new control peer, and the worker may send it before it reads the `disconnect`. It is not an error, and the core takes no action on it [C3/control-connect-openshell-sequence, check `control-sequence`]. | — | the core |
| 15 | PW-FS | PW → FS | as step 7 | as step 7 | worker | The manifest rewrite of step 7. The worker MUST NOT exit while it holds another connection (`disconnect`: it "exits only if it holds no other connection") [owed]. | — | a restarted core (core-restart) |
| 16 | — | CO → CO | — | → session-end-from-outside | the core | A listed session the core has already ended keeps that end. One the core has not ended is an internal error: it ends `terminated` / `connection_lost` and is logged (DEFAULT 15, accepted by the operator 2026-09-26; session-end-from-outside step 5) [owed]. The contract README's end rule, which takes "the core's own `disconnect`" out of `terminated`, is stale text (queue K9; Open items). | — | attached clients, as session-end-from-outside says |
| 17 | CO-BB | CO → BB | a bbolt write | `connection_record` | the core | As step 10: unspecified (not in plan §7). | — | — |

## Refusals and who is told

- **A linger close tells no surface.** It ends no session (`connection_lost`); its one northbound trace is that
  session-open no longer offers the connection (step 8).
- **A reuse that crosses the close.** session-open's `open_shell` can cross step 6. What the worker answers and what the
  creating principal gets are unspecified (not in plan §7). The grader reads any message after `connection_lost` as
  an order fault (check `control-sequence`).
- **The core's `disconnect`.** Clients attached to a listed session the core had not ended are closed 4012, as
  session-end-from-outside says (DEFAULT 15, step 16); a session the core had ended told its clients at its own end.

## Security considerations

- **The target's login lines.** A lingering connection holds a live, authenticated login on the target, and one of
  its VTY lines, with no session on it, for at most `connection_linger` (session model §11: "costs at most 5 min of
  a VTY line"). A retained ended session never extends that hold, "or its target login" (`release`; session model
  §11, §14) [C3/ended-retention-bounded]. `connection_linger` 0 frees the line with the last session
  (`timer_values`); the value is the operator's knob, never the contract's (conventions V7).
- **Only its own principal reuses it.** Candidates are the requesting principal's own connections, same target,
  same credential (`SessionCreateRequest`; error `connection_ids`; Contract §4: users are tenants)
  [C1/reuse-409-lists-candidates]. A second principal always gets a new connection (session model §5) [owed beyond
  the 409's list].
- **Reuse skips the target's authentication.** A reopen within the wait shares the login already made: "Reusing
  the connection reuses the authentication" (Contract §15.3), "a TACACS+ round trip saved" (session model §11). That
  is the timer's purpose, bounded by the knob.
- **No credential crosses.** `disconnect` and `connection_lost` carry none, and `reason` is free text for the log,
  never parsed (ipc-control top). The connection record holds `credential_ref`, a reference, never the credential
  (`connection_record`) [C6/continuity-record-valid].

## Open items

- **G-7.** When the core sends `disconnect` (step 11): the trigger. The end of a listed session the core has not
  ended is ruled (DEFAULT 15, step 16); the contract README sentence that leaves it no valid end is stale text
  (queue K9). G-7 also covers a v0.1 worker's connection too: the core sends such a worker no `timers` and its `connect` carried none, so
  it has no `connection_linger`, and it is never a reuse candidate (ipc-control top; `connection_record`
  `proto_version`). The core runs no linger for it either (`C3/n1-v0.1-worker-unreachable`: "no hello, ping, timers,
  kill request or linger", graded by U2 `version_test.go`); whether anything closes it once empty is not stated.
- **Not in plan §7:**
  - when the wait starts on a connection that never carried a session, and how `connection_linger` 0 spares a new
    connection before its first `open_shell` (step 2);
  - the reset point, `open_shell` or `channel_opened`, and the outcome of an `open_shell` that crosses a linger
    close (steps 3, 6);
  - a linger close with no core attached: the event has no reader, the manifest drops the connection and the
    worker exits if it holds no other (step 7); what a returning core does with that connection's record (core-restart);
  - what the core writes to the `connection_record` once its connection closes (steps 10, 17);
  - whether a `timers` that changes `connection_linger` re-times a wait already running (step 1);
  - the order of the manifest rewrite and `connection_lost` (step 7);
  - whether a session whose `close_channel` is in flight is "still open" for the `disconnect` answer's list
    (`connection_lost`: "every session still open on it"; lang-arch §6.4 step 3: "every session riding it") (step 14).

## Picture

pending
