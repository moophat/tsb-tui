# byte-relay — a live session's terminal bytes, both ways

| | |
|---|---|
| **ID** | byte-relay |
| **Purpose** | Carry a live session's terminal bytes both ways — the keyboard holder's input to the target, the target's output to every attached client — recording input before it is sent. |
| **Status** | built (E0–E1); grows E2 (steps 9 and 10, and step 11's per-client output rule) |
| **Corpus clauses** | Contract §6, §10, §11, §13; immortality §4.1, §4.2, §5.2; lang-arch §4.1 (amendment 2026-09-14), §8.1, §8.3, §10.1; open_decisions §5; session model §9 E1–E3, §10; PTY §7.1; terminal-size §2 |
| **Seams** | C2, C3, C4, C8 |
| **Schemas** | `ws-frames.schema.json` (top: binary frames), `ws-frames.schema.json#/$defs/input_refused`; `ipc-data-frame.schema.json` (top; it has no `$defs`), `ipc-data-frame.schema.json#/x-binary-layout`; `ipc-control.schema.json#/$defs/input_refused`, `#/$defs/input_frame_max`, `#/$defs/buffer_bytes`; `vt-ipc.schema.json#/$defs/output_frame`, `#/$defs/error`, `vt-ipc.schema.json#/x-binary-layout` (kind 2); `ttyrec.schema.json#/$defs/data_frame`, `#/x-sidecar`, `#/x-derivation`; `session-record.schema.json#/properties/recording`, `policy.schema.json#/properties/recording` |

Schema references are relative to `contract/schemas/` at `contract-v0.3`; fixture names (`C3/…`) to `contract/fixtures/`.

## Path

input: SF-CO → CO-DK → CO-PW → PW-TG · output: PW-TG → CO-PW → CO-DK → CO-VT → (grid-snapshot) → SF-CO

## Steps

byte-relay is the steady state of a session whose handshake has reached `live` (→ reattach-handshake); the core offers a session
northbound only after that (`ipc-control.schema.json#/$defs/live`), so byte-relay always follows session-open or session-attach.

Words used below:

- **keyboard holder**: the one attached client holding the control token, the right to type (Contract §10). Who it
  is and how the token moves is typing-ownership's.
- **data socket**: a session's own unix socket between core and worker (`conn-{conn_id}/{socket}`). Every chunk on
  it is a **data frame**: a 24-byte envelope (version, flags whose bit 0 is the direction, length, `seq`, a
  nanosecond `timestamp`), then the payload, unframed (`ipc-data-frame.schema.json`; lang-arch §4.1 amendment).
- **seq**: a frame's number, per session and per direction, from 1. Output's is the worker's (immortality §4.1);
  input's is the core's own counter (`ipc-data-frame.schema.json` top).
- **ring**: the worker's bounded per-session output buffer, PTY sessions only (immortality §4.1, §4.3).
- **recording**: `<session_id>.ttyrec`, classic ttyrec, plus its sidecar `<session_id>.dir`, one code byte per
  frame: 0 output, 1 input (`ttyrec.schema.json` top, `#/x-sidecar`).
- **VT worker**: the process holding each `shell` session's grid, the server-side terminal state (Contract §11;
  open_decisions §1). Its socket carries length-prefixed messages; **kind 2** is an output frame
  (`vt-ipc.schema.json#/x-binary-layout`). **through_seq** is the last output seq a grid has applied.
