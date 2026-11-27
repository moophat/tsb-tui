# Changelog — TSB contract

Consumers pin one tag (`CONTRACT_VERSION`). Field names are in the schemas and fixtures at that
tag, never in this file.

## v0.26.1 — 2026-11-26

`fix(contract)`: the two fixture generators' usage lines (`tests/make_c4_fixtures.py`, `tests/make_v03_fixtures.py`) name `python3` instead of a machine interpreter path, and `tools/build-tarball.sh`'s comment says every published code repo also carries the same `contract/` tree. No schema, fixture, protocol or tool behaviour changes. With it, open_decisions §6 drops the two items once listed as open before publishing (Tu, 2026-11-25). The contract now ships its Apache-2.0 licence in its tree and in its tarball.

## v0.26.0 — 2026-11-26

E3b adds target import, own targets with a share list (just the owner is private; a list means only those named; no list is the shared inventory as before), generated target IDs, release-owned prompt templates, import receipts and the e3b profile. The e3b profile follows e4 and e4b and requires their fixtures as well. Provider tests and integrated evidence remain owed.

Compatibility: a caller holding neither `target.edit` nor `target.private` now receives 403 on a target write before shape or visibility, where it previously could receive 422 or 404; a `target.edit` caller keeps the released order. A query parameter on a target write is now 422. A supplied shared `g-` ID is refused with 422; IDs with that prefix in any source not written by the core are skipped and logged. Empty `prompt_pattern` is valid and detaches the template; absence follows the vendor template. `GET /targets` can show own targets whose share list names the caller without `target.discover`. Mixed-provenance duplicates follow the new deterministic matrix, and `private_owner` and `share` are validated together by the loader while `import_ref` outside core-owned sources is ignored. Records gain optional `share` and `owner`, absent on shared targets. Older cores refuse a policy containing `target.private`.

Release dependency: amend and re-export the vault corpus together with this contract: open_decisions §3 and §9, Storage §3.5, §3.6, §7.4, §9, §10.1, §10.1a and §10.1b, and credential §10.2. The corpus amendment carries the share list text: a user's own target names who sees it; no list is the shared inventory. No release is ready until the corpus and contract land together under the operator's go.

## v0.25.1 — 2026-11-24

