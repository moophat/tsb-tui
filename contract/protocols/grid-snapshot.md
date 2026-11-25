# grid-snapshot — advance a session's commit point

| | |
|---|---|
| **ID** | grid-snapshot |
| **Purpose** | Advance a session's commit point: snapshot the grid and store it with the recording position in one bbolt write. |
| **Status** | E2. PTY `shell` sessions only: no other session type gets a grid (vt-ipc `open`). A session with no `grid_snapshot_record` moves its commit point record-only (rule 11) |
| **Called by** | byte-relay step 10 (a cadence knob trips); size-control step 10, session-attach step 22, stale-screen step 19 (a PTY size change); stale-screen step 1 (a gap frame, from reattach-handshake step 12); reattach-handshake step 1 (the realign before every `hello`, rule 10); core-restart step 3 (a graceful stop, rule 13). grid-restore runs no step of it (Open item 9). Its own trigger: step 1. operator-alerts step 8 meets its failure |
| **Corpus clauses** | immortality §4.1, §5 (opening), §5.1, §5.2; Storage §5, §8.6; Contract §11, §13; open_decisions §1 (amendment 2026-07-19), §5 |
| **Seams** | C4, C6 |
| **Schemas** | `vt-ipc.schema.json#/$defs/snapshot`, `#/$defs/snapshot_ready`, `#/$defs/screen_change`, `#/$defs/engine`, `#/$defs/request_id`, `#/$defs/max_message_bytes`, `#/$defs/error`, `#/x-binary-layout` (kind 3); `continuity-state.schema.json` (top), `#/$defs/grid_snapshot_record`, `#/$defs/session_record` (`recording_last_seq_out`, `recording_committed_offset`, `out_bytes_committed`, `stale_seq`, `stale_cause`); `common.schema.json#/$defs/alert_kind` (`vt_unavailable`, through operator-alerts) |

Schema paths are relative to `contract/schemas/` in this release; fixtures are `<seam>/<name>`
under `contract/fixtures/`. Every C4 fixture cited here, and `C6/grid-snapshot-record` and `C6/snapshot-seq-aligned`,
is required-green e2 and reference-graded, its U2/U4/U5 graders owed. Corpus files are under `srs/`:
immortality = `immortality-mechanism.md`, Storage = `storage-and-discovery-architecture.md`, Contract =
`architecture-contract.md`. D-lines: the v0.3 approval page (tsb-core `plans/261006-1015-e2-contract/approval.md`). D24, a second
stored counter, is withdrawn (CHANGELOG contract-v0.3 D-lines, F1).

**Words used here.**

- **Grid**: the server-side terminal state of one `shell` session, held by the **VT worker**, the process behind
  `VTParser` (open_decisions §1; vt-ipc top). **through_seq**: the last output seq the grid has applied.
- **Snapshot**: the engine's own binary snapshot of a grid, opaque to the core (immortality §5.2; vt-ipc `snapshot`).
- **Commit point**: `recording_last_seq_out` with `recording_committed_offset` on the bbolt session record, and
  `out_bytes_committed` and `recording_last_seq_in` for the same point. It advances only in the write that stores a
  `grid_snapshot_record`, so at every write `snapshot_seq` equals `recording_last_seq_out`, immortality §4.1's
  `last_committed_seq`: one persisted value (continuity-state top; decision 5.39). It marks where a restore starts. The
  recording is never cut back to it: it may hold output past it, which a restart finds by walking forward from it
  (ttyrec `x-sidecar` reopen; open_decisions §5).
- **Realign**: moving the commit point to the recording's end before a `hello`, so that `hello`'s
  `last_committed_seq` is `snapshot_seq` and the recording's end at once (rule 10).
- **request_id**: correlates `snapshot` with its answer and names every chunk of a **kind-3 blob**, a binary message on
  the VT socket (vt-ipc `request_id`, `x-binary-layout`).

## Path

