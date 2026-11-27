# reattach-handshake — bring a session's data path to live

| | |
|---|---|
| **ID** | reattach-handshake |
| **Purpose** | Bring one session's data path to live so that no output is lost, repeated or silently spliced, declaring any loss as a gap. |
| **Status** | built (E1); steps 11 and 12 are E2 |
| **Called by** | session-open (a new session's first data peer), core-restart (core restart), data-socket-redial (a lone data-socket loss); spooled-end-finalize steps 2 and 7, inside core-restart (a retained ended session's replay, then `release`). Each caller owns its trigger; the `last_committed_seq` every caller brings is the same, the commit point realigned before step 1 (grid-snapshot rule 10); reattach-handshake owns the exchange (plan §2.1). A VT worker's death runs no handshake: the data path never broke (grid-restore). |
| **Corpus clauses** | immortality §4.1, §4.2, §4.3, §6.1, §6.3 (input authority only after the replay), §6.4; Contract §15.1; session model §9 E10; lang-arch §4.1 (amendment 2026-09-14) |
| **Seams** | C3, C8, C4 (E2), C2, C5, C6 |
| **Schemas** | `ipc-control.schema.json#/$defs/hello`, `#/$defs/hello_ack`, `#/$defs/replay`, `#/$defs/replay_done`, `#/$defs/live`, `#/$defs/release`, `#/$defs/error`, `#/$defs/input_refused`, `#/$defs/buffer_bytes`, `#/$defs/socket`; `ipc-data-frame.schema.json` (top description: the gap frame; `x-binary-layout`); `ws-frames.schema.json#/$defs/input_refused`; `ttyrec.schema.json#/$defs/gap_marker`, `#/$defs/data_frame`, `#/x-gap-line`, `#/x-sidecar`; `vt-ipc.schema.json#/$defs/output_frame`; `continuity-state.schema.json#/$defs/session_record`; `worker-manifest.schema.json` |

Schema paths are relative to `contract/schemas/` at `contract-v0.3`. Corpus files, under `srs/`:
immortality = `immortality-mechanism.md`, Contract = `architecture-contract.md`, session model =
`session-model-and-lifecycle.md`, lang-arch = `language-architecture-decision.md`. Socket names below are relative
to TSB's node-local runtime directory (ipc-control top, vt-ipc top).

**Words used here.**

- **handshake**: `hello` → `hello_ack` → `replay` → `replay_done` → `live` (or `release`), one per session, on its
  connection's control socket `conn-{conn_id}/control.sock` (ipc-control top).
- **data peer**: the core's end of the session's data socket `conn-{conn_id}/{socket}`. Every chunk on it is a
  24-byte envelope and a payload (ipc-data-frame top; lang-arch §4.1 amendment).
- **seq**: the per-session output sequence number the worker assigns, from 1 (immortality §4.1).
- **ring**: the worker's bounded per-session output buffer, `buffer_bytes` long, dropping oldest on overflow while seq
  keeps counting (immortality §4.1, §4.3). PTY sessions only (Contract §15.1), which at this tag means `shell`.
- **`last_committed_seq`**: the seq of the last output frame or gap marker the core's recording keeps at its commit
  point (for a session that is not recorded, the last output frame the core received there); a gap marker counts as
  its gap frame's seq (ipc-control `hello`). It is continuity-state
  `session_record.recording_last_seq_out`, which is `snapshot_seq` (decision 5.39). Before step 1 the core realigns:
  it moves the commit point to the recording's end (never above P for a pair quarantined by a recording sync failure:
  terminal-output-accounting §5.3), with a snapshot or, for a session with no snapshot, record-only
  (grid-snapshot rules 10, 11), so the worker replays only what the recording lacks.
- **gap frame**: an output frame with flags bit 1 and no payload, declaring that output before it was lost
  (ipc-data-frame top). **gap marker**: its recording frame, sidecar code 2, whose data is the `x-gap-line` line.
- **keyboard holder**: the one attached client that holds the control token and may type (Contract §10).

