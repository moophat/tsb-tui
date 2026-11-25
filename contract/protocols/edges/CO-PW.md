# Edge CO-PW — core ↔ protocol worker

| | |
|---|---|
| **Edge** | `CO-PW`, between the core (CO) and a protocol worker (PW) |
| **Seam** | C3 |
| **Status** | built (E0–E1); growth is marked per row (E2, E3) |
| **Control transport** | one unix socket per connection, `/tmp/tsb/conn-{conn_id}/control.sock`. One JSON object per line, UTF-8, newline-terminated. The core sends commands (`cmd`), the worker sends events (`event`) |
| **Data transport** | one unix socket per session, `/tmp/tsb/conn-{conn_id}/{socket}` (`socket` is the worker's file name, sent in `channel_opened`). Each chunk is a 24-byte envelope plus payload, in both directions |
| **Schemas** | `contract/schemas/ipc-control.schema.json`, `contract/schemas/ipc-data-frame.schema.json`, at `contract-v0.3` |
| **Fixtures** | `contract/fixtures/C3/` (graded names are cited in the protocol documents, not repeated here) |

This page is an index. It lists what crosses the edge and which protocols use it. Behaviour stays in the protocol
documents and in [`../conventions.md`](../conventions.md).

## Transport rules

| Rule | Where it is stated |
|---|---|
| **Trust.** SO_PEERCRED on the control socket and every data socket: a peer is accepted only with the TSB service uid. No token, no TLS, no handshake of identity. | conventions V10; ipc-control top; ipc-data-frame top; session-open step 6, reattach-handshake step 1, core-restart steps 12 and 15 |
| **Only dialler.** The worker binds, the core dials and never accepts. The core dials the data socket before it sends `hello`. | conventions V10; reattach-handshake step 1; session-open step 6 |
| **Versioning.** `proto_version` rides on `hello` and `hello_ack`. On `hello` the core offers the worker's version when it knows and speaks it ("0.2" toward a v0.2 worker, which then gets exactly v0.2's messages), and otherwise its own ("0.8"). A new worker's version comes from `worker_created`, before its manifest need exist. Absent means v0.1. The core speaks N and N-1 at least: 0.8, 0.7, 0.6, 0.5, 0.4, 0.3 and 0.2 at this tag, and sends a v0.1 worker no `hello`, `ping`, `timers` or `release`. A telnet `connect` goes only to a peer at 0.5 or later, `open_shell`'s `open_timeout` only to a peer at 0.6 or later, the device-login messages (a prompt-mode `connect` with `login_timeout`, `hostkey_answer`, `auth_answer`) only to a peer at 0.7 or later, and the terminal messages (`seal_output`, `release_accounted`, `output_sealed`, and the `hello_ack` `output_seal` summary and `connect`/`timers` `output_drain_timeout` key) only to a peer at 0.8 or later. The data frame carries a version byte, and a receiver refuses a version it does not speak. | conventions V1; ipc-control top; contract README "Senders and receivers"; worker-manifest top; `supervisor#/$defs/worker_created`; `C3/n1-v0.2-worker-positive`; `C3/n1-v0.3-worker-positive`; `C3/n1-v0.4-worker-positive`; `C3/n1-v0.5-worker-positive`; `C3/open-timeout-version-gate`; `C3/n1-v0.6-worker-positive`; `C3/device-login-version-gate`; `C3/terminal-disposal-version-gate`; terminal-output-accounting §3.1; orphan-session (v0.1 worker paragraph); core-restart steps 15, 23 |
| **Unknown command.** The worker answers `error` `unknown_cmd` and keeps the connection. A malformed line is `bad_request`. | conventions V2, V4; ipc-control top |
| **Unknown event, field, or `cause` / `by` value.** The core ignores an unknown event or field. An unknown `cause` is read as `transport`, an unknown `by` as `target`. | conventions V2, V5; ipc-control top |
| **Control socket close.** A worker closes a connection's control socket only after that connection's `connection_lost`, or by exiting. A control-socket EOF with no `connection_lost` makes the core dial once; only a refused or missing socket is E9. A core that closes without `disconnect` ends nothing. | ipc-control top ("The control socket"); session-end-from-outside steps 6 and 7; orphan-session step 1 |
| **Framing and endianness.** Big-endian, 24-byte header: version u8, flags u8 (bit 0 direction, bit 1 gap), stream u16 (0), length u32, seq u64, timestamp u64 (ns, UTC). Payload is unframed terminal bytes. | conventions V12; ipc-data-frame `x-binary-layout`; byte-relay steps 4 and 7 |
| **Sizes.** An output payload is at most `buffer_bytes`; an input payload at most `input_frame_max`. The worker refuses a longer input frame from its header alone. A retained ended session keeps its memory within `buffer_bytes`, keeps no worker running, and lasts at most as long as its connection. | ipc-data-frame top; ipc-control `buffer_bytes`, `input_frame_max`, `release`; data-socket-redial step 1 |
| **Credentials.** Only in `connect.auth` and, from 0.7, `auth_answer`, never in argv, the environment, a manifest, the spool, `create_worker` or a data socket. | conventions V10; ipc-control top, `auth` and `auth_answer`; session-open step 7 and its Security considerations; device-login Security considerations |
| **Resizes are never coalesced.** Each `resize` reaches the target as its own window-change, in order. | ipc-control `resize`; size-control step 7 |
| **Data path loss is not an end.** A data socket that ends alone is re-dialled once and re-handshaken; after an end the worker writes its last output frame, then closes the peer. | ipc-data-frame top ("The data path"); data-socket-redial steps 2 to 7; session-end-from-outside step 12; session-end-by-tsb step 12 |
| **Reattach order.** `hello` → `hello_ack` → `replay` → `replay_done` → `live` (or `release` for an ended session). Input only after `replay_done`. After a restart, toward a worker whose version the core speaks, the first `ping` on a control socket goes before its first `hello` (core-restart step 22). | ipc-control top ("Reattach"); reattach-handshake |

