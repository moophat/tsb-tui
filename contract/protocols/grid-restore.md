# grid-restore — keep every grid through a VT worker's death or a core restart

| | |
|---|---|
| **ID** | grid-restore |
| **Purpose** | Keep every grid through a VT worker's death or a core restart: learn what the VT worker holds, restore from the last snapshot, re-feed, and hand a grid that cannot be rebuilt to stale-screen. |
| **Status** | E2 (the VT worker and its seam, C4, are new at contract-v0.3) |
| **Called by** | core-restart step 21 (a core restart). While the core lives, grid-restore owns its own trigger: a VT worker that dies or misses heartbeats (vt-ipc top). It calls stale-screen (`grid_lost`) and operator-alerts (`vt_unavailable`); at a core restart, reattach-handshake follows it (core-restart step 23); each owns its own exchange (plan §2.1). |
| **Corpus clauses** | open_decisions §1 (LOCKED; amendment 2026-07-19); immortality §2.1, §3 (the Grid column), §5.1–§5.3, §6.1, §6.4; Contract §4, §11; session model §9 E7 (by analogy) |
| **Seams** | C4, C6; C3, C8 and C2 through reattach-handshake, stale-screen and session-attach |
| **Schemas** | `vt-ipc.schema.json` (top: "Order and idempotence", "Grids outlive the core", "Snapshot", "Version"), `#/$defs/hello`, `#/$defs/hello_ack`, `#/$defs/held_grid`, `#/$defs/proto_version`, `#/$defs/engine`, `#/$defs/open`, `#/$defs/close`, `#/$defs/restore`, `#/$defs/restored`, `#/$defs/request_id`, `#/$defs/max_message_bytes`, `#/$defs/output_frame`, `#/$defs/ping`, `#/$defs/pong`, `#/$defs/error`, `#/x-binary-layout` (kinds 2, 3); `continuity-state.schema.json#/$defs/grid_snapshot_record`, `#/$defs/session_record` (`recording_last_seq_out`, `stale_seq`, `stale_cause`); `ipc-control.schema.json#/$defs/hello` (through reattach-handshake); `ws-frames.schema.json#/$defs/input_refused`, `#/$defs/data_path`; `common.schema.json#/$defs/alert_kind`, `#/$defs/alert_subjects` |

