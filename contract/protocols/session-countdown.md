# session-countdown — when each session will end, and the warning before it

| | |
|---|---|
| **ID** | session-countdown |
| **Purpose** | Tell every user when each session will end and which timer ends it, with a warning inside the window. |
| **Status** | built (E1); the two durable clocks since E0 (continuity-state top: "an E0 record loads unchanged") |
| **Hands off to** | session-end-by-tsb when `idle` or `unattended` fires; link-liveness when `link_timeout` fires; orphan-session while the core is gone. session-countdown ends nothing (session model §11 `expiry_notice`: "Ends nothing"). |
| **Corpus clauses** | session model §7, §9 E1–E3, §10, §11 (`expiry_notice`, `idle`, `unattended`, `link_timeout`, T1–T7), §14, §15; Storage §8.1, §8.6; immortality §8 |
| **Seams** | C1, C2, C3, C6 |
| **Schemas** (contract-v0.3) | `session-record.schema.json#/properties/ends_at`, `#/properties/ends_by`, `#/properties/ending`, `#/properties/state`; `ws-frames.schema.json#/$defs/notice`, `#/$defs/attached`; `ipc-control.schema.json#/$defs/link_state`, `#/$defs/timer_values`, `#/$defs/timers`, `#/$defs/connection_lost`; `ipc-data-frame.schema.json` (top); `continuity-state.schema.json#/$defs/session_record` (`activity_at`, `client_touch_at`); OpenAPI `#/components/schemas/SessionRecord`, `#/components/schemas/SessionList` |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`) at contract-v0.3.
Fixtures are cited as `<seam>/<name>` under `contract/fixtures/`. Corpus files are under `srs/`.

**Words used here.**

- The **countdown** is three fields on a session record: `ends_at`, when the soonest timer that will end the
  session fires if nothing resets it; `ends_by`, which timer that is (`link_timeout`, `idle` or `unattended`); and
  `ending`, whether the session is inside a warning window (session-record `ends_at`, `ends_by`, `ending`).
- **E1** is input the core forwarded to the worker, ≥ 1 byte, from the **keyboard holder** (the one attached client
  holding the control token, Contract §10); **E2** is output the core received from the worker, ≥ 1 byte; **E3** is
  any sign from the target's SSH server that it still answers, the keepalive reply included (session model §9).
- The two **clocks**: `activity` is the last byte either way (E1 or E2), `idle`'s clock; `client_touch` is the last
  byte a client sent (E1), `unattended`'s clock. Both start at session creation (session model §10). The core keeps
  them as `activity_at` and `client_touch_at` (continuity-state `session_record`).
- A **warning window**: for `idle` and `unattended`, the last `expiry_notice` before the timer fires (a `config.yaml`
  knob; the corpus default is 72 h, conventions V7); for `link_timeout`, from the worker's `link_state` `missing`
  until `answering` (session model §11 `expiry_notice` "Fires when"; ipc-control `link_state`). With the corpus
  defaults `idle` (7 d) warns at 4 d and `unattended` (30 d) at 27 d (T1, T4); `link_timeout` (360 s) warns from
  the first missed keepalive (session model §11 "Order"; T6, T7).

## Path

clocks and link (steps 1–5): CO-PW → CO-BB → CO-PW · told (6–10): SF-CO · ends (11–12): (session-end-by-tsb) | (link-liveness) · core gone
and back (13–15): (orphan-session) → CO-BB → CO-PW

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/{socket}` | `→ byte-relay: input frame`, ≥ 1 byte | keyboard holder, through the core | E1: `activity` and `client_touch` move to now (rule 5) | — | — |
| 2 | CO-PW | PW → CO | unix socket `conn-{conn_id}/{socket}` | `→ byte-relay: output frame`, ≥ 1 byte | worker | E2: `activity` moves to now; `client_touch` does not, so output alone never holds off `unattended` (session model §11, §14) | — | — |
| 3 | CO-BB | CO → BB | bbolt write | `continuity-state.schema.json#/$defs/session_record` (`activity_at`, `client_touch_at`) | the core | the clocks are durable (rule 8). When they are written is unspecified (Open item 3) | — | — |
| 4 | CO-PW | PW → CO | unix socket `conn-{conn_id}/control.sock` | → link-liveness: `link_state` `missing` | worker | one `keepalive_interval` passed with no E3; `link_timeout`'s countdown starts from `last_ack_at` (rules 2, 7) | — | the core |
| 5 | CO-PW | PW → CO | unix socket `conn-{conn_id}/control.sock` | → link-liveness: `link_state` `answering` | worker | E3 returned: `link_timeout` leaves the countdown and its window closes; a flap that recovers ends nothing (T6; rule 7) | — | the core |
| 6 | — | CO → CO | — | — | the core | computes `ends_at`, `ends_by` and `ending` for every session not ended, from the clocks, the connection's latest `link_state` and the timer values in force for the target (rules 2–4) | — | — |
| 7 | SF-CO | CO → SF | WebSocket | `notice` | the core | a warning appears, updates or is withdrawn (rule 6) | — | every attached client |
| 8 | SF-CO | CO → SF | HTTPS | `→ session-open: SessionCreateResponse` | the core | the new session's record carries the countdown (rule 1) | — | the caller |
| 9 | SF-CO | CO → SF | HTTPS | `→ session-list: SessionRecord`, alone or in a `SessionList` | the core | the record carries the countdown as computed at the read (rule 1) | as session-list says | the caller |
| 10 | SF-CO | CO → SF | WebSocket | `→ session-attach: attached` | the core | its `session` record carries the countdown, so a client learns it at attach. Whether a `notice` also follows inside a window is unspecified (plan §7 G-6; decision queue) | as session-attach says | the attaching client |
| 11 | — | CO → CO | — | → session-end-by-tsb | the core | `idle` or `unattended` fires while the core runs: killed / `idle` or `unattended`, and the countdown leaves the record (session model §11 "Evaluated by": core) | — | as session-end-by-tsb says |
| 12 | — | PW → PW | — | → link-liveness | worker | `link_timeout` fires at the worker (now − last E3 ≥ `keepalive_interval` × `keepalive_count_max`); the connection closes with `connection_lost` `keepalive_timeout`, and every session on it ends terminated / `connection_lost` (→ session-end-from-outside) (session model §11, §13; ipc-control `connection_lost`) | — | as link-liveness says |
| 13 | — | PW → PW | — | → orphan-session | worker | while the core is gone the worker runs `idle` and `unattended` itself, with the last timer values it received, and spools any end (ipc-control `timer_values`, `timers`; immortality §8). No countdown is served: nothing northbound runs | — | as orphan-session says |
| 14 | CO-BB | BB → CO | bbolt read | `continuity-state.schema.json#/$defs/session_record` (`activity_at`, `client_touch_at`) | the restarted core, during → core-restart | the timers continue from the stored clocks, so a deploy restarts none (rule 8) | — | — |
| 15 | CO-PW | PW → CO | unix socket `conn-{conn_id}/control.sock` | → link-liveness: `link_state`, the first event to a new control peer | worker | the restarted core has `link_timeout`'s countdown at once (→ link-liveness step 10) | — | the core |

