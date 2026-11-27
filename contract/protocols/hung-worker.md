# hung-worker — a worker whose loop stops answering

| | |
|---|---|
| **ID** | hung-worker |
| **Purpose** | Treat a protocol worker whose event loop stops answering the core's ping as dead: have its supervisor kill it, and end every session in it. |
| **Status** | built (E1) |
| **Hands to** | session-end-from-outside, which owns the end (`terminated` / `worker_lost`); operator-alerts, which raises `worker_unkillable` or `supervisor_unresponsive` (plan §2.1). |
| **Corpus clauses** | session model §4, §9 E6, E7, E9, §11 (`worker_heartbeat`, `supervisor_heartbeat`, `core_heartbeat`), T11, §13; immortality §2.1, §2.2, §6.1, §6.4; Contract §4, §12; open_decisions §8 |
| **Seams** | C3 |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ipc-control.schema.json` (top: the v0.1 rule), `#/$defs/ping`, `#/$defs/pong`, `#/$defs/timer_values`; `supervisor.schema.json` (top: one answer per request, a `pong` never held behind a kill), `#/$defs/kill_worker`, `#/$defs/worker_killed`, `#/$defs/kill_failed`; `common.schema.json#/$defs/duration_ms`, `#/$defs/count` |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files, under
`srs/`: session model = `session-model-and-lifecycle.md`, immortality = `immortality-mechanism.md`,
Contract = `architecture-contract.md`, open_decisions = `open-decisions.md`.

**Words used here.**

- **Event loop**: the one asyncio loop a worker runs all its connections on (session model §4).
- **E6, E7** (session model §9): E6 `core_beat`, the worker receives the core's `ping`. E7 `worker_beat`, the core
  receives the worker's `pong`, sent from the worker's own event loop.
- **`worker_heartbeat`**: the core's timer on a silent worker, `heartbeat_interval` × `heartbeat_missed` (30 s at the
  defaults, 10 s × 3); its scope is the worker, and E7 resets it (session model §11). Its mirror at the worker,
  `core_heartbeat`, is orphan-session's.
- **Supervisor**: PID 1 of the worker container; it creates, kills and reaps workers (immortality §2.1).
  **Reap**: collect an exited child's status, so no zombie process is left.

**The heartbeat pair is core-wide.** `heartbeat_interval` and `heartbeat_missed` are `config.yaml` knobs
(conventions V7) with no per-target override: their scope is the worker, which holds connections to many targets,
so a per-target value has no single target to come from. Every connection carries the same pair, and a worker uses
the latest pair it received on any of its connections (ipc-control `timer_values`). Ruled: contract-v0.2 approval,
U1 DEFAULT 12 (tsb-core `plans/260918-1658-e1-contract/approval.md`), approved by the operator 2026-09-29; confirmed by the
operator 2026-10-01 (tsb-core `plans/reports/rulings-261001-1311-e1-protocol.md`). Session model §11's "A target's own value
overrides it" is stale for these two keys (Open items).

**Out of scope.** A satellite worker's silence may be a partition, so its sessions become `unreachable`, never
killed. Satellites are post-v1 (session model §11 `worker_heartbeat`, §13; open_decisions §8).

## Path

