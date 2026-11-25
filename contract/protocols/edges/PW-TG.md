# Edge PW-TG: protocol worker to target

| | |
|---|---|
| **Edge** | PW-TG |
| **Ends** | protocol worker (PW, process) and target (TG, external) |
| **Seam** | none; the devices' own protocols |
| **Status** | built (E0-E1) for SSH; telnet built (this release); serial deferred to a later release; sftp E5 |
| **Transport** | SSH; telnet; serial in a later release |

This page is an index. TSB has no schema for this edge. The contract names only the SSH messages TSB reacts to (`contract/schemas/ipc-control.schema.json` top, `resize`, `channel_closed`, `connection_lost`; session model section 8). Every row below is "not contract".

## Transport rules

- The credential is injected at the protocol layer, below the recorded byte stream, and travels only in `connect.auth` and, from 0.7, `auth_answer`: V10; session-open step 8; device-login Security considerations.
- Host keys: on a connect that is not in prompt mode the worker accepts, warns and records on first use; in prompt mode it asks first (`hostkey_ask`) and records the key only when the operator accepts; a changed key is never accepted: session-open steps 8, 9; device-login steps 11 to 14.
- An exit status or exit signal is never acted on (at this release the contract does not carry it; carrying it to the clients as data is E5's, with exec), and a prompt is never matched: V13; session-end-from-outside step 1.
- `CHANNEL_EOF` alone ends nothing: session-end-from-outside step 1.
- A worker never coalesces two `window-change` requests: stale-screen step 17 (`ipc-control#/$defs/resize`).
- Ends from outside TSB: V13 (`terminated`); ends through TSB: `killed`.
- The echo driver has no target: session-open step 8.

## Messages

| Message (target protocol) | Direction | Protocols and steps | Status |
|---|---|---|---|
| SSH key exchange, host key presented, then user authentication | PW to TG; TG to PW | session-open 8 | built |
| the probe: SSH key exchange only (public-key host-key algorithms, no certificate algorithm), then a close; no user name and no `SSH_MSG_USERAUTH_REQUEST` | PW to TG; TG to PW | device-login 11 | E4 (Release B) |
| `SSH_MSG_USERAUTH_BANNER` | TG to PW | device-login 15 | E4 (Release B) |
| `SSH_MSG_USERAUTH_INFO_REQUEST` (RFC 4256), and its `SSH_MSG_USERAUTH_INFO_RESPONSE` | TG to PW; PW to TG | device-login 15, 16 | E4 (Release B) |
| the password request, and the password answer | TG to PW; PW to TG | device-login 15, 16 | E4 (Release B) |
| `SSH_MSG_USERAUTH_FAILURE` with partial success true or false (RFC 4252 §5.1), and the password method's change request | TG to PW | device-login 17, 18 (the attempt number rises only on a refusal) | E4 (Release B) |
| `session` channel, `pty-req`, `shell` | PW to TG | session-open 13 | built |
| `CHANNEL_DATA` carrying typed input | PW to TG | byte-relay 5 | built |
| `CHANNEL_DATA` carrying `0x0C` | PW to TG | stale-screen 15 | E2 |
| `CHANNEL_DATA` or `EXTENDED_DATA` carrying output | TG to PW | byte-relay 6; orphan-session 4 (with no core) | built |
| `window-change` (once) | PW to TG | size-control 8 | built |
| `window-change` twice, in order | PW to TG | session-attach 20; stale-screen 17 | E2 |
| `keepalive@openssh.com`, want-reply | PW to TG | link-liveness 4 | built |
| any SSH packet, `REQUEST_SUCCESS` or `REQUEST_FAILURE` | TG to PW | link-liveness 6 | built |
| `SSH_MSG_CHANNEL_CLOSE` on a session's channel (TSB asks) | PW to TG | session-end-by-tsb 9, 23 | built |
| `SSH_MSG_CHANNEL_CLOSE` in reply | TG to PW | session-end-by-tsb 10 | built |
| `CHANNEL_CLOSE` not in reply; `DISCONNECT`; TCP loss (EOF, `ECONNRESET`, `ETIMEDOUT`, `EPIPE`) | TG to PW | session-end-from-outside 1; orphan-session 6 | built |
| the transport closes (linger; the core's `disconnect`; `link_timeout`) | PW to TG | connection-linger 5, 13; link-liveness 15 | built |
| telnet: the address check (resolve once; an IPv4-mapped IPv6 address is checked as the IPv4 address it maps; a deny-listed address is refused before any connection) | PW → TG | session-open 8; fleet-discovery's deny list | built (this release) |
| telnet: TCP connect and option negotiation (RFC 854, RFC 855); TERMINAL-TYPE answered with `connect.term`; the size by NAWS (RFC 1073) | PW → TG; TG to PW | session-open 8; link-liveness 3; size-control 8 | built (this release) |
| telnet: the bytes read before `open_shell` are the session's first output frames, from seq 1 | TG to PW | session-open 8; byte-relay 6 | built (this release) |
| telnet NAWS; a `resize` for a session whose peer refused NAWS, or turned it off with DONT NAWS, is dropped with no answer | PW to TG | size-control 8 | built (this release) |
| telnet `IAC DO TIMING-MARK`; `WILL`, `WONT`, any payload; a `WILL`/`WONT` answering no probe the worker sent is not E3 and draws no reply | PW to TG; TG to PW | link-liveness 5, 7 | built (this release) |
| telnet: one session per connection; the socket closes with it (`connection_lost` cause `single_session`) | PW → TG | connection-linger 4; session-end-by-tsb 8, 23 | built (this release) |
| telnet end (socket closed) | TG to PW | session-end-from-outside 1 | built (this release) |
| serial: DCD (line state); no size message | TG to PW | link-liveness 7; size-control 8 | deferred to a later release |
| sftp | PW to TG | file-transfer (plan section 2.2) | E5 |
| exec channel | PW to TG | command-exec (plan section 2.2) | E5 |

## Gaps

- No TSB schema and no fixture grades the SSH side: the rows say "owed" or point at the CO-PW events the worker raises.
- Whether the worker waits for the reply `CHANNEL_CLOSE`, and what it does if none comes: session-end-by-tsb open items.
- Whether the worker sends `SSH_MSG_DISCONNECT` before closing the transport, and the order against `connection_lost`: link-liveness open items (item 4); connection-linger open items.
- The wire form of the linger close is not contract: connection-linger step 5.
- Serial rows wait for the release that connects serial: link-liveness step 5.
