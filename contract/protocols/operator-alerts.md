# operator-alerts — operator alerts

| | |
|---|---|
| **ID** | operator-alerts |
| **Purpose** | Raise and clear TSB's own operator alerts, audit each, and stream them only to principals holding `broker.health`. |
| **Status** | built (E1); grows E2 (`vt_unavailable`: steps 8 and 16, and its part of step 20) |
| **Corpus clauses** | session model §9 E11, §11 (`supervisor_heartbeat`; "Operator alerts"); Contract §4, §12; Storage §6 (audit log), §9 (`policy.yaml`, hot-reloadable) |
| **Seams** | C1 (the stream, `policy.yaml`), C3 (supervisor), C4 (VT worker), C5 (supervisor manifest), C6 (audit) |
| **Schemas** (contract-v0.3) | see the list below |

Schemas and `$defs`, at `CONTRACT_VERSION` contract-v0.3:

- `contract/openapi/tsb.openapi.json`: operation `alerts` (`GET /alerts`) with its 200 `x-itemSchema`;
  `components/schemas/AlertEvent`; `components/responses/Unauthenticated`
- `contract/schemas/common.schema.json`: `$defs/alert_kind`, `$defs/alert_subjects`
- `contract/schemas/audit-event.schema.json`: `$defs/alert_event`
- `contract/schemas/supervisor.schema.json`: top; `$defs/ping`, `pong`, `list_workers`, `workers`, `kill_failed`
- `contract/schemas/supervisor-manifest.schema.json`: top
- `contract/schemas/vt-ipc.schema.json`: top ("Order and idempotence"); `$defs/snapshot`, `$defs/error`
- `contract/schemas/continuity-state.schema.json`: `$defs/grid_snapshot_record`
- `contract/schemas/policy.schema.json`: `$defs/permission` (`broker.health`)
- `contract/schemas/error.schema.json`: `x-codes` `unauthenticated`, `forbidden`

Fixtures are cited by seam and name; they live under `contract/fixtures/`.

**Words used here.**

- An **operator alert** is TSB's own internal state that an operator must act on: a supervisor that stops answering, a
  worker that will not die, a VT worker that is down. It is bound to no session and no principal (session model §11).
- The **active set** is the alerts raised and not yet cleared.
- The **alert stream** is the answer to `GET /alerts`: **server-sent events**, one HTTP response of type
  `text/event-stream` that stays open while the core writes events into it as text. Each event here has the type
  `alert`, and its data is one `AlertEvent` as JSON. A **comment line** is a line of that format that carries no event.
- **E11** (`supervisor_beat`): the core receives a supervisor's `pong`, or its answer to a create or kill request
  within that request's timeout (session model §9).
- The **commit point** is the recording position on the session record (`recording_last_seq_out`,
  `recording_committed_offset`), written in the same bbolt write as the session's last grid snapshot, or record-only
  for a session with none (grid-snapshot rule 11); the
  `grid_snapshot_record` carries no offset of its own (continuity-state `grid_snapshot_record`). The **ring** is the
  worker's bounded per-session output buffer.
- `heartbeat_interval` is a `config.yaml` knob (conventions V7).

## Path

SF-CO → CO-SV → CO-VT (E2) → CO-DK → SF-CO → CO-FS → CO-SV → CO-DK → SF-CO; ended by → login (SF-CO) or → config-reload (CO-FS)

## Steps

