# Edge CO-DK: core ↔ disk

**Status:** normative, 2026-10-07, released with the schemas and fixtures of the same release, under the rules page [`../README.md`](../README.md). An index: it points at the protocols and at
[`../conventions.md`](../conventions.md) and restates none of their rules. Edge catalogues are defined in
[`../README.md`](../README.md), section Files.

| | |
|---|---|
| Ends | CO (core, process) ↔ DK (disk: recordings and the audit log, store) |
| Seams | C8 (recordings: `ttyrec.schema.json`); C6 (the audit: `audit-event.schema.json`) |
| Transport | files on node-local disk: two files per recording, one append-only audit file. No dialogue, no socket |
| Direction | both ways. The core writes everything. It also reads the disk twice: to find a recording's end on restart, cutting only a torn tail (core-restart steps 16–17), and to rebuild the active alert set from the audit log (operator-alerts step 19). The rules page draws the edge one way (plan finding F4) |
| Schemas | `contract/schemas/ttyrec.schema.json`, `contract/schemas/audit-event.schema.json` at `contract-v0.3` |

## Transport rules

Target create, update and delete audit records name the shared or validated private source and field names only, never field values. Credential stores keep the existing audit-before-commit rule.

One line each; the rule itself lives at the source named.

- **Input is recorded before it is sent.** An input frame is appended before the core sends it (ttyrec `x-sidecar`
  `reopen`, Input; byte-relay step 3 before step 4; Contract §13).
- **Append only, frame then code.** The `.ttyrec` frame goes first, its `.dir` code second; the `.dir` holds exactly one
  byte per frame (ttyrec `x-sidecar` `write_order`, `layout`; byte-relay steps 3, 8). The `.ttyrec` has no file header.
- **Clocks.** Output frames carry the worker's stamp and the core never alters one; input frames and broker markers carry
  the core's own input clock; across directions nothing is assumed (ttyrec top, `x-compare` `monotonic`; V12 for
  encodings). The ns → sec/usec derivation is `x-derivation`, truncated.
