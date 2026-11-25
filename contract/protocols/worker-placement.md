# worker-placement — get a worker for a new connection

| | |
|---|---|
| **ID** | worker-placement |
| **Purpose** | Get a protocol worker process for a new connection from a supervisor, sharing a worker only with connections of the same placement key. |
| **Status** | built (E0–E1) |
| **Corpus clauses** | immortality §2.1, §2.2, §6.1, §6.4; Contract §4, §8; session model §4, §9 E11, §11 (`supervisor_heartbeat`); lang-arch §4.1, §6.4 step 4 |
| **Seams** | C3 (the supervisor's socket), C5 (the two manifests) |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `supervisor.schema.json#/$defs/create_worker`, `#/$defs/worker_created`, `#/$defs/error`, `#/$defs/conn_dir`, `#/$defs/placement_key`, `#/$defs/pid`, `#/$defs/request`, `#/$defs/reply`; `supervisor-manifest.schema.json`; `worker-manifest.schema.json`; `common.schema.json#/$defs/proto_version`, `#/$defs/id`, `#/$defs/duration_ms` |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3.

session-open runs worker-placement when it needs a new connection; a reused connection needs no new worker. worker-placement ends when
the core holds the connection's directory. session-open then dials that directory's control socket and sends
`connect` over edge CO-PW. worker-placement has no consumer step: a surface sees only session-open's answer.

**Words used here.**

- **Supervisor.** The long-lived process that is PID 1 of the worker container. It creates, kills and reaps
  workers, so workers outlive the core (immortality §2.1).
- **Placement key.** A string the supervisor compares for equality and never interprets. Connections with
  the same key may share a worker (supervisor `placement_key`; session model §4).
- **Manifest.** The file a supervisor or a worker writes so that the core, or a restarted core, can find it
  (Contract §8; immortality §2.1).
- **Dial.** Open a connection to a unix socket. The core is the only dialler (conventions V10).
- **SO_PEERCRED.** The kernel tells each end of a unix socket the other end's uid. It is the whole of
  TSB's local trust (immortality §6.1).
- **Connection directory.** `conn-{conn_id}/` under the shared runtime directory. It holds the
  connection's control socket and its sessions' data sockets (lang-arch §4.1; supervisor `conn_dir`).
- **Reap.** Collect an exited child process's status, so that no zombie process is left behind.

## Path

SV-FS → CO-FS → CO-SV → SV-PW → PW-FS → CO-SV

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SV-FS | SV → FS | a file | `supervisor-manifest.schema.json` (`supervisors/{supervisor_id}/manifest.json`) | supervisor | Names itself as one member of the set of supervisors the core addresses. "Every live supervisor manifest names one" (supervisor top). The manifest gives the supervisor's control socket and, from v0.2, its `proto_version` (the C3 version it and its workers speak). The supervisor MUST write exactly the closed shape [C5/supervisor-manifest-valid; C5/supervisor-manifest-proto-version]. | — | the core, at step 2 |
| 2 | CO-FS | FS → CO | a file | `supervisor-manifest.schema.json` | the core | Reads every supervisor manifest. The core MUST treat supervisors as a set and never assume there is only one (supervisor top) [C5/supervisor-manifest-valid states this but grades only the shape: owed]. It MUST ask for workers only through a supervisor whose `proto_version` it speaks; a missing version means v0.1 (conventions V1) [C5/supervisor-manifest-proto-version; C3/n1-v0.1-worker-unreachable]. | A supervisor that is v0.1, or whose version the core does not speak, is never asked for a worker. The core still pings it and lists its workers (→ operator-alerts). If no supervisor is left, session-open answers 503 `worker_unavailable`, saying the workers predate the core [C1/create-refused-v0.1-workers]. | the creating principal, through session-open |
| 3 | CO-SV | CO → SV | unix socket | dial of the manifest's `control_sock` | the core | Liveness is the dial. A socket that is missing or refuses the dial belongs to a dead supervisor, and the core MUST skip it (supervisor-manifest top) [C5/manifest-rediscovery-case, e0 reference: fixture-sup-2 is dead; U2 `rediscover_test.go`]. When the core dials, it MUST check SO_PEERCRED (immortality §6.1; V10) [owed]. | The supervisor MUST refuse a peer whose uid is not the TSB service uid (supervisor top) [owed]. | — |
| 4 | — | CO → CO | — | — | the core | Picks one live supervisor whose version it speaks. Which one is the core's own policy (plan §7 G-16; verifier: wording only, the supervisor top text to say so). | — | — |
| 5 | CO-SV | CO → SV | unix socket, one JSON object per line | `supervisor.schema.json#/$defs/create_worker` | the core | Asks for a worker for `conn_id`. The request MUST carry `placement_key` and `n_max`, and MUST NOT carry a credential or any connect detail [C3/supervisor-request-shape]. The key MUST be the owning TSB user's login name under `worker.placement: per_user`, and one constant under `shared` (session model §4; `create_worker`) [C3/supervisor-request-shape grades the key's presence and shape; the value rule is owed]. `n_max` is the knob `worker.max_connections` (V7). The optional `undialled_timeout` is a range, never a default [C3/create-worker-knobs-no-default]; step 11 says what it bounds. | A malformed request, or a second `create_worker` for a connection the supervisor already placed, gets `error` `bad_request`, never `capacity` [C3/supervisor-bad-request]. An unknown `cmd` gets `unknown_cmd` (V2, V4). A request still unanswered when its timeout runs out raises `supervisor_unresponsive` (→ operator-alerts), and that supervisor's workers are untouched (session model §11 `supervisor_heartbeat`). | the core |
| 6 | — | SV → SV | — | — | supervisor | Compares `placement_key` for equality only. The supervisor MUST put the connection only into a live worker that was spawned with the same key and holds fewer than `n_max` connections; otherwise it spawns a worker with this key. A worker never holds connections of two keys, not even one after the other [C3/supervisor-create-worker-ready]. When several workers qualify, which one takes the connection is unspecified (not in plan §7). | `capacity`: no worker can take the connection. What limits the number of workers, and so makes `capacity` happen, is unspecified (not in plan §7). | the core, at step 10 |
| 7 | SV-PW | SV → PW | fork+exec with an inherited socketpair (a connected pair of unix sockets the child inherits) | not contract | supervisor | Spawns the worker, or hands the connection to the chosen live worker. Waits a bounded time for the worker to be ready, and reaps children that exit (system-architecture "Who does what"; immortality §2.1). Not contract. | `spawn_failed`: the child could not start, or exited before it was ready; carries `exit_code` when known. `ready_timeout`: a spawned worker is killed and reaped; a live worker that was handed the connection has the hand-off withdrawn, and nothing is killed. Each error names `conn_id` [C3/supervisor-create-worker-error]. The kill and reap, and the withdrawal, are owed. | the core, at step 10 |
| 8 | — | PW → PW | — | — | worker | Binds `conn-{conn_id}/control.sock`. The supervisor answers only after this (step 10). | — | — |
| 9 | PW-FS | PW → FS | a file | `worker-manifest.schema.json` (`workers/{worker_id}.json`) | worker | The worker MUST rewrite its manifest atomically (a temporary file renamed over the old one) on every change. It MUST use the closed shape, give its `supervisor_id` and `proto_version`, and carry no credential [C5/worker-manifest-valid; C5/manifest-closed-v0.1; C5/manifest-proto-version; these grade the shape, so atomicity is owed]. A new worker may list no connection yet. A connection is listed with its `target_id`, which reaches the worker in `connect` (ipc-control `connect`), so the new connection appears in the manifest during session-open. The manifest need not exist yet when the core reads step 10's reply: the core takes a new worker's version from `worker_created` (supervisor `worker_created`). Whether this write comes before or after step 10 is otherwise not stated. | — | a restarted core (core-restart) |
| 10 | CO-SV | SV → CO | unix socket, one JSON object per line | `supervisor.schema.json#/$defs/worker_created`, or `#/$defs/error` | supervisor | The supervisor MUST send `worker_created` only after the worker has bound the control socket, so the core can dial it as soon as it reads the reply [C3/supervisor-create-worker-ready]. The reply names `conn_id`, `worker_id`, `conn_dir` and the worker's `proto_version`, "so the core knows a new worker's version before its manifest need exist" (`worker_created`) [C3/supervisor-request-shape-v0.2]. The supervisor MUST answer every request line exactly once, refusals included. Answers need not follow request order; each names its request [C3/supervisor-create-worker-error; C3/supervisor-bad-request; C3/supervisor-heartbeat]. operator-alerts step 5 and hung-worker step 8 cite this once-only answer. | the refusals of steps 5–7 | the core. It hands `conn_dir` back to session-open, which dials over CO-PW, or session-open answers 503 `worker_unavailable` |
| 11 | — | PW → PW | — | — | worker | When `undialled_timeout` was sent and no core dials the connection's control socket within it, the worker MUST drop that connection and its directory. It MUST exit only if it then holds no other connection (`create_worker`; lang-arch §6.4 step 4; immortality §2.2) [owed: C3/create-worker-knobs-no-default grades only the field's range]. When no bound was sent, the worker MUST NOT assume one [owed: C3/create-worker-knobs-no-default states it but grades only the `create_worker` shape]. | — | No message is defined. The worker's manifest changes (step 9), and the supervisor reaps the worker if it exits (step 7). |

