# size-control — the target's terminal size, on the keyboard holder's request

| | |
|---|---|
| **ID** | size-control |
| **Purpose** | Set the target's terminal size on the keyboard holder's request — clamped, coalesced and answered — and tell every attached client the size in effect. |
| **Status** | built (E0: the holder's `resize`, the C3 `resize`, the SSH window-change); grows E2 (steps 2–6 and 9–12, the creation clamp); E3 (`not_resizable` required, telnet NAWS) |
| **Corpus clauses** | terminal-size §1–§7; PTY §7.1, §7.2; Contract §10, §11, §12; lang-arch §3.2, §7.3; session model §9 E1; Storage §8.6; immortality §5.3 |
| **Seams** | C1, C2, C3, C4 (C6 through → grid-snapshot) |
| **Schemas** (contract-v0.3) | `ws-frames.schema.json` (top: the ordering rule), `#/$defs/resize`, `#/$defs/size`, `#/$defs/attached`, `#/$defs/attach` (S-3 only); `session-record.schema.json#/properties/cols`, `#/properties/rows`, `#/properties/resizable`, `#/properties/pinned`; `ipc-control.schema.json#/$defs/resize`; `vt-ipc.schema.json#/$defs/resize`; `common.schema.json#/$defs/cols`, `#/$defs/rows`; OpenAPI `#/components/schemas/SessionCreateRequest`, `#/components/schemas/SessionCreateResponse` (G-5 only) |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`) at contract-v0.3;
fixtures are `<seam>/<name>` under `contract/fixtures/`. Corpus files, under `srs/`: terminal-size =
`terminal-size-and-control.md`, PTY = `pty-ownership-model.md`, Contract = `architecture-contract.md`, lang-arch =
`language-architecture-decision.md`, session model = `session-model-and-lifecycle.md`, Storage =
`storage-and-discovery-architecture.md`, immortality = `immortality-mechanism.md`. D-lines: v0.3 approval page.

**Words used here.** A **target size request** is a `resize` frame asking the core to change the target PTY's size,
never a report of how the surface renders (terminal-size §1; D33). There is no REST resize: after the session opens,
the holder's C2 `resize` is the only size request (session-record `cols`; CHANGELOG contract-v0.3 C1). The **size in effect** is the PTY's one size, the
size the target sees. The **keyboard holder** is the one attached client holding the control token, the right to type
and to request the size; in **escrow** the core holds it (Contract §10; PTY §7.1). **Floor, ceiling, threshold** and
**coalescing window** are `config.yaml` knobs with per-target overrides, with no contract field or value (terminal-size
§3; D36; conventions V7). A **window-change** is the SSH request that carries a new size (RFC 4254 §6.7); telnet's is
NAWS (RFC 1073); serial has none (terminal-size top).

## Path

request (1–13): SF-CO → CO-PW → PW-TG → CO-VT → (grid-snapshot) → SF-CO · token move (14): (typing-ownership) · session open (15–18):
SF-CO → CO-PW → SF-CO, inside session-open

## Steps

The contract fixes "coalesces, then answers each request in order" (CHANGELOG C2); steps 7, 9–11's order is open.

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | WebSocket | `ws-frames.schema.json#/$defs/resize` | any attached client; only the keyboard holder's counts (→ typing-ownership) | a target size request. Not event E1, so it moves no timer clock (session model §9 E1 lists `resize` as no event). One client's frames apply in the order sent (ws-frames top; terminal-size §6) | step 2 | — |
| 2 | — | CO → CO | — | — | the core | checks that the sender holds the control token | not the holder: step 11 answers `not_typist` with the size in effect; steps 3–10 do not run · **E2** | the requester |
| 3 | — | CO → CO | — | — | the core | reads the record's `resizable` and `pinned` · **E2** | `resizable` false: answer `not_resizable` (required **E3**); `pinned` true: answer `pinned`; each with the size that stands, and steps 4–10 do not run | the requester |
| 4 | — | CO → CO | — | — | the core | clamps cols and rows to the target's floor and ceiling (terminal-size §3). Mechanical arbitration under Contract §12's presentation exemption: no choice is presented · **E2** | — | — |
| 5 | — | CO → CO | — | — | the core | coalesces a burst of requests, the last one winning, as a backstop to the surface's debounce (terminal-size §3). Held in memory only: "in-flight resize arbitration" is regenerable state (Storage §8.6) · **E2** | — | — |
| 6 | — | CO → CO | — | — | the core | a change no larger than the propagation threshold is not sent to the target (terminal-size §3); steps 7–10 do not run · **E2** | its answer is OPEN (G-4) | — |
| 7 | CO-PW | CO → PW | unix socket, one JSON object per line | `ipc-control.schema.json#/$defs/resize` | the core | the applied size, naming the session, because a connection carries N (lang-arch §3.2: resize is out-of-band) | no C3 event answers a delivered one; one that fails is answered `error` naming the session (ipc-control `error`), its code unspecified (Open items, "Unspecified" (8)) | — |
| 8 | PW-TG | PW → TG | SSH; telnet from E3 | SSH `window-change`; telnet NAWS; serial: nothing | worker | the target's PTY takes the size; a full-screen program redraws (PTY §7.2). Telnet NAWS; a peer that refused NAWS makes the session not resizable (`channel_opened` `resizable`) [C2/size-not-resizable, required e3, its live shape owed by the E3 core plan]. A `resize` that reaches a telnet session whose peer refused NAWS, or turned it off with DONT NAWS, is dropped by the worker with no answer | — | the target |
| 9 | CO-VT | CO → VT | unix socket, kind 1 | `vt-ipc.schema.json#/$defs/resize` · **E2** | the core | the session's grid takes the PTY's size; the VT worker applies a session's resizes and output frames in arrival order (vt-ipc top; D18, open: Open items) | `error` `unknown_session` for a session with no grid (vt-ipc `resize`) | — |
| 10 | — | CO → CO | — | → grid-snapshot · **E2** | the core | a snapshot forced at the new size, so no size change lies between the last snapshot and a crash (CHANGELOG C6; `C4/vt-snapshot-on-resize`) | a snapshot that cannot be taken: grid-snapshot's (`vt_unavailable`, → operator-alerts) | — |
| 11 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/size` · **E2** | the core | one answer per request, in request order: `cols`/`rows` the size in effect after it, `reason` why | — | the requester |
| 12 | SF-CO | CO → SF | WebSocket | `ws-frames.schema.json#/$defs/size` · **E2** | the core | the applied size, to every attached client. The record's `cols`/`rows` now read it (→ session-list); a client attaching later reads it in `attached` (`x-attach-order`; → session-attach) | — | every attached client; any principal reading the record |
| 13 | — | SF → SF | — | — | the surface | renders the size in effect at its own font, zoom and pan, and shows the result (terminal-size §1, §4) | — | the operator |
| 14 | — | CO → CO | — | → typing-ownership | the core | a token move (hand-off, owner revoke or reassign, take-over, auto-release, escrow grant, detach) or a token in escrow: the size stays, and no resize and no `size` frame is sent (terminal-size §2, §4, §5) | — | no `size` frame; the `token` frame is typing-ownership's |
| 15 | SF-CO | SF → CO | HTTPS | → session-open: `POST /sessions`, `SessionCreateRequest` (`cols`, `rows` required) | the creator | the creator's size, a size request at session open (terminal-size §4) | session-open's | — |
| 16 | — | CO → CO | — | — | the core | clamps it to the floor and ceiling like any request (terminal-size §3–§4; verifier G-5) · **E2** | — | — |
| 17 | CO-PW | CO → PW | unix socket | → session-open: `ipc-control.schema.json#/$defs/open_shell` | the core | carries the clamped size; the worker's `pty-req` and the VT `open` follow at that size (session-open steps 13, 16; lang-arch §7.3) | session-open's | — |
| 18 | SF-CO | CO → SF | HTTPS | → session-open: 201 `SessionCreateResponse` | the core | `session.cols`/`rows` are the size applied, "the terminal size in effect" (session-record `cols`) | — | the creator |

### Rules for the core and the worker ("owed": no fixture grades it yet)

1. **Every request is answered** (steps 1, 11). The core MUST answer every `resize` with a `size` frame to the
   requester and MUST NOT drop one silently (terminal-size §2–§3; D34, D35) [C2/size-request-applied,
   C2/size-request-clamped, C2/size-request-not-typist; a burst: C2/size-coalesced, reference, live grading owed].
2. **Only the holder** (step 2). A request from a client without the control token MUST be answered `size` reason
   `not_typist`, carrying the size in effect, with the PTY unchanged; it MUST NOT be answered `request_refused`
   (ws-frames `resize`, `request_refused`; D35) [C2/size-request-not-typist]. `not_typist` is decided before
   `resizable` is read [C2/size-not-resizable, instance note].
3. **No size, fixed size** (step 3). With `resizable` false the core MUST answer every request `not_resizable`
   [C2/size-not-resizable: published e2, required e3; live grading owed by the E3 core plan, D38]. With floor equal
   to ceiling the size is pinned: the core MUST answer every request `pinned` with the fixed size, and the record
   reads `pinned` true and `resizable` true [C2/size-pinned, reference; live grading owed by U2]. The core MUST put
   both fields on every record not ended (session-record) [shape: C1/session-record-shape-v0.3; presence owed].
4. **Clamp** (step 4). A request below the floor MUST be answered `clamped_floor` with the floor's size, one above
   the ceiling `clamped_ceiling` with the ceiling's [C2/size-request-clamped; bounds: contract `README.md` "Inputs"].
   The floor is a proper terminal size; the ceiling sits above any real screen and may be off (terminal-size §3).
5. **Coalesce** (steps 5, 11). The core MUST apply the last request of a burst and answer every request of it in
   request order, each with the size that applied and the reason that size got [C2/size-coalesced, reference; live
   grading owed by U2]. No reason names coalescing [C2/size-coalesced, invalid instance `coalesced`].
6. **Threshold** (step 6). The core MUST NOT send the target a change no larger than the threshold
   (terminal-size §3) [owed]. What it answers is OPEN (G-4).
7. **Southbound** (steps 7–8). The core MUST send each applied change as one C3 `resize` naming its session
   [C3/control-resize-close-disconnect, e0]. The worker MUST deliver each `resize` to the target as its own
   window-change, in order, and MUST NOT coalesce two (ipc-control `resize`) [owed: C3/attach-redraw-wiggle states
   it, reference, its graders owed]. Serial has no size; nothing is sent (terminal-size top; PTY §7.2).
8. **Grid** (steps 9–10). The core MUST send every PTY size change to the VT worker, so the grid always has the
   PTY's size, and force a snapshot at it (vt-ipc top, `resize`; CHANGELOG C6) [C4/vt-snapshot-on-resize,
   reference; U2 and U4 graders owed]. The VT worker MUST apply resizes and output frames in arrival order
   [C4/golden-resize-mid-stream, U4 grader owed] (D18, open: Open items).
