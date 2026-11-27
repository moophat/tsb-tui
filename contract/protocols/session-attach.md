# session-attach — attach a client to a running session

| | |
|---|---|
| **ID** | session-attach |
| **Purpose** | Attach a client to a running session: mint a ticket, open the stream, and give the client the current screen (the prefix and the dump), the list of attached clients and one redraw, then live output. |
| **Status** | built (E0: ticket, `attach`, `attached`, closes 4000–4004, `session.join`); grows E2 (steps 10–16, 18–22: prefix and dump, `clients`, the wiggle) |
| **Corpus clauses** | Contract §10, §11, §12 (presentation exemption); PTY §7.1, §7.2; immortality §5.2, §5.3, §7; terminal-size §1, §2, §4, §7; session model §9 E4, §10, §11 (`quiet_prompt`); Frontend §6.2, §8; open_decisions §5; Storage §9 |
| **Seams** | C1, C2, C3, C4, C6 |
| **Schemas** (contract-v0.3) | OpenAPI `POST /auth/ws-ticket` (`mintWsTicket`), `#/components/schemas/TicketRequest`, `#/components/schemas/Ticket`, `#/components/responses/Unauthenticated`, `Forbidden`, `SessionNotFound`, `InvalidRequest`; `GET /ws/attach` (`attach`) and its `x-websocket`; `ws-frames.schema.json#/$defs/attach`, `#/$defs/attached`, `#/$defs/prefix`, `#/$defs/clients`, `#/x-attach-order`, `#/x-close-codes` (4000, 4001, 4003, 4004, 4011); `common.schema.json#/$defs/ticket`, `#/$defs/u64`; `session-record.schema.json` (`state`, `control_holder`, `clients`, `cols`, `rows`, `recording`); `vt-ipc.schema.json#/$defs/prefix`, `#/$defs/prefix_ready`, `#/$defs/request_id`, `#/$defs/resize`, `#/x-binary-layout` (kind 3); `ipc-control.schema.json#/$defs/resize`; `audit-event.schema.json#/$defs/session_other`; `vt-screen.schema.json` (grading only, never on a wire) |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`) at contract-v0.3.
Fixtures are cited as `<seam>/<name>` under `contract/fixtures/`. Corpus files are under `srs/`:
Contract = `architecture-contract.md`, PTY = `pty-ownership-model.md`, immortality = `immortality-mechanism.md`,
terminal-size = `terminal-size-and-control.md`, session model = `session-model-and-lifecycle.md`, Frontend =
`frontend-extensibility-model.md`, Storage = `storage-and-discovery-architecture.md`. "Verifier" is
tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`; "approval" is tsb-core `plans/261006-1015-e2-contract/approval.md`.

**Words used here.**

- **Client**: one attachment of one principal to one session, on one WebSocket (rules page "Terms").
- **Ticket**: a one-time string from `POST /auth/ws-ticket`, bound to (principal, session), at most 60 s old, held in
  the core's memory, presented once in the first WebSocket frame and never in a URL (`Ticket`; common `ticket`).
