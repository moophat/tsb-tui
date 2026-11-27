# orphan-session — while the core is gone

| | |
|---|---|
| **ID** | orphan-session |
| **Purpose** | While the core is gone, the protocol worker keeps each session `orphan-pending` and on `hold`, and runs its timers from the last values it received, until the core's heartbeat returns; an end made meanwhile goes to the end protocol that owns it. |
| **Status** | built (E1) |
| **Entered from** | core-restart step 4 (the core stops or dies), session-countdown step 13 (the countdown while the core is gone). |
| **Hands to** | session-end-by-tsb (a timer's end: `orphan_timeout`, `idle`, `unattended`, spool included), session-end-from-outside (an end from outside, spool included), link-liveness (`link_timeout`), core-restart (the restart itself), hung-worker (the `pong`). |
| **Corpus clauses** | immortality §4.1–§4.3, §6.1, §7, §8; session model §9 E6, §11 (`core_heartbeat`, `orphan_timeout`, `idle`, `unattended`), §14, §15; Storage §8.1; Contract §4 |
| **Seams** | C3, C6 |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ipc-control.schema.json` (top: "The control socket"), `#/$defs/ping`, `#/$defs/pong`, `#/$defs/timers`, `#/$defs/timer_values`; `ipc-data-frame.schema.json` (top: the gap frame); `continuity-state.schema.json` (top); `common.schema.json#/$defs/session_state` |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files, under
`srs/`: immortality = `immortality-mechanism.md`, session model = `session-model-and-lifecycle.md`,
Storage = `storage-and-discovery-architecture.md`, Contract = `architecture-contract.md`. Socket and file names are
relative to TSB's node-local runtime directory.

orphan-session applies to workers at `proto_version` 0.2 or later. The core sends a v0.1 worker no `ping`, `timers` or
`release`, so a session there has no orphan bound, and the core reuses no connection on one for that reason
(ipc-control top).

Who ended a session and whether the core was present are two different axes (the operator, 2026-10-07).
orphan-session owns only the orphan state. An end made while the core is gone is its end protocol's, in that
protocol's branch "while the core is gone": session-end-by-tsb for a timer's end (step 7), session-end-from-outside
for an end from outside (step 6).

**Words used here.**

- **The core is gone**: no core serves this worker: it died, it is restarting, or it is stuck. The worker learns it
  only from missing pings (step 2).
- **`core_heartbeat`**: the worker's bound on a silent core, `heartbeat_interval` × `heartbeat_missed`
  (ipc-control `timer_values`; session model §11).
- **`orphan-pending`**: the state of every session in a worker whose `core_heartbeat` fired, until the core's
  heartbeat returns (immortality §7, §8). It belongs to the worker alone (step 3).
- **`hold`**: the default orphan policy: the session keeps its PTY and keeps buffering (immortality §8).

## Path

core gone: CO-PW → PW-TG; an end meanwhile: PW-TG (session-end-from-outside) | a timer (session-end-by-tsb);
core back: (core-restart) → CO-PW

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock` | end of file, with no `disconnect` before it | the core, gone | The worker MUST end nothing: a core that closes a control socket without `disconnect` is the graceful restart, and a worker that tidied up there would end every session on every deploy (ipc-control top, "The control socket") [C3/control-socket-stays-open]. | — | — |
| 2 | CO-PW | CO → PW | unix socket `control.sock` | `ping`, missing | the core, gone | The worker MUST run `core_heartbeat` per worker: a `ping` on any of its connections resets it, and it fires once no `ping` has come for `heartbeat_interval` × `heartbeat_missed` (ipc-control `ping`, `timer_values`; session model §9 E6, §11) [owed: C3/heartbeat-ping-pong grades the pings and pongs, not the worker's timer]. A stuck core whose sockets stay open is caught here, never by a hang-up (ipc-control top). The pair is core-wide, already ruled (Open item 2): every connection carries the same pair, and a worker uses the latest it received on any of them (`timer_values`). | — | — |
| 3 | — | PW → PW | — | `core_heartbeat` fires | worker | Every session in the worker enters `orphan-pending` (immortality §8; session model §11). The worker MUST end nothing here (session model §11: "Ends nothing") [owed]. `orphan-pending` is on no wire and in no record: no message carries it and no core persists it (common `session_state`; continuity-state top; DEFAULT O5, which the CHANGELOG calls a departure from immortality §7's wording) [C6/state-vocabulary; C1/session-record-shape-v0.2]. | — | nobody: no core is there |
| 4 | PW-TG | TG → PW | SSH | `CHANNEL_DATA` / `EXTENDED_DATA` on a session's channel (RFC 4254 §5.2); not contract | target | `hold`: the session keeps its channel and its PTY. For a PTY session with no core peer on its data socket, the worker MUST keep reading into the ring, each chunk with its seq and its read-time stamp, dropping the oldest on overflow and counting on (immortality §4.1–§4.3, §8; ipc-data-frame top) [C3/gap-frame; C3/seq-continuity-across-restart]. A non-PTY session's channel the worker MUST NOT read while no core peer holds its data socket (ipc-control top) [owed: C3/reattach-non-pty states it, marked published e1 and never required-green]. No input arrives: with no core there is no client (immortality §4.1). | — | — |
| 5 | — | PW → PW | — | runs `idle`, `unattended` and `orphan_timeout` | worker | From the last timer values it received for the session's connection, `connect`'s `timer_values` (session-open) or the latest `timers` (config-reload, core-restart), which it keeps while the core is away (ipc-control `timers`; session model §15; Storage §8.1) [the sending: C3/connect-carries-timers; the keeping: owed]. `orphan_timeout` MUST be armed while the session is `orphan-pending` and fire once its value has passed since `core_heartbeat` fired (session model §11) [the end it makes: C6/event-spooled-end]. The worker MUST run `idle` and `unattended` only while the core is gone (ipc-control `timer_values`; session model §11, "Evaluated by") [owed]. The clocks it runs them from: Open item 1. `link_timeout` runs as ever (→ link-liveness). | — | — |
| 6 | PW-TG | TG → PW | SSH | → session-end-from-outside: `CHANNEL_CLOSE`, `DISCONNECT` or TCP loss (or link-liveness's `link_timeout`) | target, or the network | An end from outside TSB while no core reads. session-end-from-outside owns it, in its branch while the core is gone: steps 1–4, its spool entry included; the event finds no core, and the returning core writes the end (session-end-from-outside steps 10, 19 → spooled-end-finalize). | — | the returning core |
| 7 | — | PW → PW | — | → session-end-by-tsb | worker | A timer of step 5 fires: `orphan_timeout`, `idle` or `unattended`. session-end-by-tsb owns the end it makes, in its branch while the core is gone: the worker ends the session and spools the end, and the returning core records and audits it (session-end-by-tsb steps 4, 21–27 → spooled-end-finalize). When a core already reads the worker's control socket as the end completes, and that control peer opened the session or sent `hello` for it (such as an end that waited for that handshake's `live`), the worker also reports it on `channel_closed` `by: worker`, and that core records it at once, with no replay (session-end-by-tsb steps 23a–23b). Before that peer's first `ping` or `hello` the end stays in the spool, and the take-back handshake finds it [C3/channel-closed-by-worker]. | — | as session-end-by-tsb says |
| 8 | — | CO → CO | — | → core-restart | the core | The core starts again and takes back what survived. core-restart owns the restart and its order; session-end-by-tsb and session-end-from-outside own the ends spooled meanwhile (their branches while the core is gone), and spooled-end-finalize writes them; orphan-session owns the heartbeat (step 9). | — | as core-restart says |
| 9 | CO-PW | CO → PW | unix socket `control.sock` | `ping` | the core | The core's heartbeat returns: a `ping` on any of the worker's connections resets its `core_heartbeat` (ipc-control `ping`). The worker MUST then end `orphan-pending` for every session in it, reset `orphan_timeout`, and leave `idle` and `unattended` to the core again (session model §11: `orphan_timeout` "Reset by the core's heartbeat returns"; "Evaluated by") [owed]. It answers `pong` from its own loop (→ hung-worker) [C3/heartbeat-ping-pong]. When the first `ping` goes is core-restart's: after a restart, toward a worker whose version the core speaks, before the first `hello` on that control socket (core-restart step 22), so no `hello` reaches that worker while it is orphan-pending, and it starts no end of its own timers after that `ping`. | — | — |

## Refusals and who is told

- **Nobody is told while the core is gone.** No core runs, so no client is attached and no record changes;
  `orphan-pending` reaches no surface (step 3).
- **No input to refuse.** With no core there is no client and no input (immortality §4.1). After the core returns,
  input waits for `live` (reattach-handshake step 3), and an ended session never reaches `live` (reattach-handshake step 15).
- **A core that never returns.** The spool stays on disk and the audit event never lands (session model §15;
  audit-event top). `orphan_timeout` (48 h by default, a knob) bounds the sessions: a core down that long "is a
  failure nobody noticed" (immortality §8).

## Security considerations

- **Privileged sessions with no control plane.** Until `orphan_timeout` fires, a worker holds live sessions to
  production gear with nothing above it. That is why the bound exists, and why it is days, not minutes: a weekend
  outage must not kill change-window sessions (immortality §8; session model §14). Output past the ring is lost,
  and the recording declares it (spooled-end-finalize step 4).
- **Who may type.** No byte reaches the target from TSB while the core is gone: input comes only through an
  authenticated core connection (immortality §4.1). The returning core's dials are trusted by SO_PEERCRED alone
  (immortality §6.1; conventions V10).
- **The host boundary.** `orphan-pending` reaches no surface (step 3; Contract §4; conventions V11).

## Open items

1. **The clocks the worker runs `idle` and `unattended` from.** The core's clocks are durable in bbolt
   (continuity-state `activity_at`, `client_touch_at`), and no message carries them to the worker; session model §9
   defines E1 and E2 at the core. Which last byte the worker counts from, and how the core takes the two clocks
   back at step 9, are unspecified (not in plan §7). An end they decide is session-end-by-tsb's (its step 21).
2. **The heartbeat pair** (step 2): core-wide, already ruled (contract-v0.2 approval; confirmed 2026-10-01; verifier report C-2). Session model §11 and Storage §8.1 still say a target may override it — stale vault text.
3. **The orphan policy as config.** Immortality §8 makes the policy config, with `hold` the default; the contract
   names no knob for it and no policy but `hold`. Unspecified (not in plan §7).
4. **A stuck core that still holds the data peers.** With a peer attached, a slow core is backpressure
   (ipc-data-frame top). Whether a worker in `orphan-pending` keeps waiting on that peer or drops it and buffers is
   unspecified (not in plan §7).
5. **OPEN — a worker the returning core cannot speak to.** Session model §11 says a returned core that cannot
   reattach makes the session `unreachable` / `protocol`, never silently killed. Whether the core still pings a
   worker that answered `hello` with `unsupported_proto` is not said by the contract (ipc-control top withholds
   `ping` from a v0.1 worker only), and the drafts disagree: core-restart step 22 resumes `ping` on every worker at
   0.2 or later, so its `orphan_timeout` never fires; queue A6 ("Folded in", N90) says such a worker "gets no
   heartbeat, so its own orphan timer ends those sessions after 48 h". A6 is not ruled. Not in plan §7.

The open items on ends spooled while the core is gone — the grid and the order on return — moved with their steps
to spooled-end-finalize (Open items 1–2).

## Picture

pending
