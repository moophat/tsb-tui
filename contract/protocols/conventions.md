# Conventions — the rules every TSB protocol obeys

**Status:** normative, 2026-10-07, released with the schemas and fixtures of the same release, under the rules page [`README.md`](README.md).

A **convention** is a rule that holds on every edge, written once here and cited by number, V1 to V16 (plan §4,
tsb-core `plans/261007-0835-tsb-protocols/plan.md`). **Every protocol obeys every V**; a document cites the V a step leans on
and never restates it. Terms, codes, edges, normative words and IDs are the rules page's. Each V gives the rule, what
**providers** (core, protocol worker, VT worker, supervisor) and **consumers** (surfaces, any API client) must do, the
contract's **registry** (no value list is copied here) and the grading fixtures. Schemas: `contract/schemas/` in
this release, `common#/…` = `common.schema.json#/…`; fixtures `C<n>/<name>`:
`contract/fixtures/`; corpus: `srs/`. **Owed**: no fixture grades it yet. **Reference, test owed**:
the fixture exists, the provider test its `graded_by` names does not. A reference fixture is graded
only by the tests `fixtures/graded-by.json` names for it, each checked against the run's own results
(`tsbc verify --provider-results`): `graded_by` is description, the index is what is checked.

## V1 — Versioning and additivity

