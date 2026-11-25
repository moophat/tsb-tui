# client-remove — remove one attached client; the session lives

| | |
|---|---|
| **ID** | client-remove |
| **Purpose** | Let the owner, or a holder of `session.terminate`, remove one attached client: close its socket with 4014, tell the clients that remain, and hand any move of the keyboard to typing-ownership; the session lives on. |
| **Status** | E2 |
| **Corpus clauses** | Contract §10; PTY §7.1; Frontend §9; Storage §9; session model §9 E4, §10, §11 (`quiet_prompt`); terminal-size §5 |
| **Seams** | C1, C2, C6 |
| **Schemas** (contract-v0.3) | OpenAPI `DELETE /sessions/{id}/clients/{client_id}` (`removeClient`), `#/components/parameters/SessionId`, `#/components/parameters/ClientId`, `#/components/responses/Unauthenticated`, `#/components/responses/Forbidden`, `#/components/responses/ClientNotFound`; `error.schema.json` `x-codes` (`unauthenticated`, `forbidden`, `session_not_found`, `client_not_found`); `ws-frames.schema.json#/x-close-codes` (4014), `#/$defs/clients`, `#/$defs/token`; `session-record.schema.json#/properties/owner`, `#/properties/state`, `#/properties/clients`, `#/properties/control_holder`; `audit-event.schema.json#/$defs/session_kick`, `#/$defs/session_control`; `policy.schema.json#/$defs/permission` |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`) at contract-v0.3.
Fixtures are cited as `<seam>/<name>` under `contract/fixtures/`. Corpus files are under `srs/`.
"Queue" is tsb-core `plans/reports/queue-261007-operator-decisions.md`; "approval" is
tsb-core `plans/261006-1015-e2-contract/approval.md`. Owner, keyboard holder and escrow are conventions V9's; a
**`session.terminate` holder** is granted "terminate any session" (Storage §9). A client is named by `client_id`,
which the caller learns from the record's `clients` (→ session-list) or a `clients` frame (→ session-attach).

Not client-remove: a client's own close, a missed heartbeat or the core's stop (close 4011) is → session-detach; the session's end
(4010) is → session-end-by-tsb; who receives the keyboard is → typing-ownership; a later attach is → session-attach.

## Path

remove (steps 1–8): SF-CO → SF-CO → (typing-ownership) → CO-DK → SF-CO → SF-CO · refused (steps 1–2): SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `DELETE /sessions/{id}/clients/{client_id}` (`removeClient`) | the owner, or a `session.terminate` holder (OPEN: Open item 1); attached or not ("the owner need not be attached to use it", `removeClient`) | asks to remove that one client | no valid cookie: 401 `unauthenticated` | the caller |
| 2 | — | CO → CO | — | — | the core | checks: the session exists and the principal may see it; the principal is the owner or a `session.terminate` holder, checked before `client_id` (`C1/client-remove-not-owner-403`); `client_id` names a client attached now | no such session, or one the principal may not see: 404, its code OPEN (Open item 3: `session_not_found` by CHANGELOG v0.3 C1, while `removeClient` declares only `ClientNotFound`); seen but neither owner nor holder: 403 `forbidden`, whatever the `client_id`; no such attached client, or one the principal may not see: 404 `client_not_found` (`ClientNotFound`). Nothing changes | the caller |
| 3 | SF-CO | CO → SF | WebSocket | close 4014 `removed` (`#/x-close-codes`) | the core | the client's socket closes: a detach, event E4 (session model §9 E4). 4013 is retired (conventions V3) | — | the removed client |
| 4 | — | CO → CO | — | — | the core | the client leaves the set (session-record `clients`); `state` stays `live`, or goes `detached` when none is left (session model §10); whether that moves an `unreachable` session's `state` is not said (Open item 7). The session lives on (4014: "The session lives on"). No clock moves: a removal is no byte (session model §10). A `quiet_prompt` armed by this client's input disarms (session model §11; → quiet-prompt step 4). The target's size does not change (terminal-size §5) | — | any reader of the record (→ session-list) |
| 5 | — | CO → CO | — | → typing-ownership | the core | typing-ownership's `token` frame on SF-CO, reason `detach`, and its `session_control` line on CO-DK, reason `detach` (D26, open: Open item 8); only when the removed client held the control token: "its token move is a detach" (`removeClient`; CHANGELOG v0.3 C2); it passes on or goes to escrow (Contract §10; PTY §7.1). Which client receives it is typing-ownership's, and unspecified (plan §7 G-2) | — | every client still attached; the audit log |
| 6 | CO-DK | CO → DK | file, append-only JSONL | `audit-event.schema.json#/$defs/session_kick`, `action` `session.kick` | the core | `user` is the remover; `client_id` and `client_user` are the removed client and its principal; `session_type` as on every `session.*` event (audit-event top). D26, open (Open item 8) | — | readers of the audit log |
| 7 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/clients` | the core | the whole set of attached clients, without the removed one (`#/$defs/clients`: "on each attach, detach and removal"; D5, in neither list on the approval page: Open item 8) | — | every client still attached; nobody when none is left |
| 8 | SF-CO | CO → SF | HTTPS | 204 | the core | "Removed." (`removeClient`). Its place among steps 3–7 is unspecified (Open item 5) | — | the caller |