CO-VT → CO-BB · cannot be taken: (operator-alerts)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-VT | VT → CO | unix socket `vt/{vt_id}.sock`, kind 1 | `screen_change` | VT worker | names the output seq that caused `alt_enter` (CSI ?1049h), `alt_exit` (CSI ?1049l) or `clear` (ED 2/ED 3); a small steady-state event, not answered (vt-ipc `screen_change`) | — | the core |
| 2 | — | CO → CO | — | — | the core | a snapshot is due: a cadence knob, `snapshot.every_kb` or `snapshot.every_sec` (byte-relay step 10); a PTY size change (size-control, session-attach, stale-screen); step 1, the input `snapshot.on_alt_screen` needs (immortality §5.2); a gap frame fed to the VT (stale-screen step 1); a `hello` about to be sent while the recording holds output past the commit point (rule 10); a graceful stop (rule 13) | — | — |
| 3 | CO-VT | CO → VT | unix socket, kind 1 | `snapshot`, a new `request_id` | the core | asks for the grid's snapshot | no answer, the VT worker down or missing heartbeats: step 9. `error` `bad_request`, a snapshot the worker cannot take: rules 9, 12. `error` `unknown_session`: rule 12 when a commit must land, otherwise unspecified (Open items 6) | — |
| 4 | — | VT → VT | — | — | VT worker | the snapshot is of the grid as applied: frames and resizes in arrival order, a frame applied before the core commits it, so `through_seq` is by construction a commit point (vt-ipc top; D18, F3e) | — | — |
| 5 | CO-VT | VT → CO | unix socket, kind 1 | `snapshot_ready` | VT worker | at once, never after a later frame (vt-ipc `snapshot`); same `request_id`; `through_seq` (0 for a fresh grid with no committed output), the grid's `cols`/`rows`, `engine` | — | the core |
| 6 | CO-VT | VT → CO | unix socket, kind 3 | blob of the same `request_id` | VT worker | the engine's binary snapshot, split above `max_message_bytes` and reassembled by `request_id` in arrival order (`x-binary-layout`; D16, D21) | — | the core |
| 7 | CO-BB | CO → BB | bbolt write | `grid_snapshot_record` with `session_record` | the core | one write: the session's one record, the latest, replaced; `snapshot_seq` = `through_seq` = `recording_last_seq_out`; `recording_committed_offset`, `out_bytes_committed` and `recording_last_seq_in` at the same point, the offset the end of a frame past output frame `through_seq` and before the next output frame or gap marker, both recording files durable through it before the write (continuity-state `recording_committed_offset`); stored only at or past the commit point. The commit point advances here and nowhere else, but in rule 11's record-only write or the active-switch write (terminal-output-accounting §5.3) (continuity-state top; CHANGELOG C6) | — | — |
| 8 | CO-BB | CO → BB | bbolt write, the same as step 7 | → stale-screen: `session_record` `stale_seq`, `stale_cause` | the core | only when step 2's trigger was a gap frame; `stale_seq` is its seq (continuity-state `stale_seq`; stale-screen step 2) | — | as stale-screen says |
| 9 | — | CO → CO | — | → operator-alerts | the core | a snapshot that cannot be taken: no write, the commit point stays, `vt_unavailable` raised; the recording keeps every frame meanwhile, and a commit that must land now waits at most `snapshot.wait_sec`, then gives the grid up (rules 10, 12; common `alert_kind`; continuity-state top; operator-alerts step 8) | — | `broker.health` holders (operator-alerts) |

### Rules ("owed": no fixture grades it yet)

For a held or quarantined pair, rules 1–3, 9 and 13 and core-restart step 3 take no commit write: the commit point stays at P until the active switch, even if a later sync succeeds (F6; terminal-output-accounting §5.3; owed).

Rules 1–3 bind a recorded PTY shell. A snapshot is stored at the recording's commit point (rule 7; immortality §5.1:
one counter, aligned), and an unrecorded session has no recording to align one with (continuity-state:
`recording_path` absent). An unrecorded shell therefore takes no snapshot, and its grid is lost at a VT worker's death
(grid-restore steps 11 and 14: a session not recorded raises `grid_lost`). Nothing else changes for it: its record
still carries the commit-point fields continuity-state requires of every session, and every other rule here applies
as written.

1. **Cadence** (step 2). The core MUST take a snapshot when `snapshot.every_kb` or `snapshot.every_sec` trips, at the
   values `config.yaml` holds (immortality §5.2; conventions V7) [owed].
2. **Size changes** (step 2). The core MUST force a snapshot on every PTY size change, applied size requests and
   wiggles alike, so no size change lies between the last snapshot and a crash (`grid_snapshot_record`; CHANGELOG C6)
   [C4/vt-snapshot-on-resize].
3. **Screen events** (steps 1–2). The VT worker MUST send `screen_change` for each alternate-screen enter, exit and
   clear, naming the seq that caused it [C4/vt-screen-change]. The core MUST force a snapshot on each
   (`grid_snapshot_record`; CHANGELOG C6) [owed: the fixture shows the events, not the snapshot].
