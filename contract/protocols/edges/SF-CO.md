# Edge SF-CO — surface ↔ core, through nginx

**Status:** normative, 2026-10-07, released with the schemas and fixtures of the same release, under the rules page [`../README.md`](../README.md). This
catalogue is an index: behaviour stays in the protocol documents (`../<ID>.md`) and in
[`../conventions.md`](../conventions.md), cited by V-number. It records what is there and decides nothing.

| | |
|---|---|
| **Edge** | SF-CO |
| **Ends** | SF (surface: TUI, web, desktop, any API client; external) ↔ CO (core; process) |
| **Seams** | C1 (REST), C2 (WebSocket frames); C7 (vault) from E4 |
| **Transports** | HTTPS REST (JSON); the WebSocket (`GET /ws/attach`); server-sent events over HTTPS (`GET /alerts`) |
| **nginx** | terminates TLS and passes traffic through; it is not an edge end (README, component codes) |
| **Contract** | `contract/openapi/tsb.openapi.json`, `contract/schemas/ws-frames.schema.json`, in this release |
| **Protocols using it** | 29: account-administration, target-import, target-write, operator-alerts, session-open, session-end-from-outside, session-end-by-tsb, session-countdown, session-attach, typing-ownership, client-remove, session-detach, session-list, scroll-control, quiet-prompt, core-restart, data-socket-redial, grid-restore, byte-relay, config-reload, reattach-handshake, login, stale-screen, size-control, inventory-list, vault-unlock, device-trust, vault-lock, screen-lock |

"P.n" below means step n of protocol P. "→" in a step cell means that protocol only runs the message on behalf of
another; the owner is the first protocol listed.

## Transport rules

- **Auth, REST and SSE:** the `tsb_session` cookie (HttpOnly, Secure, SameSite=Strict), held in memory only; no
  ticket (V10; OpenAPI `securitySchemes.session`; login). Foreign-origin browser writes to login and logout are
  refused (V10; conventions open item 8).
- **Auth, WebSocket:** a one-time ticket in the first frame (`attach`), bound to principal and session or, on a device-login socket from Release B (`device_login_attach`), to principal and device login, never in a URL
  and no cookie needed (V10; session-attach.2–5; `ws-frames.schema.json#/x-attach-order`).
- **Ordering:** one client's frames apply in the order sent; a take-over applies before that client's next frame
  (`ws-frames` top; terminal-size §6; size-control.1; byte-relay.1).
- **Errors:** REST failures carry the error body, a registered `code` at one status (V4); the WebSocket ends only with
  a close code from one registry (V3, below); unknown `t`, field or event is ignored (V2); unknown value: V5.
- **Versioning:** every WebSocket text frame carries `t` and `v` (1); REST has none but `Ticket.v` (1, OpenAPI
  `Ticket`; `C1/ws-ticket-mint`); growth by addition only (V1).
- **Encodings:** JSON text frames, raw binary frames with no envelope, SSE `data` one JSON object (V12; binary names
  V15).
- **Host boundary:** no pid, path, hostname, software version or uptime in any response, frame, error or alert (V11;
  Contract §4).
- **Who may act and who is asked:** roles V9; choices presented, never picked V14; no privileged path V16.
- **Close codes** (`ws-frames.schema.json#/x-close-codes`): 4000 `bad_attach`, 4001 `ticket_invalid`, 4003
  `forbidden`, 4004 `session_not_found`, 4010 `session_closed`, 4011 `detached_by_server`, 4012
  `session_terminated`, 4014 `removed`, from the vault release 4015 `device_revoked`, and from Release B 4016
  `device_login_ended`, and E4b 4017 `account_login_ended`. 4013 is retired. Client: unknown 4xxx is 4010; no auto-reattach after 4014, 4015, 4016 or 4017 (V3).
- **Vault bytes:** every byte value a C7 operation carries is padded base64, `device_id` base64url; free text in them
  (references, labels, device names) is bounded in the schema and rendered as text (conventions, encodings).

## Messages — HTTPS REST

