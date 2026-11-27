# session-end-by-tsb — a session ended by TSB or through it

| | |
|---|---|
| **ID** | session-end-by-tsb |
| **Purpose** | End one session through TSB — its owner or an admin over REST, the owner's yes to "Kill the session?", or a timer (`idle`, `unattended`, `orphan`) — closing that one channel, finishing its recording and audit record, and closing every attached client with 4010; while the core is gone, the worker ends and spools it, also tells a core that reads its control socket at the end (`channel_closed` `by: worker`), and the returning or present core records it. |
| **Status** | built (E0–E1); step 18 (the VT grid's `close`) is E2 |
| **Corpus clauses** | Contract §4, §10, §12, §13, §15.2; PTY §7.1; Storage §6, §9; session model §3, §7, §9 E5, §11 (`idle`, `unattended`, `orphan_timeout`, `quiet_prompt`, `connection_linger`), T5, §12, §13; immortality §1.1, §4.3, §8; lang-arch §6.3, §6.4; open_decisions §5 |
| **Seams** | C1, C2, C3, C4 (E2), C5, C6, C8 |
| **Schemas** (`contract/schemas/` and `contract/openapi/` at contract-v0.3) | OpenAPI `DELETE /sessions/{id}` (`closeSession`), `#/components/responses/Unauthenticated`, `#/components/responses/Forbidden`, `#/components/responses/SessionNotFound`; `ipc-control.schema.json` (top: "Spool"), `#/$defs/close_channel`, `#/$defs/channel_closed`, `#/$defs/release`, `#/$defs/input_refused`; `spool-entry.schema.json`; `vt-ipc.schema.json#/$defs/close`; `ttyrec.schema.json#/$defs/end_marker`, `#/$defs/gap_marker`, `#/x-sidecar`; `audit-event.schema.json#/$defs/session_other`, `#/$defs/session_end`; `continuity-state.schema.json#/$defs/session_record`; `session-record.schema.json`; `worker-manifest.schema.json`; `ws-frames.schema.json#/x-close-codes` (4010) |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files are under
`srs/`.

**Words used here.**

- **killed** and **terminated** are the two end states. `killed` is an end by TSB or through it: a timer, the owner,
  an admin. `terminated` is an end from outside TSB, which is session-end-from-outside's (session model §12; conventions V13).
- The **owner** holds the owner token, the session's lifecycle authority, which never moves by itself (Contract §10;
  PTY §7.1). An **admin** is a principal holding the `session.terminate` permission, "terminate any session"
  (Storage §9; conventions V9).
- The **end marker** is the recording's last frame: no data, sidecar code 3 for `killed` (ttyrec top, `x-sidecar`).
- The **data peer** is the core's end of the session's data socket `conn-{conn_id}/{socket}` (ipc-data-frame top).
- **Retained**: a worker keeps an ended session's ring, data socket and manifest entry until the core sends
  `release`, so a core that dies before recording the tail loses nothing silently (ipc-control `release`).
- **While the core is gone**: no core serves the worker; its sessions are `orphan-pending` and the worker runs
  `idle`, `unattended` and `orphan_timeout` itself (orphan-session).
- **Spool**: one file per session end a worker originated or observed, `spool/{session_id}.json`, kept until a
  core audits it (spool-entry top).

An immortal session still ends when a timer fires or its owner or an admin ends it (immortality §1.1). session-end-by-tsb owns
every `killed` end, whether or not the core is present (the operator, 2026-10-07). quiet-prompt asks the question,
session-countdown computes the countdown, and orphan-session runs the timers while the core is gone; each gets one step
here (plan §2.1). An end while the core is gone takes the branch **while the core is gone**, steps 21–27: the worker
decides the end and spools it; the returning core reads the spool, writes the end marker and audit event once, and
deletes the entry (→ spooled-end-finalize, with end state `killed`). When a core reads the worker's control socket as the end completes — a returning core that has opened the session or sent `hello` for it, such as an end that waited for that handshake's `live` — the worker also reports the end (step 23a), and that core records it at once from the event, with no replay: the core computes the gap from the event's out_bytes, not from the spool entry, so output the worker still holds and the core did not receive is declared lost (step 23b). Before that peer's first `ping` or `hello` the end stays in the spool, and the take-back handshake finds it.