- **event E1, event E2**: session model §9's events "a client typed" and "the target printed"; they move the session's clocks
  (§10).

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | WebSocket | binary: input bytes (conventions V15) | keyboard holder (→ typing-ownership) | the core takes the bytes in the order the client sent them (`ws-frames.schema.json` top) | not forwarded, never held: `ws-frames.schema.json#/$defs/input_refused` (reasons below) | the client that sent them |
| 2 | — | CO → CO | — | — | the core | splits the bytes into input frames no longer than the connection's recorded `input_frame_max`; gives each the next input `seq` and the core's send-time stamp | — | — |
| 3 | CO-DK | CO → DK | file | recording frame `ttyrec.schema.json#/$defs/data_frame`, sidecar code 1 | the core | when the session's recording is active, the frame is appended, `.ttyrec` then `.dir`, before step 4 | a failed write: unspecified (Open items 3) | — |
| 4 | CO-PW | CO → PW | unix socket (data socket) | input frame (`ipc-data-frame.schema.json`, flags bit 0 = 1: input) | the core | event E1 for the session: `t_in`, `client_touch` and `activity` move (session model §9 E1, §10; → session-countdown, → quiet-prompt) | `ipc-control.schema.json#/$defs/input_refused`: `ended`, `too_large`, `replaying` (below) | the core; the worker counts and logs it |
| 5 | PW-TG | PW → TG | SSH | the payload on the session's channel (`CHANNEL_DATA`, RFC 4254 §5.2) | worker | the target receives the bytes as typed (lang-arch §10.1) | an ended channel: step 4's `ended` | — |
| 6 | PW-TG | TG → PW | SSH | `CHANNEL_DATA` or `EXTENDED_DATA` on the session's channel | target | the worker stamps the bytes at read time, gives them the next output `seq`, and keeps them in the ring (PTY sessions); any SSH packet is also the connection's event E3 (→ link-liveness). For telnet: bytes after IAC processing only; CR NUL and 8-bit bytes unchanged; the bytes the target sends before `open_shell` are the session's first output frames [C3/telnet-payload-bytes; C3/telnet-output-from-connect, reference, owed by the E3 worker plan] | — | — |
| 7 | CO-PW | PW → CO | unix socket (data socket) | output frame (`ipc-data-frame.schema.json`, flags bit 0 = 0: output) | worker | event E2 for the session: `activity` moves and typed input counts as answered (session model §9 E2, §10; → session-countdown, → quiet-prompt) | — | — |
| 8 | CO-DK | CO → DK | file | recording frame `ttyrec.schema.json#/$defs/data_frame`, sidecar code 0 | the core | when the session's recording is active, the frame is appended with the worker's stamp (`#/x-derivation`) | a failed write: unspecified (Open items 3) | — |
| 9 | CO-VT | CO → VT | unix socket | kind 2: `vt-ipc.schema.json#/$defs/output_frame` · status **E2** | the core; VT worker | the session's grid applies the frame and its `through_seq` moves; `shell` sessions only (`vt-ipc.schema.json#/$defs/open`). **E2:** after step 8 has written the frame and its `.dir` code, when the recording is active, so no grid stands past the recording (vt-ipc top; continuity-state top) [owed] | `vt-ipc.schema.json#/$defs/error` `seq_jump`: the core MUST close that grid, open an empty one, feed it from the refused frame on and raise the stale flow with cause `grid_lost` (5.52) → stale-screen; a dead VT worker → grid-restore, operator-alerts | the core |
| 10 | — | CO → CO | — | → grid-snapshot · status **E2** | the core | when a snapshot cadence knob trips (immortality §5.2; conventions V7) | — | — |
| 11 | SF-CO | CO → SF | WebSocket | binary: output bytes (conventions V15) | every attached client, view-only included | each client receives the payload, envelope stripped, through its own output queue | a queue that will not drain: close 4011 → session-detach | every attached client |

Rules by step. Each MUST names its fixture; "owed" means no fixture grades it yet.

- **Step 1.**
  - The core MUST forward input only from the keyboard holder (Contract §10; terminal-size §2), the owner's
    take-over by typing being typing-ownership's. Graded by `C2/owner-takeover-by-typing`.
  - The core MUST answer every input it does not forward with `input_refused`, and MUST NOT hold it for later
    (`ws-frames.schema.json#/$defs/input_refused`; `ipc-control.schema.json#/$defs/hello`). Graded by
    `C2/stream-input-refused-unreachable`, `C2/stream-input-refused-replaying`, `C2/stale-flow`,
    `C2/owner-takeover-by-typing`.
  - A surface MAY prefilter operator input before it reaches the broker (Contract §13). The recording then holds
    what reached the broker.