52 operations (`contract/openapi/tsb.openapi.json`): 15 before the vault release, 24 from it, four target-write operations, six E4b account operations, two vendor-template reads, and target import. Direction is SF → CO for the request, CO → SF for the answer.
Statuses come from the protocols' headers. There is no REST resize: a size request is the C2 `resize` frame from the
keyboard holder (`session-record` `cols`; CHANGELOG v0.3 C1; size-control).

| Method + path (operationId) | Request → answer (schema) | Protocols and steps | Status |
|---|---|---|---|
| `GET /accounts` (`listAccounts`) | → `AccountList`; 401, 403 | account-administration.1–2 (read only) | E4b |
| `POST /accounts` (`createAccount`) | `AccountCreateRequest` → 201 `AccountCreated`; 401, 403, 409 `user_exists`, 422, 500 | account-administration.1–6 | E4b |
| `POST /accounts/{username}/disable` (`disableAccount`) | → 204 (also already disabled, no audit); 401, 403, 404, 409 `account_self_disable`, 422, 500 | account-administration.1–6 | E4b |
| `POST /accounts/{username}/enable` (`enableAccount`) | `AccountPasswordRequest` → 204; 401, 403, 404, 409 `account_already_enabled`, 422, 500 | account-administration.1–6 | E4b |
| `POST /accounts/{username}/password` (`setAccountPassword`) | `AccountPasswordRequest` → 204; 401, 403, 404, 409 `account_disabled`, 422, 500 | account-administration.1–6 | E4b |
| `POST /auth/password` (`changeOwnPassword`) | `OwnPasswordChangeRequest` → 204; 401, 403 `password_change_failed` or foreign Origin, 422, 429, 500 | account-administration.1–6 | E4b |
| `GET /healthz` (`healthz`) | → `Health` | none | built (contract only) |
| `POST /targets` (`createTarget`) | `TargetCreateRequest` → 201 `TargetWriteRecord`; 401, 403, 409 `target_id_taken`, 422, 500 `internal_error` (change took effect, audit missing; re-read before retry), 503 | target-write.1–8 | E4 |
| `GET /targets/{id}/definition` (`getTargetDefinition`) | → 200 `TargetDefinition`; 401, 403, 404, 503 (visibility unavailable) | target-write.1–2 (CO-RD visibility), .3 (CO-FS read), .8 | E4 |
| `PUT /targets/{id}` (`replaceTarget`) | `TargetDefinition` → 200 `TargetWriteRecord`; 401, 403, 404, 422, 500 `internal_error` (change took effect, audit missing; re-read before retry), 503 | target-write.1–8 | E4 |
| `DELETE /targets/{id}` (`deleteTarget`) | → 204; 401, 403, 404, 500 `internal_error` (target gone, audit missing; re-read before retry), 503 | target-write.1–8 | E4 |
| `POST /target-imports` (`importTargets`) | `TargetImportRequest` → 200 preview or result; 401, 403, 409, 422, 500, 503; errors carry only authorized progress; every answer no-store | target-import.1–15 | E3b |
| `GET /vendor-prompt-templates` (`listVendorPromptTemplates`) | → 200 `VendorPromptTemplateList`; 401 | target-write, vendor prompt catalogue | E3b |
| `GET /vendor-prompt-templates/{vendor}` (`getVendorPromptTemplate`) | → 200 `VendorPromptTemplate`; 401, 404 `vendor_template_not_found` | target-write, vendor prompt catalogue | E3b |
| `GET /targets` (`listTargets`) | `type`, `tag` (repeatable), `group`, `discovered_by`, `limit`, `cursor` → 200 `TargetList` (optional caller-relative SSH login hints; no-store when present); 401, 422, 503 | inventory-list.1, .3, .4 | E3 |
| `GET /targets/{id}` (`getTarget`) | → 200 `TargetRecord` (optional caller-relative SSH login hint; no-store when present); 401, 404, 503 | inventory-list.5, .6, .7 | E3 |
| `POST /auth/login` (`login`) | `LoginRequest` → `Principal` + cookie; 401 `invalid_credentials`; 403 `CrossOrigin`; 422 `InvalidRequest`; 429 `rate_limited` | login.1, .6 | built (E0; cross-origin E1) |
| `POST /auth/logout` (`logout`) | → 204, cookie cleared; 401; 403 `CrossOrigin` | login.8, .11; operator-alerts.22 (→ login) | built |
| `GET /auth/me` (`me`) | → `Me` (the `Principal` and `recording.login_input`, session-open.22); 401 | login.7; session-open.22 | built; `recording` from the release that adds `login` |
| `POST /auth/ws-ticket` (`mintWsTicket`) | `TicketRequest` → `Ticket`; 401, 403, 404, 422 | session-attach.2, .3 | built (E0) |
| `GET /sessions` (`listSessions`) | optional `ended` (`include` default, `exclude`, `only`), `limit` (1..500), `cursor` (requires `limit`) → `SessionList`, optional `next_cursor`, descending `(created_at, id)`; 401, 422 `invalid_request` | session-list.1, .3, .8–10; session-countdown.9 | built; record grows E2; `ended` E4 (published) |
| `POST /sessions` (`createSession`) | `SessionCreateRequest` (the `new` branch may carry `login`, `SessionLogin`: a username and password for one new connection, used only to authenticate it; the create stores nothing of it, V10; or, from Release B, `kind: prompt`, a device login) → 201 `SessionCreateResponse` (`HostKey`), or for a device login 202 `DeviceLoginPending`; 401, 403, 404, 409, 422, 429, 502, 503 | session-open.1, .2a, .3, .4, .21; device-login.3–7; size-control.15, .18; session-countdown.8 | built (E0–E1); clamp E2; `login` from its release |
| `GET /sessions/{id}` (`getSession`) | → `SessionRecord` until retention removal, then indistinguishable 404 `session_not_found`; 401, 404 | session-list.4, .6, .10; session-countdown.9 | built; record grows E2 |
| `DELETE /sessions/{id}` (`closeSession`) | → 204; 401, 403, 404 | session-end-by-tsb.1, .7 | built (E0–E1) |
| `DELETE /sessions/{id}/clients/{client_id}` (`removeClient`) | → 204; 401, 403, 404 `ClientNotFound` | client-remove.1, .8 | E2 |
| `GET /sessions/{id}/output` (`outputPage`) | `before_seq`, `max_bytes` → 200 `application/octet-stream` + six `TSB-Page-*` headers; 401, 403, 404, 422, 501 `NotImplemented` | scroll-control.5, .8, .9 | E5 (published E2; an E2 core answers 501) |
| `GET /alerts` (`alerts`) | → `text/event-stream` (see SSE); 401; 403 | operator-alerts.1; login.10; config-reload.10 | built (E1) |
| `GET /ws/attach` (`attach`) | → 101, then the WebSocket (`x-websocket`: ws-frames `client`, `server`) | session-attach.4 | built (E0) |
| `GET /vault/salt` (`vaultSalt`) | → `VaultSalt`; 401 | vault-unlock.1 | E4 (C7) |
| `POST /vault/salt` (`vaultMintSalt`) | no body → `VaultSalt`; 401, 409 `vault_exists` | vault-unlock.1b | E4 (C7) |
| `POST /vault/init` (`vaultInit`) | `VaultInitRequest` → 204; 401, 409 (`vault_not_minted`, `vault_exists`), 422 | vault-unlock.3, .5 | E4 (C7) |
| `POST /vault/unlock` (`vaultUnlock`) | `VaultUnlockRequest` → 204; 401, 403 `device_not_trusted`, 409 (`unlock_failed`, `challenge_failed`, `part2_missing`, `vault_not_initialized`), 422, 429 | vault-unlock.3–8 | E4 (C7) |
| `POST /vault/part2` (`regeneratePart2`) | `Part2RegenerateRequest` → 204; 401, 403, 409, 422, 429 | vault-unlock.3, .7 | E4 (C7) |
| `DELETE /vault/users/{username}` (`wipeVault`) | → 204; 401, 403, 404 `user_not_found`, 409 `vault_wipe_blocked`, 500 | vault-unlock.11 | E4 (C7) |
| `POST /devices/challenges` (`issueDeviceChallenge`) | `DeviceChallengeRequest` → `DeviceChallenge`; 401, 403, 409 `challenge_failed`, 422 | device-trust, the challenge; .1 | E4 (C7) |
| `POST /devices` (`registerDevice`) | `DeviceRegisterRequest` → 201 `Device`; 401, 409, 422, 429, 500 | device-trust.2–6 | E4 (C7) |
| `GET /devices` (`listDevices`) | `user` → `DeviceList`; 401, 403 | device-trust.8 | E4 (C7) |
| `DELETE /devices/{device_id}` (`revokeDevice`) | `user` → 204; 401, 404 `device_not_found`, 500 | device-trust.9–13 | E4 (C7) |
| `GET /vault/credentials` (`listCredentials`) | → `CredentialList`; 401 | session-open.3 (what may be mapped) | E4 (C7) |
| `POST /vault/credentials` (`storeCredential`) | `CredentialStoreRequest` → 201 `CredentialStored`; 401, 409 (`vault_locked`, `vault_not_initialized`, `credential_exists`), 422, 500 | saving a login for a target (D15) | E4 (C7) |
| `DELETE /vault/credentials/{reference}` (`deleteCredential`) | → 204; 401, 404, 409 `vault_locked`, 500 | vault-unlock open item 2 (the mappings it clears) | E4 (C7) |
| `GET`, `POST /vault/org-credentials`; `DELETE /vault/org-credentials/{reference}` (`listOrgCredentials`, `storeOrgCredential`, `deleteOrgCredential`) | `OrgCredentialStoreRequest` → 201 `OrgCredentialStored`; `OrgCredentialList`; 403 without `vault.admin`; 409 (`vault_locked`, `broker_unlock_unavailable`, `credential_exists`, `credential_has_grants`) | session-open.3 (Layer 2b) | E4 (C7) |
| `POST /vault/grants`; `POST`, `DELETE /vault/grants/{grant_id}` (`createGrant`, `changeGrantScope`, `revokeGrant`) | `GrantCreateRequest` → 201 `Grant`; `GrantScopeRequest` → `Grant`; 204; 403, 404 `grant_not_found`, 422 | session-open.3 (Layer 2b) | E4 (C7) |
| `POST /profiles/batch` (`mapProfiles`) | `ProfileMapRequest` → 204; 401, 422 | session-open.3 (Layer 2a) | E4 (C1) |
| `POST /auth/bind` (`bindLogin`) | `LoginBindRequest` → 204; 401, 403, 409, 422 | login.15; device-trust.7 | E4 (C1) |
| `POST /auth/activity` (`activitySignal`) | → 204; 401 | vault-lock.2; login.17 | E4 (C1) |
| `POST /auth/lock` (`lockNow`) | → 204; 401 | vault-lock.6; login.17 | E4 (C1) |
| `POST /auth/screen-unlock` (`screenUnlock`) | `ScreenUnlockRequest` → 204; 401, 403 `screen_unlock_failed`, 422, 429 | screen-lock.3, .4; login.16 | E4 (C1) |