The core's own `disconnect` (→ connection-linger) is not an end through TSB. A session its answer lists that the
core has not ended is an internal error: it ends `terminated` / `connection_lost` and is logged (DEFAULT 15,
accepted by the operator 2026-09-26; → session-end-from-outside step 5).

## C3 0.8 terminal branch

For C3 0.8 PTY sessions within [its §1 scope](terminal-output-accounting.md#1-scope-and-acceptance), [terminal output accounting](terminal-output-accounting.md) §§3/6/9 C/D extends steps 12–16:
the configured output_drain_timeout bounds the terminal wait; timeout or core stop is not clean EOF.
If still incomplete, code 9 precedes the sole original end, then the lifecycle audit and unconfirmed
fact; withhold core release until sealing and durable accounting. A complete end is never rewritten.
Stop admits no new timer action after Close begins (tsb-core #680), never sends seal/release/disconnect
for a still-live session, and cannot manufacture control completion/EOF
[C3/core-stop-during-end-drain; C8/terminal-incomplete-before-end; C3/terminal-drain-timeout].

## Path

SF-CO | (quiet-prompt) | (session-countdown) → CO-BB → SF-CO → CO-PW → PW-TG → CO-PW → CO-DK → CO-PW → PW-FS → CO-VT → SF-CO →
(connection-linger); while the core is gone: (orphan-session) → PW-FS → PW-TG → PW-FS → (connection-linger) → (core-restart) →
(spooled-end-finalize); a core on the control socket at the end: (orphan-session) → PW-FS → PW-TG → PW-FS → CO-PW → CO-BB → SF-CO → CO-DK → CO-FS → CO-PW → PW-FS → (connection-linger)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `DELETE /sessions/{id}` (`closeSession`) | the owner, or an admin | Asks to end the session. | The core MUST refuse 403 `forbidden` to a principal that can see the session but is neither owner nor admin, and the session lives on [C1/session-delete-not-owner-403]. It MUST answer 401 `unauthenticated` without a valid login cookie, and 404 `session_not_found` for no such session or one the principal may not see (OpenAPI `closeSession` responses) [owed]. | the caller |
| 2 | — | CO → CO | — | → quiet-prompt | the owner's client | The owner answers `quiet_prompt_answer` `kill: true`. That ends the session killed / owner, "as DELETE /sessions/{id} by the owner does" (ws-frames `quiet_prompt_answer`; session model §11 `quiet_prompt`). quiet-prompt owns who is asked and which answer counts [C2/quiet-prompt-owner-only]. | — | as quiet-prompt says |
| 3 | — | CO → CO | — | → session-countdown | the core | The countdown session-countdown computes runs out: `idle` (no byte either way) or `unattended` (no client byte) fires. While the core runs, the core evaluates both, and when one fires it MUST end the session killed / `idle` or `unattended` (session model §11; lang-arch §6.3: "TSB timeout fires → Go ends the session → tells the Python worker to close that channel") [owed: no fixture runs a day-scale timer]. | — | — |
| 4 | — | PW → PW | — | → orphan-session | the worker; the core on its return | While the core is gone the worker runs `idle`, `unattended` and `orphan_timeout` itself (ipc-control `timer_values`), and one fires (orphan-session step 7). Its end is this protocol's, in the branch while the core is gone (steps 21–27): the worker spools it, and the returning core records it. Steps 5–20 do not run for it. With a core reading the control socket at the end, steps 23a–23b run in place of steps 26–27: step 23b records the end as steps 13 and 15–20 record one the core decides, with the worker's stamp, a gap marker when the spool entry counts output the recording lacks, and no replay. | — | as steps 21–27 say |
| 5 | — | CO → CO | — | — | the core | Fixes the end: `killed`, with end reason `owner` (the owner's request or yes), `admin`, `idle` or `unattended` (OpenAPI `closeSession`; session model §13). A session ends once: the first end the core knows is the end and is held unchanged after (continuity-state `session_record.end_reason`), so the core MUST NOT write a second end marker or a second `session.disconnect` [C8/end-marker-killed; C6/event-session-ended]. | A request or a yes for a session already ended: unspecified (not in plan §7). | — |
| 6 | CO-BB | CO → BB | a bbolt write | `continuity-state.schema.json#/$defs/session_record` | the core | The nested record's `state` becomes `killed`, with `end_reason` and `ended_at`; the countdown leaves the record (session-record `oneOf`). The core MUST persist the reason when it acts (spool-entry top: "the core knows every end it asked for and persists its reason when it acts") [shape: C6/continuity-record-valid; the timing: owed]. The record then reads `killed` with no countdown (→ session-list) [C1/session-delete; C1/session-record-shape-v0.2]. | — | any principal reading the record (session-list) |
| 7 | SF-CO | CO → SF | HTTPS | 204 | the core | DELETE only. The core MUST answer 204 only once the record reads `killed` [C1/session-delete]. Whether the 204 also waits for steps 8–19 is unspecified (not in plan §7). | — | the caller |
| 8 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock` | `close_channel` | the core | Names the one session. Its sibling sessions and the connection MUST survive (Contract §15.2) [C3/control-resize-close-disconnect; C1/session-delete]. A telnet connection is the exception: it carries one session and closes with it, reporting `connection_lost` cause `single_session` once this end has reached the core [C3/telnet-one-session, reference, owed by the E3 worker plan]. The core MUST send it to a v0.1 worker's unreachable session too: owner and admin ends and its idle and unattended timers reach it as `close_channel` (ipc-control top) [C3/n1-v0.1-worker-unreachable states it, but the tests it names do not grade it: owed]. | — | the worker |
| 9 | PW-TG | PW → TG | SSH | `SSH_MSG_CHANNEL_CLOSE` on the session's channel (RFC 4254 §5.3); not contract | worker | Closes the channel: end the session and TSB closes its channel (session model §3). | — | the target |
| 10 | PW-TG | TG → PW | SSH | `SSH_MSG_CHANNEL_CLOSE` in reply (RFC 4254 §5.3); not contract | target | Not an end signal: E5 counts only a close "not in reply to a close TSB asked for" (session model §9 E5). Whether the worker waits for this reply, and what it does when none comes, is unspecified (not in plan §7). | — | the worker |
| 11 | CO-PW | PW → CO | unix socket `control.sock` | `channel_closed`, `by: core` | worker | The worker MUST answer `close_channel` with `channel_closed` for that session, `by: core` [C3/control-resize-close-disconnect]. `reason` is free text and never parsed (ipc-control top). | An input frame that reaches the worker after the channel ended is not delivered: `input_refused` `ended` [C3/input-refused-ended]. | the core |
| 12 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | end of file on the data peer, after the session's last output frames (→ byte-relay carries those) | worker | The worker writes the last output frame and closes that data peer (`C3/ended-release-attached` description). Its order relative to step 11 is not stated. | — | the core |
| 13 | CO-DK | CO → DK | file `<session_id>.ttyrec` + `.dir` | `ttyrec.schema.json#/$defs/end_marker`, sidecar code 3 | the core | When the session is recorded (`recording.active`). On a clean drain the core MUST record the output up to the data peer's end of file, then exactly one end marker, as the last frame, stamped with the time the session ended on the core's clock, and no marker while the session lives: it is "present exactly when the session has ended" (ttyrec top; the `x-sidecar` end-marker rule; C8/end-marker-killed: "a live session's recording has none") [the marker: C8/end-marker-killed; output first: C3/ended-release-attached grades it for a target's close, so for this end it is owed]. Input that reached the core before the end is already recorded, since input is recorded before it is sent [C8/input-recorded-before-sent]. | — | — |
| 14 | CO-DK | CO → DK | file, the audit log (append-only JSON lines) | `audit-event.schema.json#/$defs/session_other`, `action` `session.terminate` | the core | DELETE only: `user` is the principal who chose the end (audit-event top). It MUST come before step 15, which closes the session's audit record (audit-event top) [shape: C6/event-vocabulary; that a DELETE writes it: owed]. | — | readers of the audit log |
| 15 | CO-DK | CO → DK | file, the audit log | `#/$defs/session_end`, `action` `session.disconnect` | the core | The core MUST write exactly one: `end_state` `killed`, `end_reason` from step 5, `user` the principal for `owner` and `admin` and none for a timer, `session_type`, `ts` the core's clock, and `recording_path` naming the `.ttyrec` or absent, never empty (Storage §6) [C6/event-session-ended; C6/event-recording-path-absent-not-empty]. Its end state MUST match the recording's end marker [C8/end-marker-killed]. | — | readers of the audit log |
| 16 | CO-PW | CO → PW | unix socket `control.sock` | `release` | the core | On the clean fully recorded path the core was attached at the end, so it MUST send `release` at once, after the end marker and the audit event: for an end while a core is attached the core "writes the end marker and the audit event, and sends release at once" (C3/ended-release-attached description; ipc-control `release`) [its U2 test grades EOF → end marker → `release` for a target's close; the audit's place, and this end: owed]. A v0.1 worker gets no `release` (ipc-control top) [owed]. Until `release`, or until its connection closes, the worker MUST retain the session [C3/ended-release-attached]. | — | the worker |
| 17 | PW-FS | PW → FS | file `workers/{worker_id}.json` | `worker-manifest.schema.json`, without the released session | worker | The worker drops the ring and the data socket and MUST stop listing the session (worker-manifest top; `release`) [C3/ended-release-attached]. | — | a restarted core (core-restart) |
| 18 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | `vt-ipc.schema.json#/$defs/close` | the core | **E2.** Only for a session with a grid, a PTY shell session, the only kind that gets `open` (vt-ipc `open`). The VT worker MUST drop the grid on `close`, and otherwise keep it until it exits (vt-ipc `close`, D19, open) [owed]. That the core sends `close` when a session ends is the plan's mapping (plan §3, edge CO-VT); no contract sentence says when, so the step is unspecified (not in plan §7). | — | the VT worker |
| 19 | SF-CO | CO → SF | WebSocket | close code 4010 `session_closed` (`ws-frames.schema.json#/x-close-codes`) | the core | The core MUST close every attached client with 4010 (OpenAPI `closeSession`; session model §12) [C2/session-closed-close-4010, one client after a DELETE; several clients, and a timer's end: owed]. A client MUST read any 4xxx code it does not know as 4010 (`x-close-codes`; conventions V3) [owed]. An attach to the ended session gets 4004 (→ session-attach). | — | every attached client |
| 20 | — | PW → PW | — | → connection-linger | the worker | When this was its connection's last live session, `connection_linger` arms; the connection and its login stay up for a quick reuse (ipc-control `release`; session model §11) [C1/session-create-reuse-empty-connection]. | — | — |
| 21 | — | PW → PW | — | a timer fires | worker | **While the core is gone.** The worker MUST end the session `killed` / `orphan` when `orphan_timeout` fires, every orphan, silent or chatty; `killed` / `idle` or `killed` / `unattended` when one of those fires (session model §11, T5; immortality §8) [`orphan`: C6/event-spooled-end; `idle` and `unattended` fired by the worker: owed]. | — | — |
| 22 | PW-FS | PW → FS | a file, `spool/{session_id}.json` | `spool-entry.schema.json` | worker | Before it acts on the end (step 23), the worker MUST write the entry atomically (a temporary file renamed over it) and fsync it (spool-entry top) [C5/spool-entry-shape]. It MUST be the closed shape, with step 21's pair, `ended_at` by the worker's clock, and `last_seq`, `out_bytes` and `last_out_ts` as spool-entry defines them; no credential, no terminal byte [C5/spool-entry-shape]. An end from outside TSB while the core is gone (orphan-session step 6) is spooled the same way, by session-end-from-outside step 3. When the end's drain of the channel is done (ruling 2026-10-14), the worker MUST rewrite the entry the same way with the ring's final `last_seq`, `out_bytes` and `last_out_ts`, before it reports the end (step 23a), or, when the connection's loss cut the end short, before `connection_lost` (session-end-from-outside step 4); from 0.4, `ended_at` is never earlier than `last_out_ts` (spool-entry `ended_at`) [U3 `worker/tests/test_spool.py` `test_a_worker_ends_ended_at_is_never_earlier_than_its_last_output_stamp`]. The spool entry keeps the numbers whether or not a core is attached; when a core is attached, the event carries them. | — | the returning core (step 27, → spooled-end-finalize) |
| 23a | CO-PW | PW → CO | unix socket `control.sock` | `channel_closed`, `by: worker` | worker | A timer's end with a control peer present. After the channel's tail is in the ring and the entry rewritten (step 22) — and, when that core's handshake for the session reached `live`, after the tail went out on the data peer and the data peer closed (step 12) — the worker MUST report the end it decided as `channel_closed` `by: worker`, with `end_state`, `end_reason`, `ended_at`, `out_bytes`, `last_seq` and `last_out_ts` as its final spool write states them (the values it wrote, or tried to write when that write failed), and `reason` free text naming the timer. It MUST choose `by` when it sends the event: `by: core` when the core's `close_channel` for the session reached it first (step 11), else `by: worker`; never `by: target`. With no control peer the event is not sent, and the spool alone carries the end (steps 26–27). It MUST NOT send the event for a session the control peer knows only as rediscovered, one that peer has neither opened (`open_shell`) nor sent `hello` for, such as one held back for its realign (core-restart step 15): that end stays in the spool, and the session's own take-back handshake finds it (`hello_ack` `ended: true`, spooled-end-finalize), so its retained tail is replayed, not declared lost [C3/channel-closed-by-worker; shape: C3/control-message-shape-v0.4]. | — | the core |
| 23b | CO-BB, CO-DK, CO-FS, CO-PW, SF-CO | CO → BB, DK, FS, PW, SF | bbolt; `<session_id>.ttyrec` + `.dir`; the audit log; `spool/{session_id}.json`; `control.sock`; WebSocket | `session_record`; `gap_marker` when due; `end_marker` code 3; `session_end` (`session.disconnect`); the entry deleted; `release`; close 4010 | the core | The core MUST end the session `killed` with the event's `end_reason`, never `terminated` (session model §12), and persist it at once with `ended_at` as the worker stamped it, its own `idle` and `unattended` stopped with it. Outside a recording hold it records the end at once, from the event, as steps 13 and 15–20 record an end the core decides, and replays nothing, with three differences [C3/channel-closed-by-worker]: (1) The end marker (step 13) and `session.disconnect`'s `ts` (step 15) carry `ended_at`, never the core's clock, and `session.disconnect` has no `user`, whether or not the spool entry is on disk [U2 `core/internal/session/worker_end_test.go` `TestAWorkerDecidedEndIsStampedWithTheEventsEndedAtEvenWithNoSpoolEntry`]. (2) Before the end marker, once nothing more of the session's output can be recorded (its data peer reached end of file, the core closed it at the end of its drain, or a held-back session's data socket was never dialled), the core MUST compare what the event carries with what its recording holds. For a recorded session whose entry's `out_bytes` exceed the output bytes its recording holds (`out_bytes_committed` plus what it keeps past the commit point, gap markers by their bytes; ttyrec `x-sidecar` reopen), it MUST write one gap marker as spooled-end-finalize step 4 writes it — the event's `out_bytes` minus those bytes lost, over the span from the stamp of the last output frame or gap marker it recorded to the event's `last_out_ts`, the event's `last_seq` its seq — though the worker still retains the session [U2 `TestAWorkerDecidedEndUnderARunningCoreDeclaresTheTailItDidNotDeliver`]. Every byte the core recorded, live or from a replay, and every byte a gap marker already declared, is in the bytes the recording holds, so it is never declared again [U2 `TestAWorkerEndsGapCountsOnlyWhatTheRecordingLacks`]. With no entry on disk there is nothing to compare, and nothing is declared. This is a declared loss (immortality §4.3): output the worker still held when it reported the end, and the core had not received, is not replayed. (3) The entry is deleted once the audit event is written (spooled-end-finalize step 8), and `release` follows (step 16); the worker then drops the session (step 17). Every attached client is closed 4010 (step 19). During a recording hold the separate exception below applies. A session the core already ended keeps that end (step 5): the event completes the control side and writes nothing. A handshake under way for the session meets the end as any end mid-handshake does: what it delivers before nothing more can be recorded is recorded, so it is not in the gap, and it finalizes nothing a second time (reattach-handshake step 15). A `by: worker` missing any of the six, or with one outside its enum, is an end the core cannot place: `terminated` / `target_closed`, logged as a worker defect with the session's spool entry pair when there is one (→ session-end-from-outside step 5) [U2 `TestAMalformedByWorkerEndIsAnEndTheCoreCannotPlace`]. A `by: worker` for a session the core does not hold is logged and ignored, except a late open's, which is released (session-open step 14); its spool entry is found at its own take-back (core-restart step 8) [U2 `TestAByWorkerEndForASessionTheCoreDoesNotHoldIsLoggedAndLeftToTheSpool`]. | — | every attached client (4010); the worker (`release`); readers of the audit log |
| 23 | PW-TG | PW → TG | SSH | `SSH_MSG_CHANNEL_CLOSE` on the session's channel (RFC 4254 §5.3); not contract | worker | A timer's end only: the worker closes that one channel; its siblings and the connection live on (session model §3; Contract §15.2) [owed]. A telnet connection is the exception: it carries one session and closes with it, reporting `connection_lost` cause `single_session` once this end has reached the core [C3/telnet-one-session, reference, owed by the E3 worker plan]. | — | the target |
| 24 | PW-FS | PW → FS | a file, `workers/{worker_id}.json` | `worker-manifest.schema.json`, the ended session still listed | worker | The worker MUST retain the ended session, keeping its ring, data socket and manifest entry until `release` or until its connection closes: no core received its tail, or the core told of it on `channel_closed` `by: worker` has not yet recorded it (step 23b) (ipc-control `release`; worker-manifest top; immortality §4.3) [C3/reattach-ended; C3/ended-retention-bounded]. Retention MUST keep its memory within the connection's `buffer_bytes` per session (`release`) [owed]. | — | the returning core (core-restart) |
| 25 | — | PW → PW | — | → connection-linger | worker | A retained session is no session for `connection_linger`, which arms once no live session is left. When the connection closes, its retained sessions are released and the spool's numbers stand (ipc-control `release`) [C3/ended-retention-bounded]. A worker that then holds no other connection exits, and the spool outlives it on disk (spool-entry top; lang-arch §6.4 step 4) [the exit: owed]. | — | — |
| 26 | — | CO → CO | — | → core-restart | the core | The core starts again and takes back what survived. core-restart owns the restart and its order; this branch owns the spooled end and finalizes it through spooled-end-finalize (step 27); orphan-session owns the heartbeat (orphan-session step 9). | — | as core-restart says |
| 27 | — | CO → CO | — | → spooled-end-finalize | the core | The returning core finalizes the spooled `killed` end, end marker code 3: it reads the spool, replays a retained session, writes the record, the recording's markers and the audit event once, sends `release` and deletes the entry. spooled-end-finalize owns those steps and their MUSTs (unless step 23b has recorded it). | — | as spooled-end-finalize says |

## Refusals and who is told

- **403 against 404.** A principal that may not see the session gets 404, exactly as for an id that does not exist
  (`SessionNotFound`). Who may see which session is open (session-list, Open item 1).
- **Who learns of the end.** The caller (step 7), every attached client (step 19), any principal reading the record
  (session-list), and the audit log. Sibling sessions and their clients learn nothing: nothing of theirs changed.
- **An unreachable session** (a v0.1 worker). Step 8 still reaches it. The core neither reads nor records that
  worker's data sockets, and it sends that worker no `release`, so steps 12, 16 and 17 do not apply (ipc-control
  top). The end marker and the audit event still do: the core writes them "at every other end" (ttyrec top).
- **A spooled end** found by the returning core closes no client; one a running core finds closes every attached
  client 4010 (→ spooled-end-finalize step 9 and "Refusals and who is told"); so does an end a core is told of on `channel_closed` `by: worker` (step 23b).
- **Unspecified (not in plan §7):** what the core does when the worker answers `close_channel` with `error`, or
  never answers; what a client's input meets between step 5 and step 19 (ws-frames `input_refused` has no reason for
  an ended session); and the order of steps 13–19 beyond end marker → audit event → `release`, including whether
  4010 waits until the last output has reached the clients.

## Security considerations

- **Lifecycle authority.** Over REST only the owner token and `session.terminate` end a session (Contract §10; PTY
  §7.1; Storage §9) [C1/session-delete-not-owner-403]. Holding the control token, the right to type, gives no right
  to end. Over the attach stream only the owner's answer to a prompt sent to that client ends it; `prompt_id` is
  unguessable, so a WebSocket frame cannot end a session without the owner's authority (quiet-prompt).
- **The broker decides nothing** (Contract §12). TSB ends a session only on a principal's request, a principal's
  answer, or a configured timer. It never ends one because a task looks hung; it asks (session model §7;
  ws-frames `quiet_prompt`).
- **Cross-origin writes.** The login cookie is `SameSite=Strict` (`securitySchemes.session`). The explicit Origin
  check (`CrossOrigin`) is declared on login and logout only; whether `DELETE /sessions/{id}` refuses a foreign
  Origin is unspecified (not in plan §7).
- **The recording tells the truth to the end** (Contract §13; open_decisions §5). Input that reached the broker is
  recorded even when a kill lands between recording and sending it (`x-sidecar` reopen). The end marker says only
  `killed`; who ended the session is on the audit event alone (ttyrec top).
- **Independence.** Ending one session never ends a sibling or the connection: a forward opened from a shell
  outlives the shell (Contract §15.2; session model §3). A telnet connection is the exception: it carries one
  session and closes with it, reporting `connection_lost` cause `single_session` once this end has reached the core.
- **The host boundary** (Contract §4; conventions V11). The 204, the error bodies and the 4010 close name no pid,
  path or host. `recording_path` is audit data and never reaches a surface (session-record top). No credential
  crosses: `close_channel` and `release` carry only the session id (ipc-control).
- **While the core is gone.** The spool carries no credential and no terminal byte [C5/spool-entry-shape] (step 22).
  What the returning core writes from it: spooled-end-finalize, "Security considerations".

## Open items

None of these is in plan §7.

1. **Owner who is also an admin.** Whether that principal's end is `owner` or `admin` is not said.
2. **Ending an ended session.** What `DELETE` (204, 404 or other) or a late yes does for a session already ended.
3. **The 204's timing.** Whether it waits for the channel to close (steps 8–11).
4. **No reply from the target.** What the worker does when `SSH_MSG_CHANNEL_CLOSE` gets no reply (step 10).
5. **The instant of the end.** For an end the core asks for, whether `ended_at`, the end marker's stamp and the
   audit `ts` are the decision (step 5) or `channel_closed` (step 11).
6. **`session.terminate` on quiet-prompt's yes.** The audit top ties the event to `DELETE /sessions/{id}`; whether the
   owner's yes writes it is not said.
7. **The VT `close`.** When the core sends it, and whether it must (step 18); whether the session's
   `grid_snapshot_record` and any pending `stale_seq` are deleted at the end.
8. **Closed — queue A1.** No restart cuts the recording back to the commit point, so an ended session's end marker
   and tail stay; the reopen cuts only a torn last frame (ttyrec `x-sidecar` reopen; continuity-state top).
9. **The clocks behind a worker's `idle` and `unattended` end** (step 21): which last byte the worker counts from,
   and how the core takes the clocks back, is orphan-session Open item 1.
10. **OPEN — an end the core asked for, cut by the core's death.** The core persists `killed` and its reason first
    (step 6), and no spool entry exists for it: "the core knows every end it asked for and persists its reason when
    it acts, so owner, admin and worker_lost never appear here" (spool-entry top). A core that dies between step 6
    and steps 8, 13–16 leaves an end that spooled-end-finalize, which needs an entry, does not finish. Whether the
    returning core re-sends `close_channel` when the channel still lives, writes the end marker and
    `session.disconnect` once from its record, and sends `release` after the handshake reports `ended: true`, is
    not said.

## Picture

pending

**Recording hold exception.** While held, write no frame or end marker to either pair. Persist the session end and `session.disconnect` at the end, on the original generation-0 recording path, before a marker exists. If replay is gone, the linked successor holds only the sole end marker and the notice at P declares the unknown remainder. After every end marker sync, write `recording_end_placement` `present` and its end offset to bbolt (terminal-output-accounting §5.3; owed). A held session's spool entry is kept and no `release` of either kind is sent for it while its accounting is unresolved; the worker's retained resources go with its connection under §8, and a replay that is gone at the next start takes the ruled branch M5 (a).
