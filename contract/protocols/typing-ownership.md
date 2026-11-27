# typing-ownership — who may type, and how that right moves

| | |
|---|---|
| **ID** | typing-ownership |
| **Purpose** | Decide which one attached client may type and request the target's size, and move that right: escrow, pass-on, hand-off, the owner's revoke and reassign, the owner's take-over, and auto-release. |
| **Status** | built (E0: escrow at creation, grant at attach; E1: escrow after a restart, pass-on at a detach, `token` reasons `attach` and `detach`); grows E2 (hand-off, revoke and reassign, take-over and the ordering rule, auto-release, `request_refused`, `input_refused` `not_typist`, `may_control`, the `session.control` audit) |
| **Corpus clauses** | Contract §10, §12; PTY §7.1; terminal-size §2, §4, §5, §6; immortality §7; Frontend §8, §9; session model §9 E1, E4, §11 (`ws_heartbeat`); Storage §9. Rulings 5.41, 5.53 (`decisions.md`) |
| **Seams** | C2, C6; the record it changes is read over C1 (→ session-list) |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ws-frames.schema.json` top (the ordering rule), `#/$defs/handoff`, `#/$defs/token`, `#/$defs/request_refused`, `#/$defs/input_refused`, `#/$defs/attached`, `#/$defs/clients`; `session-record.schema.json#/properties/owner`, `#/properties/control_holder`, `#/properties/clients`; `audit-event.schema.json#/$defs/session_control`, `#/$defs/holder`; `policy.schema.json#/$defs/permission` |