## Messages — WebSocket

`GET /ws/attach` upgrades; frames per `contract/schemas/ws-frames.schema.json`: 5 client frames, 12 server frames,
3 binary messages, WebSocket ping and pong, and the close codes. `#/$defs/<name>` in the third column.

| Frame `t` / message | Direction | `$def` | Protocols and steps | Status |
|---|---|---|---|---|
| `attach` | SF → CO | `attach` | session-attach.5; typing-ownership.3, quiet-prompt.11, core-restart.27 (→ session-attach) | built (E0); `cols`/`rows` E2 |
| `resize` | SF → CO | `resize` | size-control.1; stale-screen.7 (→ size-control); typing-ownership.14 (non-holder) | built (E0) |
| `handoff` | SF → CO | `handoff` | typing-ownership.5, .6, .7, .9 | E2 (hand-off, revoke, take-over) |
| `quiet_prompt_answer` | SF → CO | `quiet_prompt_answer` | quiet-prompt.8 | built (E1) |
| `stale_answer` | SF → CO | `stale_answer` (choice enum `stale_choice`) | stale-screen.11 | E2 |
| `device_login_attach` | SF → CO | `device_login_attach` | device-login.8 | E4 (Release B) |
| `device_login_hostkey_answer` | SF → CO | `device_login_hostkey_answer` | device-login.13 | E4 (Release B) |
| `device_login_answer` | SF → CO | `device_login_answer` | device-login.16 | E4 (Release B) |
| `device_login_cancel` | SF → CO | `device_login_cancel` | device-login."how a device login ends" | E4 (Release B) |
| `attached` | CO → SF | `attached` | session-attach.9; typing-ownership.3, size-control.12, session-countdown.10 (→ session-attach) | built (E0) |
| `prefix` | CO → SF | `prefix` | session-attach.12; grid-restore.15; scroll-control.4 (`scrollback`, E5) | E2 |
| `clients` | CO → SF | `clients` | session-attach.15; session-detach.8; client-remove.7 | E2 |
| `notice` | CO → SF | `notice` | session-countdown.7 | built (E1) |
| `quiet_prompt` | CO → SF | `quiet_prompt` | quiet-prompt.7 | built (E1) |
| `token` | CO → SF | `token` | typing-ownership.16; session-attach.17; client-remove.5, session-detach.7 (→ typing-ownership, reason `detach`) | built (E1: `attach`, `detach`); more reasons E2 |
| `input_refused` | CO → SF | `input_refused` | byte-relay.1; typing-ownership.14; quiet-prompt.1; stale-screen.6; reattach-handshake.3; data-socket-redial.3; core-restart.28 | built (E1); `not_typist`, `stale` E2 |
| `data_path` | CO → SF | `data_path` | data-socket-redial.6, .8 | built (E1) |
| `size` | CO → SF | `size` | size-control.11, .12; typing-ownership.14; stale-screen.7 | E2 |
| `request_refused` | CO → SF | `request_refused` | typing-ownership.13; stale-screen.11 | E2 |
| `stale` | CO → SF | `stale` | stale-screen.3, .5, .22; session-attach.16 | E2 |
| `stale_prompt` | CO → SF | `stale_prompt` | stale-screen.4; session-attach.16 | E2 |
| `device_login_hostkey` | CO → SF | `device_login_hostkey` | device-login.12 | E4 (Release B) |
| `device_login_prompt` | CO → SF | `device_login_prompt` | device-login.15 | E4 (Release B) |
| `device_login_result` | CO → SF | `device_login_result` | device-login.9, .18, .20, .21 | E4 (Release B) |
| binary: input bytes (V15) | SF → CO | none (raw bytes) | byte-relay.1; typing-ownership.8; quiet-prompt.1; stale-screen.6; reattach-handshake.3; data-socket-redial.3; core-restart.28 | built (E0) |
| binary: output bytes (V15) | CO → SF | none | byte-relay.11 | built (E0); per-client rule E2 |
| binary: dump (V15) | CO → SF | none; geometry in `prefix` | session-attach.13; grid-restore.15 | E2 |
| WebSocket ping | CO → SF | RFC 6455 | session-detach.2 | built |
| WebSocket pong | SF → CO | RFC 6455 | session-detach.3 | built |
| close (client's own) | SF → CO | RFC 6455 §5.5.1; no code named | session-detach.1 | built |
| close 4000, 4001, 4003, 4004 | CO → SF | `x-close-codes` | session-attach.5 (first failing check); 4003 also config-reload.9; 4004 also session-end-from-outside.18, session-end-by-tsb.19 (a later attach) | built (E0) |
| close 4010 | CO → SF | `x-close-codes` | session-end-by-tsb.19; spooled-end-finalize.9 (`killed`, a running core) | built |
| close 4011 | CO → SF | `x-close-codes` | session-detach.5, .9; byte-relay.11; core-restart.1; session-attach.14, grid-restore.15 (no grid) | built (E0; the core stopping E1); no-grid use E2 |
| close 4012 | CO → SF | `x-close-codes` | session-end-from-outside.18; spooled-end-finalize.9 (`terminated`, a running core) | built |
| close 4014 | CO → SF | `x-close-codes` | client-remove.3 | E2 |
| close 4015 | CO → SF | `x-close-codes` | device-trust.12; login.13 | E4 |
| close 4016 | CO → SF | `x-close-codes` | device-login.8 to .21 (every end but `connected`) | E4 (Release B) |
| close 4017 | CO → SF | `x-close-codes` | account-administration.5–6; login.18; device-login How a device login ends | E4b |

## Messages — server-sent events

| Message | Direction | Schema | Protocols and steps | Status |
|---|---|---|---|---|
| `alert` event, state `raised` (the active set on connect, then each raise) | CO → SF | `AlertEvent` (`x-itemSchema`), `common#/$defs/alert_kind`, `alert_subjects` | operator-alerts.2, .10 | built (E1); `vt_unavailable` E2 |
| `alert` event, state `cleared`, naming the raise's `alert_id` | CO → SF | `AlertEvent` | operator-alerts.18 | built (E1) |
| comment line when no event went out for `heartbeat_interval` | CO → SF | none (OpenAPI `/alerts`) | operator-alerts.21 | built (E1) |
| end of the stream, on logout, expiry, an E4b account act ending its login or a reload removing `broker.health` | CO → SF | none | operator-alerts.24; login.10; config-reload.10 | built (E1) |

## Not yet in the contract

Planned v1 messages that will cross SF-CO (plan §2.2). The contract has no schema for any of them; the inventory
endpoints are inventory-list's, built at this release.

| Protocol | Crosses SF-CO as | Epoch |
|---|---|---|
| file-transfer | open an sftp session and move files (ipc-control `open_sftp` is published on C3 only) | E5 |
| command-exec | run one command as its own recorded session (`session_type` `exec` exists) | E5 |
| recording-playback | play a recording back to a principal allowed to see it | E5 / v1.0 |
| ownership-transfer | transfer a session's ownership | epoch not placed (CHANGELOG D8, known gaps) |

inventory-list's "rebuild the index on demand" is
deferred to a later release: this release rebuilds at start and on a file change (fleet-discovery 6, 7).

## Gaps

1. **`GET /healthz` is used by no protocol.** It is in the contract (graded by `C1/healthz`) and named only in
   login.7 as the one operation outside the cookie rule. It is edge-level liveness, no flow.
2. **Every other contract message is used.** All 49 operations, all 24 text frames, all 11 close codes and both SSE
   shapes appear in at least one SF-CO step. The `$def`s `client`, `server`, `stale_choice` and `stale_choice_text`
   are groupings or helper shapes, not frames, so they have no step of their own.
3. **A step naming a message the contract lacks:** none for a frame, operation or close code. Two names carry no
   schema: the client's own WebSocket close (session-detach.1; "the contract names no close code for a client's own close")
   and WebSocket ping and pong (RFC 6455 control frames; the heartbeat is the core's knob `ws_ping_interval`, no
   contract field, V7).
4. **Status mismatches to note, not decide:** scroll-control is E5 but its OpenAPI operation and the 501 answer are published
   at E2; session-attach.14 and grid-restore.15 (close 4011 for no grid) are E2 uses of a code built at E0; `request_refused`,
   `stale`, `stale_prompt` and `data_path` have no unknown-value rule (conventions open item 5).
5. **Open items that touch this edge** (conventions): 7 (`forbidden` in `x-codes` does not name the `/alerts` 403),
   6 (re-attach after 4011 or a code-less close), 8 (reach of the origin check), 4 (an admin removing a client),
   13 (no registry code for the core's own failure; the built core closes 1011).

**Recording hold (tsb-program #141).** Input is refused `recording_unavailable`. Each attach during a hold gets its retained-grid prefix and one `data_path` `lost`; other attached clients get one lost at the hold (terminal-output-accounting §5.3). [owed]