reattach-handshake runs only against a worker that speaks the handshake (`proto_version` 0.2 or later). A v0.1 worker gets no
`hello`; its sessions are unreachable / protocol (ipc-control top), which is core-restart's.

## C3 0.8 terminal branch

For C3 0.8 PTY terminal recovery within [its §1 scope](terminal-output-accounting.md#1-scope-and-acceptance), [terminal output accounting](terminal-output-accounting.md) §§3/9
extends this handshake: ended:true never sends close_channel; seal_output explicitly abandons the
paused peer, seals producers, then a fresh data peer/hello replays the immutable extent. It is the new
read-pause exception; ordinary hello never resumes reads. terminal_busy refuses hello during sealing
or a core-requested end in flight; a hello offering a version below 0.8 then gets unsupported_proto, and
after sealing the legacy handshake and release, at that legacy core's next start (its §3.1, tsb-program #138). Never live/input/resize
for this ended path; use release_accounted only after both replay barriers and durable accounting.
Legacy release keeps its meaning [C3/terminal-hello-busy; C3/terminal-seal-idempotent;
C3/terminal-input-refused-mid-seal; C3/terminal-legacy-offer-mid-seal].

## Path

CO-PW → SF-CO → CO-PW → CO-DK → CO-VT → (stale-screen) → CO-PW → (byte-relay); ended: (spooled-end-finalize) → CO-PW → PW-FS

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/{socket}` | dial of the session's data socket (no message) | the core | **E2:** only after the realign (grid-snapshot rule 10), whose wait for a VT snapshot `snapshot.wait_sec` bounds (rule 12) [owed]. The core MUST dial before step 2 (`hello`; owed). The worker accepts the peer by SO_PEERCRED alone (conventions V10) and takes the newer core peer (ipc-data-frame top, data path paragraph). | Refused, missing, or failing SO_PEERCRED: E10 (session model §9 E10), decided only after the core reads the spool (ipc-control top, "Spool"). The caller owns it (data-socket-redial, core-restart) → session-end-from-outside | — |
| 2 | CO-PW | CO → PW | unix socket `control.sock` | `hello` | the core | Opens the handshake for one session. Its `proto_version` is the worker's when the core speaks it (`hello` "0.2" toward a v0.2 worker, sending exactly v0.2's messages), and the core's own when it does not know the worker's (worker-manifest top; CHANGELOG contract-v0.3 C3; C3/n1-v0.2-worker-positive; conventions V1). `last_committed_seq` MUST be as defined above, 0 when the recording keeps none (C3/seq-continuity-across-restart, C8/gap-twice-across-restart). The input gate of step 3 shuts here, or at the loss when a lone data-socket loss started it. | — | — |
| 3 | SF-CO | SF → CO | WebSocket | binary: input bytes, at any time from step 2 until step 13 | keyboard holder (a non-holder's input is typing-ownership's) | The core MUST NOT forward it to the worker and MUST NOT hold it for later (`hello`; C3/input-refused-replaying, through the core test it names; tsbc `control-reattach`). | `input_refused` `replaying` (C2/stream-input-refused-replaying) | the sending client |
| 4 | CO-PW | PW → CO | unix socket `control.sock` | `hello_ack` | worker | The worker MUST answer only on a data peer accepted after the previous one ended, waiting for one at most `heartbeat_interval` (C3/hello-no-data-peer). It reports `first_buffered_seq`, `first_buffered_offset`, `current_seq`, `last_dropped_ts` and `ended` as `hello_ack` defines them. From `hello` until `live` or `release` it MUST NOT read the session's channel, so `current_seq` cannot move (C3/reattach-sequence). That holds while the core that sent the `hello` is present (ruling 2026-10-14, operator). From 0.4 the worker MUST take that core as gone, and then read an ended session's remaining output into the ring, where the next handshake finds it, only when its control peer drops or is replaced, it sends `close_channel` for the session, the worker enters orphan-pending after that `hello`, or the connection ends; a worker already orphan-pending when the `hello` came does not take it as gone for that alone [U3 `worker/tests/test_end_during_handshake.py` `test_a_hello_received_while_orphan_pending_keeps_reads_paused_until_that_cores_live`]. An end that lands meanwhile MUST wait for `live`, its tail then going out live before `channel_closed` (session-end-by-tsb step 23a), or for `release`, which settles it ended with nothing more read into the ring and no `channel_closed` [U3 `test_a_release_during_an_ends_wait_settles_it_ended_reads_nothing_more_and_arms_linger`]. A new `hello` closes the read again, and the end waits for that handshake. A core that stops pinging is gone once the worker enters orphan-pending (orphan-session step 3), so `core_heartbeat` bounds the wait on a silent core; after a restart a core that speaks the worker's version pings it before its first `hello` (core-restart step 22), so that `hello` never reaches it orphan-pending [C3/channel-closed-by-worker]. A non-PTY session MUST report `first_buffered_seq` = `current_seq` + 1 and never carry a gap (C3/reattach-non-pty, marked published e1 only: required green with the tag that opens a non-PTY session, since SFTP and NETCONF answer `not_supported` until a later release defines the file surface). | `error` `unsupported_proto` or `unknown_cmd`: the core MUST make the session unreachable / protocol and MUST NOT kill it (immortality §6.4; C3/hello-unsupported-proto; whether `unreachable` reaches beyond a v0.1 worker: Open items). `no_such_session`: the core MUST re-read the spool, write a spooled end if one is there (→ spooled-end-finalize), and only otherwise end the session E10 terminated / worker_lost (→ session-end-from-outside) (C3/hello-no-such-session). `no_data_peer`: the core's next step is unspecified (no plan §7 entry) | unreachable: input refused `unreachable` (byte-relay) and the record's `unreachable_reason` (session-list); E10: clients closed 4012 (session-end-from-outside) |
| 5 | — | CO → CO | — | computes the replay and the loss | the core | `from_seq` = `last_committed_seq` + 1. A loss is due when the session is PTY and `first_buffered_seq` > `from_seq` (immortality §4.3; `replay`). Its byte count is `first_buffered_offset` minus the output bytes the recording holds (`out_bytes_committed`, plus a gap marker the recording holds past the commit point: `hello_ack`). **E2:** a count of zero is allowed only when the walk met a gap marker past the commit point: that marker already declares this loss, and no new one is written (a reading of immortality §4.3: the CHANGELOG's corpus readings). Any other count below one means the walk or the recording is wrong: the core MUST write no marker and treat it as a failed recording write (byte-relay open item 3) [owed]; its span runs from the stamp of the last output frame or gap marker recorded to `last_dropped_ts`. Frames the ring dropped that the recording holds are not a loss (`hello_ack`). | — | — |
| 6 | CO-PW | CO → PW | unix socket `control.sock` | `replay` | the core | The core MUST send `from_seq` = `last_committed_seq` + 1 (C3/reattach-sequence). | — | — |
| 7 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | data: gap frame, only when step 5 found a loss | worker | The worker MUST write it before the first replayed frame: seq `first_buffered_seq` − 1 (the last lost seq), timestamp `last_dropped_ts`, no payload (ipc-data-frame top; C3/gap-frame). A gap frame appears nowhere else (ipc-data-frame top). | — | — |
| 8 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | data: output frame, each buffered one from max(`from_seq`, `first_buffered_seq`) through `current_seq` | worker | The worker MUST write each in order with its original seq and timestamp (`replay`; immortality §4.2; C3/seq-continuity-across-restart). | — | — |
| 9 | CO-PW | PW → CO | unix socket `control.sock` | `replay_done` | worker | `through_seq` MUST equal `hello_ack`'s `current_seq` (C3/reattach-sequence). The worker MUST accept input on that peer only after sending it (C3/input-refused-replaying). | An input frame before it is not delivered and is answered `input_refused` `replaying`, a core defect (ipc-control `input_refused`) | the core; the worker counts and logs it |
| 10 | CO-DK | CO → DK | file `<session_id>.ttyrec` + `.dir` | ttyrec `gap_marker` (code 2) for step 7; `data_frame` (code 0) for each frame of step 8 | the core | For a gap frame the core MUST write exactly one gap marker, unless step 5 found the loss already declared, data the `x-gap-line` line, stamped with the span's end, before the first frame that survived (C8/gap-marker, C8/gap-after-overflow). The marker is a commit point: the core MUST append no frame after it but an end marker until the write that commits it lands — from E2 stale-screen's one write (step 12), or a record-only write for a session with no snapshot (grid-snapshot rule 11) — so a later loss is counted from it and none is declared twice (continuity-state top; C8/gap-twice-across-restart; the wait: owed). The core MUST record each replayed output frame once, with the worker's stamp, and never a frame its recording already holds (ttyrec `x-sidecar` reopen; C3/seq-continuity-across-restart). | — | — |
| 11 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | kind 2 `output_frame`, for each frame of steps 7–8 | the core | **E2.** PTY shell sessions only (vt-ipc `open`). The core MUST feed every frame verbatim, gap frame included, in seq order (vt-ipc top; D16 the kind-2 framing, D17 the message names, D18 the order, all open: Open items; C4/vt-feed-order, whose core grading that fixture marks owed). The VT ignores a frame at or below its through_seq; a gap frame moves its through_seq and nothing else (vt-ipc top). | A jump without a gap frame: `error` `seq_jump`, grid unchanged → stale-screen, cause `grid_lost` (vt-ipc top) | — |
| 12 | — | CO → CO | — | → stale-screen | the core | **E2.** Only when step 7 declared a loss. stale-screen's first write is the gap commit, the forced snapshot and `stale_seq` in one bbolt write (continuity-state top). Until it lands the replay waits at the marker (step 10). The VT worker answers that snapshot at once (vt-ipc `snapshot`); when it does not within `snapshot.wait_sec`, or cannot, the core commits the marker record-only with the pair, gives the grid up and goes on (grid-snapshot rules 4, 12). | — | every attached client (stale-screen) |
| 13 | CO-PW | CO → PW | unix socket `control.sock` | `live`, when `hello_ack` said `ended: false` | the core | The core MUST send it only when the replay is complete: `replay_done` has arrived and either frame `through_seq` has arrived on the data peer, or nothing was due (`replay_done`; C3/reattach-sequence, C3/reattach-empty, C3/reattach-non-pty). The worker resumes output after it; the core sends input only after it (`live`), so the gate of step 3 opens here. | — | the caller: session-open offers the session northbound only now (`live`; C3/reattach-empty); data-socket-redial sends `data_path` `restored` |
| 14 | — | CO → CO | — | → byte-relay | the core | byte-relay carries the session's bytes from here. | — | — |
| 15 | — | CO → CO | — | → spooled-end-finalize, when `hello_ack` said `ended: true` | the core | After the replay is complete the core writes the spooled end, end marker and audit event (worker-manifest top). The core MUST NOT send `live` for an ended session (C3/reattach-ended; tsbc `control-reattach`). A session whose end the core already recorded from `channel_closed` `by: worker` (session-end-by-tsb step 23b) keeps that end: the event reaches the core before any `hello_ack` `ended: true` for the session, since the worker sends it as it ends the session; what the replay delivers before nothing more can be recorded is recorded, and nothing is written a second time. Under a running core (data-socket-redial) clients may be attached: every one MUST be closed, 4012 for an end from outside TSB (ipc-control top: "Every such end closes attached clients 4012"; ws-frames `x-close-codes`), 4010 for `killed` (conventions V3), as spooled-end-finalize step 9 writes it [owed]. Whether that end is audited here or as a live end: Open items. | — | as spooled-end-finalize says; under a running core every attached client, by its close |
| 16 | CO-PW | CO → PW | unix socket `control.sock` | `release` | the core | Takes `live`'s place: the core has recorded the session to its end, end marker included (`release`; C3/reattach-ended). The worker MUST keep the session's ring, data socket and manifest entry until `release` or until its connection closes, and then drop them (C3/ended-release-attached, C3/ended-retention-bounded). | — | — |
| 17 | PW-FS | PW → FS | file `workers/{worker_id}.json` | worker-manifest, without the released session | worker | A retained ended session stays listed until released or its connection closes, then goes (worker-manifest top; C3/ended-release-attached). | — | — |

## Refusals and who is told

- **Input during the handshake.** The gate is two checks in two processes: the core refuses client input on C2 from
  `hello` (or the loss) until `live` (step 3), and the worker delivers no input frame that arrives before
  `replay_done` (step 9). A worker's `input_refused` `replaying` is a core defect; tsbc `control-reattach` fails a
  capture that shows one.
- **A worker the core cannot speak to.** `unsupported_proto` or `unknown_cmd` in answer to `hello` makes the
  session unreachable / protocol (ipc-control top): never killed, input refused `unreachable`, the operator drains
  it (immortality §6.4).
- **A non-PTY session.** Its handshake gates input and resumes output, but while no core peer holds its data socket
  the worker stops reading its channel, so nothing is replayed and no gap is declared. Output already sent to a peer
  that died is, for sftp, the transfer layer's: it resumes by offset, outside the recording, with no gap frame
  (Contract §15.1; ipc-control top). Exec, netconf and forward have no loss rule at this tag, and the contract admits no silent splice for them either (ipc-control top; immortality §4.3).
- **Retention ends with the connection.** A retained ended session never holds `connection_linger`; when the
  connection closes, its retained sessions are released and the spool's numbers stand (`release`;
  C3/ended-retention-bounded). That close is connection-linger's or session-end-from-outside's.

## Security considerations

- **Who may type.** No byte reaches the target from `hello` (or the loss) until `live`: keystrokes typed while the
  screen still replays are how you type into the wrong prompt (immortality §6.3, as `hello` applies it to the local
  path). The core holds no refused input for later.
- **The host boundary.** Every socket on this path is trusted by SO_PEERCRED alone; the handshake carries no claim
  token and no secret (immortality §6.1, LOCKED; decision 5.30; conventions V10). No credential crosses here:
  credentials travel only in `connect.auth` and, from 0.7, `auth_answer` (ipc-control top). Northbound, a client learns only `input_refused`
  `replaying`, with no host detail (conventions V11).

## Open items

- **After `no_data_peer`.** The worker tells a core that failed to dial (`error` `no_data_peer`); what the core does
  next — re-dial, unreachable, or E10 — is unspecified. Plan §7 has no entry for it.
- **A gap in an ended session's replay.** Step 12 hands a declared loss to stale-screen. Whether an ended session (step
  15) raises stale-screen is unspecified. Plan §7 has no entry for it.
- **OPEN — who audits an end a running core's handshake finds** (not in plan §7). spool-entry top: "A core that is
  attached when the end happens audits it live and deletes the entry"; `release`: sent "after a handshake whose
  hello_ack reported ended: true and whose replay is complete". An end that lands between a lone data-socket loss and
  `hello_ack` `ended: true` happens with the core still attached on the control socket, yet
  its output waits for the replay. Step 15 routes it to spooled-end-finalize, the returning core's path
  (C3/reattach-ended); for a running core the contract names no path, live end (→ session-end-from-outside) or spooled
  end. Not in the queue or the review log.
- **OPEN, the contract against itself — the scope of `unreachable`.** common `session_state`: "at this tag only a v0.1
  worker under a v0.2 core"; C1/session-unreachable: "locally, only a v0.1 worker". ipc-control top and contract
  README "Senders and receivers": `unsupported_proto` or `unknown_cmd` from any peer whose version the core does not
  know makes its sessions `unreachable` / `protocol`. Step 4 follows the second. Not in the queue or the review log;
  conventions open item 17 carries it.
- **Approval items leaned on, not agreed:** D16, D17, D18 (step 11), open in the approval page's southbound set
  (queue A8).

## Picture

pending

**Quarantined recording continuation (tsb-program #141).** The next start dials a new peer and sends hello at P, then records the worker replay into the durably linked successor. A counted gap starts the successor when the ring lacks P + 1; its normal hold commits before later replay. No predecessor suffix is read for any decision. [owed]
