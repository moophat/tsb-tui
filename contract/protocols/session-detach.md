# session-detach — a client's attachment ends; the session lives

| | |
|---|---|
| **ID** | session-detach |
| **Purpose** | End one client's attachment to a session (it closes, misses WebSocket heartbeats, cannot drain its output queue, or the core stops), tell the clients still attached, and hand any move of the keyboard to typing-ownership; the session lives. |
| **Status** | built (E0–E1); grows E2 (step 7's `session_control` audit line, step 8, the `clients` frame) |
| **Corpus clauses** | Contract §10; PTY §7.1, §7.3; session model §9 E4, E8, §10, §11 (`ws_heartbeat`, `quiet_prompt`), §13; immortality §1.1, §3, §7; terminal-size §2, §4 |
| **Seams** | C2, C6; the record it changes is read over C1 (→ session-list) |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ws-frames.schema.json` top (heartbeat), `#/$defs/clients`, `#/$defs/token`, `#/x-close-codes` (4011); `session-record.schema.json#/properties/state`, `#/properties/clients`, `#/properties/control_holder`; `audit-event.schema.json#/$defs/session_control`, `#/$defs/holder` |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files, under
`srs/`: Contract = `architecture-contract.md`, PTY = `pty-ownership-model.md`, session model =
`session-model-and-lifecycle.md`, immortality = `immortality-mechanism.md`, terminal-size =
`terminal-size-and-control.md`.

**Words used here.**

- **Client**: one attachment to one session, on one WebSocket. One principal may have several (rules page, "Terms";
  `C2/clients-frame` attaches one principal's two sockets).
- **Detach**: the event that ends an attachment: "that WebSocket closes, or `ws_heartbeat` fires for it" (session
  model §9 E4). It ends nothing else: "a client detaches, or its WebSocket dies → nothing — the session is
  `detached`" (session model §13).
- **Ping, pong**: WebSocket control frames (RFC 6455 §5.5.2, §5.5.3). A pong the core receives from a client is that
  client's event E8 (session model §9).
- **`ws_heartbeat`**: fires for a client when no pong has arrived for `ws_ping_interval` × `ws_missed_pongs`, two
  `config.yaml` knobs (session model §11 lists 20 s × 3; conventions V7). The contract leaves the count to the core's
  configuration (ws-frames top).
- **Control token, keyboard holder, escrow**: the right to type and to request the target's size; the one attached
  client holding it; the core holding it when no attached client may (Contract §10; PTY §7.1; session-record
  `control_holder`).

Not session-detach: a removal by the owner or a session.terminate holder (close 4014) is → client-remove; from the vault release, a client whose login ended because the device it is tied to was revoked is closed 4015 `device_revoked` (→ device-trust step 12; login step 13), its session living on, and it MUST NOT auto-reattach (conventions V3); from E4b, an account act ends the affected login's clients with 4017 `account_login_ended` (→ account-administration steps 5–6; login step 18), its terminal sessions living on, and consumers MUST NOT auto-reattach (conventions V3); a session's end (close 4010
or 4012) is → session-end-by-tsb or → session-end-from-outside; the 4011 sent when no attach prefix arrives in time is → session-attach (plan §7 G-9). Every
move of the control token, including the one a detach causes, is → typing-ownership (plan §2.1).

## Path

client closes (1) or the core detaches it (2–5): SF-CO → SF-CO → SF-CO → (typing-ownership) → SF-CO · core stops (9):
SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | WebSocket | close frame (RFC 6455 §5.5.1); the contract names no close code for a client's own close | any attached client | a detach (E4); go to step 6 | none: nothing gates a client's own close | — |
| 2 | SF-CO | CO → SF | WebSocket | WebSocket ping | the core | sent to each attached client every `ws_ping_interval` (session model §11 `ws_heartbeat`; PTY §7.3) | — | — |
| 3 | SF-CO | SF → CO | WebSocket | WebSocket pong | any attached client | E8: restarts that client's `ws_heartbeat`. Only a pong is E8; the client socket's TCP state is not (session model §9 E8) | — | — |
| 4 | — | CO → CO | — | — | the core | decides to detach the client: its `ws_heartbeat` fired, or its output queue will not drain. What "will not drain" means is unspecified (Open item 2) | — | — |
| 5 | SF-CO | CO → SF | WebSocket | close 4011 `detached_by_server` (`#/x-close-codes`) | the core | a detach (E4). After `ws_heartbeat` the socket may be dead and the close never arrive | — | the detached client |
| 6 | — | CO → CO | — | — | the core | the client leaves the session's client set (session-record `clients`); with none left, `state` goes `live` → `detached` (session-record `state`; session model §10); whether the last detach moves an `unreachable` session's `state` is not said (Open item 8). No timer's clock moves: `activity` and `client_touch` move only on input and output bytes (session model §10). A pending `quiet_prompt` whose sender this client was is disarmed (session model §11 `quiet_prompt`; → quiet-prompt). The target's size does not change (terminal-size §2, §4) | — | any principal reading the record (→ session-list) |
| 7 | — | CO → CO | — | → typing-ownership | the core | typing-ownership's `token` frame on SF-CO, reason `detach` (built E1), and its audit line `session_control` reason `detach` on CO-DK (**E2**: new at contract-v0.3; D26, open: Open item 9); only when the departing client held the control token: it passes on to another attached client or goes to escrow (Contract §10; PTY §7.1; session model §11 `ws_heartbeat`). Which client receives it is typing-ownership's, and unspecified (plan §7 G-2) | — | every client still attached; the audit log |
| 8 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/clients` · status **E2** | the core | the whole set of attached clients, without the departed one (D5, in neither list on the approval page: Open item 9) | — | every client still attached; nobody when the last one left |
| 9 | SF-CO | CO → SF | WebSocket | close 4011 `detached_by_server` | the core | the core stops. Every session lives; after the restart every control token is in escrow (immortality §3, §7); → core-restart follows | — | every attached client |

### Rules for these steps

The core:

1. A detach MUST NOT end the session, and MUST change nothing but what steps 6–8 say (session model §13; immortality
   §1.1, §3; PTY §7.3). When the last client closes, the record MUST read `detached` with `control_holder` null.
   Fixture: `C2/attach-ok-bytes-roundtrip` (required-green e0, live).
2. The core MUST ping each attached client every `ws_ping_interval`, and MUST detach a client from which no pong
   arrived for `ws_ping_interval` × `ws_missed_pongs` (session model §11 `ws_heartbeat`; V7). It MUST find a client
   that vanished without a close this way, never by waiting for a close frame or by the socket's TCP state (PTY
   §7.3: "detected via missed heartbeats, not reliant on WebSocket close event"; session model §9 E4). Fixture: owed
   (plan §7.4).
3. The core MUST detach a client whose output queue will not drain (`#/x-close-codes` 4011). Fixture: owed (plan
   §7.4). That one client's backlog delays no other client is → byte-relay's (`C2/per-client-output`).
4. Every detach the core makes MUST close the socket with 4011, never with a code that says the session ended
   (`#/x-close-codes`). Fixture: owed.
5. On every detach, the core MUST send `clients` to every client still attached, without the departed client, and the
   record's `clients` MUST match it. Fixture: `C2/clients-frame` (required-green e2, live) grades the frame after a
   close; the record's field after a detach is owed. Both lean on D5 (Open item 9).
6. A detach MUST NOT change the target's size, whether or not the token moves (terminal-size §2: "With the token in
   escrow, the PTY keeps its current size"; §4: getting the token never sends a request). Fixture:
   `C2/token-move-no-resize` (required-green e2, live) grades a hand-off and a take-over; the detach case is owed.
7. When the core stops, it MUST close every attached client with 4011 (`#/x-close-codes` 4011: "or the core
   stopping"). Fixture: owed. A core that dies sends no close frame; its clients see the WebSocket closed with no
   code, which RFC 6455 calls 1006 (§7.1.5, §7.4.1). What follows is → core-restart.
8. Step 7's frame and audit event are typing-ownership's MUSTs, named here only: `C2/token-event-on-detach` (required-green e1,
   live) grades the `token` frame with reason `detach` naming the new holder; `C6/event-vocabulary-v0.3`
   (required-green e2, reference) grades the `session_control` shape. The audit write is owed.

A consumer (surface, any API client):

9. A consumer MUST answer every ping with a pong (RFC 6455 §5.5.2), or `ws_heartbeat` detaches it. Fixture: owed; no
   fixture grades a consumer.
10. A consumer MUST read 4011 as its own detach with the session alive, never as the session's end (`#/x-close-codes`
    4011: "the session lives on"). Fixture: owed.
11. A consumer MUST read each `clients` frame as the whole set of attached clients, replacing the set it held
    (`#/$defs/clients`: "The set of attached clients"). Fixture: owed.
12. To come back, a consumer attaches again with a new ticket, since a ticket is one-time
    (`common.schema.json#/$defs/ticket`; → session-attach). Whether it does so by itself is unspecified (Open item 4).

## Refusals and who is told

- session-detach has no refusal: no clause gates a client's own close, and a server-side detach is not a request.
- The departed client is told nothing after it leaves, except the 4011 close of a server-side detach (steps 5, 9).
- The clients still attached get `clients` (step 8), and `token` when the holder left (step 7, → typing-ownership).
- Any principal reading the record sees its `clients`, `state` and `control_holder` change (→ session-list).
- The audit records only a token move (step 7). The vocabulary has no event for a detach as such: `session.join`
  records an attach, and nothing records a leave (`audit-event.schema.json` `$defs`).

## Security considerations

- **Who may type.** The control token leaves with its holder (Contract §10), so a client that is gone never keeps the
  keyboard. A holder that vanishes without a close keeps it until its `ws_heartbeat` fires: for up to
  `ws_ping_interval` × `ws_missed_pongs` no other client receives it through session-detach. The owner can still take it by
  typing or by take-over in that window (terminal-size §5; → typing-ownership).

## Open items

1. **G-2.** Which attached client receives the control token when the holder detaches, and whether choosing one is a
   broker decision under Contract §12. typing-ownership's (plan §7 G-2).
2. **Unspecified — the drain bound.** No clause says when a queue "will not drain", and no knob names the bound. The
   corpus lists "Internal WS backpressure protocol" as "Implementation-time · Not an architecture decision"
   (`desktop-client-renderer-landscape.md` §6), while the rules page allows only a `config.yaml` knob to vary, never
   the implementer's judgment. Not in plan §7.
3. **Unspecified — frame order.** When the holder detaches, whether `token` (step 7) comes before or after `clients`
   (step 8). Not in plan §7.
4. **Unspecified — coming back.** Whether a surface re-attaches by itself after 4011 or after a closure with no
   code. The contract forbids it only for 4014 (contract `README.md` "Senders and receivers"), and the client
   transport-resilience layer is deferred whole (decision 5.15, ws-frames top). Not in plan §7. The re-attach loop
   when the grid is unavailable is session-attach's (plan §7 G-9).
5. **Contradiction, the contract against itself (reported, not resolved; not in plan §7.2).** Audit
   `session_control`: "a detach is from the departing holder to null". ws-frames `token`, reason `detach`: "the
   token passed on or went to escrow", and `C2/token-event-on-detach` expects the new holder in `holder`. When the
   token passes on, either the audit's `to` is null while a client holds the token, or a second event follows with
   no reason that fits (`attach` is "the no-clients case"). typing-ownership's to carry. Also, `C6/event-vocabulary-v0.3`'s
   `detach` instance has `user` `bob` and `from` alice's client, against its own note "user is the departing holder"
   (schema-valid, so the grade passes; the example is inconsistent).
6. **G-8.** Whether a client whose join right a policy change removes while it is attached is detached, and with
   which close code. config-reload's (plan §7 G-8).
7. **Unspecified — the core's stop and the audit.** Whether the core writes `session_control` `detach` for each
   holder it closes at step 9; `C2/token-auto-release` says "Every token move is a session.control audit event".
   core-restart's and typing-ownership's. Not in plan §7.
8. **Unspecified — an `unreachable` session.** It is attachable (common `session_state`), and the record's `oneOf`
   allows `unreachable_reason` only with state `unreachable`. Whether the last detach moves it to `detached` (step 6)
   is not said (`C1/session-unreachable`: "Not detached: detached has no clients, unreachable has no data path the
   core reads"). Not in plan §7.
9. **OPEN — approval items leaned on.** D5 (`clients` on record and frame: step 8, rule 5) is in neither the agreed
   nor the open list on the approval page (queue A11). D26 (the `session_control` audit event: step 7) is an open
   southbound item (approval "Southbound items"; queue A7). Not ruled.

## Picture

pending