## Control commands (core → worker): 17 `$defs`

`$def` is in `ipc-control.schema.json`. Steps are `PROTOCOL step`.

| Command | Used by (step) | Status |
|---|---|---|
| `connect` (embeds `auth`, `timer_values`, `buffer_bytes`, `input_frame_max`) | session-open 7; connection-linger 1; hung-worker 1; link-liveness 1 (timer values carried) | built (E0–E1); `keepalive_probe_interval` E2 (link-liveness 1); the telnet branch, with no `user` and no `auth`, is this release's [C3/control-message-shape-v0.5] |
| `open_shell` | session-open 12; size-control 17; connection-linger 3 (reuse) | built (E0–E1); the clamped size E2 (session-open 12); `open_timeout`, the bound on the open, from `proto_version` 0.6 (session-open 12–14) [C3/open-timeout-shape; C3/open-timeout-expired; C3/open-timeout-version-gate] |
| `open_sftp` | none at v0.3. file-transfer names it (E5, no v1 step yet); fixture `C3/open-sftp` | published only; E5 |
| `open_subsystem` | none. No v1 protocol names it; fixture `C3/open-subsystem` | published only |
| `resize` | size-control 7; session-attach 19 (wiggle); stale-screen 16 (wiggle) | built (E0); wiggle E2 |
| `close_channel` | session-end-by-tsb 8; data-socket-redial 12 (failed re-dial); session-open 14 (refusal column: a `channel_opened` after the core's wait expired) | built (E0–E1) |
| `disconnect` | connection-linger 12 (the core's own); a listed session the core had not ended: connection-linger 16 → session-end-from-outside 5 (DEFAULT 15) | built (E1) |
| `hello` | reattach-handshake 2 | built (E1) |
| `replay` | reattach-handshake 6 | built (E1) |
| `live` | reattach-handshake 13 | built (E1) |
| `release` | reattach-handshake 16; session-end-by-tsb 16; session-end-from-outside 15; spooled-end-finalize 7 (via reattach-handshake); session-end-by-tsb 23b; session-open 14 (refusal column: a session the core gave up before offering it, nothing recorded) | built (E1) |
| `timers` | config-reload 4; core-restart 13; link-liveness 2; connection-linger 1 | built (E1); the `config.yaml` reload trigger E3 (config-reload) |
| `hostkey_answer` | device-login 13 | E4 (Release B, 0.7) |
| `auth_answer` | device-login 16 | E4 (Release B, 0.7) |
| `ping` | hung-worker 2; orphan-session 2 (its absence) and 9 (its return); core-restart 22 (before the first `hello`) | built (E1) |
| `seal_output` | terminal-output-accounting 9A 3; 9B 3; 9C 2 (the running core's bounded seal) | published e4 (this release's, C3 0.8); U2/U3 provider tests owed [C3/control-message-shape-v0.8; C3/terminal-seal-idempotent] |
| `release_accounted` | terminal-output-accounting 9A 5; 9B 3 (as 9A) | published e4 (this release's, C3 0.8); U2/U3 provider tests owed [C3/terminal-disposal-version-gate; C3/terminal-disposal-crash-after-drop] |

## Control events (worker → core): 15 `$defs`

| Event | Used by (step) | Status |
|---|---|---|
| `hostkey` | session-open 10 (never on a prompt-mode connect) | built (E0–E1) |
| `hostkey_ask` | device-login 12 | E4 (Release B, 0.7) |
| `auth_prompt` | device-login 15 | E4 (Release B, 0.7) |
| `connected` | session-open 11 | built (E0–E1) |
| `auth_failed` | session-open 11 | built (E0–E1) |
| `channel_opened` | session-open 14 | built (E0–E1); `session_type` `shell` only at this tag; `resizable` is this release's, absent means true [C3/control-message-shape-v0.5] |
| `channel_closed` | session-end-by-tsb 11 (`by: core`); session-end-from-outside 4 (`by: target`); data-socket-redial 4 (via session-end-from-outside); session-end-by-tsb 23a (`by: worker`); session-open 14 (refusal column: `by: core`, answering the close of a late `channel_opened`) | built (E0–E1) |
| `connection_lost` | session-end-from-outside 4; connection-linger 6 (`linger`) and 14 (`disconnect`); link-liveness 14 (`keepalive_timeout`); quiet-prompt 5 (via link-liveness) | built (E1); `single_session` is this release's, sent once the session's end has reached the core [C3/telnet-one-session] |
| `error` | session-open 11 (`hostkey_changed`) and 14; reattach-handshake 4 (refusal column: `unsupported_proto`, `unknown_cmd`, `no_such_session`, `no_data_peer`); core-restart 23 (refusal column: `unsupported_proto`, `unknown_cmd`, `no_such_session`); config-reload 4 (refusal column: `bad_request`); terminal-output-accounting §3.1 and 9B 4 (`terminal_busy`, answering a hello offering 0.8 while the session is sealing or its core-requested end is in flight; `unsupported_proto`, answering a hello offering a version below 0.8 then, tsb-program #138); conventions V2, V4 (`unknown_cmd`, `bad_request`, `internal`) | built (E0–E1); `terminal_busy` is published e4 (this release's, C3 0.8) [C3/terminal-hello-busy]; the below-0.8 answer is published e4 [C3/terminal-legacy-offer-mid-seal] |
| `hello_ack` | reattach-handshake 4 | built (E1) |
| `replay_done` | reattach-handshake 9 | built (E1) |
| `pong` | hung-worker 3 | built (E1) |
| `link_state` | link-liveness 8, 9, 10; session-countdown 4, 5, 15; core-restart 14 (via link-liveness 10) | built (E1) |
| `input_refused` | byte-relay 4 (refusal column: `ended`, `too_large`, `replaying`); session-end-by-tsb 11 (refusal column: `ended`); session-end-from-outside (Refusals: `ended`); stale-screen 14 (refusal column, → byte-relay 4); data-socket-redial 1 (`too_large`); reattach-handshake 9 (`replaying`, a core defect) | built (E1) |
| `output_sealed` | terminal-output-accounting 9A 4 (the fresh data dial and hello follow it); 9B 3 (as 9A); 9C 2; §3.1 (a peer states 0.8 by a parsed hello, refused hellos included, or by sending seal_output without a hello; a replacement control peer starts with no statement) | published e4 (this release's, C3 0.8); U2/U3 provider tests owed [C3/control-message-shape-v0.8; C3/terminal-seal-idempotent; C3/terminal-seal-output-states-version] |

## Data frames and socket events

Frames are in `ipc-data-frame.schema.json`. The dials and EOFs are not messages; they are listed because protocols
give them steps on this edge.

| Item | Direction | Used by (step) | Status |
|---|---|---|---|
| Input frame (flags bit 0 = 1; `seq` is the core's own counter; `ctrl_l` is payload `0x0C`) | CO → PW | byte-relay 4; session-countdown 1; quiet-prompt 2; stale-screen 14 (`ctrl_l`) | built (E0); `ctrl_l` E2 |
| Output frame (flags bit 0 = 0; `seq` is worker-assigned, from 1) | PW → CO | byte-relay 7; session-countdown 2; quiet-prompt 3; reattach-handshake 8 (replayed); session-end-from-outside 12; session-end-by-tsb 12 | built (E0) |
| Gap frame (output, bit 1 set, length 0, `seq` = `first_buffered_seq` − 1) | PW → CO | reattach-handshake 7 (then stale-screen) | built (E1) |
| Dial of `control.sock` | CO → PW | session-open 6; session-end-from-outside 7 (the one re-dial); core-restart 12 | built |
| Dial of a data socket | CO → PW | reattach-handshake 1; core-restart 15; data-socket-redial 7 (via reattach-handshake) | built |
| Data peer EOF, after an end (last output frame first) | PW → CO | session-end-by-tsb 12; session-end-from-outside 12 (then `release`) | built (E1) |
| Data peer EOF or error, no end behind it | PW → CO | data-socket-redial 2 | built (E1) |
| Control-socket EOF with no `connection_lost` | PW → CO | session-end-from-outside 6 (then the one re-dial, step 7) | built (E1) |
| Core's own control and data ends close; no message | CO → PW | orphan-session 1; core-restart 2 | built (E1) |

## Gaps

Counts: 17 commands and 15 events (the contract's `command` and `event` lists), 3 data frame kinds (input, output,
gap).

1. **No v0.3 protocol uses `open_sftp` or `open_subsystem`.** `open_sftp` maps to file-transfer (E5); file-transfer's schema line
   names `open_sftp` only. `open_subsystem` has no protocol, v1 or later, so it has no owner on paper. The two
   fixtures `C3/open-sftp` and `C3/open-subsystem` are published-only.
2. **No step names a message the contract lacks.** Every message in a CO-PW step above resolves to a `$def` or to
   the data frame; `ctrl_l` is a payload byte, not a message.
3. **Two error codes have no step, only prose.** `not_supported` and `internal` (session-open open items; conventions
   V2, V4). Every other code sits in a step's refusal column (the `error` row above).
4. **Planner draft corrections** (plan §3 `edges/CO-PW`): the draft's `connect` row omitted connection-linger, hung-worker and link-liveness;
   `open_shell` omitted size-control 17 and connection-linger 3; `timers` omitted link-liveness 2 and connection-linger 1; `connection_lost` omitted
   quiet-prompt 5; `link_state` omitted core-restart 14; `error` listed only conventions and omitted session-open and reattach-handshake; the data-peer
   EOF row omitted data-socket-redial 2; the dials, the core's own EOFs (orphan-session 1, core-restart 2) and the data-frame consumers
   (session-countdown, quiet-prompt, session-end-from-outside, session-end-by-tsb) were missing.
5. **`timers` at E3.** Closed by this release: config-reload steps 1–7 are this release's, and the reload trigger is graded by `C3/config-reload-timers` (reference; its U2 test and the U11 rig are owed).
6. **`error` after `timers`.** The frozen v0.2 worker refuses an unknown `timers` key with `bad_request`
   (conventions V2 known breach; config-reload open item 4). The core's next step is unspecified.

## Terminal output accounting

| Message or record | Owner | Contract and grading |
|---|---|---|
| `seal_output`, `output_sealed` | terminal-output-accounting §§3/9 | C3 0.8 PTY scope (terminal-output-accounting §1); session-local immutable producer result, terminal_busy hello races, fresh peer replay [C3/control-message-shape-v0.8; C3/terminal-seal-idempotent] |
| `release_accounted` | terminal-output-accounting §§3/8 | C3 0.8 PTY scope (terminal-output-accounting §1); core asserts durable seal/output accounting, absent duplicate silent success; no ack [C3/terminal-disposal-version-gate] |
| `hello_ack.output_seal`, `timers.output_drain_timeout` | terminal-output-accounting §§2/3 | optional additive fields, sent only to capable peers [C5/config-output-drain-timeout] |

**Recording hold (tsb-program #141).** Close only the held session's data peer, suppress redial and preserve shared control and heartbeats. U3 must prove continued ring reading before reliance (terminal-output-accounting §5.3). [owed]
