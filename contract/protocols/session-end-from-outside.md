# session-end-from-outside — a session ended from outside TSB

| | |
|---|---|
| **ID** | session-end-from-outside |
| **Purpose** | Learn that a session ended from outside TSB — the target closed it, the connection was lost, or the worker died — and finalize it: record, recording, audit, grid and clients; while the core is gone, from the spool when the core returns. |
| **Status** | built (E0–E1); step 17 is E2; telnet ends (step 1) from this release; serial ends with a later release |
| **Entered from** | link-liveness (`link_timeout` fired), hung-worker (a hung worker killed), data-socket-redial (a failed re-dial), core-restart (a session missing at rediscovery), reattach-handshake (`no_such_session`), orphan-session step 6 (an end from outside while the core is gone), connection-linger step 16 (a session the core's own `disconnect` lists that the core has not ended). Each owns its trigger; session-end-from-outside owns the end, whether or not the core is present (plan §2.1; the operator, 2026-10-07). session-end-by-tsb owns every end through TSB. |
| **Corpus clauses** | session model §8 (A1–A3, A7), §9 E5, E9, E10, §10, §11 (`link_timeout`, `worker_heartbeat`; T7, T9, T10, T11), §12, §13, §14; lang-arch §6.4; immortality §1.1, §3, §4.3; Contract §4, §6, §13, §15.2; open_decisions §4; Storage §6 |
| **Seams** | C3 (control and data sockets), C5 (spool), C6 (session record, audit), C8 (recording), C1 (the record), C2 (close 4012), C4 (E2: the grid) |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ipc-control.schema.json` (top: "How a session ends from outside TSB", "The control socket", "Spool"), `#/$defs/channel_closed`, `#/$defs/connection_lost`, `#/$defs/release`, `#/$defs/input_refused`; `ipc-data-frame.schema.json` (top: the data-path paragraph); `spool-entry.schema.json`; `worker-manifest.schema.json` (top: retained sessions); `continuity-state.schema.json#/$defs/session_record`, `#/$defs/connection_record`; `session-record.schema.json` (`state`); `common.schema.json#/$defs/session_state`; `ttyrec.schema.json#/$defs/end_marker`, `#/x-sidecar`, `#/x-derivation`; `audit-event.schema.json#/$defs/session_end`; `vt-ipc.schema.json#/$defs/close`; `ws-frames.schema.json#/x-close-codes` |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files, under
`srs/`: session model = `session-model-and-lifecycle.md`, lang-arch =
`language-architecture-decision.md`, immortality = `immortality-mechanism.md`, Contract = `architecture-contract.md`,
open_decisions = `open-decisions.md`, Storage = `storage-and-discovery-architecture.md`. Socket and file names are
relative to TSB's node-local runtime directory (ipc-control top).

**Words used here.**

- **End from outside TSB**: end_state `terminated`, with end_reason `worker_lost`, `target_closed` or
  `connection_lost`. Neither TSB nor anyone through TSB ended it (session model §10, §12).
- **`CHANNEL_CLOSE`**: SSH_MSG_CHANNEL_CLOSE (RFC 4254 §5.3), the target closing one channel, so one session.
  **`CHANNEL_EOF`**: the target will send no more output; not an end. **`DISCONNECT`**: SSH_MSG_DISCONNECT
  (RFC 4253 §11.1), the target ending the whole connection.
- **E9, E10**: session model §9. E9: the worker died. E10: after a core restart or a re-dial, a worker or session
  is not where its manifest said.
- **Spool**: one file per session end a worker observed or decided, kept until a core audits it (spool-entry top).
- **Data peer**: the core's end of a session's data socket `conn-{conn_id}/{socket}` (ipc-data-frame top).
- **Retained session**: an ended session whose ring, data socket and manifest entry the worker keeps until the core
  sends `release` or the connection closes (ipc-control `release`).
- **End marker**: the recording's last frame, no data, sidecar code 4 for `terminated` (ttyrec `end_marker`).
- **While the core is gone**: no core serves the worker; its sessions are `orphan-pending` (orphan-session).

An end from outside while the core is gone takes the branch **while the core is gone**, steps 10 and 19: the
worker observes the end and spools it (steps 1–4); the returning core reads the spool, writes the end marker and
audit event once, and deletes the entry (→ spooled-end-finalize, with end state `terminated`).

## Path

end observed: PW-TG | (link-liveness) → PW-FS → CO-PW → PW-FS; worker lost: CO-PW → CO-PW (E9) | (hung-worker) | CO-FS (E10);
then CO-BB → CO-PW → CO-DK → CO-PW → PW-FS → CO-FS → CO-VT → SF-CO; while the core is gone: PW-TG | (link-liveness) → PW-FS →
(core-restart) → (spooled-end-finalize)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | PW-TG | TG → PW | SSH | `CHANNEL_CLOSE` on the session's channel, not answering a close TSB asked for; `DISCONNECT` on the connection; or our end of the TCP connection gone (EOF, `ECONNRESET`, `ETIMEDOUT`, `EPIPE`) | target, or the network | The worker MUST read exactly these as ends: `CHANNEL_CLOSE` ends one session; `DISCONNECT` and TCP loss end the connection (session model §8 A1–A3, §9 E5, §13). `CHANNEL_EOF` alone MUST NOT end anything (ipc-control top). The worker and the core MUST NOT act on an exit status or exit signal (RFC 4254 §6.10: optional, unreliable on network gear): it decides nothing, at this release the contract does not carry it, and carrying it to the clients as data is E5's, with exec (session model §12); they MUST NOT match a prompt (Contract §6; session model §12, §14) [owed: C3/ends-from-outside grades the events and the audit, not the worker's reading of SSH]. **E3:** telnet has no end message; a closed telnet socket is a lost connection, `connection_lost` `transport` [C3/telnet-socket-close, reference, owed by the E3 worker and core plans]; serial arrives with a later release. `single_session` lists no session and ends none — except at the bound `heartbeat_interval × heartbeat_missed` from the end, with a control peer attached, when it lists the connection's one session and the core treats it as any listed session (it reads the spool first) (ipc-control `connection_lost`). | — | the worker |
| 2 | — | PW → PW | — | → link-liveness | worker | `link_timeout` fired: the worker closes the connection and reports cause `keepalive_timeout` (step 4). link-liveness owns the timer and its probes. | — | — |
| 3 | PW-FS | PW → FS | a file, `spool/{session_id}.json` | `spool-entry.schema.json`, one per session that ended | worker | For every end it observes from outside TSB, whether or not a core is attached, the worker MUST write the entry atomically (a temporary file renamed over it) and fsync it before it acts on the end, because "the worker cannot know a sent event was consumed" (spool-entry top; ipc-control top "Spool") [C5/spool-entry-shape]. Whether sending step 4's event is acting on the end, so the entry comes first, is not said (Open items). The pair is `terminated` / `target_closed` or `connection_lost`, by step 5's mapping; `ended_at` is the worker's clock. The entry MUST be the closed shape, with no credential and no terminal byte [C5/spool-entry-shape]. A dead worker spools nothing, so `worker_lost` is never here [C5/spool-entry-shape]. | — | the core that audits the end: step 16, or spooled-end-finalize step 8 while the core is gone |
| 4 | CO-PW | PW → CO | unix socket `conn-{conn_id}/control.sock`, one JSON object per line | `channel_closed` `by: target`; or `connection_lost`, `cause` `target_disconnect`, `transport` or `keepalive_timeout` | worker | `channel_closed` by target MUST name only an open session whose close the core did not ask for; its siblings and the connection live on (Contract §15.2) (`by: worker` is session-end-by-tsb's: its step 23a) [C3/ends-from-outside; C3/control-connect-openshell-sequence; `by` required: C3/control-message-shape]. `connection_lost` MUST name the connection and list exactly the sessions still open on it, retained ones not (`connection_lost`; lang-arch §6.4 step 3); a session whose end the worker has begun but not yet reported (its own timer's end, in its drain) is listed, and its spool entry holds that end, rewritten with the ring's final numbers before `connection_lost` is sent (ruling 2026-10-14: steps 3–5 for every listed session before `connection_lost`) (step 5) [U3 `worker/tests/test_end_during_handshake.py` `test_a_transport_loss_mid_drain_rewrites_the_ending_sessions_spool_entry_before_connection_lost`]; whether a session whose `close_channel` is in flight counts is not said (Open items) [C3/connection-lost-lists-sessions; C3/control-message-shape]. A bare TCP loss MUST be `transport` [C3/control-message-shape]. After `connection_lost` nothing more crosses this socket [C3/control-connect-openshell-sequence]; the worker MUST close the connection, and MUST NOT exit while it holds another (lang-arch §6.4 step 4; immortality §2.2) [owed]. `reason` is free text for the log (ipc-control `channel_closed`). | — | the core. A connection left with no live session arms `connection_linger` (connection-linger) |
| 4a | PW-FS | PW → FS | a file `workers/{worker_id}.json` | `worker-manifest.schema.json`, without the lost connection and its retained sessions | worker | After `connection_lost`, the worker MUST rewrite its manifest atomically on this change (worker-manifest top: "on every change"; a retained session stays listed "until the core releases it or its connection closes") [C5/worker-manifest-valid grades the shape; the rewrite is owed]. A worker that crashed leaves its manifest in place (worker-manifest top). | — | a restarted core (core-restart) |
| 5 | — | CO → CO | — | maps the event to an end | the core | `channel_closed` by target → that session ends `terminated` / `target_closed`. `connection_lost` → every listed session ends `terminated` / `target_closed` for `target_disconnect`, `terminated` / `connection_lost` for `transport` and `keepalive_timeout` (ipc-control top; session model §13) [C3/ends-from-outside; C3/connection-lost-lists-sessions]. The core MUST read a `cause` it does not know as `transport`, and a `by` it does not know as `target`: an end it cannot place is an end from outside TSB (ipc-control top) [owed]. It MUST NOT parse `reason` [C3/ends-from-outside grades the audit against `by` and `cause` alone]. `channel_closed` `by: worker` is not an end from outside TSB (→ session-end-by-tsb step 23b); one missing any of its six fields, or with one outside its enum, is an end the core cannot place, read as `by: target`, and logged as a worker defect with the session's spool entry pair when there is one. For each session a `connection_lost` lists, the core MUST read its spool entry first and, when there is one, record that entry's end through spooled-end-finalize (the session is not retained: its connection is gone, so step 4 there declares what the recording lacks), as it reads the spool before E10; only a listed session with no entry ends by the cause [U2 `core/internal/session/worker_end_test.go` `TestAConnectionLostListingASessionWithASpoolEntryRecordsTheSpooledEnd`]. `linger` lists no session (`connection_lost`). `disconnect` answers the core's own `disconnect` (→ connection-linger step 16): a listed session the core has already ended keeps that end; one it has not ended is an internal error, and the core MUST end it `terminated` / `connection_lost` and log it (DEFAULT 15, accepted by the operator 2026-09-26: e0-core plan, "What ends what"; queue K9) [owed]. The contract README says otherwise: stale text (Open items). | — | — |
| 6 | CO-PW | PW → CO | unix socket `control.sock` | EOF or error, with no `connection_lost` before it | worker (its control socket closes, or its process is gone) | Not yet an end. A worker MUST NOT close a control socket except after that connection's `connection_lost` or by exiting, never to shed a slow core: a slow core is backpressure, a stuck one is `core_heartbeat`'s (ipc-control top, "The control socket") [C3/control-socket-stays-open]. | — | — |
| 7 | CO-PW | CO → PW | unix socket `control.sock` | one dial of `control.sock`, SO_PEERCRED as on every dial (conventions V10) | the core | The core MUST dial once. If the worker answers, the core carries on and ends nothing. Only a refused or missing socket is E9: every session on that connection ends `terminated` / `worker_lost`, since a false E9 would end live sessions (ipc-control top; session model §9 E9) [C3/control-socket-stays-open]. E9's other trigger, the supervisor reporting that the process exited (session model §9 E9), has no message at this tag: unspecified (not in plan §7). | — | — |
| 8 | — | CO → CO | — | → hung-worker | the core | A worker whose loop stopped answering `ping` is killed through its supervisor; every session in it ends `terminated` / `worker_lost`, through steps 11–18 (session model §11 `worker_heartbeat`, T11). | — | — |
| 9 | CO-FS | FS → CO | a file, `spool/{session_id}.json` | `spool-entry.schema.json`, read | the core | E10, raised by core-restart (a manifest's socket refuses, is missing, or fails SO_PEERCRED), data-socket-redial (the re-dial fails) or reattach-handshake (`no_such_session`) (session model §9 E10). The core reads the spool for that session first (→ core-restart step 8, which owns the read order). An entry there is the session's end (→ spooled-end-finalize step 1: through step 19 for a `terminated` entry, session-end-by-tsb step 27 for a `killed` one); with the core running, clients may be attached, and spooled-end-finalize step 9 closes them. Only without one does the session end `terminated` / `worker_lost` (spool-entry top). | — | — |
| 10 | — | CO → CO | — | → core-restart | the core | **While the core is gone.** No core read step 4 (the core was gone), or the core died before step 13. The core starts again and takes back what survived: core-restart owns the restart and its order; this branch owns the spooled end and finalizes it through spooled-end-finalize (step 19); orphan-session owns the heartbeat (orphan-session step 9). The returning core writes the end from the spool, stamped with the worker's `ended_at`. A session the worker still retains is replayed first by its handshake, ending in `release` (reattach-handshake steps 15–16; worker-manifest top; spool-entry top). | — | as core-restart says |
| 11 | CO-BB | CO → BB | bbolt write | `continuity-state.schema.json#/$defs/session_record` | the core | The core MUST persist state `terminated`, `end_reason` and `ended_at` the first time the end is known, and MUST NOT change them after: a session ends once (`session_record` `end_reason`; session model §12) [C6/continuity-record-valid grades the shape; the once-only rule is graded through the audit by C6/event-session-ended]. The nested northbound record reads `terminated` and carries no countdown; there is no `disconnected` state (session-record `state`) [C1/session-ended-unexpectedly]. | — | any principal reading the record (session-list) |
| 12 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | data: the session's last output frame, then EOF | worker | After an end it observed (steps 1–4), the worker MUST write the session's last output frame and then close that data peer. That EOF, not the event alone, tells the core its recording holds the output, and the core MUST record up to it before step 13 (ipc-data-frame top) [C3/ended-release-attached]. A lost worker sends no orderly EOF (Open items). | — | — |
| 13 | CO-DK | CO → DK | files `<session_id>.ttyrec` + `.dir` | `ttyrec.schema.json#/$defs/end_marker`, sidecar code 4 | the core | The core MUST write exactly one end marker, as the last frame, with no data and its code the end_state, never the reason, and none while the session lives (ttyrec `x-sidecar`: "present exactly when the session has ended") [C8/end-marker-terminated; C8/ttyrec-shape; a live recording has none: C8/end-marker-killed]. Its stamp is the time the session ended as the core observed it (for `worker_lost`, when it found the worker dead), derived as frames are (`x-derivation`); a session whose spool entry the core recorded at step 5 is stamped with the entry's `ended_at` (spooled-end-finalize step 5) [C8/worker-death-recorded]. | — | — |
| 14 | CO-DK | CO → DK | a file, the audit log (append-only JSONL) | `audit-event.schema.json#/$defs/session_end` (`session.disconnect`) | the core | `terminated` with the end_reason of step 5, 7, 8 or 9; no `user`, since no principal ended it; `ts` the time the core observed the end, or the entry's `ended_at` for a session whose entry the core recorded at step 5 (spooled-end-finalize step 6); `session_type` always; `recording_path` absent, never empty, when there is no recording. The core MUST write it exactly once per session [C6/event-session-ended; C3/ends-from-outside; C8/worker-death-recorded; C6/event-recording-path-absent-not-empty]. | — | auditors |
| 15 | CO-PW | CO → PW | unix socket `control.sock` | `release` | the core | After `channel_closed` only. The core MUST send it at once after steps 13 and 14, when the recording holds the session to its end: the core "writes the end marker and the audit event, and sends release at once" (C3/ended-release-attached description; ipc-control `release`) [C3/ended-release-attached; its U2 test grades EOF → end marker → `release`, so the audit's place is owed]. Until `release`, or until its connection closes, the worker MUST keep the session's ring, data socket and manifest entry [C3/ended-release-attached; C3/ended-retention-bounded], within the connection's `buffer_bytes` per session (`release`) [owed]. Once the connection is lost or lingers out, the core MUST send nothing more on it: the close released the retained sessions [C3/control-connect-openshell-sequence; C3/ended-retention-bounded]. The connection record lists only sessions not yet released (`connection_record` `session_ids`). | — | — |
| 15a | PW-FS | PW → FS | a file `workers/{worker_id}.json` | `worker-manifest.schema.json`, without the released session | worker | On `release` the worker drops the ring and the data socket and MUST stop listing the session, rewriting its manifest atomically (worker-manifest top; `release`) [C3/ended-release-attached]. | — | a restarted core (core-restart) |
| 16 | CO-FS | CO → FS | a file | the step 3 entry, deleted | the core | Once its audit event is written, the core MUST delete the entry, so every entry is deleted by the core that audits its end (spool-entry top) [owed: C5/spool-entry-shape grades only the returning core's delete]. An entry for a session already audited is deleted unread (→ core-restart step 8). | — | — |
| 17 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | `vt-ipc.schema.json#/$defs/close` | the core | **E2.** PTY shell sessions only (vt-ipc `open`). Unspecified (decision queue): the contract does not say the core sends `close` at an end, as session-end-by-tsb step 18 and spooled-end-finalize open item 1 also say. A grid is dropped only on `close` or on the VT worker's exit (vt-ipc `close`); immortality §3, "Grid: Discarded", covers a worker crash only. | — | — |
| 18 | SF-CO | CO → SF | WebSocket | close 4012 `session_terminated` (ws-frames `x-close-codes`) | the core | The core MUST close every client attached to the session with 4012 (session model §12; lang-arch §6.4 step 6) [C1/session-ended-unexpectedly grades `worker_lost`; owed for `target_closed` and `connection_lost`]. A listed session whose spool entry the core recorded at step 5 as `killed` is closed 4010 (spooled-end-finalize step 9). The session is no longer attachable: a later attach is closed 4004 (session-attach). The core MUST NOT reopen it: a reconnect is a new session (session-open), on a new connection when the end took the connection with it (lang-arch §6.4 step 7, of a lost connection; session model §10, §14); after `channel_closed` by target the connection lives and stays a reuse candidate (Contract §15.2; `SessionCreateRequest`) [C1/session-ended-unexpectedly grades the state rule; the rest owed]. | — | every attached client; everyone else reads the record (session-list) |
| 19 | — | CO → CO | — | → spooled-end-finalize | the core | **While the core is gone**, on its return. The returning core finalizes the spooled `terminated` end, end marker code 4: it reads the spool, replays a retained session, writes the record, the recording's markers and the audit event once, sends `release` and deletes the entry. spooled-end-finalize owns those steps and their MUSTs. | — | as spooled-end-finalize says |

## Refusals and who is told

- **Input in flight at the end.** The worker does not deliver an input frame for an ended channel. It answers
  `input_refused` `ended`, naming the frame's seq, and counts and logs it (ipc-control `input_refused`)
  [C3/input-refused-ended]. Input the core recorded before sending stays in the recording (ttyrec `x-sidecar`, input
  rule; Contract §13). C2's `input_refused` has no `ended` reason, so a client learns of the end from 4012 alone.
- **A control socket that ends and re-dials.** Steps 6–7 end nothing when the worker answers, and nothing northbound
  says the socket ended.
- **Siblings.** `channel_closed` ends one session; its siblings and their clients are untouched (Contract §15.2).
  `connection_lost` ends every session on its connection; other connections, even in the same worker, have their
  own transports. A dead worker ends every session on every connection in it: the worker is the blast radius
  (session model §13 and its worked example; Contract §15.3).
- **Who is told.** Attached clients by 4012 (step 18). Every other principal reads `terminated` in the record
  (session-list; C2/session-closed-close-4010: "GET /sessions/{id} gives the rest"). Auditors read `session.disconnect`
  (step 14). No operator alert is raised for an end; a failed kill under hung-worker raises `worker_unkillable` (operator-alerts).
- **A spooled end** found by the returning core closes no client; one a running core finds (steps 5, 9) closes every
  attached client with its end state's code: 4012 for `terminated`, as every end from outside, and 4010 for a `killed` entry (spooled-end-finalize step 9).

## Security considerations

- **Who may type.** From the end no byte reaches the target: the worker refuses in-flight input (`ended`), and the
  session is not attachable after 4012.
- **Trust.** Step 7's re-dial is the same trusted dial as every other: SO_PEERCRED alone (immortality §6.1;
  conventions V10). A peer that fails it is never a live worker; whether that is E9 or E10 is open (Open items).
- **The host boundary.** The 4012 close and the record name no pid, path or hostname (Contract §4; conventions V11)
  [owed]. The worker's `reason` is for the log (ipc-control top), and no clause sends it northbound.
- **Credentials.** The spool carries no credential and no terminal byte [C5/spool-entry-shape]. What the returning
  core writes from it: spooled-end-finalize, "Security considerations".

## Open items

Unless marked OPEN or stale text, all are unspecified and not in plan §7.

- **E9 reported by a supervisor.** Session model §9 E9 lets the supervisor report that a worker exited. No
  supervisor message carries this at contract-v0.3, so step 7 is the only E9 path.
- **The spool before E9.** The core reads the spool before an E10 decision (step 9), not before E9. If a worker
  spooled an end and died before sending its event, step 7 ends the session `worker_lost`. Its entry is deleted
  unread at the next spool read (step 16). Whether the core reads the spool first is unstated.
- **A dead worker's other connections.** Session model §13 ends every session on every connection of a dead
  worker. The contract's rule acts per control socket (ipc-control top). Not stated: whether the core ends the
  worker's other connections at the first refused re-dial, or as each of their control sockets ends.
- **Output lost with a worker.** On `worker_lost` the ring dies with the worker, and no count of the lost bytes
  exists. So no gap marker is defined, and the recording ends at the last frame the core recorded (immortality §3,
  §4.3). Whether the recording says that output may be missing is unstated.
- **The wait for step 12's EOF.** No bound is stated for how long the core waits for the data peer's EOF before
  steps 13–15.
- **Order.** The order of steps 11, 17 and 18 relative to steps 12–16 is not stated. The contract fixes EOF (12) and
  the end marker (13) before `release` (15), and the spool delete (16) after the audit (14); for a returning core the
  whole order (spooled-end-finalize Open item 2). For a live end the end marker and the audit event come before
  `release` (C3/ended-release-attached description); `release` against the delete is unstated.
- **The VT `close`** (step 17): whether the core sends it at an end is unspecified (decision queue; session-end-by-tsb open
  item 7).
- **The spool and step 4's event.** The worker spools "before it acts on the channel's end" (spool-entry top;
  ipc-control top "Spool"; U3 `test_spool.py`: "before it is acted on"). Whether sending `channel_closed` or
  `connection_lost` is acting on it, so the entry is fsync'd before the event, is not said (step 3).
- **A session awaiting `channel_closed`.** `connection_lost` lists "every session still open on it"; lang-arch §6.4
  step 3 says "every session riding it". Whether a session whose `close_channel` is in flight counts is not said
  (step 4; connection-linger step 14).
- **OPEN — a control re-dial that fails SO_PEERCRED.** ipc-control top, "The control socket", makes a refused or
  missing re-dial E9; session model §9 places E10 "at rediscovery or reattach". The end reason is `worker_lost`
  either way, but the core reads the spool before E10 only (step 9), so the class decides whether a spooled end is
  found (step 7; Security considerations).
- **Contract text stale (queue K9).** Contract `README.md` "Senders and receivers" ends a session `terminated` on
  "a `connection_lost` of any cause but the core's own `disconnect`", which leaves a session listed in the answer
  to the core's own `disconnect` with no end state the audit `session_end` admits. DEFAULT 15, accepted by the
  operator 2026-09-26, ends it `terminated` / `connection_lost`, logged as an internal error; step 5 follows the
  ruling, and the README sentence awaits its fix (queue K9).

## Picture

pending

**Recording hold exception.** While held, write no frame or end marker to either pair. Persist the session end and `session.disconnect` at the end, on the original generation-0 recording path, before a marker exists. If replay is gone, the linked successor holds only the sole end marker and the notice at P declares the unknown remainder. After every end marker sync, write `recording_end_placement` `present` and its end offset to bbolt (terminal-output-accounting §5.3; owed).
