# Edge CO-VT — core ↔ VT worker

| | |
|---|---|
| **Edge** | CO-VT |
| **Ends** | CO (core) and VT (VT worker); the core dials |
| **Seam** | C4 |
| **Status** | E2 (the VT worker and C4 are new at contract-v0.3). PTY `shell` sessions only: no other session type has a grid (`vt-ipc` `open`) |
| **Transport** | unix socket `vt/{vt_id}.sock` under the shared runtime volume; `[u32 BE length][u8 kind][body]`; kind 1 a JSON control object, kind 2 an output frame, kind 3 a blob chunked by `request_id` |
| **Schemas** | `contract/schemas/vt-ipc.schema.json` (top, `$defs`, `x-binary-layout`) at contract-v0.3; `vt-screen.schema.json` is not on this edge (see below) |

This page is an index. The shape of every message is in the schema; every rule is in the protocol or in
[`../conventions.md`](../conventions.md). Nothing is restated here.

## Transport rules

| Rule | Where |
|---|---|
| Trust is SO_PEERCRED: a peer is accepted only with the TSB service uid, no token on this seam. The core is the only dialler | `conventions.md` V10; `vt-ipc` top (D15); grid-restore step 2 |
| `proto_version` is "0.3" on `hello` and `hello_ack`; additive only, unknown fields and events ignored | `conventions.md` V1, V2; `vt-ipc#/$defs/proto_version`; grid-restore step 3 |
| The maximum message size is a knob (range 1 to 2^32-1), never a default; a blob over it is split into kind-3 chunks of one `request_id`, reassembled in arrival order | `conventions.md` V7; `vt-ipc#/$defs/max_message_bytes`, `x-binary-layout` (D16) |
| An unknown command is answered `error` `unknown_cmd` and the connection is kept; a malformed object is `bad_request` | `conventions.md` V2; `vt-ipc#/$defs/error`; fixture `C4/vt-unknown-cmd` (reference, tests owed) |
| `ping` is answered by `pong` from the VT worker's own loop, in order, no id on either; a hung CGo call is a dead VT worker | `vt-ipc#/$defs/ping`, `pong`; grid-restore steps 16–17 |
| No cell content goes northbound: the dump and the snapshot are the only cell-bearing forms and stay inside the broker; no attach or client message crosses this seam | `vt-ipc` top (R8); session-attach steps 10–13 |
| Per session the VT applies output frames and resizes in arrival order and ignores a frame at or below its `through_seq` | `vt-ipc` top (D18); byte-relay step 9; grid-snapshot |
| The VT worker keeps every grid when the core's socket closes, dropping one only on `close` or on its own exit | `vt-ipc` top (D19); core-restart step 3; grid-restore step 4 |
| Error codes are a closed seam enum, listed in the registry, not here; `vt_unavailable` is the core's alert, not a VT code | `conventions.md` V4; `vt-ipc#/$defs/error` (`code`) |

## Messages

Status `E2` means published and required at E2. Protocol steps are cited as ID step.

### Core → VT, kind 1

| `$def` | Used by | Status |
|---|---|---|
| `hello` | grid-restore 3 (core-restart 21 → grid-restore) | E2 |
| `open` | session-open 16; grid-restore 7, 10 | E2 |
| `resize` | size-control 9; session-attach 21; stale-screen 18 | E2 |
| `prefix` | session-attach 10 | E2 |
| `snapshot` | grid-snapshot 3; operator-alerts 8 (via grid-snapshot) | E2 |
| `restore` (+ kind-3 blob) | grid-restore 8 (core-restart 21 → grid-restore) | E2 |
| `close` | session-end-by-tsb 18; session-end-from-outside 17 | E2 |
| `ping` | grid-restore 16; operator-alerts 8 (via grid-restore) | E2 |

### VT → core, kind 1

| `$def` | Used by | Status |
|---|---|---|
| `hello_ack` (with `held_grid`) | grid-restore 4 | E2 |
| `prefix_ready` (+ kind-3 dump) | session-attach 11 | E2 |
| `snapshot_ready` (+ kind-3 snapshot) | grid-snapshot 5, 6: at once, never after a later frame (rule 5) | E2 |
| `restored` | grid-restore 9 | E2 |
| `screen_change` | grid-snapshot 1 (byte-relay notes it returns per event, not per frame) | E2 |
| `applied` | scroll-control 2; scroll-control rule 12; fixture `C4/vt-applied-ground` | applied: published e2, required e5 |
| `pong` | grid-restore 17 | E2 |
| `error` | `unknown_session`: size-control 9, stale-screen 18, grid-snapshot 3 (unspecified). `seq_jump`: byte-relay 9, grid-restore 12. `snapshot_incompatible`: grid-restore 9. `unsupported_proto`: grid-restore 3. `bad_request`, `unknown_cmd`: V2 only | E2 |