`fix(contract)`: five clarifying sentences for slice 11's recording continuation, each placed where its clause lives. They clarify v0.25.0's text and change no schema shape, fixture instance or enum value; the readings are the orchestrator's DEFAULTs of 2026-11-24, graded by tests owed in tsb-core.
- S11-3: a hold that starts inside an end (a successor's included) follows §9 C.3's hold sentence — `session.disconnect` durable at once, and the hold write records placement `none` for that pair as a live session's hold does (terminal-output-accounting §5.3).
- S11-4: a failed or overflowing input-seq reservation refuses input with C2 `recording_unavailable`; the reason's description names a failed reservation as well as a quarantine (terminal-output-accounting §5.3; ws-frames `input_refused`).
- S11-5: the active-switch write also sets the session record's `activity_at` to the switch time, so `idle` re-armed from the switch survives a crash after it (terminal-output-accounting §5.3; continuity-state `activity_at`).
- S11-9: §3.3's unwritten-end order holds in a successor — recovered output, counted code 2, code 9 if incomplete (its `unconfirmed` fact on the generation-0 path), then the sole original end marker (terminal-output-accounting §3.3).
- S11-13: §5.3's "held and reported" and "no end decision uses a suffix" bind a quarantined pair, and `recording_held`'s realignment ban binds the quarantined predecessor; an active successor never quarantined, whose end placement a crash left unwritten, is walked forward from its commit point as any recording (core-restart steps 16–17) (terminal-output-accounting §5.3).

## v0.25.0 — 2026-11-23

Recording continuations after a sync failure (tsb-program #141): a failed pair remains quarantined at P, with its suffix preserved as unconfirmed evidence. An audit-linked successor carries worker replay, with the ordinary counted gap when needed. Added generation and input-sequence reservations, end-placement and hold state, chain-aware reader results, the recording_unavailable input refusal and alert, and reference fixtures. Chain collections carry ordered pairs, captured session state and audit facts; incomplete evidence and legacy graders fail closed. U2 and U3 provider tests remain owed; the held-ring rule must not be relied on before the worker-provider proof.

## v0.24.1 — 2026-11-23

Fixed: a control peer sending `seal_output` states C3 0.8 even without a hello (tsb-core #783, option
B); the worker records the statement for that peer, so it receives `output_sealed`. A replacement
control peer starts with no statement and receives neither 0.8-only event until it states 0.8 itself.
Added the e4 reference fixture `C3/terminal-seal-output-states-version` with U2/U3 graders owed, and
aligned the control-order checker and its regression tests. No wire shape change.

## v0.24.0 — 2026-11-22

Corpus only: architecture-contract.md, credential-protection-model.md, open-decisions.md, storage-and-discovery-architecture.md

## v0.23.2 — 2026-11-22

Fixed: the grader index schema's owed entry now agrees with the runner: an owed entry alone leaves its fixture UNGRADED, and a named test on the same fixture that ran and failed fails it (the operator's ruling, 2026-11-22). No fixture or protocol text changes.

## v0.23.1 — 2026-11-21

Fixed: the E4b grader index names the tsb-core tests read at `91dedc41` for 62 entries across units U2, U5 and U6. The two C2 close-4017 fixtures stay owed whole, their U2 and U7 entries alike: their U7 consumer test has no citation form yet, and a fixture that would mix a named test with an owed one meets two texts that disagree (the index schema: an owed entry's fixture is never FAIL; the runner: a cited test that fails fails the fixture). No fixture, schema or protocol text changes.

## v0.23.0 — 2026-11-19

Terminal output accounting — the operator's answers to three questions from tsb-core #645 (decided 2026-11-19),
written where each question sits in protocols/terminal-output-accounting.md.

Recording durability after a sync failure (tsb-program #137, option b): a failed recording fsync is quarantined in the
audit ledger. Added the audit-only `unconfirmed` reason `recording_unsynced`, written by the running core at P, the
session record's bbolt commit point, which may be below the recording walk's A; only this reason has a baseline below
A. A quarantined pair is never relied on above P: no exact fact above P, no `release_accounted`, and §8's notice stays
at P, where the quarantine's key is reused. It adds nothing to the code-9 grammar, and the `unrecorded` kind does not
take it. If the audit append fails too, only the in-process rule protects the pair; and when the key at P already
holds a line with another reason, the quarantine is not written down and a later process does not see it. Both are
accepted residuals, of the same class as the crash-before-notice limit. A restart honours the fact: the startup scans
the audit file before it realigns, the commit point stays at P (core-restart step 17, reattach-handshake and the ttyrec
`x-sidecar` reopen text say so), the hello carries P's seq, and no later successful fsync lifts the quarantine. Bytes
the recording holds above P are neither cut (the reopen cut never goes back to the commit point) nor relied on. Open:
how the replay from P + 1 is treated when the core must still append to a quarantined recording (an original end not
yet written, or a live session), and whether such a session's commit point may move above P again.

An offer below 0.8 during a carried seal or close (tsb-program #138, option B): a C3 0.8 worker answers such a hello
with `unsupported_proto` naming the session, never `terminal_busy`; once sealed, the session goes to that peer by the
ordinary legacy handshake and legacy release; `output_sealed` and `terminal_busy` reach only a peer that stated 0.8 in a
hello the worker parsed, refused hellos included. Old peers keep exactly their old messages. A running legacy core sets
no retry timer, so that handshake and release come at its next start; meanwhile a non-retained session stays
`unreachable` / `protocol`, never killed, and a connection that closes first takes the retained resources with it.
No shape change; the ipc-control `error` description, the CO-PW `error` row and reattach-handshake's 0.8 branch say it.

The control-order checker follows: toward a hello offering below 0.8, a carried seal is settled by the legacy
`hello_ack` with `ended: true` (one saying `ended: false` is flagged), where it used to demand a sealed summary that B
forbids sending to that peer. `output_sealed` and `terminal_busy` are flagged until the connection's peer has stated 0.8
in a parsed hello, for any session on it; three tests that accepted a barrier before any hello are rewritten with a 0.8
hello first. A later hello offering less does not undo that statement: the core's own terminal vocabulary follows it,
while `terminal_busy` and a `hello_ack`'s `output_seal` are judged by the hello they answer, so both stay flagged toward
a hello offering below 0.8.

A late exact line after closure (tsb-program #139, option a): a `worker_absent` closure notice at F is final. No
`sealed` or `not_recorded` fact, and no extent whose lower boundary is at or above F, counts toward coverage or
completeness, wherever it lies in the audit file; such lines stay as history and are reported as post-closure and
non-authoritative. §5.4's settling and §8's later-frontier clause no longer apply to a `worker_absent` notice. A crash
before the notice remains an accepted limit, and so does its same-class case: when the key at F already holds an
unconfirmed line with another reason, that line is reused, so the freeze is not written down and a later process does
not see it.

§2 adds one sentence: where `config.yaml` is rendered from committed templates, "the operator adds the key" is met by
the template carrying `worker.output_drain_timeout`. Three reference fixtures, published and required-green e4 like the
other terminal fixtures, with provider tests owed: C6/terminal-recording-sync-quarantine (U2),
C3/terminal-legacy-offer-mid-seal (U2/U3), C6/terminal-close-late-exact-fact (U2/U11).

## v0.22.1 — 2026-11-18

Fixed: the protocol prose names `TargetWriteRecord` for target create and replace, matching the OpenAPI.
The write record equals the indexed GET apart from `login_hint`, which a write answer never carries.
No schema, fixture or behaviour change.

## v0.22.0 — 2026-11-17

Added: optional caller-relative `TargetRecord.login_hint` on eligible SSH inventory reads, with one enum registry,
metadata-only candidate resolution and no-store on hinted responses. Target create and replace answer `TargetWriteRecord`
without `login_hint`. Missing runtime targets and unauthorized callers get no hint; resolution faults are unavailable.
Saved metadata does not guarantee credential material. Consumers suppress absent/unknown hints and keep the normal connect flow.
C1 reference fixtures publish e4 and require e5, with U2/U6 provider tests owed; schema, thin-client decoding and fixture-server
checks run here. Corpus: credential §8.1 says the establishment-only rule governs credential material; reading a
credential's reference, kind and grant to say whether a login is saved reads metadata.

## v0.21.0 — 2026-11-17

Session history — added opt-in `GET /sessions` paging by `limit` (1–500) and unsigned opaque `cursor`, ordered by
`created_at` then session id, both descending. Continuations require `limit`; `SessionList` gains
optional `next_cursor`. `ended=only` selects exactly ended records. Requests without paging retain
the complete retained visible list, and the client's existing `sessions()` still returns a list;
new `list_sessions(*, ended=None, limit, cursor=None)` returns one page envelope.

Added core-wide `sessions.ended_retention`, a positive duration with deployment templates supplying
seven days, never a schema default. Fully finalised ended records are removed no earlier than that
age (last activity when `ended_at` is absent), stay listed/readable until removal, and then answer
the existing indistinguishable 404. Removal touches only the session record and grid snapshot,
never audit or recordings; cleanup scheduling belongs to the core. New reference fixtures are
published e4, required-green e5 to preserve the existing e4 gate; U2 provider tests are owed.

## v0.20.0 — 2026-11-15

Terminal output accounting — added C3 0.8 terminal sealing and accounted disposal, optional immutable hello summaries and a
version-gated connection-wide output_drain_timeout. Older peers keep their old messages and release
meaning. New runtime configuration requires the key, without a contract default.
Restarted control connections accept carried drain barriers, hello_ack and no_such_session settle carried drains, and artifact grading rejects code-9 markers on non-PTY sessions.
Carried seals and carried core-requested closes are tracked apart across restarts, so an interrupted close refuses an output_sealed barrier and matches only its channel_closed by: core.
A hello_ack settles a carried drain only with proof (output_seal with sealed: true for a carried seal, ended: true for a carried close) while an unproven ack is flagged as an order fault, and an error no_such_session in a later run directly answers a carried seal_output.

Added the closed recording.output_incomplete audit union (including explicit unrecorded null-baseline
context), code-9 pre-end unknown marker, strict codec/comparison rules and single-file audit startup
repair of only a non-LF final suffix. Connection teardown stays bounded by linger and declares the
unaccounted suffix unknown; no new worker disk record. Completed recordings and original ends stay
unchanged. Terminal fixtures are published/required-green e4, with reference provider tests owed;
shape/codec checks run here. Storage §6 vocabulary must be amended through the vault and approved
in the same PR before release. The fixture register now names the five integrity variants (extent,
unconfirmed, sealed, not_recorded, unrecorded), matching §5.1 and the schema. The unrecorded close
notice's key is the unconfirmed form with null baselines, `ri1:<session_id>:u:null:null`, so it is
reason-free and the first durable fact is reused whatever the later reason; the event keeps its
reason. See protocols/terminal-output-accounting.md.

A seal reply is a successful seal: `output_sealed` takes the sealed:true branch alone, kept as
`$defs/output_seal_final`; the shared summary union keeps sealed:false for a hello_ack's preliminary
summary. Accounted disposal needs the seal summary on the replay's hello_ack, so the
`final_last_seq == current_seq` snapshot check runs before every accounted release, and a hello_ack
for a session that is sealing is an order problem — terminal_busy is owed for that attempt, and the
replay after such an ack answers no handshake. The 0.8 reattach fixture's connect and hostkey notes
name the 0.8 peer its capture negotiates.

A telnet connection's accounted release now marks its one session's `single_session` report as owed, exactly as
the `release` branch does: after `release_accounted` succeeds for the connection's single session, the checker
requires the next event to be `connection_lost` cause `single_session`, so a 0.8 telnet worker that disposes of its
session through the accounted path and leaves the connection open, or skips the report, is flagged. SSH disposal is
unaffected. Tests cover the flagged capture, the capture with the report, and an SSH capture.

A `terminal_busy` that answers a hello is now an order problem unless the session is sealing or its already-requested core end is in flight (terminal-output-accounting §3.1), and the CO-PW catalogue lists the 0.8 vocabulary: `seal_output` and `release_accounted` command rows, an `output_sealed` event row, `terminal_busy` on the `error` row, the 0.8 version gate and the schema-exact counts (17 commands, 15 events).

The code-9 checker now compares a marker's `seq`/`bytes` counters against the immutable recording walk before it (§6, §4: last output seq, output bytes with code-2 counted loss; code 9 adds neither), so a marker whose counters do not match the recording it sits in is a recording problem. A `seal_output` left unanswered when a capture ends is now an order problem, as an unanswered hello or replay is, and an error answering a seal is allowed only from §3 line 70's set {`bad_request`, `no_such_session`} — any other code answering a pending seal (e.g. `no_data_peer`) is flagged, while a `terminal_busy` that answers a fresh hello sent during sealing stays a handshake answer. The 0.8 fixtures and captures pass unchanged; focused tests cover the flagged marker, the flagged pending seal, the flagged unlisted-code answer, and the two permitted refusals as guards. Past a code-2 gap the committed sequence is not in the recording, so the walk's L is unknown: `recording_baseline` returns it as unknown from the first gap marker on (bytes keep counting), and the checker compares a code-9 marker's bytes always and its seq only while L is known, inventing no sequence. A command's answer comes on the connection it was sent on, so `sealing` and `closing` stay per-run and are cleared at a run reset as before; what crosses the boundary is the worker's terminal state (Amendment 1, terminal-output-accounting §3.1): every session still sealing or closing joins a carried drain set, so a restarted core's hello answered `terminal_busy` for it is accepted, and a session leaves that set when an `output_sealed` or a `channel_closed` for it arrives in any later run, or when it is released; a `terminal_busy` for a session neither pending in this run nor carried stays an ORDER fault — tests cover the carried seal and close captures, a fresh seal/close answered in the later run clearing the drain so a further refusal is flagged, release clearing the drain, and the idle refusal, with round 2's boundary test restored and passing.

## v0.19.1 — 2026-11-14

Fixed the CO-FS edge index: what a refused `policy.yaml` leaves in force is no longer called open there; it points at
config-reload step 2, where a refused file changes nothing and the last accepted file stays in force (its open item 2
is closed). Index text only; no schema, fixture or protocol rule changes.

## v0.19.0 — 2026-11-14

Fixed: `tsbc serve` applies the `GET /sessions` ended filter and refuses invalid values with the fixed rule text, matching session-list rule 8; server tests cover the unchanged default/include list, exclusion and refusal.

Added: optional `ended=include|exclude` on `GET /sessions`, default `include`, to leave ended sessions out without changing visibility or any response shape or existing meaning (V1); surfaces still drop ended sessions received from older cores that ignore the parameter. Other values are 422 `invalid_request` without echoing the value. Three C1 reference fixtures cover exclusion, the unchanged default and explicit include, and invalid-value refusal, published e4 and required-green e5 (V8), with U2 provider tests owed in `graded-by.json`.

## v0.18.0 — 2026-11-14

Added: the thin client's public methods for the six account-administration operations released at v0.17.0 (`accounts()`, `create_account(username, password)`, `disable_account(username)`, `enable_account(username, password)`, `set_account_password(username, password)`, `change_own_password(current_password, new_password)`), on its existing transport; passwords travel only in request bodies and appear in no `ApiError`, and a wrong current password stays a 403 `password_change_failed`, never a lost login. Client tests against the fixture server; the contract-matching test calls all six. No schema, protocol, fixture or OpenAPI change.

## v0.17.0 — 2026-11-13

Added E4b account management: account-administration REST operations, own password changes, the `account.admin`
permission, the configurable minimum new-password length (DEFAULT 15), the disabled-account users record, audit
events, login-disabled behavior, close 4017 for ended account logins, and 63 published/required-green E4b
reference fixtures with provider graders owed. Disable retry is a 204 no-op; enabling an enabled account and
administrator password set on disabled are 409 refusals. Names are never reused; account acts keep terminal
sessions, devices and vault data. `e4b` follows `e4` and precedes `e5`; every existing fixture and profile
classification stays unchanged. The runner checks/redacts both own-change password fields. Contract tests
check old-record load shape, disabled-hash rollback safety, field leakage and epoch compatibility.

Red-team rulings add device-login close 4017 without a result, host-only `account.recover` audit, wrong-proof
`auth.password_change_failed` audit, transactional account check-and-set and in-flight unlock protection,
and account operations in the in-memory reference server. The account list is ordered by username. Login
end effects complete before the successful response. Rollback documentation states both the old core's
500 hash-format refusal and the re-seeding limit. The 15-character default cites NIST SP 800-63B-4
§3.1.1.2 item 1. Additional reference fixtures cover these rulings; provider and consumer graders are owed.

## v0.16.0 — 2026-11-12

Added: how a client with no screen of its own, such as `tsb-tui drive`, carries the screen lock
(`protocols/screen-lock.md`): it voids its looks, answers no session content and sends no typed input until the core
re-checks the login password through `POST /auth/screen-unlock`, the password read again from the file or descriptor
its login came from, never from a command. No schema shape, operation, description or core behavior changes.

## v0.15.1 — 2026-11-09

Fixed: the E4 grader index names the verified tsb-core tests and lab rigs read at `d2239382`
for all 101 C7 reference fixtures and 50 C1/C6 E4 reference fixtures, plus C5's three E4 knob
fixtures, `policy-target-edit`, `policy-login-input`, and C8's `recording-login-frames` and
`login-line-escape`. Each of the 18 C7 vector
fixtures now declares its grading units; their vector data and every other existing field are
unchanged. Each chain vector names both D11 implementations: Go and the deployed browser vector
rig for browser-side steps, Go and the harness's independent Python reference-client tests for
core-only steps. Parametrized pytest citations omit their parameter suffixes; the authoring
script is never a grader. `rig-vault-canary` is cited with its lab prerequisite confirmed by the
2026-11-09 nightly PASS.

The three C5 knob fixtures also declare U2's required-key loader checks. C6
`event-cred-store-personal-no-class` declares and cites U2's real audit emitter/schema test.
The three E4 prompt-pattern entries name the tests tsb-core added for them (merged as `2a3b10e`):
C1 `target-record-prompt-pattern`, C5 `inventory-target-prompt-pattern` and C5
`inventory-yaml-prompt-pattern`. No fixture required green at e4 is owed.
Future-epoch entries are unchanged. The core-only vectors' U11 Python citations also need consideration when
promoting those published-only vectors to required-green: the core's current rig derivation
accepts only shell-rig citations there.

## v0.15.0 — 2026-11-09

Corpus only: external-study-and-doctrine-guardrails.md, open-decisions.md

## v0.14.1 — 2026-11-07

Fixed: the artefact and hygiene scans exempt a plain `"device_id"` value only when the whole value matches common.schema.json's device-id pattern (43 unpadded base64url characters). Other values under that key remain visible to every credential detector; the same value under any other key is still scanned.
Fixed: in `_ws.py`, when a ping's pong cannot be sent, `recv` drops the socket and returns one abnormal close message (code 1006); every later `recv` raises `ConnectionClosed`.

## v0.14.0 — 2026-11-07

Added target writes: `POST /targets` (`createTarget`), `GET /targets/{id}/definition`
(`getTargetDefinition`), `PUT /targets/{id}` (`replaceTarget`) and `DELETE /targets/{id}` (`deleteTarget`).
`TargetDefinition` is closed, with eight editable fields and SSH-only `user`. A replace preserves every other file
key, including released `prompt_pattern` and noneditable `options.*`. `TargetRecord` and the login `Principal`
are unchanged. `Me.permissions` is optional, for permission-aware surfaces. `target_id_taken` is a new 409;
`target.edit` is the eighth named permission. A dedicated `TargetWriteNotAudited` 500 `internal_error` says the
change took effect and its audit line is missing: re-read before retrying, never the vault’s `ActNotAudited`.

Added `target_write_event`, with `target.create`, `target.update` and `target.delete`: source and field names only,
no fields on delete, `[]` on a no-op update. New x-layout rule 11 reserves `tsb` for the core on a principal’s API
request; rule 1 also names both reserved sources and rule 4 stays unchanged. New protocol `target-write` and corresponding edge/convention updates.
The client adds `create_target`, `get_target_definition`, `replace_target`, `delete_target`; `tsbc serve` implements
them and sends `Me.permissions`. PUT is supported in the fixture format, runner, self-check and server.

Fifteen new fixtures, each `published: e3`, `required-green: e4`: C1 `target-create`, `target-create-id-taken-409`,
`target-create-secret-field-422`, `target-create-outside-filter-403`, `target-edit-other-source-403`, `target-replace`,
`target-delete`, `target-definition-read`, `target-write-forbidden-without-permission`, `target-write-index-timeout-503`, `me-permissions`;
C5 `policy-target-edit`; C6 `event-target-create`, `event-target-update`, `event-target-delete`.
The filter and index-timeout cases and C5/C6 provider graders remain owed by phases 3–4. Existing fixtures are unchanged.
The audit now precedes the index wait: failed audit is 500 whatever the index does; an audited change whose index
wait times out is 503, with that state stated in the message. The timeout fixture requires its audit line already
present. Definition reads declare 503 when visibility is unavailable. Target-write audit source is `tsb` only;
delete carries no field names. Discovery writers refuse both reserved names, `manual` and `tsb`.
This is additive throughout (V1) against the published v0.13.0 contract; the current layout prose is extended.

## v0.13.0 — 2026-11-06

Added: optional target `prompt_pattern` in inventory files and northbound target records, for surfaces that check the
cursor's row before sending a line. It is an I-Regexp (RFC 9485), matched against the whole row with trailing spaces
trimmed; the core only loads, stores and serves it as written. Separate C5 and C1 fixtures require green from e4; their
provider tests are owed.

## v0.12.1 — 2026-11-01

Fixed: the published client's `parse_server` accepts only the JSON integer `1` as a frame's `v`, as `parse_device_login` does (`true` and `1.0` are ignored); the self-check also refuses Python's named group `(?P<name>` and named backreference `(?P=name)` in any schema pattern, which ECMA-262 and Go RE2 do not accept.

Fixed: the Release B additivity test exempts `info/version` of the OpenAPI document, which the release workflow writes, so the release commit's own CI passes.

Fixed: `continuity-state` `connection_record`'s pattern for a stored login's `credential_ref` used a lookahead, outside the pattern syntax the README allows (Go RE2 has none); it is now an RE2-safe pattern of the same meaning, and the self-check refuses lookaround and backreferences in any schema pattern.

Fixed: the published client's `parse_server` now ignores a known frame `t` at a version it does not speak, as V1's consumer rule requires (a known `t` with an unknown `v` is ignored as an unknown `t`); a frame at the client's version parses unchanged.

## v0.12.0 — 2026-11-01

Fixed: the fixture server's non-waiting inspection of a socket reads at most 1 MiB per call and none while 1 MiB of payload is held unread; the rest stays in the socket for the ordinary read, in order.

Fixed: the device login's Security considerations name TSB's own `login as:` and `Password:` as sidecar code 8, recorded under the same knobs and consent as codes 6 and 7; the host-key question and its answer, and Remember, are never recorded.

Fixed: a `device_login_cancel` the served dialogue has already received when the device accepts ends the login `cancelled`
and opens no session.

Fixed: a socket close or EOF the served dialogue can already see when the device accepts ends the login `cancelled` and opens
no session.

Fixed: a client frame at a `v` the served dialogue does not speak (missing, or not the integer 1) is ignored as an unknown
`t` is, before the session exists.

Fixed: `target.auth_failed` `detail.attempts` is required for `login` `prompted` and refused for `supplied` in the audit schema.

Fixed: a create naming a `connection_id` that is not a string is 422 `invalid_request`.

Fixed: a send on a socket a device login's end has closed ends the served dialogue quietly.

Added: a device login's connection is offered back to its own principal. A `reuse` with no `connection_id`, no login and no
credential choice takes the principal's own prompted connections to the target as candidates, with the connections opened
with the one stored credential that resolves; they are listed in a 409 `connection_choice_required`, a lone candidate is
reused (201), and `no_resolving_credential` and `credential_choice_required` do not answer first. Never another principal's.

Added: the device login (login at connect, Release B). A create in prompt mode (`login {user, auth: {kind: prompt}}`)
opens no session: it answers 202 with a one-time ticket, the pane redeems it on `GET /ws/attach` with
`device_login_attach`, the worker asks the device's own SSH login questions (a host key, the banner, keyboard-interactive
rounds, a password), the operator answers them in the pane, and when the device accepts the same socket continues as an
ordinary attach. C3 goes to `proto_version` 0.7. Additive throughout (V1): every request, frame, message, schema and
fixture valid at v0.11.1 stays valid, and every old fixture is unchanged. Corpus: nothing changes in `srs/` in this PR; the
one §10.2 line below awaits the operator's go. **Cores are upgraded before workers, and rolled back after them:** a 0.6 core
creates nothing on a 0.7 supervisor, and a 0.7 core over 0.6 workers answers a prompt-mode create 503 while every stored
and supplied login keeps working.

Added: sidecar code 8, TSB's own prompt (`ttyrec` `x-own-prompt-line`, a fixed `login as:` or `Password:` in the login line's form with its own tag), written in a device login's recording head before the code-7 answer it asks for, under the same knobs and consent as codes 6 and 7; code 6 stays text the device sent, a supplied login has none, collection keeps it, and a reader of an earlier tag refuses it (OPEN-R4, ruled 2026-11-01 21:53). `C8/end-marker-killed` invalid case 3 now uses code 9 as its undefined code.

**The corpus line, for the operator's go** (credential-protection-model.md §10.2, after "… and never stores it."; OQ8; he
authors it in his vault and the export lands it in this PR; without it the contract's own wording stands alone): "Keeping
that login is a separate act: the user saves it into their own vault by an explicit store of their own, as any login is
saved, and the session-creation request itself stores nothing."

**C1.** `SessionLoginAuth` gains the branch `{kind: prompt}`. `createSession` gains 202 `DeviceLoginPending
{device_login_id, ticket, expires_in}` (its own closed shape, not `Ticket`; OPEN-R1 (a)) and 429 `rate_limited` (a
prompt-mode create past the principal's `worker.device_logins_max`); its 503 gains the 0.7 floor and its fixed text, its
403 a prompt-mode create by a principal who may not join a session on the target. `error.schema.json` `x-codes`:
`rate_limited` and `worker_unavailable` each gain a sentence ("From Release B it also answers …"); no new code.

**C2.** Four client frames (`device_login_attach`, `device_login_hostkey_answer`, `device_login_answer`,
`device_login_cancel`) and three server frames (`device_login_hostkey`, `device_login_prompt` in three methods,
`device_login_result` with eleven outcomes). `request_refused` gains the requests `device_login_answer` and
`device_login_hostkey_answer` and the reason `invalid`. `x-close-codes` gains 4016 `device_login_ended`. `x-attach-order`
gains the device-login branch.

**C3, 0.7.** `auth {kind: prompt}`; `connect.login_timeout` (10000 to 1800000 ms, with a prompt-mode connect only);
events `hostkey_ask` and `auth_prompt`; commands `hostkey_answer` and `auth_answer`; `auth_failed.code`; `error` code
`hostkey_refused`. Eight rules in `ipc-control` (versions, order, a cancel, the peer, the probe, one terminal answer, the
core's reaction to an unknown value, attempts). The attempt number rises only when the device refuses an answer; there is
no new field for the signal (OPEN-R5 (a)).

**C5, C6, C8.** `worker.login_timeout`, `worker.device_logins_max` (neither joins `worker.required`) and the target's
`options.login_timeout`. `target.auth_failed` gains `detail.login` `prompted` and `attempts`; new `target.hostkey_refused`;
`session.connect` gains `detail.login` `prompted` and `refused_attempts`; the connection record gains `login` (`stored`,
`supplied`, `prompted`) with its placement rule (OPEN-R2 (a)). C8: a device login's frame order, the stamp rule, the empty
prompt, and "a code-6 frame is text the device sent", with TSB's own prompts in code 8 (OPEN-R4); `x-login-line` is unchanged, `x-own-prompt-line` is new.

**Collections, conventions, protocols.** `answers` joins the credential-named keys (`artifacts-collected` top; `tsbc`
`CREDENTIAL_KEYS`, read as "each string of the list"). V1, V3, V4, V5 (six registries), V10, V13, V14. New protocol
`device-login`; `session-open`, `session-attach` (CR6), `login`, `edges/SF-CO`, `CO-PW`, `PW-TG`, `CO-DK`.

**Client and `tsbc`.** `Client.create_device_login(...)` and `AsyncDeviceLogin` (`open`, `recv`, `answer_hostkey`,
`answer`, `cancel`, `attachment`); an answer is in its frame and nowhere in the client's log. `tsbc serve` scripts one
device login on a fixture target (a first-use key, one keyboard-interactive round of two prompts, one echo off, accepted for
any answers, then an ordinary session). The control-sequence check grades the C3 rules a capture shows (2, 3, 6, 7, 8).

**The 34 sentences this release makes false, each rewritten** (the plan's table; `tests/test_release_b_additive.py` grades
that the rewritten descriptions are exactly these and the ten sentences listed after the table, and no others, and that each old sentence is gone):

| # | Where | Old sentence (v0.11.1) | New wording |
|---|---|---|---|
| 1 | OpenAPI `SessionLogin` | "…in `connect.auth` exactly as it sends a stored credential (C3 is unchanged)…; never stored" | true of `kind: password`; a `prompt` login is a device login, which needs a worker at 0.7; "the create, the device login and the core store nothing of a login (V10)", and keeping one is a separate store into the user's own vault |
| 2 | OpenAPI `SessionLoginAuth` | "One branch at this release, `password`." | "Two branches at this release: `password`, and `prompt`…" |
| 3 | `SessionLoginAuth.password` | "never stored (V10)" | "never stored by the create or the core (V10)" |
| 4 | OpenAPI `createSession` | "Resolves the target … and opens a shell. The session exists…"; "The login is never stored" | "On a 201 the create resolves …"; a prompt-mode create answers 202 and opens nothing yet; "The create stores nothing of the login (V10)" |
| 5 | `Me.recording.login_input` | "a login supplied on session create would be recorded" | "…or a device login's user name, the device's prompts and every answer, would be recorded" |
| 6 | `audit-event` `target_auth_event`, `detail.login` | "the target refused a login this request supplied … only for a refused supplied login"; "always supplied" | a supplied login, or a device login ended with at least one answered attempt; `detail.login` is `supplied` or `prompted` |
| 7 | `audit-event` top | "target.auth_failed: the target refused a login supplied on session create" | "…or a device login that ended without connecting after at least one answered attempt"; `target.hostkey_refused` listed |
| 8 | `session_connect.credential_ref` | "Absent for a supplied login (detail.login supplied)" | "Absent for a supplied or a prompted login (detail.login supplied or prompted)" |
| 9 | `connection_record.credential_ref` | "or the constant supplied for a connection opened with a supplied login" | "…or a device login, which `login` tells apart" |
| 10 | `ttyrec` top, `x-sidecar` codes 6 and 7, the code-6 `$def` | "Code 6 is the same line for a prompt the user was asked, which a supplied login never has"; "what the user was asked"; "A login supplied on session create asks nothing" | "text the device sent"; the device login's order, the stamp rule and the empty prompt added; "a code-6 frame is text the device sent", TSB's own prompt text being code 8; the `ttyrec` top's list of codes gains "TSB's own prompt (8)" |
| 11 | `ipc-control` top | "Credentials travel only here, only inside connect.auth"; "nothing matches prompts" | "…and, from proto_version 0.7, inside auth_answer"; the second kept, with V13's sentence on relaying SSH's own authentication messages |
| 12 | `ipc-control` `auth_failed` | "The target refused the credential." | "…or, on a prompt-mode connect, its bound passed or the device asked for a password change, which `code` says" |
| 13 | `ipc-control` `disconnect` | "Close the connection and every session on it." | "…; before a prompt-mode connect's terminal answer it instead ends the attempt (rule 3)" |
| 14 | `ipc-control` `hostkey` | "accept, warn, record" | "…on a connect that is not in prompt mode; on a prompt-mode connect the worker never sends hostkey: it asks first with hostkey_ask" |
| 15 | `ws-frames` `attach` | "Must be the first frame on the socket" | "On a socket that attaches by a session ticket, must be the first frame…; a device-login socket opens with `device_login_attach` and never carries `attach`" |
| 16 | `ws-frames` `x-attach-order` items 1 and 3 | "not a text attach frame"; "minted for another session" | `device_login_attach` added; another device login, or a ticket of the other kind; the device-login branch and 4016 added as items |
| 17 | `x-close-codes` 4003 | "Ticket not valid for this session, or the principal may not join it." | "…or, on a device-login socket, for this device login or of the other kind, or the principal may not join it (or … may no longer see the target or create one)" |
| 18 | `x-close-codes` 4015 | "The session lives on" | "On an attach the session lives on…; on a device-login socket no session exists, so the device login ends, with no result frame" |
| 19 | `request_refused` | "`not_pending`: no stale choice is outstanding" | "…or no device-login question or prompt with that id is outstanding; `invalid` added" |
| 20 | `artifacts-collected` top | the credential values a harness replaces | "…and every string of an answers list, on C2 device_login_answer or C3 auth_answer" |
| 21 | conventions V10 | "MUST accept an attach only by a one-time … ticket … bound to principal and session"; "a supplied login MUST NOT be stored" | "…or, from Release B, to principal and device login, redeemable only by `device_login_attach`"; "the create, the device login and the core MUST store nothing of a login" (OQ8) |
| 22 | session-open "Words used here" | Supplied login "It is never stored"; First use "It accepts, warns and records it" | "The create stores nothing of it (V10)…"; in prompt mode the worker asks first |
| 23 | session-open step 7 | "no C3 field is new, no `proto_version` step is taken, and every worker … serves a supplied login" | true of a `kind: password` login; a prompt-mode connect is 0.7's |
| 24 | session-open step 10 | "The worker MUST send it between `connect` and `connected`" | "On a connect that is not in prompt mode …"; never on a prompt-mode connect |
| 25 | session-open step 11 | "When the refused credential was a supplied login the core MUST also write … `target.auth_failed`" | "…or a device login with an answered attempt (device-login step 19)" |
| 26 | session-open step 17 | "(the username, then the password: two code-7 frames…)" | "for a supplied login …; a device login's frames are device-login's, step 20" |
| 27 | session-open "Who is told" | "with one exception" | "with two exceptions": `target.auth_failed` and `target.hostkey_refused` |
| 28 | session-open Security, the credential | "crosses SF-CO once, in the body of the create request, and CO-PW once, in `connect.auth`" | true of a supplied login; a device login's answers cross in `device_login_answer` and `auth_answer` |
| 29 | session-open Security, host keys | "Only the creator is shown the fingerprint: `hostkey` exists in `SessionCreateResponse` alone, and no record or frame carries it" | "…but `device_login_hostkey` (… to the creator's device-login socket alone)" |
| 30 | session-attach step 1 and CR1 | "Before it attaches or sends input, a consumer MUST show …" | CR1 scoped to an attach by a session ticket; new CR6: on `attached` after a device login the consumer shows the recording warning before any input |
| 31 | edges/SF-CO | the `createSession` row ("never stored"); "bound to principal and session"; the close-code list | 202 and 429 added; "the create stores nothing of it, V10"; "or to principal and device login"; 4016 |
| 32 | edges/CO-PW | "Credentials. Only in `connect.auth`"; the Versioning row | "…and, from 0.7, `auth_answer`"; 0.7, the device-login messages only to 0.7 |
| 33 | edges/PW-TG | "travels only in `connect.auth`"; "the worker accepts, warns and records on first use" | "…and, from 0.7, `auth_answer`"; in prompt mode the worker asks first |
| 34 | edges/CO-DK | "Audit `$def` events 14, actions 23" and the `target_auth_event` row | "15 and 24"; a refused supplied or device login; `target_hostkey_refused_event` row |

**Beside the 34: ten more sentences the table missed, found by the pre-release search and rewritten on the orchestrator's
ruling of 2026-11-01 15:00** (each in the manner of the rows above; `test_release_b_additive.py` grades them too):

| # | Where | Old sentence (v0.11.1) | New wording |
|---|---|---|---|
| M1 | `reattach-handshake.md`, `config-reload.md`, `worker-placement.md`, `grid-restore.md`, `byte-relay.md`, `session-open.md` Security | "credentials travel only in `connect.auth`" (and its variants; "It crosses edge CO-PW only inside `connect.auth`") | "… and, from 0.7, `auth_answer`", as V10 says |
| M2 | OpenAPI `/ws/attach` description | "the first frame is `attach` carrying the ticket and the session id" | "… `attach` (…) or, on a device-login socket, `device_login_attach` (…)" |
| M3 | `ws-frames` `x-close-codes` 4000; `session-attach.md` rule R3 | "The first frame was not a valid attach frame"; "4000 for a first frame that is not a text `attach` with `v` 1" | "… (or, on a device-login socket, a valid `device_login_attach` frame)"; R3a |
| M4 | `session-open.md` step 19 | "It MAY carry `detail.login` (`stored` or `supplied`)"; "written only for a refused supplied login" | `prompted` added; or a device login that ended after an answered attempt, with `attempts` |
| M5 | `audit-event` top, `session_connect.detail.login` and `detail` | "(login: stored or supplied)"; "`stored` … or `supplied` …"; "names two optional keys" | `prompted` and `refused_attempts` added; "three optional keys" |
| M6 | `common` `proto_version`; `worker-manifest` `proto_version` | "This tag writes \"0.6\""; "\"0.6\" at this tag" | "0.7" |
| M7 | `common` `credential_ref`; `continuity-state` `credential_layer` | "the constant a connection opened with a supplied login records"; "Absent … for a supplied login" | "… or a device login"; "… or a prompted login" |
| M8 | OpenAPI `SessionCreateRequest`, `createSession` 409; `session-open.md` steps 1 and 4 | "a username and password for that one connection"; "one that is not such a candidate is 422"; "never one opened with a supplied login"; "`connection_id` that is not a candidate: 422" | the password or, in prompt mode, nothing yet; a prompted connection of this principal is reusable by id, and (ruling 2026-11-01 19:02) offered back to it when a reuse names none; "supplied login" with a device login's connection listed to its own principal only |
| M9 | `edges/CO-DK.md` (the Contract line), `edges/SF-CO.md` Gaps | "fourteen `$def`s carrying twenty-three actions"; "all 17 text frames, all 9 close codes" | "fifteen … twenty-four"; "all 24 … all 10" |
| M10 | `README.md` "Credentials never leave the node" | the redaction list without `answers`; "the username and password a supplied login records" | "… and every string of an `answers` list …"; "or the user name and answers a device login records" |

(M4 is the step 19 cell; the table has no row for it because step 19 was only to gain text.)

**And one more, found in the third review round of #109, rewritten with the fix below:**

| # | Where | Old sentence (v0.11.1) | New wording |
|---|---|---|---|
| M11 | `session-open.md` step 3 | "**A request without a login** (from the vault release, E4): the core resolves the candidates …" | "**Any other request without a login** …": the same step now opens with the one request that resolves no credential and needs no login, the caller's own prompted connection reused by `connection_id` |

**And one more, ruled 2026-11-01 19:02 (the operator: "Why not just prompt and ask?"):** a device login's connection is
offered back to its own principal. Sentences of this release's own text that said otherwise are rewritten (the OpenAPI
`SessionCreateRequest` and `createSession` 409, `error.schema.json` `connection_ids`, `session-open.md` steps 3 and 4,
`device-login.md` "Reusing a prompted connection"; the fixture `C1/reuse-prompted-connection-never-implicit` is now
`C1/reuse-prompted-connection-offered-to-its-principal`), and one sentence of v0.11.1:

| # | Where | Old sentence (v0.11.1) | New wording |
|---|---|---|---|
| M12 | `session-open.md` step 4 | "`connection_ids` MUST list the principal's own candidates and no other principal's, and none opened with a supplied login" | "… the principal's own candidates, a prompted connection among them, and no other principal's, and none opened with a supplied login" |

**And two more, found when code 8 was ruled (2026-11-01 21:53, the operator: TSB's own prompt text gets a recording code of its own):**

| # | Where | Old sentence (v0.11.1) | New wording |
|---|---|---|---|
| M13 | `ttyrec` `x-sidecar` `reopen` | "a code-5, code-6 or code-7 frame counts for neither" | "a code-5, code-6, code-7 or code-8 frame counts for neither" |
| M14 | `edges/CO-DK.md` Counts | "Recording codes 8 (0–7), from the release that adds `SessionCreateRequest.login`." | "Recording codes 9 (0–8), … and, for code 8, Release B", with a table row for codes 6 and 7 and one for code 8 |

`C8/end-marker-killed` is a v0.1 fixture whose invalid case 3 named code 8 as a code no tag defined (code 7 until Release A);
it names code 9 now, its sidecar and problem text only, which the digest tests allow (`TEXT_ONLY`).

**Sentences this release makes false and leaves, because digests pin their fixtures** (`tests/v01-`, `v02-`, `v05-` and
`v010-fixture-digests.json`: a fixture of an earlier release is not edited, as v0.11's list did for `C3/ends-from-outside`).
Their instances and verdicts are unchanged and still right; their prose reads against this release: `C3/control-message-shape`
(description: "Credentials belong in connect.auth and nowhere else": now also `auth_answer`),
`C3/control-connect-openshell-sequence` (a note: "credentials travel only here"), `C3/supervisor-request-shape`
(description: "credentials reach a worker only through connect.auth": now also `auth_answer`),
`C6/event-target-auth-failed` (description and a note: "written only for a refused supplied login", "only a supplied login is
audited here": a device login is audited too, `C6/event-target-auth-failed-prompted`), `C6/event-session-connect-login`
(description: "`detail.login` is stored or supplied": `prompted` too, `C6/event-session-connect-prompted`),
`C2/attach-first-frame-invalid-close-4000` (description: "The first frame must be the attach frame": on a session socket; a
device-login socket opens with `device_login_attach`).

**Fixtures: 44 new, every one `published: e3`, `required-green: e5`** (OQ7 (b): E4's exit at `--profile e4` does not wait on
Phase B; Release A's stay at e4). C1: `session-create-device-login-202`, `-needs-v0.7-503`, `-limit-429`,
`-unacknowledged-409`, `-not-ssh-422`, `-no-join-403`, `reuse-prompted-connection-by-id`,
`reuse-prompted-connection-offered-to-its-principal`. C2: `ws-frames-shape-device-login` (`kind: instance`, a valid and an invalid
instance of each of the seven frames), `device-login-relay`, `-order`, `-hostkey-first-use`, `-ticket-once` and
`-ticket-kinds` (live, run against `tsbc serve`), `-cancel`, `-after-connected`, `-permission-lost`, `-answer-refused`,
`-unattached-expires`, `-hostkey-audit-unwritable`. C3: `device-login-sequence`, `hostkey-ask-before-any-auth`, `hostkey-refused`,
`hostkey-revoked-refused-before-ask`, `auth-failed-codes`, `device-login-bad-answer-ends`, `device-login-partial-success`,
`device-login-connect-without-login-timeout-refused`, `device-login-disconnect-cancels`, `device-login-peer-gone-drops`,
`device-login-version-gate`, `hostkey-audit-unwritable-disconnects`, `control-message-shape-v0.7`, a frozen `control-message-shape-v0.6`,
`n1-v0.6-worker-positive`. C5: `config-login-timeout`, `config-device-logins-max`, `inventory-target-login-timeout`. C6:
`event-target-auth-failed-prompted`, `event-target-hostkey-refused`, `event-session-connect-prompted`,
`connection-record-login`. C8: `recording-device-login-frames` (its head opens with the code-8 `login as:`) and `recording-device-login-own-prompt` (a keyboard-interactive round and a password round, three invalid recordings). Each reference fixture has an `owed` entry in
`graded-by.json` naming its unit (U2, U3, U5, U7 or U11). Every credential-shaped value is the synthetic constant, every
fingerprint a synthetic one in the `SHA256:` form. `C1/session-create-device-login-not-ssh-422` is `reference`, not live
as the plan's table says: no run input names a non-ssh target (the ruling G2 of Release A, for the same fixture). The
plan's "(ws, reference)" C2 rows are `kind: instance` fixtures, as `C2/stale-flow` is: the fixture format has no reference
grade for `kind: ws`.

**Known gaps.** The changed-key acceptance is Q10's later item (a later `kind` on `hostkey_ask` and `device_login_hostkey`,
never a new message). The limiter on refused logins stays deferred. A connection opened with a supplied login stays never
reusable. A `session.connect` rebuilt at a core restart has no `refused_attempts`, which is not persisted. When the core
cannot write `target.hostkey_accepted` for an accepted key (ruled 2026-11-01 15:00), it sends the worker `disconnect`, never
`hostkey_answer {accept: false}`; the pane is told `connect_failed`, the socket closes 4016, the failure is logged and no
`target.hostkey_refused` is written (device-login step 13).

**Review fixes (#109, round 3), one line each.**

- Fixed: the client ignores a device-login frame whose `v` is not 1 as it ignores an unknown `t` (V1), so a `device_login_result` of another `v` can never set the connected state.
- Fixed: `AsyncDeviceLogin.attachment()` closes the WebSocket when its wait for `attached` fails (a timeout, a receive error, a cancel), as the ordinary attachment opener does; the socket passes to the attachment only with a valid `attached` frame.
- Fixed: a prompted connection reused by its `connection_id` is decided first (`tsbc serve`, session-open step 3, device-login "Reusing a prompted connection"), with no credential resolved: no resolving-credential, credential-choice or mapped-credential refusal answers it. The steps keep their numbers; step 3 now says the exception runs first. No schema, fixture or wire change.

## v0.11.1 — 2026-10-31

Fixed: the published client's `ApiError` gains `detail`, the error body's members beside `code` and `message` that
the client dropped before (`candidates`, `attempts_remaining`, `connection_ids`, `credential_refs`,
`credential_ref`), copied as the JSON decoded them; `str(err)` is unchanged, the non-JSON body path passes no detail,
and no path copies a request into `detail`, so a supplied login or a password never enters an exception. No schema,
fixture or protocol change. Additive (V1): every call the client makes and every wire format are as at v0.11.0.

## v0.11.0 — 2026-10-29

Added: the vault release (E4) — C7, the split-key vault and device trust; C1's E4 auth and lock operations; C6's vault
records. A principal's credentials are stored sealed under a key derived from a passphrase the broker never sees
(credential-protection-model.md, cred below); a surface unlocks the vault for one login, untrusted (both halves of
master_hash) or trusted (Part 1 and a signed device challenge); the core holds the unlock under the vault lock and
uses it only at session establishment. Additive throughout (V1): every request, schema and fixture valid at v0.10.0
stays valid, and v0.10.0's fixtures are unchanged (`tests/v010-fixture-digests.json`).

**C7 — the vault and device operations** (OpenAPI; protocols vault-unlock and device-trust, new).

- **Initialisation in two calls** (OPEN-1.3 (c) and the ruling of 2026-10-29 21:07): the mint, `POST /vault/salt`, no
  body, stores fresh `user_salt` and `salt2` in a vault minted but not sealed and answers `user_salt` with `initialized`
  false (a repeat while unsealed answers the same salts; on a sealed vault 409 `vault_exists`); then init, `POST
  /vault/init` `{part1, part2}`, the halves derived from that salt, seals an empty set and holds an unlock on the login
  (409 `vault_not_minted` before any mint, `vault_exists` on a sealed vault). Neither writes an audit line. An unlock or a
  registration on a minted, unsealed vault is 409 `vault_not_initialized`; a minted vault does not expire, and a wipe
  removes it. `vault_user` has the two states (salts only, or salts with the sealed set and its index).
- `GET /vault/salt` (`VaultSalt`: `initialized`, true once sealed; `user_salt` when minted or sealed; writes nothing),
  `POST /vault/unlock` (`VaultUnlockRequest`: `untrusted` with both halves, or `trusted` with Part 1, a challenge and
  its signature), `POST /vault/part2` (Part 2 regeneration from the login's tied device), `DELETE
  /vault/users/{username}` (a vault administrator's wipe, D16).
- `POST /devices/challenges` (a challenge for one operation and one device: `device.register`, `login.bind`,
  `vault.unlock`, `part2.regenerate`; 32 bytes, a login handle, `expires_in` 1 to 60 s), `POST /devices` (register:
  both halves, an SPKI P-256 key, a signature, a name; proves the passphrase), `GET /devices`, `DELETE
  /devices/{device_id}` (revoke). Revoke declares no 403: another principal's device, asked about by a caller without
  `vault.admin`, is 404 `device_not_found`, the answer of an absent one (device-trust step 9; OPEN-1.6 (a)).
- `GET`, `POST /vault/credentials` and `DELETE /vault/credentials/{reference}` (the personal tier: kind `key`,
  `password` or `none`, a login name with every secret); `GET`, `POST /vault/org-credentials` and `DELETE
  /vault/org-credentials/{reference}` (the org tier, an unlock class required for every kind); `POST /vault/grants`,
  `POST` and `DELETE /vault/grants/{grant_id}`.
- The challenge's signed bytes, the associated data of every blob and the byte forms are `vault.schema.json`
  (`x-associated-data`, `b64_16`, `b64_32`, `signature`, `public_key`). Bytes are padded base64 on the wire;
  `device_id` is base64url (it is a path segment).
- **Vectors** (published e4), `vault-vectors.schema.json`: known answers from the standards — argon2id from RFC 9106
  §5.3 and from the reference implementation's own tests (OPEN-1.7 (c)), PBKDF2-HMAC-SHA256 (RFC 7914 §11), AES-256-GCM
  (the GCM specification's 256-bit cases 13–16), ECDSA P-256 P1363 (a named Wycheproof subset), SHA-256 (FIPS 180-4's
  examples), OpaqueString (RFC 8265 §4.2; computed, as the RFC prints none); and eleven chain vectors, from the
  passphrase's bytes to the challenge's signed bytes, each naming the two implementations that must reproduce it. The
  chain vectors were computed once by tsb-program's `tools/author-c7-vectors.py` (DEFAULT 55: a third tool, exact
  pins in its header, run by no CI, outside `contract/` so the import-lint keeps seeing every Python file the contract
  ships). Every vector file is hex.
- **Fixtures**: one valid and at least one invalid instance per operation; the replay suite and the challenge's
  issue-time checks; the twelve NO rows of cred §6.1–§6.3 as corrected (`C7/breach-*`); the behaviour the core owes
  (attempt limit, Part 2's TTL, registration, revocation in both policies, the wipe). Every required-green one is a
  reference fixture owed to the E4 phase that builds its grader (`fixtures/graded-by.json`).

**C1 — auth, locks, session create.**

- `Principal` (the login answer) and `Me` (`GET /auth/me`) gain optional `locks` (`LockSettings`: `screen_lock_after`,
  `vault_lock_on_idle`, `vault_idle_after`, `vault_hard_cap`, `activity_interval`, seconds): the caller's resolved lock
  settings (H5 (b); no new endpoint).
- `POST /auth/bind` (tie this login to a trusted device, once), `POST /auth/activity` (the user-activity signal, 204
  always), `POST /auth/lock` ("lock now"), `POST /auth/screen-unlock` (the screen lock's password re-check; a wrong one
  is 403 `screen_unlock_failed`, never 401); protocols vault-lock and screen-lock (new). `POST /profiles/batch`: the
  principal's Layer 2a mapping of targets to its own credentials (Storage §10.6's batch, its credential mapping only).
- `SessionCreateRequest`: both branches take `credential_ref` with `credential_layer` (both or neither), the operator's
  choice after a 409 `credential_choice_required`, which now carries `candidates`; on `new` the choice and `login` are
  exclusive in one request (422 by shape). A Layer 2a mapping whose reference is not in the principal's vault (a
  `profiles.yaml` edited or restored by hand) is 409 `mapped_credential_missing`, carrying the reference as
  `credential_ref`, never skipped silently (Contract §12). A vault-resolved session's login name is the credential's, never the target
  file's `options.user` (DEFAULT 52). A new connection whose personal credential needs an unlock this login does not hold
  is 409 `vault_locked`. A new connection whose org credential is of class `user_unlocked` needs its storing
  administrator's unlock, held by any login of that administrator, and never the caller's (credential-protection §8.2,
  §5.5); while no login of the administrator holds one it is 409 `org_credential_locked`, fixed text and no field: the
  surface says the administrator must unlock and never asks for the operator's passphrase (ruling 2026-10-30 00:58; the
  code is a DEFAULT) [C1/session-create-org-credential-locked-409].
- **Error codes** (`error.schema.json`): `vault_locked`, `vault_not_initialized`, `vault_exists`, `vault_not_minted`,
  `unlock_failed` (carries `attempts_remaining`), `part2_missing`, `challenge_failed`, `credential_exists`,
  `credential_has_grants`, `broker_unlock_unavailable`, `vault_wipe_blocked` (carries `credential_refs`),
  `mapped_credential_missing` (carries `credential_ref`), `org_credential_locked` at 409; `device_not_trusted`,
  `screen_unlock_failed` at 403; `device_not_found`, `credential_not_found`, `grant_not_found`, `user_not_found` at
  404. `rate_limited`, `forbidden`, `invalid_request` and `credential_choice_required` mean more, as their meanings say.
  **An E3 surface meets every new code by its HTTP status (V4)**: a create on a credential that needs an unlock can now
  answer 409 `vault_locked`, and one on a user-unlocked org credential 409 `org_credential_locked`.
- Vault and device writes declare 500 (`ActNotAudited`): an act whose audit line cannot be written is refused, as a
  login is (DEFAULT 49).

**C2 — close code 4015 `device_revoked`.** A client whose login was ended by a revocation of the device it is tied to;
no auto-reattach. **An E3 client meets it as an unknown 4xxx, which it reads as 4010** (README "Senders and
receivers"): it does not reattach, though it shows a session that lives on as ended (red-team LOW-11).

**C5 — policy.yaml and config.yaml.** `policy.yaml`: the permission `vault.admin` (Q6 (a)); `recording.force`, target
filters whose sessions are recorded whatever `recording.enabled` says (Q9 (a); a single target by `id:`, O16 (a));
`devices.revocation` (`end_logins`, the default, or `next_connection`, 5.40c) and `devices.inactive_after`.
`config.yaml`: sections `vault` (`challenge_ttl`, `unlock_attempts`, `unlock_window`, `part2_ttl`), `locks`
(`screen_after`, `vault_on_idle`, `vault_idle_after`, `vault_hard_cap`, `activity_interval`) and `auth`
(`login_lifetime`, `login_idle`), optional in the schema like every later knob; the E4 core requires them.

**C6 — vault records and audit.** `vault.schema.json`: buckets `vault_users` (salts, the sealed set and its plaintext
index of reference and kind), `devices` (keyed by user and device), `grants`, `org_credentials`; the sealed set's and an
org blob's plaintext; `users/<name>/profiles.yaml`'s shape; the Redis key `vault:part2:{user_id}:{device_id}`
(`x-redis`). Audit (`audit-event.schema.json`): `cred.store`, `cred.delete`, `cred.grant`, `cred.revoke`,
`cred.scope_change` (`cred_event`, naming the credential by reference and layer; `cred.store` carries the unlock class
for an org credential and none for a personal one; a delete or a wipe writes its line, then rewrites
`users/<name>/profiles.yaml` without the cleared mappings, then commits bbolt, so a failed commit leaves the credential
live and unmapped, logged: O30's order, ruled 2026-10-29 21:07), `device.register`, `device.revoke` (`device_event`), `vault.wipe`
(`vault_wipe_event`), `auth.screen_unlock_failed`; `session.connect` gains `credential_ref` and `credential_layer`.
`connection_record` gains `credential_layer`. Shared shapes in `common.schema.json`: `credential_ref` (1 to 64
path-safe characters; `supplied` refused), `credential_layer` (`personal`, `org`), `credential_kind`, `unlock_class`,
`device_id`, `login_name`, `display_label`, `device_name`.

**Conventions.** V3 (4015), V4 (the new codes), V7 (the E4 knobs; resolved settings are values the core states), V9
(the vault administrator), V10 (the device proof, for the vault unlock only; the cookie stays unbound), V12 (bytes
base64 on the wire and hex in vector files; free text bounded and rendered as text), V14 (the credential choice has
arrived, as reference and layer).

**Exit status wording** (session model §12, 2026-10-30). V13, edge PW-TG, session-end-from-outside step 1 and two
`ipc-control.schema.json` descriptions now say an exit status is never acted on, where they said it is never read: it
decides nothing, the contract does not carry it at this release, and carrying it to the clients as data is E5's, with
exec. Wording only; no shape changes.

**Protocols.** New: vault-unlock, device-trust, vault-lock, screen-lock. Grown: login (steps 13–17 normative; open items
1 and 2 answered; open item 7's CSRF half answered, Q10 (ii)), session-open (step 3; steps 7, 18, 19), session-detach,
config-reload. Edge catalogues SF-CO, CO-BB, CO-RD, CO-DK list the vault's messages and records.

**Tooling.** `tsbc/selfcheck.py`: `part1`, `part2`, `master_hash`, `storage_key`, `encryption_key` join the credential
keys, with five base64 synthetic constants; the published vector files (`fixtures/C7/kat-*.json`,
`fixtures/C7/chain-*.json`) are exempt from the credential-key rule by path and by nothing else (DEFAULT 44). `tsbc
serve` answers every new operation with canned data and verifies no signature (DEFAULT 16). The client gains one
method per operation (`vault_mint_salt` and `vault_init` among them) and `create_session(..., credential=(ref, layer))`; it carries bytes the caller derived and
computes nothing.

**cred §11.1's names** (OPEN-1.2 (a): the contract's own resource style): `GET /vault/user-salt` → `GET /vault/salt`;
`POST /vault/unlock` → `POST /vault/unlock` (it returns no credential: N1 item 11); `POST /devices/register` → `POST
/devices`; `GET /devices/list` → `GET /devices`; `DELETE /devices/{id}/revoke` → `DELETE /devices/{device_id}`.

**Renamed from the plan's working names**: `C1/credential-choice-required-409` (required e4) is
`C1/credential-choice-required-candidates-409` — the v0.1 fixture of the first name is kept, unchanged, published e0;
`C7/challenge-signed-bytes` is `C7/chain-challenge-signed-bytes` (one path rule exempts every vector file);
`C7/kat-aes256gcm-*` is one file, `C7/kat-aes256gcm`; `C7/kat-argon2id-*` are `kat-argon2id-rfc9106` and
`kat-argon2id-reference`; `profile_map` is `POST /profiles/batch`; the screen-lock re-check is `POST
/auth/screen-unlock`; "lock now" is `POST /auth/lock`.

**Values picked by the contract lane, for the operator (5.36; the templates' to ship, never a schema's):**
`vault.challenge_ttl` 30 s; `vault.unlock_attempts` 5 and `vault.unlock_window` 1 h (the corpus's); `vault.part2_ttl`
720 h (30 days); `locks.screen_after` 15 min, `locks.vault_on_idle` true, `locks.vault_idle_after` 30 min,
`locks.vault_hard_cap` 8 h (the corpus's); `locks.activity_interval` 60 s; `auth.login_lifetime` 24 h; `auth.login_idle`
12 h (the built constant). Bounds: a reference 1–64 characters, a label 1–128, a device name 1–64, a login name 1–256, a
password secret 1–1024, a key secret 1–16384, at most 1024 entries in a vault, 64 selectors in a grant, 1000 mappings in
one batch; close code 4015. Named at the 2026-10-29 21:07 ruling's request: init's path `POST /vault/init`, and the codes
`vault_not_minted` (init before any mint) and `mapped_credential_missing` (a mapping to a reference the vault no longer
holds).

**Known gaps.**

- Passphrase change has no flow (DEFAULT 18); vault unlock and lock audit events wait for cred §11.2.
- The pinned-key reset "with the vault tag" (contract-v0.1, contract-v0.2): an administrator's acceptance of a changed
  host key, and a declined first-use key, are one item after E4, each needing a worker message and a C3 change (Q10).
  The credential-choice field "with the vault tag" has arrived (above).

**Sentences of earlier releases this release makes false** (kept as written in their fixtures and entries, which
v0.10.0's digests pin): `C1/credential-choice-required-409`'s "At this tag one credential resolves … the field that
carries the operator's choice arrives with the vault tag"; `C3/ends-from-outside`'s "no exit status is read" (an exit
status is now never acted on; not carried at this release; carried to the clients as data at E5); error `x-codes`
`credential_choice_required`'s "at this tag one credential resolves" (rewritten here); protocols README's "Not in the
contract yet: vault-unlock, device-trust (E4)" (rewritten); login's "Steps 13–14 are E4, not in the contract yet" and
open items 1 and 2 (rewritten); session-open's G-12 and pinned-key known gap (rewritten); edge CO-RD's "vault Part 2:
none yet" (rewritten); contract-v0.2's and contract-v0.1's lines that the pinned-key reset and the credential-choice
field come with the vault tag.

## v0.10.0 — 2026-10-29

Added: a login supplied on session create (release A of login-at-connect), and the recording knob for it. An
unattended client, or an operator who has no stored credential for a target, supplies the target account's username
and password for one new connection; the core uses it only to authenticate that connection and never stores it.
Additive throughout (V1): every request, schema and fixture that was valid stays valid, and **no C3 change and no
`proto_version` step** — the worker is sent the `connect` it already serves for a stored password.

**C1 — OpenAPI.**

- `SessionCreateRequest`'s `new` branch gains optional `login` (`SessionLogin`): `{user, auth}`, closed. `user` is the
  target's account, 1 to 256 characters (the bound of C3 `connect.user`). `auth` is `SessionLoginAuth`, a `oneOf` with
  one branch at this release, `{kind: password, password}`: 1 to 1024 characters in the schema, and 1024 bytes of
  UTF-8 as the core's bound (a longer one is 422). The `reuse` branch stays closed, so a login on a reuse is 422
  `invalid_request` by shape. A core older than this release answers any `login` 422 too, so it never uses a stored
  credential in place of a login it was given.
- A login on a target whose `type` is not `ssh` is 422 `invalid_request`, before any worker is asked (session-open
  step 2a). A request with a login resolves no stored credential, so `no_resolving_credential` and
  `credential_choice_required` never answer it (409 and V14). A refused login is 502 `target_auth_failed`, as today.
  `POST /sessions`'s 422, 409 and 502 descriptions say so.
- **A new error code, for the operator to rule on its name: `login_recording_not_acknowledged`** (409, in
  `error.schema.json`'s registry). `SessionLogin` gains optional `recording_acknowledged` (boolean, absent means
  false): the operator's consent that the login be recorded. While the effective value below is true, a login that
  does not set it true is refused 409 with this code, before anything is sent to a worker, opened or recorded
  (session-open step 2b); the surface re-reads `/auth/me`, warns and repeats the request with the field set. With the
  value false the field is accepted and changes nothing.
- `GET /auth/me` answers a new `Me`: the `Principal` and an optional `recording.login_input` (boolean), so a surface
  knows before it sends a login. It is the **effective value**: true only while `recording.enabled` and
  `recording.login_input` are both on, so a surface never warns of a recording nobody writes. `POST /auth/login` still
  answers `Principal`.

**C5 — policy.** `policy.yaml` `recording.login_input` (boolean, optional, deployment-wide): whether a supplied login
is recorded. Absent means off, which is the default. It applies only to a recorded session, and the core reads it at
each create.

**C6 — audit.** `session.connect`'s `detail` names two optional keys, `login` (`stored` or `supplied`) and
`login_recorded` (true exactly when login frames were written); never a value. A new event, `target.auth_failed`
(`target_auth_event`), is written only for a refused supplied login: the TSB principal, the target, `detail.login`
(`supplied`) and the connection when there is one; no session, no password, no device reason. A refused stored
credential writes no event, as before.

**C8 — recording.** Sidecar codes 6 (login prompt) and 7 (login answer), defined with their line, `x-login-line`: CR
LF, a fixed tag, the text, CR LF. The text is the value's UTF-8 with a backslash written as two and every C0, C1 and
DEL character written `\xNN` (lowercase hex of the code point), so the line is always one printable row and a replay
acts on no control in it; the escape is the only encoding. Two frames of code 7, the username then the password, stand
at the head of a recording, before every other frame, stamped on the core's input clock, when the knob is on and the
session is recorded; with it off no byte of the login is recorded. A supplied login asks nothing, so no code-6 frame is
written; the code is defined so the line is fixed once. Codes 6 and 7 count for neither seq in the reopen walk, are left
out of the payload in "payload-identical" and are compared line for line. A reader of an earlier tag refuses them, as a
v0.2 reader refused code 5. `tsbc` writes, parses and self-checks the line and its escape (`login_line`,
`parse_login_line`), and refuses a login frame that is not the line of its code or not at the head.

**Conventions.** V10: a target credential travels only in `SessionCreateRequest.login` on C1 and in `connect.auth` on
C3, and a supplied login is never stored, logged, audited or echoed. V14: a login the request carries is the
operator's choice of credential. V4: no message on the create path echoes a value of the request. The redaction list
(artifacts-collected, README) gains `login.auth.password`, and a collected recording must not hold a code-7 login answer
frame: a collector leaves such a recording out, and `tsbc`'s clean-collection gate refuses a collection whose recording
sidecar holds a code 7, naming the file (a code-6 prompt is not refused).

**Protocols.** `session-open` steps 1, 2a (new), 2b (new: consent to a recorded login), 3, 4, 7, 11, 17, 18, 19 and 22, "Refusals and who is told",
"Security considerations" and a new open item (reusing a connection opened with a supplied login: never at this
release); `edges/SF-CO.md`, `login.md` (`/auth/me`), `byte-relay.md` Security (a supplied login is not session input).
A connection opened with a supplied login records `credential_ref` `supplied`, a constant that is a valid `id`:
neither the password nor the user name is written to bbolt, and such a connection is never a reuse candidate: not
implicitly, not by `connection_id` (422), and not in a 409's `connection_ids` (the core keeps a marker on the
connection that it was opened with a supplied login).
`supplied` is reserved: no stored credential, now or in a later release's vault, may use it as its reference.

**Client.** `Client.create_session(…, login=None)` sends the object as given, in the request body and nowhere else; it
never logs it.

**Fixtures** (all published at e3 and required green at e4, so nothing turns an e3 run red): C1
`session-create-supplied-login`, `session-create-login-not-ssh-422`, `session-create-login-never-echoed` and
`session-create-login-unacknowledged-409` (reference,
graded by the core's own tests: no live fixture names a non-ssh target and a synthetic password authenticates
nowhere), `session-create-login-on-reuse-422` (live); C3 `control-connect-supplied-login` (`control-sequence`); C5
`policy-login-input`; C6 `event-session-connect-login` and `event-target-auth-failed`; C8 `recording-login-frames` and
`login-line-escape`. Every credential value is the synthetic `FIXTURE-NOT-A-PASSWORD`. Their `graded-by.json` entries
are owed by the core and schema plans for a supplied login. `tsbc serve` and the test stub implement the login rules,
so the serve acceptance run passes. One v0.1 fixture changes in a case, not in meaning: `C8/end-marker-killed`'s refused
sidecar code is 8 where it was 7, which this release defines (the additivity records follow: the exemption names that case's `sidecar_hex` and `problem` and nothing else of it).

Known gaps: only a password is supplied (a key or a prompt is a later branch of `SessionLoginAuth`); a connection opened
with a supplied login is not reused; no permission or knob turns supplied logins off, and no rate limit bounds guesses;
`GET /auth/me` carrying `recording.login_input` has no fixture of its own; the warning a surface shows before it sends
a recorded login has no fixed text or code. Corpus: open_decisions §5, credential-protection-model §8.2, §10.1, §10.2
and storage-and-discovery-architecture §10.1a land with this release.

## v0.9.0 — 2026-10-28

Changed (protocol text only; no schema or fixture changes): `protocols/login.md` open item 2, what a core restart
does to a login session, is settled by the corpus — logins live in the core's memory, so a restart ends every one
(credential §3.2; Storage §5 no longer lists web sessions in bbolt). A fixture for the restart end is owed with the
E4 contract. Corpus: credential-protection-model.md, frontend-extensibility-model.md,
storage-and-discovery-architecture.md, rationale/change-log.md (the E4 vault answers and the two locks).

## v0.8.1 — 2026-10-27

Fixed: the open-shell bound's graders are named. Every entry owed by the open-shell bound in `fixtures/graded-by.json`
now names the tsb-core tests and rig that grade it, each read at tsb-core `main` 5339eaa: U3 for
C3/open-timeout-expired; U2 for C3/open-timeout-expired, open-late-closed-released and n1-v0.5-worker-positive; U5 for
C5/config-open-timeout and inventory-target-open-timeout; and U11 for C3/n1-v0.5-worker-positive
(`rig-n1-positive-v05`). C3/open-timeout-shape and C3/open-timeout-version-gate stay graded by tsbc's
`control-open-timeout` check over a collection, so they gain no entry.

## v0.8.0 — 2026-10-27

Corpus only: README.md, expanded-capabilities.md, frontend-extensibility-model.md

## v0.7.0 — 2026-10-27

Added: a bound on a shell's open, carried on `open_shell` and set by a `config.yaml` knob with a per-target override;
C3 moves to `proto_version` "0.6" (tsb-core #348, phase 2).

**C3 — core ↔ worker.**

- `open_shell` gains optional `open_timeout` (`common#/$defs/duration_ms`): how long the worker waits for the target
  to open the shell (the `session` channel, `pty-req` and `shell`). Past the bound the worker gives the open up: it
  answers `error` naming the session, with no `code` — the shape it already uses for an open the target refuses —
  never sends `channel_opened` for that session afterwards, and closes a channel the target already confirmed.
  Absent means no bound: an older core sends none, and the worker waits for the target as before. Until now a
  target that held the open held the worker's wait with no limit (tsb-core #348).
- The core bounds its own wait for `channel_opened` by the target's `open_timeout` plus one `heartbeat_interval`, so
  the worker's `error` arrives first; on an `error` that arrives after it gave up, it drops the open (session-open
  steps 12–14). session-open's open item "no knob bounds the core's wait for `connected` or `channel_opened`" is
  narrowed: the wait for `channel_opened` is bounded, the wait for `connected` stays open.
- That wait applies toward every worker, with or without `open_timeout`: a worker below 0.6 is given no bound, and
  the core waits the same. A `channel_opened` that arrives after the core gave up (from a worker below 0.6, or a 0.6
  worker racing its own bound) is closed with `close_channel`, and on the `channel_closed` that answers it the core
  sends `release`, recording nothing (no session record, no recording, no audit line): it never offered the session.
  `release`'s description gains that case (session-open steps 12 and 14). The fix sits in the core, so it works with
  frozen older workers, and it settles tsb-core #376: a late open's session was never released, so a worker kept
  listing it until its connection closed.
  Before that `release` the core deletes the session's spool entry, if any (one the worker wrote when it ended the
  channel by itself or saw the target close it), auditing nothing; `spool-entry`'s description gains the case, and
  CO-FS's "spool entry, deleted" row gains session-open 14. The late rule is for an open still unanswered: a
  `channel_opened` after an `error` that answered the open is a worker fault, which the order rules refuse, logged.
  A late open whose end lands while no core is attached stays as it was: its entry is left in place and logged at
  the next start (the accepted restart-window risk).
- `proto_version` is "0.6" on `hello`, `hello_ack`, both manifests and `worker_created`. The core speaks 0.6, 0.5,
  0.4, 0.3 and 0.2 and sends `open_timeout` only to a peer at 0.6 or later, so an older peer gets exactly its own
  version's messages; a rolled-back 0.5 core sends no bound, and a 0.6 worker then waits as a 0.5 one did. Cores are
  upgraded before workers, and rolled back after them.

**C5 — the knob.** `config.schema.json` gains `worker.open_timeout`, beside `undialled_timeout` and `reap_timeout`:
the bound on a shell's open, a positive whole-millisecond duration, optional in the schema as every later knob is.
`inventory-target.schema.json` gains the per-target override `options.open_timeout`, integer seconds as the other
per-target timer overrides; a target's own value overrides the knob. The default is `config.yaml`'s, never a
schema's (decision 5.36). V7's knob table gains the row.

**Fixtures**, each published at e3 and required green at e4: `C3/open-timeout-shape` (an `open_shell` with the bound
validates, with none validates, and 0, a duration string, a non-integer or a value past 2^53-1 is refused);
`C3/open-timeout-expired` (the bound expires: `error` naming the session, no `code`, and no `channel_opened` after
it; reference, its U3 and U2 tests owed by the worker and core plans for the open-shell bound); `C3/open-timeout-version-gate`
(the bound on every `open_shell` toward a peer at 0.6 or later and on none toward an older one);
`C3/n1-v0.5-worker-positive` (a 0.6 core drives a frozen 0.5 worker with exactly 0.5's messages: `hello` in 0.5 and
no `open_timeout`; reference, its U2 test owed by the core plan and its U11 rig `rig-n1-positive-v05` by the harness
plan for the open-shell bound); `C3/open-late-closed-released` (toward a 0.5 worker, no `open_timeout`; the core's
wait expires, then the late `channel_opened` is answered `close_channel`, and its `channel_closed` by `release`;
reference, its U2 test owed by the core plan for the open-shell bound); `C5/config-open-timeout`
(`worker.open_timeout` as a positive whole-millisecond Go duration validates, absent validates, a non-duration or negative string is refused; 0s and a fractional millisecond
match the pattern and are the loader's refusal cases); `C5/inventory-target-open-timeout` (`options.open_timeout` of
20, 1 or absent validates, and 0, a duration string, a non-integer or a value past the maximum is refused); both C5
fixtures reference, their U5 test owed by the schema plan for the open-shell bound.

**Runner.** tsbc speaks C3 0.6 (`C3_VERSION`), so a 0.6 peer is spoken to in 0.6 and a later one is offered 0.6. New
check `control-open-timeout`: every `open_shell` toward a worker at 0.6 or later carries `open_timeout`, and none
toward an older one, by the peer's version in its manifest or `worker_created`; it grades `open-timeout-shape` and
`open-timeout-version-gate`. The order rules already refuse a `channel_opened` after the `error` that answered its
open.

**What each unit must change.** U5: the knob and the per-target override in the loader. U2: send `open_timeout` to
peers at 0.6 or later; wait for `channel_opened` the bound plus one `heartbeat_interval`, toward every worker; drop
the open on a late `error`; close a late `channel_opened` and release it on its `channel_closed`, recording
nothing, after deleting the session's spool entry, if any, with nothing audited; log a `channel_opened` that follows
the `error` that answered its open; speak 0.6, 0.5, 0.4, 0.3 and 0.2. U3: bound the open by `open_timeout` and answer
`error` naming the session past it; close a channel the target already confirmed; advertise and serve 0.6. U11:
freeze a 0.5 worker beside the earlier frozen workers, and a rig that drives it from a 0.6 core (`C3/n1-v0.5-worker-positive`).

Nothing valid before becomes invalid: `open_timeout` is optional in `open_shell`, `worker.open_timeout` in the config
schema and `options.open_timeout` in a target file, and no released fixture changes.

## v0.6.2 — 2026-10-26

Fixed: the fixture server's stale prompt follows the protocol; two fixtures that contradicted the contract are
corrected; a fixture can require a header to be absent (tsb-core #121).

**`tsbc serve` — the stale choice (stale-screen rules 4 and 7; typing-ownership rule 16).**

- Any move of the keyboard while a stale choice is pending voids the outstanding prompt. The client that receives
  the keyboard gets the `token` frame, then a new `stale_prompt` with a new `prompt_id`. A move to escrow asks
  nobody. Until now the prompt and its id survived every move.
- On attach, `stale` goes to the joining client only, and the prompt only if the joiner holds the keyboard. Until now
  every attach re-sent both to every client.
- An answer from a client that is not the current holder is refused `not_allowed`, which covers a former holder
  answering a voided prompt. The current holder answering with an outdated `prompt_id` is still refused
  `not_pending`, as shipped: stale-screen open item 6 leaves that case unspecified.

**Fixtures and the grader.**

- `C1/output-page-ground-start` step 3 required `TSB-Page-More: false` beside `TSB-Page-No-Ground: true`, against its own
  description and `outputPage` ("an explicit false end-of-history is never sent"). It now requires `TSB-Page-More` to
  be absent: a fixture header whose expected value is `null` means the response must not carry that header
  (`fixture.schema.json`; `tsbc`). scroll-control's open item that named this contradiction is removed.
- `C6/event-vocabulary-v0.3`'s `detach` instance named `bob` as the user of alice's client; the user is now the
  departing holder.

## v0.6.1 — 2026-10-23

Fixed: the E3 graders are named. Every entry owed by an E3 plan in `fixtures/graded-by.json` now names the tsb-core
tests and rigs that grade it, each read at tsb-core `main` e92698d: U2 for C1's five target fixtures and three
session-create fixtures, C2/size-not-resizable, C3's config-reload-timers, n1-v0.4-worker-positive,
telnet-one-session, telnet-socket-close and target-address-refused, C5's inventory-layout, inventory-bulk-swap and
inventory-write-then-observe, and C6/session-record-protocol-telnet; U3 for C3's link-state-telnet,
telnet-one-session, telnet-socket-close, target-address-refused, telnet-output-from-connect and
telnet-payload-bytes; U5 for C5's config-valid, inventory-layout, inventory-target-valid and inventory-yaml-mapping;
and U11 for C3's config-reload-timers and n1-v0.4-worker-positive and C5's inventory-bulk-swap and
inventory-write-then-observe. The known gap "the graders owed by the E3 unit plans" at v0.6.0 is closed.

## v0.6.0 — 2026-10-19

**Added.**

- **The inventory file, its layout and its lock (C5).** `inventory-target.schema.json` is the shape of one target
  file: `id`, `type` (`ssh`, `telnet`, `serial`, `echo`), `name`, `group`, `tags`, `discovered_by`, `last_seen`,
  `options`; the top level and `options` are open, because a target file is the one shape that must forgive field
  changes, and a reader ignores what it does not use. `password`, `privateKeys` and `credential_ref` are refused at
  the top level and in `options`: secrets never live in inventory. The layout — the `current` link, one file per
  target named by its id, the atomic write, the bulk swap by rename, the staging directory, the single lock,
  retention, the refusals for a duplicate id and an invalid file, and YAML's data model for a scalar a schema types
  as a string — is the schema's `x-layout`, graded by `C5/inventory-target-valid`, `C5/inventory-layout`,
  `C5/inventory-yaml-mapping`, `C5/inventory-write-then-observe` and `C5/inventory-bulk-swap`.
- **New alert kinds for the inventory are a later item:** at this release a refused target file, a missing `current` link and a refused `config.yaml` are log lines, not alerts. Two more reports join them: a duplicate id (two sources holding one), and a changed `config.yaml` key that does not reload.
- **`config.yaml`'s knob shape (C5).** `config.schema.json` lists the knob sections — `timers`, `worker`, `size`,
  `control`, `attach`, `vt`, `snapshot`, `inventory` — with each key's range as the loader enforces it, and `required`
  names exactly the knobs the core refuses to start without. Everything else in the file is deployment-local and stays
  the loader's rule. The E3 knobs are `inventory.keep_versions` and `inventory.staging_max_age`. Discovery keys are
  not in this release's schema: they arrive with a later item. New fixtures `C5/config-valid`, `C5/inventory-layout`,
  `C5/inventory-yaml-mapping`, `C5/inventory-write-then-observe`, `C5/inventory-bulk-swap`.
- **`GET /targets` with filters and a cursor, and `GET /targets/{id}` (C1)**, with `TargetRecord` and `TargetList`;
  the client's `list_targets`, `iter_targets` and `get_target`; and `tsbc serve` answering both, from an inventory of
  documentation addresses. A record never carries a broker-host path, `options`, `user`, a credential reference or a
  login script; a serial record has no `port`. The two endpoints read the index, resolve no credential, answer 401
  without the cookie, 422 for a parameter outside the list, 404 alike for an absent and an invisible target, and 503
  `inventory_unavailable` when the index is down. New protocols `inventory-list` and `fleet-discovery`; fleet-discovery's
  discovery worker (its steps 1–3) is not placed: Type-1 discovery left E3 (the operator, 2026-10-19).
- **Telnet sessions on C3, one per connection.** A `connect` with `protocol` `telnet`: no `user`, no `auth`; it names
  `term`, `cols` and `rows`, which the target asks for the moment TCP is up, and goes only to a peer at
  `proto_version` 0.5 or later. The connection carries one session, ever; a later `open_shell` on it gets
  `error not_supported` naming that session and nothing follows it; the worker closes the TCP connection when that
  session ends or its open fails, and reports it as `connection_lost` cause `single_session`, once the session's end
  has reached a core that knew the session (its `channel_closed`, the `error` answering the first `open_shell`, or
  the core's `release` for it — never `hello_ack` alone), and at the bound `heartbeat_interval × heartbeat_missed`
  with none. The bytes the target sends from the TCP connect up, after IAC processing, are the session's first output
  frames, from seq 1, held in its ring and replayed at its first handshake; past `buffer_bytes` the worker stops
  reading, so nothing is dropped there, and `link_timeout` is disarmed while reads are paused. The address check
  (ssh and telnet): the worker resolves `host` once, refuses an address in its deployment's deny list with
  `error target_address_refused`, naming the target id and never the address, checks an IPv4-mapped IPv6 address as
  the IPv4 address it maps, and connects to the address it checked.
- **`resizable` on `channel_opened`**, optional, absent means true: `true` exactly when the peer agreed to NAWS
  before the worker sent the event. The core keeps the value it was told for the session's life, so a core restart
  does not change it.
- **`telnet` as a session's protocol** in the continuity record (C6), and `inventory_unavailable` as an error code
  (503).
- **C3 `proto_version` "0.5"**: on `hello`, `hello_ack`, both manifests and `worker_created`. The core speaks 0.5,
  0.4, 0.3 and 0.2, offers each peer its own version, sends a telnet `connect` only to a peer at 0.5 or later, and a
  rolled-back 0.4 core never sends telnet.
- New fixtures required green at e3 across C1, C2, C3, C5 and C6; `tsbc`'s new check `control-connect-telnet` and its
  telnet order rules; the default profile is `e3`, and `--profile e2` still asserts exactly v0.5.0's set.

**Meanings extended by appended sentences** (nothing valid before becomes invalid):

- `target_connect_failed`: a refused target address, and a reuse of a telnet connection, which carries one session.
- `worker_unavailable`: a create on a telnet target when no supervisor provides a worker at 0.5 or later.
- `invalid_request`: a query parameter outside the list, or outside its range, on `GET /targets`.
- `connected`: for telnet, the TCP connection is up and the opening negotiation is done; nothing was authenticated.
- `session_type` `shell`: a telnet session is `shell` too; the continuity record's protocol says which.
- `resizable`: false also for a telnet session whose peer refused NAWS.
- **9.6 — telnet E3:** a `WILL`/`WONT TIMING-MARK` answering no probe the worker sent is not an E3, and the worker sends no reply to it; the ipc-control top's E3 sentence is aligned with link-liveness step 7 ("answering any probe sent").
- **9.7 — `link_timeout` and `missing`:** while a telnet worker's reads are paused before `open_shell`, the `missing` timer is disarmed with `link_timeout` (a `missing` already sent stays); both re-arm fresh at `open_shell` (link-liveness step 3, ipc-control top).
- **9.8 — `resize`:** one for a telnet session whose peer refused NAWS, or turned it off with DONT NAWS, is dropped by the worker with no answer; the core answers that size request `not_resizable` from `resizable` (ipc-control `resize`, size-control step 8, PW-TG).
- **9.9 — `link_state`:** during the single_session wait, each new control peer gets one `answering` `link_state` (`last_ack_at` the last E3) as its first event, and no timer-driven one (link-liveness step 10, ipc-control `link_state`).
- **9.10 — `single_session`:** the report goes right after the flushed `channel_closed`; a `hello` answer still pending is dropped with the peer (ipc-control top and `connection_lost`).
- **The connection's definition**, "one authenticated transport", in V13, the `connect` description and session-open's
  words: a telnet connection is one TCP connection with no authentication of its own (RFC 854); its one login is
  typed by the operator in its one session.
- **Where a session's close leaves its connection up**, every v0.5.0 sentence gets its telnet sentence appended:
  `close_channel`, `channel_closed`, session-end-by-tsb steps 8 and 23 and its Independence bullet,
  connection-linger's opening rule, OpenAPI `closeSession`, and V13. A telnet connection carries one session and
  closes with it.
- `continuity-state` `connection_record.credential_ref`: on a telnet connection, which authenticates with nothing,
  this names the credential that authorized the create, not one used for authentication.

**What each unit must change.**

- **U5:** the inventory schema and loader, the layout, the YAML mapping, `config.schema.json` mirrored by the loader,
  the index definition; a Type-1 discovery driver is a later item.
- **U2:** the index, the file-change watch, the rebuild, the two endpoints, the `config.yaml` reload. Telnet: the
  `connect`, the version gate, `resizable` read and kept; a telnet session's credential gate sends nothing; 0.5
  beside 0.4, 0.3 and 0.2.
- **U3:** the telnet driver (bytes only), the output from the TCP connect up, one session per connection and
  `single_session` once the end has reached the core, the address check, 0.5.
- **U7:** the inventory browser, rendering target strings as text.
- **U10:** the E3 knobs, the inventory link and its volume, the networks; the discovery service is a later item.
- **U11:** the frozen 0.4 worker and its N-1 rig; the E3 rigs; the agentless check.

**Known gaps.**

- Serial is deferred (the operator, 2026-10-18). Serial targets are listed and answered 502
  `target_connect_failed`; their rows wait for the release that connects serial.
- There are no login scripts at this release. The operator types the whole login, recorded verbatim. A target file's
  login script (`options.scripts`, `login_scripts`) loads and is ignored: no script runs.
- TSB types no secret into a stream. A telnet login and an enable secret are typed by the operator and recorded
  verbatim, as everything typed is (open_decisions §5).
- A reuse of a telnet connection that is still up, until its `connection_lost` is reported, is answered 502
  `target_connect_failed`.
- A rebuild on demand is deferred. The index is rebuilt at start and on a file change.

**Rulings applied** (by content): serial deferred; SSH with an enable step against the gear is in the gate; a telnet
connection carries one session, and its worker closes it; TSB types no vault secret into a stream, and nothing is
masked; no login scripts at this release; contract changes are additive only.

**Nothing valid before becomes invalid.** Two fixture texts: `C2/size-not-resizable`'s description (serial is
deferred, so a telnet peer that refused NAWS is the first case to grade it) and `C3/control-message-shape`'s note on
the telnet instance. `C3/open-sftp`'s and `C3/reattach-non-pty`'s descriptions name the file surface as a later
release's, not this tag's named successor. `connection_lost`'s `linger` branch, whose `const` became an `enum`
holding the same value, keeps every v0.5.0 verdict; the telnet instance stays invalid, and `cause: "fin"` stays
invalid.

## v0.5.0 — 2026-10-17

Added: a protocol worker names, on edge CO-PW, an end it decided itself, so a core that is present records it as
TSB's end; after a restart the core's first `ping` goes before its first `hello`; C3 moves to `proto_version` "0.4"
(tsb-core #290).

**C3 — core ↔ worker.**

- `channel_closed` gains `by: worker`. A timer the worker runs while the core is gone (`orphan_timeout`, `idle`,
  `unattended`) fired, and the worker closed the channel itself. The event carries the end it decided —
  `end_state` (`killed`), `end_reason` (`orphan`, `idle` or `unattended`), `ended_at`, and the ring's final
  `out_bytes`, `last_seq` and `last_out_ts` — as its final spool write states them, and `reason` names the timer.
  Until now such an end went out `by: core`, which means only the answer to `close_channel`, and a core that had
  sent none recorded it `terminated` / `target_closed`: a TSB-decided end reached the audit and the recording's end
  marker as an end from outside TSB (session model §12; Storage §6).
  The worker chooses `by` when it sends the event: a `close_channel` that reached it first makes it `by: core`.
  With no core on the control socket it sends nothing, and the spool carries the end as before.
- A core that receives it records `killed` with that reason at once, from the event: the end marker (code 3) and
  `session.disconnect` (no `user`) are stamped `ended_at`, attached clients are closed 4010, the spool entry is
  deleted after the audit, and `release` follows. The core computes the gap from the event's `out_bytes`, not from
  the spool entry, so a failed spool write leaves no undeclared tail (immortality §4.3; session-end-by-tsb steps
  23a–23b). A session the core already ended keeps its end; a malformed `by: worker`, missing any of the six fields or
  with one out of its shape, is an end the core cannot place (`terminated` / `target_closed`, logged as a worker
  defect); one for a session the core does not hold is ignored, and its spool entry is found at take-back.
- **A declared loss.** The core does not replay the session for this end. The worker gives a present core 2 s after
  the end to take the session's last output. When the core has not received all of it by the time the end is
  reported — a slow or stuck core — the output the worker still holds is not recorded, and the core declares it:
  before the end marker it writes one gap marker for the event's `out_bytes` minus the output bytes its
  recording holds, over the span to `last_out_ts`, and `release` then lets the worker drop it. Those bytes are lost,
  and the recording says so (immortality §4.3: a declared gap, never a silent splice). Output the core did receive,
  live or replayed, is in the recording and is never declared twice. The worker reports `by: worker` only for a
  session that control peer opened or sent `hello` for: a session the returning core holds back for its realign
  gets no event, and its own take-back handshake replays its tail as before. A lossless finish, replaying the output
  the worker holds, is not in this release; tsb-core #290 stays open for it.
- After a restart the core sends its first `ping` on each control socket, after `timers`, before its first `hello`
  there, toward every worker whose version it speaks (core-restart step 22, formerly step 23; the handshake is now
  step 23), so the worker has left orphan-pending and handed `idle` and `unattended` back to the core before any
  handshake opens, and starts no end of its own timers after that `ping`.
- From 0.4 the worker's pause from `hello` until `live` holds while the core that sent the `hello` is present
  (ruling 2026-10-14): that core is gone when its control peer drops or is replaced, it sends `close_channel`, the
  worker enters orphan-pending after that `hello`, or the connection ends. An end that lands meanwhile waits for
  `live`, and its tail goes out live, or for `release`, which settles it ended with nothing more read and no
  `channel_closed` (reattach-handshake step 4; `hello_ack`; `release`).
- A `connection_lost` that lists a session with a spool entry: the core records the entry's end, stamped with its
  `ended_at`, and declares from its numbers what the recording lacks (session-end-from-outside step 5). The worker
  rewrites the entry with the ring's final numbers before it sends `connection_lost`. A worker's own timer's end,
  cut by a transport loss mid-drain, is recorded `killed` with its reason, not `terminated` / `connection_lost`.
- The spool entry's `ended_at` is when the worker finished the end, and from 0.4 never earlier than `last_out_ts`.
- `proto_version` is "0.4" on `hello`, `hello_ack`, both manifests and `worker_created`. A 0.4 core speaks 0.4, 0.3
  and 0.2 and sends nothing new, so a 0.3 peer gets exactly v0.3's messages; `keepalive_probe_interval` goes to every
  peer at 0.3 or later. A rolled-back 0.3 core reads `by: worker` as an unknown `by`, so as `target`, and ignores the
  six fields (V2, V5). Cores are upgraded before workers, and rolled back after them.
- New fixtures, required green at e2: `channel-closed-by-worker`, `control-message-shape-v0.4`,
  `n1-v0.3-worker-positive` (a frozen 0.3 worker in tsb-core `harness/fixtures/worker-v0.3/`).

**Runner.** tsbc's C3 order rules know `by: worker`: no `close_channel` before it, its end carried with it and only
with it, a crossing `close_channel` accepted, an unseen session in a reattached capture taken as rediscovered, and a
`release` after it accepted while a crossed `hello`'s replay is incomplete. `outside-ends` no longer expects a
`by: worker` end audited `terminated`, and accepts a timer's `killed` end, or `terminated` / `target_closed`, for a
session a `connection_lost` lists. `audit-orphan-end` accepts an orphan end stamped after the latest core's start
when that core was told of it on `channel_closed`.

**What each unit must change.** U2: record `by: worker` at once, from the event, with the gap the spool entry shows;
read the spool for each session a `connection_lost` lists; send the first `ping` before the first `hello` after a
restart; arm no timer at `live` over a session whose spool entry it read at take-back; speak 0.4, 0.3 and 0.2; send
the probe key to every peer at 0.3 or later. U3: report its own timer ends `by: worker` with their end; take a core
as gone only on orphan-pending entered after its `hello`; let `release` settle an end that is waiting; rewrite an
ending session's entry before `connection_lost`; start no timer's end after the core's `ping`; stamp `ended_at` no
earlier than `last_out_ts`; advertise and serve 0.4. U11: the frozen 0.3 worker and its N-1 rig.

Nothing valid before becomes invalid: no earlier instance carries `by: worker`, and the six fields are optional in
the schema.

## v0.4.3 — 2026-10-16

Fixed: three C2 fixtures written against the stub server, which echoes input raw and has fixed size
bounds, graded live against a real shell and a configured core at tsb-core's e2 move.

- **attach-prefix-modes.** Steps 7 and 9 send the mode sequences as `printf` command lines, so the target
  shell emits each sequence as its output: a real shell never echoes typed escape sequences (readline
  consumes them), and the echoed command line holds the backslash text, never ESC, so the graded bytes
  prove the target printed them. The expectations (steps 8 and 10, the final prefix's `binary_contains`)
  are unchanged. It stays serve-exempt.
- **owner-takeover-then-ctrl-c.** Step 22 expects `^C`: a real terminal echoes Ctrl-C as those two
  characters, not the raw byte the stub echoes. The fixture is added to `fixtures/C2/.serve-exempt`,
  because the graded echo is a real terminal's.
- **size-request-clamped.** The fixture no longer hard-codes the stub's bounds: it clamps 1×1 and 2×2 to
  one floor and 65535×65535 and 65534×65534 to one ceiling, saving the floor from the first clamped answer
  and the ceiling from the first ceiling-clamped answer, and checking the second request of each pair and
  the session record against the saved values. The exact bounds are the graded core's providers' to grade;
  README "Inputs" states the knob requirement these requests need (floor above 2, ceiling below 65534).

## v0.4.2 — 2026-10-14

Fixed: the graders owed at e2 that tsb-core now ships, a parametrized pytest case in pytest's default JUnit output,
and a `link_state` a worker may send while the core hangs up.

- **The graded-by index** (`fixtures/graded-by.json`). Every entry owed by an E2 plan now names the tests that grade
  it, each test read and confirmed to exist on tsb-core `main`:
  - U4's 20 entries on the C4 fixtures. The seven goldens are graded by `vt/internal/server/golden_test.go`
    `TestC4Golden`, by the subtest named after each fixture. Eleven more are graded by their replay in
    `vt/internal/server/fixtures_test.go` (`TestC4<Name>`), with any server test that checks the same property beside
    it. `C4/vt-frame-layout` is graded by `vt/internal/c4/frame_layout_test.go` and `C4/vt-screen-shape` by the oracle's
    and the contract test's shape checks.
  - U2's 26 entries: the core's tests for per-client output, the prefix after a restart and after a VT worker's death,
    size coalescing and pinning, both stale-flow causes, auto-release, the attach wiggle, the N-1 v0.2 worker, the
    core's side of C4, and the C6 records.
  - Four entries are due only at e5, so they stay owed, now to the E5 plans. These are U2's on `C1/output-page-gap` and
    `C1/output-page-ground-start` (an E2 core answers a page request 501), and U2's and U4's on `C4/vt-applied-ground`
    (nothing emits `applied` before pages land).
- **tsbc** (#76). A parametrized pytest case is placed by its classname when its JUnit case carries no `file`, as a
  non-parametrized case already was. pytest's default JUnit family (xunit2) writes no `file`, so such a case was
  never matched, and its fixture stayed UNGRADED although the test ran and passed. For a `Class::method` citation, a
  JUnit case that names the method alone counts only when its class is the cited one.
- **The hygiene scan.** A test name inside a graded-by entry's `test_id` is not read as a base64 secret: three Go test
  names there ran past the entropy limit. The rest of the citation, and the same string in any other field or file,
  is still scanned.
- **A `link_state` between the core's `disconnect` and the worker's `connection_lost`** (#72). link-liveness step 10
  makes `link_state` the first event to a new control peer, so a worker a restarted core re-dials and then hangs up
  may send one before it reads the `disconnect`. connection-linger step 14 now says so, and tsbc's C3 grader no longer
  reports it as "link_state before connected". The tsb-core loop failed three C3 shape fixtures on such a capture on
  2026-10-13 and again on 2026-10-14.

## v0.4.1 — 2026-10-14

Fixed: graders owed at e2 that tsb-core now ships, and two clauses that could be read against
others.

- **The graded-by index** (`fixtures/graded-by.json`). U11's owed entries become the rigs that
  grade them, each as a rig result `{U11, harness/rigs/<name>.sh, rig-<name>}`:
  - `C2/stale-flow` by `stale-ctrl-l`;
  - `C3/attach-redraw-wiggle` by `attach-wiggle`, `stale-ctrl-l` and `n1-positive`;
  - `C3/n1-v0.2-worker-positive` by `n1-positive`;
  - `C2/prefix-after-restart` by `prefix-restart`, which stamps only when a graceful stop, a killed core and a killed
    VT worker all restore the screen exactly.

  U5's owed entry on `C6/grid-snapshot-record` becomes `schema/session/snapshot_test.go`
  `TestGridSnapshotRecordFixture`. U2's entries on these fixtures stay owed.

  One wrong citation is corrected. `C1/create-refused-v0.1-workers` cited its two U2 tests in
  `core/internal/httpapi/sessions_test.go`, but they live in `core/internal/worker/version_test.go`. tsb-core's first
  run graded from its own results showed it: the tests ran and passed in that package, and the fixture stayed
  UNGRADED.
- **grid-snapshot.** Rules 1–3 bind a recorded PTY shell. A snapshot is stored at the recording's commit point (rule
  7), and an unrecorded session has no recording to align one with, so an unrecorded shell takes no snapshot and its grid is
  lost at a VT worker's death (grid-restore steps 11, 14). Rule 5's refusal at once is graded by U4's two tests, named
  in the rule.
- **core-restart step 24** re-arms `idle` and `unattended` for every session the core holds whose end its spool entry
  does not already hold. A session the worker ended while the core was away has its end in the spool, and no timer is
  armed over it: its end is the worker's (spooled-end-finalize step 3). A timer fired there would write a second, wrong
  end. Graded by U2's test, named in the step.

Text and index entries only: no fixture file, no schema, no wire shape. Nothing valid before becomes invalid, and a
consumer pinned to an earlier tag is unaffected. A provider that moves its pin to this tag must run the four rigs on
its e2 loop for those fixtures to grade PROVIDER at e2.

## v0.4.0 — 2026-10-12

`graded_by` becomes a checked reference. A reference fixture is no longer PROVIDER because the
caller said the units ran: `fixtures/graded-by.json` (new, `schemas/graded-by.schema.json`) names
each one's cited tests as `{unit, file, test_id}`, and `tsbc verify --provider-results FILE`
(repeatable) reads the run's own test output — `go test -json`, pytest's `--junitxml`, or a small
JSON list for a rig — and grades the fixture PROVIDER only when every cited test passed. A cited
test that ran and failed is FAIL; one absent from the results is UNGRADED; an entry `{unit, owed}`
is UNGRADED and never green. `--provider-tests` stays accepted and documented, and alone grades no
reference fixture. Nothing valid before is refused, no fixture byte changes, and `test_additivity`
is unchanged: the index is additive and sits beside the fixtures. Self-check refuses an index entry
for a fixture that does not exist or is not graded `reference`, an entry whose `unit` is not among
the fixture's own `graded_by` units, a `file` that is absolute or holds `..`, an owed entry on a
fixture required green at e0 or e1, and a required reference fixture with no index entry.

Nothing else changes: no fixture file, no wire shape, no interface, and a consumer pinned to an
earlier tag is unaffected. A provider that moves its pin to this tag must hand `tsbc verify` its test
results with `--provider-results` for its reference fixtures to grade PROVIDER: `--provider-tests`
alone now leaves them UNGRADED. The index is a new file beside the fixtures and a new option on the
runner. `fixtures/graded-by.json` is not a
fixture and is never loaded as one (its schema is a runner schema, like `fixture.schema.json`
itself), and self-check proves it as coherent with the fixtures it speaks for.

## v0.3.6 — 2026-10-12

Fixed: a grid that cannot take the next output frame keeps the session attachable. When the VT worker refuses a frame (`seq_jump`) or a restore (`snapshot_incompatible`), or a `shell` session has no grid at all, the core closes that grid when one exists, opens an empty one, feeds it from the first frame the old grid did not apply (after `seq_jump`, the refused frame itself) and raises the stale flow with cause `grid_lost` (5.52). The empty grid takes the first output frame it is fed as its start, so a feed that starts past seq 1 is applied, not refused; it starts blank and its dump shows what it has applied since, so an attach proceeds instead of being held and closed 4011. Text only: no field or shape changes, and nothing previously valid becomes invalid.

## v0.3.5 — 2026-10-12

No contract change. Queue A2 is closed by the operator's ruling (O1, 2026-10-11): the contract's reading stands,
the grid lives in the VT worker and nothing grid-shaped crosses C4 per frame. The corpus is re-exported to say the
same (open_decisions §1, immortality §5 and §5.1, Storage §8.6), and the open items that quoted the old wording
(grid-snapshot 1, grid-restore 1, byte-relay 1) are closed.


## v0.3.4 — 2026-10-12

Fixed: a core restart no longer cuts the recording back to the commit point, so the contract obeys
open_decisions §5 (LOCKED: "No byte is … dropped from the recording stream") and Contract §13
("The broker records what actually reached it"). From v0.3.1 the commit point moved only with a grid
snapshot, and a restart cut the recording back to it (continuity-state top; core-restart step 16).
That cut dropped the input frames, broker markers and end marker written since the last snapshot,
which no worker ring holds. Queue A1. The corpus is unchanged; four clauses hold under readings,
listed below, which the operator's go on this change rules.

**C6 — durable state.**

- The commit point stays one persisted value, advanced only in a grid snapshot's write, where
  `snapshot_seq` equals `recording_last_seq_out`. `out_bytes_committed` and `recording_last_seq_in`
  are written with it, for the same point; no other write moves any of the four, and every other
  write carries them as stored. `recording_committed_offset` is the end of a frame after output frame
  `recording_last_seq_out` and before the next output frame or gap marker, and both recording files
  are durable through it before the write. A snapshot is never stored below the commit point.
- **Realign before `hello`.** Before every `hello` (a new session, a core restart, a lone
  data-socket re-dial) the core moves the commit point to the recording's end: it stores a snapshot
  of a grid that has applied every output frame the recording holds (at a restart: the snapshot
  restored and the recording's own frames past the commit point re-fed). So `hello` carries
  `snapshot_seq`, the worker replays only what the recording lacks, and a gap is declared only for
  output that neither the recording nor the ring holds. The snapshot and the commit point name the
  walk's last output seq. A grid the VT worker kept that already applied the gap frame of a marker
  whose commit never landed is not snapshotted. The core restores the stored snapshot over it, and the
  handshake's re-sent gap frame numbers that marker for the gap hold to commit. This replaces v0.3.1's
  "On restart the core cuts the recording back to the committed offset and replays the rest".
- **The wait is bounded; the recording ranks above the grid.** A new `config.yaml` knob,
  `snapshot.wait_sec` (1 to 600 s, shipping 30; conventions V7; U5 adds it), bounds the wait for a
  snapshot that a commit needs now (the realign, and a gap's hold), covering the answer and every chunk
  of its blob. When it runs out at either stage, or the VT worker cannot give one (a refusal,
  `unknown_session`, a grid stuck after `seq_jump`), the core gives the grid up and keeps the recording
  going:
  - it deletes the snapshot;
  - it commits record-only;
  - it sends `hello`, or appends the replay;
  - it raises the stale flow with cause `grid_lost`.

  The corpus ranks the byte stream above the grid (open_decisions §4: "the byte stream is
  byte-perfect", "grid continuity is best-effort"; immortality §4). A cadence, size or screen-event
  snapshot that fails changes nothing; it is asked again at the next trigger.
- **No snapshot: record-only.** A session with no `grid_snapshot_record` moves its commit point in a
  record-only write. That covers:
  - a session type with no grid;
  - a shell before its first snapshot;
  - a shell whose grid was given up or found `snapshot_incompatible`;
  - a session whose end is known.
- **A graceful stop** snapshots every grid the recording has outrun, so an ordinary restart needs no
  realign; only a crash pays for one.
- **At a restart, a session whose realign is still waiting:**
  - it reads `detached`, and its input is refused `replaying`;
  - the core pings its worker and runs its timers;
  - its dial and E10 decision come when the wait ends.
- **Gap hold.** A gap marker a handshake writes is a commit point. The core appends nothing after
  it, an end marker excepted, until the gap's forced snapshot at the gap frame's seq, or a
  record-only write, commits it.
- **A session that is not recorded:**
  - its end is the last output frame the core received;
  - a VT worker's death loses its grid, and the stale flow follows with cause `grid_lost`;
  - its input seq continues from a floor: a reservation at E2, while an E1 core's floor conforms.

**C8 — recording.**

- The reopen rule (`x-sidecar` `reopen`) cuts only a torn last frame; no complete frame is ever cut.
  It gives the walk that finds the recording's end.
- From E2, gap markers past the commit point (cores that died before their commits) add their byte
  counts and no seq. The next handshake writes a marker only for bytes lost since: zero only when such
  a marker is there; any other count below one is a failed recording write. After an end marker the
  recording takes nothing more.
- An output frame and its `.dir` code are recorded before the frame is fed to the VT worker, so no grid
  stands past the recording (byte-relay step 9).

**C3 — core ↔ worker.** No message changes. `hello`'s `last_committed_seq` is `recording_last_seq_out`,
realigned to the recording's end; it is `snapshot_seq` for a session with a snapshot. A spooled end
compares bytes, and writes neither marker again when its recording already holds the end marker.

**C4 — VT.** No message changes.

- The VT worker answers `snapshot` at once with the grid as applied, an open sequence carried in the
  snapshot, and never waits for a later frame. One it cannot take it refuses at once (`bad_request`,
  whose text now says so).
- After a VT worker's death the core restores the grid and feeds it the recording's own frames past
  the commit point, then the frames recorded meanwhile. No handshake runs and input is not gated. A
  frame re-fed from the recording carries its recorded stamp. Two cases still lose the grid
  (`grid_lost`): a session that is not recorded, and a size change that missed its snapshot.

**Corpus readings for the operator's go.**

1. immortality §4.3: "On reattach, if first_buffered_seq > last_committed_seq + 1, the core writes an
   explicit gap marker." The core can hold a reattach for up to `snapshot.wait_sec` while it waits for
   a snapshot, and output the ring drops in that time is declared under this clause. That bounded wait
   is read as the ring's job, like core downtime (§4).
2. immortality §4.1, "Core tracks last_committed_seq per session (persisted — §5.2)", and decision 5.39,
   "Counters = one, aligned (last_committed_seq is snapshot_seq)". A shell with no snapshot, before its
   first one or after its grid was given up, commits record-only. "Aligned" is read as having nothing
   to align when no snapshot exists.
3. immortality §5.1: "load snapshot → request worker delta seq > snapshot_seq → replay". After the
   realign, `snapshot_seq` is read as the snapshot current when the request goes out, not the one
   loaded.
4. immortality §4.3, narrow: when a core died after writing a gap marker and before committing it,
   the next handshake writes no second marker if the ring dropped nothing more. The marker already in
   the recording is read as the explicit gap marker the clause asks for, and zero is allowed only then.

**What each unit must change.**

- U2 (core), at E2:
  - the realign, with its bound;
  - the graceful-stop snapshot;
  - held-back sessions at a restart;
  - record before feed;
  - the gap hold, with its bound;
  - giving a grid up (delete the snapshot, commit record-only, `grid_lost`);
  - after a VT death, re-feed from the recording, with no re-dial;
  - cut only a torn frame, and count markers past the commit point by their bytes;
  - spooled ends: compare bytes, and skip markers already written;
  - E1's restart write becomes the realign write.
- U4 (VT worker): answer every snapshot at once or refuse it at once. The VT plan's DV11 wait for a
  later frame must go, and U2's phases 13–14 depend on that change.
- U5: the knob `snapshot.wait_sec`.
- U11: none required. A rig for "VT down across `restart-core`" is useful.

**Protocols.**
- Steps: core-restart steps 3, 7, 15–17, 21–24; reattach-handshake steps 1, 5, 10 and 12, and its
  callers; grid-snapshot steps 2, 3, 5, 7 and 9, rules 4, 5, 7 and 9–13; grid-restore steps 10–14,
  refusals and security; stale-screen rule 1; spooled-end-finalize steps 4 and 5; data-socket-redial
  step 7; byte-relay step 9; operator-alerts.
- Text: conventions V7; orphan-session; the README CO-DK row; the CO-BB, CO-DK and CO-VT edges.
- Closed: grid-snapshot open items 2, 4, 5 and 7; core-restart 1–3; grid-restore 2, 4 and 5;
  session-end-by-tsb 8; spooled-end-finalize 3; stale-screen 10; and the `last_committed_seq` items of
  reattach-handshake and data-socket-redial.

**Runner.** `recordings-gap` matches a marker to a gap frame that a later run's handshake superseded,
and does not require one: the marker a dead core wrote before its commit stays declared.

**Fixtures.** Descriptions only, no instance, verdict or byte: `C6/snapshot-seq-aligned`,
`C6/out-bytes-committed-after-reopen` (and its grader's text), `C8/gap-after-overflow`,
`C8/gap-twice-across-restart`, `C4/vt-restore-after-vt-death`, `C4/vt-snapshot-unavailable`,
`C2/prefix-after-restart`. The v0.2.1 record allows the three e1 descriptions this fix made false. No
schema shape changes: every instance valid at v0.3.3 stays valid.

## v0.3.3 — 2026-10-11

Tests only; no contract change. The suite no longer reads wall-clock margins to assert a deadline:
the client resize tests drive an injected clock, and the alert-stream test asserts that the run
stopped before the stream ended instead of a time bound. The `serve` IPv6 test now calls the
server's own bind and asserts the socket family. The passing `--artifacts` cases are built from
hand-written ttyrec and IPC bytes rather than the runner's own codec, so a codec bug cannot make
its own expected recording agree with itself. Three tautological assertions are removed.

## v0.3.2 — 2026-10-10

Fixed: the package has no hard-coded version. v0.3.1 shipped `tsbc.__version__` and pyproject at
0.3.0 while its label read v0.3.1; `tsbc.__version__` is gone and pyproject reads its version from
`CONTRACT_VERSION` at build time. The release tool now also runs the self-check tests on the stamped
tree.

## v0.3.1 — 2026-10-09

The first release authored in tsb-program (cut by CI as `v0.3.1`, continuing the contract's versions). It carries the contract-v0.3 work — E2, grid and multi-client — and, new in `contract/protocols/`, the TSB protocol documents: 28 protocols, the conventions V1–V16 and 13 edge catalogues, now normative. Review history: moophat/tsb-core#120.

Additive on contract-v0.2.1 (immortality §6.4: additive only, unknown fields ignored; the core
speaks N and N-1): every instance valid at v0.2.1 is valid here, and a field added to a v0.2
message or record is optional in its schema — what a v0.3 sender must send is graded by fixtures
and artefact checks, never by making it required. 203 fixtures: the 139 of v0.2.1, and 64 new, all
marked e2 or later. The `--profile e0` set is exactly v0.1's 62; `--profile e1` still asserts
v0.2.1's set (e0's 62 plus its e1 fixtures) and is unchanged here; `--profile e2` adds this tag's
new set; `--profile e5` adds the pagination and `applied` fixtures. The default profile is now e2,
the epoch this tag pins. This tag adds no loss rule beyond v0.2's, and a v0.2 core or worker needs
no change to remain a valid peer.

**C2 — session stream.**

- Versioning: every frame keeps `v: 1`, and a later tag adds a `t` or an optional field, never a
  breaking change inside a frame. A known `t` with an unknown `v` is ignored like an unknown `t`
  (the attach first-frame rule wins: 4000 on a bad first frame). `frame-versioning` grades it.
- `attach` gains optional `cols`, `rows` — the client's viewport at attach. It is a report, never
  a resize: a non-holder's attach, and an attach while the token is in escrow, leave the PTY at its
  size. Absent (a v0.2 client), the client has no declared viewport until its first `resize`.
- The attach prefix: after `attached`, a `prefix` frame `{t, v, through_seq, cols, rows, scrollback?}`
  then **exactly one binary message, ≥ 1 byte — the dump** — then live output, which is exactly the
  output frames with `seq` > `through_seq`, in order, none repeated, none skipped. PTY `shell`
  sessions only; other session types get no prefix and output follows `attached` directly. A v0.2
  client ignores the text frame and renders the dump as bytes. If the grid is unavailable the core
  holds that client's output until the prefix exists, bounded by a knob, then closes 4011 — a client
  never gets live bytes without a prefix. What the dump carries is C4 decision 6.
- `prefix.scrollback {first_seq, bytes}`: the oldest output seq the raw ring holds and the bytes it
  holds up to `through_seq`. Optional in the schema, required e5 with pagination (no core ring
  exists at E2).
- Attached clients: the session record (and `attached`) gains optional `clients`
  `[{client_id, user, may_control}]`, on every record not yet ended and never restored from disk;
  server frame `clients` goes to every attached client on each attach, detach and removal.
- Hand-off: client frame `handoff {t, v, to}` (`to` a `client_id`, or `null` = revoke to escrow),
  from the holder to a client that `may_control` or from any client of the owner principal. `token`
  reasons gain `handoff` and `owner`. After a revoke the token sits in escrow until the owner
  reassigns — an attach does **not** take it. Refusals are server frame `request_refused {t, v,
  request, reason}` with `reason` in `not_allowed`, `no_such_client`, `view_only`, `not_pending`.
  Ownership transfer is **not** at this tag (D8; known gap).
- Taking the keyboard back (5.53): the owner takes the token either by typing — its input while not
  the holder moves the token, and that same input is then forwarded — or by `handoff` to its own
  `client_id`. One client's frames apply in the order sent, and a take-over applies before that
  client's next frame, so a `handoff` to self and `0x03` is one trip. The previous holder's input is
  refused from the instant of the move (`input_refused`, new reason `not_typist`), never two
  typists. Auto-release (a `config.yaml` knob, 0 = never and the default) returns the token to the
  previous holder. `token` reasons gain `takeover` and `release`. Getting the token never sends a
  resize and the core never resizes on a token move.
- Removed client: `DELETE /sessions/{id}/clients/{client_id}` (C1 decision 2) closes the socket
  with **4014 `removed`** (4013 is retired vocabulary); a v0.2 client reads an unknown 4xxx as 4010
  and stops. For the token it is a detach (`token` reason `detach`).
- Target size: `resize` now means "set the target's size" (a target size request from the holder),
  never the viewport. Every request is answered in a new server frame `size {t, v, session_id,
  cols, rows, reason}` with `reason` in `applied`, `clamped_floor`, `clamped_ceiling`, `pinned`,
  `not_resizable`, `not_typist`; `cols`/`rows` are the size in effect, so a refusal still carries
  the standing size. The applied size is broadcast to every attached client. A non-holder's request
  is refused by the `size` answer itself with reason `not_typist` — `request_refused` is never used
  for it. The core clamps to the target's floor and ceiling (`config.yaml` knobs, floor = ceiling
  pins the size), coalesces bursts last-wins, then answers each request in order with the size that
  applied. The session record gains optional `resizable` and `pinned`. A clamped answer is final
  until the surface's own window changes.
- Redraw on attach (5.46): an attach issues a C3 `resize` pair the core originates (the wiggle), a
  broker action never a client request and so not answered with `size`. Order at attach: `attached`
  → `prefix` + dump → `clients` → one wiggle → live. It never fires while a stale choice is
  pending. It is not announced to clients.
- Stale grid after a gap (5.46, 5.52): server frame `stale {t, v, session_id, state, cause, gap_seq?,
  at, choice?, by?}` to every attached client — `cause` in `gap` (a declared loss) and `grid_lost`
  (a grid that could not be rebuilt with no byte lost), `gap_seq` present only for `gap`. `stale_prompt`
  to the holder's client carries a fixed text and four choices (`wiggle`, `ctrl_l`, `continue`,
  `fresh_session`) with fixed texts, held to the session's protocol; `stale_answer` answers it, and
  any answer ends the pending state (D12). Until answered every input is refused (`input_refused`
  new reason `stale`); a holder's size request is still answered. Only PTY `shell` sessions.
- `x-attach-order` gains these steps, and the schema's top description carries the ordering rule of
  decision 6. Receiver rules added: a `size` reason not known → read `cols`/`rows` as the size in
  effect; a clamped `size` is final; a non-holder's `input_refused` reason not known → input was
  refused; no `prefix` frame → output follows `attached`; render the dump at `prefix.cols × rows`.

**C1 — northbound REST.**

- Session record gains optional `clients`, `resizable` and `pinned`. No REST resize: a size request
  is a C2 frame from the holder, as at v0.2.
- `DELETE /sessions/{id}/clients/{client_id}`: owner or `session.terminate` holder, 204; 403
  `forbidden`; 404 `session_not_found` / `client_not_found`; audits `session.kick`. On C1 because
  it is the owner token's lifecycle authority.
- Pages — `GET /sessions/{id}/output?before_seq=N&max_bytes=M` (published e2, required e5): 200
  `application/octet-stream` of whole output frames, with headers `TSB-Page-First-Seq`,
  `TSB-Page-Last-Seq`, `TSB-Page-More`, `TSB-Page-Gap`, the no-ground signal
  `TSB-Page-No-Ground` + `TSB-Page-Next-Before-Seq`, and a 501 `not_implemented` answer from an E2
  core that does not serve pages yet. A page never spans a gap, starts where the VT parser was at
  ground, and never touches the session or another client. Allowed exactly when the principal may
  attach.
- Error codes `client_not_found` (404) and `not_implemented` (501).

**C4 — core ↔ VT worker (new).**

- Where and trust: `/tmp/tsb/vt/{vt_id}.sock`, one per VT worker; the core dials every socket there
  by `SO_PEERCRED` and the TSB service uid, no token. N VT workers allowed, one configured.
- Framing: one stream, `[u32 BE length][u8 kind][body]`, big-endian as the embedded C3 envelope,
  a stated maximum message size and **chunked blobs** for kind-3 bodies above it, reassembled by
  `request_id` (`common` `id`'s shape). Kind 1 a JSON control object; kind 2 an output frame (the
  C3 data frame verbatim, gap included); kind 3 a blob.
- Messages: core → VT `hello`, `open` (PTY `shell` only), `resize`, `prefix`, `snapshot`, `restore`,
  `close`, `ping`; VT → core `hello_ack {proto_version, engine, grids}`, `prefix_ready`,
  `snapshot_ready`, `restored`, `screen_change`, `applied`, `pong`, `error` (`unknown_session`,
  `seq_jump`, `snapshot_incompatible`, `bad_request`, `unknown_cmd`, `unsupported_proto`).
- Order and idempotence: per session the VT applies frames and resizes in arrival order, ignores a
  `seq` ≤ `through_seq`, refuses a jump without a gap frame (`seq_jump`, grid unchanged), and a gap
  frame moves `through_seq` only. The VT applies a frame before the core commits it, so a snapshot's
  `through_seq` is a commit point. A VT worker that dies while the core lives is restored from its
  last snapshot and the C3 handshake re-run per PTY session; frames the recording holds are fed to
  the VT only, never recorded twice. A VT worker that misses heartbeats is dead: the core raises the
  `vt_unavailable` alert.
- Grids outlive the core: the VT worker keeps every grid when its core's socket closes and lists
  them in `hello_ack.grids`.
- The dump: answered at the first parser-ground point; if the parser is not at ground and none
  arrives within a knob's wait, the VT answers at `through_seq` and appends the raw bytes since the
  last ground point (no 4011 loop). It carries cells and SGR (the pen included), cursor, the
  alternate screen (primary first when active), origin, autowrap, insert and every carried input
  mode, emitting the **full state of each mode** (`CSI ? n h`/`l`) and `CSI ?1049l` first when the
  primary screen is active, then cursor home + ED 2 before the cells. Never history, RIS, 2026
  left set, tab stops, scroll region, charsets or saved cursor.
- Snapshot: the engine's own binary snapshot, opaque to the core, with `through_seq` and `engine`;
  a restore from another engine version answers `snapshot_incompatible`, after which the core opens
  an empty grid, feeds what the replay holds and raises the stale flow with `cause` `grid_lost`.
- `applied {session_id, through_seq, mid_sequence[]}` marks ground (published e2, required e5).
- `proto_version` `"0.3"` on `hello`/`hello_ack`, additive-only from the first version; `ping`/`pong`
  answered from the VT worker's own loop. No grid mirror crosses C4 and no per-frame deltas.

**C6 — durable state and audit.**

- `grid_snapshots` bucket: session id → `grid_snapshot_record {session_id, snapshot, snapshot_seq,
  cols, rows, engine {name, version}, written_at}`, one per session, the latest; no
  `recording_offset` (F1: one copy — the session record's `recording_committed_offset`).
- One persisted value (D24 withdrawn, 5.39): the session record's `recording_last_seq_out` and
  `recording_committed_offset` advance **only** when a grid snapshot is persisted, in the same
  bbolt write, so at every write `snapshot_seq` = `recording_last_seq_out`. On restart the core cuts
  the recording back to the committed offset and replays the rest; output recorded but not yet
  committed is cut and re-recorded, or declared a gap if the ring no longer holds it. Snapshots are
  forced on every PTY size change and alt-screen enter/exit/clear; if a snapshot cannot be taken (VT
  down) the commit point does not advance and the `vt_unavailable` alert is raised.
- The durable session record (`continuity-state.schema.json`, C6) gains optional `stale_seq` and
  `stale_cause` (`gap` | `grid_lost`), both or
  neither, so a pending choice survives a restart. On a gap frame the commit, the snapshot record
  and `stale_seq` are **one** bbolt write; a `grid_lost` write stands alone.
- Audit: `session.control {from, to, reason}` on every token move (`reason` in `attach`, `detach`,
  `handoff`, `owner`, `takeover`, `release`), `session.kick {client_id, client_user}`,
  `session.stale_choice {choice, cause, gap_seq?, client_id}` (`gap_seq` exactly when `cause` is
  `gap`). A size request is not audited — presentation under Contract §12's exemption.
- Property-name guard: `snapshot*` only inside `grid_snapshot_record`; `claim*` banned everywhere.

**C8 — recording.**

- Broker marker, sidecar **code 5**: one fixed pure-ASCII line `x-broker-line` — `CR LF [TSB:
  stale screen after lost output - <choice> chosen] CR LF`, a plain ASCII hyphen-minus — stamped on
  the core's input clock, a classic player prints it, and a v0.2 reader refuses it as v0.1 refused
  code 2. `x-compare` leaves code 5 out of payload and compares it line for line. The attach wiggle
  and applied size changes are not recorded (presentation; ttyrec has no resize).

**C3 — core ↔ worker, core ↔ supervisor.**

- No new message; `proto_version` becomes `"0.3"` (D28) because `timer_values` gained a key. A
  v0.3 core speaks 0.3 and 0.2 (N/N-1), sending exactly v0.2's messages toward a 0.2 peer.
- `resize`'s text gains: each resize reaches the target as its own window-change, in order; a worker
  never coalesces two.
- `timer_values` gains optional `keepalive_probe_interval` (duration ms, per target): how often the
  worker sends the liveness probe, re-armed by any E3 and by each probe. Range: > 0 and
  `< keepalive_interval` when that is > 0; ignored when `keepalive_interval` is 0 (off). Absent, the
  worker probes every `keepalive_interval` — v0.2's behaviour, so a `timer_values` valid at v0.2.1
  stays acceptable and a rolled-back v0.2 core still drives a 0.3 worker. **The core sends the field
  only to peers ≥ 0.3** (D29): the frozen v0.2 worker refuses a `timers` whose key set is not
  exactly its own, so the key sent toward 0.2 would kill every session there.
- N-1 positive: `n1-v0.2-worker-positive` runs a v0.3 core against the frozen v0.2 worker.

**Runner.**

- `--profile e2` is the default; `--profile e1` asserts exactly v0.2.1's set; e5 adds the pagination
  set. **Era rule (F5):** a fixture reads server frames as its own era knew them — a frame type
  introduced at a later tag than the fixture's mark epoch is skipped like an unknown `t` — so
  v0.2.1's `token-event-on-detach` stays green at e1/e2 without editing it.
- `python -m tsbc serve [--bind 127.0.0.1] [--port P] [--static DIR]`: the shipped fixture server,
  stdlib, plain HTTP on loopback, built from the test stubs (which import it back; the `BUGS`
  toggles stay in tests only). Scenarios by target id — `normal`, `alt-screen`, `stale-pending`,
  `two-client` — each answering with a canned golden dump, so no stub shell or VT is needed.
  Prefix-content fixtures are exempt from the serve acceptance run. `--static DIR` serves a web app
  from the same origin. Acceptance: `tsbc verify http://127.0.0.1:P --seams C1,C2 --profile e5`
  passes every live C1/C2 fixture against it.
- New fixture step forms: the `prefix` expectation (text frame then binary message), an HTTP
  expectation on a binary body and response headers; the OpenAPI subset admits
  `application/octet-stream` + `headers` on exactly the pages 200.
- Artefact check `recordings-broker-marker`; `REFERENCE_FRAMES` gains `stale`, `stale_prompt`,
  `stale_answer`.

**Client.**

- `attach(…, cols=None, rows=None)`, `Attachment.handoff(to)` (`to` its own `client_id` is the
  owner's take-over), `Attachment.resize(cols, rows)` now returning the `size` answer,
  `answer_stale(prompt_id, choice)`, `Client.remove_client(sid, cid)`,
  `Client.output_page(sid, before_seq, max_bytes)` → bytes + headers; `recv_message()` returns the
  new frames; `recv()` keeps returning output only — the dump included, as bytes a renderer needs.

**DEFAULTs — U1's**, each vetoable by line (approval page): D1 frames stay `v` 1, unknown `v`
ignored · D2 viewport on `attach` · D3 `prefix` frame + one binary message, held output, 4011 on
timeout · D4 scrollback in `prefix`, required e5 · D5 `clients` on record and frame, `may_control` ·
D6 `handoff` frame, owner revoke to escrow until the owner reassigns (amended, F11) · D7
`request_refused` · D8 no ownership transfer at v0.3 · D9 removal on C1, close 4014 (was 4013, F2)
· D10 attach order, wiggle unannounced · D11 stale frames, fixed texts, choices by protocol · D12
any answer ends pending (incl. fresh session; cost stated, F20) · D13 pages: headers, whole frames,
never across a gap, ground start, cap knob, no-ground signal · D14 page right = attach right · D15
VT socket dir, N allowed · D16 C4 framing (big-endian, max size, chunked blobs, `request_id` =
`common` `id`) · D17 C4 message names · D18 idempotent feed, gap moves seq only, VT-feed-before-commit
· D19 grids outlive the core · D20 dump contents and encoding (full mode state, mid-sequence answer,
SGR pen, kitty/modifyOtherKeys) · D21 `snapshot_incompatible` · D22 `applied` ground marks · D23
snapshot bucket as JSON + base64 (no `recording_offset`) · **D24 withdrawn (F1: superseded by the
corpus-based reading of 5.39 in C6 decision 2 — one persisted value, commit only at snapshot
persistence)** · D25 `stale_seq` (same bbolt write as the gap commit, F4) · D26 three audit events
(`session.control` reasons incl. `takeover`/`release`; no audit of size requests) · D27 code 5 and
its ASCII line · D28 C3 `proto_version` `"0.3"`, no new message · D29 `keepalive_probe_interval`
sent only to peers ≥ 0.3; absent toward a 0.3 worker = probe every `keepalive_interval` · D30
`tsbc serve` scope (canned dumps, `--static`, `BUGS` test-only, one live fixture per scenario) · D31
take-over by typing or by `handoff` to self, the ordering rule, `token` reasons `takeover`/`release`,
`input_refused` `not_typist` (cost: an owner's stray keystroke takes the keyboard — the ruling's
intent) · D32 auto-release is a core knob with no contract field but the reason · D33 `resize` =
target size request, attach viewport never a request · D34 `size` frame, its six reasons, broadcast
to every attached client · D35 `not_typist` on the `size` answer is the one refusal path for a size
request · D36 floor, ceiling, threshold and coalescing window are knobs with no contract field ·
D37 coalescing last-wins, every request of a burst answered in order with the size that applied ·
D38 `not_resizable` published e2, required e3 (v0.4 brings serial) · D39 `stale.cause` and the
durable `stale_cause`, `gap_seq` only for `gap`.

**Rulings applied** (the operator, 2026-10-06 and the plan's earlier ones): **5.46** after lost output
the screen is stale: input is refused until the control-token holder chooses a redraw (wiggle), Ctrl-L,
continue or a fresh session, and TSB sends nothing to the target on its own; separately, the
redraw-on-attach wiggle, issued by the core, never answered with `size`, unannounced to clients;
**5.47**
the attach prefix and its dump; **5.48** pages — `prefix.scrollback` says how much scrollback
exists, and a page starts at a parser-clean boundary; **5.49** no plugin API (no contract change);
**5.50** the target size — `resize` is the holder's request, `size` the applied answer, no client
viewport enters the PTY; **5.51** `keepalive_probe_interval`, the per-target liveness probe;
**5.52** a grid that cannot be rebuilt raises the same stale flow with `cause` `grid_lost`, not a
separate one; **5.53** the owner takes the keyboard back by typing or by `handoff` to self, in the
same trip as the input.

**v0.2 fixtures changed** — text only this tag made false (no `doc`/`valid` change), held by
`tests/v02-fixture-digests.json`: `C1/auth-cross-origin-403`, `C2/data-path-marker`,
`C3/control-socket-stays-open`, `C3/input-refused-too-large`,
`C6/out-bytes-committed-after-reopen`, `C8/input-recorded-before-sent` (each a `graded_by` path
re-pointed at the test that grades it); `C2/token-event-on-detach` and `C2/unknown-frame-ignored`
(`description`: what "a later tag"/"contract-v0.3" will do is now done); `C6/no-claim-token-field`
(`description`: "no grid snapshot or snapshot_seq (contract-v0.3)" → they live in the
`grid_snapshots` bucket). Schema text (not fixtures) also changed where a shape changed: the
`ws-frames` top description gains the ordering rule and the `resize` and `attached` text is
rewritten as the target size request, `token` loses "Nothing goes client → server at this tag",
`continuity-state` and `ipc-control` descriptions and `timer_values` gain the new key, and
`fixture.schema.json` the step forms. `tests/test_additivity.py` holds the v0.2.1 tree to the
record.

**Known gaps.**

- **Ownership transfer is not at this tag** (D8): the owner may hand off to an attached client and
  revoke to escrow, but cannot transfer ownership of a session to another principal; the
  orchestrator places it at a later tag.
- **No core ring before E5**, so `prefix.scrollback` and the pages endpoint are published e2 and
  required e5; an E2 core answers pages 501 `not_implemented`.
- **Pages after a core restart start at the replay** — the ring is not yet rebuilt across a restart
  at E2.
- **VT upgrade vs snapshot format**: a `snapshot` taken by one engine version may not restore into
  another (`snapshot_incompatible`); how an upgrade is staged to avoid a `grid_lost` stale flow is
  U4/U2's, not the contract's.
- **The attach wiggle is unrecorded** (5.46): the redraw-on-attach resize pair is never announced to
  clients and never written to the recording (ttyrec has no resize).
- **`not_resizable` published e2, required e3** (D38): serial has no driver at v0.3, so the only
  protocol with no size is first gradeable at E3 with v0.4's telnet/serial.
- **Three prefix-content fixtures are exempt from the serve run**: `attach-prefix-mid-session`,
  `attach-prefix-modes`, `attach-scrollback-amount` (the `.serve-exempt` flag), because they need a
  real shell's output, not a canned dump.
- **#114**: the frozen v0.2 worker refuses a `timers` carrying an unknown key, against C3's own
  ignore-unknown rule. It stays open on U3; this tag gates `keepalive_probe_interval` to peers ≥
  0.3 (D29) so the key never leaks toward it, and the 0.3 worker fixes the bug.

**Fixed in the release review.**

- `ws-frames`: a `chosen` `stale` event must name a client in `by` (null is refused; `pending` is
  unchanged), and a `stale_prompt` choice is valid only with its own fixed text.
- `continuity-state`: a `grid_snapshot_record.snapshot` is whole Base64 (groups of four, a final
  group of two or three characters, padded or not), not any run of Base64 characters.
- `ttyrec`: a `broker_marker` is one of the four `x-broker-line` lines with its exact length.
- The client: `resize` waits one deadline for the `size` frame, not one per frame read, takes an
  optional `timeout` (0 sends and returns at once, as at contract-v0.2.1), and `recv` no longer
  discards the server frames a `resize` queued.
- `serve`: a client whose send fails is dropped (listing and control token follow); a bad
  `Content-Length` or JSON body is answered `422 invalid_request`; a malformed WebSocket frame or
  non-JSON control closes the connection with a close frame and the normal cleanup.
- The runner: a vt-control capture whose second run does not open with the core's hello is
  reported; the C4 stream decoder refuses a kind other than 1, 2 or 3; a repeated self-check run
  registers the octet-stream check once.

## contract-v0.2.1 — grader fixes

A **patch tag**: the grader and its tests are fixed, and nothing a provider sends or records changes.
No schema, message or fixture-instance semantics change: every field, every fixture instance and
every wire behaviour stays exactly as contract-v0.2 defines them, and a v0.2 core or worker needs no
change to pass. Consumers pin **v0.2.1** to get these fixes — the tag is otherwise v0.2.

Tests that the corpus says the system passes were being failed by an over-strict grader. Four
captures from a real E1 collection (`make fixtures` on the e1 profile) are the record.

- **Heartbeat trailing pings (C3 `heartbeat-ping-pong`).** The end-of-capture rule allowed only the
  last ping to be unanswered, but session model §11 and T11 say a hung worker misses
  `heartbeat_missed` pings (3) before the core's supervisor kills it. A capture of a hung worker
  therefore legitimately ends with 2 or 3 unanswered trailing pings, which the lab's hung-worker
  connections show exactly. The grader now allows up to `heartbeat_missed` trailing unanswered pings:
  counting back from the newest, the j-th newest is excused only if the limit in force when it was
  sent was at least j, and the first ping that fails this ends the excused group, so every older
  unanswered ping is still a fail — a later `timers` that raises the limit never excuses pings sent
  under a smaller one, and one that lowers it is honoured too. A v0.1 capture with no value keeps the
  old rule of one, and more than the allowed number is still a fail. Unchanged: a ping skipped while a
  later one was answered, and every supervisor rule.
- **The "new" handshake (C3 `reattach-empty`, `reattach-sequence`, `redial-replay`).** A new session
  was counted only when its `hello_ack` reported `current_seq` 0, but a real target prints its banner
  before the core's `hello` arrives, so the ack reports `current_seq` ≥ 1 and the replay carries those
  bytes. The C3 handshake text makes that the one handshake path for a new session, a core restart and
  a lone re-dial, so a handshake remains "new" when the session was opened in this run **and** the
  core's `hello` carried `last_committed_seq` 0 — whatever `current_seq` the worker answers. The other
  three kinds are unchanged. The three fixtures' descriptions are updated to say so; the instances stay
  valid against their schemas.
- **A real input frame dropped at a spooled end (C3 `ns-derivation-in`, issue #57).** `_match_frames`
  trimmed the last recorded frame when a recording ended with a spool gap marker then the end marker.
  That trim is for the output direction, where the gap marker is in `recorded`; for input the gap
  marker is never in `recorded`, so the trim deleted the last real input frame. The trim is now applied
  only when the last recorded item is the gap marker (the output direction), so a spooled end no longer
  loses an input frame. The invariant — every tapped input frame is in the recording, in order — stands.
- **A client test read what the server had not recorded yet (contract test only, issue #83).**
  `test_blocking_attach_round_trip` read `session(sid)["state"]` immediately after `att.close()`, but
  the server records the detach asynchronously when it handles the close, so the REST read could land
  first and still see `live`. The test now polls the state until it reads `detached` (up to 2 s) and then
  asserts. This is a test-stability fix; the client and server are unchanged.
- **Patch tags.** The tarball builder and the self-check accepted only `contract-vX.Y`; both now also
  accept `contract-vX.Y.Z`, keeping `-dev` where it is accepted today, so a grader-only patch can ship
  as its own tag.
- **Package version.** The runner and the client now read version `0.2.1` (`tsbc.__version__` and
  `pyproject.toml`), the contract tag's numeric part, not the stale `0.2.0`.

## contract-v0.2 — E1, broker-owned continuity

Additive on contract-v0.1 (immortality §6.4: additive only, unknown fields ignored; the core speaks
N and N-1): every instance valid at v0.1 is valid here, and a field added to a v0.1 message or
record is optional in its schema — what a v0.2 sender must send is graded by fixtures and artefact
checks, never by making it required. 139 fixtures: the 70 of v0.1, and 69 new, all marked e1. The
`--profile e0` set is exactly v0.1's 62; `--profile e1` asserts 135 (v0.1's 62, its five e1
fixtures, and 68 new); four are published only — v0.1's three, and C3 `reattach-non-pty`, required
with the tag that opens a non-PTY session. The default profile is now e1, the epoch this tag pins.

**C3 — core ↔ worker, core ↔ supervisor.**

- The local reattach handshake (immortality §6.1, LOCKED: SO_PEERCRED alone, no claim token; the
  §6.3 sketch with every token field stripped). Every data-socket peer starts with it — a new
  session, a core restart, a lone data-socket re-dial — so output lost on a dead socket is replayed
  by one path. The core dials the data socket, then sends `hello` with `last_committed_seq`; the
  worker answers `hello_ack` only once it holds a new data peer, waiting at most
  `heartbeat_interval` before `error no_data_peer`, and replays on that peer; `replay` from the next
  seq; `replay_done` through `current_seq`, since from `hello` to `live` the worker reads nothing
  from the channel; replay is complete when `replay_done` has arrived and either nothing was due or
  its last frame has; `live`, after which the worker accepts input. A new session is offered (the
  201) only after its handshake reaches `live`. Input before `replay_done` is refused
  `input_refused replaying`. `last_committed_seq` is the last output frame *or gap marker* the
  recording keeps: a gap marker is a commit point, so a core killed right after writing one
  declares no loss twice. The replay is complete once `replay_done` is in and either its last frame
  has arrived or nothing was due, so a handshake never waits for a frame that will not come.
- Retention: an end whose output a core may not have received is kept — ring, data socket,
  manifest entry — until the core sends `release`, at once when attached (the data peer's EOF after
  the end) or after a handshake reporting `ended: true`. A retained session is not a session for
  `connection_linger`, so retention ends with the connection.
- Only PTY sessions get a ring, a replay and a gap (Contract §15.1, binding). A non-PTY session runs
  the handshake with nothing buffered, and while no core holds its data socket the worker stops
  reading the channel. For `sftp`, output already in flight to a peer that died is the transfer
  layer's to resume by offset, outside C8 and with no gap frame. `exec`, `netconf` and `forward` have
  no loss rule at this tag — no v0.2 worker opens one — and the tag that first opens one says how its
  loss is declared; until then a seq jump on one is a silent splice, refused as on a shell.
- Version: `proto_version` (`^[0-9]+\.[0-9]+$`, "0.2" here) on `hello`, `hello_ack`, both manifests
  and `worker_created`; absence means v0.1. A live v0.1 worker's sessions are `unreachable` /
  `protocol`, never killed by `worker_heartbeat`; their idle/unattended timers and owner/admin ends
  still reach it as `close_channel`. The core neither reads nor records its output (decision 5.38:
  it shows what it already has): it dials each data socket once, to tell live from lost, and closes
  it unread. `unsupported_proto`, `no_such_session` (session model §9 E10) and `no_data_peer` join
  the error codes, and each names its session; an `unknown_cmd` naming a session answers its
  `hello`. The data frame's version byte stays 1.
- The gap frame: flags bit 1, output only, no payload, seq `first_buffered_seq` − 1 (the last lost
  seq), timestamp `last_dropped_ts`; it appears only in a handshake's replay.
- Timer values on `connect` (`timers`, `buffer_bytes`, `input_frame_max`, each a range and never a
  default) and on every change (`timers`); a v0.2 worker refuses a `connect` without them
  (`bad_request`). `keepalive_interval` 0 turns `link_timeout` off; `connection_linger` 0 closes with
  the last session. The two heartbeat values are core-wide (their scope is the worker), a stated
  departure from session model §11's per-target override; the rest take one. `buffer_bytes` and `input_frame_max` are fixed for a connection's
  life and kept in its C6 record, so a restarted core splits input at the connection's value.
  `input_frame_max` is 1 … 2^32−1; the worker refuses a longer input frame from its header
  (`input_refused too_large`) and closes that data peer.
- Heartbeats: `ping` / `pong` on every connection's control socket, answered from the worker's own
  loop, and on the supervisor socket.
- `link_state` (`missing` | `answering`, `last_ack_at`) on a transition, and as the first event to
  every new control peer of a connected connection.
- `connection_lost` cause `linger`, listing no session (its shape says so). A v0.1 core reads it as
  transport.
- The control-socket clause: a worker closes a control socket only after `connection_lost` or by
  exiting, never to shed a slow core; it ends nothing when its core closes one without
  `disconnect`; the core confirms a control EOF with one dial before E9. The data-path statement: a
  data socket that ends alone is re-dialled — once for each such loss — and replayed; a re-dial that
  fails is E10 once the spool holds no end, and the core then sends `close_channel` when the control
  socket still answers; input from the loss until `live` is refused `replaying`.
- Supervisor: `kill_worker` → `worker_killed` (after the reap; also for a worker already gone) or
  `kill_failed unkillable`; `ping` / `pong`; `error` gains `bad_request` and `unknown_cmd`, the two
  reasons that alone may lack `conn_id`; every request line gets exactly one answer; the
  `ready_timeout` text covers a withdrawn hand-off; replies need not follow request order, a `pong`
  is never held behind a kill, and an error with no `conn_id` answers the oldest line with no reply
  of its own — a kind this tag does not define, or a request missing its key — never a ping, a
  `list_workers` or a request that names its key;
  `create_worker` gains `undialled_timeout`, after which the worker drops that connection and exits
  only if it holds no other;
  `worker_created` gains `proto_version`. The placement sentence says what session model §4 says:
  placement isolates crashes, not compromise — every worker runs as the one service uid.

**C5 — manifests and the spool.** `proto_version` on both manifests; a retained ended session
stays listed until released. `/tmp/tsb/spool/{session_id}.json` (`spool-entry.schema.json`):
exactly the ends a worker can name (killed / orphan, idle, unattended; terminated / target_closed,
connection_lost), with `last_seq`, `out_bytes`, `last_out_ts`, written atomically and fsync'd before
the end is acted on. Rediscovery in the v0.2 order (`c5-rediscovery-v0.2`): the spool, then the dial
as at v0.1, then — for live peers only — the version branch; a retained ended session is replayed
before its spooled end is written, and an entry for a session already audited is deleted unread; a
core attached at the end deletes the entry once it has audited it. The v0.1 rule `c5-rediscovery`
keeps its meaning.

**C6 — durable state and audit.** `continuity-state.schema.json`: the sessions bucket holds the
record E0 already writes (an E0 record validates unchanged: `""` tenancy fields, clocks with a
local offset), plus optional `protocol` and `out_bytes_committed` — the output bytes through the
frame `recording_last_seq_out` names, declared losses included, persisted in the same write;
`recording_last_seq_out` is `last_committed_seq`, one counter. The connections bucket maps a
connection to its worker, target, credential reference, version, sessions and the two knobs it was
connected with. No claim token, no
snapshot state (v0.3), no owner-token id, no control-token escrow state. One five-value state list
for C1 and C6 (`live`, `detached`, `killed`, `terminated`, `unreachable`). Audit: `alert.raised` /
`alert.cleared` (TSB components only, one `alert_id` per alert); a spooled `session.disconnect`
carries the worker's stamp.

**C8 — recording.** Sidecar code 2 is the gap marker: one fixed-format line
(`\r\n[TSB gap: N bytes lost from <start> to <end>]\r\n`, UTC, microseconds) a classic player
prints, stamped with the span's end, standing before the first surviving frame or, for a loss the
spool reports, before the end marker; a TSB reader knows it by its code (`x-gap-line`). The byte
count is `first_buffered_offset` (or the spool's `out_bytes`) less the output bytes the recording
held, declared losses included, and the span starts at its last output frame or gap marker (the
session's creation when it held neither), so two markers never overlap. `x-compare`:
payload-identical per direction (codes 0 and 1, boundaries free, end markers out, gap markers
compared line for line) and monotonic per
direction, each stamp with its owner. The reopen rule gains: no output recorded twice
or lost after a reattach; the core records an input frame before it sends it.

**C1 / C2 — northbound.** The session record gains the countdown (`ends_at`, `ends_by`, `ending`:
all three on every record not yet ended, none on an ended one) and the state `unreachable` with
`unreachable_reason` (`protocol`). New server frames: `notice` (when `ending` or `ends_by` changes,
or `ends_at` moves while ending), `quiet_prompt` (fixed text "Kill the session?"), `token` (reason
`attach` | `detach`, holder or null), `input_refused` (`unreachable` | `replaying`), `data_path`
(`lost` | `restored`); client frame `quiet_prompt_answer`. After a restart the control token is in
escrow and the next client that may type takes it on attach (`token-escrow-after-restart`); a
view-only client never does. No ack or resume frame (decision 5.15).
4011's meaning names the core stop. `GET /alerts`: a text/event-stream behind `broker.health`,
cookie-authenticated — the active set, then each raise and clear; a keepalive comment every
`heartbeat_interval`; ended on logout, expiry and the loss of `broker.health`. The alert names its
components by one rule for the stream and the audit. Under OpenAPI 3.1 the stream's schema
describes one event's data, and `x-itemSchema` names it so, until OpenAPI 3.2's `itemSchema` (a
stated deviation). 403 `forbidden` is
declared on `POST /auth/login` and `POST /auth/logout` for a cross-origin browser write. The 409
`connection_choice_required` lists the principal's own candidates in `error.connection_ids`, and no
other code carries them. 503 `worker_unavailable` also covers workers that predate the core.
`broker.health` joins `policy.yaml`'s permissions.

**Runner.** The OpenAPI subset admits `text/event-stream` for `GET /alerts`' 200 and nowhere else,
with its `x-itemSchema`, and a request sends its operation's `Accept`. A C2 frame is covered by a
live fixture, as at v0.1, except the five no live run can produce (`notice`, `quiet_prompt`,
`quiet_prompt_answer`, `input_refused`, `data_path`), which a reference-graded instance covers.
Tapped input must be an in-order subsequence of the recording: a recorded-but-unsent frame is
allowed at the end of a killed run, and one per lone re-dial inside a run, since the core records
before it sends. Match kind `none`. Sequence messages may carry `run`
and `invalid`; byte fixtures may hold one tap per run (`runs`); transforms `c5-rediscovery-v0.2` and
`c8-compare`, and `c3-to-c8` takes the handshake's and the spool's numbers. Captures gain the run
boundary and the `run-frames` kind. New artefact checks: `control-reattach`, `frames-continuity`,
`frames-monotonic`, `control-timers`, `control-heartbeat`, `supervisor-heartbeat`, `control-linger`,
`recordings-gap`, `recordings-monotonic`, `audit-orphan-end`. `frames-seq` and
`recordings-match-frames-out` take the replay overlap once across a boundary; a collection with no
boundary grades as at v0.1; a gap frame both runs tapped must repeat exactly, and only an earlier
real frame the later run declares lost may differ. `recordings-gap` matches gap frames to markers in
order, so two losses in one microsecond stay two, and holds a first span's start between the start of
the run that opened the session and its `session.connect`, since no collected file holds
`created_at`. A live step that expects `GET /alerts`' 200 reads the stream as it arrives — at most
64 events, for at most the run's timeout — and holds each `alert` event to `x-itemSchema`. The
satellite and snapshot guard is a property-name guard.

**Client.** `ConnectionClosed` (one class both transports raise), `HandshakeError`,
`ProtocolError` and `HttpError` exported; `recv_message()` returns output and the new server frames;
`answer_quiet_prompt()`; `Client.alerts()` reads the alert stream with a stdlib event-stream reader
(`AlertStream`), sending `Accept: text/event-stream`, and failing the stream with `HttpError` past
a line of 64 KiB or one event's data of 1 MiB. `recv()` keeps its v0.1 meaning, and the private
`tsbc` names consumers import keep their signatures.

**DEFAULTs — U1's**, each vetoable by line (approval page): one handshake for every data-socket
peer; `hello_ack` bounded by `heartbeat_interval` then `no_data_peer`; the completion rule across two
sockets; a new session offered only after `live`; retention with `release`, bounded by the
connection; the gap frame's shape and its seq; the loss computed from `hello_ack`, with no separate
message; the spool's form; `link_state` and the linger cause; `input_refused` on C3 and C2;
`buffer_bytes` and `input_frame_max` on `connect`, and a worker refusing a `connect` without them;
the supervisor's names; the version pattern; the C6 layout; the gap line's text and parse rule; the
names of every message, frame, field, check, fixture and transform; the run id and its layout; the
default profile e1.

**DEFAULTs — the orchestrator's** (delegated 2026-09-29): O5 `orphan-pending` is a worker-only state,
on no wire and in no record — a departure from immortality §7's wording, "Session state machine gains
two states"; O6 only `sftp` resumes by offset (Contract §15.1), with no ring and no gap frame, and the
loss rule for `exec`, `netconf` and `forward` is left to the tag that first opens them; O7 a v0.2
core neither reads nor records a v0.1 worker's output (decision 5.38) — **cost:** output after the
upgrade is not recorded for such a session, which the E1 upgrade's one-time `restart-workers` (O1)
makes moot in practice; O8 `text/event-stream` kept, with the per-event schema stated. O1 no new session on a v0.1 worker — 503
`worker_unavailable`; **cost:** after a core-only upgrade no new session until the workers restart,
so the E1 upgrade runs `manage.sh restart-workers` once. O2 `broker.health` a per-user permission,
granted to the seeded superadmin by the deploy's template. O3 no control-token escrow state in C6.
O4 `quiet_prompt`'s audience and its answer rule.

**Rulings applied** (the operator, 2026-09-29): Q1 the alert stream behind `broker.health`; Q2 (a)
the gap marker's numbers as a readable line; Q3 the 409 lists the principal's own candidates; Q4 (b)
telnet and serial keep 502 `target_connect_failed` until contract-v0.4; Q5 (a) 403 on login and
logout; Q6 (a) the data-path marker at this tag; Q7 backup and restore stay out of C6; Q8
`input_frame_max` a `connect` knob, range only; Q9 (b) no worker-wide buffer cap; Q10 the placement
sentence reworded; Q12 (a) consumers read their pinned tag — landed before this tag (tsb-core #36).

**v0.1 fixtures changed** — only text this tag made false, and the one case that asserted code 2
was held for this tag: C3 `data-frame-shape` (description; two instance notes: bit 1 and the empty
frame were "reserved at this tag"), C5 `manifest-closed-v0.1` (description: "the version marker the
next tag introduces"), C8 `end-marker-killed` (description; its invalid case "gap marker a later tag
defines" re-cut as a code-2 frame whose data is not the gap line). No other instance's doc or
verdict changed; `tests/test_additivity.py` holds the v0.1 tree to a digest record of the tag.

**Known gaps.**

- The pinned-key reset endpoint and the credential-choice field are still to come (the vault tag).
- Telnet and serial targets answer 502 `target_connect_failed` until contract-v0.4 connects them.
- No worker-wide ring cap: memory is bounded by N × sessions × `buffer_bytes`.
- The attach prefix a reattaching client needs is contract-v0.3's: at E1 a client may repaint.
- C1 `session-create-reuse-empty-connection` (v0.1, required green at e1) needs the graded prefix's
  `connection_linger` above 0 and above the time the fixture takes.
- Whether a gap marker was due before a spooled end is graded only by the core's own test: the spool
  entry is deleted once written, so no collection holds it.
- A v0.1 session left under a v0.2 core records nothing after the upgrade, and nothing in its
  recording marks that: a v0.1 worker reports no byte count to declare (O7).
- `exec`, `netconf` and `forward` have no loss rule at this tag (O6); no v0.2 worker opens one.
- `lang-arch` §4.1 still sketches `connected {session_id}` and §8.2 `type`; the contract follows
  Contract §15 (`conn_id`, `session_type`). The corpus edit is pending.

## contract-v0.1 — E0, walking skeleton

The first tag. It pins E0: the core, the worker and supervisor, the TUI and the bare attach
page. 70 fixtures: 62 `required-green: e0`; 8 `published: e0` only — of those, C1
`credential-choice-required-409` and C3 `open-sftp`, `open-subsystem` are required green in no
epoch yet, and five are required green at e1:

- C1 `session-delete-not-owner-403` and C1 `no-resolving-credential-409` — they need a second
  principal and a target with no credential, which the E1 harness seeds (phase-00 seeds one
  superadmin);
- C1 `session-create-reuse-empty-connection` — a connection still up with no session on it, which
  `connection_linger` keeps from E1; at E0 it is 0, so a connection closes with its last session;
- C8 `worker-death-recorded` and `end-marker-terminated` — a recording that ends terminated, from
  a worker the harness kills while collecting (the E1 worker-death rig). At e0 self-check grades
  the bytes and the core's own test proves the same end (C1 `session-ended-unexpectedly`).

**C1 — northbound REST** (`openapi/tsb.openapi.json`). TSB's own login: `POST /auth/login`,
`POST /auth/logout`, `GET /auth/me`, with the session cookie `tsb_session` (HttpOnly, Secure,
SameSite=Strict). `POST /auth/ws-ticket {session_id}` → `{v, ticket, expires_in}`, one-time,
bound to (principal, session), and short-lived: `expires_in` is at most 60 seconds.
`POST /sessions {target_id, connection: reuse|new, connection_id?, term, cols, rows}` →
`{session, hostkey?}`; a reuse candidate is a connection of the principal to the target, same
credential, whose transport is up, whether or not a session is on it now (`connection_linger`
keeps a connection up after its last session so a quick reopen reuses the login, session model
§5, §11); a connection that has closed or was lost is never one; reuse with two or more
candidates is refused 409
`connection_choice_required` unless `connection_id` names one — the broker presents the choice
and never picks. `GET /sessions`, `GET /sessions/{id}`, `DELETE /sessions/{id}`, `GET /healthz`.
There is no REST resize: the C2 `resize` frame from the control-token holder is the one resize
path. Error envelope `{error: {code, message}}`, 15 codes, among them `connection_choice_required`
and `credential_choice_required` (409; the field that carries a credential choice arrives with
the vault tag); a consumer handles a code it does not know by the HTTP status. Error messages
never name a path on the broker host. Session states (session model §10, decision 5.45):
`live` and `detached` — closing the laptop lands there, and a client going away never ends a
session — and two end states: `killed`, ended by TSB or through it (the `idle`, `unattended` or
`orphan` timer, the owner, an admin), and `terminated`, ended from outside TSB (its worker was
lost, the target closed it, or its connection was lost). There is no `disconnected` state: with
the connection gone there is nothing to keep, and a reconnect is a new session. A consumer treats a
state it does not know as not attachable. An active recording
carries `warning_code` `recording.active` and the fixed warning text. A `session.join.ro`
principal never holds the control token. User names are path-safe. Schemas: the northbound
session record (closed; carries no broker path), `policy.yaml`, the error envelope. No target
listing (contract-v0.4).

**C2 — session stream** (`GET /ws/attach`, `schemas/ws-frames.schema.json`). The first text
frame is `attach {t, v, ticket, session_id}`; the server answers `attached {session, client_id}`
or closes with 4000, 4001 (unknown, expired or used ticket), 4003 or 4004. Binary frames are raw
bytes both ways; the one control frame is `resize`. The session's end reaches an attached client
as a close code: 4010 `session_closed` (killed), 4012 `session_terminated`; 4011 is a
server-side detach. A client treats a 4xxx code it does not know as 4010. Frames carry `v: 1`;
receivers ignore a `t` they do not know. No attach prefix yet (contract-v0.3), no state frame.

**C3 — core ↔ worker, core ↔ supervisor.** The lang-arch §4.1 control set on
`conn-{id}/control.sock`, with credentials only in `connect.auth`, plus `close_channel` /
`channel_closed` and `hostkey`; protocols `ssh` and `echo`; an unknown command is answered by
`error unknown_cmd`. The worker says who ended a session (session model §9 E5, §13):
`channel_closed` carries `by` (`core` answers `close_channel`; `target` — the target's
`SSH_MSG_CHANNEL_CLOSE`, RFC 4254 §5.3 — ends that session `terminated` / `target_closed`), and
`connection_lost` carries `cause` (`disconnect` answers the core's; `target_disconnect` — the
target's `SSH_MSG_DISCONNECT`, RFC 4253 §11.1 — ends every listed session `terminated` /
`target_closed`; `transport`, our end of the TCP connection gone, and `keepalive_timeout`,
`link_timeout` fired, end them `terminated` / `connection_lost`). A core reads a value it does
not know as an end from outside TSB. The contract carries and interprets no exit status or exit
signal — optional in RFC 4254 §6.10 and unreliable on network gear — and nothing matches
prompts (Contract §6); a worker's `reason` is free text. Telnet and serial have no end message
and arrive with a later tag. The supervisor clause: `create_worker {conn_id, placement_key, n_max}`
(no credentials) → `worker_created` only once the worker is bound, or a bounded `error`;
`list_workers` → `workers`, each with its key. Placement (session model §4): a worker keeps the
`placement_key` it was spawned with for its life and takes a connection only of that key and only
below `n_max`; under `per_user`, the default, the key is the owning TSB login, so one user's
worker never holds another user's connection (Contract §4), and under `shared` it is one constant.
The key has a user name's shape and the supervisor only compares it. The data
socket's 24-byte big-endian envelope: version, flags (bit 0 direction), stream, length, u64 seq,
u64 nanosecond timestamp. `seq` on output is immortality §4.1's worker-assigned per-session
counter; on input it is the core's own per-session counter. Every C3 socket trusts its peer by
SO_PEERCRED. The C3 fixtures are graded from the control captures a harness collects.

**C5 — manifests.** `/tmp/tsb/workers/{worker_id}.json` and
`/tmp/tsb/supervisors/{id}/manifest.json`, both closed, and the rule a restarted core follows to
rediscover workers: liveness is dialling the published sockets, and a socket that refuses or is
missing is dead. `pid` is informational — meaningful only inside the writer's container. A v0.1
manifest carries no version field; the next tag adds one.

**C6 — audit, minimal.** `auth.login`, `auth.login_failed`, `session.connect` (names the target),
`session.join`, `session.disconnect`, `session.terminate`, `target.hostkey_accepted`,
`config.recording_state`; `session_type` mandatory on every session event; `recording_path`
absent, never empty. `session.disconnect` closes a session's audit record with `end_state`
(`killed` | `terminated`) and `end_reason` (killed: `idle`, `unattended`, `orphan`, `owner`,
`admin`; terminated: `worker_lost`, `target_closed`, `connection_lost` — session model §12,
Storage §6). There is no expiry event: a timer's end is killed. `session_type` is session model
§2's five: `shell`, `exec`, `sftp`, `netconf`, `forward` — a session is one channel or one forward
(Contract §15). `exec` (one command, ended when the target closes its channel) and `forward` have
no v0.1 producer, since lang-arch §4.1 has no verb that opens either.

**C8 — recording.** One recording per session, both directions, in two files.
`<session_id>.ttyrec` is **classic ttyrec** — `[sec u32][usec u32][len u32]` little-endian, then
the data — so any ttyrec player reads it. `<session_id>.dir` is its sidecar: one code per frame,
0 output, 1 input, 3 end killed, 4 end terminated; 2 is held for the gap marker contract-v0.2
defines. A classic player plays both directions interleaved; keep the frames coded 0 to see only
what the target printed. Output frames keep the worker's timestamp and input frames the core's
send-time stamp, derived to sec/usec by truncation; readers never assume timestamps increase
across directions. **An ended session's recording closes with exactly one end marker**, as its
last frame: a ttyrec frame with no data — a player writes no bytes to the terminal for it
(unpatched upstream ttyplay 1.0.8 may warn on stderr about an empty frame) — stamped with the
time the session ended as seen by the component that observed the end (always the core at this
tag; from contract-v0.2 an end spooled while the core is away carries the worker's stamp, session
model §15), coded 3 or 4 to match the audit's `end_state` (immortality §1.1, §3:
a worker's death finalizes the recording with a termination marker; session model §12). Why it ended is only on the audit event. A live session's
recording has no marker. `recording_path` names the `.ttyrec`. Each frame is written to the
`.ttyrec` first, then its code to the `.dir`; before appending to an existing recording (after
a crash, say) the writer cuts both files to the last frame both hold, and a live recording is
collected only cut that way.

**Runner and client.** `python -m tsbc verify` (self-check, `--list`, a live URL, `--artifacts`,
`--provider-tests`, epoch profiles) and `tsb_contract_client` (blocking `attach`, asyncio
`async_attach`). Both are stdlib only and refuse `http://` except to a loopback host. Results are
PASS, FAIL, SKIP, PROVIDER and UNGRADED; a run that leaves required-green fixtures ungraded exits
3, never 0. A live or `--artifacts` run self-checks the tree first and grades nothing against a
tree that fails. A live run deletes, at its end, every session it created and left open, prints
a password a provider echoes back as `<redacted>`, and keeps a bounded tail of streamed output.
`pattern` follows ECMA-262 (`$` is the end of the string); JSON is RFC 8259 (no `NaN` or
`Infinity`). Timestamps bound every field (month, a day the month has, hour, minute, second).
Collected artefacts: a supervisor's control capture lives in `control/supervisors/`, apart from
connections'; a collection holds regular files only.

**Known gaps.**

- A target whose host key changes legitimately (a rekey) is refused `hostkey_changed`, and this
  tag has no API to accept the new key; a pinned-key reset endpoint is planned.
- More than one resolving credential is answered 409 `credential_choice_required`; the field that
  carries the operator's choice arrives with the vault tag.
- The rest of the session model is contract-v0.2 (session model §15): the timer values the core
  sends the worker on C3, the countdown every session record carries (`ends_at`, `ends_by`,
  `ending`) and its C2 notice, the heartbeats, the spool that records an end while the core is
  away, durable last-activity clocks, and the admin-only alerts. Until then a session that a timer
  ends while no core is present is not recorded as such.