- **Step 2.**
  - The core MUST split input at the connection's `input_frame_max` as it sent it on `connect` and keeps it in the
    connection record, never at a value its configuration holds after a restart
    (`ipc-control.schema.json#/$defs/input_frame_max`). Graded by `C3/input-refused-too-large`.
  - The core MUST number a session's input frames from 1, strictly increasing, and stamp each with its own clock at
    send time, never decreasing within the session's input (`ipc-data-frame.schema.json` top). Graded by
    `C3/seq-monotonic-per-session`, `C3/data-frame-envelope`, `C3/output-stamps-monotonic`,
    `C8/timestamps-monotonic`.
- **Step 3.**
  - With recording active, the core MUST record each input frame before it sends it, so a keystroke that reached
    the broker is in the recording even when a kill lands between the two (`ttyrec.schema.json#/x-sidecar`
    `reopen`; Contract §13). Graded by `C8/input-recorded-before-sent`.
  - The core MUST record the bytes verbatim — no byte masked, filtered or dropped, a typed password included
    (open_decisions §5) — appending to `.ttyrec` before `.dir` and deriving `sec`/`usec` by truncation
    (`#/x-sidecar` `write_order`, `#/x-derivation`). Graded by `C8/mixed-direction`, `C8/ns-derivation-in`,
    `C8/roundtrip`, `C8/ttyrec-shape`.
- **Step 4.**
  - The core MUST NOT send input before the handshake reaches `live` (→ reattach-handshake). Graded by
    `C3/input-refused-replaying`.
  - The worker MUST NOT deliver an input frame for a session whose channel has ended; it answers `input_refused`
    `ended`, naming the frame's `seq`. Graded by `C3/input-refused-ended`.
  - The worker MUST refuse a frame whose header declares more than `input_frame_max` from the header alone,
    answer `input_refused` `too_large`, and close that data peer; the session lives. Graded by
    `C3/input-refused-too-large`.
- **Step 5.** The worker MUST write the payload to the session's channel unchanged (Contract §6; lang-arch
  §10.1). Graded by `C2/attach-ok-bytes-roundtrip`.
- **Step 6.**
  - The worker MUST stamp output with its own clock at read time, never decreasing within the session's output
    (immortality §4.2). Graded by `C3/output-stamps-monotonic`, `C8/ns-derivation-out`.
  - The worker MUST number a session's output from 1, strictly increasing; a jump is allowed only for frames its
    ring dropped while no core peer held the socket (immortality §4.1, §4.3). Graded by
    `C3/seq-monotonic-per-session`.
  - The worker MUST emit an output data frame only for the session channel's data — never for `WINDOW_ADJUST`,
    channel EOF or CLOSE, or another channel (session model §9 E2) — and never an empty one. The empty frame is
    graded by `C3/data-frame-shape`; the source rule is owed.
  - The worker MUST split its reads so that no output payload exceeds the connection's `buffer_bytes`
    (`ipc-control.schema.json#/$defs/buffer_bytes`). Owed.
- **Step 7.** With a core peer on the data socket, the worker MUST NOT drop output or close a socket to shed a
  slow core: it stops reading the target or blocks its own write (`ipc-control.schema.json` top;
  `ipc-data-frame.schema.json` top). Graded by `C3/control-socket-stays-open`.
- **Step 8.** With recording active, the core MUST record every output frame it receives with the worker's stamp,
  never its own arrival time, verbatim (`ttyrec.schema.json` top, `#/x-derivation`; immortality §4.2;
  open_decisions §5). Graded by `C8/ns-derivation-out`, `C3/output-stamps-monotonic`, `C8/mixed-direction`,
  `C8/roundtrip`, `C8/timestamps-monotonic`.
