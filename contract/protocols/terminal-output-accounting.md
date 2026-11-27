# Terminal output accounting — C3 0.8, C5, C6 and C8

Normative terminal drain, sealing, integrity declaration and accounted-disposal rules. This protocol
extends core-restart, reattach-handshake, spooled-end-finalize, session-end-by-tsb and connection-linger
for a C3 0.8 peer; their legacy paths remain unchanged. An audited end is never reopened or ended again.
The original recording and sidecar remain immutable after their complete end marker. Unknown suffix
is never counted overflow or zero loss. All new fixtures are published e4 and required-green e4;
temporal/provider assertions are reference obligations, not claims of passing consumer implementations.

Schemas are relative to `schemas/`. Corpus authority is architecture-contract §§1–15,
immortality §§4/6, open-decisions §5 and session-model-and-lifecycle §§12–15. Integrity vocabulary
requires the operator-approved Storage §6 amendment in the same release; no release with a known
frozen-clause conflict. Configuration default values belong in consumer deploy templates.

## 1. Scope and acceptance

Terminal draining, sealing, replay and accounted recovery in this protocol apply only to PTY
sessions, which at this tag means `session_type` `shell` (Contract §15.1: only PTY streams get
the worker-side ring). Non-PTY sessions retain reattach-handshake's existing rule: no ring,
no replayed output, `first_buffered_seq = current_seq + 1`, and the same handshake gating input
and resuming output. They are never sent `seal_output` or `release_accounted` and never receive
a code-9 marker under this protocol. Their published loss rule is unchanged: the tag that first
opens one says how its loss is declared. The scope applies throughout, including §§3.3 and 9 A/B;
connection-wide configuration and audit-file startup repair keep their stated scope
[C3/terminal-non-pty-retained].

For a capable audited retained end, the handshake begins with hello, sent after the connection's first
ping (core-restart step 22). Separate channel ended, output producer sealed and clean EOF. Core-requested
release requires durable accounting; connection teardown never waits for that accounting and declares
the unaccounted suffix unknown. Live siblings, local SO_PEERCRED trust, normal core-stop survival and
old peers' exact old messages remain unchanged. Fixtures below cover the changed contracts; unknown
unrelated unreachable/dead-worker spool conflicts are not resolved here.

## 2. Configuration and frozen boundaries

| Ruling | Binding design in this revision |
|---|---|
| O1 | One required runtime `worker.output_drain_timeout`, connection-wide, positive whole milliseconds, no contract default; exact conversion and next-attempt reload. |
| O2 | Integrity audit facts and code-9 pre-end marker; code-2 remains counted overflow; validated contiguous audit-derived coverage, never max-through-seq. |
| O3 | New seal_output/output_sealed, optional hello_ack.output_seal; terminal_busy rejects a competing hello during sealing or a core-requested end in flight. |
| O4 | New release_accounted; duplicate after disposal succeeds silently, no acknowledgment. |
| O5/E1 revised, flagged | No new worker disk record for terminal output. Connection closes and drops retained resources on its existing deadline; core declares the unaccounted suffix unknown. |
| O6 | Truly unrecorded gets no invented missing-recording extent; partial/failed artifacts take integrity path; repair original unaudited end without changing its first end. The unrecorded close-notice encoding is specified in §5.7. |
| O7 | Preserve legacy path. Bounded individual recovery waits use existing I/O waits; leave retained/uncertain on failure while connection exists; retry on next rediscovery, no new running-core retry timer. |

No recording truncation/mutation except the published torn/uncoded tail reopen cut. Never remove/rewrite an end, insert a marker before an existing complete end, append after an end, renumber recorded bytes, or replace a counted gap with audit-only uncertainty while the recording can still hold its code-2 marker. Audited-end precedence is before spool/E10/live recovery; never a second end, client-end close, token/grid publication or synthetic worker_lost for that already-ended session. SO_PEERCRED local trust stays unchanged.

`worker.output_drain_timeout` is a Go duration string in config, optional as an additive schema field but **required by the new runtime**, with an actionable missing-key error. Accept only positive whole milliseconds representable both in signed Go duration nanoseconds and `common.duration_ms` (1..9007199254740991 wire ms). Reject zero, negative, fractional-ms and either overflow; no truncation/infinite/disabled value. No schema default. Template values belong only to the consumer deployment, never to this contract; upgrades preserve instance-owned config and require the operator to add the key. In a deployment whose `config.yaml` is rendered from committed templates, "the operator adds the key" is met by the template carrying it.

On C3 0.8 only, carry `output_drain_timeout` as an optional `timer_values` key through connect.timers and later timers. A new capable sender supplies it; an old peer gets no new key. Record its connection-wide scope in both V7 and timer_values as the second exception to per-target session timers beside the heartbeat pair. A connection keeps its last received value while core is absent. Each worker terminal drain and each core terminal wait snapshots its value and deadline at admission; reload affects the next attempt, not an active one. These are distinct phases using one value, not an overall recovery bound. Wakeups and duplicate seals never restart the budget. U2 cites tsb-core #680: no timer action, including a new drain deadline action, starts after Close begins. A core deadline winning the tie with worker completion enters uncertainty; it never infers EOF from the clock equality [C5/config-output-drain-timeout; C3/terminal-drain-timeout; C3/core-stop-during-end-drain].

## 3. Additive terminal exchange and durable coverage

### 3.1 Capability and C3 additions

Advertise C3 proto_version `0.8` consistently in common's version description, worker/supervisor manifest descriptions, supervisor worker_created text, C3 version tables and new shape fixtures. Preserve old tags/shapes; new commands are sent only after capability is known. Do not force worker replacement. Unknown version stays on published negotiation behavior; conflicting unreachable scopes are not decided here.

New `ipc-control` definitions use the existing per-session command/event envelope:

- `$defs/seal_output`: closed object, required `cmd` constant `seal_output` and `session_id` common.id, no other fields.
- `$defs/output_sealed`: closed object, required `event` constant `output_sealed`, `session_id` common.id, `output_seal` referencing `$defs/output_seal` below.
- `$defs/release_accounted`: closed object, required `cmd` constant `release_accounted` and `session_id` common.id, no other fields.
- `$defs/hello_ack`: optional `output_seal` reference. Its absence never proves anything. Existing fields/meanings unchanged; no newly required field invalidates an old hello_ack.
- `$defs/output_seal`: closed object, required `sealed` boolean. If false, no other field is present and no finality is claimed. If true, also require `read_outcome` enum eof/timeout/read_error, `final_last_seq`, `final_out_bytes`, `final_last_out_ts` (common.u64, all zero or all positive), `observed_at_ns` (common.u64 minimum 1), and `unknown_suffix` boolean, false iff eof and true otherwise. Counters count assigned/read payload, never bytes hidden in the driver. Observation time belongs to the worker and is retained unchanged with the seal. A partial sealed-true shape or unknown outcome withholds disposal.

Capable hello_ack reports the summary once a final seal exists; otherwise optional omission or sealed:false carries no final proof. The core only accepts output_sealed with sealed:true as a successful sealing result. The capable-provider presence/relations are graded by new fixtures rather than making legacy messages invalid [C3/control-message-shape-v0.8; C3/terminal-seal-idempotent].

Register one new error code `terminal_busy` in the existing C3 error registry; it carries existing required error fields plus the named session_id. A hello while that session is sealing, or while its already-requested core end is in flight, is refused with terminal_busy; close only the attempted new data peer, not the active sealing peer/task, sibling session or shared transport. No hello_ack for that attempt. The core retries with a fresh data peer only after the relevant output_sealed or channel_closed barrier, within its existing per-attempt I/O waits. If the barrier does not arrive, leave uncertainty for next rediscovery. No polling timer is added. Duplicate seal commands coalesce while sealing and receive the retained immutable result once finished; after sealing they get exactly that same result. Peer replacement neither resets the seal nor permits output mutation [C3/terminal-hello-busy; C3/terminal-seal-idempotent].