### Rules for these steps

The core. Marks are the fixture's (`required-green` epoch, grade kind).

1. **Authority.** The core MUST remove a client only for the owner or a `session.terminate` holder, attached or not
   (`removeClient`, the contract's text; Open item 1), and MUST refuse 403 `forbidden` to a principal that can see
   the session but is neither, before it reads `client_id`, touching nothing (Contract §10; conventions V9).
   Fixture: `C1/client-remove-not-owner-403` (e2, live). The owner not attached, and the 401: owed.
2. **Not found.** The core MUST answer 404 `client_not_found` when `client_id` names no client attached to the
   session, touching nothing (`ClientNotFound`). Fixture: `C1/client-remove-unknown-404` (e2, live). A client the
   principal may not see, and a session that is absent or invisible (Open item 3): owed.
3. **The close; the session lives.** The core MUST answer 204, close the removed client's socket with 4014 and no
   other code, and MUST NOT end the session (`removeClient`; `#/x-close-codes` 4014). Fixture:
   `C1/client-remove-by-owner` (e2, live): 204, 4014, `live` with a client left. The last one removed: owed.
4. **`clients`.** The core MUST send `clients` to every client still attached, without the removed one, and the
   record's `clients` MUST hold the same set (`#/$defs/clients`). Fixture: `C1/client-remove-by-owner` grades the
   frame; the record's field: owed. Both lean on D5 (Open item 8).
5. **The keyboard.** Removing a client that does not hold the control token MUST leave the token where it is.
   Fixture: `C1/client-remove-by-owner` (`control_holder` stays the remaining client). Removing the holder MUST move
   the token as a detach (step 5); its frame and audit line are typing-ownership's MUSTs. Fixture: owed;
   `C2/token-event-on-detach` (e1, live) grades a holder's own close, not a removal.
6. **No resize.** A removal MUST NOT change the target's size, whether or not the token moves (terminal-size §5:
   "A token move never resizes the target"; CHANGELOG v0.3 C2). Fixture: owed; `C2/token-move-no-resize` (e2,
   live) grades a hand-off and a take-over only.
7. **Audit.** The core MUST write exactly one `session.kick` per removal, as step 6 says (`#/$defs/session_kick`).
   Fixture: the shape, `C6/event-vocabulary-v0.3` (e2, reference; its U2 test is "owed by the E2 core plan"),
   which refuses a kick without `client_user`; the write per removal: owed. `session.kick` is D26, open (Open item 8).

A consumer (surface, any API client). No fixture grades a consumer, so each MUST below is owed.

8. A consumer MUST NOT re-attach by itself after 4014 (contract `README.md` "Senders and receivers"; conventions
   V3). A v0.2 consumer does not know 4014, reads it as 4010 and stops: "4014 `removed` reads safe-by-4010".
9. A surface's owner kill button MUST ask for confirmation before it sends the request (Frontend §9: "Hard
   disconnect, immediate. Requires confirmation"). `clients` is read as session-detach rule 11 says; error codes by V4.

## Refusals and who is told