CO-PW → CO-SV → SV-PW → CO-SV → (session-end-from-outside); on `kill_failed` or no answer, also (operator-alerts)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock`, one JSON object per line | → session-open: `connect`, with `timer_values` | the core | The pair reaches the worker in `connect`, and every change in `timers` (config-reload, core-restart). The core MUST send the same pair on every connection [C3/connect-carries-timers grades that every connect carries its timer values; the same pair everywhere is owed]. The worker MUST use the latest pair it received on any of its connections (ipc-control `timer_values`) [owed]. | — | the worker |
| 2 | CO-PW | CO → PW | the same socket | `ping`, the core's own counter `n` | the core | The core MUST send it on every connection's control socket every `heartbeat_interval` (ipc-control `ping`; session model §9 E6, §11) [C3/heartbeat-ping-pong grades that pings are answered in order; the cadence is owed]. At the worker it is E6: a ping on any of the worker's connections resets that worker's `core_heartbeat` (orphan-session). | — | the worker |
| 3 | CO-PW | PW → CO | the same socket | `pong`, the same `n` | worker | The worker MUST answer every ping with `pong` naming its `n`, none skipped, from its own event loop, so a worker whose loop is stuck stops answering though its process and socket are alive (ipc-control `ping`, `pong`; session model §9 E7) [C3/heartbeat-ping-pong; the loop rule is graded by C3/supervisor-kill's rig `harness/rigs/hung-worker.sh`]. A pong on any of the worker's connections is an E7 for that worker: `worker_heartbeat` runs per worker, not per connection (session model §4, §9 E7, §11). | — | the core |
| 4 | — | CO → CO | — | `worker_heartbeat` fires | the core | Armed while the core holds a C3 connection to the worker, it fires when `now − last(E7) ≥ heartbeat_interval × heartbeat_missed` (session model §11). The core MUST then treat the worker as dead: locally, a worker that cannot answer is a dead worker (session model §11, T11) [C3/supervisor-kill, by `harness/rigs/hung-worker.sh`]. A late `pong` is never E9: lateness is this timer's (session model §9 E9). | — | — |
| 5 | CO-SV | CO → SV | unix socket `supervisors/{supervisor_id}/control.sock`, one JSON object per line | `kill_worker`, with `worker_id` and `reap_timeout` | the core | The core MUST ask the worker's supervisor to kill it: the supervisor that answered its `worker_created`, which the worker manifest names in `supervisor_id` (supervisor `kill_worker`; session model §11) [C3/supervisor-kill]. `reap_timeout` bounds the supervisor's wait for the process to go; the knob behind its value is unspecified (not in plan §7). No northbound request can kill a worker (C1/session-ended-unexpectedly). | — | the supervisor |
| 6 | SV-PW | SV → PW | a signal (not contract) | SIGKILL | supervisor | The supervisor kills the process and waits up to `reap_timeout` for it to go (supervisor `kill_worker`; system-architecture "Who does what"). Not contract. The process's sockets and target connections go with it (C3/supervisor-kill). | — | — |
| 7 | CO-SV | SV → CO | the supervisor's socket | `worker_killed` | supervisor | The supervisor MUST send it only after the reap, so the core knows the process and its target connections are gone [C3/supervisor-kill], and MUST send it too for a worker it no longer holds: the worker is gone either way, and a refusal would read as a failed kill [C3/supervisor-kill-gone]. | — | the core |
| 8 | CO-SV | SV → CO | the supervisor's socket | `kill_failed`, reason `unkillable` | supervisor | The process outlived SIGKILL past `reap_timeout`, stuck in the kernel, say [C3/supervisor-kill-failed]. With step 7, the supervisor answers each `kill_worker` exactly once, naming its `worker_id` (→ worker-placement step 10), and MUST NOT hold a `pong` behind a kill waiting out its `reap_timeout`, so a slow kill never reads as a silent supervisor (supervisor top) [owed]. | — | the core |
| 9 | — | CO → CO | — | → session-end-from-outside | the core | Once the kill is answered (step 7 or 8), or is unanswered within the core's timeout (session model §11 `supervisor_heartbeat`), the core MUST end every session in the worker, on every connection it holds, `terminated` / `worker_lost`: record, recording, audit, grid, and close 4012 to every attached client, as session-end-from-outside steps 11–18 write it (session model §11, §13, T11; supervisor `kill_worker`, `kill_failed`) [after `worker_killed`: C3/supervisor-kill, by `harness/rigs/hung-worker.sh`; after `kill_failed` or no answer: owed]. | — | as session-end-from-outside says: attached clients by 4012, everyone else by the record (session-list), auditors |
| 10 | — | CO → CO | — | → operator-alerts | the core | On `kill_failed`, operator-alerts raises `worker_unkillable`, naming the worker, the supervisor that could not kill it and the sessions affected, and saying the process may still hold its target connections (operator-alerts step 7; supervisor `kill_failed`). A kill unanswered within the core's timeout raises `supervisor_unresponsive` instead, and that alert too says the process may still hold those target connections (operator-alerts steps 5–6; session model §11 `supervisor_heartbeat`: "the alert says the process may still hold those target connections"; common `alert_kind` says it of `worker_unkillable` only: Open items). `worker_killed` raises no alert: no alert kind names a kill that worked (common `alert_kind`). | — | `broker.health` holders (operator-alerts) |

## Refusals and who is told

- **A v0.1 worker is never killed by this.** It has no handshake: the core MUST NOT send it `ping` or `kill_worker`.
  Its live sessions are `unreachable` / `protocol` and outlive `worker_heartbeat`, never silently killed; the
  operator drains them (ipc-control top; immortality §6.4) [C3/n1-v0.1-worker-unreachable].
- **Siblings.** Every session in the hung worker ends, on all its connections: the worker is the blast radius.
  Other workers keep answering, so their sessions live (session model §13 and its worked example; immortality §2.2).
- **Who is told.** Attached clients by close 4012; every other principal reads `terminated` / `worker_lost` in the
  record (session-end-from-outside; session-list). A principal without `broker.health` gets no alert, only these ordinary signals (session
  model §11; plan §7 G-13, found a false alarm in tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`).
  Holders of `broker.health` also get operator-alerts's event after `kill_failed` or an unanswered kill.
