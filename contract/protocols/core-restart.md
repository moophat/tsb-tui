# core-restart — stop the core, and take back every session that survived

| | |
|---|---|
| **ID** | core-restart |
| **Purpose** | Stop the core without ending any session, and on start take back every session that survived: spool, rediscovery, the recording's end, handshake, grids, escrow, timers. |
| **Status** | built (E1); E2: steps 3, 21 and 25, the probe key of step 13, the prefix of step 27, and the realign of steps 17 and 21; 0.4: the first `ping` before the first `hello` (step 22) |
| **Corpus clauses** | immortality §1.1, §2.1, §3, §4.1–§4.3, §5.1, §6.1, §6.4, §7, §8; Storage §8.6; session model §9 E10, §10, §11, §13, §15; open_decisions §4, §5; Contract §8, §10, §13 |
| **Seams** | C1, C2, C3, C4 (E2), C5, C6, C8 |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `worker-manifest.schema.json`; `supervisor-manifest.schema.json`; `spool-entry.schema.json`; `continuity-state.schema.json#/$defs/session_record`, `#/$defs/connection_record`, `#/$defs/grid_snapshot_record`; `ipc-control.schema.json#/$defs/timers`, `#/$defs/timer_values`, `#/$defs/link_state`, `#/$defs/hello`, `#/$defs/error`; `session-record.schema.json` (`state`, `unreachable_reason`, `control_holder`, `clients`); `ws-frames.schema.json#/$defs/input_refused`, `#/x-close-codes` (4011); `ttyrec.schema.json#/x-sidecar` (`reopen`), `#/x-compare`; `audit-event.schema.json#/$defs/config_event`; `vt-ipc.schema.json#/$defs/hello`, `#/$defs/hello_ack` (E2, through grid-restore) |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files are under
`srs/`. Conventions are cited by their number in the protocols plan (§4, V1–V16).

core-restart owns the trigger and every decision of a restart. Each protocol it runs owns its own exchange: reattach-handshake the
handshake, grid-restore the grids, typing-ownership the token, session-end-by-tsb and session-end-from-outside the ends, spooled ones included (plan §2.1).

**Words used here.**

- **rediscovery**: finding, on start, the supervisors and workers a previous core left running, through the
  manifests they write (immortality §2.1; Contract §8). Liveness is a dial, never a pid (worker-manifest top).
- **E10** (`claim_refused`): a manifest's socket refuses, its peer fails SO_PEERCRED, or the worker holds no such
  session (session model §9). Locally a worker the core cannot reach is a dead worker (immortality §3).
- **commit point**: `recording_last_seq_out` with `recording_committed_offset`, and `out_bytes_committed` and
  `recording_last_seq_in` for the same point. From contract-v0.3 they advance only in the bbolt write that stores a
  grid snapshot (continuity-state top).
- **the recording's end**: the last output frame or gap marker the recording keeps, the output bytes through it and
  the last input frame, found by walking the recording forward from the commit point (`x-sidecar` reopen). The
  recording is never cut back to the commit point: before appending, the core cuts only a torn last frame
  (`x-sidecar` reopen; open_decisions §5).
- **realign**: moving the commit point to the recording's end before `hello`, so that `last_committed_seq` is
  `snapshot_seq` (grid-snapshot rule 10; decision 5.39).
- **taken back**: a stored session whose worker answers and speaks the handshake. It runs on, `detached`.
- **escrow**: the control token held by the core because no attached client holds it (PTY §7.1).

## C3 0.8 terminal branch