Schema paths are relative to `contract/schemas/` in this release. Fixtures are
`<seam>/<name>` under `contract/fixtures/`. Every fixture cited here is marked required-green e2 and its U2, U4 or
U11 graders are owed: reference, tests owed (conventions V8). Corpus files are under `srs/`:
immortality = `immortality-mechanism.md`, Contract = `architecture-contract.md`, session model =
`session-model-and-lifecycle.md`, open_decisions = `open-decisions.md`. Socket names are relative to TSB's
node-local runtime directory (vt-ipc top). "v0.3 plan" is tsb-core `plans/261006-1015-e2-contract/plan.md`; "queue" is
tsb-core `plans/reports/queue-261007-operator-decisions.md`; "verifier" is tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`.

**Words used here.**

- **grid**: a session's terminal state, held by the VT worker behind `VTParser` (Contract §11; open_decisions §1).
  PTY `shell` sessions only: no other session type is opened (vt-ipc `open`).
- **held grid**: one the VT worker lists in `hello_ack.grids`, with `through_seq`, the highest output seq it has
  applied, and its size (vt-ipc `held_grid`).
- **snapshot**: the engine's own binary snapshot, opaque to the core, kept in bbolt as the session's latest
  `grid_snapshot_record` (continuity-state; immortality §5.2).
- **commit point**: `recording_last_seq_out` with `recording_committed_offset`, advanced only in the bbolt write that
  stores a snapshot, so `snapshot_seq` equals it (continuity-state top; 5.39).
- **the recording's end**: the last output frame or gap marker the session's recording keeps, at or past the commit
  point: found at a restart by walking the recording forward from the commit point, and kept in memory while the
  core runs (ttyrec `x-sidecar` reopen). Before a `hello` the core realigns the commit point to it, so `hello` carries
  `snapshot_seq` (grid-snapshot rule 10).
- **re-feed**: output frames sent to a grid again; the VT worker ignores a frame at or below its `through_seq` (D18).
  After a restore the core re-feeds the recording's own output frames past the commit point, numbered by that walk
  (vt-ipc top).

## Path

CO-FS → CO-VT → CO-BB → CO-VT → CO-VT (the recording's frames) → at a restart: (reattach-handshake) → CO-VT → CO-DK → (stale-screen) → SF-CO · heartbeat: CO-VT → (operator-alerts), then CO-FS again

## Steps

Steps 1–4 run on every connection to a VT worker: at a core start (core-restart step 21) and when a restarted VT worker's
socket is there. Steps 5–15 run per PTY `shell` session. Steps 16–18 run while the core holds the socket.

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-FS | FS → CO | file (directory) | `vt/`: one socket `vt/{vt_id}.sock` per VT worker (no contract shape) | the core | The core dials every socket there; N VT workers are allowed and one is configured; which session goes where is the core's own policy (vt-ipc top, D15; plan §3 CO-FS). When it looks: Open item 7. | — | — |
| 2 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | dial (no message) | the core | The core MUST be the side that dials, and MUST accept the peer only when SO_PEERCRED gives the TSB service uid, with no token anywhere on this seam (vt-ipc top; immortality §6.1, LOCKED; conventions V10) [owed]. | another uid: not accepted (vt-ipc top) | — |
| 3 | CO-VT | CO → VT | unix socket | `hello` | the core | MUST be the first message on the socket, carrying `proto_version` (vt-ipc `hello`, `proto_version`; conventions V1). A core that reconnects, after a restart or a re-dial, sends it again [C4/vt-hello-grids]. | `error` `unsupported_proto`: the core's next step is unspecified (Open item 7) [owed: no C4 fixture] | — |
| 4 | CO-VT | VT → CO | unix socket | `hello_ack`: `engine`, `grids` (one `held_grid` each) | VT worker | The VT worker MUST keep every grid when a core's socket closes, dropping one only on `close` or its own exit, and MUST list every grid it holds; a fresh process lists none (vt-ipc `hello_ack`, `close`; D19) [C4/vt-hello-grids; C4/vt-restore-after-vt-death]. `engine` names the engine it runs, so the core can tell whether a snapshot it holds is compatible (vt-ipc `engine`). | — | the core |
| 5 | — | CO → CO | — | chooses what to do with each session's grid | the core | A held grid is kept, re-fed or replaced by `restore`: "its policy, not the contract's" (v0.3 plan C4 decision 5; Open item 6), except that a held grid standing past the recording walk's last output seq (it applied the gap frame of a marker whose commit never landed) MUST be replaced by `restore` before a realign (grid-snapshot rule 10) [owed]. An `open` of a held grid keeps it (vt-ipc `open`); a re-fed frame at or below its `through_seq` is ignored (D18). A session whose grid no VT worker holds is restored from its snapshot (vt-ipc top; immortality §5.1): steps 6–9. | — | — |
| 6 | CO-BB | BB → CO | bbolt read | `grid_snapshot_record` | the core | The session's latest snapshot: its bytes, `snapshot_seq`, size and `engine`. At every write `snapshot_seq` equals the session record's `recording_last_seq_out` (continuity-state; 5.39). | none stored: Open item 7 | — |
| 7 | CO-VT | CO → VT | unix socket | `open` | the core | Sent before the `restore` in C4/vt-restore-after-vt-death; the schema does not require it (Open item 7). PTY `shell` sessions only (vt-ipc `open`). | — | — |
| 8 | CO-VT | CO → VT | unix socket | `restore` + kind-3 blob of the snapshot's bytes (`x-binary-layout`) | the core | After a VT worker's death the core MUST restore each affected session's last snapshot into the restarted worker (vt-ipc top) [C4/vt-restore-after-vt-death]: `snapshot_seq`, `cols` and `rows` from the record; the bytes under the same `request_id`, split across messages above `max_message_bytes` (vt-ipc `restore`, `max_message_bytes`; D16) [C4/vt-snapshot-restore]. | — | — |
| 9 | CO-VT | VT → CO | unix socket | `restored` | VT worker | MUST carry the `restore`'s `request_id`; `through_seq` is the seq the restored grid is at (vt-ipc `restored`) [C4/vt-snapshot-restore; C4/vt-restore-after-vt-death]. | `error` `snapshot_incompatible`, no `restored`: a snapshot from another engine version (vt-ipc `restore`; D21) [C4/vt-snapshot-restore; C4/vt-grid-lost] → step 10 | the core |
| 10 | CO-VT | CO → VT | unix socket | `close` when a grid exists, then `open`: an empty grid | the core | When a grid can no longer take the next frame — `seq_jump` (step 12), `snapshot_incompatible` (step 9), or a `shell` session with no grid at all — the core MUST close that grid when the VT worker holds one (a `close` answered `unknown_session` changes nothing), open an empty one, and raise the stale flow (step 14) (vt-ipc `open`, `close`, `restore`, `error`; 5.52) [C4/vt-grid-lost]. It MUST feed the empty grid from the first frame the old grid did not apply, then every later frame in seq order: after `seq_jump` the refused frame itself; after `snapshot_incompatible` the frames past the snapshot, the recording's own (step 11), then the worker's replay (step 12); for a session with no grid the next output frame. A grid that has applied no output frame takes the seq of the first output frame it is fed as its start, so a feed that starts past seq 1 is applied, not refused (vt-ipc `open`). The new grid starts blank and its dump shows only what it has applied since, so an attach proceeds (prefix, then the stale frames) instead of being held and closed 4011. | — | — |
| 11 | — | CO → VT | unix socket | kind 2 `output_frame`: the recording's own output frames past the commit point | the core | After the grid's restore, or onto a grid the VT worker kept, the core MUST feed the recording's own output frames past the commit point, numbered by the recording's walk, then any gap frame a hold keeps (grid-snapshot rule 4), then the frames it records meanwhile, in seq order (vt-ipc top; ttyrec `x-sidecar` reopen; continuity-state top) [owed]. After a VT worker's death the data path never broke: no handshake runs and input is not gated; at a core restart the realign snapshot follows, then core-restart step 23 runs the handshake (grid-snapshot rule 10). **A session that is not recorded** has nothing to re-feed: the frames after `snapshot_seq` are gone (no core ring before E5), so a VT worker's death loses its grid, and the core raises the stale flow with cause `grid_lost` (step 14). **A size change that missed its snapshot** (the restored snapshot's `cols`/`rows` differ from the session's PTY size) means the re-fed frames were produced at two sizes: the rebuilt grid is not exact, and the core raises `grid_lost` after the re-feed (step 14) [owed]. | a grid that cannot be rebuilt → step 14 | — |
| 12 | CO-VT | CO → VT | unix socket | kind 2 `output_frame`: each replayed frame, gap frame included | the core | At a core restart: the worker's replay of seq > `snapshot_seq`, which is only what the recording lacks. The core MUST feed each verbatim, in seq order (reattach-handshake step 11; vt-ipc top, D18) [C4/vt-feed-order]. The VT ignores a frame at or below its `through_seq`, so re-feeding a kept or restored grid is harmless (D18). | `error` `seq_jump`, grid unchanged (D18) [C4/vt-grid-lost] → step 10 (close and re-open an empty grid), step 14 | the core |
| 13 | CO-DK | CO → DK | file `<session_id>.ttyrec` + `.dir` | → reattach-handshake: ttyrec `data_frame` (code 0), only for a replayed frame past the recording's end | the core | `hello` carries `snapshot_seq`, realigned to the recording's end, so the worker replays only frames past it, and the core MUST NOT record a frame the recording already holds (vt-ipc top; ttyrec `x-sidecar` reopen; reattach-handshake step 10) [C2/prefix-after-restart; the recording side owed to U2, per C4/vt-restore-after-vt-death]. | — | — |
| 14 | — | CO → CO | — | → stale-screen, cause `grid_lost` | the core | After step 10's feed, on step 12's `seq_jump`, or in step 11's two cases (a session not recorded; a missed size change), the core MUST raise it, unless a choice is already pending, `stale_seq` being the committed seq at which the grid could not be rebuilt, written alone before the first new commit (continuity-state `stale_seq`, `stale_cause`; 5.52) [C4/vt-grid-lost]. After `snapshot_incompatible` that write also deletes the `grid_snapshot_record`, which this engine can never restore, and the session commits record-only until a new snapshot is stored (grid-snapshot rule 11) [owed]. It MUST NOT raise it after a restart that lost nothing and rebuilt its grid (immortality §5.3) [owed]. | — | every attached client (stale-screen) |
| 15 | SF-CO | CO → SF | WebSocket | → session-attach: `prefix` + binary: dump (conventions V15) | the core | After a core or a VT worker restart, the next attach's dump MUST yield the screen the rebuilt grid holds [C2/prefix-after-restart]. | no VT worker holds the grid: session-attach step 14 (4011; D3 OPEN) | the attaching client |
| 16 | CO-VT | CO → VT | unix socket | `ping` | the core | The core MUST probe each VT worker it holds a socket to (vt-ipc top, `ping`) [C4/vt-ping-pong; the cadence owed]. Cadence and missed count: Open item 7. | — | — |
| 17 | CO-VT | VT → CO | unix socket | `pong` | VT worker | MUST answer each `ping` from its own loop, in order: neither carries an id (vt-ipc `ping`, `pong`) [C4/vt-ping-pong]. A hung CGo call is a dead VT worker (vt-ipc top; session model §9 E7, by analogy). | — | the core |
| 18 | — | CO → CO | — | → operator-alerts: raise `vt_unavailable` | the core | A VT worker that misses heartbeats is dead: the core MUST stop using it and raise the alert, listing the sessions whose grids it held (vt-ipc top; common `alert_kind`, `alert_subjects`) [owed; C4/vt-snapshot-unavailable records the condition]. Restarting its container is out of band (vt-ipc top; Contract §4). Once a restarted VT worker's socket is there, steps 1–15 run for each session it held. | — | `broker.health` holders (operator-alerts) |

## Refusals and who is told

- **The VT worker refuses only to the core.** `snapshot_incompatible` and `seq_jump` lead to stale-screen (steps 9, 10,
  12, 14): on either, or when no grid at all holds a `shell` session, the core MUST replace the grid with an empty
  one, fed from the first frame the old grid did not apply, and raise the stale flow (step 10); `unknown_session` says it holds no such grid; `bad_request`
  and `unknown_cmd` keep the connection (vt-ipc `error`; conventions V2); `unsupported_proto` is Open item 7.
  `vt_unavailable` is the core's operator alert, never a VT error code (vt-ipc `error`).
- **Clients while a grid is rebuilt.** After a VT worker's death the data path never broke: no handshake runs, no
  `data_path` is sent and input is not gated (step 11). At a core restart input is refused `replaying` from `hello`
  until `live` (reattach-handshake step 3; ws-frames `input_refused`).
- **While no VT worker holds a session's grid.** No snapshot can be taken, so the commit point does not advance and
  `vt_unavailable` stands (continuity-state `grid_snapshot_record`; → grid-snapshot, operator-alerts step 8). The
  recording keeps every frame meanwhile and the restore re-feeds them (step 11; common `alert_kind`); a session that
  must reattach meanwhile waits for a snapshot at most `snapshot.wait_sec`, then gives the grid up (grid-snapshot
  rules 10, 12).
  An attach is held, then closed 4011
  (session-attach step 14; D3 OPEN).
- **Who is told.** Every attached client learns a `grid_lost` from stale-screen's `stale` frame; `broker.health` holders
  learn `vt_unavailable` on the alert stream. No other message names a VT worker's death (operator-alerts, G-13).

## Security considerations

- **Who may type.** During a core restart's rebuild no byte reaches the target from `hello` until `live`
  (reattach-handshake step 3; immortality §6.3, as `hello` applies it). A VT worker's death replays nothing to anyone,
  so it gates no input. A grid that cannot be rebuilt refuses all input until the keyboard holder
  chooses a remedy (stale-screen; immortality §5.3).
- **The host boundary.** The VT socket is trusted by SO_PEERCRED alone, with no token (immortality §6.1, LOCKED;
  conventions V10); credentials travel only in C3 `connect.auth` and, from 0.7, `auth_answer` (ipc-control top). The snapshot and the dump are the
  only forms that carry cells, and cell content stays inside the broker (vt-ipc top; Contract §11).
  `vt_unavailable` names no component of its own and no host detail (common `alert_subjects`; conventions V11).

## Open items

1. **Closed 2026-10-11 — queue A2 (plan §7 C-1), ruled by the operator (O1).** The contract's reading stands: no grid
   mirror crosses C4 and no per-frame deltas; the grid lives in the VT worker. The corpus now says the same
   (open_decisions §1, immortality §5 and §5.1, Storage §8.6; vault f970731, re-exported in this release).
2. **Closed — queue A1.** `hello` carries `snapshot_seq`, realigned to the recording's end, and the grid takes the
   recording's own frames past the commit point first, so a frame the recording holds is never asked of the worker's
   ring (step 11; grid-snapshot rule 10; ipc-control `hello`). A VT worker's death runs no handshake.
3. **OPEN — queue A8 (D15–D21, D23, still open on the approval page; the queue advises deciding A2 first).** Every
   step leans on them: D15 the socket and its trust (steps 1–2), D16 chunked blobs (step 8), D17 the message names,
   D18 the idempotent feed and `seq_jump` (step 12), D19 grids outlive the core (step 4), D21 `snapshot_incompatible`
   (step 9), D23 the stored snapshot (step 6).
4. **Closed — queue A1.** A VT worker's death runs no re-dial, so G-15's `data_path` question does not arise (step
   11; vt-ipc top).
5. **Closed — queue A1.** The VT worker being down while the core runs loses no recorded output: the recording keeps
   every frame and the restore re-feeds them (step 11). A grid can still be lost: a session that is not recorded, a
   size change that missed its snapshot, or a grid given up after `snapshot.wait_sec` (grid-snapshot rule 12) — each
   cause `grid_lost`. Lost output is only ever a declared gap, cause `gap` (`C4/vt-snapshot-unavailable`;
   continuity-state `stale_cause`).
6. **Stated only in the v0.3 plan, not in the published contract.** The keep, re-feed or replace choice per held
   grid is "its policy, not the contract's" (C4 decision 5; step 5). The rules page lets only a `config.yaml` knob
   vary between implementations (README "Normative words"); no knob names this choice.
7. **Unspecified (not in plan §7):**
   - when the core reads `vt/` and how it learns a restarted VT worker is back (step 1); who restarts a VT worker
     that died, where vt-ipc puts only a missed-heartbeat restart out of band; how the core observes a death other
     than by missed heartbeats (E9 is C3's);
   - the core's next step after C4 `unsupported_proto`; no C4 fixture grades that refusal;
   - the heartbeat's cadence and missed count: no knob or field names them, and conventions V7's list of knobs with no
     contract field does not list them (conventions open item 10 carries it); E7 is borrowed by analogy;
   - whether the core restores at all when `hello_ack.engine` already differs from the record's `engine`; whether a
     refused `restore` leaves the grid as it was. (How the empty grid of step 10 takes a replay that starts past
     seq 1 is answered in vt-ipc `open`: it takes the first frame it is fed as its start.)
   - whether `restore` needs an open grid: C4/vt-restore-after-vt-death sends `open` first and the schema says
     nothing; the same fixture models the death by a `close` of another session on the same socket, which the prose
     never sends;
   - a PTY session with no `grid_snapshot_record` yet (step 6);
   - whether replayed frames the attached clients already received go to them again: "fed to the VT only" (vt-ipc
     top) names only the recording;
   - a held grid whose session is not stored, or ended while the core was away: `close` or restore (session-end-by-tsb open
     item 7; spooled-end-finalize open item 1);
   - a restored grid's size against the PTY's size after a size change made while no snapshot could be taken (size-control
     open items, "Unspecified" (7)).
8. **Carried from other drafts.** G-10, when `vt_unavailable` clears and how a rebuilt one is re-checked (operator-alerts);
   `vt_unavailable` itself (step 18) is not on the approval page: the operator has not seen this alert kind (queue S46;
   operator-alerts open item C-7);
   D3, an attach while no VT worker holds the grid (session-attach open item 1; queue A3); the known gap "VT upgrade vs
   snapshot format": how an upgrade is staged so no `grid_lost` follows is U4's and U2's, not the contract's
   (CHANGELOG contract-v0.3).

## Picture

pending

**Quarantined recording continuation (tsb-program #141).** Restore only a trusted snapshot at P and feed the worker replay once; never feed a quarantined suffix for a grid decision. If restoration is not possible, follow the existing grid_lost stale path (terminal-output-accounting §5.3). [owed]
