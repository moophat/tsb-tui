# stale-screen — the screen is stale: refuse input, let the keyboard holder choose

| | |
|---|---|
| **ID** | stale-screen |
| **Purpose** | When output was lost, or a grid cannot be rebuilt, tell every client the screen is stale, refuse input, and let the keyboard holder choose the remedy; record the choice as broker-originated. |
| **Status** | E2. PTY `shell` sessions only: no other session type has a grid (vt-ipc top; CHANGELOG C2 bullet 11) |
| **Called by** | reattach-handshake step 12 (cause `gap`); grid-restore and byte-relay step 9 (cause `grid_lost`, from the VT worker's `seq_jump` or `snapshot_incompatible`); core-restart (a pending choice resumes after a restart) |
| **Corpus clauses** | immortality §4.3, §5.3, §7; PTY §7.1, §7.2; Contract §10, §12, §13, §15.3; session model §9 E1, §10, §11 (`quiet_prompt`, `unattended`); terminal-size §2. Rulings 5.46, 5.49, 5.52, 5.53 (`decisions.md`) |
| **Seams** | C2, C3, C4, C6, C8 |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ws-frames.schema.json#/$defs/stale`, `#/$defs/stale_prompt`, `#/$defs/stale_answer`, `#/$defs/stale_choice`, `#/$defs/stale_choice_text`, `#/$defs/request_refused`, `#/$defs/input_refused`, `#/x-attach-order`; `continuity-state.schema.json#/$defs/session_record` (`stale_seq`, `stale_cause`), `#/$defs/grid_snapshot_record`; `audit-event.schema.json#/$defs/session_stale_choice`; `ttyrec.schema.json#/$defs/broker_marker`, `#/$defs/data_frame`, `#/x-broker-line`, `#/x-sidecar`; `ipc-control.schema.json#/$defs/resize`; `ipc-data-frame.schema.json` (top: the input frame); `vt-ipc.schema.json#/$defs/resize`, `#/$defs/error` |

Fixtures (`<seam>/<name>` under `contract/fixtures/`) are required-green e2, except reattach-handshake's `C8/gap-twice-across-restart` (e1): C2, C3, C4, C6 ones reference-graded
with U2/U11 live graders owed, C8 ones graded by the artefact check `recordings-broker-marker`. Corpus files are under
`srs/`. "v0.3 plan" is tsb-core `plans/261006-1015-e2-contract/plan.md` and "approval" its `approval.md`
(D-lines agreed 2026-10-07: D11, D12, D25, D27, D39). "Verifier" is tsb-core `plans/reports/verify-261007-tsb-protocols-conflicts.md`.

**Words used here.**

- **Stale**: the grid, the core's copy of the session's screen, may not match the target's, because output was lost
  (a declared gap) or the grid could not be rebuilt though no byte was lost (immortality §5.3; 5.52).
- **Pending**: a stale choice is waiting for an answer. It is durable: `stale_seq` and `stale_cause` on the bbolt
  session record, present exactly while pending (continuity-state `stale_seq`).
- **Keyboard holder**: the one attached client holding the control token, the right to type and request a size.
  **Escrow**: the core holds the token because no attached client may take it (Contract §10; PTY §7.1).
- **Prompt**: the `stale_prompt` frame; its `prompt_id` names one question. **Void**: a prompt the core no longer
  accepts an answer to.
- **Wiggle**: a resize one column narrower, then back, which a full-screen program answers by redrawing (PTY §7.2).
- **Commit point**: the recording position bbolt keeps, advanced only with a grid snapshot; the recording is never
  cut back to it (continuity-state; → grid-snapshot). A gap marker is one: nothing follows it in the recording until
  step 2's write.
- **Broker marker**: a recording frame, sidecar code 5, saying the core acted on a stale choice (ttyrec `broker_marker`).

## Path

raise: (grid-snapshot) → CO-BB → SF-CO · pending: SF-CO → (typing-ownership) → (reattach-handshake) · restart: CO-BB (core-restart) · answer: SF-CO →
CO-DK → ctrl_l: CO-DK → CO-PW → PW-TG · wiggle: CO-PW → PW-TG → CO-VT → (grid-snapshot) · then CO-DK → CO-BB → SF-CO ·
fresh_session: (session-open)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | — | CO → CO | — | → grid-snapshot, cause `gap` only | the core | a snapshot forced once the gap frame is fed to the VT worker (reattach-handshake step 11; continuity-state top) | — | — |
| 2 | CO-BB | CO → BB | bbolt write | `session_record` with `stale_seq`, `stale_cause` | the core | `gap`: the same write as step 1's commit point and `grid_snapshot_record`; `stale_seq` is the gap frame's seq. `grid_lost`: a write of its own, before the first new commit; `stale_seq` is the committed seq at which the grid could not be rebuilt (continuity-state `stale_seq`) | — | — |
| 3 | SF-CO | CO → SF | WebSocket | `stale`, state `pending` | the core | pending begins: steps 6–9 apply | — | every attached client |
| 4 | SF-CO | CO → SF | WebSocket | `stale_prompt` | the core | a new `prompt_id`, outstanding until step 11 or void (steps 8, 9) | — | the holder's client only; nobody while the token is in escrow |
| 5 | SF-CO | CO → SF | WebSocket | `stale`, state `pending`, at attach (→ session-attach) | the core | to each client attaching while pending, after `clients`; no attach wiggle. A client that takes the token from escrow at attach gets step 4 | — | the attaching client |
| 6 | SF-CO | SF → CO | WebSocket | binary: input bytes (conventions V15) | any attached client | not forwarded, never held. The owner typing while not the holder still takes the token (→ typing-ownership), then step 8 | `input_refused` `stale` (a non-holder's reason: byte-relay open item 6) | the sender |
| 7 | SF-CO | SF → CO | WebSocket | → size-control: `resize` | keyboard holder | still answered with `size` (CHANGELOG contract-v0.3 C2); whether it is applied to the target is unspecified (decision queue; → size-control open items). A non-holder's is answered `not_typist` as always | as size-control | as size-control |
| 8 | — | CO → CO | — | → typing-ownership: any move of the token | the core | the outstanding prompt is void; a client that receives the token gets step 4 with a new `prompt_id`; to escrow, nobody is asked | — | as typing-ownership |
| 9 | — | CO → CO | — | → reattach-handshake: a new gap while pending | the core | steps 1–2 for the newer gap, but `stale_seq` keeps where the staleness began (continuity-state `stale_seq`); the old prompt is void, steps 3–4 again; one answer covers every gap since `stale_seq`. Which `gap_seq` the new `stale` carries: OPEN (Open item 16) | — | every attached client |
| 10 | CO-BB | BB → CO | bbolt read | → core-restart: `session_record` | the core | a restart while pending: pending resumes from `stale_seq` and `stale_cause`; the gap is not declared again; every token is in escrow (immortality §7), so steps 5 and 4 run at attach | — | — |
| 11 | SF-CO | SF → CO | WebSocket | `stale_answer` | keyboard holder | accepted: pending ends, whatever the choice (D12) | `request_refused`, request `stale_answer`: `not_pending` or `not_allowed` | the sender |
| 12 | CO-DK | CO → DK | file `<session_id>.ttyrec` + `.dir` | `broker_marker` (code 5), data `x-broker-line` with the choice | the core | every accepted answer, when the session's recording is active (→ byte-relay step 3's condition) | — | — |
| 13 | CO-DK | CO → DK | file `<session_id>.ttyrec` + `.dir` | `data_frame`, code 1, payload `0x0C` | the core | `ctrl_l` only, after step 12 | — | — |
| 14 | CO-PW | CO → PW | unix socket `conn-{conn_id}/{socket}` | input frame, payload `0x0C` | the core | `ctrl_l` only: the next input seq, stamped on the core's clock (→ byte-relay step 2). Not event E1 | the worker's `input_refused` (→ byte-relay step 4) | — |
| 15 | PW-TG | PW → TG | SSH | `CHANNEL_DATA` carrying `0x0C` | worker | typed input the target's program interprets (immortality §5.3) | — | — |
| 16 | CO-PW | CO → PW | unix socket `control.sock` | `resize` twice: `cols` − 1, then `cols` | the core | `wiggle` only | — | — |
| 17 | PW-TG | PW → TG | SSH | `window-change` twice (RFC 4254 §6.7) | worker | each its own window-change, in order, never coalesced (ipc-control `resize`) | — | — |
| 18 | CO-VT | CO → VT | unix socket `vt/{vt_id}.sock` | `resize` twice | the core | `wiggle` only: the grid keeps the PTY's size (vt-ipc `resize`) | `error` `unknown_session` | the core |
| 19 | — | CO → CO | — | → grid-snapshot | the core | `wiggle` only: a snapshot forced on each PTY size change (CHANGELOG C6 bullet 2) | — | — |
| 20 | CO-DK | CO → DK | file (audit log, JSONL) | `session_stale_choice` | the core | every accepted answer: who chose, which client, the choice and the cause | — | the audit log |
| 21 | CO-BB | CO → BB | bbolt write | `session_record` without `stale_seq`, `stale_cause` | the core | cleared on the answer (continuity-state `stale_seq`) | — | — |
| 22 | SF-CO | CO → SF | WebSocket | `stale`, state `chosen` | the core | carries the choice and `by`, the answering client | — | every attached client |
| 23 | — | SF → SF | — | → session-open, `fresh_session` only | the surface | the surface calls `POST /sessions` itself; the old session keeps running until closed | as session-open | as session-open |

### Rules for these steps

The core:

1. **Steps 1–2.** On a gap the core MUST force a snapshot and write the commit point, the `grid_snapshot_record`,
   `stale_seq` and `stale_cause` in one bbolt write (a session with no snapshot: the commit point record-only, with
   the pair; grid-snapshot rules 4, 11, 12), and append nothing after the gap marker but an end marker before it lands;
   for `grid_lost` it MUST write the pair alone, before the first new commit (continuity-state top; CHANGELOG C6
   bullet 3; D25). `C6/stale-pending-durable`.
2. **Step 3.** The core MUST send `stale` `pending` to every attached client, with `gap_seq` exactly when the cause
   is `gap`. `C2/stale-flow`.
3. **Step 4.** The core MUST send `stale_prompt` to the holder's client and to no other, with the fixed prompt text
   and the choices the session's protocol supports, each with its fixed text: all four for `ssh` and `echo` at this
   tag (ws-frames `stale_prompt`; D11). `C2/stale-flow` grades the prompt text and the four choices; the four choice
   texts are owed, because `stale_choice_text.text` is any non-empty string in the schema.
4. **Step 5.** The core MUST send `stale` `pending` to each client attaching while pending, after `clients`
   (ws-frames `stale`), and MUST NOT fire the attach wiggle while any choice is pending (`x-attach-order`; PTY §7.2).
   The frame: owed. The wiggle: `C3/attach-redraw-wiggle`.
5. **Step 6.** While pending the core MUST refuse every client's input, the holder's included, with `input_refused`
   (the holder's `stale`; a non-holder's reason is byte-relay's open item 6), and MUST NOT forward or hold it (ws-frames
   `input_refused`; immortality §5.3). `C2/stale-flow`. The
   owner's take-over by typing still moves the token, and the keystroke that caused it is refused `stale` (v0.3 plan
   C2 decision 10; 5.53 governs who may type, not whether this byte goes). Owed.
