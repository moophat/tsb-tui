# Edge CO-SV: core to supervisor

| | |
|---|---|
| **Edge** | CO-SV |
| **Ends** | core (CO) and supervisor (SV), both processes |
| **Seam** | C3 |
| **Status** | built (E0-E1) |
| **Transport** | unix socket `supervisors/{supervisor_id}/control.sock`, one JSON object per line; the core dials |
| **Contract** | `contract/schemas/supervisor.schema.json` (4 requests, 6 replies) |

This page is an index. The step tables in the protocol documents own every rule; `conventions.md` owns the V rules.

## Transport rules

- The core dials the supervisor's `control_sock` (`supervisor-manifest` top). That the supervisor never dials the core
  is not contract: V10, OPEN (conventions open item 16).
- A peer is accepted only with the TSB service uid (SO_PEERCRED); no token on the socket: V10.
- Encoding: one UTF-8 JSON object per line: V12.
- Every request line gets exactly one answer, a refusal included. Answers may come out of order and each names its request (`conn_id`, `worker_id` or `n`): `supervisor.schema.json` top; worker-placement step 10.
- A `pong` is never held behind another request's work: `supervisor.schema.json` top; operator-alerts step 4.
- Unknown command: `error` `unknown_cmd`, connection kept; malformed line: `bad_request`: V2.
- Unknown `proto_version` or v0.1 peer: V1; version gate in worker-placement step 2.
- No credential and no connect detail in any request: V10; worker-placement step 5.
- `pid` in a `workers` reply is host detail that never goes northbound: V11; operator-alerts (closing note).
- Knobs carried (`n_max`, `placement_key`, `undialled_timeout`, `reap_timeout`): V7.

## Messages

| Message | `$def` in `supervisor.schema.json` | Direction | Protocols and steps | Status |
|---|---|---|---|---|
| dial of `control_sock`, no message (liveness) | no contract shape; named in `supervisor-manifest` top | CO to SV | worker-placement 3; core-restart 10; operator-alerts 15 (refused dial) | built |
| `create_worker` | `#/$defs/create_worker` | CO to SV | worker-placement 5 | built |
| `worker_created` | `#/$defs/worker_created` | SV to CO | worker-placement 10 | built |
| `error`: `spawn_failed`, `ready_timeout`, `capacity` | `#/$defs/error` | SV to CO | worker-placement 10 (causes in 6, 7) | built |
| `error`: `bad_request`, `unknown_cmd` | `#/$defs/error` | SV to CO | worker-placement 5 (refusal column); V2 | built |
| `kill_worker` | `#/$defs/kill_worker` | CO to SV | hung-worker 5; named in operator-alerts 5 | built |
| `worker_killed` | `#/$defs/worker_killed` | SV to CO | hung-worker 7 | built |
| `kill_failed` (`unkillable`) | `#/$defs/kill_failed` | SV to CO | hung-worker 8; operator-alerts 7 | built |
| `ping` | `#/$defs/ping` | CO to SV | operator-alerts 3 | built |
| `pong` | `#/$defs/pong` | SV to CO | operator-alerts 4, 11 | built |
| `list_workers` | `#/$defs/list_workers` | CO to SV | operator-alerts 12 | built |
| `workers` | `#/$defs/workers` | SV to CO | operator-alerts 13 | built |

Shared `$defs` (`request`, `reply`, `conn_dir`, `placement_key`, `pid`) are shapes, not messages.

## Gaps

- No knob or field for the core's wait on a `create_worker` or `kill_worker` answer: worker-placement, hung-worker, operator-alerts open items; conventions open item 10.
- Which supervisor the core asks when several are live is the core's own policy; whether it asks another after a refusal is unspecified: worker-placement open items (G-16).
- What limits the number of workers, so that `capacity` happens, is unspecified: worker-placement open items.
- Which `config.yaml` knob gives `reap_timeout` its value: hung-worker open items.
- The clause is "transport-agnostic" (unix socket today, SSH channel for a satellite supervisor, `supervisor.schema.json` top). No protocol uses the satellite form.

## Terminal output accounting

| Message or record | Owner | Contract and grading |
|---|---|---|
| `worker_created.proto_version` | terminal-output-accounting §3 | capable new worker advertises 0.8 before manifest exists; old worker's version preserved [C3/control-message-shape-v0.8; C3/terminal-disposal-version-gate] |