- **Keyboard holder, escrow**: the attached client holding the control token (the right to type and to request the
  target's size); the core holding it while no attached client may (Contract §10; PTY §7.1). Every move is typing-ownership's.
- **Grid**: the VT worker's per-session terminal state (Contract §11). **Ground**: the VT parser's state outside any
  escape sequence. **through_seq**: the last output `seq` a dump reflects.
- **Prefix, dump**: the `prefix` text frame, then one binary message of escape sequences that rebuilds the current
  screen (Contract §11: "a synthetic escape-sequence prefix, then … raw bytes thereafter"). Named "binary: dump"
  (conventions V15).
- **Wiggle**: a resize one column narrower and back, so a full-screen program redraws (PTY §7.2).
- **Knob**: a `config.yaml` value; the contract fixes its range, never its value (conventions V7).

Not session-attach: who holds the keyboard, and the escrow grant at attach (→ typing-ownership); a stale prompt to a new holder (→ stale-screen);
the scrollback amount and pages (→ scroll-control); the bytes once live (→ byte-relay); the end of an attachment (→ session-detach).

## Path

attach: (session-open | session-list) → SF-CO → (typing-ownership) → CO-DK → SF-CO → CO-VT → SF-CO → CO-PW → PW-TG → CO-VT → (grid-snapshot) → (byte-relay)
· refused at attach: SF-CO · grid unavailable: SF-CO → (typing-ownership) → CO-DK → SF-CO → CO-VT → SF-CO (4011)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | — | SF → SF | — | → session-open or → session-list | the surface | learns the session id and its record, `recording` included (`TicketRequest`: "creates or selects the session first, then mints"); shows the recording warning (rule CR1). On the device-login branch (device-login) the socket attaches before the surface has read the record, so the warning comes after `attached`, before any input (rule CR6) | — | the operator |
| 2 | SF-CO | SF → CO | HTTPS | `POST /auth/ws-ticket`, body `TicketRequest` | a principal holding the login cookie and `session.join.ro` or `session.join.rw` (Storage §9; Open items 5), or the owner (D14; OpenAPI `outputPage`: "one rule with ticket minting") | none yet | no valid cookie: 401 `unauthenticated`; no `session_id`: 422 `invalid_request`; no such session, or one the principal may not see: 404 `session_not_found`, both alike; visible but not joinable: 403 `forbidden` (rule R1) | the caller |
| 3 | SF-CO | CO → SF | HTTPS | 200 `Ticket` | the core | a ticket bound to (principal, `session_id`), in memory, alive `expires_in` seconds (1–60, the core's configuration); the response carries `v` 1, its shape version, "so a later token format can be told apart" (`Ticket`; `C1/ws-ticket-mint`) | — | the caller |
| 4 | SF-CO | SF → CO | HTTPS | `GET /ws/attach` → 101 | any client | a WebSocket opens; no cookie is needed and nothing in the URL names the session or carries the ticket (`attach` operation) | — | — |
| 5 | SF-CO | SF → CO | WebSocket | `ws-frames.schema.json#/$defs/attach` | the client | the core checks it in `#/x-attach-order`'s order; its `cols`/`rows` (E2) are the client's viewport, information only (rule R5) | close 4000, 4001, 4003 or 4004, the first check that fails (rule R3) | the client |
| 6 | — | CO → CO | — | — | the core | event E4 `attach` (session model §9); the client joins the session's client set and `state` goes `detached` → `live` (common `session_state`); whether an attach moves an `unreachable` session's `state` is not said (Open items 4). No clock moves: "an attach is not a byte" (session model §10; → session-countdown). The sender's attach re-arms `quiet_prompt` (session model §11; → quiet-prompt) | — | any reader of the record (→ session-list) |
| 7 | — | CO → CO | — | → typing-ownership | the core | the first eligible client takes the token from escrow (PTY §7.1; immortality §7); a `session.join.ro` client never does; after an owner revoke no attach takes it (ws-frames `handoff`) | — | step 9, step 17 |
| 8 | CO-DK | CO → DK | file, append-only JSONL | `audit-event.schema.json#/$defs/session_other`, `action` `session.join` | the core | the attach is on the audit record, with `session_type` (audit-event top). Where this write falls among steps 6–23 is unspecified (not in plan §7; Open items 4) | — | readers of the audit log |
| 9 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/attached` | the core | carries the record (size in effect, `control_holder`, `clients`, countdown, `recording`) and this client's `client_id`, so the client learns whether it holds the token (`attached`) | — | the attaching client |
| 10 | CO-VT | CO → VT | unix socket, kind 1 | `vt-ipc.schema.json#/$defs/prefix` · **E2** | the core | PTY `shell` sessions only (F12; ws-frames `prefix`). The C4 socket and message names are D15 and D17, open (Open items 7). The core holds this client's live output from here until the dump is sent, since "a client never gets live bytes without a prefix" | grid unavailable: step 14 | — |
| 11 | CO-VT | VT → CO | unix socket, kind 1 then kind 3 | `#/$defs/prefix_ready`, then the dump as a kind-3 blob, split into chunks above `max_message_bytes` and reassembled by `request_id` (vt-ipc `x-binary-layout`) · **E2** | VT worker | at ground: `through_seq` is the first ground point after every frame received before the request (0 before any output). Not at ground and no ground point within the prefix wait (a knob): `through_seq` is the last frame applied, and the raw bytes since the last ground point follow the dump, so the client's parser ends in the same mid-sequence state, with no 4011 loop (vt-ipc `prefix`, F8) | a VT `error` answer: unspecified (not in plan §7; Open items 4) | the core |
| 12 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/prefix` · **E2** | the core | `through_seq`, `cols`, `rows` from step 11: the dump's geometry. `scrollback` is → scroll-control (required e5) | — | the attaching client |
| 13 | SF-CO | CO → SF | WebSocket | binary: dump (conventions V15) · **E2** | the core | exactly one binary message, at least one byte. A v0.2 client ignores step 12 and renders this as bytes (ws-frames `prefix`) | — | the attaching client |
| 14 | SF-CO | CO → SF | WebSocket | close 4011 `detached_by_server` (`#/x-close-codes`) · **E2** | the core | instead of 12–13 when the grid is unavailable (for example its VT worker is down: → grid-restore, → operator-alerts `vt_unavailable`): the client's output is held until the prefix exists, bounded by a knob, then the socket closes; the session lives (ws-frames `prefix`). What follows is OPEN (D3, G-9) | — | the attaching client |
| 15 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/clients` · **E2** | the core | the whole set of attached clients, the new one included; the record's `clients` holds the same set, never restored from disk (`clients`; D5, in neither list on the approval page: Open items 7) | — | every attached client |
| 16 | SF-CO | CO → SF | WebSocket | → stale-screen: `stale` (`pending`), and `stale_prompt` when step 7 made this client the holder · **E2** | the core | only while a stale choice is pending; `stale` after `clients` (ws-frames `stale`); where `stale_prompt` falls is unspecified (plan §7 G-6; decision queue) | — | the attaching client |
| 17 | SF-CO | CO → SF | WebSocket | → typing-ownership: `token`, reason `attach` | the core | only when step 7 moved the token; the attaching client learns it from step 9's `control_holder`; whether it also gets this frame is open (told column) (`C2/token-escrow-after-restart` grades a client already attached; verifier G-6) | — | every attached client, per ws-frames `token`; whether the joiner also gets it is open (typing-ownership open item 12) |
| 18 | — | CO → CO | — | — · **E2** | the core | decides the wiggle: none when its knob is off, a stale choice is pending, or the protocol has no size (serial); otherwise one (`#/x-attach-order`; PTY §7.2; immortality §5.3). Fires on every attach (OPEN, D10) | — | — |
| 19 | CO-PW | CO → PW | unix socket, one JSON object per line | an `ipc-control.schema.json#/$defs/resize` pair: `cols`−1, then `cols` (at the minimum `cols`, +1 then back) · **E2** | the core | a broker action, never a client request: no `size` answer, no announcement, and not recorded, since ttyrec has no resize (CHANGELOG v0.3 C8 and known gaps: "The attach wiggle is unrecorded"); the size in effect does not change (terminal-size §7; Contract §12 exemption) | — | nobody |
| 20 | PW-TG | PW → TG | SSH | two `window-change` requests (RFC 4254 §6.7), in order · **E2** | worker | a full-screen program redraws; the redraw arrives as live output (PTY §7.2) | — | — |
| 21 | CO-VT | CO → VT | unix socket, kind 1 | a `vt-ipc.schema.json#/$defs/resize` pair, the same two sizes · **E2** | the core | the grid keeps the PTY's size through both halves (vt-ipc top; D17, D18, open: Open items 7) | — | — |
| 22 | — | CO → CO | — | → grid-snapshot · **E2** | the core | a snapshot is forced on every PTY size change, wiggles included (`C4/vt-snapshot-on-resize`) | — | — |
| 23 | — | CO → CO | — | → byte-relay | the core | live output to this client: exactly the frames with `seq` > `through_seq`. A non-`shell` session gets no prefix and output follows `attached` directly (rule R12) | — | the attaching client |

### Rules for these steps

The core and the VT worker. Marks are the fixture's (`required-green` epoch, grade kind).

- **R1 — mint.** The core MUST mint a ticket only for an authenticated principal, bound to one session, with
  `expires_in` at most 60 (`C1/ws-ticket-mint`, e0 live). It MUST refuse a request without `session_id` with 422
  (`C1/ws-ticket-missing-session-422`, e0 live), and MUST answer an absent session and an invisible one alike with
  404, so a refusal never confirms that a session exists (`C1/ws-ticket-unknown-session-404`, e0 live, grades the
  absent one; the invisible one is owed). The 401 and the 403 are owed.
- **R2 — never in a URL.** The core MUST take the ticket only from the first frame and MUST NOT accept one from the
  upgrade URL (`attach` operation; common `ticket`). `C2/attach-ok-bytes-roundtrip` (e0 live) opens with nothing in
  the URL; refusing a URL-borne ticket is owed.
- **R3 — the attach order.** The core MUST apply `#/x-attach-order`'s checks in order and close with the first that
  fails, sending nothing before (no session byte flows on a socket that has not presented a ticket):
  4000 for a first frame that is not a text `attach` (or, on a device-login socket, `device_login_attach`: R3a) with `v` 1 (`C2/attach-first-frame-invalid-close-4000`, e0 live;
  `C2/frame-versioning`, e2 reference), or none within the attach timeout (owed); 4001 for an unknown, expired or
  presented ticket (`C2/attach-bad-ticket-close-4001`, `C2/attach-ticket-expired-close-4001`,
  `C2/attach-ticket-reused-close-4001`, all e0 live); 4003 for a ticket minted for another session
  (`C2/attach-ticket-wrong-session-close-4003`, e0 live) or a principal that may no longer join (owed); 4004 for a
  session that no longer exists or has ended (owed).
- **R3a — the device-login branch (Release B).** A socket whose first frame is `device_login_attach` is checked in the order
  `#/x-attach-order` gives for it (4000, 4001, 4003, 4016, and 4015 as login steps 9 to 14), then carries only the
  device-login frames until `device_login_result`; with outcome `connected` it continues in this protocol's order from
  `attached` (step 6), the creator holding the owner token and taking the control token as any first client does
  (device-login step 21) [C2/device-login-ticket-kinds, live; C2/device-login-relay, reference, owed].
- **R4 — one presentation.** The core MUST burn a ticket on its first presentation, valid or not for the session
  named (ws-frames `attach`). Reuse after a successful attach: `C2/attach-ticket-reused-close-4001`; after a 4003: owed.
- **R5 — the viewport is information.** An attach MUST NOT change the target's size or the record's `cols`/`rows`,
  whoever attaches and whatever `cols`/`rows` it reports (ws-frames `attach`; D2 agreed 2026-10-07; terminal-size
  §2, §4: an escrow grant never sends a request). Owed: `C2/token-move-no-resize` grades a hand-off and a take-over only.
- **R6 — `attached`.** The core MUST send `attached` with this client's `client_id` and the closed record, naming
  this client in `control_holder` when step 7 gave it the token, and the record MUST read `live`
  (`C2/attach-ok-bytes-roundtrip`, e0 live, whose run validates frames against `C2/ws-frames-shape-v0.3`, e2;
  `C2/token-escrow-after-restart`, e1 reference).
- **R7 — prefix, dump, live (E2).** For a PTY `shell` session the core MUST send `prefix`, then exactly one binary
  message of at least one byte, then exactly the output frames with `seq` > `through_seq`, in order, none repeated,
  none skipped, with `through_seq` 0 before any output (ws-frames `prefix`; Contract §11). `C2/attach-prefix-mid-session`
  (e2 live); `C2/prefix-after-restart` (e2 reference) after a core or VT restart; each client gets its own prefix
  (`C2/per-client-output`, e2 reference; its U2 grader owed).
- **R8 — getting the dump (E2).** The core MUST get every dump from the VT worker by `prefix` and MUST put
  `prefix_ready`'s `through_seq`, `cols` and `rows` in the `prefix` frame, the reassembled blob in the dump. The VT
  worker MUST answer with the same `request_id`, at the ground point step 11 names, or after the prefix wait with the
  raw bytes appended (`C4/vt-prefix-at-ground`, `C4/vt-prefix-mid-sequence`, e2 reference; U2 and U4 graders owed).
- **R9 — what the dump carries (E2).** The VT worker MUST emit exactly what vt-ipc `prefix` lists under D20, open (C4
  decision 6): the carried state, each mode's full state, the ordering, and the states it never carries. The dump
  MUST be **faithful**: plain escape sequences that a standard terminal parser rebuilds into the same screen. That is
  checked in the core by a libghostty-vt round trip (the dump re-fed to a fresh engine gives the same grid and modes)
  and at each surface by its own renderer, the TUI first; a renderer that disagrees with libghostty-vt is a surface
  defect, never a core change, and no surface renderer is part of the contract (immortality §5.2; 5.47 corrected).
  Graded by `C2/attach-prefix-modes` (e2 live, each mode emitted by `printf` on the target shell), and by U4's round trip on `C4/golden-alt-screen`,
  `golden-input-modes`, `golden-mode-reset`, `golden-reflow`, `golden-resize-mid-stream`, `golden-sgr`,
  `golden-wide-chars` (e2 reference; no expected screen is stored, F14) with `C4/vt-screen-shape` as the oracle's shape.
- **R10 — the grid unavailable (E2).** The core MUST NOT send live bytes to a client before its prefix; it MUST hold
  that client's output until the prefix exists, for at most the held-output knob, then close 4011 (ws-frames
  `prefix`; `#/x-close-codes` 4011). Owed (plan §7.4).
- **R11 — `clients` (E2).** On every attach the core MUST send `clients` to every attached client, the new one
  included, after the dump, and the record's `clients` MUST hold the same set (`C2/clients-frame`, e2 live, grades
  the frame; the record's field is owed). Both lean on D5 (Open items 7).
- **R12 — no prefix.** A non-`shell` session MUST get no `prefix` (ws-frames `prefix`). Owed: no request at
  contract-v0.3 opens one (→ session-open).
- **R13 — nothing in between: unspecified (decision queue).** Where a server frame other than the prefix and the dump
  falls between `attached` and `clients` (`token`, `size`, `notice`, `quiet_prompt`) is unspecified: plan §7 G-6. The
  contract fixes only `attached`, then the prefix and the dump, then `clients`, then the wiggle (`#/x-attach-order`),
  and puts `stale` after `clients` (ws-frames `stale`). The verifier proposes a contract sentence (G-6); it is not in
  the contract.
- **R14 — the wiggle (E2).** Unless step 18 rules it out, the core MUST issue exactly one wiggle per attach: a C3
  `resize` pair, `cols`−1 then `cols`, never below common `cols`'s minimum (there, +1 then back), sent after
  `clients`; it MUST NOT answer it with `size`, announce it or record it (CHANGELOG v0.3 C8; the recording half
  owed). The worker MUST deliver each half as its own
  `window-change`, in order, never coalesced (ipc-control `resize`). `C3/attach-redraw-wiggle` (e2 reference; the
  harness rig owed). Both halves reach the grid (`C4/vt-snapshot-on-resize`, e2 reference).
- **R15 — audit.** The core MUST write one `session.join` per accepted attach, carrying `session_type`
  (audit-event top). Shape: `C6/event-vocabulary` (e0, audit artefacts); the write per attach is owed.

A consumer (surface, any API client). No fixture grades a consumer, so each MUST below is owed.

- **CR1.** Before it attaches by a session ticket or sends input, a consumer MUST show `session.recording.warning_text`
  when `recording.active` is true (session-record `recording`; open_decisions §5, LOCKED). A device-login socket attaches
  before the consumer has seen `recording`: rule CR6 is its rule.
- **CR2.** A consumer MUST present the ticket only in the `attach` frame, never in a URL, and MUST mint a new ticket for
  every attach (common `ticket`; Frontend §8 "Surface attachment mechanism").
- **CR3.** After `prefix` a consumer MUST read the next binary message as the dump and render it at `prefix.cols` ×
  `prefix.rows`; with no `prefix`, output follows `attached` directly (contract `README.md` "Senders and receivers").
- **CR4.** A consumer MUST NOT treat its attach viewport as a size request: to change the target's size it holds the
  keyboard and sends `resize` (D2: "join, get the keyboard, then request a size"; → size-control).
- **CR6 (Release B).** On `attached` after a device login the consumer MUST show `session.recording.warning_text` when
  `recording.active` is true before it sends any input: the device login ran before the consumer had read the record, so
  CR1's "before it attaches" could not hold [owed].
- **CR5.** A consumer MAY zoom, pan, scroll and reflow at its own viewport; none of it reaches the target
  (terminal-size §1; Contract §11: "Clients own all rendering and reflow").

### Rulings followed, and text still to fix

- **C-4, ruled (D2, agreed 2026-10-07).** The attach viewport goes to the core as information only, never a size
  request; no step consumes it. Stale: vault terminal-size §1, "It is local to the surface, never reaches the broker
  or the target". Fix (verifier): "except the viewport a client reports at attach, as information only". Optionally
  ws-frames `attach`'s "a surface rendering fact" becomes "information only".
- **S-3, wording only (D33, agreed).** `resize` is always a target size request. Stale: ws-frames `attach`, "Absent
  (a v0.2 client), the client has no declared viewport until its first `resize`", and the same line in
  `contract/CHANGELOG.md` (contract-v0.3, C2, second bullet); also spine `development-plan.md` "resize per 5.50 — the
  holder's viewport only". Fix: "Absent (a v0.2 client), the client reports no viewport; its `resize` is a target size request".
- **G-6, wording only (verifier), unspecified until the operator rules (decision queue).** The joiner learns the
  holder and the countdown from `attached.session`; rule R13 states nothing about the slot of frames caused by
  others. Fix `#/x-attach-order`: "between `attached` and `clients` nothing else is
  sent; any other server frame comes after `clients`".

## Refusals and who is told

- Every refusal goes to the caller or the attaching client only: an HTTP answer at steps 2–3, a close code at step 5.
  The audit vocabulary has no event for a refused mint or attach (`audit-event.schema.json` `$defs`).
- A consumer reads a 4xxx code it does not know as 4010 (ws-frames top; conventions V3). 4011 is a detach with the
  session alive (→ session-detach), and after step 14 nothing stops the client from attaching again (OPEN, D3).
- Other clients learn of a join from `clients` (step 15) and, when the token moved, `token` (step 17). A reader of
  the record sees `state`, `clients` and `control_holder` change (→ session-list).
- No answer or close carries host detail (OpenAPI `info`; conventions V11).

## Private target disclosure

When a principal is authorized to see or join an existing session on a target whose share list does not name them, every session record, list and attach answer exposes its target ID only. It must not expose the target's name, host or definition. Session access does not confer inventory access or a credential.

## Security considerations

- **The ticket.** A login cookie mints it (SameSite=Strict, `securitySchemes.session`); the WebSocket needs no
  cookie, so the ticket is the only proof on the upgrade. It is one-time, burned on its first presentation valid or
  not, short-lived (at most 60 s), bound to one principal and one session, held in the core's memory only (`Ticket`), and never in a URL (rules R1–R4;
  Frontend §8).