### Rules for these steps

Provider rules.

1. The core MUST put `ends_at`, `ends_by` and `ending` together on every record of a session not ended, an
   `unreachable` one included, and none of them on a `killed` or `terminated` record (session-record `anyOf`,
   `oneOf`). Fixtures: `C1/session-countdown` (required-green e1, live: present on the `POST /sessions` answer and on
   `GET /sessions/{id}`); `C1/session-record-shape-v0.2` (e1: the three together, absent once ended).
2. `ends_at` MUST be the soonest of the fire times below, computed with the timer values in force for the session's
   target (ipc-control `timer_values`; Storage §8.1), and `ends_by` MUST name that timer:
   `idle` at `activity_at` + `idle`; `unattended` at `client_touch_at` + `unattended`; `link_timeout` at
   `last_ack_at` + `keepalive_interval` × `keepalive_count_max`, counted only while the connection's latest
   `link_state` is `missing` — "the countdown runs from the first missed keepalive" (ipc-control `link_state`;
   session model §11 "Order"). Fixture: owed; `C1/session-countdown` "asserts that they are there, never what they
   are".
3. `ending` MUST be true inside a warning window and false outside one (session-record `ending`). Fixture: the
   frame's value, `C2/notice-changes` (e1, reference: U2 `core/internal/session/countdown_test.go`); the record's,
   owed.
4. Only the core computes the countdown: the core MUST NOT send `expiry_notice` to a worker (ipc-control
   `timer_values`: "expiry_notice and quiet_prompt are the core's alone and are never sent"). Fixture:
   `C3/timer-values-shape` (e1), its invalid instance.
