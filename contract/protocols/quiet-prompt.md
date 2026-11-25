# quiet-prompt — ask the owner when typed input goes unanswered

| | |
|---|---|
| **ID** | quiet-prompt |
| **Purpose** | When typed input goes unanswered on a live link, ask the owner "Kill the session?" and act only on the owner's answer. |
| **Status** | built (E1) |
| **Corpus clauses** | session model §7, §9 (E1–E4), §10 (`unanswered`, `attached`, `link_alive`, `t_in`, `sender`), §11 (`quiet_prompt`, the worked example, T8), §12; Contract §6, §10, §12; PTY §7.1 |
| **Seam** | C2; the data frames it watches are C3's (→ byte-relay) |
| **Schemas** (contract-v0.3) | `contract/schemas/ws-frames.schema.json#/$defs/quiet_prompt`, `#/$defs/quiet_prompt_answer`; `contract/schemas/common.schema.json#/$defs/ticket`. Read, owned elsewhere: `contract/schemas/ipc-data-frame.schema.json` (→ byte-relay), `contract/schemas/ipc-control.schema.json#/$defs/timer_values`, `#/$defs/link_state` (→ link-liveness), `contract/schemas/session-record.schema.json` `owner`, `clients` |

Fixtures are cited by seam and name, under `contract/fixtures/`. **Words used here:**

- **E1**: the core forwards at least one byte from the keyboard holder's client to the worker as an input data frame.
  **E2**: the core receives an output data frame of at least one byte for the session (session model §9).
  `t_in(s)` is the time of the latest E1, `sender(s)` the client that sent it (§10).
- **`unanswered(s)`** is `t_in(s) > last(E2(s))`: you typed, and the program has printed nothing since. Only an E2
  answers input, never a TCP ACK, a keepalive reply or a window adjust (§10).
- **`link_alive(conn)`**: the last E3 (any SSH packet, a keepalive answer included) came less than `link_timeout`
  ago; always true with no liveness signal (§10). It turns false only as `link_timeout` fires or the connection goes,
  ending every session on it (§11, §13). `link_state` `missing` is one interval without E3 (ipc-control `link_state`).
- The **`quiet_prompt` timer** is a `config.yaml` knob, 180 s by default, a target's own value overriding it
  (session model §11; conventions V7); the core's alone, never sent to a worker (ipc-control `timer_values`). No
  range is fixed: it crosses no wire, and `config.yaml` has no contract shape yet (plan §3, CO-FS; not in plan §7).
- The **owner** is the principal in the record's `owner`, holding the owner token (Contract §10); its **attached
  clients** are the record's `clients` whose `user` it is (session-record). A prompt is **outstanding** from the
  moment it is sent until the first valid answer to it.

Why it exists: no signal shows whether a quiet task works or hangs, and reading output to guess is forbidden, so TSB
asks (session model §7; Contract §6). It only asks, since some input is never echoed, such as a password prompt (§11,
worked example): the session's fate is the owner's, never the broker's (Contract §10, §12).

## Path

arm (steps 1–5): SF-CO → CO-PW → CO-PW → (session-detach | client-remove) → CO-PW · ask (6–10): SF-CO → SF-CO → (session-end-by-tsb) · re-arm (11): SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | WebSocket | → byte-relay: binary: input bytes | keyboard holder (→ typing-ownership) | the core takes the bytes | input the core refuses (`input_refused`, any reason) is never forwarded, so never an E1 (→ byte-relay) | — |
| 2 | CO-PW | CO → PW | unix socket (data socket) | → byte-relay: input frame, payload ≥ 1 byte | the core | **E1**: `t_in(s)`, `sender(s)` move; `unanswered(s)` holds. The timer arms while `attached(s, sender(s))` and `link_alive` also hold, counting from `t_in(s)`; a new E1 re-arms it (session model §11) | — | — |
| 3 | CO-PW | PW → CO | unix socket (data socket) | → byte-relay: output frame, payload ≥ 1 byte | worker | **E2**: `unanswered(s)` ends, the timer disarms. A gap frame has no payload (ipc-data-frame top), so it is no E2; nor is `link_state` or any E3 (→ link-liveness) | — | — |
| 4 | — | CO → CO | — | → session-detach, → client-remove | the core | the sender's WebSocket closes or misses its pongs, or it is removed: `attached(s, sender(s))` fails (session model §9 E4): the timer disarms | — | — |
| 5 | CO-PW | PW → CO | unix socket (control socket) | → link-liveness: `connection_lost` | worker | the link died: the timer disarms and the session ends (→ session-end-from-outside) | — | — |
| 6 | — | CO → CO | — | — | the core | the timer fires when `now − t_in(s) ≥ quiet_prompt`, once per unanswered input. Asked: the sender's client when the sender's principal is the owner; otherwise every attached client of the owner; nobody when the owner has none attached. The core mints a `prompt_id` | — | — |
| 7 | SF-CO | CO → SF | WebSocket | `quiet_prompt` | the core | the prompt is outstanding | — | step 6's clients, no other |
| 8 | SF-CO | SF → CO | WebSocket | `quiet_prompt_answer` | an asked client | the first valid answer ends the prompt | from a client not sent that `prompt_id`, or once the prompt is not outstanding: ignored, no reply | — |
| 9 | — | CO → CO | — | → session-end-by-tsb | the core, on the owner's authority | an answer with `kill` true: the session ends `killed` / `owner`, as `DELETE /sessions/{id}` by the owner ends it | — | → session-end-by-tsb: every client, close 4010 |
| 10 | — | CO → CO | — | — | the core | an answer with `kill` false: nothing changes; asked again at the next unanswered input (step 2) or attach (step 11) | — | — |
| 11 | SF-CO | SF → CO | WebSocket | → session-attach: `attach` | the sender, attaching again | re-arms the timer, still counted from `t_in(s)` (session model §11); T8: "no → stays, asked again next attach". Whose attach: Open item 2 | — | — |

