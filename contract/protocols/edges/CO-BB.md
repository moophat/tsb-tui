# Edge CO-BB — core ↔ bbolt

| | |
|---|---|
| **Edge** | CO-BB |
| **Ends** | CO (core, process) and BB (bbolt, store). Reads are `BB → CO`, writes are `CO → BB` |
| **Seam** | C6 (README "Edges") |
| **Status** | built (E0: sessions, users); E1 (connections); E2 (grid snapshots, `stale_seq`); E4 (the vault buckets: `vault_users`, `devices`, `grants`, `org_credentials`) |
| **Transport** | an embedded database, one file (`~/.tsb/tsb.db`, Storage §5); the core is its only user. A step names it "a bbolt write" or "a bbolt read". One value per key, JSON (continuity-state top) |
| **Contract** | `contract/schemas/continuity-state.schema.json` at contract-v0.3 (`session_record`, `connection_record`, `grid_snapshot_record`); from the vault release `contract/schemas/vault.schema.json` (`vault_user`, `device_record`, `grant`, `org_credential`); E4b `contract/schemas/users.schema.json` |

This page is an index. It restates no rule: the rules are in the protocols and in
[`../conventions.md`](../conventions.md).

## Transport rules

Import receipts are secret-free C6 records in `import_receipts`, keyed by import ID. For save, the receipt and sealed credential batch commit together after the credential audit; discard or no-secret records the receipt before target publication. Resume reads the caller-owned receipt without another credential store. See [target-import](../target-import.md).

1. Shapes are closed (`additionalProperties` false on all three records), and a field added in a later tag is optional:
   V2, V1.
2. A record written by an older core is read correctly: an E0 session record loads unchanged, and the optional
   fields (`protocol`, `stale_seq`, `stale_cause`, `end_reason`) may be absent (continuity-state top; V1). Clocks
   written in a local zone are read with their offset (`client_touch_at`, `activity_at`).
3. Nothing in here is restored to clients. `control_holder` reads null after any restart and the attached
   `clients` are never restored from disk (core-restart step 7; typing-ownership step 2; continuity-state `session`). The owner is
   the one stored principal (`session.owner`).
