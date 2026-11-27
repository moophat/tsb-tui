# config-reload — apply a changed config.yaml or policy.yaml

| | |
|---|---|
| **ID** | config-reload |
| **Purpose** | Apply a changed `config.yaml` or `policy.yaml` to the running core: new timer values to every worker connection that can take them, new permissions to every later request. |
| **Status** | built (E1) for `policy.yaml`; E3 for `config.yaml` ("timers hot-reload", development-plan §3, E3 row): steps 3–7. The `timers` message itself is built (E1) and is also sent at reattach (→ core-restart). Grows E2: `keepalive_probe_interval` in every set toward a 0.3 worker (step 4) |
| **Corpus clauses** | Storage §2.4, §8.1, §9; session model §11, §15; open_decisions §3, §5; Contract §1, §4, §13 |
| **Seams** | C1 (`policy.yaml`, the alert stream), C2 (attach), C3 (`timers`) |
| **Schemas** (contract-v0.3) | see the list below |

Schemas and `$defs`, at `CONTRACT_VERSION` contract-v0.3:

- `contract/schemas/policy.schema.json`: top; `$defs/user`, `$defs/permission`, `$defs/target_filter`
- `contract/schemas/ipc-control.schema.json`: top (the version paragraph); `$defs/timers`, `$defs/timer_values`,
  `$defs/error` (`bad_request`), `$defs/buffer_bytes`, `$defs/input_frame_max`
- `contract/schemas/common.schema.json`: `$defs/duration_ms`, `$defs/count`
- `contract/schemas/ws-frames.schema.json`: `x-attach-order` (4003), `x-close-codes` (4003), `$defs/clients`
- `contract/openapi/tsb.openapi.json`: operation `alerts` (`GET /alerts`), its description
- `contract/schemas/audit-event.schema.json`: `$defs/config_event`

Fixtures are cited by seam and name; they live under `contract/fixtures/`.

**Words used here.**

- `config.yaml` holds the knobs (conventions V7), the session timers among them; the knobs of `config.yaml` have the contract shape `config.schema.json`, and every other key in the file is deployment-local.
  `policy.yaml` holds who may do what on which targets, and the recording policy `recording.enabled` (policy
  `recording`; decision 5.40a, policy top); the core validates it against `policy.schema.json`. From the vault release
  it also holds the permission `vault.admin`, `recording.force` (target filters whose new sessions are recorded
  whatever `recording.enabled` says), and `devices` (the revocation switch and the device inactivity bound); each is
  read at its use, so a reload applies to the next create, revocation or sweep [C1/policy-e4-keys].
- A connection's **set** is the timer values its sessions run by (`timer_values`). Each is a system default in
  `config.yaml`, which **a target's own value** overrides, except the **heartbeat pair** (`heartbeat_interval`,
  `heartbeat_missed`), which is **core-wide**: the same on every connection.
- A worker's **version** is its `proto_version`; absent means v0.1 (conventions V1).

E4b adds `account.admin` to the closed permission set [C5/policy-account-admin]. It is checked on every account
administrator request after a reload. `auth.account_password_min_length` is a non-timer key: step 2's existing
restart rule applies [C5/config-account-password-min-length]. No new policy-reload login-end behavior is defined.

## Path