### Rules for these steps

1. The core MUST run the `quiet_prompt` timer itself and MUST NOT send it to a worker (ipc-control `timer_values`).
   Fixture: `C3/timer-values-shape` (e1): its closed shape refuses the core's own keys (instance: `expiry_notice`).
2. The core MUST count input as answered only by an E2 of the same session, never by step 3's non-events or output on
   another session of the connection (session model §9 E2, §10), and MUST NOT read output's content to judge
   (Contract §6; session model §7). Fixture: owed.
3. The core MUST arm, disarm and re-arm the timer as steps 2–5 and 11 say, firing at most once per unanswered input
   (session model §11). Fixture: owed; `C2/quiet-prompt-owner-only` grades who is asked, not when.
4. The core MUST send `quiet_prompt` to step 6's clients and no other (ws-frames `quiet_prompt`; Contract §10).
   Fixture: `C2/quiet-prompt-owner-only` (required-green e1, reference: graded by the U2 test it names).
5. The core MUST send the fixed `code` and `text` and an unguessable `prompt_id` of the ticket's shape, so no answer
   can be forged (ws-frames `quiet_prompt`; common `ticket`). Fixtures: `C2/quiet-prompt-owner-only` refuses another
   text and the id `"1"`; `C2/ws-frames-shape-v0.2` (e1). Unguessable beyond the shape: owed.
6. The core MUST accept `quiet_prompt_answer` only from a client it sent that `prompt_id` to, only while the prompt
   is outstanding, end the prompt at the first valid answer, and ignore every other answer (ws-frames
   `quiet_prompt_answer`). Fixture: `C2/quiet-prompt-owner-only` (reference).
7. On `kill` true the core MUST end the session `killed` / `owner` (→ session-end-by-tsb); on `kill` false it MUST change
   nothing (ws-frames `quiet_prompt_answer`; session model §11, §12). Fixture: `C2/quiet-prompt-owner-only`.
8. The core MUST NOT end a session through quiet-prompt without a valid `kill` true: not when the timer fires, nobody is
   asked or nobody answers (session model §11, "Never ends a session by itself"; Contract §12). Fixture:
   `C2/quiet-prompt-owner-only` grades "no leaves it"; a prompt never answered: owed.
9. A surface MUST answer with the `prompt_id` it received in exactly the closed shape, with no delivered position, and
   MUST NOT raise the question on its own timer: the core alone computes it (ws-frames top, `quiet_prompt_answer`;
   decision 5.15). Fixture: the shape, `C2/quiet-prompt-owner-only` (`seq` invalid); a surface's behaviour: owed.

The audience among the owner's clients and the answer rule are the orchestrator's DEFAULT O4 (CHANGELOG
contract-v0.2; the fixture's description); the corpus says "the owner if attached" (session model §11).

## Refusals and who is told

- Only step 8 refuses, silently: an answer the core does not accept gets no frame and touches nothing.
- Only the asked clients learn of the question; the keyboard holder, when not the owner's, and every other attached
  client never see it (Contract §10). The audit vocabulary has no event for the question or for a no
  (`audit-event.schema.json` `$defs`); a yes is audited as session-end-by-tsb's end, `session_end` `killed` / `owner`.
- A surface that does not know `quiet_prompt` ignores it (conventions V2); since quiet-prompt never ends a session by itself
  (rule 8), the session lives on. The contract lets a surface show a translation keyed on `code` (ws-frames
  `quiet_prompt`); no corpus clause grants that freedom, so this document states no MAY for it (reported).

## Open items

1. **Unspecified (not in plan §7) — when a prompt stops being outstanding** other than by its first valid answer: an
   E2, the asked or the sending client detaching, a newer prompt, the session ending. No frame withdraws a question,
   so the owner's other asked clients are not told that it was answered or became moot.
2. **Unspecified (not in plan §7) — whose attach re-arms the timer.** "The sender attaching again" (session model
   §11), "next attach" (T8), "the next unanswered input or attach" (ws-frames `quiet_prompt_answer`). A client is one
   attachment (rules page, "Terms"), so the sender attaching again must mean its principal or its surface; neither is
   said. Nor is where `quiet_prompt` falls in `x-attach-order`: unspecified (plan §7 G-6; decision queue).
3. **Unspecified (not in plan §7) — a fire with nobody asked** (step 6): whether the owner's later attach brings the
   question. The corpus re-arms only on a new E1 or the sender's attach.
4. **Unspecified (not in plan §7) — across a core restart.** `sender(s)` and any outstanding prompt are in no durable
   record (`continuity-state.schema.json#/$defs/session_record`), and the core's stop closes every client 4011
   (→ core-restart). Whether a question is asked after the restart is not said.

## Picture

pending