5. The core MUST move `activity` on E1 and E2 only, and `client_touch` on E1 only. An attach, a detach, a read, a
   `resize`, a WebSocket ping or pong, a keepalive reply, a zero-length frame and input from a client without the
   control token move neither (session model §9 E1, E2 "Must not count"; §10: "An attach is not a byte").
   Fixture: owed.
6. The core MUST send `notice` to every attached client when `ending` changes, and while `ending` is true when
   `ends_by` changes or `ends_at` moves, and MUST NOT send one while `ending` stays false: "never outside a window".
   Its three values MUST be the record's at that moment (ws-frames `notice`). Fixtures: `C2/notice-changes` (e1,
   reference: U2 `countdown_test.go`); shape also `C2/ws-frames-shape-v0.2`.
7. When the worker reports `missing` and `answering`, what `last_ack_at` means, and that `link_state` is the first
   event to every new control peer of a `connected` connection are link-liveness's (→ link-liveness steps 3, 8, 9 and 10, with their
   fixtures). session-countdown only reads them.
8. The core MUST keep `activity_at` and `client_touch_at` in the sessions bucket from session creation, write them
   in UTC with `Z`, read one an earlier core wrote with a numeric offset, and after a restart continue each timer
   from its stored clock, never from the restart (continuity-state `session_record`; session model §15; Storage
   §8.6). Fixtures: `C6/continuity-record-valid` (e1, reference: U5 and U2 tests) grades the shape and the zone;
   continuing across a restart is owed.

Consumer rules.

9. A consumer MUST take the countdown only from the core — a record (steps 8–10) or a `notice` — and MUST NOT
   derive `ends_at`, `ends_by` or `ending` from anything else (session-record `ends_at`: "Computed by the core only;
   a surface only renders it"; ws-frames top). Fixture: owed; no fixture grades a consumer.
10. A consumer that meets an `ends_by` it does not know MUST show `ends_at` (session-record `ends_by`; contract
    `README.md` "Senders and receivers"; V2). Fixture: owed.
11. A consumer MUST withdraw its warning when a `notice` carries `ending` false (ws-frames `notice`). Fixture: owed.

Outside a window no `notice` comes (`idle` moves with every byte, ws-frames `notice`), so an attached client's
`ends_at` can be old; a read gives the current one (→ session-list). A v0.1 worker's `unreachable` session still carries
a countdown (rule 1), but its `idle` sees none of its output (ipc-control top) and it sends no `link_state`, an
event added at contract-v0.2 (CHANGELOG).

## Refusals and who is told

- session-countdown takes no request, so it refuses nothing. Reading the record is session-list's, with session-list's refusals.
- Every attached client gets `notice`, view-only ones included (ws-frames `notice`); a principal not attached learns
  the countdown only by reading the record. No audit event exists for either (`audit-event.schema.json` `$defs`).
- No last `notice` precedes an end: when a timer fires the clients are closed 4010 (→ session-end-by-tsb) or 4012 (→ session-end-from-outside),
  and the record reads ended, with no countdown (rule 1).

## Open items

1. **A `notice` at attach.** `attached` carries the countdown; whether a client attaching inside a window also gets
   a `notice`, and where in `x-attach-order`, is unspecified (plan §7 G-6; decision queue).
2. **`ending` when two windows disagree.** With `link_state` `missing` while `idle` or `unattended` is the soonest
   and outside its own window — possible only when `expiry_notice` is set below `link_timeout`'s span — whether
   `ending` is true is unspecified. `ending` names both windows without saying how they combine. Not in plan §7.
3. **When the clocks are written** is unspecified. A stored clock older than the last byte fires a timer early after
   a restart, against "a session that a client keeps using is never ended by a timer" (session model §11). Not in
   plan §7.
4. **A tie.** Which `ends_by` names two timers that fall due at the same instant is unspecified. Not in plan §7.
5. **The clocks across the core's absence.** Nothing hands the worker's clocks (step 13) back to a returning core:
   it restores its stored ones (step 14), and replayed output is E2 when the core receives it (session model §9
   E2). Whether output during the absence counts from the worker's stamp is unspecified. Not in plan §7.

## Picture

pending

**Recording hold timers.** Suspend `idle` while a session is held; re-arm it from the active-switch time. `unattended` keeps running; refused input is not a client byte (terminal-output-accounting §5.3; owed).