- **Comparing two recordings.** Per direction the data payloads (codes 0, 1) of an uninterrupted run and a run across
  a restart are byte-equal; end markers are left out; gap markers (code 2) and broker markers (code 5) are left out
  of the payload and compared line for line (so are the login frames, codes 6 and 7, and the own-prompt frames, code 8) (ttyrec `x-compare` `payload-identical`; U11's differ).
- **Reopen rule.** Before appending to an existing recording the writer cuts both files to the last frame both hold, and
  never pads a code (ttyrec `x-sidecar` `reopen`; core-restart step 16). A reader of a live recording cuts the same
  way. Nothing else is ever cut, and never back to the commit point: the core walks forward from it to find the
  recording's end (`x-sidecar` reopen; open_decisions §5).
- **Not recorded.** The attach wiggle and applied size changes: presentation, and ttyrec has no resize frame
  (CHANGELOG v0.3 C8; session-attach step 19; size-control).
- **No credential in a recording.** The worker injects it below the recorded byte stream (session-open step 8; V10, the
  credential rule). The audit and the recordings never carry it; events reference a recording by path, never its bytes
  (audit-event top).
- **Audit is append-only JSONL**, one event per line (audit-event top; V12). `session_type` is on every `session.*`
  event; `recording_path` names the `.ttyrec` and is **absent, never empty**, when there is none (audit-event top,
  `session_end`; session-end-by-tsb step 15; session-end-from-outside step 14; spooled-end-finalize step 6).
- **The host boundary.** `recording_path` is bbolt and audit data only; no surface sees it (V11; session-open Security;
  session-list).
- **Unknown values.** A reader refuses a sidecar code it does not know (ttyrec `x-sidecar` `rules`; V1 for the tag a code
  arrived at: 2 at v0.2, 5 at v0.3). Code 8 arrived with the device login (Release B), 6 and 7 with the supplied login.
- **Graded by** the C8 and C6 fixtures named in each protocol step (V8 for the marks). Sources for the fixed lines:
  ttyrec `x-gap-line`, `x-broker-line`.

## Records

### Recording frames and sidecar codes

One recording per session: `<session_id>.ttyrec` plus `<session_id>.dir`. Direction W = core writes, R = core reads.

| Code | `$def` (ttyrec) | Written / read by (protocol step) | Status |
|---|---|---|---|
| 0 out | `data_frame` | W: byte-relay 8; reattach-handshake 10 (each replayed frame); grid-restore 13 (only a replayed frame past the recording's end) | built (E0–E1) |
| 1 in | `data_frame` | W: byte-relay 3; stale-screen 13 (`0x0C`, `ctrl_l` only) | built; stale-screen 13 E2 |
| 2 gap | `gap_marker` (data: `x-gap-line`) | W: reattach-handshake 10; spooled-end-finalize 4 | built (E1) |
| 3 end:killed | `end_marker` | W: session-end-by-tsb 13; spooled-end-finalize 5 (`killed`) | built (E0–E1) |
| 4 end:terminated | `end_marker` | W: session-end-from-outside 13; spooled-end-finalize 5 (`terminated`) | built (E0–E1) |
| 5 broker | `broker_marker` (data: `x-broker-line`) | W: stale-screen 12 | E2 |
| 6 login_prompt, 7 login_answer | `login_prompt_marker`, `login_answer_marker` (data: `x-login-line`) | W: session-open 17 (a supplied login's two code-7 frames); device-login 20 (a device login's head) | the release that adds `SessionCreateRequest.login`; a device login's from Release B [C8/recording-device-login-frames] |
| 8 own_prompt | `own_prompt_marker` (data: `x-own-prompt-line`) | W: device-login 20 (`login as:` before the user name, `Password:` before a password round's answer, in the head) | E4 (Release B) [C8/recording-device-login-own-prompt] |
| (cut) | `x-sidecar` `reopen`; no `$def` | R then cut: core-restart 16 (a torn tail only); R: core-restart 17 (the walk to the recording's end) | built |

Open, as the protocols hold it: a failed recording write (byte-relay Open items 3).

### Audit events

Contract: eighteen `$def`s carrying thirty-four actions (the vault release adds three `$def`s, `cred_event`, `device_event` and `vault_wipe_event`, and one action on `auth_event`; the release that adds `SessionCreateRequest.login` added `target_auth_event`; Release B adds `target_hostkey_refused_event`; target writes add `target_write_event`; E4b adds `account_event` and `account_recover_event`, separates `auth_event`, and adds `auth.password_change_failed`). Direction W unless marked. From the vault release every vault, device and grant act writes its line first, inside its bbolt write transaction before the commit, and is refused when the line cannot be written (DEFAULT 49) [C7/audit-unwritable-refuses-act].

| `$def` (audit-event) | `action` | Written / read by (protocol step) | Status |
|---|---|---|---|
| `session_connect` | `session.connect` | session-open 19 | built; `credential_ref` and `credential_layer` E4 [C6/event-session-connect-credential]; `detail.login` `prompted` and `refused_attempts` from Release B [C6/event-session-connect-prompted] |
| `target_write_event` | `target.create`, `target.update`, `target.delete`; `detail.source`, field names only; delete omits fields, no-op update has `[]` | target-write 6; file lands before audit, which precedes the index wait; missing line returns 500 with change retained whatever the index does | E4 [C6/event-target-create; C6/event-target-update; C6/event-target-delete] |
| `target_event` | `target.hostkey_accepted` | session-open 19 (when step 10 reported a key) | built |
| `session_other` | `session.join` | session-attach 8 | built |
| `session_other` | `session.terminate` | session-end-by-tsb 14 (DELETE only) | built |
| `session_end` | `session.disconnect` | session-end-by-tsb 15; session-end-from-outside 14; spooled-end-finalize 6 | built |
| `session_control` | `session.control` | typing-ownership 17; also client-remove 5 and session-detach 7 by → typing-ownership | E2 (new at contract-v0.3; the C2 `token` frame for `attach` and `detach` is built at E1, this event is not) |
| `session_kick` | `session.kick` | client-remove 6 | E2 |
| `session_stale_choice` | `session.stale_choice` | stale-screen 20 | E2 |
| `account_event` | `account.create`, `account.disable`, `account.enable`, `account.password_set`, `account.password_changed` | account-administration 3; before commit; user is target account, actor is caller, never a password or hash; no account event for refusals or already-disabled disable | E4b [C6/event-account-*] |
| `account_recover_event` | `account.recover` | account-administration host recovery; with the core stopped, recovery re-passwords and re-enables an account; seed replace also writes it when replacing and re-enabling a disabled rig login; user is target account, actor absent | E4b [C6/event-account-recover] |
| `auth_event` | `auth.login`; `auth.screen_unlock_failed` (E4); `auth.password_change_failed` (E4b) | login 5; screen-lock 5; account-administration 2 (wrong current-password proof) | built; the screen lock's E4 [C6/event-screen-unlock-failed]; wrong own-change proof [C6/event-password-change-failed; C1/account-password-change-wrong-current-403] |
| `auth_event` | `auth.login_failed` | login 5; detail.reason is `invalid_credentials` or E4b `disabled` | built; E4b disabled reason [C6/event-login-failed-disabled] |
| `target_auth_event` | `target.auth_failed` | session-open 19 (a refused supplied login, or a device login that ended after an answered attempt: device-login 19) | the release that adds `SessionCreateRequest.login`; `login` `prompted` and `attempts` from Release B [C6/event-target-auth-failed-prompted] |
| `target_hostkey_refused_event` | `target.hostkey_refused` | device-login 13 (the operator refused a first-use host key) | E4 (Release B) [C6/event-target-hostkey-refused] |
| `cred_event` | `cred.store`, `cred.delete`, `cred.grant`, `cred.revoke`, `cred.scope_change` | the vault's store and delete (personal and org), and the grant operations (SF-CO, C7) | E4 [C6/event-cred-store-org, C6/event-cred-store-personal-no-class, C6/event-cred-delete, C6/event-cred-grant, C6/event-cred-revoke, C6/event-cred-scope-change] |
| `device_event` | `device.register`, `device.revoke` | device-trust 4, 10, 14; vault-unlock 12 (the wipe's revocations) | E4 [C6/event-device-register, C6/event-device-revoke] |
| `vault_wipe_event` | `vault.wipe` | vault-unlock 12 | E4 [C6/event-vault-wipe] |
| `alert_event` | `alert.raised`, `alert.cleared` | W: operator-alerts 9, 17. R: operator-alerts 19 (rebuild at start) | built; `vt_unavailable` E2 |
| `config_event` | `config.recording_state` | core-restart 6 | built |

`holder` is a shared shape inside `session_control`, not an event.

## Gaps

Records no protocol writes, and steps whose record the contract lacks. Recorded, not decided.

1. **No step creates the recording.** No protocol names the CO-DK step that opens `<session_id>.ttyrec` and `.dir`
   (session-open has one CO-DK step, the audit line at 19). byte-relay steps 3 and 8 append when "the recording is active"; the
   first append is where a file would appear. The condition is policy `recording`.
2. **Nothing reads a recording for a person.** recording-playback (playback to a principal, E5 / v1.0) and command-exec (E5, a recorded
   exec session) are in plan §2 and have no document. Their CO-DK use, and the older-history read scroll-control places at E5
   (G-18), is not in any protocol.
3. **Audit writes the contract has and a protocol leaves owed or unspecified.**
   - `session.terminate` on DELETE: shape graded, "that a DELETE writes it: owed" (session-end-by-tsb 14).
   - `session.join`: its place among session-attach steps 6–23 is unspecified (session-attach Open items 4).
   - `session.control` `detach` for a holder closed at the core's stop: unspecified (session-detach Open items 7); client-remove's `user`
     on a detach: unspecified (client-remove Open items 4).
   - `alert.raised` / `alert.cleared`: the writes, one `alert_id` per raise and clear, and the rebuild at start are
     graded by `C6/event-alert` (reference: U2 `core/internal/alert/alert_test.go`; operator-alerts 9, 17, 19); no
     `vt_unavailable` instance in the contract, so that kind's writes stay owed.
   - `config.recording_state` after a reload: the contract writes it "once when the core starts" (audit-event top;
     `C6/event-recording-state`), so a reload writes none; config-reload Open items 8 holds whether a reload changes
     recording for sessions already open.
4. **Records with no write at all, by design.** Logout writes no audit line: the `auth_event` actions name no logout
   (login 11). A session listed in the answer to the core's own `disconnect` does get one, ruled (DEFAULT 15, accepted
   2026-09-26): it ends `terminated` / `connection_lost`, its `session.disconnect` written as for any end, and is
   logged as an internal error; the contract README's sentence is stale, queue K9 (conventions Open items 9;
   connection-linger 16, session-end-from-outside 5).
5. **Vault events with no protocol step of their own**: `cred.*` is written by the vault's store, delete and grant
   operations, which have no protocol document (they are C7 operations on SF-CO, listed in edge SF-CO); vault unlock and
   lock events wait for credential §11.2.
6. **Steps that name a record the contract lacks:** none found. Every record above has a `$def` or an `x-` rule in the two
   schemas.
7. **Order unspecified:** where the client send falls against the recording and the VT feed (byte-relay Open items 4; an output frame is
   recorded before it is fed, byte-relay step 9).
   Not unspecified: the end marker comes before `release`, which says the core has recorded the session to its end,
   end marker included (ipc-control `release`; session-end-by-tsb 16).

## Counts

Recording codes 9 (0–8), from the release that adds `SessionCreateRequest.login` and, for code 8, Release B. Audit `$def` events 19, actions 34. CO-DK steps found: 27 table rows across 16 protocols (byte-relay 2, reattach-handshake 1, grid-restore 1, stale-screen 3, session-end-by-tsb 3, session-end-from-outside 2,
spooled-end-finalize 3, core-restart 2, session-open 1, session-attach 1, client-remove 1, typing-ownership 1, login 1, operator-alerts 3, target-write 1, account-administration 1). session-detach and client-remove 5 reach the edge only through
→ typing-ownership, as a `—` step.

## Terminal output accounting

| Message or record | Owner | Contract and grading |
|---|---|---|
| audit `recording.output_incomplete` | terminal-output-accounting §5 | closed extent/unconfirmed/sealed/not_recorded/unrecorded union, fsync before core release; no second end [C6/recording-integrity-event-shape; C6/recording-integrity-unrecorded] |
| ttyrec code 9 `terminal_incomplete` | terminal-output-accounting §6 | PTY scope (terminal-output-accounting §1); unknown remainder before original end, no counted loss or output seq; completed recording immutable [C8/terminal-incomplete-before-end] |
| audit startup LF-tail repair | terminal-output-accounting §7 | cut only unterminated final suffix, fsync before exposing writer; LF-complete parse failures remain [C6/terminal-audit-tail-reopen] |

**Recording continuation (tsb-program #141).** Create and sync the continuation pair and relevant directory entries; append and fsync the audit link before the bbolt active switch; quarantine a failed pair at P and preserve its suffix as unconfirmed evidence. See terminal-output-accounting §5.3. [owed]