4. No claim token: a local reattach is SO_PEERCRED alone (continuity-state top; core-restart "Security
   considerations"; V10). `snapshot*` fields only inside `grid_snapshot_record` and none named `claim*` anywhere
   (grid-snapshot step 7) [C6/no-claim-token-field].
5. Redis is never authoritative: what bbolt holds is the truth, Redis holds derived state only (continuity-state
   top; Storage §5, §8.6). The runtime registry (clients, worker pid, socket) is in memory and rebuilt on start (core-restart).
6. The commit point is one value, advanced in the write that stores the grid snapshot, or record-only for a session
   with no snapshot, and realigned to the recording's end before every `hello`; the recording is never cut back to it
   (grid-snapshot step 7, rules 7, 10, 11; continuity-state top). Pointer only: the rules are grid-snapshot's.
7. What a record carries on the wire must validate against the schema [C6/continuity-record-valid]. A credential is
   never stored here as itself: `connection_record.credential_ref` is a reference (session-open step 18).
8. From the vault release: the vault's records are not continuity state and nothing in them is restored to a client.
   A secret is stored only sealed (AES-256-GCM, `IV ‖ ciphertext ‖ tag`, associated data per `vault.schema.json`
   `x-associated-data`); master_hash, Part 1, Part 2, `storage_key` and `encryption_key` are never stored here
   (credential §3, §12). The plaintext index is written in the same transaction as the sealed set (DEFAULT 42). Every
   vault, device and grant write carries its audit line inside the same transaction, before the commit (DEFAULT 49).

E4b account writes follow account-administration: audit before commit, no usable hash in disabled records;
pre-E4b records load enabled with hash unchanged [C6/users-record; C6/users-old-record-loads-enabled].

## Records and buckets

`W` = writes it, `R` = reads it, with the step.

| Bucket · record | `$def` | Protocols | Status |
|---|---|---|---|
| `sessions` · session record, key session id | `continuity-state#/$defs/session_record` | W: session-open 18 (create); grid-snapshot 7 (commit point, with the snapshot) and rule 11 (record-only, a session with no snapshot); grid-snapshot 8 and stale-screen 2 (`stale_seq`, `stale_cause`); stale-screen 21 (clear); session-countdown 3 (clocks); session-end-by-tsb 6 (`killed`); session-end-from-outside 11 (`terminated`); spooled-end-finalize 3 (end stamped by the worker); core-restart 17 (the realign: E1 record-only; E2 with step 21's snapshot, or record-only for a session with no snapshot), core-restart 19 (`unreachable`). R: core-restart 7; session-countdown 14; stale-screen 10. Removed by session-list rule 10 (fully finalised ended-record retention) | built (E0); grows E2 (`stale_*`, `out_bytes_committed`) |
| `connections` · connection record, key conn id | `#/$defs/connection_record` | W: session-open 18 (new, or `session_ids` extended on a reuse); connection-linger 10, 17 (`session_ids` while it lingers). R: core-restart 7 | built (E1) |
| `grid_snapshots` · grid snapshot record, key session id, the latest only | `#/$defs/grid_snapshot_record` | W: grid-snapshot 7; stale-screen 2 (a gap: the same write); deleted by grid-snapshot rule 9 (a refused snapshot), grid-restore 14 (`snapshot_incompatible`), and session-list rule 10 (with its retained ended session record). R: grid-restore 6; core-restart 7 | E2 |
| `users` · account, key username | `users.schema.json` | R: login 3, account-administration 2; W: account-administration 4 | E4b [C6/users-record; C6/users-old-record-loads-enabled] |
| `vault_users` · vault record, key user_id: `user_salt`, `salt2`, the sealed set, the plaintext index | `vault#/$defs/vault_user` | W: vault-unlock 1b (the mint: salts only), 5 (init: the sealed set and its index); the personal store and delete (C7); deleted by vault-unlock 11 (wipe). R: vault-unlock 4; device-trust 3; session-open 3 (the index, without an unlock) | E4 [C6/vault-user-record] |
| `devices` · device record, key (user_id, device_id) | `vault#/$defs/device_record` | W: device-trust 4 (register), 10 and 14 (revoke, sweep), the challenge (each verified challenge moves `last_used_at`). R: the challenge's issue checks; device-trust 8 | E4 [C6/device-record] |
| `grants` · grant, key grant_id: user × selectors → an org reference | `vault#/$defs/grant` | W: the grant operations (C7). R: session-open 3 (Layer 2b) | E4 [C6/grant-record] |
| `org_credentials` · org credential, key reference: class, kind, the sealed blob | `vault#/$defs/org_credential` | W: the org store and delete (C7). R: session-open 3, at establishment | E4 [C6/org-credential-record] |

stale-screen 10 (a pending stale choice) and session-countdown 14 (the clocks) are CO-BB reads of the `session_record`
during core-restart, listed as R above; core-restart 25 reaches stale-screen with no CO-BB step of its own.

Two stored values every step above leans on: `recording_last_seq_out` with `recording_committed_offset` (the
commit point) and `snapshot_seq` (equal to the first at every write), then `out_bytes_committed` and
`recording_last_seq_in` for the same point, written with it [C6/out-bytes-committed-after-reopen].

Fixtures that grade this edge: `C6/continuity-record-valid`, `C6/grid-snapshot-record`,
`C6/snapshot-seq-aligned`, `C6/stale-pending-durable`, `C6/no-claim-token-field`,
`C6/out-bytes-committed-after-reopen`, `C1/session-retention-safety`,
`C1/session-get-removed-404` (V8 marks in each protocol say which are owed).

## Gaps

Checked against the planner's draft (plan §3): its sessions row lacked the write of a spooled end (now spooled-end-finalize 3), and its grid snapshots row lacked
stale-screen (a write). Both are added above. Its connections and users rows match.

**No protocol writes or deletes these**
- Connection-record deletion: connection-linger steps 10 and 17 leave what the core writes when a connection
  closes unspecified. Session-record and associated grid-snapshot retention removal is specified by
  [session-list rule 10](../session-list.md): fully finalised ends only, no earlier than the retention age,
  preserving shared connections, sibling sessions, audit and recordings. The rule decides no sweep timing.
- Users bucket: E4b account-administration creates and changes accounts; deletion remains absent by design (names never reused).
- Web session tokens: none — login sessions live in the core's memory (login open item 2, settled).
- `connection_record.proto_version`, `session_record.protocol`, `owner_id`, `tenant_id`, `recording_last_seq_in`:
  session-open step 18 does not name them; no protocol says when they are written (`recording_last_seq_in` is not in any
  protocol document). The contract's rule for `recording_last_seq_in`: the core's own input counter, which it continues
  after a restart (`continuity-state` `session_record`; `C3/seq-continuity-across-restart`, required e1); core-restart
  restores the output point only.
- `unreachable` is persisted only for the protocol cause (core-restart 19); no step writes any other
  `unreachable_reason`.
- Timing is unspecified for session-open 18 among steps 11–17 (only before `session.connect`), session-countdown 3 (when the clocks
  are written), spooled-end-finalize 3 against the first `ping` (spooled-end-finalize open item 2).

**Steps that name a record the contract lacks**
- login 3: the account now has its E4b shape in `users.schema.json`.
- The vault's store, delete and grant operations write CO-BB with no protocol document of their own: they are C7
  operations, listed in edge SF-CO.
- ownership-transfer: path SF-CO → CO-BB → CO-DK in the plan, no document; an owner change would be a `session_record` write
  that no schema or step covers (CHANGELOG D8, known gaps).
- session-list has no numbered CO-BB read step (rule 10 owns retention removal): it reads the northbound record (`session-record.schema.json`), which is not stored as
  is; the stored `session_record.session` nests it. Where session-list's data comes from is not said.

**Recording continuation (tsb-program #141).** Reserve the generation in bbolt before pair creation; switch the active pair only after its audit link is durable. Preserve snapshot and committed-point alignment and the input-seq reservation (terminal-output-accounting §5.3). [owed]