- **Step 9** (status E2).
  - The core MUST feed every output frame of a `shell` session to its VT worker as a kind-2 message carrying the
    tapped frame byte-identical (`vt-ipc.schema.json` top, `#/x-binary-layout`). Graded by `C4/vt-frame-layout`
    (its graders, U2 and U4, are owed).
  - The VT worker MUST apply a session's frames in arrival order, ignore a frame at or below its `through_seq`,
    and refuse a seq jump without a gap frame with `error` `seq_jump`, leaving the grid unchanged (vt-ipc top,
    "Order and idempotence"). Graded by `C4/vt-feed-order` (its graders, U2 and U4, are owed). On that refusal the
    core MUST close the grid, open an empty one, feed it from the refused frame on and raise the stale flow with cause
    `grid_lost` (5.52). Graded by `C4/vt-grid-lost`; the core's side owed to U2.
  - Nothing returns per frame: `screen_change` feeds grid-snapshot and `applied` feeds scroll-control (vt-ipc `x-binary-layout`
    kinds). That a frame is applied before the core commits it is grid-snapshot's (vt-ipc top).
- **Step 11.**
  - The core MUST send each attached client the output bytes unchanged, in `seq` order, none repeated and none
    skipped, view-only clients included (PTY §7.1; Contract §11; `ws-frames.schema.json#/$defs/prefix`). Graded
    by `C2/attach-ok-bytes-roundtrip`, `C2/attach-prefix-mid-session`.
  - **Status E2:** each attached client MUST have its own output state, so a client that stops reading delays no other
    (C2 decisions 3 and 5, as `C2/per-client-output` states them). Graded by `C2/per-client-output` (required e2;
    its U2 grader is owed).

## Refusals and who is told

Client input the core does not forward (step 1) is answered on the WebSocket, to the client that sent it
(`ws-frames.schema.json#/$defs/input_refused`). The reasons, and the protocol that owns each trigger:

| reason | when | owner |
|---|---|---|
| `not_typist` | the sender does not hold the control token | → typing-ownership |
| `stale` | a stale-screen choice is pending: every client's input is refused until one is chosen | → stale-screen |
| `replaying` | the data path is being restored, from `hello` or a lone data-socket loss until `live` | → reattach-handshake, → data-socket-redial |
| `unreachable` | the session is `unreachable` (a v0.1 worker, or a peer whose version the core does not know) | → core-restart |

A receiver reads a reason it does not know as "input was refused" (conventions V5).

The worker's refusals of an input frame (step 4) go to the core only, named by the frame's `seq`, and the worker
counts and logs them (`ipc-control.schema.json#/$defs/input_refused`). No client is told by an `input_refused`
frame: C3's reasons are never C2's (`C2/stream-input-refused-unreachable`).

- `ended`: the frame crossed the session's end. It is in the recording (step 3 precedes step 4) and never reached
  the target. Clients learn of the end itself through → session-end-from-outside or → session-end-by-tsb.
- `too_large`: a core defect (step 2). The worker closes that data peer; the core re-dials and re-runs the
  handshake (→ data-socket-redial), and clients get data-socket-redial's `data_path` and `replaying` meanwhile.
- `replaying`: a core defect, input sent before `live` (→ reattach-handshake).

The VT worker's refusals (step 9) go to the core: on `seq_jump` the core MUST close the grid, open an empty one,
feed it from the refused frame on and raise the stale flow with cause `grid_lost` (→ stale-screen); a VT worker that
dies or misses heartbeats is grid-restore's, and its `vt_unavailable` alert is operator-alerts's.

A client whose output queue will not drain is detached with close 4011, the session living on
(`ws-frames.schema.json#/x-close-codes`; → session-detach). A session's end closes every client 4010 or 4012 (→ session-end-by-tsb,
→ session-end-from-outside).

## Security considerations

- **Credentials never cross byte-relay.** The worker injects vault credentials at the SSH protocol layer, below the byte
  stream TSB records (open_decisions §5); credentials travel only in `connect.auth` and, from 0.7, `auth_answer` on the control socket, never on a
  data socket (`ipc-control.schema.json` top). The worker MUST NOT write a credential to a data socket. Owed: no
  fixture grades a data socket for credentials (the artefact collection's key-material scan, contract README
  "Artefacts", is tooling).