Schema-only `$defs` (no wire message of their own): `core_message`, `vt_message` (the two kind-1 unions),
`proto_version` (V1), `engine` (grid-snapshot, grid-restore), `max_message_bytes` (V7), `request_id`, `session_id`,
`scrollback_bytes` (session-open 16; V7), `held_grid` (grid-restore 4).

### Binary kinds

| Kind | Direction | Body | Used by | Status |
|---|---|---|---|---|
| 2, `output_frame` | CO → VT | `[u8 id_len][session_id][C3 data-frame envelope + payload]`, gap frames included, byte-identical to `ipc-data-frame.schema.json`; a frame re-fed from the recording carries its recorded stamp (vt-ipc top) | byte-relay 9; reattach-handshake 11; grid-restore 11 (re-fed from the recording), 12; scroll-control 1 | E2 |
| 3, blob, snapshot | CO → VT | `[u8 id_len][request_id][bytes]`, chunked | grid-restore 8 | E2 |
| 3, blob, snapshot | VT → CO | same | grid-snapshot 6 | E2 |
| 3, blob, dump | VT → CO | same | session-attach 11 | E2 |

There is no VT → core kind 2.

## Not on the wire

`contract/schemas/vt-screen.schema.json` is the shape of an expected screen. It is fixture-only, and no C4 message or
client message carries it (`vt-ipc` top, R8). No C4 golden fixture stores an expected screen: each is graded by a
libghostty-vt round trip, the dump re-fed to a fresh engine giving the same screen and modes (decision 5.47 as
corrected 2026-10-06; the `C4/golden-*` descriptions, F14). Contract text stale: `vt-screen` top still says a golden
fixture asserts that its screen equals this document (open item below).

## Open items on this edge (pointers only)

- **Queue A2, grid deltas.** Open corpus clause against "no grid mirror, no per-frame deltas": grid-snapshot open item 1,
  grid-restore open item 1, byte-relay open item (step 9).
- **Queue A8, D15–D21 and D23.** grid-restore open item 3 maps them: D15 socket and trust (grid-restore 1–2), D16 chunked blobs
  (grid-restore 8), D17 names, D18 feed and `seq_jump` (grid-restore 12), D19 grids outlive the core (grid-restore 4), D21
  `snapshot_incompatible` (grid-restore 9), D23 stored snapshot (grid-restore 6); grid-snapshot open item 10 repeats it. D20 (what the dump
  carries): session-attach R9.
- **Stale contract text, ruled:** `vt-screen` top describes golden fixtures that compare against a stored screen;
  decision 5.47 (as corrected) and the `C4/golden-*` fixtures grade by a libghostty-vt round trip and store none. Not
  in the queue's contract-text list.
- **Dump errors, `hello` refusal, ping cadence:** session-attach step 11 (VT `error` on `prefix` unspecified); grid-restore open item 7
  (`unsupported_proto` next step, `ping` cadence, `open` before `restore`); grid-snapshot open item 6 (error on `snapshot`).

## Gaps

- **Messages no protocol uses:** none. All 16 kind-1 `$defs`, and kinds 2 and 3, have at least one step.
- **Messages with no step of their own** (reached only through another protocol): `hello`, `restore`, `hello_ack`,
  `restored`, `pong` appear on this edge in grid-restore only; core-restart reaches them by → grid-restore (step 21), and core-restart step 3
  is a socket close with no message.
- **`error` codes with no step:** `bad_request` and `unknown_cmd` appear in no protocol step (V2 and fixture
  `C4/vt-unknown-cmd` only). `unsupported_proto` has a step (grid-restore 3) whose next action is unspecified.
- **Unspecified in steps:** that the core sends `close` at an end (session-end-by-tsb 18, session-end-from-outside 17, spooled-end-finalize open item 1);
  `error` answering `prefix` (session-attach 11) or `snapshot` (grid-snapshot 3).
- **Steps naming a message the contract lacks:** none found. operator-alerts 8 names `vt_unavailable`, which is the core's
  alert and not a `vt-ipc` message.
- **Planner draft, corrected:** it listed core-restart for `hello` and `restore` (core-restart reaches them through grid-restore); it
  omitted `ping` and `snapshot` under operator-alerts 8, kind 2 under grid-restore and scroll-control, `error` under size-control, grid-snapshot, byte-relay and
  session-attach, and `applied` is scroll-control only (byte-relay only notes it).