- **No restart.** The core MUST NOT restart the worker, its container or its supervisor. Restarting the worker
  container ends every worker in it; the operator decides that, out of band (`manage.sh`) (session model §11
  `supervisor_heartbeat`; Contract §4, §12) [owed: C3/supervisor-kill-failed states it, and its grader covers the
  supervisor only].

## Security considerations

- **The host boundary** (Contract §4; conventions V11). A hung worker is TSB's own state, acted on only by the core,
  through the supervisor; no northbound request kills a worker (C1/session-ended-unexpectedly). The 4012 close, the
  record and any alert name no pid, path or hostname [owed here; operator-alerts cites C1/alerts-stream and C6/event-alert
  for the alert's shape].
- **An unkillable worker.** After `kill_failed` the process may still hold its target connections. Its sessions are
  ended and their input refused, but the target side of those connections is the operator's to end, out of band
  (supervisor `kill_failed`; session model §11 `supervisor_heartbeat`).
- **Trust.** SO_PEERCRED on the supervisor's socket and on every control socket is the only authentication; no
  token (immortality §6.1; conventions V10).

## Open items

- **Vault text, ruled, not a conflict.** Session model §11 says "A target's own value overrides it" of every timer.
  For `heartbeat_interval` and `heartbeat_missed` that line is stale: they are core-wide by the contract-v0.2
  approval and the operator's 2026-10-01 confirmation (see the header). The vault's §11 needs the exception; the
  proposed text is in tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`, item C-2.
- **Unspecified (not in plan §7):**
  - which `config.yaml` knob gives `reap_timeout` its value (step 5);
  - the core's timeout for an answer to `kill_worker`, which no field or knob names (steps 9–10; operator-alerts names the
    same gap);
  - input between step 4 and the end: whether the core refuses it, and with which reason, since C2's
    `input_refused` has none that fits;
  - which moment stamps the end of a hung worker's sessions, the timer firing (step 4) or the kill's answer
    (steps 7–8); session-end-from-outside step 13 says only "when it found the worker dead";
  - the kill closes the worker's control sockets. Whether the core also reads those EOFs through session-end-from-outside steps 6–7
    (one dial, refused, E9) or ignores them is not stated. Either way the end is `terminated` / `worker_lost`, and
    a session ends once (session-end-from-outside step 11);
  - a `pong` that arrives after step 4 and before the end: whether it withdraws the kill.
- **Contract text short of the corpus (not in the queue).** supervisor top says that on `supervisor_unresponsive`
  "its workers and their sessions are untouched", with no exception; session model §11 `supervisor_heartbeat` adds
  "If a kill for a hung worker went unanswered, that worker's sessions still end", and supervisor `kill_worker` ends
  a hung worker's sessions. Step 9 follows the corpus and `kill_worker`. The same row says the alert for an
  unanswered kill names the process that may still hold its target connections; common `alert_kind` says that of
  `worker_unkillable` only (step 10).

## Picture

pending