- **A supplied login is not session input.** A login supplied on session create (`SessionCreateRequest.login`) goes into
  SSH's authentication at the protocol layer, below the byte stream, like a vault login; it never crosses byte-relay, is
  not the typed input of the next bullet, and is not in any data frame. When `policy.yaml` `recording.login_input` is true
  it is recorded in the session's recording, marked as login input (sidecar codes 6 and 7), and never as code-1 input
  (open_decisions §5; session-open steps 7 and 17; ttyrec `x-login-line`).
- **Typed secrets are recorded.** Operator-typed input, passwords included, is recorded verbatim (open_decisions
  §5; step 3). Every surface warns before attach when recording is active
  (`session-record.schema.json#/properties/recording`; → session-open, → session-attach). A deployment may switch recording off
  (`policy.schema.json#/properties/recording`); steps 3 and 8 then do not run.
- **Who may type** is typing-ownership's. byte-relay forwards only the keyboard holder's input (step 1).
- **A prefilter is not automatically a control.** A surface's prefilter (step 1) is enforceable where a surface
  backend holds the broker credential and proxies for the operator, and advisory where the operator's client holds
  its own (Contract §13). The core never parses, classifies or interprets a command (Contract §6, §13).
- **Local trust.** Data sockets and the VT socket accept only the TSB service uid (conventions V10), so the worker
  takes input only from an authenticated core connection (immortality §4.1).

## Open items

1. **Closed 2026-10-11 — queue A2 (plan §7 C-1), ruled by the operator (O1); it touched step 9.** The contract's reading stands: no grid
   mirror crosses C4 and no per-frame deltas; the grid lives in the VT worker. The corpus now says the same
   (open_decisions §1, immortality §5 and §5.1, Storage §8.6; vault f970731, re-exported in this release).
2. **Plan §7 C-5, already ruled** (5.53; D31, D7 agreed 2026-10-07; verifier C-5). The core answers a non-holder's
   input `not_typist`, and the owner's typing takes the keyboard (ws-frames `attached`, `input_refused`). Frontend
   §9's client-side suppression is stale vault text, and a surface's own filter is a guardrail (5.41; conventions
   V9). The consumer rule is typing-ownership's.
3. **Unspecified (not in plan §7):** what the core does when a recording write fails (steps 3, 8) — whether it
   still forwards the bytes, refuses them, or ends the session.
4. **Unspecified (not in plan §7):** where step 11 falls against steps 8 and 9 for one output frame. The contract fixes
   that input is recorded before it is sent (step 3), that an output frame is recorded before it is fed to the VT
   (step 9), and that the VT applies a frame before the core commits it (→ grid-snapshot).
5. **Unspecified (not in plan §7):** a zero-length binary input message. It is not event E1 (session model §9), no data
   frame can carry it (`ipc-data-frame.schema.json`: `length` ≥ 1), and no `input_refused` reason names it.
6. **Unspecified (not in plan §7):** which `input_refused` reason the core sends when more than one applies, for
   example a non-holder typing while a stale choice is pending.
7. **Approval items leaned on, not agreed.** Step 9 and its rules rest on D16 (kind-2 framing), D17 (C4 message
   names) and D18 (arrival order, `through_seq` idempotence, `seq_jump`), open in the approval page's southbound set
   (queue A8). Step 11's per-client output rule ("C2 decisions 3 and 5") rests on D3, open (queue A3), and D5, on
   neither of the approval page's lists (queue A11).

## Picture

pending

**Recording hold (tsb-program #141).** A sync failure serializes the input gate, writes and closure of this session's data peer. Input forwarded before the barrier was recorded before send; admitted but unforwarded input is refused, never reported delivered. While held, refuse input with `recording_unavailable`; the worker's ring and restart follow terminal-output-accounting §5.3. [owed]