6. **Step 7.** The core MUST still answer the holder's size request (CHANGELOG contract-v0.3 C2, "a holder's size
   request is still answered"; ws-frames `resize`). Owed. Whether it is applied to the target is unspecified
   (decision queue; size-control open items).
7. **Step 8.** A token move while pending MUST void the outstanding prompt, and the client that receives the token
   MUST get a new `prompt_id` (ws-frames `stale_prompt`). Owed.
8. **Step 9.** A new gap while pending MUST NOT move `stale_seq`: it is "where the session's staleness began"
   (continuity-state `stale_seq`; C6/stale-pending-durable's description). The core MUST void the prompt and send
   `stale` `pending` and a new prompt (v0.3 plan C2 decision 10, F10; Open item 3). Owed. Which `gap_seq` goes out:
   Open item 16.
9. **Step 10.** After a restart the core MUST resume a pending choice from the record and MUST NOT declare its gap
   again (continuity-state top; 5.52). `C6/stale-pending-durable`; the not-twice half is reattach-handshake's
   `C8/gap-twice-across-restart`. For a `stale_cause` the reader does not know, continuity-state says it "shows the
   stale notice without a cause"; the C2 `stale` frame requires `cause` (Open item 17).
10. **Step 11.** The core MUST accept `stale_answer` only from the current holder's client for the outstanding
    `prompt_id`, and answer any other with `request_refused` (ws-frames `stale_answer`). `C2/stale-flow` (shapes).
    Any accepted answer MUST end the pending state, `fresh_session` included (D12). Owed.
11. **Step 12.** For every accepted answer, with recording active, the core MUST write exactly one broker marker
    whose data is `x-broker-line` for that choice, stamped on its input clock (ttyrec `broker_marker`; 5.46).
    `C8/broker-marker`. The marker is not target output: when two recordings' output is compared it is left out of
    the payload and compared line for line, like a gap marker (ttyrec `x-compare` `payload-identical`; CHANGELOG C8;
    graded by U11's differ).
12. **Steps 13–15.** For `ctrl_l` the core MUST send exactly one input frame of `0x0C`, recorded as a code-1 frame
    after the marker and before it is sent (ttyrec `x-sidecar` code 5; Contract §13). `C8/broker-marker-ctrl-l`,
    `C8/recordings-broker-marker`. That byte comes from no client, so it is not event E1 (session model §9 E1) and
    MUST NOT move `t_in`, `client_touch` or `activity` (§10): neither `quiet_prompt` nor `unattended` is touched
    (v0.3 plan C2 decision 10). Owed.
13. **Steps 16–19.** For `wiggle` the core MUST send a C3 `resize` pair, one column narrower then back (immortality
    §5.3; 5.46; v0.3 plan C2 decision 10), and each PTY size change MUST reach the VT worker and force a snapshot
    (vt-ipc `resize`; CHANGELOG C6). The worker MUST NOT coalesce the two (ipc-control `resize`).
    `C3/attach-redraw-wiggle` grades the pair's shape on an attach and `C4/vt-snapshot-on-resize` the snapshot; the
    stale wiggle itself is owed.
14. For `continue` and `fresh_session` the core MUST send nothing to the target (ttyrec `x-sidecar` code 5). For
    `fresh_session` it MUST NOT open the new session and MUST NOT end the old one (immortality §5.3; G-1 ruling,
    below). Owed.
15. **Step 20.** For every accepted answer the core MUST write `session.stale_choice` with the chooser as `user`, the
    answering client as `client_id`, and `gap_seq` exactly when the cause is `gap`
    (audit-event `session_stale_choice`); which gap's seq after step 9 is Open item 16. `C6/event-vocabulary-v0.3`; every marker
    needing its event is `C8/recordings-broker-marker`.
16. **Steps 21–22.** The core MUST clear `stale_seq` and `stale_cause` on the answer and send `stale` `chosen` to
    every attached client. Clearing: `C6/stale-pending-durable`; the frame: `C2/stale-flow`.
17. From step 2 until the answer the core MUST NOT send the target anything on its own: no attach wiggle, no redraw,
    no Ctrl-L (immortality §5.3; 5.46). Whether the holder's size request (step 7) reaches the target is unspecified (decision queue): immortality §5.3 forbids only what TSB sends on its own, and the request is the operator's.
    `C3/attach-redraw-wiggle` for the wiggle; the rest owed.

A consumer (surface, any API client):

18. A surface that renders text MUST show the `stale` notice line, and MUST show the prompt's text and each choice's
    text as sent, so no surface softens the warning (ws-frames `stale`, `stale_prompt`; D11; immortality §5.3).
    Owed; no fixture grades a consumer.
19. A surface that answers `fresh_session` MUST open the new session itself with `POST /sessions`, choosing reuse or a
    new connection as for any session (→ session-open; Contract §15.3; 5.49; G-1 ruling). Owed.

## Refusals and who is told

- **`stale_answer`** (ws-frames `request_refused`): `not_pending` when no stale choice is outstanding; `not_allowed`
  when the answer is not from the current holder's client for the outstanding prompt, which covers a view-only
  client, any non-holder, and a former holder answering a voided prompt. A view-only client (session.join.ro) never
  holds the token, so it is never asked (ws-frames `attached`).
- **Input** is refused `stale` to the client that sent it, every client alike. Which reason wins when another also
  applies (`not_typist`, `replaying`) is byte-relay's open item 6.
- **Told:** every attached client gets `stale` at steps 3, 5, 22; only the holder's client gets a prompt; the
  recording gets the marker and the audit log the event. The northbound session record has no stale field
  (session-record schema), so a principal reading it with → session-list is not told.
- **D12's cost, stated** (approval D12; v0.3 plan F20): `continue` and `fresh_session` reopen input on a screen still
  stale, and a client attaching after the answer gets no `stale` frame, since one goes only while pending.

## Security considerations

- **Who may type.** From step 3 to the answer nobody's bytes reach the target, the holder's included (immortality
  §5.3: "blocking until things is normal"). A take-over moves the right, never the byte that caused it (step 6).
- **Who may choose.** Only the current holder's client, for the outstanding `prompt_id`. The owner chooses only after
  taking the keyboard, a move every client sees in a `token` frame (→ typing-ownership). After an owner's revoke to escrow
  nobody is asked and nobody types until the owner reassigns the token or takes it (S-2, below).
- **Broker-originated bytes.** The core sends `0x0C` or a resize pair only on the holder's answer, never on its own
  (Contract §12; 5.46). The recording marks the act as the broker's, before the `0x0C` frame, so the byte is never
  attributed to a client; who chose is on the audit event only (ttyrec `broker_marker`; Contract §13). Ctrl-L is
  typed input the target's program interprets: the choice text says "no guarantee" (immortality §5.3).
- **The host boundary.** No stale-screen frame carries host detail (conventions V11).

## Open items

1. **S-2, wording only (verifier).** ws-frames `stale_prompt` says "Token in escrow: the next eligible attach gets
   it", while `handoff` says an attach does not take a revoked token. Behaviour, as written above: after an owner's
   revoke no attach is eligible; when the owner reassigns or takes the token, that move (step 8) gives the new holder
   a new prompt, and input stays refused meanwhile. Text to fix: `ws-frames.schema.json#/$defs/stale_prompt`, as the
   verifier proposes ("the client that next receives it gets the prompt: the next eligible attach under the
   no-clients rule; after an owner revoke, whoever the owner reassigns it to").
2. **G-1, already ruled.** The surface calls `POST /sessions` after `fresh_session` (v0.3 plan C2 decision 10, under
   D11 and D12, agreed 2026-10-07; 5.49; Contract §15.3). The published contract never says so: the sentence belongs
   in `stale_prompt` or `stale_answer`, or in contract `README.md` "Senders and receivers".
3. **Stated only in the v0.3 plan, not in the published contract** (not in plan §7): the new-gap rule (step 9; its
   `stale_seq` half the contract contradicts, Open item 16), the
   take-over keystroke refused `stale` (step 6), `ctrl_l` not counting as E1 (step 14), and the wiggle's direction
   (`ttyrec` says only "a resize pair"). Like G-1, each needs its sentence in `contract/`.
4. **D3, OPEN** (approval; plan §7 G-9): joining while the VT worker is down, (a) wait then refuse or (b) join with
   the stale flow. Under (b) stale-screen gains a third trigger. Not decided; nothing here assumes either.
5. **D26, awaiting the operator** (approval, southbound set): the shape of `session.stale_choice`. That the choice is
   audited is decided (immortality §5.3; 5.46). Also open in the same southbound set and leaned on here (queue A8):
   D18 (`seq_jump`) and D21 (`snapshot_incompatible`), the two sources of cause `grid_lost` ("Called by").
6. **Unspecified (not in plan §7):** the reason for an answer from the current holder carrying an outdated
   `prompt_id` while a newer one is outstanding. Neither reason's text fits; the shipped `tsbc serve` stub answers
   `not_pending`.
7. **Unspecified (not in plan §7):** how a surface learns its prompt is void (no frame withdraws one); and where
   `stale_prompt` falls in the attach order: `x-attach-order` names neither `stale` nor `stale_prompt`, only that the
   wiggle does not fire while a choice is pending; `stale`'s place, after `clients`, is ws-frames `stale`'s (plan §7
   G-6; decision queue).
8. **Unspecified (not in plan §7):** an answer arriving before reattach-handshake reaches `live` under data-socket-redial, when
   `ctrl_l` would need an input frame the core may not yet send.
9. **Unspecified (not in plan §7):** the order of steps 12, 16, 20, 21, 22 beyond the marker preceding `0x0C`, and
   what a crash between them leaves (a cleared record with no effect sent, or an effect sent and the prompt back).
10. **Closed — queue A1.** When the gap's snapshot cannot be taken within `snapshot.wait_sec`, or the VT worker
    answers that it cannot, the core writes the pair with a record-only commit of the gap marker and gives the grid up
    (grid-snapshot rules 4, 11, 12).
11. **Unspecified (not in plan §7):** the stale wiggle at the `cols` minimum, a floor or a pinned target (the attach
    wiggle's minimum rule is `C3/attach-redraw-wiggle`'s, F20), and whether it is announced with `size`.
12. **Unspecified (not in plan §7):** what `stale.at` means, and its value after a restart (not durable).
13. **Unspecified (not in plan §7):** a cause change while pending (`grid_lost` during a pending `gap`, or the
    reverse); a `seq_jump` outside a restore, where continuity-state's "found at restore, before any new commit" does
    not hold. Open elsewhere: a gap in an ended session's replay (reattach-handshake), the record at an end while pending (session-end-by-tsb).
14. **Unspecified (decision queue):** whether the holder's size request while a choice is pending reaches the target
    (step 7). The contract says only that it "is still answered" (CHANGELOG contract-v0.3 C2); size-control's open items
    carry the same question.
15. **Answered for telnet; the serial half stays open with serial.** A resizable telnet session is offered all four
    choices. A non-resizable one is offered three, without `wiggle`: its `resize` pair never reaches a target that
    refused NAWS (size-control step 3). A choice not offered is answered `request_refused`, request `stale_answer`,
    code `not_allowed`, and the prompt stays pending [the shape: C2/stale-flow; the answer, and the serial half
    (serial has no window-change, immortality §5.3): owed by the E3 core plan].
16. **OPEN — a second gap while pending** (not in plan §7). continuity-state `stale_seq` is "where the session's
    staleness began", and C6/stale-pending-durable says the same; ws-frames `stale`: "gap_seq names where it began".
    So `stale_seq` keeps the first gap (step 9, rule 8). The v0.3 plan (C2 decision 10, F10, unpublished) moves it to
    the newer gap and names the newest in the audit. Which `gap_seq` the re-sent `stale` `pending`, the `chosen` frame
    and `session.stale_choice` carry after a second gap is not settled: the first gap's seq (where staleness began),
    or the newest (audit-event `gap_seq`, "the lost seq the gap frame names"). Queue K10 logs only that the new-gap
    rule lacks a contract sentence, not that the published text contradicts the plan.
17. **OPEN, the contract against itself — an unknown `stale_cause`.** continuity-state `stale_cause`: "a receiver
    that meets a value it does not know shows the stale notice without a cause". ws-frames `stale` requires `cause`,
    so a notice without a cause has no wire form, and only the core reads the bbolt record. Who that receiver is, and
    what it sends, is not said. Queue S91's general rule for "a `stale` state or cause" is a proposed fill, not a
    ruling, and would differ from the durable field's rule.

## Picture

pending
