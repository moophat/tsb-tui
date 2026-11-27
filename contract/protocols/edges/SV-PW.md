# Edge SV-PW: supervisor to protocol worker

| | |
|---|---|
| **Edge** | SV-PW |
| **Ends** | supervisor (SV) and protocol worker (PW), both processes, both inside U3 |
| **Seam** | none; not contract |
| **Status** | built (E0-E1) |
| **Transport** | fork and exec with an inherited socketpair; a signal and the reap |

This page is an index. The edge is not part of the contract: no schema, no fixture of its own. It is listed so worker-placement and hung-worker steps can name it.

## Transport rules

- Fork and exec, with a socketpair (a connected pair of unix sockets) the child inherits: worker-placement step 7; tsb-core `docs/system-architecture.md` "Who does what".
- The supervisor waits a bounded time for the worker to be ready, and reaps children that exit: worker-placement step 7.
- The supervisor kills with SIGKILL and waits up to `reap_timeout` for the process to go: hung-worker step 6; `supervisor.schema.json#/$defs/kill_worker`.
- No dial and no SO_PEERCRED check on this edge: the worker is the supervisor's child and inherits its end of the socketpair, so the fork itself says who the peer is. Both run as the one TSB service uid (session model §4). V10's SO_PEERCRED rule is for the sockets the core dials: CO-PW, CO-SV, CO-VT.
- The worker's sockets and target connections go with the process when it is killed: hung-worker step 6.

## Records

| Record | Contract shape | Direction | Protocols and steps | Status |
|---|---|---|---|---|
| spawn: fork and exec with inherited socketpair; or hand-off of a connection to a live worker | no contract shape | SV to PW | worker-placement 7 | built |
| readiness: the worker binds `conn-{conn_id}/control.sock`, then the supervisor answers | no contract shape (the reply is CO-SV `worker_created`) | PW to SV | worker-placement 8 (answered at 10) | built |
| SIGKILL, then the reap | no contract shape (the request is CO-SV `kill_worker`) | SV to PW | hung-worker 6 | built |
| reaping a child that exits by itself | no contract shape | SV from PW | worker-placement 7 (named; no step of its own) | built |

## Gaps

- Nothing the contract shapes crosses this edge, so no fixture grades it. `C3/supervisor-kill` and `C3/supervisor-create-worker-ready` grade the CO-SV side only.
- What the socketpair carries, if anything beyond readiness, is not described in any protocol.
- The readiness bound has no knob: worker-placement open items (`ready_timeout` on CO-SV is its error).
- `undialled_timeout` (a worker never dialled by the core) is a CO-SV field; its effect on a worker is not a step here: worker-placement step 5.