4. **Gap** (steps 2, 8). On a gap frame fed to the VT the core MUST force a snapshot and carry `stale_seq` and
   `stale_cause` in its write (continuity-state top; CHANGELOG C6; D25) [C6/stale-pending-durable]. The core sends the
   VT worker nothing for that session between the gap frame and the snapshot, so `through_seq` is the gap frame's seq
   and that write commits the gap marker; for a session with no `grid_snapshot_record` a record-only write commits it
   (rule 11). The core MUST append no frame to the recording after the marker until that write lands, an end marker
   excepted (continuity-state top; reattach-handshake step 10). The hold is bounded: rule 12 [owed].
5. **The answer** (steps 3–6). The VT worker MUST answer `snapshot` with `snapshot_ready` of the same `request_id`,
   then the snapshot as a kind-3 blob of that `request_id`, chunked above `max_message_bytes` (vt-ipc `snapshot`,
   `snapshot_ready`, `x-binary-layout`) [C4/vt-snapshot-restore; C4/vt-snapshot-on-resize; chunking C4/vt-frame-layout].
   It MUST answer at once with the grid as applied, an open sequence carried in the snapshot, and never wait for a later
   frame: the realign (rule 10) and a gap's hold (rule 4) wait on that answer. A snapshot it cannot take it MUST refuse
   at once with `error` `bad_request` (vt-ipc `snapshot`, `error`) [graded by U4
   `vt/internal/server/server_test.go` `TestASnapshotMidSequenceIsRefusedAtOnceNeverHeld`; with the real engine,
   `vt/internal/server/fixtures_test.go` `TestARealSnapshotOfASequenceLongerThanTheCapIsRefusedAtOnce`].
6. **Applied before committed** (step 4). The VT worker MUST apply a session's frames and resizes in arrival order,
   ignore a frame at or below `through_seq`, and report as `through_seq` the last frame applied (vt-ipc top; D18)
   [C4/vt-feed-order].
7. **One write** (step 7). The core MUST store `grid_snapshot_record` and the commit point in one bbolt write, with
   `snapshot_seq` = `through_seq` = `recording_last_seq_out`, and `out_bytes_committed` and `recording_last_seq_in` at
   the same point, after both recording files are durable through `recording_committed_offset` (continuity-state top,
   `recording_committed_offset`, `out_bytes_committed`, `recording_last_seq_in`) [C6/snapshot-seq-aligned;
   `out_bytes_committed` in the seq's write: C6/out-bytes-committed-after-reopen; the offset and `recording_last_seq_in`:
   owed]. It MUST NOT move any of the four numbers in any other write, but in rule 11's record-only write, the continuation active-switch write (terminal-output-accounting §5.3), and, for a
   session that is not recorded, the `recording_last_seq_in` reservation (continuity-state `recording_last_seq_in`);
   every other write of the session record carries the four as stored, never the recording's end the core holds in
   memory. It MUST NOT store a snapshot below the commit point (continuity-state `snapshot_seq`; CHANGELOG C6)
   [C6/snapshot-seq-aligned; the rest owed].
8. **The record** (step 7). The core MUST keep one per session, the latest: `snapshot` the blob unchanged, as base64,
   `engine` the engine that produced it, no `recording_offset`, and no `snapshot*` field outside this record
   (`grid_snapshot_record`; CHANGELOG C6 property-name guard; D23) [C6/grid-snapshot-record;
   C6/no-claim-token-field refuses `snapshot_seq` and `grid_snapshot` on the session record].