For PTY sessions within [terminal output accounting §1](terminal-output-accounting.md#1-scope-and-acceptance),
with a known C3 0.8 worker, audited-end precedence runs before step 18's aborting/spool/E10/live branches:
use [terminal output accounting](terminal-output-accounting.md) §9 A/B, the handshake for such a session
begins with hello, sent after the connection's first ping (core-restart step 22), no second end or
active-session publication. Ended records with a complete marker but missing audit keep that marker;
write only the original missing audit. Absent retained resources use its §8 unknown-suffix branch,
including an explicit unrecorded event for an entirely unrecorded session. Before any startup audit
producer, apply its §7 LF-tail repair and single-file history scan. Older peers retain the steps below
[C3/reattach-ended-audited-sealed; C6/terminal-close-core-restart; C6/terminal-audit-repair-order].

## Path

stop: SF-CO → CO-PW → CO-VT → (orphan-session) · start: CO-FS → CO-DK → CO-BB → CO-FS → CO-SV → CO-FS → CO-PW → CO-DK →
CO-BB → (session-end-from-outside | session-end-by-tsb) → CO-BB → (typing-ownership) → (grid-restore) → (reattach-handshake) → (hung-worker) → (stale-screen) → (operator-alerts) → SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | CO → SF | WebSocket | → session-detach: close 4011 `detached_by_server` (`ws-frames.schema.json#/x-close-codes`) | the core | When the core stops, every attached client is closed 4011, "the core stopping"; session-detach step 9 and rule 7 own that requirement. The session lives on (`x-close-codes`; immortality §3: all disconnected). A core that dies sends no close: its clients see the socket end with no code (→ session-detach). | — | every attached client |
| 2 | CO-PW | CO → PW | unix sockets `conn-{conn_id}/control.sock`, `conn-{conn_id}/{socket}` | the core's ends close; no message | the core | The stop MUST end no session: the core sends no `close_channel` and no `disconnect`, and MUST NOT signal any worker (immortality §1.1, §2.1 LOCKED, §3; open_decisions §4: "core restart must not signal them") [owed]. A control socket closed without `disconnect` is the graceful core restart, and the worker MUST end nothing on it (ipc-control top) [C3/control-socket-stays-open]. With no core peer a PTY session's output goes to its ring (immortality §4.1); a non-PTY channel is not read (ipc-control top). | — | — |
| 3 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | the core's end closes; no message | the core | **E2.** Before its end closes, a stopping core MUST snapshot every unquarantined grid whose recording holds output past its commit point, each wait bounded by `snapshot.wait_sec`, so an ordinary restart needs no realign (grid-snapshot rule 13) [owed]. A held or quarantined pair takes no commit write and stays at P until active switch (F6; terminal-output-accounting §5.3) [owed]. The VT worker MUST keep every grid when its core's socket closes, dropping one only on `close` or exit (vt-ipc top, D19, open) [C4/vt-hello-grids; its graders owed]. | — | — |
| 4 | — | PW → PW | — | → orphan-session | worker | While no core pings, the worker keeps every session. After `core_heartbeat` its sessions are orphan-pending, and it runs `idle`, `unattended` and `orphan_timeout` itself, spooling each end, and reporting it on `channel_closed` `by: worker` as well to a core that reads that connection's control socket when the end completes (→ session-end-by-tsb step 23a) (session model §11; immortality §8). | — | as orphan-session says |
| 5 | CO-FS | FS → CO | file | `config.yaml` (no contract shape) | the core | The knobs step 13 sends and the recording switch step 6 records (session model §11; conventions V7). | — | — |
| 6 | CO-DK | CO → DK | file, the audit log (append) | `audit-event.schema.json#/$defs/config_event` (`config.recording_state`) | the core | The core MUST write it once when it starts, so a disabled recorder is on the record (audit-event top; open_decisions §5: a single-user deployment may disable recording) [C6/event-recording-state]. | — | readers of the audit log |
| 7 | CO-BB | BB → CO | a bbolt read | `continuity-state.schema.json#/$defs/session_record`, `#/$defs/connection_record`, `#/$defs/grid_snapshot_record` | the core | What reattach cannot be done without (Storage §8.6). The owner comes back from `session.owner`, by storage (immortality §7). The core MUST NOT restore a control holder from disk: `control_holder` reads null after any restart (continuity-state `session`) [C2/token-escrow-after-restart]; nor the attached clients (session-record `clients`: "never restored from disk") [owed]. It MUST split input at the connection record's `input_frame_max`, never at a value its configuration now holds (`connection_record`) [C3/input-refused-too-large grades the split; after a restart owed]. An E0 record MUST load unchanged [C6/continuity-record-valid]; for a session born at E0 the core MUST compute `out_bytes_committed` from its recording at the first E1 start (continuity-state `out_bytes_committed`) [owed]. Input is the core's own counter, never replayed: for an ordinary restart the core MUST continue it from the recording's end, `recording_last_seq_in` plus the input frames the recording keeps past the commit point, and for a session that is not recorded from the `recording_last_seq_in` reservation (step 17; continuity-state `recording_last_seq_in`) [C3/seq-continuity-across-restart: "run 2 simply goes on at 4"; the reservation: owed]. A continuation switch instead uses the durable reservation high-water, never the quarantined suffix (terminal-output-accounting §5.3) [owed]. | — | — |
| 8 | CO-FS | FS → CO | file `spool/{session_id}.json` | `spool-entry.schema.json`, every entry | the core | The core MUST read the spool before any E10 decision, because a worker that ended a session while the core was away may since have exited, and MUST read it again for a session on every `no_such_session` or refused session socket, since an end can land between the read and the dial. An entry for a session it has already audited it MUST delete unread: a session ends once (worker-manifest top; spool-entry top) [C5/manifest-rediscovery-v0.2; C5/spool-entry-shape; C3/hello-no-such-session; C3/redial-replay]. What an entry means is spooled-end-finalize step 1's, `killed` or `terminated` as the entry says. | — | — |
| 9 | CO-FS | FS → CO | file `supervisors/{supervisor_id}/manifest.json` | `supervisor-manifest.schema.json` | the core | Each names one member of the set of supervisors (supervisor-manifest top; immortality §2.1). | — | — |
| 10 | CO-SV | CO → SV | unix socket | dial of each manifest's `control_sock` (no message) | the core | The core MUST address every supervisor whose socket accepts and skip one that is missing or refuses; liveness is the dial, never `pid` [C5/manifest-rediscovery-case]. It MUST NOT create a worker through a supervisor with no `proto_version` (→ worker-placement) [C5/manifest-rediscovery-v0.2; C3/n1-v0.1-worker-unreachable]. The supervisor heartbeat resumes (→ operator-alerts). | a dead supervisor is skipped | — |
| 11 | CO-FS | FS → CO | file `workers/{worker_id}.json` | `worker-manifest.schema.json` | the core | Each connection's `conn_dir` and each session's `socket`; a retained ended session stays listed until released (worker-manifest top). | — | — |
| 12 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock` | dial (no message) | the core | Liveness is the dial, trusted by SO_PEERCRED alone, never `pid` (worker-manifest top; immortality §6.1, LOCKED) [C5/manifest-rediscovery-case; the SO_PEERCRED check owed]. The core reattaches the connection and does not reconnect it: no `connect`, so no credential crosses (C3/reattach-sequence). A control socket that is missing or refuses makes every session of its connection dead, even one whose own socket answers [C5/manifest-rediscovery-case]. | missing, refused or failing SO_PEERCRED: E10 for its sessions, decided at step 18 | — |
| 13 | CO-PW | CO → PW | unix socket `control.sock` | `ipc-control.schema.json#/$defs/timers` | the core | To a worker at `proto_version` 0.2 or later the core MUST send the full set at reattach; the worker keeps the last set it received (`timers`) [C3/reattach-sequence]. **E2:** it MUST carry `keepalive_probe_interval` only toward a peer at 0.3 or later (`timer_values`; D29, open: Open item 11) [C3/n1-v0.2-worker-positive; its graders owed]. A v0.1 worker gets none (ipc-control top) [C3/n1-v0.1-worker-unreachable]. | — | the worker |
| 14 | CO-PW | PW → CO | unix socket `control.sock` | → link-liveness: `link_state` | worker | The first event to the restarted core's new control peer, so it has `link_timeout`'s countdown at once (→ link-liveness step 10, which owns the rule and its fixtures; → session-countdown). | — | the core |
| 15 | CO-PW | CO → PW | unix socket `conn-{conn_id}/{socket}` | dial (no message), for each listed session | the core | Toward a worker at 0.2 or later this dial is reattach-handshake's step 1. **E2:** it follows the session's realign (steps 17, 21), which waits for a VT snapshot at most `snapshot.wait_sec` and then gives the grid up (grid-snapshot rules 10, 12). Meanwhile the session reads `detached`, its input is refused `replaying`, the core pings its worker (step 22) and runs its timers (step 24), and its dial and the step 18 decision that rests on it (with a fresh spool read) come when the wait ends [owed]. Toward a v0.1 worker the core MUST dial each data socket once and close it unread: it neither reads nor records that output (ipc-control top) [C3/n1-v0.1-worker-unreachable]. So output after the upgrade is not in that session's recording and nothing there marks it, since a v0.1 worker reports no byte count to declare, and its `idle` timer sees none of that output (ipc-control top; C1/session-unreachable). | missing or refused: E10 for that session, decided at step 18 | — |
| 16 | CO-DK | CO → DK | active pair `.ttyrec` + `.dir` (read; ordinary torn tail cut) | cut by `ttyrec.schema.json#/x-sidecar` `reopen` | the core | Before the core appends to an unquarantined recording again, it MUST cut both files to the last frame both hold, never padding a missing code (`x-sidecar` reopen) [C6/out-bytes-committed-after-reopen]. It MUST cut nothing else, and never back to the commit point: input frames, broker markers, gap markers, an end marker and output the recording holds stay (`x-sidecar` reopen; continuity-state top; open_decisions §5; Contract §13) [owed]. A quarantined pair is never cut or reopened for writing (terminal-output-accounting §5.3) [owed]. | — | — |
| 17 | CO-BB | CO → BB | a bbolt write | `#/$defs/session_record` | the core | For an unquarantined pair, the core MUST find the recording's end by walking the recording forward from the commit point; a quarantined pair stays at P and uses a linked successor (terminal-output-accounting §5.3) [owed]: the seq of the last output frame or gap marker kept, the output bytes through it (`out_bytes_committed` plus what the recording keeps past the commit point) and the last input seq; a gap marker past the commit point adds its byte count and no seq (`x-sidecar` reopen; `out_bytes_committed`) [C6/out-bytes-committed-after-reopen]. When the recording holds output past the commit point, the core realigns the commit point to that end before step 23 (never above P for a pair quarantined by a recording sync failure: terminal-output-accounting §5.3), the four numbers in one write: at E1 in a write of the session record alone; **E2:** with step 21's snapshot (grid-snapshot step 7, rule 10), or record-only for a session with no `grid_snapshot_record` (grid-snapshot rules 11, 12); with no output past it, no write [owed]. | — | — |
| 18 | — | CO → CO | — | — | the core | In the order worker-manifest top fixes, which the core MUST follow [C5/manifest-rediscovery-v0.2; C5/manifest-rediscovery-case; C3/n1-v0.1-worker-unreachable]: (a) the spool holds its end → that end (→ spooled-end-finalize, for session-end-by-tsb or session-end-from-outside by its end state), written after step 23 replays the session when a live worker's manifest still lists it, otherwise now (a dead worker whose manifest still lists it: Open item 8); an end the worker reports on `channel_closed` `by: worker` is recorded from the event instead, with no replay of its own: what step 23's handshake delivered before it is recorded, and what the recording still lacks is declared lost (session-end-by-tsb step 23b); (b) its socket is dead and the spool holds no end → E10, `terminated` / `worker_lost`, whatever the manifest's version (→ session-end-from-outside); (c) a live peer whose manifest has no `proto_version` → `unreachable` / `protocol`, never killed (immortality §6.4); (d) a live peer of any version → taken back (steps 20–28). | records and manifests that disagree: Open item 4; a `killed` end the core persisted but did not finish: Open item 10 | as session-end-from-outside or session-end-by-tsb says |
| 19 | CO-BB | CO → BB | a bbolt write | `#/$defs/session_record`, nested `session.state` `unreachable` with `unreachable_reason` `protocol` | the core | Case (c), and a worker that answers step 23's `hello` with `error` `unsupported_proto` or `unknown_cmd` [C3/hello-unsupported-proto]. One copy, in the nested record (continuity-state top) [C1/session-unreachable]. The core then sends that v0.1 worker no `hello`, `ping`, `timers`, `release`, kill request or linger, never kills it by `worker_heartbeat`, and sends its ends as `close_channel` (→ session-end-by-tsb) (ipc-control top) [C3/n1-v0.1-worker-unreachable]. | — | any principal reading the record (→ session-list); whether `unreachable` reaches beyond a v0.1 worker: Open item 9 |
| 20 | — | CO → CO | — | → typing-ownership | the core | Every session not ended has its control token in escrow until a client that may type attaches: "no new mechanism" (immortality §7; continuity-state `session`) [C2/token-escrow-after-restart]. | — | as typing-ownership says |
| 21 | — | CO → CO | — | → grid-restore | the core | **E2.** Per PTY shell session: what the VT worker still holds (`hello_ack` `grids`); the restore from its `grid_snapshot_record`; then the recording's own output frames past the commit point; then, when the recording holds output past the commit point, the realign snapshot at the walk's last output seq, stored before step 23; a kept grid that stands past that seq (it applied the gap frame of a marker whose commit never landed) is replaced by the restore, not snapshotted (grid-snapshot rule 10). Restore is the snapshot, the recording's frames past it, then the worker's replay of seq > `snapshot_seq`, which is only what the recording lacks (continuity-state top; vt-ipc top; immortality §5.1; D18, D21, open: Open item 11) [C4/vt-hello-grids; C2/prefix-after-restart; their graders owed]. A snapshot not given within `snapshot.wait_sec`, or one the VT worker cannot give, gives the grid up: record-only realign and `grid_lost` (grid-snapshot rule 12; step 15). | a grid that cannot be rebuilt → stale-screen, cause `grid_lost` (grid-restore) | as grid-restore says |
| 22 | — | CO → CO | — | → hung-worker | the core | The core's `ping` resumes on every control socket of a worker at 0.2 or later that it dialled at step 12, whether its sessions are taken back or still wait on a realign (step 15) [C3/heartbeat-ping-pong; the waiting case owed]. Toward a worker whose version it speaks, the first `ping` on each such socket MUST go before the first `hello` there (step 23), after step 13's `timers`, so the worker's `core_heartbeat` resets and orphan-pending ends before any handshake opens [U2 `core/internal/session/reattach_test.go` `TestARestartedCorePingsEachControlSocketBeforeItsFirstHello`; C3/channel-closed-by-worker]; after it the worker MUST start no end of its own timers (orphan-session step 9) [U3 `worker/tests/test_spool.py` `test_a_ping_during_the_orphan_sweep_stops_the_rest_of_that_pass`]. A worker of a version the core does not know is offered `hello` with no `ping` before it; for one that answered `hello` with `unsupported_proto` or `unknown_cmd` (step 19): Open item 7. It resets the worker's `core_heartbeat` and ends orphan-pending (session model §11: `orphan_timeout` is reset when the core's heartbeat returns). | — | as hung-worker says |
| 23 | — | CO → CO | — | → reattach-handshake, from its step 2, for each session of case (d) and each session of case (a) a live worker still lists | the core | After step 22's first `ping` on the session's control socket: `hello` carries `snapshot_seq`, the commit point realigned to the recording's end (steps 17, 21), as `last_committed_seq`, and the core's own `proto_version` when it does not know the worker's (worker-manifest top) [C3/reattach-sequence; C3/seq-continuity-across-restart]. A retained ended session's replay is followed by its spooled end, then `release` (→ spooled-end-finalize) [C3/reattach-ended]. Across the restart the recording MUST be payload-identical per direction to an uninterrupted run, and never so when it declares a loss; per direction its stamps MUST NOT decrease (`x-compare`) [C8/payload-identical-across-restart; C8/payload-identical-gap-declared; C8/timestamps-monotonic; C3/output-stamps-monotonic]. | `unsupported_proto`, `unknown_cmd` → step 19; `no_such_session` → spool re-read, then session-end-from-outside (reattach-handshake) | as reattach-handshake says |
| 24 | — | CO → CO | — | re-arms `idle` and `unattended` | the core | From the stored `activity_at` and `client_touch_at`, for every session it holds whose end its spool entry does not already hold, a waiting one included (step 15): the core MUST NOT restart either clock (session model §15: a deploy does not restart every timer; Storage §8.6) [owed; C6/continuity-record-valid grades only the clocks' shape]. A session whose spool entry holds its end (step 18 case (a)), or whose end the worker reported on `channel_closed` `by: worker` (session-end-by-tsb step 23b), has no clock left to run: no timer is armed over it, and its end is the worker's (spooled-end-finalize step 3); for a session that is not held back: U2 `core/internal/session/reattach_test.go` `TestASessionWhoseSpoolEntryWasReadAtTakeBackArmsNoTimerAtLive`. `ends_at` follows (→ session-countdown). | — | any principal reading the record (→ session-list) |
| 25 | — | CO → CO | — | → stale-screen | the core | **E2.** A record carrying `stale_seq` and `stale_cause` keeps its pending choice across the restart (continuity-state `stale_seq`) [C6/stale-pending-durable; its graders owed]. | — | as stale-screen says |
| 26 | — | CO → CO | — | → operator-alerts | the core | The core rebuilds the active alert set from the raises in the audit log that have no clear, re-checks each against live state and clears what no longer holds (OpenAPI `GET /alerts`). | — | as operator-alerts says |
| 27 | SF-CO | SF → CO | WebSocket | → session-attach: `attach` | a client of a principal that may join | The session reads `detached` until then (session-record `state`). The first client of a `session.join.rw` principal takes the token from escrow (step 20) [C2/token-escrow-after-restart]. **E2:** its prefix's dump yields the screen the restored grid holds [C2/prefix-after-restart; its graders owed]. | as session-attach says | as session-attach says |
| 28 | SF-CO | SF → CO | WebSocket | → byte-relay: binary: input bytes | an attached client (who may type is typing-ownership's) | Not forwarded before reattach-handshake's `live` [C2/stream-input-refused-replaying]; never forwarded to a case (c) session (`input_refused`; immortality §3) [C2/stream-input-refused-unreachable]. | `input_refused` `replaying` (reattach-handshake step 3) or `unreachable` | the sending client |

**Order.** The contract fixes: the spool (step 8) before any E10 decision (step 18); the dials (steps 12, 15) before
the version branch (worker-manifest top); toward a worker whose version the core speaks, the first `ping` on a control socket before the first `hello` there (step 22); `timers` before `hello` (C3/reattach-sequence); the reopen, the walk and the
realign (steps 16–17, 21) before the session's dial and `hello`, which replays from the recording's end (`x-sidecar`
reopen; grid-snapshot rule 10); a grid's restore, and its re-feed from the recording, before its replay is fed (vt-ipc
top; step 21). No other order among steps 5–11 is stated.

## Refusals and who is told

- **Nothing in core-restart refuses a principal.** Its branches are the dead peer (E10), the version (`unreachable`) and
  input refused until `live` or for good (step 28).
- **Who learns of a restart.** Attached clients get 4011 (step 1). After it, a principal reads each session's state
  and countdown on its record (→ session-list); an end decided at step 18 is on the record and in the audit log, and any
  client attached by then is closed as session-end-from-outside or session-end-by-tsb says.
- **A consumer** MUST treat a session `state` it does not know as not attachable, so a v0.1 consumer never attaches
  to `unreachable` (session-record `state`; contract README "Senders and receivers") [owed].

## Security considerations

- **Who may type.** No holder survives a restart: every token goes to escrow and only an attach takes it
  (immortality §7) [C2/token-escrow-after-restart]. No byte reaches the target from `hello` until `live`
  (immortality §6.3, as `hello` applies it to the local path), and none ever reaches an `unreachable` session
  (immortality §3; decision 5.38 as ipc-control top cites it). Lifecycle authority survives by storage: the owner is
  in bbolt from creation (immortality §7; Contract §10).
- **The host boundary.** Every local socket is trusted by SO_PEERCRED alone; no claim token is stored or sent
  (immortality §6.1, LOCKED; continuity-state top) [C6/no-claim-token-field]. A manifest's `pid` is informational.
  Manifests and the spool carry no credential, and the connection record keeps `credential_ref`, a reference
  (worker-manifest top; spool-entry top; `connection_record`). A restart sends no `connect` (step 12).
  Northbound, nothing names a pid, path or host, and `recording_path` never leaves the core (conventions V11;
  continuity-state `recording_path`).
- **Workers are never signalled** (step 2), so a core upgrade is never a fleet-wide session massacre
  (immortality §6.4).

## Open items

1. **Closed — queue A1.** No restart cuts the recording back to the commit point, so input frames and broker
   markers stay, and the input counter goes on from the recording's end (steps 7, 16–17; `x-sidecar` reopen;
   open_decisions §5; Contract §13).
2. **Closed — queue A1.** An ended session's end marker and tail, and the recording of a session core-restart cannot
   take back, are never cut (step 16). A spooled end that finds its end marker already written writes neither marker
   again (spooled-end-finalize steps 4–5).
3. **Closed — queue A1.** `hello` carries `snapshot_seq` after a restart and after a lone re-dial alike, the commit
   point first realigned to the recording's end (steps 17, 21; grid-snapshot rule 10; ipc-control `hello`).
4. **Records and manifests that disagree** (unspecified, not in plan §7): a stored session that no manifest lists
   and the spool does not end; a listed session with no stored record (session-open's write order is open). The fixtures
   decide only listed sessions and spooled ends.
5. **Serving before rediscovery ends** (unspecified, not in plan §7): whether the core answers REST and attaches
   before step 18 completes. system-architecture says rediscovery runs first, but it is descriptive only.
6. **The stop** (unspecified, not in plan §7): whether the core writes `session_control` `detach` for each holder it
   closes at step 1 (session-detach open item 7); and whether step 1 precedes step 2.
7. **OPEN — a newer worker the core cannot speak to, and the heartbeat** (decision queue A6, N90; not in plan §7). A
   worker that answers `hello` with `unsupported_proto` or `unknown_cmd` has sessions `unreachable` / `protocol`
   (step 19). ipc-control top states the no-`ping` rule for a v0.1 worker only and is silent here. If the core pings
   it, its `core_heartbeat` resets, `orphan_timeout` never fires, and hung-worker's kill can reach it; if not, its own
   orphan timer ends those sessions after 48 h (queue A6's "Folded in" note, inside an open decision). orphan-session
   says it "is not said". Not ruled.
8. **OPEN — a retained ended session listed by a dead worker** (not in plan §7). A manifest is "left in place if the
   worker crashes", and the replay is "that handshake", run "for a live peer only" (worker-manifest top). spool-entry
   top: "If the worker's manifest still lists the ended session … the handshake replays it first; if not … the gap
   marker". No text says that a dead worker's stale listing takes the "if not" branch, a gap from the spool's numbers.
   Not in the queue or the review log.
9. **OPEN, the contract against itself — the scope of `unreachable`.** common `session_state`: "at this tag only a
   v0.1 worker under a v0.2 core"; C1/session-unreachable: "locally, only a v0.1 worker". ipc-control top and
   contract README "Senders and receivers": `unsupported_proto` or `unknown_cmd` from any peer whose version the core
   does not know makes its sessions `unreachable` / `protocol`. Step 19 follows the second. Not in the queue or the
   review log; conventions open item 17 carries it.
10. **Closed for C3 0.8; legacy gap remains — a `killed` end cut by a core crash** (not in plan §7; session-end-by-tsb's steps). The core persists a
    `killed` end it asked for in bbolt first (session-end-by-tsb step 6) and the worker spools none: "owner, admin and
    worker_lost never appear here" (spool-entry top). A core that dies before that end's `close_channel`, end marker,
    audit event and `release` leaves no spool entry, yet step 18 (a) and reattach-handshake step 15 hand an ended
    session to spooled-end-finalize, which needs one. Whether the returning core re-sends `close_channel` while the
    channel lives, writes the end marker and `session.disconnect` once from its record, and sends `release`, is not
    said for legacy peers. For C3 0.8 PTY sessions within terminal-output-accounting §1, §§3/9 now specifies hello-first discovery,
    close only if open, replay/seal/accounting, and the persisted first end/marker/audit repair
    [C3/reattach-ended-audited-open-unread; C6/terminal-unrecorded-and-partial].
11. **Approval items leaned on, not agreed:** D19 (step 3), D29 (step 13's probe key; also D28's "0.3"), D18 and D21
    (step 21: the feed and `snapshot_incompatible`), open in the approval page's southbound set (queues A6, A8).

## Picture

pending

**Quarantined recording continuation (tsb-program #141).** Steps 17 and 21 do not walk a quarantined pair above P. They use the audit-linked continuation, inherited session-wide counters and trusted P snapshot, then replay as terminal-output-accounting §5.3 specifies. The predecessor remains frozen while the successor's commit point may advance. [owed]