Steps 1–2 open the stream. Steps 3–10 raise an alert, 11–18 clear one, 19–20 rebuild the active set at core start,
and 21–24 hold or end the stream.

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `GET /alerts` (operation `alerts`), the login cookie (`securitySchemes.session`); no ticket | `broker.health` holder | the core opens an alert stream for this login | no valid cookie: 401 `unauthenticated` and no stream opens (`C1/alerts-unauthenticated-401`); no `broker.health`: 403 `forbidden` and no stream opens (`C1/alerts-without-broker-health-403`) | the requester |
| 2 | SF-CO | CO → SF | HTTPS (server-sent events) | 200 `text/event-stream`: one `alert` event, `AlertEvent` state `raised`, per alert in the active set | the core | the core MUST send the active set first (`C1/alerts-stream` (reference; U2 stream test)). A reconnecting surface gets the set again, so no `Last-Event-ID` is needed | — | that stream |
| 3 | CO-SV | CO → SV | unix socket | supervisor `ping` | the core | the core MUST ping every supervisor it knows (every live supervisor manifest names one) every `heartbeat_interval` (`C3/supervisor-heartbeat` grades pings sent and answered; the cadence is owed) | — | — |
| 4 | CO-SV | SV → CO | unix socket | `pong`, the same `n` | supervisor | an E11 for that supervisor. The supervisor MUST answer each ping with its `pong` (`C3/supervisor-heartbeat`), and MUST NOT hold a `pong` behind another request's work, such as a kill waiting out its `reap_timeout` (owed) | — | — |
| 5 | CO-SV | CO → SV | unix socket | → worker-placement: `create_worker`, or → hung-worker: `kill_worker` | the core; supervisor | an answer within the request's timeout is an E11; no answer is a trigger for step 6. The supervisor answers every request line exactly once, a refusal included (→ worker-placement step 10), so step 6 never fires on a supervisor that is merely refusing | — | — |
| 6 | — | CO → CO | — | raise `supervisor_unresponsive` | the core | when `now − last(E11)` reaches `supervisor_heartbeat` (30 s at the defaults: three pings unanswered), or a request of step 5 goes unanswered within its timeout, the core MUST raise it naming that supervisor (owed). It MUST NOT kill that supervisor's workers, end their sessions — except the hung worker whose kill went unanswered, whose sessions still end (→ hung-worker step 9) — or restart the supervisor (owed; Contract §12). What the alert says after an unanswered kill: Open items | — | steps 9–10 |
| 7 | CO-SV | SV → CO | unix socket | → hung-worker: `kill_failed` (`unkillable`) | supervisor; the core | the core MUST raise `worker_unkillable` naming the worker, the supervisor that could not kill it and, in `session_ids`, the sessions affected (owed; `C3/supervisor-kill-failed` grades `kill_failed` only). The alert says the process may still hold its target connections; hung-worker still ends its sessions | — | steps 9–10 |
| 8 *(E2)* | CO-VT | CO → VT | unix socket | → grid-snapshot: `snapshot` (cannot be taken), or → grid-restore: `ping` (unanswered) | the core | the core MUST raise `vt_unavailable`, naming no component of its own and listing in `session_ids` the sessions whose grid that VT worker holds, if any (owed; `C4/vt-snapshot-unavailable` records the condition and marks the core owed). The commit point stops advancing; the recording keeps every frame meanwhile, and a listed session that must reattach (a core restart, a lone data-socket loss) waits for a snapshot at most `snapshot.wait_sec`, then loses its grid (continuity-state top; grid-snapshot step 9, rules 10, 12) | — | steps 9–10 |
| 9 | CO-DK | CO → DK | file (append) | audit `alert.raised` (`alert_event`), a new `alert_id` | the core | the core MUST write it, bound to no principal and to no session's record (`C6/event-alert` (reference; U2 alert test: each raise audited under its `alert_id`); it has no `vt_unavailable` instance, so that kind's write is owed) | — | the audit log; a restarted core (step 19) |
| 10 | SF-CO | CO → SF | HTTPS (server-sent events) | `alert` event, `AlertEvent` state `raised` | the core | the core MUST send it on every open stream, in order (`C1/alerts-stream` (reference; U2 stream test: the active set, then each raise and clear); no fixture has a `vt_unavailable` event, so that kind's is owed) | — | every `broker.health` holder with a stream open |
| 11 | CO-SV | SV → CO | unix socket | `pong` | supervisor | a supervisor under `supervisor_unresponsive` answers a ping again: the core MUST clear that alert (owed) | — | steps 17–18 |
| 12 | CO-SV | CO → SV | unix socket | `list_workers` | the core | the core MUST poll it at the supervisor heartbeat cadence (owed) | — | — |
| 13 | CO-SV | SV → CO | unix socket | `workers` | supervisor | the supervisor answers step 12 exactly once (`C3/supervisor-heartbeat`). When the list no longer names a worker under `worker_unkillable`, the core MUST clear that alert (owed) | — | steps 17–18 |
| 14 | CO-FS | FS → CO | file (read) | `supervisor-manifest`, removed | the core | that supervisor is gone: the core MUST clear every `supervisor_unresponsive` and `worker_unkillable` that names it (`C1/alerts-stream` (reference; U2 stream test)). When the core looks, outside a restart, is unspecified (not in plan §7; reported) | — | steps 17–18 |
| 15 | CO-SV | CO → SV | unix socket | a dial to the supervisor's control socket, refused | the core | gone, as step 14: "missing or refused means dead" (supervisor-manifest top); the same clears (`C1/alerts-stream` (reference; U2 stream test)) | — | steps 17–18 |
| 16 *(E2)* | — | CO → CO | — | clear `vt_unavailable` | the core | unspecified (plan §7 G-10) | — | — |
| 17 | CO-DK | CO → DK | file (append) | audit `alert.cleared` (`alert_event`), the `alert_id` of its raise | the core | as step 9 (`C6/event-alert` (reference; U2 alert test: the clear audited under its raise's `alert_id`); a `vt_unavailable` clear owed) | — | the audit log |
| 18 | SF-CO | CO → SF | HTTPS (server-sent events) | `alert` event, `AlertEvent` state `cleared`, the `alert_id` of its raise | the core | the alert leaves the active set; sent on every open stream, in order (`C1/alerts-stream` (reference; U2 stream test); a `vt_unavailable` clear owed) | — | every `broker.health` holder with a stream open |
| 19 | CO-DK | DK → CO | file (read) | audit `alert.raised`, `alert.cleared` | the core, at start | the core MUST rebuild the active set from the raises that have no clear (`C6/event-alert` (reference; U2 alert test)) | — | — |
| 20 | — | CO → CO | — | re-check each rebuilt alert | the core | the core MUST check each against live state (steps 3–4, 12–15) and clear what no longer holds (steps 17–18) (`C6/event-alert` (reference; U2 alert test: re-checked, and cleared when it no longer holds)). How a rebuilt `vt_unavailable` is re-checked: unspecified (plan §7 G-10) | — | steps 17–18 |
| 21 | SF-CO | CO → SF | HTTPS (server-sent events) | comment line | the core | the core MUST send one whenever no event was sent for `heartbeat_interval` (the stream has no knob of its own), so no proxy closes an idle stream (`C1/alerts-stream` (reference; U2 stream test)) | — | that stream |
| 22 | SF-CO | SF → CO | HTTPS | → login: `POST /auth/logout`, the login expires, device revocation ends it, or an account act ends it (login step 18) | the principal; the core | the login the stream was opened with ends | — | — |
| 23 | CO-FS | FS → CO | file (read) | → config-reload: a `policy.yaml` that removes `broker.health` from the principal, or removes the principal | an admin editing files out of band | the principal no longer holds `broker.health` | — | — |
| 24 | SF-CO | CO → SF | HTTPS | the end of the stream | the core | after step 22 or 23 the core MUST end that login's stream: authorization holds for the stream's life, not only at connect (`C1/alerts-stream` (reference; U2 stream test)) | — | that stream's surface |

## Refusals and who is told

**Refusals.** Only step 1 refuses (401, 403). Losing the right mid-stream ends the stream (step 24); the contract
names no closing event. A supervisor's `error` answers its request, so it is an E11, never a missed one (supervisor
top).

**The audit log.** Every raise and clear is written (steps 9, 17), and the log is what a restarted core reads back
(step 19). The contract orders the events on one stream; it does not order an audit write against its stream event,
and the step numbers do not either.

**Whoever runs the host, out of band.** Alerts also go "out of band, as log lines and metrics for whoever runs the
host" (session model §11). The core MUST emit that copy (owed). It is not an edge (plan §1.3 F3, §1.5 P7): no TSB
component receives it, the contract gives it no shape, and host administration stays out of band (Contract §4).

**A principal without `broker.health`** "sees only what an alert does to their own sessions" (session model §11;
policy `permission`). No alert message carries it: a principal sees the session's ordinary signals (plan §7 G-13, a false alarm per the verifier). Effects that reach such a principal
today belong to other protocols and do not name the alert: the sessions of a `worker_unkillable` worker end through
hung-worker and session-end-from-outside (supervisor `kill_failed`). During a `vt_unavailable` the recording keeps
every frame; a session that must reattach meanwhile waits at most `snapshot.wait_sec`, then loses its grid, which
reaches the principal through stale-screen as `grid_lost` (common `alert_kind`; grid-snapshot rules 10, 12).

**Consumers.**

- A surface MUST ignore an event type it does not know on the stream (OpenAPI `alerts` 200; conventions V2). Owed.
- A surface MUST show an alert `kind` it does not know as TSB's own alert, never as host state (`AlertEvent`; common
  `alert_kind`; contract README "Senders and receivers"). Owed.
- The 200's schema types one event's data, not the whole body: a stated deviation from OpenAPI 3.1 until 3.2's
  `itemSchema`, which `x-itemSchema` already names. A code generator types each `alert` event's data as
  `AlertEvent`, never the stream as one object (OpenAPI `alerts` 200).

## Security considerations

**The host boundary** (Contract §4; conventions V11). An alert is TSB's own state, not host state. On the stream and
in the audit log it names only TSB's components (a supervisor, a worker, sessions), never a pid, a path or a
hostname. Messages on edges CO-SV and CO-FS do carry host detail: the `pid` in a `workers` reply, and the `pid` and
control socket path in a supervisor manifest. The core MUST NOT copy any of it into an alert. `C1/alerts-stream`
refuses `pid` and `hostname`, and `C6/event-alert` refuses `pid`, `host` and `conn_dir`; both shapes are closed.

**The permission** (policy `permission`; session model §11; conventions V9). Seeing TSB's internals is itself a
permission. `broker.health` is granted per user (the orchestrator's DEFAULT O2, CHANGELOG contract-v0.2 entry, approved 2026-09-29: queue V14, PD4), and the deploy's template grants
it to the seeded superadmin alone (`C1/policy-broker-health`). The stream is authenticated by the login cookie,
never by a ticket or a token in a URL (conventions V10). The right is checked for the stream's whole life (step 24).

**No action through TSB.** operator-alerts only tells. Acting on an alert at the host level, such as restarting a supervisor or
worker container, is host administration: `manage.sh`, out of band, never a TSB API (session model §11; Contract
§4). TSB never restarts a supervisor by itself and decides nothing on the operator's behalf (Contract §12; step 6).
A `worker_unkillable` alert means the process may still hold its target connections (supervisor `kill_failed`).
Ending them is the operator's decision, made out of band.

## Open items

- **G-10** (plan §7.3): when `vt_unavailable` clears, and how a rebuilt one is re-checked at restart. Unspecified.
- **G-13** (plan §7.3): a false alarm per the verifier; the ordinary session signals (4012, the record's state) carry the effect.
- Not in plan §7, reported here as unspecified:
  - the core's timeout for an answer to `create_worker` or `kill_worker`. Session model §11 says "a create/kill
    request unanswered within its timeout", but no field or knob names that timeout;
  - when the core notices a removed supervisor manifest outside a restart (step 14). Whatever the answer, a supervisor
    that is gone clears both kinds that name it, never raises one ("both when that supervisor is gone — its manifest
    removed or its socket refusing", OpenAPI `alerts`; supervisor-manifest top);
  - whether a condition already raised and still active is raised again, for example a VT worker that fails each
    forced snapshot.
- `forbidden` in error `x-codes` does not name the missing-`broker.health` 403 on `/alerts` (conventions open item 7).
- **C-7** (plan §7.1): `vt_unavailable` is contract-v0.3's addition to the corpus's "Kinds so far", additive (V1).
  It is not on the approval page: it came from a red-team fix and the operator has not seen it (queue S46, V8). Steps
  8, 16 and 20's `vt_unavailable` part, grid-snapshot step 9 and grid-restore step 18 lean on it.
- **Who holds `broker.health` — ruled** (O2, approved 2026-09-29; queue V14, PD4): a per-user permission the deploy
  grants to the seeded superadmin (policy `permission`; CHANGELOG contract-v0.2). Stale vault text: session model §11
  "superadmin-only permission `broker.health`" (queue V14). How roles are defined stays OPEN (open_decisions §3).
- **OPEN — the alert after an unanswered kill** (step 6; not in plan §7). session model §11 `supervisor_heartbeat`:
  "If a kill for a hung worker went unanswered, that worker's sessions still end …, and the alert says the process
  may still hold those target connections". The contract gives `supervisor_unresponsive` no way to say it
  (`AlertEvent` carries no text; common `alert_kind` says it of `worker_unkillable` only). And supervisor top says
  that on `supervisor_unresponsive` "its workers and their sessions are untouched", with no exception, where
  supervisor `kill_worker` ends a hung worker's sessions. Step 6 follows the corpus. Not in the queue's contract
  stale text (K1–K15).

## Picture

pending

**Recording hold (tsb-program #141).** Raise one `recording_unavailable` alert per held session for `broker.health` holders, rebuild the active set at start and clear when `recording_held` clears. Audit each raise and clear as in steps 9, 17 and 19. The existing audit rule orders the events on one stream but does not order the audit write against its stream event. [owed]

**Recording hold alert persistence.** The bbolt held state is the alert's source. At start the core raises `recording_unavailable` for every held session it finds there. It raises the in-process alert before writing `alert.raised`; a failed audit write is logged and the in-process alert remains. Clear in process before writing `alert.cleared`, logging a failure likewise. A lost audit line loses history, not the active held alert. [owed]

**Held-alert reconciliation.** At start each held session has one alert. An audit raise for the same session and kind denotes that same alert and reuses its `alert_id`; an audit raise for a session no longer held receives `alert.cleared` at start. Clear the alert when `recording_held` clears, including after a held ended session's successor end marker is synced (terminal-output-accounting §5.3; owed).