9. **Tell everyone** (step 12). The core MUST send the applied size to every attached client, and the record's
   `cols`/`rows` MUST read the size in effect after the request [C2/size-request-applied; C2/size-request-clamped;
   C2/resize-control, e0].
10. **Token moves never resize** (step 14). The core MUST NOT change the size, send a C3 or VT `resize`, or send a
    `size` frame because the token moved [C2/token-move-no-resize: hand-off and take-over; auto-release, escrow
    grant and detach owed]. With the token in escrow the PTY MUST keep its size (terminal-size §2) [owed].
11. **Session open** (steps 15–18). The core MUST clamp the creator's size to the floor and ceiling before
    `open_shell`, and the 201's record MUST carry the size applied [both owed: every fixture creates in bounds].

A size request is not audited (CHANGELOG C6; D26, which the approval page still lists open), and neither an applied
size nor the attach wiggle is recorded: ttyrec has no resize (CHANGELOG C8; CHANGELOG known gaps, "The attach wiggle
is unrecorded").

### Rules for surfaces (no fixture grades a consumer: every MUST here is owed)

- A surface MUST NOT send a `resize` for its own rendering: font size, zoom or pinch, rotation, an on-screen
  keyboard, scrolling, panning, or a window resize while auto-request is off (terminal-size §1). It MAY show a target
  of any size at any font, scrolling and panning (terminal-size §1); a view-only client reflows at its own viewport
  (terminal-size §7; Contract §11).
- A surface MAY offer an explicit request, labelled with the size it asks for; the label MUST NOT hide that this is
  a request to the target that may come back different (terminal-size §4).
- A surface MAY auto-request on its own window resize: on by default for a desktop browser and the TUI, off on a
  phone, switchable by each user per surface (terminal-size §4; Frontend §9). It MUST debounce, and MUST NOT
  auto-request on zoom or a font change (terminal-size §4). At session open it sends the creator's size (§4).
- A surface MUST NOT send a request because it got the token: not on a hand-off, a take-over, an auto-release or an
  escrow grant (terminal-size §4).
- After a clamped answer a surface MUST NOT ask again until its own window changes (terminal-size §3; contract
  `README.md` "Senders and receivers").
- A surface MUST show the target's real size, quietly, and the result of each request, with the reason when it is
  not `applied` (terminal-size §4; contract `README.md` "Senders and receivers").
- A surface that meets a `reason` it does not know MUST read `cols`/`rows` as the size in effect (ws-frames `size`;
  conventions V2, V5).

**Not size-control.** The redraw wiggle — one column narrower and back — is session-attach's (at attach) and stale-screen's (the holder's
`wiggle` choice). It is a C3 `resize` pair the core issues, never a size request: never answered with `size`, never
announced, never recorded, never fired while a stale choice is pending (PTY §7.2; terminal-size §7; ws-frames `x-attach-order`;
immortality §5.3; CHANGELOG C8) [C3/attach-redraw-wiggle]. It uses steps 7–10's messages ("wiggles included", vt-ipc `resize`).
The `attach` frame's `cols`/`rows` is a viewport report, never a request (D2, D33; → session-attach).

## Refusals and who is told

| `size` reason | when | step | told |
|---|---|---|---|
| `applied` | the request was applied | 11–12 | the requester; the applied size to every attached client |
| `clamped_floor`, `clamped_ceiling` | clamped up to the floor, down to the ceiling | 4, 11–12 | the same |
| `pinned` | floor equals ceiling; the size is fixed | 3, 11 | the requester |
| `not_resizable` | the protocol has no size (serial, when a later release connects it), or a telnet peer that refused NAWS (`channel_opened` `resizable`) | 3, 11 | the requester |
| `not_typist` | the requester does not hold the control token; the one refusal path for a size request | 2, 11 | the requester |

Every answer carries the size in effect, a refusal included (ws-frames `size`; registry: conventions V5). A request
inside the threshold has no reason (G-4).

## Security considerations

- **Only the keyboard holder changes the size** (Contract §10; PTY §7.1; terminal-size §2). A client of a principal
  holding only `session.join.ro` is never given the token (ws-frames `attached`), so its every request is answered
  `not_typist` and changes nothing [C2/size-request-not-typist]. Every refusal is explicit, never silent.
- **Bounds**: the ceiling guards against a buggy client asking for 10 000 columns (terminal-size §3); every request
  is within 1–65535 (common `cols`, `rows`); coalescing is a backstop against a burst.
- **Presentation, not input**: a size change is out-of-band (lang-arch §3.2), never bytes in the target's input
  stream, and never an operational decision (Contract §12). A `size` frame carries no host detail (conventions V11).

## Open items

- **G-4 — OPEN (plan §7 G-4; verifier: real).** terminal-size §3 says "A change no larger than the threshold is not
  sent to the target", and also that every request is answered with "the size actually applied and why" from six
  reasons (D34; ws-frames `size`). None of the six fits. The verifier's options: (a) contract only — answer
  `applied` with the unchanged size, plus a receiver rule; cheap, but misleading. (b) A seventh reason, such as
  `below_threshold`: one corpus line (terminal-size §3) and one contract value, additive under the receiver rule
  "an unknown reason → read cols/rows"; **the verifier recommends (b)**. (c) Vault and D36: drop the threshold for
  explicit requests, keeping coalescing, so it applies only to the redraw wiggle (PTY §7.2). Not ruled.
- **Wording only, agreed rules the contract text lags:**
  - **S-3.** `resize` never declares a viewport (D2, D33, agreed). Still stale: ws-frames `attach` — "Absent (a v0.2
    client), the client has no declared viewport until its first `resize`" — and the same sentence in CHANGELOG
    contract-v0.3 C2, bullet 2; also the spine's `development-plan.md` v0.3 row, "resize per 5.50 — the holder's
    viewport only" (the orchestrator's). Fix proposed in the verifier report.
  - **G-5.** The creator's size is a size request, so it is clamped (terminal-size §3–§4), and the 201's record
    carries the size applied (steps 16, 18). The contract is silent: neither `POST /sessions` nor
    `SessionCreateRequest` says so. session-open step 12 points here.
