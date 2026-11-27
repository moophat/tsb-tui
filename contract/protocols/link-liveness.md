# link-liveness — a connection's link stopped answering

| | |
|---|---|
| **ID** | link-liveness |
| **Purpose** | Find out that a connection's link stopped answering: the worker probes the target, reports `missing` and `answering` to the core, and ends the connection when `link_timeout` fires. |
| **Status** | built (E1); E2: `keepalive_probe_interval` (steps 1, 2, 4); telnet TIMING-MARK (steps 5, 7) from this release; serial DCD with serial, in a later release |
| **Hands to** | session-countdown, which computes and shows `link_timeout`'s countdown; session-end-from-outside, which owns the end (`terminated` / `connection_lost`), an end spooled while the core was gone included (plan §2.1). |
| **Corpus clauses** | session model §7, §8 (A1, A2, A4, A5), §9 E3, §10 (`link_alive`; states), §11 (`link_timeout`, `keepalive_probe_interval`, `expiry_notice`; T6, T7), §12, §13; lang-arch §6.4 step 3 |
| **Seams** | C3; C5 (the spool, through session-end-from-outside) |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `ipc-control.schema.json` (top: ends from outside TSB, version), `#/$defs/timer_values`, `#/$defs/timers`, `#/$defs/connect`, `#/$defs/connected`, `#/$defs/link_state`, `#/$defs/connection_lost`, `#/$defs/error` (`bad_request`); `common.schema.json#/$defs/duration_ms`, `#/$defs/count`, `#/$defs/timestamp`; `spool-entry.schema.json` (through session-end-from-outside) |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` in this release. Corpus
files, under `srs/`: session model = `session-model-and-lifecycle.md`, lang-arch =
`language-architecture-decision.md`. Conventions are cited by number (`conventions.md`, V1–V16).

**Words used here.**

- **E3** `link_ack(conn, t)`: a sign that the far end still talks to us (session model §9). SSH: any packet on the
  connection, the reply to the probe included. Telnet (E3): `IAC WILL` or `WONT TIMING-MARK` answering
  `IAC DO TIMING-MARK`, or any payload. Serial (E3): DCD asserted, when the line provides it.
- **Probe**: what the worker sends to draw an E3 from a silent link. SSH: the global request
  `keepalive@openssh.com` with want-reply (RFC 4254 §4). Telnet: `IAC DO TIMING-MARK` (RFC 860). Serial has none.
- **`link_timeout`** = `keepalive_interval` × `keepalive_count_max` (corpus defaults 30 s × 12 = 360 s);
  `keepalive_interval` 0 turns it off for the target. **`keepalive_probe_interval`**: how often a silent link is
  probed (corpus default 15 s, half of `keepalive_interval`). All three are `config.yaml` knobs with a per-target
  override; only the heartbeat pair is core-wide, which link-liveness does not touch (V7; `timer_values`; session model §11).
- **`missing`**, **`answering`**: the values of `link_state` `state`. **`last_ack_at`**: when the last E3 arrived.
- **Control peer**: the core's end of a connection's `control.sock`. Every dial makes a new one.

## Path

values (1–2): CO-PW · probe and E3 (4–7): PW-TG · report (8–11): CO-PW → (session-countdown) · `link_timeout` (12–16):
PW-FS → CO-PW → PW-TG → (session-end-from-outside)

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-PW | CO → PW | unix socket `conn-{conn_id}/control.sock`, one JSON object per line | `→ session-open: connect`, its `timers` (`#/$defs/timer_values`) | the core | Carries the connection's `keepalive_interval`, `keepalive_count_max` and, toward a worker at `proto_version` 0.3 or later, `keepalive_probe_interval`. The core MUST put the probe field on every `connect` and `timers` toward a peer at 0.3 or later, above 0 and below a positive `keepalive_interval`, and MUST NOT send it toward a 0.2 peer: the frozen v0.2 worker refuses a key set that is not exactly its eight (D29; #114; V1) [C3/timers-keepalive-probe, e2, `artifacts:control-timers`; C3/n1-v0.2-worker-positive, e2, reference, its tests owed]. | A worker at 0.3 or later MUST answer an out-of-range value with `error` `bad_request` (`timer_values`) [owed]; on `connect` it then opens no transport (→ session-open step 7). Config load refuses the value first (→ config-reload step 2). | the worker |
| 2 | CO-PW | CO → PW | the same socket | `→ config-reload: timers`; also at reattach (→ core-restart step 13) | the core | A full new set. The worker keeps the last set it received and runs `link_timeout` and the probe by it, also while no core is attached (`timers`; session model §11 "Evaluated by": worker) [the field on `timers`: C3/timers-keepalive-probe; the keeping: owed, as config-reload step 5]. | as step 1; the core's next step after a refusal is config-reload open item 4 | the worker |
| 3 | — | PW → PW | — | arm | worker | `link_timeout` runs while the connection is open and its protocol gives a liveness signal; the probe runs while `link_timeout` does (session model §11). With `keepalive_interval` 0 both are off and `keepalive_probe_interval` is ignored (`timer_values`). Such a connection, or one with no liveness signal, MUST only ever report `answering` (`link_state`) [owed]. For a telnet connection whose worker has stopped reading at `buffer_bytes` before `open_shell`, `link_timeout` and the `missing` timer are disarmed for that connection until `open_shell`: a reply the worker is deliberately not reading never counts as a dead link, and a `missing` already sent stays; both re-arm fresh at `open_shell` [C3/telnet-output-from-connect; C3/link-state-telnet, reference, owed by the E3 worker plan]. | — | — |
| 4 | PW-TG | PW → TG | SSH | `keepalive@openssh.com`, want-reply | worker | The worker MUST send it when now − max(last(E3), last probe) ≥ `keepalive_probe_interval`: any E3 and each probe re-arm it, so a silent link is probed once per interval, never continuously (session model §11; `timer_values`; ruling 5.51 as CHANGELOG v0.3 cites it). With the field absent the worker MUST probe every `keepalive_interval`, v0.2's behaviour (`timer_values`; CHANGELOG v0.3 C3), which the corpus forbids (Open item 1) [the cadence: owed, "graded by U3's own test (owed by the E2 worker plan)", C3/timers-keepalive-probe]. | — | the target |
| 5 *(E3)* | PW-TG | PW → TG | telnet | `IAC DO TIMING-MARK` | worker | The step 4 rule, for telnet, from proto_version 0.5 (`timer_values`; session model §8 A4, §11) [C3/link-state-telnet, reference, owed by the E3 worker plan]. Serial has no probe: DCD is a line state (session model §8 A5). | — | the target |
| 6 | PW-TG | TG → PW | SSH | any SSH packet on the connection, `REQUEST_SUCCESS` or `REQUEST_FAILURE` answering the probe included | target | E3: the remote SSH server process is alive, not that the session's program produced anything (session model §7, §8 A2, §9 E3). It resets `link_timeout` and the probe. The worker MUST NOT count anything at TCP level, the kernel's own keepalive included, nor the absence of an error (session model §8 A1, §9 E3 "Must not count") [C3/link-state grades `missing` and `answering` against a silent peer; the TCP exclusion: owed]. | — | the worker |
| 7 *(E3)* | PW-TG | TG → PW | telnet; serial | telnet `IAC WILL` or `WONT TIMING-MARK`, or any payload; serial DCD asserted | target | E3; a `WONT` counts (session model §9 E3, §11 `link_timeout`). Serial without DCD has no liveness signal and never arms `link_timeout` (session model §10 `link_alive`, §11 `link_timeout`). A DCD drop is the connection gone, not a missed E3 (→ session-end-from-outside step 1); its `cause` arrives with serial, in a later release (ipc-control top). A telnet E3 is a `WILL` or a `WONT TIMING-MARK` answering any probe sent, the second answer to one probe included, or any payload; a `WILL`/`WONT` answering no probe the worker sent is not an E3, and the worker sends no reply to it; silence is no E3, and a peer that never answers reaches `link_timeout` (`now − last(E3) ≥ interval × count`). A target file sets `keepaliveInterval: 0` to turn `link_timeout` off for gear that never answers [C3/link-state-telnet, reference, owed by the E3 worker plan]. | — | the worker |
| 8 | CO-PW | PW → CO | `control.sock` | `link_state` `missing` | worker | The worker MUST send it when one `keepalive_interval` passes with no E3, on that transition only (`link_state`; CHANGELOG v0.2 "on a transition"), with `last_ack_at` the last E3's arrival, or the connection's establishment before the first [C3/link-state, e1, reference: U3 `worker/tests/test_keepalive.py`; shape: C3/control-message-shape-v0.2, e1]. Nothing ends, and no session changes state: it stays `live` or `detached` (session model §10). | — | the core |
| 9 | CO-PW | PW → CO | `control.sock` | `link_state` `answering` | worker | The worker MUST send it when E3 returns. A flap that recovers before `link_timeout` ends nothing (T6) [C3/link-state]. | — | the core |
| 10 | CO-PW | PW → CO | `control.sock` | `link_state`, either state, as the first event | worker | **The worker MUST send `link_state` as the first event to every new control peer of a connection that is `connected` — a restarted core's dial (→ core-restart step 12) and the core's one re-dial after a control-socket EOF (→ session-end-from-outside step 7) alike — so that core has `link_timeout`'s countdown at once; and MUST NOT send `link_state` before `connected`, when there is no link to report** (`link_state`; CHANGELOG v0.2). During the single_session wait, before that connection's `single_session` report, each new control peer gets one `answering` `link_state` (`last_ack_at` the last E3) as its first event, and no timer-driven `link_state` is sent for it [C3/link-state, e1, reference: U3 `worker/tests/test_keepalive.py`; C3/reattach-sequence, e1, `artifacts:control-reattach`, by tsbc's order rule (`contract/tsbc/_ipc.py` `conn_order`); C3/control-socket-stays-open, e1, reference; also C3/reattach-ended, C3/hello-no-such-session, C3/hello-unsupported-proto, e1, reference]. In these fixtures the peer that sent `connect` gets no `link_state` at `connected`; its first is a transition. | — | the core |
| 11 | — | CO → CO | — | → session-countdown | the core | From the connection's latest `link_state` the core computes the countdown, `last_ack_at` + `keepalive_interval` × `keepalive_count_max`, counted only while `missing`; only the core computes it (session-countdown rule 2; session model §11 `expiry_notice`). The core MUST NOT end a session on `link_state`: `link_timeout` is evaluated by the worker (session model §11) [owed]. | — | as session-countdown says: attached clients by `notice`, everyone else by the record |
| 12 | — | PW → PW | — | `link_timeout` fires | worker | now − last(E3) ≥ `keepalive_interval` × `keepalive_count_max`, so `link_alive` is false (session model §10, §11; T7), whether or not a core is attached. The worker MUST close the connection (session model §11, §13) [owed: no fixture makes `link_timeout` fire]. | — | — |
| 13 | PW-FS | PW → FS | a file, `spool/{session_id}.json` | `→ session-end-from-outside: spool-entry`, one per session on the connection | worker | `terminated` / `connection_lost`, written and fsynced before the worker acts on the end, step 14 included (session-end-from-outside step 3) [C5/spool-entry-shape]. With no core attached the spool carries the end (→ session-end-from-outside steps 10, 19; spooled-end-finalize). | — | the core that audits the end |
| 14 | CO-PW | PW → CO | `control.sock` | `connection_lost`, `cause` `keepalive_timeout` | worker | Names the connection and every session still open on it, retained ones not (`connection_lost`; lang-arch §6.4 step 3: "every session riding it"); whether a session whose `close_channel` is in flight counts: Open item 5 [shape: C3/control-message-shape, e0; the list: C3/connection-lost-lists-sessions, captured with `transport`]. The one TCP signal that counts is our socket gone, and that is `transport`, never `keepalive_timeout` (session model §8 A1; ipc-control top; → session-end-from-outside step 4). Nothing crosses this socket afterwards (→ session-end-from-outside step 4). | — | the core |
| 15 | PW-TG | PW → TG | SSH | the transport closes | worker | Every session on the connection goes with it (session model §13). Whether the worker sends `SSH_MSG_DISCONNECT` first, and this step's order against step 14, are unspecified (Open item 4). | — | the target |
| 16 | — | CO → CO | — | → session-end-from-outside | the core | Every listed session ends `terminated` / `connection_lost`: record, recording, audit, and close 4012 to every attached client (ipc-control top; session model §12, §13) [C3/ends-from-outside grades the mapping of every captured cause; a captured `keepalive_timeout`: owed]. | — | as session-end-from-outside says |

## Refusals and who is told

- **An out-of-range `keepalive_probe_interval`** is refused twice: by config load (→ config-reload step 2) and by a 0.3
  worker with `bad_request` (step 1). The frozen v0.2 worker refuses the key itself, whatever its value (#114,
  CHANGELOG v0.3 known gaps), which is why the core sends it only to 0.3 peers (D29). #114 is fixed by U3's E2 plan
  (decision queue A6).
- **No consumer step.** No frame or field carries `link_state` northbound. A surface sees `ends_by` `link_timeout`,
  `ending` and `notice` (→ session-countdown), and at the end close 4012 and the record (→ session-end-from-outside). A consumer takes the
  countdown only from the core (session-countdown rule 9).
- **No audit, no alert.** No audit event names `missing` or `answering`: the end is `session.disconnect` with
  `connection_lost` (`audit-event.schema.json#/$defs/session_end`), and no `alert_kind` names a link
  (`common.schema.json#/$defs/alert_kind`).
- **Siblings.** `link_timeout` ends every session on its connection and no other: another connection, even in the
  same worker, has its own transport (session model §13 and its worked example).

## Open items

1. **OPEN — decision queue A6 (plan §7 C-3; D28, D29).** Contract: with `keepalive_probe_interval` absent "the
   worker probes every `keepalive_interval` — v0.2's behaviour, so … a rolled-back v0.2 core still drives a 0.3 or 0.4
   worker" (`timer_values`; CHANGELOG v0.3 C3). Corpus: the value "must be above 0 and below `keepalive_interval`,
   or config load refuses it: probing at the same interval as the `missing` mark made a healthy idle link blink
   'not answering' once per interval (tsb-core #70)" (session model §11). Under a rolled-back v0.2 core a 0.3 or 0.4
   worker gets no value and blinks. A 0.2 worker under a 0.3 core never gets the field (D29), so it probes at the
   same interval too. Whether that is supported, and whether the blink is acceptable, is not ruled: parked for the
   approval step (tsb-core `plans/reports/queue-261007-operator-decisions.md` A6, which lists the options). D28 and D29 are
   unagreed DEFAULTs (conventions open item 3). Step 4's fallback is the contract's text, not a decision.
2. **No receiver rule for an unknown `link_state` `state`** (V5; conventions open item 5). Unspecified (not in plan
   §7). A fill is proposed in the decision queue (S91), not ruled.
3. **A reload while `missing`.** A `timers` that sets `keepalive_interval` to 0 turns `link_timeout` off, and such a
   connection "only ever reports answering" (`link_state`). Whether the worker sends `answering` at once, so the
   core drops the countdown, is unspecified (not in plan §7). A bound shortened below a clock already run is config-reload
   open item 6.
4. **How the transport closes** (step 15): whether the worker sends `SSH_MSG_DISCONNECT` before it closes, and the
   order of the close against `connection_lost`, are unspecified (not in plan §7). session-end-from-outside step 4 places the close
   after the event; no clause says so for `keepalive_timeout`.
5. **A closing session in `connection_lost`** (step 14; unspecified, not in plan §7). ipc-control `connection_lost`
   lists "every session still open on it"; lang-arch §6.4 step 3, "every session riding it". Neither says whether a
   session whose `close_channel` is in flight, awaiting `channel_closed`, is listed. connection-linger and
   session-end-from-outside carry the same question (their open items; steps 14 and 4).

## Picture

pending