Fixtures: `<seam>/<name>` under `contract/fixtures/`, with `required-green` epoch and grade. Corpus, under
`srs/`: Contract `architecture-contract.md`, PTY `pty-ownership-model.md`, terminal-size
`terminal-size-and-control.md`, immortality `immortality-mechanism.md`, Frontend `frontend-extensibility-model.md`,
session model `session-model-and-lifecycle.md`, Storage `storage-and-discovery-architecture.md`. Queue =
tsb-core `plans/reports/queue-261007-operator-decisions.md`; verifier = tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`;
v0.3 plan, approval = tsb-core `plans/261006-1015-e2-contract/` `plan.md`, `approval.md` (D6, D7, D31, D32 agreed 2026-10-07).

**Words used here.**

- **Two tokens, never conflated** (Contract §10). The **owner token** is lifecycle authority (end the session, remove
  a client, revoke and reassign the control token). The principal that created the session holds it; it is stored in
  bbolt from creation and never auto-transferred (PTY §7.1; immortality §7; session-record `owner`). Nothing moves it
  at this tag (CHANGELOG known gaps, D8; → ownership-transfer, later). The **control token** is the right to type and to request
  the target's size; one attached client holds it (session-record `control_holder`).
- **Keyboard holder**: the client holding the control token. **An owner's client**: any client whose principal is the
  owner; one principal may have several (rules page "Terms").
- **Escrow**: the core holds the control token, `control_holder` null: no attached client may hold it (none is
  attached, or only view-only ones are: session-record `control_holder`), or the owner revoked it (ws-frames
  `handoff`). `control_holder` names only the first cause (Open item 16).
- **`may_control`**: the client's principal holds `session.join.rw` (session-record `clients`; Storage §9). A
  **view-only client** does not hold the token; one whose principal holds only `session.join.ro` never gets it
  (ws-frames `attached`). Roles are conventions V9.
- **Take-over**: an owner's client taking the token for itself, by typing while not the holder or by `handoff` to its
  own `client_id` (5.53). **Auto-release**: N seconds after the owner's last keystroke the token returns to the client
  that held it before the take-over. N is a `config.yaml` knob; 0, the default, means never (terminal-size §5; D32;
  conventions V7).

Not typing-ownership: the attach (→ session-attach), a client's end (→ session-detach) or removal (→ client-remove), the bytes (→ byte-relay), the size (→ size-control),
the stale prompt (→ stale-screen). Every move of the control token is typing-ownership's, whoever causes it (plan §2.1).

## Path

escrow (1–2): (session-open) · (core-restart) · a move (3–9, 11, then 15–19): SF-CO → SF-CO → CO-DK → (size-control) → (stale-screen), or from
(session-detach | client-remove) at step 4, or from the core's timer at step 11 · refused (12–14): SF-CO → SF-CO

## Steps

Steps 15–19 follow every move, whatever caused it. The contract fixes no order among steps 15–17 (Open item 13).

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | — | CO → CO | — | → session-open | the core | at creation the creating principal holds the owner token, persisted in bbolt; the control token is in escrow, `control_holder` null, since no client is attached (Contract §10; PTY §7.1; immortality §7) | — | the creator, in the 201 (→ session-open) |
| 2 | — | CO → CO | — | → core-restart | the core | after a restart every control token is in escrow: a restart detaches every client and no holder is restored from disk; the owner comes back from bbolt (immortality §7, LOCKED; continuity-state keeps no escrow state) | — | nobody until an attach |
| 3 | SF-CO | SF → CO | WebSocket | → session-attach: `attach` | a client that may_control | with the token in escrow and not revoked (step 6) this client takes it, reason `attach` (PTY §7.1: "in escrow by broker until next attach"). An attach never takes a token another client holds, the owner's attach included; a `session.join.ro` client never takes it | — | the joiner by `attached`'s `control_holder` (→ session-attach); the others by step 16 |
| 4 | — | CO → CO | — | → session-detach, → client-remove | the core | the holder's client detached or was removed: the token passes to another attached client that may_control, or to escrow when none is; reason `detach` (Contract §10; PTY §7.1; session model §11 `ws_heartbeat`; OpenAPI `removeClient`: "its token move is a detach"). Which client: OPEN (Open item 2) | — | steps 15–19 |
| 5 | SF-CO | SF → CO | WebSocket | `handoff`, `to` an attached client's `client_id` · **E2** | keyboard holder | the token moves to `to`, reason `handoff` (PTY §7.1 explicit hand-off) | steps 12–13 | steps 15–19 |
| 6 | SF-CO | SF → CO | WebSocket | `handoff`, `to` null · **E2** | an owner's client | revoke: the token goes to escrow, reason `owner`, and stays there through detaches and attaches until an owner's client reassigns it (step 7) or takes it (steps 8–9) (ws-frames `handoff`; D6) | steps 12–13 | steps 15–19 |
| 7 | SF-CO | SF → CO | WebSocket | `handoff`, `to` another client's `client_id` · **E2** | an owner's client that is not the holder | reassign, from a holder or from escrow, reason `owner`. An owner's client that holds the token and passes it on makes a plain hand-off, reason `handoff` (ws-frames `token`) | steps 12–13 | steps 15–19 |
| 8 | SF-CO | SF → CO | WebSocket | → byte-relay: binary: input bytes (conventions V15) · **E2** | an owner's client that is not the holder | take-over by typing: the token moves to this client, reason `takeover`, before its bytes are handled; the bytes then go on as the holder's (CHANGELOG contract-v0.3 C2, "Taking the keyboard back"; terminal-size §5) | the bytes may still be refused for another reason (→ byte-relay; `stale` → stale-screen) | steps 15–19 |
| 9 | SF-CO | SF → CO | WebSocket | `handoff`, `to` its own `client_id` · **E2** | an owner's client | explicit take-over: step 8 without bytes, reason `takeover` (ws-frames `handoff`; 5.53) | — | steps 15–19 |
| 10 | — | CO → CO | — | — · **E2** | the core | the ordering rule: one client's frames apply in the order sent, a take-over before that client's next frame, so `handoff` to self then `0x03` is one trip (ws-frames top; terminal-size §6). From the instant of any move, input from a client that is not the holder, other than an owner's client (step 8), is refused (step 14): never two typists (terminal-size §2, §5) | — | — |
| 11 | — | CO → CO | — | — · **E2** | the core | auto-release, knob above 0: N seconds after the owner's last keystroke the token returns to the client that held it before the take-over, reason `release` (terminal-size §5; D32). 0 means never. That client gone or no longer may_control: OPEN (Open item 3) | — | steps 15–19 |
| 12 | — | CO → CO | — | — · **E2** | the core | checks a `handoff`: the sender holds the token or is an owner's client; `to` null comes only from an owner's client; `to` names an attached client, and that client may_control (ws-frames `handoff`, `request_refused`) | step 13 | — |
| 13 | SF-CO | CO → SF | WebSocket | `request_refused`, `request` `handoff` · **E2** | the core | nothing moves | reason `not_allowed`, `no_such_client` or `view_only` (table below) | the sender only |
| 14 | SF-CO | CO → SF | WebSocket | `input_refused`, reason `not_typist` · **E2** | the core | input from a client that neither holds the token nor is an owner's client: not forwarded, never held (ws-frames `input_refused`; terminal-size §2). A non-holder's `resize`, the owner's included, is no take-over: it is answered `size` `not_typist` (→ size-control step 2) | — | the sender only |
| 15 | — | CO → CO | — | — | the core | the record's `control_holder` reads the new holder, or null; `owner` and the size do not change (Contract §10; session-record) | — | any reader of the record (→ session-list) |
| 16 | SF-CO | CO → SF | WebSocket | `token` | the core | `holder` in the record's `control_holder` shape; `reason` names the cause, never the destination (ws-frames `token`) | — | every attached client (the joiner at step 3: Open item 12) |
| 17 | CO-DK | CO → DK | file, append-only JSONL | `audit-event.schema.json#/$defs/session_control` · **E2** | the core | `from`, `to` in the `holder` shape (null = escrow), `reason`, `user`: "who could type when", since the recording does not attribute input (audit-event `session_control`) | — | the audit log |
| 18 | — | CO → CO | — | → size-control | the core | no size change, no C3 or VT `resize`, no `size` frame (terminal-size §4, §5; size-control step 14) | — | — |
| 19 | — | CO → CO | — | → stale-screen | the core | while a stale choice is pending the outstanding prompt is void; a client that receives the token gets `stale_prompt` with a new `prompt_id`; to escrow, nobody is asked (ws-frames `stale_prompt`; stale-screen step 8) | — | as stale-screen says |