- **D18 — open** (approval page, "Southbound items — D15–D21 …"; queue A8). Step 9 and rule 8's arrival order rest
  on it; the C4 `resize` itself on D15–D17 (socket, framing, message names), in the same open set.
- **D37 (last-wins, every request answered in order) is not on the approval page's agreed list**, nor its open one.
  Rule 5 rests on terminal-size §3 and C2/size-coalesced; "in request order" on D37 and ws-frames top's order rule.
- **Unspecified (not in plan §7):** (1) the `reason` for a request clamped one way in cols and the other in rows —
  an answer has one; (2) how a change is measured against the threshold, and whether before or after clamping;
  (3) the broadcast — whether a refusal is copied to other clients, how many frames with which reason they get for a
  burst, whether the requester's answer is also its copy (C2/size-request-applied grades one applied request);
  (4) the order of steps 7, 9, 10 and 11, and where the VT `resize` falls among the output frames; (5) whether a
  holder's request while a stale choice is pending, which "is still answered" (CHANGELOG C2), reaches the target —
  immortality §5.3 forbids only what TSB sends on its own; (6) where a restarted core reads the size in effect: the
  durable session record has none, `grid_snapshot_record` has the size at the last snapshot (shell sessions only);
  (7) whether a grid restored after the VT worker was down is resized to the PTY's size (→ grid-restore); (8) what the
  worker answers to a `resize` it cannot deliver: an `error` naming the session (ipc-control `error`); which code
  (`not_supported`, `internal`, …) is unspecified.

## Picture

pending