- **The recording warning applies to every joiner.** Once a joiner holds the keyboard, what it types is recorded
  verbatim, passwords included (open_decisions §5); its surface shows the fixed warning first (rule CR1).
- **Who may type.** Joining never gives a `session.join.ro` client the keyboard, and a joiner's viewport never
  resizes the target (rules R5, R6; → typing-ownership). The wiggle is presentation, not input (PTY §7.2; Contract §12).
- **Cell content stays in the broker.** The dump, sent only to a client that passed the attach order, is the one
  cell-bearing form; no endpoint serves cells or rows, and the VT socket admits only the TSB service uid (Contract
  §11; vt-ipc top; conventions V10).

## Open items

1. **D3 / plan §7 G-9 — OPEN.** While the VT worker is down a joining client gets `attached`, is held, then closed
   4011 (step 14), and 4011 invites a re-attach, so a surface can loop until the VT worker returns. Options
   (approval "D3"; verifier G-9): (a) wait then refuse, as written, plus a receiver rule in the contract README to
   back off after a 4011 that follows `attached` with no `prefix`; (b) join with the stale flow (→ stale-screen), a contract
   change before the tag that removes the loop. Approval and verifier recommend (b). Not ruled.
2. **D10 — OPEN.** The wiggle fires on every join since 5.50, viewers and agents included, and each forces a snapshot
   (steps 18–22). Options (approval, "Status after the operator's review"): (a) keep; (b) at most one per N seconds
   per session, a knob; (c) only on request. The orchestrator leans (b). Not ruled.