## Refusals and who is told

- **An error with no `conn_id`.** It answers a line that has no readable key. The supervisor sends it as
  soon as it reads that line, so it answers the oldest such line still pending (supervisor top; V2).
- **What the surface sees.** A surface never sees a supervisor's reason. session-open answers every failure to get
  a worker with 503 `worker_unavailable` (error `x-codes`). That answer names no supervisor, pid or path
  (V11). Only the v0.1 case is graded [C1/create-refused-v0.1-workers]. The 503 after `spawn_failed`,
  `ready_timeout`, `capacity`, or no answer at all is owed.
- **Asking another supervisor.** Whether the core asks another live supervisor before it answers 503 is
  unspecified (not in plan §7; the verifier's G-16 covers only the pick).
- **The core's timeout for `create_worker`.** The core waits "within its timeout" (session model §11), but no
  knob names that timeout. It is unspecified (not in plan §7).
- **The supervisor's readiness bound** (step 7). It is bounded, but neither the wire nor a knob carries its
  value (`error`).
- **A supervisor that stops answering.** This is operator-alerts's job. `supervisor_unresponsive` goes to holders of
  broker.health only. TSB never restarts a supervisor by itself (Contract §12; session model §11).

## Security considerations

- **Tenants (Contract §4).** Under `per_user`, one user's worker crash never reaches another user's
  sessions. A worker that once held one user's connections never serves another user's (`create_worker`;
  session model §4) [C3/supervisor-create-worker-ready]. Under `shared`, a crash reaches every user whose
  connections share that worker (session model §4).
- **Crashes, not compromise.** Placement keeps one user's crash away from other users. It does not contain a
  compromised worker: every worker runs as the one TSB service uid that SO_PEERCRED trusts, so a
  compromised worker holds what the whole broker holds (session model §4; immortality §6.1).
- **Credentials.** No credential goes into `create_worker`, a worker's argv or environment, or a manifest.
  Credentials go only in `connect.auth` and, from 0.7, `auth_answer` on the connection's own socket (ipc-control top). The
  `create_worker` and manifest cases are graded [C3/supervisor-request-shape; C5/manifest-closed-v0.1]. The
  argv and environment cases are owed.
- **Trust.** SO_PEERCRED on the supervisor's socket is the only authentication; no token is used
  (immortality §6.1; V10). Steps 3 and 5 are owed.
- **The host boundary.** A manifest's `pid` is informational. It means something only inside the writer's
  PID namespace, and liveness is decided by the dial (supervisor `pid`). No pid, path or connection
  directory reaches a surface (Contract §4; V11).
- **The boundary after a restart.** `list_workers` reports each worker's key, so a restarted core can see
  which user's connections a worker holds (supervisor `workers`; operator-alerts) [C3/supervisor-create-worker-ready].

## Open items

- **G-16, wording only** (verifier): which supervisor the core picks when several are live is the core's own policy;
  the supervisor top text is to say so. Whether it asks another one after `spawn_failed`, `ready_timeout`,
  `capacity` or no answer: unspecified (not in plan §7).
- **Not in plan §7:**
  - which of several qualifying same-key workers takes the connection (step 6);
  - what limits the number of workers, and so makes `capacity` happen (step 6);
  - the knob for the core's `create_worker` timeout (step 5);
  - the knob, if there is one, for the supervisor's readiness bound (step 7).
- **OPEN, the contract against itself — crash or compromise.** C3/supervisor-create-worker-ready's description: under
  `per_user` "one user's crash or compromised worker never reaches another user's sessions". supervisor
  `create_worker`: "Placement isolates crashes, not compromise" (also session model §4; CHANGELOG contract-v0.2 C3).
  This page follows the schema (Security, "Crashes, not compromise") and cites that fixture only for the key rule it
  grades. Not in the queue or the review log.

## Picture

pending