Every seam grows by addition only: a later tag adds a message, a frame type or an optional field, never a new
meaning for an old one (immortality §6.4, LOCKED). WebSocket text frames carry `v`, 1 at this tag and kept (ws-frames
top; D1). C3 peers carry `proto_version` "0.8" on `hello`, `hello_ack`, both manifests and `worker_created`, and
**absent means v0.1** (`common#/$defs/proto_version`; ipc-control top); C4 carries it on `hello`/`hello_ack`
(`vt-ipc#/$defs/proto_version`); the C3 data frame has a version byte (`ipc-data-frame#/x-binary-layout`). REST has
none but `Ticket.v` (const 1, "so a later token format can be told apart": OpenAPI `Ticket`; `C1/ws-ticket-mint`);
responses are closed (OpenAPI `info`). A field added to a message is optional in its schema; what a newer
sender must send is graded by fixtures, never by `required` (CHANGELOG v0.3, opening).
- **Providers** MUST NOT change the meaning of an existing message, field or value. The core MUST speak N and N-1 at least on
  C3 — 0.8, 0.7, 0.6, 0.5, 0.4, 0.3 and 0.2 at this tag (immortality §6.4: "N and N-1 minimum") — sending a 0.3 peer exactly v0.3's messages and a 0.2 peer exactly v0.2's (CHANGELOG v0.3 C3 and this release's C3; D28), and only keys its version knows:
  `keepalive_probe_interval` only to peers ≥ 0.3 (D29), `open_shell`'s `open_timeout` only to peers ≥ 0.6, and the device-login messages (a prompt-mode `connect` with its `login_timeout`, `hostkey_answer`, `auth_answer`) only to peers ≥ 0.7. Meeting an unknown `proto_version` it MUST send `hello` with
  its own; `unsupported_proto` or `unknown_cmd` in answer makes the sessions `unreachable` / `protocol`, never killed
  (README "Senders and receivers"; §6.4; the contract's own scope for `unreachable` disagrees: open item 17). A
  data-frame receiver MUST refuse an envelope version it does not speak.
- **Consumers** MUST ignore a known `t` with an unknown `v` as an unknown `t` (the attach first-frame rule wins:
  4000); with no `prefix` frame, output follows `attached` (ws-frames top; README "Senders and receivers").
- **Graded by** `C2/frame-versioning`, `C3/n1-v0.2-worker-positive` (reference, tests owed); `C3/n1-v0.3-worker-positive`;
  `C3/n1-v0.5-worker-positive` (reference, tests owed; published e3, required green e4); `C3/hello-unsupported-proto`; `C3/n1-v0.1-worker-unreachable`; `C3/control-message-shape-v0.2`; `C3/control-message-shape-v0.4`;
  `C5/manifest-proto-version`; `C5/supervisor-manifest-proto-version`; `C1/create-refused-v0.1-workers`;
  `C3/timers-keepalive-probe`; `C3/open-timeout-version-gate` (published e3, required green e4); from Release B `C3/n1-v0.6-worker-positive`, `C3/device-login-version-gate`, `C3/control-message-shape-v0.6` and `-v0.7`, `C2/ws-frames-shape-device-login` (all published e3, required green e5; the first two reference, tests owed); schema additivity by `contract/tests/test_additivity.py` (tooling). Consumers: owed.

From C3 0.8, only capable peers receive seal_output/release_accounted and the output_drain_timeout key;
older peers receive exactly their old messages [C3/terminal-disposal-version-gate].

## V2 — Closed shapes; receivers ignore what they do not know

A sender emits exactly the shape its tag defines; a receiver ignores fields, frame types and events it does not know,
so a later tag never breaks an earlier peer (contract `README.md` "Senders and receivers"; ws-frames, ipc-control,
supervisor, vt-ipc tops). How to act on an unknown *value* sits with its registry: V1, V3, V4, V5.
- **Providers** MUST emit only declared fields (a broker path in a session record fails). A worker, supervisor or VT
  worker MUST answer an unknown command with `error` `unknown_cmd` and keep the connection; a malformed line is
  `bad_request`, so a later core can tell an older peer from a broken request (ipc-control top;
  `supervisor#/$defs/error`; `vt-ipc#/$defs/error`). The core MUST ignore an unknown C3 `event` or field.
- **Consumers** MUST ignore unknown fields, frame types and events, alert-stream event types included (OpenAPI
  `/alerts`, where `x-itemSchema` is a stated OpenAPI 3.1 deviation typing each event: CHANGELOG v0.2 O8).
- **Known breach**: the frozen v0.2 worker refuses an unknown `timers` key (CHANGELOG v0.3 known gap #114; V1, D29).
- **Server-computed frames.** `notice`, `quiet_prompt`, `token`, `input_refused` and `data_path` are computed by the
  core alone; a surface only renders them. None carries a delivered position: there is no client-side ack or resume
  frame (ws-frames top; decision 5.15).
- **The target file and import preview definition are open shapes**: the preview carries the target-file shape in a response. Storage §2.1 makes it drift-friendly, so a reader ignores what it does
  not use; it refuses the secret keys Storage §3.5 removed [C5/inventory-target-valid].
- **Graded by** `C2/unknown-frame-ignored`; `C3/control-unknown-cmd`; `C3/supervisor-unknown-cmd`;
  `C4/vt-unknown-cmd` (reference, tests owed); `C3/supervisor-bad-request`; `C5/manifest-closed-v0.1`; the shape
  fixtures `C1/session-record-shape*`, `C2/ws-frames-shape*`, `C3/control-message-shape*`,
  `C3/supervisor-request-shape*`. The core ignoring an unknown event, and consumers: owed.

## V3 — Close codes

A **close code** is the number a WebSocket close carries (RFC 6455). The session stream ends only with a code from
one registry, each naming its cause: a `killed` end closes 4010, a `terminated` one 4012 (session model §12); 4013 is
retired vocabulary (`x-close-codes` 4014; session model §15). Which protocol sends which: plan §6.2.
- **Providers**: the core MUST close with the code the registry gives for the cause, and only with a listed code.
  OPEN: the registry has no code for a failure of the core's own, and the built core closes 1011 there (open item 13).
- **Consumers** MUST treat an unknown 4xxx as 4010 and MUST NOT auto-reattach after 4014 (README "Senders and
  receivers"; ws-frames top), nor, from the vault release, after 4015 `device_revoked`: the client's login was ended by
  a revocation of the device it is tied to, and its session lives on (device-trust step 12). An earlier client reads
  4015 as 4010, so it does not reattach either (red-team LOW-11). From Release B, nor after 4016 `device_login_ended`: the device login this
  socket held ended with no session (the `device_login_result` before it says how), so there is nothing to reattach to, and
  a client may start a new device login. An earlier client reads 4016 as 4010.
- **E4b:** the core MUST close an attached client whose login an account act ended with 4017
  `account_login_ended`; the terminal session lives on. A device-login socket gets 4017 with no result frame; no session exists yet, so the session-lives-on clause has nothing to apply to. Consumers MUST NOT auto-reattach; an older consumer reads
  it as 4010 and does not reattach either [C2/account-login-ended-close-4017;
  C2/account-device-login-ended-close-4017].
- **Registry** `ws-frames.schema.json#/x-close-codes`.
- **Graded by** `C2/attach-first-frame-invalid-close-4000`; the three `C2/attach-*-close-4001`;
  `C2/attach-ticket-wrong-session-close-4003`; `C2/session-closed-close-4010`; `C1/session-ended-unexpectedly`
  (4012, `worker_lost`); `C1/client-remove-by-owner` (4014); `C7/revoke-default-ends-logins` (4015, reference, test
  owed); from Release B `C2/device-login-cancel` (4016, reference, test owed). 4012 for `target_closed` and `connection_lost`
  (`C3/ends-from-outside` grades the audit only: `tsbc` check `outside-ends`), 4004, 4011 (plan §7.4), consumers: owed.

## V4 — Error codes

A non-2xx JSON response on the REST API carries the error body: its `code` is the contract, with one HTTP status;
its `message` is human text without host detail, V11 (`error.schema.json` top). Local seams keep their own refusal
vocabularies, which no consumer sees.
- **Providers**: the core MUST answer with a registered code at its listed status and MUST NOT invent one; `target_id_taken` is a create’s 409 when any source already holds the id [C1/target-create-id-taken-409]. Only
  `connection_choice_required` carries `connection_ids` (error top). From the vault release: only
  `credential_choice_required` carries `candidates`, only `unlock_failed` carries `attempts_remaining`, only
  `vault_wipe_blocked` carries `credential_refs`, only `mapped_credential_missing` carries `credential_ref`; the vault's codes (`vault_locked`, `vault_not_initialized`,
  `vault_exists`, `vault_not_minted`, `unlock_failed`, `part2_missing`, `challenge_failed`, `credential_exists`,
  `credential_has_grants`, `broker_unlock_unavailable`, `vault_wipe_blocked`, `mapped_credential_missing`, `org_credential_locked` at 409; `device_not_trusted`, `screen_unlock_failed` at 403;
  `device_not_found`, `credential_not_found`, `grant_not_found`, `user_not_found` at 404) are never 401, which a surface
  reads as a lost login (OPEN-1.4, ruled). An E3 surface meets them by their status. A local peer MUST refuse from its seam's enum.
  The core MUST NOT act on free text: a VT `error`'s `message` and a C3 end's `reason` are for the log, never parsed
  for control (`vt-ipc#/$defs/error`; ipc-control top; Contract §6). No message on the create path echoes a value of the
  request: every one is fixed text, and a `SessionCreateRequest.login` above all is never in any answer (session-open
  step 1; V10). From Release B no device-login message echoes an answer either, and the two codes it extends keep their
  status: `rate_limited` (429) also answers a prompt-mode create past the principal's device logins in progress, and
  `worker_unavailable` (503) one when no supervisor provides a worker at proto_version 0.7 or later (`error.schema.json`
  `x-codes`; no new code). On C2 `request_refused` gains the reason `invalid` (a device-login answer of the wrong count or
  length), and every refusal leaves the prompt outstanding.
- **Consumers** MUST handle an unknown code by its HTTP status (error top; README "Senders and receivers").
- Only `internal_error`, `inventory_unavailable`, and `import_conflict` may carry `import_progress`; other codes MUST NOT carry it.
- **Registry** `error.schema.json#/x-codes`, `#/$defs/code`; OpenAPI `components.responses`;
  `ipc-control#/$defs/error` (`code`), `supervisor#/$defs/error` (`reason`), `vt-ipc#/$defs/error` (`code`).
- **Graded by** each code's C1 fixture, named in its protocol (e.g. `C1/auth-login-bad-password`,
  `C1/output-page-401`); `C1/error-codes-vault` (every vault-release code in the error body);
  `C1/session-create-login-never-echoed` (reference, owed by the core plan for a supplied login); from Release B `C1/session-create-device-login-limit-429`, `-needs-v0.7-503`, `C2/device-login-answer-refused` (all reference, tests owed; published e3, required green e5); `not_implemented` has none (scroll-control rule 1); `tsbc` fails a live error whose message names a host path (error top). Never acting on free text, consumers: owed.

E4b registers 409 `user_exists`, `account_self_disable`, `account_already_enabled`, `account_disabled` and
403 `password_change_failed`. `user_not_found` (404) also covers account mutations. Own-change failures never
answer 401; the existing login limiter answers 429. Account 422 text names a violated rule and never echoes a value.
The core MUST use these answers [C1/account-create-name-exists-409; C1/account-create-disabled-name-exists-409;
C1/account-disable-self-409; C1/account-enable-already-enabled-409; C1/account-password-set-disabled-409;
C1/account-password-change-wrong-current-403; C1/account-password-change-limited-429;
C1/account-name-shape-422; C1/account-name-reserved-422; C1/account-password-empty-422;
C1/account-password-utf8-422; C1/account-password-too-long-422; C1/account-password-too-short-422].
Already-disabled disable is a 204 no-op, not an error [C1/account-disable-already-disabled].

## V5 — States and reasons

Every state (where something is) and reason (why it happened) comes from one schema enum; a document names the
`$def`, never the values.

| Registry | Where | A receiver meeting an unknown value |
|---|---|---|
| session state | `common#/$defs/session_state` (`session-record` `oneOf` narrows it) | the session is not attachable (README) |
| session type | `common#/$defs/session_type` | an opaque type, never an error (README) |
| target type | `inventory-target` `type` (OpenAPI `TargetRecord`) | an opaque type, never an error |
| target login hint | OpenAPI `TargetRecord/$defs/login_hint` | suppress the hint; retain the target and normal connect flow, never infer none [C1/target-login-hint-older-core-and-unknown] |
| end state × reason | `audit-event#/$defs/session_end` (its `oneOf` pairs them); `continuity-state#/$defs/session_record`; `spool-entry`; `ttyrec#/$defs/end_marker`; session model §12 | not on a consumer wire: it sees state and close code (V3) |
| countdown | `session-record` `ends_by` | show `ends_at` (README) |
| token move | `ws-frames#/$defs/token` `reason`; `audit-event#/$defs/session_control` | read `holder` (README) |
| refused input | `ws-frames#/$defs/input_refused` (C2); `ipc-control#/$defs/input_refused` (C3) | input was refused (README) |
| size answer | `ws-frames#/$defs/size` `reason` | `cols`/`rows` are the size in effect (README) |
| refused request, stale screen, data path | `ws-frames#/$defs/request_refused`, `stale`, `stale_choice`, `data_path`; `audit-event#/$defs/session_stale_choice` | unspecified (not in plan §7) |
| durable stale cause | `continuity-state#/$defs/session_record` `stale_cause` | show the stale notice without a cause (its description; open item 5) |
| C3 link and ends | `ipc-control#/$defs/link_state`, `connection_lost` `cause`, `channel_closed` `by` | `cause` → `transport`, `by` → `target` (ipc-control top); `link_state` unspecified (not in plan §7) |
| alert | `common#/$defs/alert_kind`, `alert_subjects`; OpenAPI `AlertEvent` | TSB's own alert, never host state (README) |
| terminal read outcome, integrity kind/reason | `ipc-control#/$defs/output_seal`; `audit-event#/$defs/recording_integrity_detail`; `ttyrec` `x-terminal-incomplete-line` | unknown/absent proof withholds accounted disposal, never EOF/zero; [C6/recording-integrity-event-shape; C3/control-message-shape-v0.8] |
| audit action; permission | `audit-event.schema.json` `$defs` (including `target_write_event`: `target.create`, `target.update`, `target.delete`); `policy#/$defs/permission` (V9), ten permissions with `target.edit`, `target.private` and `account.admin`, and E4b `account_event` actions and host-only `account_recover_event` (`account.recover`) | —; a permission outside the set is refused |
| import format | OpenAPI `TargetImportFormat`; `import-receipt` | refuse unknown request values; do not act on an unknown response format |
| share entry kind | `common#/$defs/share_entry` | refuse a request entry of a kind this release does not know (422); a reader meeting one matches no principal and shows it as text |
| import action | OpenAPI `TargetImportRequest` | refuse an unknown request action |
| import outcome and credential choice, destination and class | OpenAPI `TargetImportResult`, `TargetImportCredentialDisposition`; `import-receipt` `choice` | refuse unknown request values; do not infer success, credential handling, destination or class from unknown response values |
| import issue kind and reason | `import-receipt#/$defs/issue` | display a generic “not carried”; do not infer the kind or reason |
| import row change, credential kind and stored layer | OpenAPI `TargetImportPreview`; `import-receipt` `plan`, `stored_credential` | do not act on an unknown change, credential kind or layer |
| import mapping states | `import-receipt#/$defs/plan_mapping`; OpenAPI `TargetImportMapping` | do not act on or infer a mapping from an unknown state |
| import receipt stage, audit and index progress | `import-receipt` `stage`, `audit`, `index`, `progress` | do not infer completion or act on unknown progress values |
| login failure audit reason | `audit-event#/$defs/auth_event` `detail.reason` for `auth.login_failed`: existing `invalid_credentials`, plus E4b `disabled` | audit-only registry; northbound answer remains `invalid_credentials` [C6/event-login-failed-disabled] |
| account disabled flag | `users.schema.json` and OpenAPI `AccountSummary` (boolean, not an enum); absent in an older stored record means enabled | local record loader applies absent = false; account summaries always carry the flag [C6/users-old-record-loads-enabled; C1/account-list] |
| device login outcome (consumer) | `ws-frames#/$defs/device_login_result` `outcome` | a failed device login (device-login) |
| device login host-key kind (consumer) | `ws-frames#/$defs/device_login_hostkey` `kind` | send `device_login_cancel`, say the surface is too old for this device login, and never offer Accept (device-login) |
| device login prompt method (consumer) | `ws-frames#/$defs/device_login_prompt` `method` | send `device_login_cancel` and say the surface is too old for this device login (device-login) |
| auth failure code (core) | `ipc-control#/$defs/auth_failed` `code` | read as `refused` (ipc-control rule 7) |
| auth prompt method (core) | `ipc-control#/$defs/auth_prompt` `method` | answer `disconnect`; the device login ends `connect_failed` (ipc-control rule 7) |
| host-key ask kind (core) | `ipc-control#/$defs/hostkey_ask` `kind` | answer `disconnect`; the device login ends `connect_failed` (ipc-control rule 7) |

- **Providers** MUST send only values their tag lists. **Consumers** MUST apply the last column where it has a rule.
- **Graded by** `C6/state-vocabulary`; `C6/event-vocabulary`; `C6/event-vocabulary-v0.3` (reference, test owed);
  `C6/event-session-ended`; `C1/session-record-shape*`; `C2/ws-frames-shape-v0.3`; `C3/control-message-shape-v0.2`; `C3/control-message-shape-v0.4`;
  `C1/policy-storage-example`; `C1/policy-broker-health`; from Release B `C2/ws-frames-shape-device-login`,
  `C3/control-message-shape-v0.7`, `C3/auth-failed-codes` (reference, test owed), `C2/device-login-hostkey-first-use` (reference, test owed). Consumers: owed.

`recording.output_incomplete` is the recording_integrity_event action, including explicit unrecorded
context after lifecycle closure; it is never another session.disconnect [C6/recording-integrity-unrecorded].

## V6 — Common shapes

A shape more than one seam uses is defined once in `common.schema.json` `$defs` and referenced (its top); at this tag
`engine` and `proto_version` are each defined twice (open item 14). `id` and
`username` are path-safe, because they become file names (OpenAPI `SessionId`, `ClientId` use `id`). **`u64`** (seq,
byte counts) exceeds 2^53: a reader MUST decode it as a 64-bit integer (README "Validator subset"). **`timestamp`** is
RFC 3339 UTC with `Z`, which every writer MUST write, never 29 February of a common year; `timestamp_any_zone` only
reads back an older core's C6 clock. Binary stamps are u64 nanoseconds since the Unix epoch, UTC
(`ipc-data-frame#/x-binary-layout`; `ttyrec#/x-derivation`). `cols`/`rows` are u16 from 1, never 0
(1 to 65535: `common#/$defs/cols`, `rows`); `term`, `fingerprint`, `ticket`
(V10), `duration_ms`, `count` (V7), `proto_version` (V1) and the enums (V5) complete the set.
- **Graded by** the shape fixtures, notably `C3/control-message-shape-v0.2` (a u64 past 2^53),
  `C6/continuity-record-valid` (an offset clock read back, a zoneless one refused), `C3/data-frame-envelope`,
  `C8/ns-derivation-in`, `C8/ns-derivation-out`, `C3/supervisor-request-shape`. Consumers' decoding: owed.

## V7 — Knobs, not defaults

A **knob** is a `config.yaml` value. What varies between deployments is a knob, never an implementer's judgment
(decision 5.36, as the contract cites it). The contract fixes a knob's field and range, never its value
(`common#/$defs/duration_ms`, `count`); a default the corpus names (session model §4, §11; terminal-size §5) lives in
`config.yaml`, never in a schema. Every session timer takes a per-target override (session model §11), except:

**The heartbeat pair is core-wide (ruled).** `heartbeat_interval` and `heartbeat_missed` scope the worker, which
holds connections to many targets: every connection carries the same pair, and a worker uses the latest it got on
any (`ipc-control#/$defs/timer_values`). Ruled in the contract-v0.2 approval, DEFAULT 12
(tsb-core `plans/260918-1658-e1-contract/approval.md`, 2026-09-29), confirmed 2026-10-01
(tsb-core `plans/reports/rulings-261001-1311-e1-protocol.md`); verifier C-2. **Stale corpus lines**, awaiting a vault fix:
session model §11 ("A target's own value overrides it") and Storage §8.1 ("with per-target overrides").

**The terminal drain is connection-wide (ruled).** worker.output_drain_timeout is the second exception
beside the heartbeat pair: no per-target override, required runtime key, optional additive config/wire
field, no contract default. Positive whole milliseconds within Go duration and duration_ms ranges;
active attempt snapshots the deadline, reload affects next attempt. Worker remembers the last received
value while core is absent [C5/config-output-drain-timeout, U5/U2/U3 tests owed].

| Knob | Contract field and range | Where |
|---|---|---|
| `heartbeat_interval`, `heartbeat_missed` (core-wide); `orphan_timeout`, `idle`, `unattended` | `duration_ms`; `count`; `duration_ms` | `ipc-control#/$defs/timer_values` |
| `keepalive_interval`, `keepalive_count_max`; `connection_linger` | ms or 0 (`link_timeout` off); `count`; ms or 0 (close with the last session) | same |
| `recording.input_seq_reservation_size` (core-wide) | integer 1 to 2^53-1; deployment template supplies default 1024, never schema/code; overflow or failed reservation refuses input | `config.schema.json#/properties/recording` [C5/config-recording-input-seq-reservation, U2 provider test owed] |
| `worker.output_drain_timeout` (connection-wide) | positive whole-ms Go duration on config, duration_ms on C3 >=0.8; no default | `config.worker`; `ipc-control#/$defs/timer_values` |
| `keepalive_probe_interval` | `duration_ms`, optional; > 0 and < `keepalive_interval` when that is > 0; peers ≥ 0.3 | same |
| worker ring per PTY session; largest input frame | `buffer_bytes`: `count`; `input_frame_max`: 1 to 2^32-1; both on `connect`, fixed for the connection; no worker-wide cap | `ipc-control#/$defs/buffer_bytes`, `input_frame_max` |
| `worker.max_connections`; `worker.placement`; never-dialled bound; reap bound | `n_max`: 1 to 65535; `placement_key`; `undialled_timeout`: `duration_ms`, optional; `reap_timeout`: `duration_ms` | `supervisor#/$defs/create_worker`, `kill_worker` |
| VT scrollback per grid; C4 message size | `scrollback_bytes`: 0 to 2^53-1, on `open`; `max_message_bytes`: 1 to 2^32-1 | `vt-ipc#/$defs/scrollback_bytes`, `max_message_bytes` |
| ticket lifetime | `Ticket.expires_in`: 1 to 60 s | OpenAPI `Ticket` |
| `worker.open_timeout`, the bound on a shell's open | `open_timeout`: `duration_ms`, optional; peers ≥ 0.6; per-target (`options.open_timeout`, integer seconds) | `ipc-control#/$defs/open_shell`; `config.schema.json#/properties/worker` |
| `sessions.ended_retention` (core-wide) | positive Go duration; no earlier removal of fully finalised ended metadata, no per-target/test-login exception; templates supply 168h, no schema/code default | `config.schema.json#/properties/sessions`; session-list rule 10 [C5/config-sessions-ended-retention, U2 test owed; published e4, required-green e5] |
| `inventory.keep_versions`, `inventory.staging_max_age` (E3) | `count`; a positive Go duration string | `config.schema.json#/properties/inventory` |
| `import.max_bytes`, `import.max_entries`, `import.max_yaml_depth`, `import.preview_ttl`, `import.receipt_retention` | decoded-byte bound, count, nesting bound, positive durations; all required at core startup, no code defaults | `config.schema.json#/properties/import`; target-import |
| `vault.challenge_ttl`; `vault.unlock_attempts`, `vault.unlock_window`; `vault.part2_ttl` (E4) | 1 to 60 whole seconds (the challenge's `expires_in`); 1 to 100, a positive duration; a positive whole-second duration | `config.schema.json#/properties/vault`; OpenAPI `DeviceChallenge` |
| `locks.screen_after`, `locks.vault_on_idle`, `locks.vault_idle_after`, `locks.vault_hard_cap`, `locks.activity_interval` (E4) | positive whole-second durations, a boolean; the vault idle timer never above the hard cap (config load refuses it) | `config.schema.json#/properties/locks`; OpenAPI `LockSettings` |
| `auth.account_password_min_length` (E4b) | integer 1–1024 characters; deployment DEFAULT 15, no schema default; only newly set passwords, never existing ones; non-timer restart rule in config-reload | `config.schema.json#/properties/auth/properties/account_password_min_length` [C5/config-account-password-min-length; C1/account-password-too-short-422] |
| `auth.login_lifetime`, `auth.login_idle` (E4) | positive durations; a login ends at the first | `config.schema.json#/properties/auth` |

**Knobs named with no contract field:** `expiry_notice`, `quiet_prompt` (the core's alone; `timer_values`); the
WebSocket heartbeat (ws-frames top); output held before a prefix, then 4011 (`ws-frames#/$defs/prefix`); the attach
wiggle's switch (`ws-frames#/x-attach-order`); size floor, ceiling, threshold, coalescing window, floor = ceiling
pinning (D36; terminal-size §3); auto-release, seen only as `token` reason `release` (D32); the VT wait for a ground
point (`vt-ipc#/$defs/prefix`); snapshot cadence `snapshot.every_kb`, `.every_sec`, `.on_alt_screen` (immortality
§5.2; `vt-ipc#/$defs/screen_change`); the wait for a whole snapshot (answer and blob) a commit needs now,
`snapshot.wait_sec`, 1 to 600 s, `config.yaml` shipping 30 (grid-snapshot rule 12; U5 adds it); the page cap (OpenAPI
`GET /sessions/{id}/output`); the discovery keys, which have no contract field at this release and arrive with a
later item.
The alert-stream comment and `hello_ack`'s data-peer wait run on `heartbeat_interval`, no knob of their own (OpenAPI
`/alerts`; `hello`).
- **Providers**: the core MUST send the values in force, never a per-target heartbeat pair. A worker MUST refuse a
  `connect` lacking its values, and any out-of-range value, with `bad_request`, and MUST keep the last set received
  (`ipc-control#/$defs/connect`, `timers`). Worker, supervisor and VT worker MUST use only values they were sent.
- **Consumers** meet knobs only as effects (`ends_at`, a `size` answer, `expires_in`), never as values to assume. From the
  vault release the caller's resolved lock settings (`Principal.locks`, `Me.locks`) are values the core states, as it
  states `expires_in`: a surface takes its screen-lock time and activity interval from them, never from a local value
  (credential §5.5).
- **Graded by** `C3/timer-values-shape`; `C3/connect-carries-timers`; `C3/connect-without-timers-refused`
  (reference); `C3/timers-keepalive-probe`; `C3/open-timeout-shape`, `C3/open-timeout-version-gate` (both
  published e3, required green e4); `C5/config-open-timeout`, `C5/inventory-target-open-timeout` (both reference,
  tests owed; published e3, required green e4);
  `C3/create-worker-knobs-no-default`; `C2/size-request-clamped`,
  `C2/size-pinned` (contract `README.md` "Inputs"); `C2/attach-ticket-expired-close-4001`; `C5/config-e4-knobs`,
  `C5/config-e4-vault-knobs`, `C5/config-e4-auth-knobs`, `C1/principal-lock-settings` (reference, tests owed; E4). One pair
  everywhere: owed.

## V8 — Fixture marks

Every fixture carries a `mark`: `required-green: eN` (providers must pass it from eN), `published: eN` (surfaces may
build and mock against it from eN), or both; `--profile eN` asserts everything required green at or before eN, later
profiles being supersets (contract `README.md` "Results, grades, marks"). The e3b profile follows e4b and precedes e5; old fixtures retain their classifications. In a document every provider requirement
names its fixture or says owed (rules page); a `published`-only fixture is cited with the epoch it turns required. Grade
kinds and run output are tooling (`contract/README.md`; plan §5). The contract README's other tree-wide constraints
have no V here (open item 15).
- **Registry** `fixture.schema.json` `mark`, `#/$defs/epoch`. **Graded by** `tsbc` self-check (tooling).

## V9 — Roles

A step's role column says who may act, in the rules page's words: a **principal** is a login, a **client** one
attachment, a **surface** the software (component SF). The processes are named by component.

| Role | Who | Source |
|---|---|---|
| principal | a login, proven by TSB's own password login, no third-party IdP | rules page; Contract §1, §13; `common#/$defs/username` |
| client | one attachment of a surface to one session, named by `client_id` | `ws-frames#/$defs/attached`; `session-record` `clients` |
| owner | the principal holding the owner token, the creator: lifecycle authority, never auto-transferred | Contract §10; PTY §7.1; `session-record` `owner` |
| keyboard holder | the one attached client holding the control token (input and target size); none in escrow | Contract §10; PTY §7.1; `session-record` `control_holder` |
| view-only client | an attached client without the control token; one whose principal holds only `session.join.ro` never gets it | PTY §7.1; `session-record` `control_holder` |
| admin | a principal holding `session.terminate`; an end it causes has reason `admin` | Storage §9; session model §12 |
| `broker.health` holder | a principal granted `broker.health`: sees the operator alerts | `policy#/$defs/permission`; session model §11 |
| target editor | a principal granted `target.edit`: creates, replaces, deletes and reads definitions in the `tsb` source, subject to its `targets:` filters; `*` is a filter, never a permission | `policy#/$defs/permission`; target-write [C5/policy-target-edit; C1/target-write-forbidden-without-permission] |
| account administrator | a principal granted `account.admin`: list, create, disable, enable and set account passwords; every login may change its own password without that permission | `policy#/$defs/permission`; account-administration [C5/policy-account-admin; C1/account-list-forbidden-403; C1/account-create-forbidden-403; C1/account-disable-forbidden-403; C1/account-enable-forbidden-403; C1/account-password-set-forbidden-403; C1/account-password-change-own] |
| vault administrator | from the vault release, a principal granted `vault.admin`: stores org credentials and sets their unlock class, grants, rescopes and revokes grants, lists and revokes any principal's devices, wipes a user's vault (credential §8.3's admin; Q6 (a)) | `policy#/$defs/permission` |

Permissions and target filters are `policy#/$defs/permission`, `target_filter`, `user`, a closed set. How RBAC roles
are defined and assigned is OPEN (open_decisions §3; policy top), so "role" here is never an RBAC role.
- **Providers**: the core MUST check the actor's right on every request (Contract §1, §13: authorization is
  broker-owned), refusing by V3 or V4; a control-token move MUST NOT change the owner (Contract §10).
- **Consumers** MAY suppress what their principal may not do, as a guardrail, never the control (Frontend §8,
  "Surface attachment mechanism"); the owner's typing is typing-ownership's exception (verifier C-5).
- **Graded by** `C1/session-delete-not-owner-403`; `C1/client-remove-not-owner-403`;
  `C1/alerts-without-broker-health-403`; `C1/policy-storage-example`, `C1/policy-broker-health` (reference);
  `C2/handoff-refused`; `C2/size-request-not-typist`; `C2/quiet-prompt-owner-only` (reference); the vault
  administrator's 403s in `C7/vault-wipe`, `C7/org-credential-store-*`, `C7/grant-revoke`, `C7/device-list`
  (reference, tests owed; E4).

A principal granted `target.private` may manage only their own targets and who their share lists name; own-target visibility requires being named and an enabled owner, but not that permission or `target.discover` or a target filter. Session creation still requires `session.create` and a matching filter.

## V10 — Trust

Two doors, and from the vault release a third for one purpose. **Inside the host** the kernel vouches: SO_PEERCRED (each end of a unix socket learns the other's uid)
must give the TSB service uid; no token, no TLS, no identity handshake (immortality §6.1, LOCKED). **From outside**
a consumer logs in for a cookie and attaches with a one-time ticket (Frontend §8; OpenAPI `securitySchemes`). **The
device proof** (from the vault release): a trusted device signs a single-use challenge to register, to tie a login, to
unlock the vault and to regenerate Part 2 (device-trust, the challenge). It guards the vault unlock only: after it the
cookie alone uses the unlock until the vault locks, and the cookie stays unbound (decision 5.41; DEFAULT 41).
- **Providers** MUST accept a local peer only with the service uid, on every C3 and C4 socket, at both ends, and
  carry no claim token or secret there (ipc-control, supervisor, vt-ipc tops). The core MUST be the only dialler: the
  other side binds, the core dials and never accepts (tsb-core `docs/system-architecture.md` "Process topology"). OPEN: no
  contract text, corpus clause or ruling backs "never accepts" (open item 16). A target
  credential MUST travel only in `SessionCreateRequest.login` on C1 (a login supplied for one new connection),
  `device_login_answer.answers` on C2 and `connect.auth` and `auth_answer.answers` on C3; a raw export in the target import request is the explicit import exception, held only for that request and discarded after its answer. Credentials and raw exports are never in `create_worker`, argv,
  the environment, a manifest, the spool or a data socket (ipc-control top), and a supplied login or a device login's
  answer MUST NOT be logged, put in an audit event or echoed in any answer. The create, the device login and the core
  MUST store nothing of a login: a user who keeps one saves it into their own vault by a separate store request of their
  own (C7, `POST /vault/credentials`), which is the vault's (credential §10.2; OQ8). A confirmed target import may
  instead store a carried password through the import transaction in [target-import](target-import.md).
  From the vault release a vault secret travels SF → CO only inside a store request's `secret` (C7) or the raw export in
  an authorized target-import request, and CO → PW only in
  `connect.auth`; it is stored only sealed (`vault.schema.json`), never returned to a surface, and never in an audit
  event; the passphrase never crosses any wire (credential §12). The core MUST set `tsb_session` HttpOnly, Secure, SameSite=Strict; MUST
  accept an attach only by a one-time, short-lived ticket in the first frame, bound to principal and session
  (`common#/$defs/ticket`; `ws-frames#/$defs/attach`) or, from Release B, to principal and device login, redeemable only
  by `device_login_attach` (`ws-frames#/$defs/device_login_attach`), whose socket becomes that one session's attach
  when the device accepts; neither kind of ticket redeems the other (4003); MUST refuse a foreign-origin browser write to
  `POST /auth/login` and `/auth/logout` (OpenAPI `CrossOrigin`).
- **E4b account passwords:** a password in an account request MUST travel only in that request's body and MUST
  NOT be logged, audited, echoed or stored except as an argon2id hash [C1/account-password-never-echoed;
  C6/users-record; C6/event-account-create; C6/event-account-disable; C6/event-account-enable;
  C6/event-account-password-set; C6/event-account-password-changed]. The core MUST refuse every account write
  with a foreign Origin after cookie authentication and before account or request-input reads [C1/account-cross-origin-403].
- **Consumers** MUST hold the cookie in memory only (`securitySchemes.session`; decision 5.34) and MUST NOT put a
  ticket or token in a URL (`common#/$defs/ticket`; Frontend §8).
- **Graded by** `C3/control-message-shape*`; `C6/no-claim-token-field`; `C5/manifest-closed-v0.1`;
  `C3/supervisor-request-shape`; `C1/ws-ticket-mint`; the 4001 and 4003 fixtures (V3); `C1/auth-cross-origin-403`
  (reference); `C3/control-connect-supplied-login`; `C1/session-create-login-never-echoed` (reference, owed by the core
  plan for a supplied login); from Release B `C2/device-login-ticket-once` and `C2/device-login-ticket-kinds` (live),
  `C3/control-message-shape-v0.7`, `C2/ws-frames-shape-device-login`. Foreign-uid refusal, the only-dialler rule and consumers: owed.

## V11 — The host boundary

A consumer is a tenant of the broker, never a principal on its host. TSB's own state may be shown behind a
permission; the host itself — a pid, a path, a hostname, a process list — never (Contract §4).
- **Providers** MUST NOT put a pid, host path, hostname, software version or uptime (a shape version such as a
  frame's `v` is not one) into any northbound response, frame,
  error, alert or stream (Contract §4; error top; OpenAPI `Health`, `info`), nor copy what local manifests and
  `workers` replies carry. An alert names TSB components by id only (`common#/$defs/alert_subjects`). A target's
  device path is a broker-host path and is never northbound [C1/target-record-shape].
- **Consumers**: a surface, plugin or API client MUST NOT bridge a consumer to the broker host (Contract §4;
  Frontend §2.3).
- **Graded by** `C1/healthz`; `C1/session-record-shape` (no `recording_path`); `C1/alerts-stream` (reference);
  `C6/event-alert`; `tsbc`'s check of live error messages (error top). The rest: owed.

## V12 — Encodings

JSON is RFC 8259, with no `NaN` or `Infinity` (README "Validator subset"). Local control lines are one JSON object per
line, UTF-8, newline-terminated (ipc-control, supervisor tops); the audit log is append-only JSONL. WebSocket text
frames are JSON with `t` and `v`; binary frames are raw bytes, no envelope (ws-frames top). The alert stream is
server-sent events (an HTTPS response that keeps sending events), each event's data one JSON object. Binary layouts
are big-endian and normative in `x-binary-layout` (`ipc-data-frame`, `vt-ipc`), sidecar codes in `ttyrec#/x-sidecar`.
A schema pattern means what ECMA-262 says: `$` only at the string's end, ASCII classes (README "Validator subset").
- **Bytes and free text, from the vault release.** Every byte value on SF-CO is padded base64 (RFC 4648 §4), but
  `device_id`, which is a path segment and is written base64url without padding (§5); import `export_bytes` is padded base64, the opaque preview token is unpadded base64url, `import_id` is lower-case unpadded RFC 4648 base32 and `plan_digest` is lower-case hex. Vendor-password vector bytes are lower-case hex. In shape fixtures byte values are
  base64 synthetic constants; in the C7 vector files they are lower-case hex, as the standards print them (DEFAULT 44).
  Free text a person reads (references, labels, device names, login names) has a length and character-set bound in the
  schema (no control character) and is rendered as plain text, never as markup, by every surface (DEFAULT 59).
- **Providers** MUST emit these encodings; **consumers** MUST decode by them.
- **Graded by** `C3/data-frame-envelope`; `C3/data-frame-shape`; `C4/vt-frame-layout` (reference, tests owed);
  `C3/control-message-shape`; a collected file not UTF-8, or not JSON where due, fails its fixtures (README
  "Artefacts"); `tsbc` refuses `NaN`/`Infinity` wherever it reads JSON; the C7 vector files are hex
  (`contract/tests/test_e4_vault.py`); the free-text bounds in `C7/device-list-shape`, `C6/device-record`,
  `C6/credential-set-plaintext`.

## V13 — Vocabulary

Three words are fixed (Contract §15; session model §1). A **connection** is one authenticated transport to one
target, inside one worker. A telnet connection (from this release) is one TCP connection with no authentication of
its own (RFC 854); its one login is typed by the operator in its one session. A **session** is TSB's record of one thing opened on it, one channel or one forward: what
a client attaches to, what is recorded and what ends. **Channel** is the wire word for that same thing, never used in
contrast to session. Nobody attaches to a connection. A session ends **`killed`** (by TSB or through it) or
**`terminated`** (from outside TSB); there is no `disconnected` state (session model §10, §12; README).
- **Providers** MUST end a session only `killed` or `terminated`; closing one MUST close that channel only, siblings
  and connection living on (Contract §15.2); a telnet connection carries one session and closes with it; MUST NOT act on an exit status (it decides nothing; at this release the contract does not carry it, and carrying it to the clients as data is E5's, with exec) or match a prompt (Contract §6). Relaying SSH's own authentication messages — the pre-login banner, a keyboard-interactive info request, a password request — to the pane, and the answers back into SSH's authentication, is not matching a prompt in the stream: the worker reads a protocol message, never the bytes of a session (the device login, from Release B).
- **Consumers** MUST NOT describe a second connection as isolation (Contract §15.3).
- **Graded by** `C6/state-vocabulary`; `C6/event-vocabulary`; `C6/event-session-ended`; `C1/session-delete`;
  `C1/session-ended-unexpectedly`; `C8/end-marker-killed`; `C8/end-marker-terminated`; from Release B `C3/device-login-sequence`. The rest: owed.

## V14 — The broker presents choices, never picks

Where more than one valid option exists the broker presents them and records the operator's choice: no silent
precedence, no default (Contract §12). Presentation is exempt, narrowly: resize arbitration and VT state on attach
are mechanical, so clamp, coalescing and attach wiggle are no choice; the target, the credential and the session's
fate always are (Contract §12). Today: 409 `connection_choice_required` with `connection_ids` (Contract §15.3); 409
`credential_choice_required` with `candidates`, each a reference and its layer, answered by repeating the create with
`credential_ref` and `credential_layer` (from the vault release; error `x-codes`; verifier G-12, closed); the
stale prompt (immortality §5.3; `ws-frames#/$defs/stale_prompt`); the quiet prompt (session model §11).
- **A login the request carries is the operator's choice of credential** (session-open step 3): the broker resolves no
  stored credential for such a request and presents no credential choice, so `no_resolving_credential` and
  `credential_choice_required` never answer it, and a request carrying both a login and a credential choice is not the
  request's shape (422).
- **A prompt-mode login is the operator's choice of credential** too (from Release B): a create whose `login.auth.kind` is `prompt` resolves no stored credential and touches no vault, and the operator answers the device's own questions in the pane.
- **A recorded login needs the operator's consent** (session-open step 2b): while a login would be recorded
  (`GET /auth/me` says `recording.login_input` is true), a request whose `login.recording_acknowledged` is not true is
  409 `login_recording_not_acknowledged`, and the surface warns and repeats the request with it set. The broker never
  records a login on a default.
- **Providers** MUST present the options and MUST NOT pick, or act on a session's fate, without the answer.
- **Consumers** MUST put the choice to their operator and repeat the request with it (Contract §12).
- **Graded by** `C1/session-create-reuse-ambiguous-409`; `C1/reuse-409-lists-candidates`;
  `C1/credential-choice-required-409` (published e0 only); `C1/credential-choice-required-candidates-409`,
  `C1/credential-choice-made`, `C1/credential-choice-same-name-two-layers`, `C1/credential-choice-with-login-422`
  (reference, tests owed; required e4); `C2/stale-flow`; `C2/quiet-prompt-owner-only`
  (reference); `C1/session-create-supplied-login` (reference, owed by the core plan for a supplied login);
  `C1/session-create-login-unacknowledged-409` (reference, owed by the core plan for the recording knob); from Release B `C1/session-create-device-login-202` and `C1/session-create-device-login-unacknowledged-409` (reference, tests owed). Consumers: owed.

## V15 — Naming binary WebSocket messages in a step

Binary WebSocket messages have no schema name (ws-frames top). A step names them by content: **"binary: input
bytes"** (SF → CO), **"binary: output bytes"** (CO → SF), **"binary: dump"** (the one binary message after `prefix`,
`ws-frames#/$defs/prefix`). **Adopted for the drafts; the operator may veto it** (plan §9 Q4). Control frames keep
RFC 6455 names, as plan §3 writes them ("WebSocket ping", "WebSocket pong", a close by its V3 code). Other edges'
binary messages are named by their schemas (`ipc-data-frame`; `vt-ipc#/x-binary-layout` kinds 2 and 3).
- **Graded by**: a naming rule, not graded.

## V16 — No privileged path

Every capability is in the northbound API, and TSB's own surfaces get no shortcut (Contract §2, §3; OpenAPI `info`).
There is no plugin API: a plugin or panel uses the public API with its user's rights, a server-side plugin is another
API client with its own login, and a missing need becomes a public endpoint (Frontend §2.3, §8; ruling 5.49, CHANGELOG
v0.3 "Rulings applied").
- **Providers** MUST NOT offer any consumer, TSB's own included, a capability, endpoint or socket outside the
  published contract. **Consumers** MUST reach the broker only through it, with their principal's rights.
- **Graded by**: owed.

## Open items

Written as open, never as defaults; none is resolved here. "New" marks an item not in plan §7.

1. **Pending vault fix (ruled): who holds `broker.health`.** O2, approved with contract-v0.2 (2026-09-29;
   tsb-core `plans/260918-1658-e1-contract/approval.md`): a permission granted per user, like the other five; the deploy's
   template grants it to the seeded superadmin (`policy#/$defs/permission`; CHANGELOG v0.2 O2). Stale corpus line:
   session model §11, "the superadmin-only permission" (queue V14, PD4). Roles stay OPEN (open_decisions §3).
   operator-alerts carries it too.
2. **Pending vault fix (ruled):** session model §11 and Storage §8.1 give the heartbeat pair a per-target value (V7).
3. **Unagreed DEFAULTs leaned on.** Still open on the v0.3 approval page (tsb-core `plans/261006-1015-e2-contract/approval.md`,
   "Status after the operator's review"), or in neither of its lists (D5); the queue item is in brackets:
   - D1, awaiting ok (A10): V1.
   - D3 (A3) and D5 (A11): byte-relay step 11 (per-client output). D5 also: session-attach step 15 and R11,
     session-detach step 8 and rule 5, client-remove step 7 and rule 4, session-list rule 5.
   - D15–D18 (A8): V10 (C4 trust, D15); V12 (D16); V2 and V4 (`C4/vt-unknown-cmd`, the `vt-ipc` registry: D17);
     session-open step 16 (D15, D17); session-attach steps 10–11 (D17) and 21 (D17, D18); reattach-handshake step 11
     (D16–D18); byte-relay step 9 (D16–D18); size-control (D18); grid-snapshot steps 5–6 (D16, D17); stale-screen
     and core-restart step 21 (D18).
   - D19 (A8): session-end-by-tsb step 18, core-restart step 3. D20 (A8): session-attach R9.
   - D21 (A8): V4 (`snapshot_incompatible`); stale-screen; core-restart step 21.
   - D26 (A7): stale-screen, size-control, client-remove steps 5–6 and rule 7, session-detach step 7.
   - D28, D29 (A6): V1; config-reload step 4; core-restart step 13 (D29); link-liveness.
4. **An admin removing a client** (plan §7 C-6, unruled): the contract allows it; the corpus gives "kill any
   attached client" to the owner token only (V9; client-remove).
5. New: **no unknown-value rule** for `request_refused`, `stale`, `data_path`, C3 `link_state` (V5). The durable
   `stale_cause` has one (show the stale notice without a cause), but only the core reads that record, and the C2
   `stale` frame that carries the cause to a surface requires `cause`, so the rule has no wire form
   (`continuity-state#/$defs/session_record` `stale_cause`; `ws-frames#/$defs/stale` `required`).
6. New: **re-attach after 4011 or a code-less close**: only 4014 forbids it (V3; session-detach).
7. New: **`forbidden`** in `x-codes` does not name the missing-`broker.health` 403 on `/alerts` (V4; operator-alerts).
8. New: **the origin check's reach**: declared on login and logout only; plan §3 says every browser write (V10).
9. **Contract text stale (ruled): a session listed in the answer to the core's own `disconnect`.** DEFAULT 15,
   accepted by the operator 2026-09-26 (tsb-core `plans/260918-1658-e0-core/plan.md`, "What ends what"): the core's own
   `disconnect` lists no session the core has not ended; should one appear, it ends `terminated` /
   `connection_lost` (the transport is gone; no `killed` reason names it) and is logged as an internal error. The
   contract README's "Senders and receivers" sentence (a `connection_lost` of any cause but the core's own
   `disconnect` is an end from outside) leaves it `killed` with no reason the audit admits
   (`audit-event#/$defs/session_end`): stale text, queue K9 (V13; connection-linger step 16, session-end-from-outside step 5).
   Plan §7 G-7 is the trigger (when the core sends `disconnect`).
10. New: **no knob** for the core's wait on supervisor answers, nor the readiness bound behind `ready_timeout`, nor
    the VT heartbeat's cadence and missed count (V7; grid-restore).
11. New: **which sessions a principal may see** has no rule (V9; session-list).
12. New: **an owner who also holds `session.terminate`**: end reason `owner` or `admin` unspecified (V9; session-end-by-tsb).
13. New: **OPEN — a close for the core's own failure.** The registry (`ws-frames#/x-close-codes`) lists eight 4xxx codes
    and none for a failure of the core's own; the built core closes 1011, RFC 6455's internal error, when an attach
    fails on its side (tsb-core `core/internal/ws/attach.go`, `internal`). V3's "only with a listed code" and the
    built core disagree, and the contract is silent on what the core sends and what a consumer does with a
    non-4xxx close (V3).
14. New: **OPEN — two shapes defined twice.** `common.schema.json` top says a shared shape is defined once there;
    `engine` is `vt-ipc#/$defs/engine` (name up to 64, version up to 128) and, inline and unbounded,
    `continuity-state#/$defs/grid_snapshot_record` `engine`; `proto_version` is `common#/$defs/proto_version` (C3, a
    pattern) and `vt-ipc#/$defs/proto_version` (C4, const "0.3"). Which definition binds where they differ is not
    said (V6).
15. New: **OPEN — the contract README's tree-wide constraints have no V.** Its "Validator subset" (the keyword list
    is a constraint on every schema; patterns in the syntax Python `re`, ECMA-262 and RE2 share), "Synthetic
    credentials" (every credential-shaped fixture value is a fixed synthetic constant) and CHANGELOG v0.3
    "Runner" (the era rule) are carried here only as u64, `NaN`/`Infinity` and the pattern meaning (V6, V12). No
    text says whether the contract README stays their home once these documents move into `contract/` (V8).
16. New: **OPEN — the core as the only dialler.** V10's "the other side binds, the core dials and never accepts" is
    backed only by tsb-core's `docs/system-architecture.md` "Process topology". The contract says, per socket, that
    the core dials (ipc-control top, vt-ipc top, both manifests' tops) and forbids nothing else; the corpus and the
    rulings are silent. A MUST needs a contract sentence or a ruling (V10; edge CO-SV).
17. New: **OPEN — the scope of `unreachable`.** `common#/$defs/session_state` ("at this tag only a v0.1 worker under
    a v0.2 core") and `C1/session-unreachable` ("locally, only a v0.1 worker") against ipc-control top and the
    contract README "Senders and receivers" (`unsupported_proto` or `unknown_cmd` from any peer's answer to `hello`
    makes its sessions `unreachable` / `protocol`). V1, reattach-handshake step 4 and core-restart step 19 follow
    the second (V1).
