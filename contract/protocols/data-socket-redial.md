# data-socket-redial — re-dial a session's data socket that ended alone

| | |
|---|---|
| **ID** | data-socket-redial |
| **Purpose** | When a session's data socket ends alone, re-dial it once and re-run the handshake, refusing input meanwhile and telling clients the data path was lost and restored. |
| **Status** | built (E1) |
| **Calls** | reattach-handshake (the re-dial and its handshake), session-end-from-outside (an end inside the wait; a failed re-dial), byte-relay (after `live`). Entered from byte-relay when the worker refuses an input frame `too_large` (step 1). data-socket-redial owns the trigger, the wait, the clients' `data_path` and the failure; reattach-handshake owns the exchange; session-end-from-outside owns the end (plan §2.1). |
| **Corpus clauses** | immortality §4.1, §4.3, §6.1, §6.3 (input authority only after the replay); session model §9 E9, E10, §12, §13; Contract §15.1. The data-path rule itself is the operator's, 2026-09-27 and 2026-09-29, as the contract records it (ipc-data-frame top; ws-frames `data_path`), not a corpus clause. |
| **Seams** | C3, C2, C5 (the spool, through session-end-from-outside) |
| **Schemas** | `ipc-data-frame.schema.json` (top: "The data path"); `ws-frames.schema.json#/$defs/data_path`, `#/$defs/input_refused`; `ipc-control.schema.json#/$defs/input_refused`, `#/$defs/close_channel`, `#/$defs/hello`, `#/$defs/channel_closed`, `#/$defs/connection_lost`; `spool-entry.schema.json` |

Schema paths are relative to `contract/schemas/` at `contract-v0.3`; fixtures are `<seam>/<name>` under
`contract/fixtures/`. Corpus files, under `srs/`: immortality = `immortality-mechanism.md`, session
model = `session-model-and-lifecycle.md`, Contract = `architecture-contract.md`. Socket and file names are relative
to TSB's node-local runtime directory (ipc-control top).

**Words used here.**

- **data peer**: the core's end of the session's data socket `conn-{conn_id}/{socket}` (ipc-data-frame top).
- **ends alone**: the data peer reaches EOF or errors with no end of the session behind it. The EOF a worker sends
  after an end is not this: it tells the core its recording holds the output (ipc-data-frame top; session-end-from-outside step 12).
- **control-side end**: an end reported on the connection's control socket `conn-{conn_id}/control.sock`:
  `channel_closed` or `connection_lost` naming the session, or that socket's own EOF (ipc-control top).
- **the wait**: the moment the core gives a control-side end to arrive before it calls a loss alone.
- **keyboard holder**: the one attached client that holds the control token and may type (Contract §10).

## Path