### Rules for these steps

The core. "Owed": no fixture grades it yet.

1. **Two tokens.** A control-token move MUST NOT change `owner`, and no message moves the owner token at this tag
   (Contract §10; D8). Owed.
2. **Escrow** (steps 1–2). Every session MUST start with `control_holder` null [C1/session-create-new, e0 live]. After
   a restart every control token MUST be in escrow and the core MUST NOT restore a holder from disk
   [C2/token-escrow-after-restart, e1 reference: U2 `restart_test.go`, U11 `kill-core.sh`; C6/no-claim-token-field,
   e1]. With no attached client that may_control, `control_holder` MUST be null [C2/attach-ok-bytes-roundtrip, e0
   live, after the last close; with only view-only clients attached: C2/token-escrow-after-restart].
3. **Grant at attach** (step 3). From escrow not revoked, the next client to attach that may_control MUST take the
   token, and its `attached` MUST name it [C2/attach-ok-bytes-roundtrip; C2/token-escrow-after-restart]. An attach
   MUST NOT take a token another client holds, the owner's attach included, nor one the owner revoked
   [C2/token-owner-revoke-reassign, e2 live].
4. **View-only.** A move MUST NOT give the token to a client whose principal holds only `session.join.ro`: at attach
   [C2/token-escrow-after-restart]; by hand-off or reassign [C2/handoff-refused, e2 live, reason `view_only`]; by
   pass-on or release [owed]. Its `clients` entry MUST read `may_control` false [C2/handoff-refused]. Its input is
   refused `not_typist` [C2/size-request-not-typist, e2 live].
5. **Pass-on** (step 4). When the holder detaches or is removed, the token MUST pass to another attached client that
   may_control, or to escrow when none is, reason `detach` [C2/token-event-on-detach, e1 live; escrow, removal: owed].
6. **Hand-off** (step 5). The core MUST accept `handoff` from the holder's client to an attached client that
   may_control [C2/token-handoff, e2 live].
7. **Revoke and reassign** (steps 6–7). The core MUST accept `handoff` from any owner's client, `to` null or an
   attached client that may_control, reason `owner`, except from an owner's client that holds the token and names
   another client: that is a plain hand-off, reason `handoff` (rule 6; ws-frames `token`). After a revoke an attach
   MUST NOT take the token until an owner's client reassigns or takes it [C2/token-owner-revoke-reassign].