9. **Cannot be taken** (step 9). With its VT worker down or missing heartbeats the core MUST NOT advance the commit
   point and MUST raise `vt_unavailable` (vt-ipc `snapshot`; `grid_snapshot_record`; CHANGELOG C6)
   [C4/vt-snapshot-unavailable records the condition; the core's half is owed]. A cadence, size or screen-event
   snapshot that is refused, or not received whole (answer and blob) within `snapshot.wait_sec`, changes nothing: the record stays, still
   aligned with a commit point that did not move, and the core asks again at the next trigger. Only a commit that must
   land now — the realign (rule 10), a gap's hold (rule 4) — gives the grid up, by rule 12 [owed].
10. **Realign before `hello`** (step 2). For a quarantined pair, do not walk its suffix: restore P and use the linked successor as terminal-output-accounting §5.3 says [owed]. Before every other `hello` — a new session, a core restart, a lone data-socket re-dial
   — the core MUST move the commit point to the recording's end: a snapshot of a grid that has applied every output
   frame the recording holds (at a restart, the snapshot restored and the recording's own output frames past the commit
   point re-fed, grid-restore), stored as in step 7, or rule 11's record-only write for a session with no
   `grid_snapshot_record`. Then `hello`'s `last_committed_seq` is `snapshot_seq` (decision 5.39; immortality §4.1,
   §5.1) and a loss is declared only for output that neither the recording nor the ring holds (immortality §4.3).
   The snapshot and the commit point MUST name the same frame: the walk's last output seq. A grid the VT worker kept
   that stands past it — it applied the gap frame of a gap marker whose commit never landed, which the walk cannot
   number (ttyrec `x-sidecar` reopen) — is not snapshotted: the core restores the stored snapshot over it and
   re-feeds the recording to that seq; the handshake's re-sent gap frame then numbers the marker, and the gap's hold
   commits it (rule 4). A session with no `grid_snapshot_record` realigns record-only, and such a grid does no harm.
   With no output past the commit point no snapshot and no write is needed. A session whose end the core already knows
   — its spool entry or record holds the end, or its recording holds the end marker — needs no grid again: the core
   deletes its `grid_snapshot_record` and realigns record-only, with no stale flow. A wait for the snapshot is bounded
   by rule 12; until it ends the session gets no `hello` and its data socket is not dialled (core-restart step 15)
   [owed].
11. **No snapshot: record-only** (steps 2, 7). A quarantined predecessor stays at P; a record-only commit may advance the linked successor (§5.3) [owed]. A session with no `grid_snapshot_record` — a session type that gets no
   grid, or a shell before its first snapshot or after its snapshot was given up (rule 12) or found
   `snapshot_incompatible` — has no `snapshot_seq` to align with. Where step 2 or rule 10 would store a snapshot and
   none can be stored, the core MUST move its commit point to the recording's end in a write of the session record
   alone, the four numbers together, the recording durable first (continuity-state top) [owed].
12. **The wait is bounded; the recording ranks above the grid.** A quarantine's fallback never moves its predecessor above P; its successor follows the ordinary bounded wait (§5.3) [owed]. When a commit must land now — the realign (rule 10), a
   gap's hold (rule 4) — the core waits for the whole snapshot, `snapshot_ready` and every chunk of its kind-3 blob
   reassembled (steps 5–6), at most `snapshot.wait_sec`, a `config.yaml` knob (conventions V7): 1 to 600 seconds,
   default 30. When that time runs out at either stage, or the VT worker answers that it cannot
   (an `error` to the `snapshot`, or `unknown_session`), or the grid can never reach the recording's end (one reopened
   after `seq_jump`, grid-restore step 10), the core MUST give the grid up and keep the recording going: it deletes the session's
   `grid_snapshot_record`; it commits record-only (rule 11), with the gap's stale pair in that write for a hold; it
   goes on (sends `hello`, or appends the replay); and it raises the stale flow with cause `grid_lost`, `stale_seq`
   the commit point's seq, in that same write, unless a choice is already pending, which then stands
   (→ stale-screen). The corpus ranks the recording above the grid: "the byte stream is byte-perfect", while "grid
   continuity is best-effort" (open_decisions §4, LOCKED approach); the recording contract is LOCKED (open_decisions
   §5); and "core downtime = recording gap = audit hole = contract violation" (immortality §4). So the core never holds
   a recording back past this bound to save a grid. A grid given up that its VT worker no longer holds resumes as
   grid-restore step 10 says: an empty grid, fed from the first frame the old one did not apply [owed].
13. **A graceful stop** (core-restart step 3). Before it closes its VT sockets a stopping core MUST snapshot every grid
   whose recording holds output past its commit point, as in step 7, each wait bounded by `snapshot.wait_sec`, so an
   ordinary restart needs no realign; a grid not stored then is realigned at the next start [owed].

## Refusals and who is told

- grid-snapshot refuses no principal and tells no client. No frame and no northbound record field carries a snapshot or a
  commit point; `grid_snapshot_record` is local state (continuity-state top; conventions V11).
- **Cannot be taken** (step 9): `broker.health` holders learn it from operator-alerts. The recording keeps every frame
  meanwhile; a grid given up after `snapshot.wait_sec` reaches clients as `grid_lost` through stale-screen (rule 12).
- **A VT `error`** in answer to `snapshot` (rules 9, 12): when no commit waits on the snapshot, it changes nothing; the
  record stays and the core asks again at the next trigger. When a realign (rule 10) or a gap's hold (rule 4) waits on
  it, the core gives the grid up: the record deleted, a record-only commit, then `grid_lost` through stale-screen. Only
  `unknown_session` with no commit waiting stays unspecified (Open items 6).