- A refusal goes to the caller alone (steps 1–2) and changes nothing; no audit event exists for it (`$defs`).
- The removed client learns of it from 4014 only; no frame names who removed it. A v0.2 client reads 4014 as 4010,
  "the session was killed" (contract `README.md`), so its user is told the session ended while it lives on.
- Every client still attached gets `clients` (step 7), and `token` when the holder was removed (step 5); neither
  frame names the remover. The audit log names both: `user` the remover, `client_user` the removed (step 6).
- A reader of the record sees `clients`, `state`, `control_holder` change (→ session-list). No host detail anywhere (V11).

## Security considerations

- **Lifecycle authority.** Removal is the owner token's power, "kill any attached client" (PTY §7.1; Contract §10).
  Holding the control token gives no right to remove: the two tokens "are never conflated" (Contract §10). The
  contract also grants it to `session.terminate` holders, which the corpus does not (Open item 1).
- **Who may type.** Removing the keyboard holder takes the keyboard from it at once: its socket is closed, so no
  further input of its arrives, and the token moves as a detach (→ typing-ownership). The owner can remove the holder too
  (Frontend §9: "including the control token holder").
- **Removal is not exclusion.** 4014 forbids only the surface's own re-attach. The removed principal keeps its
  `policy.yaml` rights; nothing in session-attach refuses a new ticket and attach by hand (→ session-attach steps 2, 5). Open item 2.
- **Cross-origin writes.** The cookie is SameSite=Strict (`securitySchemes.session`); whether this DELETE refuses
  a foreign Origin is conventions open item 8 (the check is declared on login and logout only).

## Open items

1. **OPEN — who may remove (queue B1; plan §7 C-6; conventions open item 4).** The contract allows the owner and
   `session.terminate` holders (`removeClient`; `#/x-close-codes` 4014; `#/$defs/session_kick`;
   `C1/client-remove-not-owner-403`). The corpus gives "kill any attached client" to the owner token alone (PTY
   §7.1; Contract §10; Frontend §9 "Owner kill button"); `session.terminate` is "terminate any session" (Storage §9);
   D9, as agreed, names the owner only (approval "D9"). Steps 1–2 and rule 1 carry the contract's text. Options: B1.
2. **OPEN — may the removed principal come back by hand (queue B2; plan §7 G-14).** The contract forbids only
   auto-reattach (contract `README.md`). Removal as "close this connection" or "keep them out" is not ruled; B2's
   option (a) leans on B3 (plan §7 G-8). Options: queue B2.
3. **OPEN — the contract against itself (not in plan §7.2): the 404 for the session.** CHANGELOG v0.3 C1 lists "404
   `session_not_found` / `client_not_found`"; `removeClient` declares only `ClientNotFound`. What an absent or
   invisible session answers on this route is therefore stated in the CHANGELOG only. Step 2 picks neither.
4. **Unspecified (not in plan §7): `user` on the detach.** `session_control`'s `user` is "the departing holder for
   detach"; in a removal the remover acted (`session_kick` `user`). Which one it names is not said; session-detach's open
   item 5 (a detach "to null" against a token that passed on) holds here too. typing-ownership's to carry.
5. **Unspecified (not in plan §7):** the order of steps 3, 5, 6, 7 and 8, and whether the 204 waits for the close;
   a caller removing its own client; a client still waiting for its prefix (→ session-attach); what makes a client of a
   visible session invisible (`ClientNotFound`; session-list open item 1).
6. **Fixture wording (reported).** `C1/client-remove-by-owner`: "The owner's token move is a detach", yet it removes
   the peer's client, which holds no token; `removeClient` says "its token move", the removed client's.
7. **Unspecified (not in plan §7): an `unreachable` session.** It is attachable (common `session_state`), and the
   record's `oneOf` allows `unreachable_reason` only with state `unreachable`. Whether removing its last client moves
   it to `detached` (step 4) is not said (`C1/session-unreachable`: "Not detached: detached has no clients,
   unreachable has no data path the core reads").
8. **OPEN — approval items leaned on.** D5 (`clients` on record and frame: step 7, rule 4) is in neither the agreed
   nor the open list on the approval page (queue A11). D26 (`session.kick` and the `session_control` line: steps 5,
   6, rule 7) is an open southbound item (approval "Southbound items"; queue A7). Not ruled.

## Picture

pending