8. **Refusals** (steps 12–13). A `handoff` the core does not apply MUST be answered `request_refused`, request
   `handoff`, with the reason the table below gives, and MUST move nothing; never silently (D7) [C2/handoff-refused:
   all three reasons].
9. **Take-over** (steps 8–9). An owner's client's input while it is not the holder MUST move the token to that client,
   reason `takeover`, and the bytes MUST then be handled as the holder's [C2/owner-takeover-by-typing, e2 live];
   `handoff` to its own `client_id` likewise [C2/owner-takeover-then-ctrl-c, e2 live; C2/token-move-no-resize, e2 live].
10. **Order** (step 10). The core MUST apply one client's frames in the order sent, and a take-over before that
    client's next frame [C2/owner-takeover-then-ctrl-c].
11. **Never two typists** (steps 10, 14). From the instant of a move the core MUST refuse, with `input_refused`
    `not_typist`, the input of every client that is not the holder, other than an owner's client, and MUST NOT forward
    or hold it [C2/owner-takeover-by-typing; C2/token-handoff; after a revoke, a pass-on or a release: owed]. Only
    the v0.3 plan defines the instant (C2 decision 6: the move's place in the core's one ordered input path).
12. **Auto-release** (step 11). The delay MUST be a `config.yaml` knob with no contract field; 0 MUST mean never;
    above 0 the return MUST carry reason `release` [C2/token-auto-release, e2 reference, instance shapes only; its U2
    grader owed by the E2 core plan]. The `token` frame with the previous holder gone: owed (plan §7.4).
13. **Tell** (steps 15–16). Every move MUST set `control_holder` and send `token` to every attached client
    [C2/token-event-on-detach; C2/token-handoff; C2/token-owner-revoke-reassign; C2/owner-takeover-by-typing; for a
    release: owed]. The record read over C1 after a move: owed.
14. **Audit** (step 17). Every move MUST be one `session.control` event with `from`, `to` and `reason` [shape:
    C6/event-vocabulary-v0.3, e2 reference, U2 grader owed; the write per move: owed].
15. **No resize** (step 18). A move MUST NOT change the size or send a resize or a `size` frame
    [C2/token-move-no-resize: a hand-off and a take-over; the other moves owed; → size-control rule 10].
16. **Stale** (step 19). A move while a stale choice is pending MUST void the prompt and give the new holder a new
    `prompt_id` [owed; → stale-screen rule 7].

A consumer (surface, any API client). No fixture grades a consumer, so each MUST is owed.

- **CR1.** A consumer MUST take a `token` frame's `holder` as the holder, whatever the `reason`, and read a reason it
  does not know by `holder` (contract `README.md` "Senders and receivers"; ws-frames top). It knows it holds the token
  when `holder.client_id` equals its own `client_id` from `attached`.
- **CR2.** A consumer MUST NOT send a size request because it got the token: not on a hand-off, a take-over, an
  auto-release or an escrow grant (terminal-size §4; → size-control).