## Security considerations

- **Local trust.** The VT socket accepts a peer only with the TSB service uid, by SO_PEERCRED, with no token
  (vt-ipc top; immortality §6.1, LOCKED; conventions V10) [foreign-uid refusal owed, as V10 says].
- **Cell content stays inside the broker.** The snapshot and the attach dump are C4's only cell-bearing forms, and no
  API surface exposes cell content (vt-ipc top, R8; Contract §11). The core MUST NOT serve a snapshot or its cells on
  any northbound surface [owed].
- **No credential.** Vault credentials are injected below the byte stream and never enter the PTY stream
  (open_decisions §5), so none reaches a grid or a snapshot.

## Open items

1. **Closed 2026-10-11 — queue A2 (plan §7 C-1), ruled by the operator (O1).** The contract's reading stands: no grid
   mirror crosses C4 and no per-frame deltas; the grid lives in the VT worker. The corpus now says the same
   (open_decisions §1, immortality §5 and §5.1, Storage §8.6; vault f970731, re-exported in this release).
2. **Closed — queue A1.** A restart no longer cuts the recording back to the commit point: it cuts only a torn last
   frame, and before every `hello` the core realigns the commit point to the recording's end (rule 10), so `hello`
   carries `snapshot_seq` and the worker is asked only for what the recording lacks (continuity-state top; ttyrec
   `x-sidecar` reopen; ipc-control `hello`). Input frames, broker markers and an end marker stay (open_decisions §5;
   Contract §13).
3. **Unspecified (not in plan §7): one snapshot per wiggle, or two.** The contract forces one on "every PTY size
   change" (`grid_snapshot_record`). session-attach step 22 runs grid-snapshot once after the pair; stale-screen step 19 and its rule 13 say
   each size change.
4. **Closed — queue A1.** The offset stored with `snapshot_seq` is the end of a frame past output frame
   `snapshot_seq` and before the next output frame or gap marker, with `out_bytes_committed` and `recording_last_seq_in` for the
   same point, both recording files durable through it before the write (step 7; continuity-state
   `recording_committed_offset`).
5. **Closed — queue A1.** A gap with no snapshot cannot hold the recording: after `snapshot.wait_sec`, or at once
   when the VT worker answers that it cannot, the core gives the grid up, commits the gap marker record-only with the
   gap's stale pair, and goes on (rules 4, 12).
6. **Unspecified (not in plan §7): the failure path.** `snapshot.wait_sec` bounds the wait for the whole snapshot,
   answer and blob (rule 12); what the core does on `error` `unknown_session` to a snapshot no commit waits on; whether a VT worker that fails each forced snapshot raises
   `vt_unavailable` again (operator-alerts's open items). When it clears is plan §7 G-10 (operator-alerts).
7. **Closed — queue A1.** A session with no grid moves its commit point record-only (rule 11).
8. **Unspecified (not in plan §7): the cadence knobs.** What `snapshot.every_kb` counts, how the "time floor"
   `snapshot.every_sec` combines with it (immortality §5.2), and whether `snapshot.on_alt_screen` can switch off the
   alt-screen snapshot the contract forces unconditionally (CHANGELOG C6; conventions V7 lists it as a knob).
9. **Unspecified, carried:** whether a restore under grid-restore is
   followed by a snapshot (grid-restore runs no grid-snapshot step; not in the queue or the review log).
10. **Unagreed DEFAULTs leaned on:** D16 (C4 framing, chunked kind-3 blobs: step 6), D17 (C4 message names),
    D18 (feed before commit; conventions open item 3), D21 (the opaque snapshot, `snapshot_incompatible`) and D23 (the
    record as JSON and base64), still open on the approval page; queue A8 groups them, to follow A2. Step 9 also
    leans on `vt_unavailable`, which is not on the approval page: the operator has not seen this alert kind (queue
    S46; operator-alerts open item C-7).

## Picture

pending

**Quarantined recording continuation (tsb-program #141).** Rules 4 and 10–12 operate on the successor pair as specified in terminal-output-accounting §5.3: never realign through the quarantined suffix; restore the trusted snapshot at P, or take the grid_lost stale path, and feed replay once. A successor gap's hold commits before later replay. The predecessor's P remains fixed while the successor may advance. Pair sync precedes the bbolt commit and aligned snapshot. [owed]