`config.yaml` (steps 1–7): CO-FS → CO-PW → (session-countdown) · `policy.yaml` (steps 1–2, 8–11): CO-FS → SF-CO → SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-FS | FS → CO | file (read) | `config.yaml` (`config.schema.json`, the knob sections) or `policy.yaml` (`policy.schema.json`), changed | an admin editing files on the host, out of band (plan §1.3 F7); the core | the core watches both files and rereads the one that changed, with no restart (Storage §2.4: "hot-reload of session timers and RBAC rules"; §8.1; §9). The watch is on the data directory, filtered to the file's name, with a debounce; a file is installed by rename, never in place. The core MUST (owed) | — | — |
| 2 | — | CO → CO | — | parse and validate | the core | a file that parses and validates holds the values in force from now on. In `config.yaml`, only timer keys reload: `timers.*` and the targets' timer overrides; any other key that differs from the running value is not applied, and the core logs a line naming the key and saying a core restart applies it. `policy.yaml`'s permissions and target filters reload as before (step 8; built at E1). A refused file changes nothing: the last accepted file stays in force; for `config.yaml`, a target's timer override that does not hold against it refuses that `config.yaml`, never the target file — the target file is fleet-discovery's case. The core logs the refusal, naming the key or the target id | the core MUST refuse a `policy.yaml` that does not validate: a permission outside the set (nine at E4b, with `vault.admin`, `target.edit` and `account.admin` [C5/policy-target-edit; C5/policy-account-admin, owed U5]), a filter outside the four forms, a `roles` key (`C1/policy-storage-example`, e0; `C1/policy-broker-health`, e1; both reference, graded by U5 `schema/policy/policy_test.go`). It MUST refuse a `keepalive_probe_interval` not above 0 and below a positive `keepalive_interval` ("config load refuses it", session model §11; owed) | the operator, by a log line for a non-timer key change; a refused file is a log line |
| 3 | — | CO → CO | — | resolve each open connection's set | the core | the default, with the target's own value over it, for every value but the heartbeat pair; the pair is core-wide and the core MUST put the same pair on every connection (`timer_values`; owed). Ruled: v0.2 approval and the operator on 2026-10-01 (verifier C-2, tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`); session model §11 and Storage §8.1 still say every value takes a per-target override | — | — |
| 4 | CO-PW | CO → PW | unix socket (control) | `timers`, carrying a full `timer_values` | the core | on every open connection whose set changed, toward a worker at v0.2 or later (`timers`: "sent on a change (a config.yaml reload)"; session model §15). The core MUST send all eight values, each in range, and never the core's own (`C3/timer-values-shape`, e1); toward a worker at 0.3 or later also `keepalive_probe_interval` in range, toward a 0.2 worker never (`C3/timers-keepalive-probe`, e2; `timer_values`; D28, D29, open: Open item 10). A v0.1 worker gets no `timers` (ipc-control top). When `heartbeat_interval × heartbeat_missed` grows, `timers` goes to every connection before the core's pinger slows; when it shrinks, the pinger speeds up first. A change of a target file's timer override is a reload that sends `timers` (Storage §8.1: "with per-target overrides, hot-reloaded") [C3/config-reload-timers, reference: its U2 test and the U11 rig are owed] | a worker at 0.3 or later answers an out-of-range value with `error` `bad_request` (`error`), and the frozen v0.2 worker refuses a key set not exactly its eight (`timer_values`). The core's next step: unspecified (Open item 4) | that connection's worker |
| 5 | — | PW → PW | — | keep the set | worker | the worker MUST keep the last set it received and run this connection's timers by it, so they keep running while the core is away; for the heartbeat pair it uses the latest pair received on any of its connections (`timers`, `timer_values`; Storage §8.1). A connection whose `timers` was refused keeps the set it already ran by [C3/config-reload-timers, owed] | — | — |
| 6 | — | CO → CO | — | the core's own timers take the new values | the core | `expiry_notice`, `quiet_prompt`, `ws_heartbeat`, `worker_heartbeat`, `supervisor_heartbeat`, and `idle` and `unattended` while the core is present (session model §11, "Evaluated by"); the first two are never sent (`timer_values`). `heartbeat_interval` also paces the alert stream's comment line (→ operator-alerts). A v0.1 worker's sessions get `idle` and `unattended` from the core as `close_channel` (ipc-control top). A bound shortened below a clock already run: Open item 6 | — | — |
| 7 | — | CO → CO | — | → session-countdown | the core | the countdown is the soonest timer under the values in force (session-record `ends_at`), so a changed value moves it; any `notice` follows session-countdown's rule (ws-frames `notice`) [C3/config-reload-timers, reference: its U2 test and the U11 rig are owed] | — | as session-countdown says |
| 8 | — | CO → CO | — | the new permissions and target filters | the core | the core MUST check every request after the reload against the policy in force (Storage §2.4, §9; Contract §1, §13). Owed: no fixture reloads a policy. How policy is cached and invalidated stays OPEN (open_decisions §3) | each operation's own refusal (→ session-open, session-attach, session-end-by-tsb, client-remove, operator-alerts) | the requester |
| 9 | SF-CO | CO → SF | WebSocket | → session-attach: close 4003 `forbidden` | the core | a ticket minted before the reload and presented after it by a principal that "may no longer join this one": the core MUST close 4003 (ws-frames `x-attach-order`, `x-close-codes`). Owed: `C2/attach-ticket-wrong-session-close-4003` (e0) grades the wrong-session case only | — | that client |
| 10 | SF-CO | CO → SF | HTTPS (server-sent events) | → operator-alerts: the end of the alert stream | the core | a reload that removes `broker.health` from a principal, or removes the principal: the core MUST end that principal's stream, since "Authorization holds for the stream's life" (OpenAPI `alerts`). Fixture: `C1/alerts-stream` (reference; U2 stream test) | — | that stream's surface |
| 11 | — | CO → CO | — | a client already attached whose principal lost its join permission or the session's target, or a keyboard holder whose principal lost `session.join.rw` | the core | unspecified (plan §7 G-8; Open item 1) | — | — |

**What a reload does not reach.** `buffer_bytes` and `input_frame_max` travel on `connect` only and are fixed for
the connection's life (`buffer_bytes`, `input_frame_max`): a changed value reaches connections opened after the reload,
whose `connect` carries the values then in force (→ session-open). The same `timers` sent at reattach is core-restart's; config-reload owns
only the change trigger. A target file that does not validate is fleet-discovery's case, not this one: its entry is
dropped from the index, and its open connections keep the timers they run by — no `timers` is sent for it (rule 9;
→ fleet-discovery).

## Refusals and who is told

- **A refused file** (step 2) is told to nobody by any contract message. The audit vocabulary has no reload event:
  `config_event` is `config.recording_state`, written at start (`C6/event-recording-state`). `alert_kind` has no
  kind for it (common `alert_kind`). Open item 3.
- **A worker's `bad_request`** to `timers` (step 4) reaches the core only. Open item 4.
- **Northbound**, a reload refuses nothing by itself. A principal learns of a narrower policy only through a later
  request's refusal (steps 8, 9) or the end of its alert stream (step 10). No consumer step follows from a reload.

## Security considerations

- **Policy is authorisation.** RBAC enforcement is the core's (Contract §1); whether an identity may reach a target is
  broker-owned (Contract §13). A reload is how access is granted and withdrawn, and today a withdrawal reaches later
  requests (steps 8–10) but not a client already attached (step 11, G-8). The verifier's words: "a policy revoke that
  a live client survives is not a revoke".
- **Who changes it.** Only an admin writes `policy.yaml` (policy `$defs/permission`: "superadmin-only means only an
  admin writes policy.yaml"), on the host and out of band. No TSB API writes either file: authentication "never grants
  shell, filesystem, or configuration access to the TSB host" (Contract §4).
- **A broken file.** What a refused, removed or half-written file leaves in force decides whether a typo can grant or
  withdraw access. Unspecified (Open item 2).
- **No record.** A change of permissions leaves no audit event (Open item 3).
- **Recording.** Users are warned at session open when recording is active (open_decisions §5, LOCKED). What a `policy.yaml`
  reload of `recording.enabled` does to sessions already open is unspecified (Open item 8).
- **No credential travels.** `timers` carries timer values only; credentials travel only inside `connect.auth`
  and, from 0.7, `auth_answer` (ipc-control top).

## Open items

1. **G-8** (plan §7.3; REAL per the verifier). A client attached when a reload removes its right to join, or the
   keyboard holder when it loses `session.join.rw`, keeps watching and perhaps typing until it leaves. The `clients`
   frame, which carries `may_control` ("the client's principal holds session.join.rw", ws-frames `clients`), goes out
   on attach, detach and removal only, not on a reload. The verifier's options: **(a)** on reload, close a client that
   may no longer join with 4003, and move the keyboard off a holder that lost `session.join.rw`, with a `token` reason
   the contract names and `clients` updating `may_control`; **(b)** rights are checked at attach only, and the contract
   says so. The verifier recommends (a), matching the alert stream. Not ruled.
2. **Closed — a broken file.** The last accepted file stays in force: a refused reload, a removed file or a
   half-written one changes nothing (step 2).
3. **Closed — telling anyone.** A reload is told to the operator by a log line naming the key; a refused file by a
   log line naming the key or the target id (step 2), with no alert kind for it at this release; no northbound
   message names a reload, and no audit event exists for one (`config_event`'s one action is
   `config.recording_state`; `alert_kind`).
4. **Closed — a worker refuses `timers`.** The connection keeps the set it already ran by, and the core logs the
   refusal, naming the key (steps 2, 5).
5. **Closed — where a target's own value lives.** In the target file, as `inventory-target`'s `options`
   (`keepaliveInterval`, `keepaliveCountMax`, and the rest of the overrides). A change to that file is a reload that
   sends `timers` (Storage §8.1: "with per-target overrides, hot-reloaded"): step 4.
6. **OPEN — a shortened timer** (unspecified; not in plan §7). Session model §11 states each timer as a clock against
   a bound ("Fires when"), so a reload moves the bound and keeps the clock. A session or connection already past a
   shortened bound (`idle`, `unattended`, `orphan_timeout`, `connection_linger`, `link_timeout`) would end at once. No
   clause says whether a reload may do that. The red-team's recommendation (never fire at once; give one
   `expiry_notice` window) is not ruled.
7. **Closed — requests in flight.** A request checked before a reload and answered after it: the answer stands
   (beside open item 1).
8. **`recording.enabled`** (unspecified; not in plan §7). Whether a `policy.yaml` reload changes recording for
   sessions already open or only later ones. `config.recording_state` is "written once when the core starts"
   (audit-event top; C6/event-recording-state), so a reload writes none; queue fill S58's "one
   `config.recording_state` line per reload" would change that text.
9. **Roles** stay OPEN (open_decisions §3) and are absent from `policy.yaml` on purpose (policy top).
10. **Approval items leaned on, not agreed:** D28 (C3 `proto_version` "0.3") and D29 (`keepalive_probe_interval`
    sent only to peers at 0.3 or later), step 4; open in the approval page's southbound set (queue A6).

## Picture

pending