3. **Plan §7 G-8 — OPEN.** A `policy.yaml` reload (Storage §2.4) that removes an attached client's right to join, or
   the write right of the keyboard holder: no rule; 4003 covers attach time only. Options (verifier G-8): (a) close
   such a client 4003 and move the keyboard off a holder that lost `session.join.rw`, with a `token` reason the
   contract names and `clients` updating `may_control`; (b) rights are checked at attach only, and the contract says
   so. The verifier recommends (a), as the alert stream already ends on loss of broker.health. Shared with config-reload
   and typing-ownership. Not ruled.
4. **Unspecified (not in plan §7):** where the `session.join` write falls (step 8); what the core does when the
   VT answers `prefix` with `error` (step 11); whether a holder's input is forwarded while its own prefix is still
   pending (steps 10–14); whether `attached.session.clients` already lists the joiner; whether an `unreachable`
   session, which is attachable, gets a prefix (common `session_state`), and whether an attach moves its `state`
   to `live` (step 6), which the record's `oneOf` would make drop `unreachable_reason` (`C1/session-unreachable`:
   "Not detached: detached has no clients, unreachable has no data path the core reads"); a mint for an ended
   session (200 then 4004, or 404); whether the wiggle obeys the target's floor and ceiling or fires on a pinned
   target (terminal-size §3 clamps requests, and the wiggle is not one); no knob names the attach timeout behind
   4000 (conventions V7 lists none); whether the Origin check applies to the mint or the upgrade.
5. **Unspecified (not in plan §7):** which principals may join. Storage §9 names `session.join.ro` and
   `session.join.rw`; how the `targets:` filter and "may see" apply to a session
   rather than a target, is stated nowhere (session-list's first open item).
6. **Ambiguity (not in plan §7.2):** `#/x-attach-order` puts `clients` after the dump for a `shell` session and says
   a non-`shell` session's "output follows `attached` directly", while `clients` goes "to every attached client on
   each attach". Where `clients` falls for a non-`shell` session is not said; no v0.3 request opens one.
7. **OPEN — approval items leaned on.** D5 (`clients` on record and frame, `may_control`: steps 15, R11) is in
   neither the agreed nor the open list on the approval page (queue A11). D15 (the VT socket directory) and D17
   (the C4 message names: `prefix`, `prefix_ready`, `resize`, steps 10, 11, 21) are open southbound items (approval
   "Southbound items"; queue A8). Not ruled.

## Picture

pending