CO-PW → SF-CO → CO-PW (an end inside the wait: session-end-from-outside) → SF-CO → (reattach-handshake) → SF-CO → (byte-relay);
re-dial failed: CO-FS → (session-end-from-outside) → CO-PW

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | PW → CO | unix socket `control.sock` | `input_refused` `too_large`, only when this is the trigger | worker | The worker MUST refuse an input frame whose header declares more than the connection's `input_frame_max` from the header alone, answer this, and then close that data peer; the session lives (`ipc-control#/$defs/input_refused`; C3/input-refused-too-large). It marks a core defect: the core splits input at that value (→ byte-relay). | — | the core; the worker counts and logs it |
| 2 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | EOF or error on the data peer, no end behind it (no message) | — | Not an end (ipc-data-frame top, "The data path"): the core MUST NOT end the session on it, and the worker keeps it (C3/redial-replay: `hello_ack` `ended: false` after the loss). A PTY session's worker keeps reading the target into its ring (C3/redial-replay); a non-PTY session's stops reading its channel while no core peer holds the socket (ipc-control top; C3/reattach-non-pty, published e1 only: required green with the tag that opens a non-PTY session, since SFTP and NETCONF answer `not_supported` until a later release defines the file surface). The input gate of step 3 shuts here, at the loss, not at `hello` (`hello`). | — | — |
| 3 | SF-CO | SF → CO | WebSocket | binary: input bytes, at any time from step 2 until reattach-handshake's `live` | keyboard holder (a non-holder's input is typing-ownership's) | The core MUST NOT forward it to the worker and MUST NOT hold it for later (ipc-data-frame top; `hello`; C2/stream-input-refused-replaying, whose named core test covers `hello` to `live`; the window from the loss to `hello` is owed). | `input_refused` `replaying` (C2/stream-input-refused-replaying) | the sending client |
| 4 | CO-PW | PW → CO | unix socket `control.sock` | → session-end-from-outside: `channel_closed`, `connection_lost`, or the control socket's EOF, inside the wait | worker | The loss was an end's EOF: data-socket-redial stops here and the core MUST NOT send `data_path` `lost` (`data_path`: "a normal end never sends a spurious lost"; C2/data-path-marker). `channel_closed` `by: core`, answering the core's own `close_channel`, is session-end-by-tsb's; a control-socket EOF is session-end-from-outside's single confirming dial (its steps 6–7). | — | as session-end-from-outside or session-end-by-tsb says |
| 5 | — | CO → CO | — | decides the loss is alone | the core | A recording hold suppresses redial until restart (§5.3) [owed]. Otherwise the core MUST wait for a control-side end before deciding (ipc-data-frame top; C2/data-path-marker, through the core test it names). How long it waits: unspecified (not in plan §7); no knob names it. | — | — |
| 6 | SF-CO | CO → SF | WebSocket | `data_path` `lost` | the core | Outside a recording hold, the core MUST send it only once the wait has passed with no control-side end; a recording hold sends it at the hold (`data_path`; C2/data-path-marker). It says input may not reach the target and output may not come back. | — | every client attached to the session |
| 7 | — | CO → CO | — | → reattach-handshake | the core | The re-dial: reattach-handshake's step 1 dials `conn-{conn_id}/{socket}` again through the same trusted dial, and the worker takes the newer core peer; reattach-handshake's handshake then replays what the dead socket lost (ipc-data-frame top). The core MUST re-dial once for this loss (ipc-data-frame top) [owed: C3/redial-replay, through tsbc `control-reattach`, grades that a re-dial's handshake reaches `live` in order, not the count]. **E2:** the core first realigns the commit point to the recording's end (grid-snapshot rule 10), so its `hello` carries `snapshot_seq`, the value a core restart would send, and the worker replays only what the recording lacks (ipc-control `hello`; reattach-handshake step 2) [C3/redial-replay]. The realign waits for a VT snapshot at most `snapshot.wait_sec`; then, or at once when the VT worker cannot give one, the core gives the grid up (record-only realign, `grid_lost`) and re-dials, `data_path` `lost` standing meanwhile (grid-snapshot rules 10, 12) [owed]. | reattach-handshake's: `unsupported_proto` or `unknown_cmd` → unreachable; `no_such_session` → spool, then session-end-from-outside; `no_data_peer` → Open items. A dial refused, missing or failing SO_PEERCRED → step 10 | as reattach-handshake says |
| 8 | SF-CO | CO → SF | WebSocket | `data_path` `restored` | the core | The core MUST send it when the re-dial's handshake reaches `live` (reattach-handshake step 13; `data_path`; C2/data-path-marker). The gate of step 3 opens at the same `live`. A handshake that ends in `release` (`hello_ack` `ended: true`) never reaches `live`, so no `restored` is sent; reattach-handshake step 15 takes the ended session (→ spooled-end-finalize) and closes every attached client, 4012 for an end from outside TSB (ipc-control top), 4010 for `killed` (spooled-end-finalize step 9). Whether that end is audited as a spooled end or live (→ session-end-from-outside): Open items. | — | every client attached to the session |
| 9 | — | CO → CO | — | → byte-relay | the core | byte-relay carries the session's bytes from here. | — | — |
| 10 | CO-FS | FS → CO | a file, `spool/{session_id}.json` | → session-end-from-outside: `spool-entry.schema.json`, read | the core | The re-dial failed: the socket refused, is missing, or the peer fails SO_PEERCRED (session model §9 E10, "at rediscovery or reattach"). The core MUST re-read the spool for the session before it decides E10 (ipc-data-frame top; session-end-from-outside step 9) [owed: C3/redial-replay states it; its case has no failed re-dial]. An entry there is the session's end (→ spooled-end-finalize). Unlike a returning core's, this core has clients attached: every one MUST be closed as its end state says, 4012 for `terminated`, an end from outside TSB (ipc-control top: "Every such end closes attached clients 4012"), 4010 for `killed` (ws-frames `x-close-codes`), as spooled-end-finalize step 9 writes it [owed]. | — | — |
| 11 | — | CO → CO | — | → session-end-from-outside | the core | No entry: the session ends E10, `terminated` / `worker_lost` (ipc-data-frame top; session model §12, §13), "so no session is left with no data path and no end" (ipc-data-frame top) and "a lost path is never left standing" (C2/data-path-marker's description) [owed: no fixture's case fails a re-dial]. session-end-from-outside records, audits and closes every attached client 4012; `restored` is never sent. | — | every attached client, by 4012 (session-end-from-outside) |
| 12 | CO-PW | CO → PW | unix socket `control.sock` | `close_channel` | the core | Only when the connection's control socket still answers. After the E10 decision the core MUST send it, so the worker does not keep a channel no core records (ipc-data-frame top; `close_channel`) [owed]. What follows the worker's `channel_closed` `by: core` answer — `release`, or retention until the connection closes (`release`) — is unspecified (not in plan §7). | — | the worker |

## Refusals and who is told

- **Input during the loss.** One gate, two ends, as in reattach-handshake: the core refuses client input on C2 from the loss
  (step 2) until `live` (step 3), and the worker delivers no input frame before `replay_done` (reattach-handshake step 9). The
  core holds nothing for later.
- **An input frame lost in flight.** The core records an input frame before it sends it (ttyrec `x-sidecar`, input
  rule; byte-relay), so a frame sent into a peer that died, or the frame refused `too_large`, is in the recording and
  never reached the target. tsbc `recordings-match-frames-in` allows one such frame per lone re-dial (CHANGELOG
  contract-v0.2, "Runner"). No C2 message names it: C3's `input_refused` reasons are never C2's (byte-relay). Clients learn only
  `data_path` `lost`.
- **A silent target is not a lost data path.** `data_path` reports what the core observed of its socket to the
  worker; input delivered to a target that does not answer is quiet-prompt's (`data_path`; C2/data-path-marker).
- **Not data-socket-redial.** A VT worker's death breaks no data socket, so it runs no handshake and sends no
  `data_path` (grid-restore step 11; vt-ipc top).

## Security considerations

- **Who may type.** No byte reaches the target from the loss until `live`: keystrokes typed while the screen still
  replays are how you type into the wrong prompt (immortality §6.3, as `hello` applies it to the local path). Input
  refused here is gone; nothing sends it later.
- **The host boundary.** The re-dial is the same trusted dial as every other: SO_PEERCRED alone, no claim token, no
  secret (immortality §6.1, LOCKED; conventions V10). A peer that fails it is E10, never a live worker (step 10).
  `data_path` and `input_refused` carry no pid, path or host detail (conventions V11).

## Open items

- **The wait.** No knob, range or bound for it (step 5). Unspecified (not in plan §7).
- **After `no_data_peer` on a re-dial.** "Once for each such loss" rules out a second dial for the same loss; the
  core's next step — unreachable, or E10 — is unspecified (reattach-handshake open item; not in plan §7).
- **Successive losses.** A loss of the re-dialled peer is a new loss with its own one re-dial ("once for each such
  loss"). No bound on successive losses, and no rule on a second `lost` before `restored`. Unspecified (not in
  plan §7).
- **What a surface shows.** The contract says what `lost` and `restored` mean, not what a surface does with them;
  no corpus clause gives a surface freedom here, so this document grants none. Unspecified (not in plan §7).
- **OPEN — who audits an end the re-dial finds** (not in plan §7; reattach-handshake's open item, carried). An end that
  lands between the loss and the re-dial's `hello_ack` `ended: true` happens while the core is attached on the
  control socket ("A core that is attached when the end happens audits it live", spool-entry top), yet its output
  waits for the replay, after which `release` follows (ipc-control `release`). Whether its end marker and audit event
  are session-end-from-outside's live end or spooled-end-finalize's is not said (step 8).

## Picture

pending

**Recording hold (tsb-program #141).** When a recording sync failure holds a session, the core closes that session's data peer and suppresses its redial until the next core start; it keeps the shared control peer and heartbeats. The held-ring behavior needs a U3 provider test before reliance; otherwise a C3 change is required (terminal-output-accounting §5.3). [owed]