**An offer below 0.8 during a carried seal or close** (tsb-program #138, decided B, 2026-11-19). The terminal_busy refusal above answers a hello offering 0.8. When a replacement control peer's hello for a session offers a version below 0.8 while that session is sealing, or while its already-requested core end is in flight, the C3 0.8 worker answers error `unsupported_proto` naming the session, never terminal_busy. Once the operation has sealed, it presents the session to that peer by the ordinary legacy handshake (a hello_ack with no output_seal summary) and legacy release. It sends the 0.8-only events, output_sealed and an error with code terminal_busy, only to a peer that stated 0.8 in a hello the worker parsed, refused hellos included, so a 0.8 core's barrier survives its own terminal_busy refusal. The worker MUST also record receipt of `seal_output` on a control connection as that peer's statement of 0.8, even without a hello, and MUST start each replacement control peer with no statement [C3/terminal-seal-output-states-version]. Old peers' exact old messages (§1) and the §10 version gate stay literal. A core at an effective version below 0.8 is the legacy core: for a retained session the refused handshake takes its retained branch during the window; a non-retained session, possible only after a failed end persistence, becomes `unreachable` / `protocol` and is never killed (conventions V1). A running legacy core sends no later hello for either: it sets no retry timer (§2 O7), so the ordinary legacy handshake, its replay and legacy release come only at that core's next rediscovery, its next start (core-restart step 23), and a non-retained session stays unreachable until then. If the connection closes first (linger or loss), the retained resources go with it (§8) and that legacy release never comes. Accepted costs: this `unsupported_proto` misstates the worker's version support for that one session, and after sealing the session waits for the legacy core's next start [C3/terminal-legacy-offer-mid-seal].

A seal names an ended session only. On a live session, seal_output or release_accounted refuses bad_request; on an absent session, seal_output refuses no_such_session. A release_accounted for an absent session is silent success. It has no ack, so send success is not proof of application. Existing legacy release is unchanged, including its published in-progress-end effect; new recovery never uses it to stop an unsealed end. On ordinary clean fully recorded end its old assertion is still valid [C3/terminal-disposal-version-gate].

### 3.2 Sealing invariants

seal_output ends this session's paused handshake, detaches/closes its old data delivery and grants an explicit terminal read-pause exception without live. Ordinary hello still never resumes reading. Stop delivery before draining into its bounded drop-oldest ring, so dead data delivery cannot hold the reader. Include an in-flight payload if it can be assigned before the bound; otherwise its unknown status remains explicit. Distinguish normal empty-read EOF from error/cancellation/finally. Stop/join all producers before publishing sealed:true; no frame may be appended afterwards. Do not cancel a sibling's reader or shared transport to meet the bound. If the driver cannot safely stop/join, log a worker defect, publish no false successful seal and withhold core release; connection lifetime still follows §8 [C3/terminal-drain-timeout; C3/terminal-drain-read-error; C3/terminal-seal-idempotent].

Worker closes the old data peer before output_sealed and before channel_closed by:core, after enqueuing its final delivered bytes. A later fresh hello snapshots the same immutable seal and replay extent. No input/resize/live/publication occurs for this ended recovery path. Attempts at input are refused ended, not buffered; attach remains the existing ended-session refusal [C3/channel-closed-after-data-eof; C3/terminal-input-refused-mid-seal].

### 3.3 Coverage validation and release

Derive A=(L,B) by walking the preserved recording, including counted gaps and post-commit bytes. Scan audit history to validate contiguous coverage above it. Keep hello.last_committed_seq=L and replay.from_seq=L+1 (for a pair quarantined by a recording sync failure, P's seq: §5.3): audit progress never impersonates recording progress. With sealed hello_ack, let F=first_buffered_seq, O=first_buffered_offset, N=current_seq and Q=final_out_bytes. Require final_last_seq=N, matching timestamps/totals, F<=N+1, no underflow/overflow, nondecreasing worker stamps, uniqueness/order and no data outside the promised snapshot. N behind A, Q below accounted bytes, malformed/unknown/partial result or relevant audit evidence fails closed [C6/recording-integrity-exact-once].

If F exceeds the next wanted seq, count uncovered dropped assigned output from O minus covered cumulative bytes and use last_dropped_ts, retaining the already-recorded-gap exception; unexplained zero/negative loss is invalid. Replay surviving frames through N, verify payload lengths and both independent barriers: replay_done AND final data frame, or the published nothing-due condition. Discard already-accounted replay only when deriving new notices, never skip validation or move the hello watermark. Audit extents are exact read/assigned loss, not guessed unread output [C3/reattach-ended-audited-sealed; C6/recording-integrity-exact-once].

For an existing complete end marker, replay bytes are not recorded: append only newly needed exact audit extents and the sealed outcome. Before an unwritten end, record recovered output and counted code-2 loss, then code 9 if incomplete, then the sole original end and lifecycle audit. This unwritten-end order holds in a successor of a quarantined pair: recovered output, counted code 2, code 9 if incomplete (its `unconfirmed` fact on the generation-0 path), then the sole original end marker. [owed] Existing marker but missing lifecycle audit is immutable-recording recovery: append only missing original lifecycle audit plus needed integrity facts. If all is disabled and no artifact ever existed, use O6 and the not_recorded result, never a stale L. A partial/failed artifact still takes the recording integrity path [C6/terminal-unrecorded-and-partial; C8/audited-end-recording-unchanged].

Fsync complete accounting before release_accounted: every assigned byte recorded or durably declared unavailable, final seal durably known, and timeout/read_error's unread suffix explicitly declared. EOF plus no missing tail invents no lost-byte event; a sealed result can record finality without asserting loss. Audit write/sync failure means no core release and uncertainty until rediscovery. A session whose recording pair is quarantined by a recording sync failure (§5.3) is never sent release_accounted. Connection teardown remains independently allowed and invokes §8. A crash after durable accounting before/after release repeats from stable keys and observes presence/absence without re-ending [C6/terminal-accounting-write-failed; C3/terminal-disposal-crash-after-drop].

## 4. Shared arithmetic and identity rules

`U` below is the existing `common.u64`: integer 0..18446744073709551615, never decoded through float. `P` is U with minimum 1. `ID` is `common.id`, 1..128 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. `TS` is the existing audit `$defs/ts` reference to `common.timestamp`, not a new timestamp syntax. All new v1 objects below are closed, all listed fields required unless explicitly optional. JSON null is explicit unknown, never zero. Reject duplicate JSON keys, non-integral numeric values and unknown relevant variants/versions before using evidence to dispose a session.

For a recording continuation, the effective output walk and its baseline use trusted predecessor prefixes through P and the linked successors; the raw unconfirmed suffix is evidence and is never counted as output or used for a core decision (§5.3). A successor's initial offset is pair-local zero and its output seq and byte counters are inherited transitively from the last committed point (owed).

For a non-quarantined pair, the immutable recording walk yields baseline A=(L,B): last output seq established by its committed-gap/reopen rules, and output bytes including code-2 counted loss. Include preserved data past bbolt's commit point. For a quarantined pair, no decision walks or derives A above bbolt's commit point P (§5.3). Code 9 adds no seq or bytes. A recorded gap with no seq committed does not fabricate one; L=0,B>0 is possible. The existing already-recorded-gap special case remains necessary during replay realignment. Do not claim arbitrary evidence fits A by changing it.

All event keys use ASCII decimal integers without signs, separators or leading zeros. `:` is safe as a delimiter because ID forbids it. Keys are strings of at most 256 characters, not `common.id` (a composite key may exceed 128 characters). The key's exact derivation must be validated against the event fields. No UUID, random id, hash algorithm choice or new durable index is involved.

## 5. Audit integrity events — specification

### 5.1 Schema and registry owners; rejected alternative

Add root `oneOf` reference `#/$defs/recording_integrity_event` to `audit-event.schema.json`. Add exactly one V5 action **`recording.output_incomplete`**. The event may record the settlement of an earlier incomplete observation; the action does not assert every variant is a new loss. It is an integrity class linked to a session, never a second `session.disconnect`. Existing actions keep their meanings.

`recording_integrity_event` is a closed object with required `ts,action,session_id,session_type,detail`; `recording_path` is optional at the envelope level and constrained by the detail variant below. Fields:

| Field | Type/range | Meaning |
|---|---|---|
| `ts` | TS | Audit append time, core UTC clock, as existing audit events; excluded from dedup identity. |
| `action` | constant `recording.output_incomplete` | The one new registered action. |
| `session_id` | ID | Original globally identified session. |
| `session_type` | existing `common.session_type` | Original type, not inferred as shell. An unknown future type is opaque on receipt and never silently recast. |
| `recording_path` | existing `session_end.properties.recording_path` shape: nonempty string | Required for `extent`, `unconfirmed`, `sealed`, `continuation` and `continuation_unconfirmed`; forbidden for `not_recorded` and `unrecorded`. Continuation facts name the pair they describe; all other facts name the original generation-0 recording reference. |
| `detail` | `#/$defs/recording_integrity_detail` | One of the seven fully specified variants below. |

No `user`, `actor`, end state, end reason, payload or credential. The core observes integrity without impersonating the user who originally ended the session. Add `$defs/recording_integrity_detail` as a `oneOf` of `recording_integrity_extent`, `recording_integrity_unconfirmed`, `recording_integrity_sealed`, `recording_integrity_not_recorded`, `recording_integrity_unrecorded`, `recording_integrity_continuation`, `recording_integrity_continuation_unconfirmed`. Each branch is a complete closed object: do not compose `additionalProperties:false` in a way that rejects a sibling branch's fields.

Choose one action and a discriminated `detail` union because existing `config_event` and `alert_event` keep structured system facts in closed detail objects, and session identity/reference belong on the existing audit envelope. The rejected alternative is separate actions `recording.extent_accounted`, `recording.output_incomplete`, `recording.output_sealed`, `recording.not_recorded`: it multiplies V5 entries and handlers for one integrity ledger without adding authority or improving the boundary identity. The variant union is closed and as explicit as separate actions.

Every detail has required `version` integer constant 1, `kind` its branch constant, and `key` the derived string (ASCII, maximum 256). Unknown action follows V5; unknown kind/version for this known relevant action withholds terminal disposal. Do not map it to EOF, zero loss or `not_recorded`.

### 5.2 Validated contiguous extent

`recording_integrity_extent`: in addition to `version,kind,key`, requires **all** fields in this table; kind constant `extent`.

| Field | Type/range | Meaning |
|---|---|---|
| `from_seq`, `through_seq` | U | Lower exclusive and upper inclusive assigned-output seq boundaries. `through_seq > from_seq`. |
| `from_byte`, `through_byte` | U | Cumulative assigned payload-byte boundaries, lower exclusive/upper inclusive; `through_byte > from_byte`. Lost bytes exactly `through_byte-from_byte`. |
| `from_out_ts` | U or null | Worker timestamp at lower boundary if known; null when no such output timestamp is available. Not the core's creation clock. |
| `through_out_ts` | P | Worker timestamp of upper assigned-output boundary. |

Key: `ri1:<session_id>:e:<from_seq>:<through_seq>:<from_byte>:<through_byte>`.

This variant states those read/assigned bytes are absent from the immutable recording and durably declared unavailable. The lower boundary must equal A or the end of already validated contiguous coverage above A; validate order, continuity, uniqueness and byte totals against replay and the immutable seal. The scanner must not treat an arbitrary schema-valid interval as truth. A gap frame is metadata, not payload; apply the published already-recorded-gap realignment rule before deriving an interval. Do not create a positive-byte extent for a metadata-only gap, nor invent a seq for a gap whose seq is not yet established. If matching seq/byte boundaries cannot be established, stop with unconfirmed accounting and withhold disposal rather than splitting by guess.

A known `from_out_ts` is an observed worker boundary, not the loss of its boundary frame; equality of timestamp endpoints is valid. When both endpoints are from the worker stream they must not decrease. No timestamp of an unread suffix appears. Known overflow in a still-writable recording still uses code 2; this audit extent is the declaration for a recording whose end marker is already present.

Example line:

```json
{"ts":"2026-11-15T01:45:00Z","action":"recording.output_incomplete","session_id":"s1","session_type":"shell","recording_path":"recordings/s1.ttyrec","detail":{"version":1,"kind":"extent","key":"ri1:s1:e:42:44:8192:8200","from_seq":42,"through_seq":44,"from_byte":8192,"through_byte":8200,"from_out_ts":1794708000000000000,"through_out_ts":1794708000123000000}}
```

### 5.3 Unconfirmed observation

`recording_integrity_unconfirmed`: common detail fields plus required `baseline_seq` U, `baseline_bytes` U, `reason` closed enum below, `observed_at_ns` P, `unknown_suffix` constant true; kind constant `unconfirmed`. No upper boundary, count or final output timestamp field is allowed. For a drain/stop marker observation, baseline matches the recording walk at that observation, including the preserved code-9 marker. For connection-close or absent-worker recovery, baseline is the last validated DURABLE coverage frontier: the recording baseline advanced only by contiguous durable extent facts (§8). It may exceed the recording baseline; it never becomes a recording commit point. For a recording sync failure (reason `recording_unsynced`, below), baseline is bbolt's commit point P, which may be below A; no other reason has a baseline below A. `observed_at_ns` is the core observation time; where code 9 exists it is exactly that marker's T.

Reasons: `core_wait_timeout`, `core_stop`, `worker_drain_timeout`, `worker_read_error` (same meanings/precedence as §6), `worker_absent` (retained session disposed with its connection, or an already-ended artifact lacking terminal proof whose worker/session is unavailable), and `recording_unsynced` (a recording sync failure quarantined the pair, below). `worker_absent` and `recording_unsynced` are audit-only: neither expands the code-9 grammar. `worker_absent` is used for the revised O5 connection-close branch even when a non-durable seal was received. The notice states only that the suffix after this frontier is unknown, with no upper boundary or count. Key: `ri1:<session_id>:u:<baseline_seq>:<baseline_bytes>`.

For an existing unconfirmed key, retain the first valid durable observation and do not append a new one with a fresh clock/reason on each restart. Subsequent evidence uses `extent`/`sealed`; it does not overwrite that first fact. Two existing lines with the same key but different semantic contents are ambiguous and withhold disposal. Writer checks the full history before constructing a new observation, so ordinary retries never produce that conflict.

**Recording sync failure: durable quarantine** (tsb-program #137, decided (b), 2026-11-19). A writeback error is reported once per open descriptor, so a later process's successful `fsync` on a fresh descriptor does not prove that bytes an earlier process failed to sync reached disk. When the running core observes a failed `fsync` of a session's recording, it appends this variant with reason `recording_unsynced` at P, the recording's last durably committed point from bbolt: the session record's `recording_last_seq_out` and `out_bytes_committed`, which both files hold durably through its `recording_committed_offset` (`continuity-state.schema.json#/$defs/session_record`). P may be below A. The pair is then quarantined, in the running core and in every later process that reads the fact: it is never relied on above P, so no exact fact is derived above P, no release_accounted is sent, and §8's notice stays at P. The quarantine never lowers A silently and never rewrites the recording (§2).

A restart honours a durable `recording_unsynced` fact. For a continuation, the session's active point can later advance in the new pair as specified below; P in the quarantined pair stays frozen. The startup scans the audit file before it realigns any recording (§8), and for a quarantined pair its commit point stays at P: no realign moves that pair above P (core-restart step 17; reattach-handshake "`last_committed_seq`"), and no later successful `fsync`, on a fresh descriptor or in the startup barrier, lifts the quarantine. So that session's hello carries P's seq as `last_committed_seq` and its replay starts at P + 1, where §3.3 otherwise uses the walk's L. The bytes the recording holds above P stay as they are. A quarantined pair is never reopened for writing or cut, including a torn or uncoded suffix; its bytes are neither cut nor relied on for decisions (`ttyrec.schema.json` `x-sidecar` reopen; open-decisions §5, LOCKED). Where durable state proves the session's original end marker is in the durable prefix, replayed bytes are never recorded (§3.3), so the replay from P + 1 only feeds validation, and nothing exact is derived from it above P. For a live session or an ended session without its durable original end marker, a recording continuation follows the ruled recovery below; the quarantined pair is never written or cut and its suffix is not read for any core decision. [owed]

**Recording continuation after sync failure (tsb-program #141).** A recording file or its directory fsync failure quarantines that pair at its last bbolt commit point P. The core MUST serialize the failure, input gate, recording writes and closure of that session's data peer; MUST stop writing the failed pair, suppress its redial, keep the shared control peer and heartbeats, and hold the session until the next core start. Each affected session is held independently; siblings are not ended. Input forwarded before the barrier was recorded before being sent; input admitted but not forwarded is refused, never claimed delivered. The core MUST refuse input with C2 `recording_unavailable`, emit one `data_path` `lost` to attached clients, and raise the per-session `recording_unavailable` alert (operator-alerts; owed). Every attach while held gets its retained-grid prefix followed by one `data_path` `lost`; input is refused. No output is delivered while held. The worker keeps reading into its bounded drop-oldest ring with that data peer closed; U3 must prove this for every supported worker version that can hold a recorded PTY session before it is relied on; any failing version is a STOP for a C3 change (owed).

The durable prefix is the pair's bytes at or below P. While held or quarantined, cadence, size and screen-change triggers, snapshot failure handling, graceful stop and core-restart step 3 take no commit write: bbolt remains at P until active switch, even if a later sync succeeds (F6; grid-snapshot rules 1–3, 9 and 13; owed). At startup `recording_held` itself forbids realignment above P, and that forbiddance binds the quarantined pair; a linked successor that was never quarantined is walked as any recording (the end-placement rule below). If its `recording_unsynced` notice is missing, append it at P before allocation; if its key already holds another reason, hold and report without continuation (owed).

The Linux durability assumptions are successful file and directory fsync and exclusive creation; unsupported directory fsync is a failed barrier. The barriers whose failure quarantines are: (1) a commit point, syncing both pair files before the bbolt write moving it (grid-snapshot rule 7); (2) pair creation, syncing both files and their entries' directory, and for a continuation also its per-session directory and that directory's entry in its parent; (3) an end marker, synced before the bbolt write of `recording_end_placement` `present`; and (4) the startup barrier before the core relies on a recording. Appends between these barriers are not individually synced. A commit barrier on an existing pair syncs only its two files, not its directory. A failed shared-directory fsync quarantines exactly the pairs whose entries were created since that directory's last successful sync. No successful later sync lifts quarantine (owed).

At the next start the core MUST scan and repair audit before realignment, reserve the next continuation generation in bbolt before creation and never reuse it. Generation G's pair is `recordings/<session_id>.chain/continuation-<G>.ttyrec` and `.dir`; use exclusive creation, path containment and length checks. Sync both files and the containing directory, and sync `recordings/` if the session directory is created. The recordings root exists from install; a missing parent halts and is reported. A failed file or relevant directory sync, including a barrier directory step, halts; a partially created unlinked pair is preserved and reported. The core MUST append and fsync the continuation link before switching the active bbolt path or relying on the pair (owed). Link and bbolt are not one atomic write.

After startup audit repair, a complete surviving link validated and synced under §7 is the link; if it is absent after a complete scan, the unlinked pair is an orphan, preserved, reported, never adopted or deleted, and a new generation is reserved. A malformed or conflicting present link halts and is reported, never classified as absent or activated. A generation allocated before a crash is spent even if no file exists. At most one new pair per session is attempted per core start; a successor's sync failure quarantines it at its own P and halts until the next start. Orphans can accumulate across starts; they are logged, with no separate orphan alert (owed).

Under the existing exclusive store ownership, the allocation write atomically stores `recording_pending_continuation` with the reserved generation, predecessor path and four P values, and successor path, and advances `recording_next_generation`. An absent `recording_next_generation` means 1. If the counter cannot advance, halt and report; after a bbolt restore, exclusive creation detects reuse and the core halts and reports rather than overwriting an existing pair. A failure to persist the hold leaves the in-process hold and the accepted missing-evidence residual. The active switch is permitted only when the pending allocation still names the active predecessor and P, the pair has passed its creation barriers, and the matching unique audit link has been validated and fsynced. One bbolt write switches `recording_active_path`, resets the pair-local offset to zero, inherits output counters, sets the input base to `input_seq_reserved_through`, preserves the aligned snapshot, and clears the pending allocation and `recording_held` for a live session. That same write sets the session record's `activity_at` to the switch time, so `idle` re-armed from the switch survives a crash after it (session-countdown's hold paragraph; core-restart step 24 re-arms from the stored clock). [owed] The quarantine-hold write sets `recording_held`; for a session that ended while held it remains set until the successor end marker has synced, then is cleared. A crash before this switch reconciles a complete durable link against pending state; a malformed or conflicting link holds. `recording_end_placement` keeps its per-pair entries across this switch. [owed]

The successor starts empty at pair-local offset zero. It inherits the last committed session-wide output seq and bytes across the chain; on the active switch its input base is the durable input-seq reservation. The core MUST use a fresh hello at P and record worker replay from P + 1. If first_buffered_seq exceeds P + 1, the first successor frame is the existing counted code-2 gap, computed from first_buffered_offset minus P's committed output bytes; its existing snapshot/stale or record-only hold lands before later replay. If a successor fails before that gap commits, the next successor inherits the last committed session-wide counters and rederives the gap from its new hello; a suffix gap is not counted. Replay_done and the corresponding data barrier precede live (owed).

The old suffix remains physically in place as recorded, durability-unconfirmed evidence; it may be displayed by a chain reader but never used by the core for a walk, comparison, clock, end or replay decision. A chain reader requires complete audit history with each link and the predecessor's corresponding quarantine notice. A missing, malformed or conflicting fact is indeterminate. Output comparison counts the effective chain, excluding suffix output; unconfirmed suffix input is never treated as equal or absent. Over a chain with a quarantined predecessor, input comparison follows §6's by-case rule; never `equal` when either side has a gap.

While held no frame or end marker is written to either pair. The end and `session.disconnect` are durably recorded at the end, with the original generation-0 `recording_path`, before a marker exists. If replay is gone because the worker died or the connection closed before next start, the successor contains only the sole end marker and the existing notice at P declares the unknown remainder. Every end marker sync is followed by the bbolt write of its `recording_end_placement` `present` and marker end offset (owed).

A closed per-pair end-placement state is `none` when recorded in the quarantine-hold write for a session live at failure, or `present` with the marker end offset, written after its sync. A hold that starts inside an end — the end's own commit or end-marker sync, the code-9 boundary commit, the recovery's reopen before an unwritten end, the absent-end finalization's writes, or a successor that fails while it carries an ended held session — follows §9 C.3's hold sentence: `session.disconnect` is durable at once, and the hold write records placement `none` for that pair, as a live session's hold does. [owed] A record whose active pair is quarantined and lacks that state is held and reported. Where durable state proves an end and its marker is in the durable prefix, no continuation is created and replay validates only. Where the session ends while held, or durable state proves its original end is absent from the prefix, the continuation receives replay and the sole effective original end marker; no second lifecycle event is written. No end decision uses a quarantined pair's suffix (owed). An active successor that was never quarantined, whose end placement a crash left unwritten, is walked forward from its commit point as any recording (core-restart steps 16–17), and its end written `present` at the walk's marker. [owed]

Every recorded session gets an input-seq reservation at creation, regardless of worker version. On the first start of this release, an existing unquarantined live session gets its first reservation from the ordinary walk-derived floor before any new input. The core durably reserves an input-seq high-water before assigning a number above it; a failed reservation write or overflow refuses input before assignment or forwarding. It refuses with C2 `recording_unavailable`, the release's recording-side refusal. [owed] On a continuation from a quarantined pair, the active switch sets the empty successor's input base to the reservation, so its walk adds to that base; an unquarantined pair keeps today's walk. Without a durable reservation for a quarantined pair, hold and report; never inspect its suffix for a floor. The positive `recording.input_seq_reservation_size` knob has its deployment default in consumer templates. Activation requires this reservation mechanism; the stated production rollout offers 0.7 until activation and has not run capable 0.8 sessions before it. This is a deployment premise, not proof about another deployment (owed).

The recording files are made durable before the bbolt write advancing the four-number commit point and aligned snapshot (grid-snapshot rule 7). Failed sync cannot advance the point, and quarantine never rolls bbolt back. A later successful sync does not lift quarantine. A session with a quarantined predecessor is never sent release_accounted until a separately specified accounting design (owed).

Residual, accepted: if the audit append fails too, only the in-process rule protects the pair (§9 C.3). When the key at P already holds a line with another reason, the first observation stands; a persisted hold prevents continuation and is reported [C6/terminal-recording-sync-quarantine].

Example:

```json
{"ts":"2026-11-15T01:45:00Z","action":"recording.output_incomplete","session_id":"s1","session_type":"shell","recording_path":"recordings/s1.ttyrec","detail":{"version":1,"kind":"unconfirmed","key":"ri1:s1:u:42:8192","baseline_seq":42,"baseline_bytes":8192,"reason":"core_wait_timeout","observed_at_ns":1794708000123456789,"unknown_suffix":true}}
```

### Continuation link and continuation quarantine

The closed `recording_integrity_continuation` detail in `audit-event.schema.json` names the predecessor pair and its durable seq, byte count and pair-local offset at P, the successor path and its positive generation. Its key is `ri1:<session_id>:c:<generation>`. The event's recording_path is the predecessor path. A complete link is durable before the active bbolt switch; the key, paths, allocation, P, chain and pair identities are validated before use (owed).

A quarantined continuation uses the closed `recording_integrity_continuation_unconfirmed` detail, kind `continuation_unconfirmed`, key `ri1:<session_id>:q:<generation>`, reason `recording_unsynced`, baseline seq, bytes and pair-local offset at P, observation time and unknown_suffix true. Its recording_path names the continuation; its generation and path must match the incoming link. A retry reuses its first valid durable fact; conflicting content under the key is ambiguous. Generation 0 retains `recording_integrity_unconfirmed` and its existing key and shape, including the first-observation rule (owed).

### 5.4 Sealed result, including continuing unknown suffix

`recording_integrity_sealed`: common detail fields plus required `final_last_seq` U, `final_out_bytes` U, `final_last_out_ts` U, `read_outcome` enum `eof|timeout|read_error`, `observed_at_ns` P, `unknown_suffix` boolean; kind constant `sealed`. Key: `ri1:<session_id>:s`.

Counters are exactly the immutable worker seal (all three zero or all positive). Observation time is the worker seal's stable observation, not the audit append clock. `unknown_suffix=false` iff outcome `eof`; true for `timeout`/`read_error`. No numeric unread-byte count or unread-output timestamp is permitted. The sealed result must agree with the replay snapshot and the retained immutable result. Existing identical result deduplicates; a second different result for this key is a conflict. A sealed timeout/read_error never becomes EOF on another restart.

EOF settles an earlier unconfirmed observation only when all assigned output through the final seq/byte total is recorded or covered by validated contiguous extents. It never settles a `worker_absent` notice, which is final (§8). A timeout/read_error is durable accounting of a **permanent unknown suffix**, not completeness. A sealed result never covers a hole in counted extents by itself.

EOF example:

```json
{"ts":"2026-11-15T01:45:01Z","action":"recording.output_incomplete","session_id":"s1","session_type":"shell","recording_path":"recordings/s1.ttyrec","detail":{"version":1,"kind":"sealed","key":"ri1:s1:s","final_last_seq":44,"final_out_bytes":8200,"final_last_out_ts":1794708000123000000,"read_outcome":"eof","observed_at_ns":1794708000123456789,"unknown_suffix":false}}
```

Continuing-unknown example, an alternative run/session rather than a second conflicting seal for s1:

```json
{"ts":"2026-11-15T01:45:01Z","action":"recording.output_incomplete","session_id":"s2","session_type":"shell","recording_path":"recordings/s2.ttyrec","detail":{"version":1,"kind":"sealed","key":"ri1:s2:s","final_last_seq":42,"final_out_bytes":8192,"final_last_out_ts":1794708000123000000,"read_outcome":"timeout","observed_at_ns":1794708000123456789,"unknown_suffix":true}}
```

### 5.5 Truly unrecorded session

`recording_integrity_not_recorded`: common detail fields plus required `recording_enabled` constant false, and the same five result fields as `sealed` (`final_last_seq,final_out_bytes,final_last_out_ts,read_outcome,observed_at_ns`) and `unknown_suffix` with the same invariants. Kind constant `not_recorded`; key `ri1:<session_id>:n`. No recording_path, baseline or lost-recording extent is allowed. Core must establish that this session was entirely unrecorded, not just that the current global knob is off. Partial/failed recording is never this branch. This implements ruled O6 without letting stale L fabricate missing recording bytes.

Example:

```json
{"ts":"2026-11-15T01:45:01Z","action":"recording.output_incomplete","session_id":"s3","session_type":"shell","detail":{"version":1,"kind":"not_recorded","key":"ri1:s3:n","recording_enabled":false,"final_last_seq":0,"final_out_bytes":0,"final_last_out_ts":0,"read_outcome":"timeout","observed_at_ns":1794708000123456789,"unknown_suffix":true}}
```

For a disappeared unsealed unrecorded session, do not invent a sealed `not_recorded` result. Keep the original durable session's recording-disabled context and unresolved uncertainty, and report that its final reader outcome is unknown. Write the unrecorded variant in §5.7 for this unrecorded case. No new command/disposal is possible for an absent session; no event falsely asserts a final read outcome.

### 5.6 Append, conflict and coverage procedure

After startup repair (§7), scan the single audit file completely before new integrity appends. Use session id, type and original recording reference for grouping, then the derived detail key for dedup. Compare semantic contents of duplicate keys (everything except top-level append `ts`, with JSON object key order ignored); conflicting facts stop terminal disposal. Reject holes, partial overlap, arithmetic overflow, inconsistent counters, malformed relevant lines and unknown integrity variants. Build coverage by boundary equality, never max `through_seq`. Multiple workers/restarts do not mint fresh identities. Audit coverage never advances the recording's `last_committed_seq`.

When the sealed total has an uncovered tail, create one extent from the validated covered frontier to that final boundary once its exact counters/timestamps are proved. Fsync extents first, then the seal/not-recorded result before release. A crash between them leaves extents without authorization to release; restart reuses them and writes the missing seal. If earlier coverage exists, validate it before deriving the remaining suffix so interval partitioning stays stable. A crash after fsync before release adds no duplicate. A complete LF-terminated line found after an earlier fsync error is revalidated and the file fsynced successfully before authorizing release. No buffered append is durability.

Connection closure before complete durable seal accounting uses §8: preserve validated durable extent progress, declare the remaining suffix unconfirmed and send no terminal command to the closed connection. Do not derive new exact extents from vanished rings or volatile pre-close counters. The core-authorized release path remains seal + complete replay barriers + durable extent/outcome accounting.

### 5.7 Entirely unrecorded close-before-accounting

`recording_integrity_unrecorded` is a complete closed detail branch with all of these fields required:
`version` integer constant 1, `kind` constant `unrecorded`, `key` string max 256 exactly
`ri1:<session_id>:u:null:null` (the unconfirmed key's own form with this branch's null baselines),
`recording_enabled` constant false, `baseline_seq` null,
`baseline_bytes` null, `reason` the §5.3 closed reason enum without `recording_unsynced` (there is no
recording pair to quarantine), `observed_at_ns` positive u64,
`unknown_suffix` constant true. No final counters or outcome are allowed. The envelope has no
recording_path: §5.1's path requirement does not apply to this kind. The core establishes the original
per-session recording-disabled context, not merely the current global knob. Closure uses reason
worker_absent. The key carries no reason, so the first valid durable unrecorded fact is reused whatever
the later reason, as §5.3 says for unconfirmed; no fresh observation is appended to repeat it. A fabricated path or zero numeric baseline is refused by the closed schema
[C6/recording-integrity-unrecorded; C6/terminal-unrecorded-and-partial, U2/U5 tests owed].

```json
{"ts":"2026-11-15T02:15:00Z","action":"recording.output_incomplete","session_id":"s4","session_type":"shell","detail":{"version":1,"kind":"unrecorded","key":"ri1:s4:u:null:null","recording_enabled":false,"baseline_seq":null,"baseline_bytes":null,"reason":"worker_absent","observed_at_ns":1794708000123456789,"unknown_suffix":true}}
```

## 6. Code-9 marker — exact tag, clock, comparison and length

For a continuation chain, comparison linearizes trusted prefixes and successors and applies the existing output payload and gap/code-9 line rules. Raw suffix evidence is not compared as output. Input is never replayed, so input sent between a quarantined pair's P and the switch exists only in that pair's unconfirmed suffix, and a chain lacking it proves nothing (§5.3: never treated as equal or absent). Over a chain with a quarantined predecessor its trusted input is a list of segments in order: each pair's input at or below its P (for a quarantined pair) or whole (for an unquarantined last pair), the continuation's from its switch; a chain whose last pair is quarantined with no continuation (a held session) ends in a trailing gap, because its input after P is unknown. Input comparison, by case: when neither side has a quarantine gap it is `equal` if the input payloads are identical and `different` otherwise; when exactly one side has gaps its trusted segments are placed in order in the other side's complete input (the first a prefix, the last a suffix only when it has no trailing gap, the middle ones in order: greedy leftmost placement decides it), `different` when they cannot be, else `unconfirmed`; when both sides have gaps the result is `different` only when their first segments disagree as prefixes (neither is a prefix of the other), or when neither side has a trailing gap and their last segments disagree as suffixes (neither is a suffix of the other); otherwise `unconfirmed`, because every middle segment and any segment before a trailing gap can sit in a gap. Never `equal` when either side has a gap; equal is possible only for single unquarantined pairs. Payloads compare concatenated, as `payload_identical.in` does. Export includes linked facts. The caller supplies complete audit history: a missing link or predecessor quarantine notice is indeterminate (§5.3; owed).

Reserve next free sidecar code 9 in the release that adds terminal output accounting. `x-sidecar.values["9"]` is exactly **`terminal_incomplete`**. Root `oneOf` adds `$defs/terminal_incomplete_marker`; decoded frame property `marker` is constant **`terminal_incomplete`**, never `terminal_incomplete_marker`. The definition is a closed object requiring exactly `sec,usec,length,marker,data_hex`. `sec`/`usec` reuse existing definitions (u32 seconds; microseconds 0..999999); `length` is integer **100..167**; `data_hex` is lowercase, even-length hex of the exact ASCII payload below. Its schema envelope pattern is `^0d0a([0-9a-f]{2})+0d0a$`; the codec validates the full decoded grammar, ranges and equality `length=len(decoded data_hex)` as it already does for other variable marker lines. No arbitrary bytes matching only the envelope are accepted.

```text
CR LF [TSB: output incomplete after seq <L> bytes <B>; reason <R>; observed_ns <T>; remaining unknown] CR LF
```

Spacing/punctuation are literal; CR/LF are bytes 0D/0A. L/B are U, canonical decimal; T is integer 1..4294967295999999999, canonical decimal. R is the closed enum `core_wait_timeout|core_stop|worker_drain_timeout|worker_read_error`. Unknown value/malformed grammar means invalid/unsupported evidence, never zero remainder or complete. L/B use §4's recording baseline. “remaining unknown” states neither the missing count nor its output timestamp is known; it does not assert positive or zero loss. No upper boundary is encoded. T is the core's observation; header `sec=T//1000000000,usec=(T%1000000000)//1000`. No end time changes to accommodate it.

Choose reason at marker write: core_stop if stopping; otherwise a validated sealed timeout/read_error chooses its matching worker reason; otherwise core_wait_timeout. Clean EOF plus complete coverage emits no marker. Completed end marker already present means no code 9 can be added. Preserve/reuse a complete code-9 frame after a crash; never backpatch its reason/baseline/time.

Exact example (escaped CR/LF for display only):

```text
\r\n[TSB: output incomplete after seq 42 bytes 8192; reason core_wait_timeout; observed_ns 1794708000123456789; remaining unknown]\r\n
```

Frame stamp: sec 1794708000, usec 123456. The length extrema are 100 (one-digit L/B/T, core_stop) and 167 (20-digit L/B, 19-digit T, worker_drain_timeout); these are the exact grammar bounds. A schema-valid length does not replace full codec validation.

**Monotonic mapping: code 9 maps to `clock=None`**, like end markers, in `recording_stamp_problems`. It is not appended to either clock accumulator; it neither checks nor resets the last `in`/`out` stamp. It is a separate core observation boundary, not typed input or worker output. This explicitly exempts only the new code; existing data/marker rules are unchanged. Rejected alternative: mapping it to `in` would assert cross-observer continuity this recovery observation does not establish.

**Comparison mapping: code 9 belongs to `payload_identical.out`**. That boolean is existing output payload equality AND existing gap-line equality AND code-9 line-list equality. `payload_identical.in` is unchanged. No code-9 bytes are concatenated into either payload. A missing/different code-9 line makes output unequal even with equal terminal bytes. This semantic comparison category is independent of the monotonic clock exemption. A matched historical marker does not certify completeness; comparison/export includes the linked integrity audit facts.

The marker is written ttyrec-first/sidecar-second before the sole original end; contributes no output seq/count, never replaces counted code 2, and never follows a complete end marker. Older TSB readers refuse unknown code 9 under the existing rule. Paired-file crash reopen removes only incomplete/uncoded tail; complete code 9 is preserved. Post-marker/pre-end recovery writes only the original missing end; post-end/pre-audit recovery writes only the missing original lifecycle audit and needed integrity facts. No truncation of a complete recording.

## 7. Single audit file and startup tail repair

### 7.1 Current scope

Scan only configured **`audit_path`**, from its beginning, retaining all its history with no file/day window and no separate index. Do not glob rotated names, open compressed segments, add rotation locks/reopen signals or require rotation fixtures. Original end, older extents and newer results can span any number of restarts in that one file. A later rotation change must keep every segment terminal accounting still needs.

An unreadable/missing expected audit file, malformed LF-terminated record, conflicting relevant event, missing necessary lifecycle/coverage proof or unknown integrity variant leaves terminal accounting uncertain: no core-authorized release and no new exact claim, no second end. Existing `alert_kind` includes supervisor_unresponsive, worker_unkillable, vt_unavailable and the per-session recording_unavailable; none represents a general audit recovery error. Log the recovery error through the existing core log route; do not emit an invented/misclassified alert. An absent file is created only for a genuinely new audit store under existing setup; it cannot erase required history for existing sessions by being recreated and treated as empty proof.

### 7.2 Flagged operator DEFAULT: repair only unterminated final suffix

**FLAGGED FOR THE OPERATOR:** adopt the ruled F3(a) exception before exposing the audit writer at core startup. It applies to this JSONL artifact, not permission to cut a complete recording or complete audit line.

Order:

1. Obtain the existing exclusive core/store ownership used by startup before audit recovery. No audit producer, startup recording-state event, HTTP request, session reconcile, alert reconstruction or host recovery writer may append concurrently. The shared audit initialization entry point must perform repair before returning a usable `Log`; do not repair independently in a caller after `audit.Open` has exposed it. Host administration invoking the same audit writer must respect the same exclusive store ownership; a concurrent owner refuses startup rather than racing a cut.
2. Validate existing audit file regularity/ownership/mode using the existing audit safety checks. Open for read/write without truncation or append exposure. Inspect its final byte. Empty file or last byte LF: no cut, regardless of whether a complete line parses.
3. If nonempty and final byte is not LF, find the last LF byte. Truncate to its following offset, or offset zero if no LF exists. This cuts exactly one unterminated suffix, even if it happens to contain syntactically valid JSON. Never discard an LF-terminated parse failure. Fsync the file before any scan or append is permitted. Log that recovery discarded an incomplete append; report the byte count, not its potentially sensitive contents.
4. If inspect/truncate/fsync fails, fail audit initialization/startup and expose no writer or disposal authorization. If no cut is needed, proceed with normal writer opening. Keep the validated same file identity through repair/open, rather than repair one path then append to a replaced inode.
5. Only after successful repair/durability may the writer be shared. Perform the strict terminal coverage scan under its existing append mutex so the captured scan boundary cannot split a concurrent append; release after obtaining a consistent complete-line snapshot. Then normal append/reconcile operations may proceed. During a running core, do not invoke startup repair against a writer's in-flight line; wait under the append mutex or fail the attempt. An LF-terminated malformed line remains untouched and uncertain on every restart.

Crash before cut repeats startup inspection; crash after truncate/before fsync repeats inspection and requires successful sync before authorization; crash after sync/before new append scans the preserved prefix and reconstructs the missing fact by deterministic key. A torn append never authorized disposal, so retry does not lose durable accounting. Complete malformed middle/final lines are preserved, not “repaired” by this rule. The audit sink's existing append-error rollback behavior is not broadened into historical-line deletion.

## 8. Connection-close branch — revised O5/E1, flagged for the operator

For a session with a quarantined predecessor, continuation does not authorize release_accounted; the existing connection closure and unknown notice rules still apply (§5.3; owed).

There is **no terminal-output evidence file**. The removed component would have preserved final assigned-output counters after a connection disappeared before the core accounted its seal, allowing more precise loss declarations in that race. That precision is deliberately forgone: the remainder after the last validated durable coverage is declared **unknown**, with no invented final seq, byte count, loss timestamp or zero-loss claim. The worker writes nothing new to disk for this branch.

A retained session never holds a connection or target login beyond connection_linger. Linger starts when the last live session ends; recovery hello, replay, sealing and integrity audit writes never reset it. A sibling live session naturally holds its connection. When linger fires, connection_lost occurs, or the worker dies, retained resources disappear with that connection under the published release/linger rules. There is no core release precondition on that teardown, no ack and no added retained-session TTL. A worker with no connections exits; one with another connection does not [C3/terminal-linger-not-reset; C3/terminal-worker-exits-last-connection].

For each already-ended retained session affected by closure (including sessions omitted from connection_lost.session_ids, as linger requires), core looks at its durable recording and integrity history. Let C be A advanced only by validated contiguous **durable** extent facts; for a pair quarantined by a recording sync failure (§5.3), C is P; generation 0 reuses the quarantine key at P, while a continuation uses its `q:<generation>` notice. If a complete seal plus all coverage was durably accounted before closure, preserve that fact and add no new uncertainty or end. Otherwise write the existing §5.3 unconfirmed variant with baseline_seq=C.seq, baseline_bytes=C.bytes, reason=worker_absent, current core observation time and unknown_suffix=true. Do not take a received-but-not-durable result, a vanished ring/manifest number, or an empty list as an exact suffix boundary. An existing unconfirmed key at C is reused; no fresh duplicate per restart. The audit's top-level ts is when it is written; neither stamp replaces ended_at. A truly unrecorded session uses §5.7; no missing recording-byte extent is fabricated.

The new notice covers everything after C, not merely the last saved recording seq. Previously durable exact extents remain exact and are not repeated; all later unaccounted output is unknown. A historical unconfirmed notice at an earlier frontier does not forbid a new one at a later validated C when exact progress was durably added in between, unless that notice is `worker_absent`, which is final (below). Unknown is not zero, and an audit-derived C never becomes a ttyrec commit point. If the core cannot validate prior coverage, retain/report uncertainty and do not guess a new frontier from the greatest seq. No terminal command is sent to a closed connection and no synthetic E10 is written for an already-ended session.

**A `worker_absent` notice is final** (tsb-program #139, decided (a), 2026-11-19). An exact-path fact (`extent`, `sealed`, `not_recorded`) whose write or fsync is in progress when the session's connection closes is not durable before closure: the notice is `worker_absent` at F, the frontier durable before closure, and a later process cannot tell the late line from one durable before closure. A `worker_absent` notice at F (for an entirely unrecorded session, the §5.7 notice) is final for the session's accounting: no `sealed` or `not_recorded` fact, and no `extent` whose lower boundary is at or above F, counts toward coverage or completeness, wherever it lies in the file. Such lines stay valid-shaped history with their own keys, and are reported as post-closure and non-authoritative. This qualifies §5.4's settling and the later-C clause above for `worker_absent`. It is consistent: a `worker_absent` notice is written only after the resources are gone, so no legitimate exact progress can follow it, and a `sealed` fact durable before closure implies complete coverage, in which case no notice is written. Accepted limit: if a crash prevents the notice, the late line alone reads as durable before closure. Accepted, of the same class: when the key at F already holds an unconfirmed line with another reason, that line is reused, no `worker_absent` line is written, so the freeze is not written down and a later process does not see it [C6/terminal-close-late-exact-fact].

If the core is down when closure occurs, the next startup performs audit-tail repair, scans its one audit file, realigns the recording (never above P for a pair quarantined by a recording sync failure, §5.3) and uses the persisted ended-session/connection record plus existing manifest/session absence rules to identify unresolved retained terminal work. Do this before dropping the old record's retained-session expectation. If durable closure bookkeeping is missing after a crash, rediscovery is the check; a worker still holding the session follows §9 A/B instead. No new worker disk write, background publisher, orphan janitor or late-publication protocol exists. The core's ordinary ended records and recording/audit progress remain recovery anchors; don't discard the only anchor before its unconfirmed append is durable [C6/terminal-close-core-restart; C6/terminal-close-audit-write-failed].

### Failure cuts and their required outcomes

| Cut | Required outcome |
|---|---|
| Close before worker produces/sends seal | Worker drops retained resources with connection, no new disk record; running core writes unconfirmed after C, never guesses final counters. Restart observes absent resources and does the same. |
| Close after result receipt, before any accounting is durable | Receipt is not durable accounting; remainder after C is unconfirmed. No exact suffix derived from the volatile result after closure; no release sent. Restart with no surviving valid seal facts makes the same unknown declaration. |
| Close while an exact fact's write or fsync is in progress | The fact is not durable before closure; the notice is `worker_absent` at F, the frontier durable before closure. After it the late line never counts, wherever it lies in the file (#139). A crash before the notice leaves the late line alone, read as durable before closure: an accepted limit. |
| Close after some exact extents fsynced but before complete seal accounting | Preserve the contiguous durable extent prefix; unconfirmed begins at its frontier, with no repeated loss extent. Crash/restart reconstructs C, not a max-only skip. |
| Close after complete accounting fsynced, before release send/application | Accounting stands; closure already disposed resources; no command, new unknown event or second end. Restart reuses durable seal/coverage. |
| Core crash during unconfirmed append | At restart cut only final non-LF suffix (§7); complete valid line deduplicates, LF-complete malformed line remains uncertain. A retry revalidates/syncs surviving full facts before relying on them. |
| Core crash after notice fsync before connection bookkeeping | Rediscovery observes absence; same key adds no event and never resurrects session/live/grid/token. |
| Audit append/sync unavailable while connection expires | Connection still closes at its original deadline; core reports failed accounting, preserves ordinary recovery anchors and retries next rediscovery. Never report accounting success from a buffered append. |
| Complete valid audit lines survive a crash before the previous process confirmed sync | Revalidate and successfully fsync before using them. They can preserve actual observed facts; never infer a seal that is absent, complete missing coverage from its totals alone, or claim it had been durably acknowledged at close. |

## 9. Full recovery flows, including timeout, stop and compatibility

### A. Audited end, channel already closed at worker

1. Recognize audited end before spool/E10/live branches. Delete its already-audited lifecycle spool entry unread under existing precedence; no output assertion comes from that deleted entry. Keep session out of active fabric/attach/timers/tokens/grids.
2. Dial retained data socket before hello; send capable timers then first ping before hello, using existing control setup order. No connect or credential exchange. Data peer is new under existing handshake rules.
3. hello_ack ended:true means never close_channel. If no validated sealed summary, send seal_output; it ends the paused handshake, closes old delivery and finishes bounded terminal sealing. If already sealed, no redundant sealing operation is needed.
4. After output_sealed, fresh data dial and hello. Verify stable seal and snapshot, replay from recording L+1, wait for both replay barriers, never live. Repeated seal/hello cannot change final counters.
5. Account exact known tail and final outcome under §§3–5; unknown suffix explicitly persists even for empty ring and N=L. Only then release_accounted. Original recording/sidecar/end/audit unchanged. If connection closes at any step, §8 replaces the remaining exchange.

### B. Audited end, worker channel still open

1. Same precedence/setup; hello_ack ended:false triggers immediate close_channel for this session, no replay/live. The existing pause-abandonment path permits reads into ring.
2. Worker closes old data peer before channel_closed by:core. That event proves closure, not clean EOF; no second core lifecycle end.
3. Once ended, seal_output needs no intermediate hello; it returns the existing completed immutable outcome or finishes bounded sealing, then fresh peer/hello/replay/account/release as A.
4. Target close racing close_channel can return bad_request; it is not universal success. Fresh dial/hello discovers ended state; terminal_busy while core end is in flight waits for channel_closed, then fresh peer. Missing barriers remain uncertain. Connection loss at any point invokes §8. Never let an empty ring substitute for driver-queue/in-flight-read proof.

### C. Running core's already-decided end hits its drain deadline

1. Persist first end/reason as existing session-end flow; request channel close, consume/record valid output during configured wait. Data EOF, channel_closed and terminal read outcome are independent facts.
2. At expiry stop/join core data consumer, don't claim clean EOF or release. Running core may perform bounded hello/close-if-open/seal/replay as A/B within the existing configured per-operation waits. At a simultaneous deadline/result tie, timeout outcome enters uncertainty recovery; it cannot shortcut validation/barriers.
3. Outside a recording hold, if still incomplete when local finalization returns, append code 9 before sole original end marker, then original lifecycle audit and durable unconfirmed notice. During a recording hold persist the end and lifecycle audit at once, write neither code 9 nor end marker to either pair, and follow §5.3 for the sole successor end marker. Known counted overflow uses code 2 first. On write/sync failure preserve pending ended record and report failure; no complete claim/release. A failed recording fsync also appends the §5.3 `recording_unsynced` quarantine at P.
4. Later recovery uses audit facts after an already written end, never reopens it. A timed-out/read-error seal is immutable unknown suffix; EOF settles only with complete contiguous assigned-output accounting. Connection disposal before then follows §8, not an infinite recovery hold.

### D. Core shutdown interrupts an end drain

1. Fabric.Close sends a still-live session no close/disconnect/seal/release; snapshot/commit and drop peers under core-restart, leaving it live at worker.
2. For a first end already requested, shutdown cannot manufacture EOF/control completion. Join local readers, distinguish stop interruption, add code 9 before an unwritten end when durable local finalization is possible, and add its unconfirmed audit. Existing completed end marker is immutable; only audit facts are appended.
3. No new terminal/timer action admitted after Close begins (#680). Do not send terminal commands after peers close. Failure to persist leaves ended/pending original state for startup repair; no fake completion or release.
4. Worker control loss abandons the hello pause under existing semantics, not because plain hello resumed reads. Worker may retain or dispose with its connection. Startup uses A/B if retained, §8 if gone. Shutdown creates no local background retry after exit.

### E. Older or unavailable worker; original-end repair

- Known pre-0.8 peers (0.4–0.7 included) keep today's audited-end path, including legacy aborting/close_channel behavior and its bad_request limitation; no new timer key, seal, accounted release or compatibility-only empty-replay authority. Versions 0.2/0.3 remain supported; v0.1 gets no hello/timers/ping/release. No forced replacement. A 0.8 worker that meets such a peer while a session is sealing or its core end is in flight answers as §3.1 says (#138).
- Unknown version retains published negotiation behavior; no new terminal message until capability known. The known unreachable-scope conflict is outside this selected path; if an implementation needs to decide it, STOP and report, never recast an audited end as E10.
- Audited end with no_such_session or refused data dial: re-read lifecycle spool and delete audited entry unread, keep original end. Absence is not recording completeness. If the retained resources really disappeared under existing discovery rules before accounting, §8 unconfirmed declaration applies. A mere no_data_peer/refused attempt while connection remains does not prove disposal; leave retained/uncertain for next rediscovery.
- Worker/control dead: no handshake/disposal command; original audited end stays. No stale manifest/counter yields exact final extent. Live unaudited sessions retain existing spool-before-E10 behavior; unrelated dead-worker/spool conflict is not solved by this fix.
- Ended but not audited: use persisted first end. Existing end marker means immutable recording and append only missing original lifecycle event plus integrity facts. No marker yet means replay/declare before original end. A worker-spooled end still follows its existing authoritative stamp/reason. Never replace it with a new end just to enter this recovery path.


## 10. Fixture register and obligations

All fixtures below are published e4, required-green e4 and grade reference. Their named provider
tests are owed in the fixture and fixtures/graded-by.json; self-check validates the declared examples,
not filesystem timing or provider implementation. Every imperative in this protocol is graded by
these examples and the associated owed provider tests.

| Fixture | Assertion / provider-test obligation |
|---|---|
| C3/control-message-shape-v0.8 | U1/U5 `terminal_control_shapes`: new exact messages/summary/error and version metadata, optional old fields still valid. |
| C3/reattach-ended-audited-sealed | U2/U3 `audited_closed_hello_seal_replay`: the handshake for such a session begins with hello, sent after the connection's first ping (core-restart step 22), no close/live, two replay barriers, durable accounting before release; empty ring still needs seal. |
| C3/reattach-ended-audited-open-unread | U2/U3 `audited_open_queued_output`: ended:false then one close; queued/in-flight data included or unknown; sibling survives. |
| C3/terminal-drain-timeout | U2/U3/U11 `terminal_deadline_tie`: configured bound, stop/join and timeout suffix; drive core deadline exactly with worker result, reject equal-counter false completeness. |
| C3/terminal-drain-read-error | U3 `terminal_error_not_eof`: old finally/read_drained cannot map read error to EOF. |
| C3/terminal-seal-idempotent | U3 `seal_stable_across_peers`: duplicate seals coalesce/reuse final result, no new deadline, no output after seal. |
| C3/terminal-hello-busy | U2/U3 `hello_during_seal_or_core_end`: terminal_busy for both cases, only attempted peer closed, fresh peer after proper event barrier. |
| C3/terminal-disposal-version-gate | U2/U3 `terminal_old_peer_unchanged`: old tags receive old messages/keys, live/unsealed new disposal refuses, absent duplicate succeeds without ack. |
| C3/channel-closed-after-data-eof | U3/U2 `channel_event_data_order`: enqueue/close old data before event; core waits for independent data barrier. |
| C3/terminal-close-raced-target | U2/U3 `target_close_race`: bad_request is rediscovery, not blanket release success. |
| C3/terminal-input-refused-mid-seal | U3 `ended_input_during_seal_replay`: input/resize never reaches driver while sealing/replaying ended session, no deferred delivery/live. |
| C3/terminal-linger-not-reset | U3/U11 `terminal_recovery_preserves_linger`: repeated hello/replay/seal never resets original last-live-session linger; sibling live connection unaffected. |
| C3/terminal-worker-exits-last-connection | U3 `terminal_disposal_worker_exit`: no remaining connection permits exit; another connection forbids it. |
| C3/terminal-disposal-crash-after-drop | U2/U11 `release_applied_before_core_persistence`: next manifest/no-such-session observes absence, no second release requirement/end or resurrection. |
| C5/config-output-drain-timeout | U5/U2/U3 `drain_config_exact_ms`: required runtime key, additive schema optionality, exact bounds/conversion/reload, V7 scope exception, old wire excludes key, no schema default. |
| C6/recording-integrity-event-shape | U1/U5 `integrity_event_variants`: the original five variants (extent, unconfirmed, sealed, not_recorded, unrecorded), plus continuation and continuation_unconfirmed, keys/enums/required/forbidden fields; examples valid, unknown fails closed. Includes the ruled unrecorded shape. |
| C6/recording-integrity-exact-once | U2/U11 `integrity_contiguous_identity`: stable extent keys, disjoint boundary coverage, holes/overlap/conflicts/malformed evidence refuse, no max-only scan or second disconnect. |
| C6/recording-integrity-unknown-suffix | U2/U5 `unknown_never_zero_or_eof`: empty/equal counters not proof, timeout stays unknown, EOF settles only complete coverage. |
| C6/terminal-accounting-write-failed | U2 `integrity_append_sync_cuts`: no core release on failure, torn suffix startup rule, valid synced identity reused on retry. |
| C6/terminal-audit-single-file-coverage | U2/U11 `history_three_restarts_one_file`: original end, extents and later seal from three restarts in one audit_path all scanned/deduped. |
| C6/terminal-audit-tail-reopen | U2/U11 `audit_non_lf_tail`: cut only unterminated suffix, including complete JSON without LF; no-LF file cuts to zero; sync before writer exposed. |
| C6/terminal-audit-complete-malformed | U2/U5 `audit_complete_bad_line_preserved`: malformed middle/final LF line never cut, scan uncertain, no release. |
| C6/terminal-audit-repair-order | U2 `audit_exclusive_repair`: existing ownership before repair/writer exposure, inspect/truncate/sync failure stops initialization, crashes retry without cutting full lines. |
| C8/terminal-incomplete-before-end | U2/U11 `marker_original_end`: code 9 before original end, counted gap separate, stamp/reason precedence correct; never after end. |
| C8/terminal-incomplete-reader-refusal | U5/U11 `marker_strict_codec`: exact tag/def/grammar, 100..167 and actual payload length, old reader refuses 9, overflow/unknown reason rejected. |
| C8/terminal-incomplete-payload-identical | U11 `marker_compare_out`: list equality only under out, no payload concatenation, absent/different marker unequal, in unchanged. |
| C8/terminal-incomplete-clock | U5/U11 `marker_clock_none`: code 9 exempt/no accumulator reset, normal decreasing data clock still fails. |
| C8/terminal-incomplete-crash-reopen | U2/U11 `marker_complete_preserved`: paired torn cut only, complete marker/end immutable, original missing audit repaired. |
| C8/audited-end-recording-unchanged | U2/U11 `recording_sidecar_immutable`: full byte identity of ttyrec AND dir through exact-tail/unknown recovery; linked integrity facts accompany export/comparison. |
| C3/core-stop-during-end-drain | U2/U3 `close_admission_and_end_stop`: #680 no new action after Close, interrupted not EOF, no stop seal/release/disconnect, live sibling survives. |
| C6/terminal-unrecorded-and-partial | U2/U5 `unrecorded_not_partial`: disabled gets no fabricated missing extent, partial remains integrity path, original unaudited marker/end repaired. Includes unrecorded null-baseline close notice. |
| C6/terminal-close-before-seal | U2/U3/U11 `close_before_result_unknown`: linger-zero/connection loss/worker death before seal result disposes without wait; running core writes unknown at durable C, no exact or zero suffix/no second end. |
| C6/terminal-close-after-result-before-sync | U2/U11 `volatile_seal_close_unknown`: receive valid result, hold accounting before sync, close connection, then resume core processing; unknown after C, no new exact extent from cached result/no release on closed peer. |
| C6/terminal-close-partial-durable-coverage | U2/U11 `close_after_extent_before_seal`: fsynced exact prefix retained, notice starts at its frontier, not recording A or highest unvalidated seq; no repeated count. |
| C6/terminal-close-core-restart | U2/U11 `close_restart_each_cut`: for each before-result, after-result/pre-sync, partial-sync, full-sync and notice-sync/pre-bookkeeping cut, restart observes absence, reconstructs correct durable facts/unknown, no double event/end or live publication. |
| C6/terminal-close-audit-write-failed | U2/U3/U11 `close_does_not_wait_for_audit`: pause/fail audit append/sync, expire original linger, observe connection closed; later unblock and retry, preserve uncertainty/recovery anchor, never claim completed accounting. No worker disk publication occurs before or after unblock. |
| C6/recording-integrity-unrecorded | U2/U5 integrity_unrecorded_shape: explicit unrecorded notice, null baselines, no path/finality; fabricated path or zero baseline refused. |
| C3/terminal-non-pty-retained | U2/U3/U5 `non_pty_retained_excludes_terminal_accounting`: existing non-PTY handshake, no replayed output or gap, no seal/accounted release and no code-9 marker, including timeout/core stop; provider evidence owed. |
| C6/terminal-recording-sync-quarantine | U2 `recording_sync_failure_quarantine` (#137): a failed recording fsync appends unconfirmed `recording_unsynced` at bbolt's commit point P, below A; no exact fact above P and no release_accounted, in the running core and after a restart; a restart keeps the commit point at P, so its hello carries P's seq, and no realign or later successful fsync lifts the quarantine; the §8 notice reuses the generation-0 key at P or the continuation generation q key; a missing notice is repaired from a durable held state where its key is available. The unrecorded kind refuses the reason. |
| C3/terminal-legacy-offer-mid-seal | U2/U3 `legacy_offer_mid_seal` (#138): a hello offering a version below 0.8 while the session is sealing or its core-requested end is in flight is answered `unsupported_proto` naming the session, never terminal_busy; output_sealed and terminal_busy reach only a peer that stated 0.8 on that control connection by a parsed hello (refused hellos included) or seal_output; after sealing, the ordinary legacy handshake and legacy release at the legacy core's next start; a core at an effective version below 0.8 stays the legacy core. |
| C6/terminal-close-late-exact-fact | U2/U11 `worker_absent_freezes_accounting` (#139): an exact fact whose write or fsync straddles the closure is not durable before closure; notice `worker_absent` at F; in the running core and after a restart no sealed or not_recorded fact and no extent from at or above F counts, wherever it lies; such lines are reported post-closure and non-authoritative; nothing settles the notice and no later C follows it. |
| C3/terminal-seal-output-states-version | U2/U3 `seal_output_states_version`: seal_output without a hello states 0.8 for that control peer and receives output_sealed; a replacement peer with no statement receives neither output_sealed nor terminal_busy. |

**Input sequence jump.** Ordinary restart retains the existing walk-derived input sequence; only a continuation switch uses the reservation as the new input base and permits the resulting jump. Supported workers use the input sequence to echo refusals rather than enforce input order; U3 must prove acceptance of this continuation jump (owed).

**Continuation audit identity.** Group facts by session id and the original generation-0 recording reference. A continuation link and continuation-unconfirmed notice name the pair they describe; all other recording integrity facts, including extent, sealed and §8 notices, and `session.disconnect` name the original pair. For generation 0 §8 reuses the P key; for generation 1 and later it uses `ri1:<session_id>:q:<generation>`. The two continuation details extend the five original variants to seven (owed).

**Continuation fixture register** (published and required-green e4; provider tests owed).

- `C3/terminal-continuation-hold`: recording_continuation_terminal_continuation_hold_u3: held-session ring proof with control and heartbeats connected, recording_continuation_terminal_continuation_hold_u2: suppress redial and isolate the held session.
- `C3/terminal-continuation-input-seq`: recording_continuation_terminal_continuation_input_seq_u3: worker accepts the input sequence jump across a continuation switch, recording_continuation_terminal_continuation_input_seq_u2: reserve the input high-water before use and jump only on continuation switch.
- `C6/terminal-continuation-audit`: recording_continuation_terminal_continuation_audit_u2: validates continuation link and generation-specific quarantine semantics.
- `C6/terminal-continuation-recovery`: recording_continuation_terminal_continuation_recovery_u2: recover generation allocation, link and bbolt crash cuts without adopting an orphan.
- `C6/terminal-continuation-failures`: recording_continuation_terminal_continuation_failures_u2: fault-injection tests for every listed continuation scenario.
- `C8/terminal-continuation-chain`: recording_continuation_terminal_continuation_chain_u2: chained recordings, evidence and comparison through multiple failures, RecordingChainTests::test_chain_reference_cases.
- `C5/config-recording-input-seq-reservation`: recording_input_seq_reservation_config.
- `C2/recording-hold-input-refused`: recording_hold_input_refused.
- `C2/recording-hold-data-path`: recording_hold_data_path.
- `C6/recording-hold-alert`: recording_hold_alert_identity.

- `C8/recording-chain-collection`: `recording-chains-complete`, complete collected generations and linked audit; legacy artefact graders refuse the chain (U1).