- **CR3.** A consumer MAY suppress input client-side while it does not hold the token (Frontend §9, "View-only
  state"), as a guardrail and never the control (Frontend §8, "Surface attachment mechanism"; 5.41). It MUST NOT
  suppress an owner's client's typing, which takes the keyboard (terminal-size §5; Open item 9). The token UI
  Frontend §9 lists (holder indicator, hand-off, view-only mark, pass-on notice) is surface design over these frames.

## Refusals and who is told

| `request_refused` reason | when | graded |
|---|---|---|
| `not_allowed` | request `handoff`: the sender neither holds the token nor is an owner's client | C2/handoff-refused |
| `no_such_client` | request `handoff`: `to` names no attached client | C2/handoff-refused |
| `view_only` | request `handoff`: `to`'s principal holds only `session.join.ro` | C2/handoff-refused |
| `not_pending` | request `stale_answer` only; `stale_answer` also uses `not_allowed` | → stale-screen |

- A holder that is not an owner's client sending `to` null is outside `handoff`'s grant; no text or fixture names the
  reason (Open item 11). No unknown-reason rule exists for `request_refused` (conventions open item 5).
- Refused input is told to its sender only (step 14). `stale`, `replaying` and `unreachable` are other protocols'
  reasons (byte-relay's table); which wins when several apply is byte-relay's open item 6.
- Every move is told to every attached client (`token`), every reader of the record (→ session-list) and the audit log.

## Security considerations

- **One typist, enforced by the core.** Only the holder's bytes reach the target; every other client's input is
  refused explicitly (steps 10, 14). A surface's suppression is a guardrail, never the control (5.41; CR3). No move
  gives the token to a client whose principal holds only `session.join.ro` (rule 4).
- **The owner's brake.** When a contractor or an agent holds the keyboard, the owner takes it back by typing or by
  `handoff` to self, and a Ctrl-C right behind reaches the target in the same trip (5.53; terminal-size §6). The
  accepted cost (D31): an owner's stray keystroke takes the keyboard. Every client sees the move (`token` `takeover`).
- **Who could type is in the audit, not the recording**, which does not attribute input (audit-event
  `session_control`). The event's shape still awaits D26 (Open item 10).
- **Where the right outlives its reason.** A holder that vanished without a close keeps the token until `ws_heartbeat`
  fires (→ session-detach). A holder whose `session.join.rw` a reload removes keeps typing until it leaves (Open item 6). A core
  restart lifts an owner's revoke: the next client that may type takes the token at attach (Open item 8).
- **Recorded verbatim.** The holder's typing is recorded, passwords included (open_decisions §5; → session-attach rule CR1).

## Open items

1. **A5 / plan §7 G-17 — OPEN: asking for the keyboard.** terminal-size §5: "the owner hands it back explicitly, and
   the other client may ask". No message lets a client without the token ask: the client frames are `attach`,
   `resize`, `quiet_prompt_answer`, `handoff`, `stale_answer` (ws-frames `client`). Options (queue A5): (a) a client
   frame and a server frame in v0.3, answered by an ordinary `handoff` or by nothing; (b) the same in v0.4, additive
   under D1; (c) no message, asking out of band, and terminal-size §5 reworded. Not ruled. Either way the rationale
   page's "left to surface design" is stale (queue V15; verifier G-17; `rationale/` not read here).
2. **Plan §7 G-2 / queue fill S1 — OPEN: who receives the token at a detach.** PTY §7.1: "Auto-transfers to next
   client on holder disconnect"; ws-frames `token`: "passed on or went to escrow". Neither names the client. Fill S1
   proposes the longest-attached client that may type, else escrow, as the E1 core does, and calls it mechanical since
   Contract §10 orders the transfer; Contract §12 lists "how a contested claim resolves" among the choices the broker
   presents. Not ruled.
3. **Plan §7 G-3 / queue fill S2 — OPEN: auto-release with nobody to return to.** The client that held the token
   before the take-over has left or lost `session.join.rw`; also (new, not in plan §7) a take-over from escrow, with no
   previous holder. Fill S2 proposes: nothing fires, the owner keeps the keyboard, no frame. Not ruled. Also
   unspecified (not in plan §7): which keystrokes count as the owner's last (refused ones?), what cancels the timer (a
   later hand-off or revoke), whether it runs after a reassign; the v0.3 plan leaves "timer scope, reset and firing"
   to the core (C2 decision 6), and the contract is silent.
4. **S-5 / queue fill S3 — OPEN: the detach audit.** audit `session_control`: "a detach is from the departing holder
   to null"; ws-frames `token` `detach`: "passed on or went to escrow", and C2/token-event-on-detach expects the new
   holder. When the token passes on, either `to` is null while a client holds it, or a second event follows with no
   reason that fits (`attach` is "the no-clients case"). Fill S3 proposes `from` the leaver `to` the new holder, null
   only on escrow. C6/event-vocabulary-v0.3's `detach` instance has `user` bob and `from` alice's client, against
   "user is the departing holder" (code finding CF5, tsb-core #121). Not ruled.
5. **Plan §7 S-2 — wording only (verifier; queue K2), not yet in the contract.** ws-frames `stale_prompt`: "Token in
   escrow: the next eligible attach gets it", while `handoff` says no attach takes a revoked token. After a revoke
   nobody is asked until the owner reassigns or takes the token (stale-screen open item 1).
6. **B3 / plan §7 G-8 — OPEN: rights that end while connected.** A `policy.yaml` reload that removes the holder's
   `session.join.rw` leaves it typing until it leaves, and no `token` reason names a move off it. Options (queue B3):
   (a) immediate, with a `token` reason the contract names and `clients` refreshing `may_control`; (b) rights checked
   at attach only, and said so. Not ruled. Shared with session-attach, config-reload, login.
7. **Queue fills S4, S5 — OPEN.** S4: after a holder leaves, `token` before `clients` (session-detach open item 3). S5: at a
   core stop, one `session_control` `detach` to null per holder before its 4011, none on a crash (session-detach open item 7;
   core-restart open item 6). Not ruled.
8. **New, the contract against itself (not in plan §7.2): a revoke across a restart.** ws-frames `handoff`: after a
   revoke the token "sits in escrow until the owner reassigns". C2/token-escrow-after-restart and immortality §7:
   after a restart "the first client of a principal that may type takes it", and C6 keeps no escrow state
   (C6/no-claim-token-field). So a restart lifts a revoke. Reported, not resolved.
9. **Plan §7 C-5 — ruled** (5.53; D31, D7 agreed 2026-10-07; verifier C-5); CR3 follows it. The core refuses a
   non-holder's input explicitly, and an owner's typing takes the keyboard. Stale vault text: Frontend §9 "View-only
   state — … input is suppressed client-side" lacks the owner's exception, as does PTY §7.1's "They cannot send
   input" (queue V4, V5).
10. **Approval items leaned on.** D26 (`session.control` shape; queue A7) open; D5 (`may_control`; queue A11) in
    neither list; D8 (no ownership transfer) noted, unlabelled. Ownership transfer is → ownership-transfer (plan §2.2), no tag yet.
11. **Unspecified (not in plan §7) — `handoff` edges.** `to` null from a holder that is not an owner's client
    (`not_allowed`'s text reads on it; no fixture); `to` the current holder; an owner's client that already holds the
    token sending `to` itself; which reason wins when two apply; `session.control` `user` for `attach`, `handoff`,
    `release` (stated for `detach`, `owner`, `takeover` only), and for a removal's `detach` (client-remove open item 4).
12. **Unspecified (not in plan §7) — `token` at attach.** ws-frames `token` goes "to every attached client"; session-attach
    step 17 sends it to every other client, the joiner learning from `attached`; C2/token-escrow-after-restart grades
    only a client already attached. Where it falls is plan §7 G-6 (queue fill S6: after `clients`).
13. **Unspecified (not in plan §7) — order inside a move** (steps 15–17). Plan §2.1 puts CO-DK before SF-CO; no clause.
14. **OPEN, the contract against itself (not in plan §7) — an owner without `session.join.rw`.** The owner may attach without a join right
    (OpenAPI `outputPage`: "the owner too, one rule with ticket minting"; → session-attach step 2), but `attached` says a client
    of a principal holding only `session.join.ro` "is never given the control token", while `handoff` lets any owner's
    client take over (also 5.53). Whether such an owner may take over is not said.
15. **Unspecified (not in plan §7) — a take-over while input is refused for another reason.** Under `stale` the token
    moves and the keystroke is refused (stale-screen rule 5; v0.3 plan C2 decision 10, not in `contract/`); under `replaying`
    or `unreachable` nothing says whether the token moves. The v0.3 plan's "an owner frame applied after [a release]
    is refused `not_typist`" (C2 decision 6) contradicts the contract's take-over by typing; it is not in `contract/`.
16. **OPEN, the contract against itself (not in plan §7) — what escrow is.** session-record `control_holder`: null
    "when the token is in broker escrow because no attached client may hold it — none is attached, or only view-only
    ones are". ws-frames `handoff`: after a revoke "the token sits in escrow until the owner reassigns; an attach does
    not take it", so `control_holder` is null while clients that may_control are attached. The record's text leaves
    out the revoke (Words, step 6). Not in the queue, the review log or the verifier. Not resolved here.

## Picture

pending
