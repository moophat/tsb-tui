# scroll-control — older output on request

| | |
|---|---|
| **ID** | scroll-control |
| **Purpose** | Give one client older output when it asks: whole raw output frames before a seq, starting where the terminal parser was clean, never across a gap, touching nothing else. |
| **Status** | E5: published at E2 (an E2 core answers 501 `not_implemented`), required at E5 |
| **Corpus clauses** | Frontend §6.1, §6.2, §6.3, §8; Contract §11; immortality §4.1; session model §9 E1, §10 |
| **Seams** | C1, C2, C4 |
| **Schemas** (contract-v0.3) | `contract/openapi/tsb.openapi.json`: `GET /sessions/{id}/output` (`outputPage`), `#/components/parameters/SessionId`, `#/components/parameters/BeforeSeq`, `#/components/parameters/MaxBytes`, `#/components/responses/Unauthenticated`, `#/components/responses/Forbidden`, `#/components/responses/SessionNotFound`, `#/components/responses/InvalidRequest`, `#/components/responses/NotImplemented`; `contract/schemas/ws-frames.schema.json#/$defs/prefix/properties/scrollback`; `contract/schemas/vt-ipc.schema.json#/$defs/applied`; `contract/schemas/common.schema.json#/$defs/u64`; `contract/schemas/error.schema.json` `x-codes` (`unauthenticated`, `forbidden`, `session_not_found`, `invalid_request`, `not_implemented`) |

Words used below:

- **seq** is the per-session output sequence number the worker gives each output frame (immortality §4.1). A page
  is addressed by seq and sized in bytes, never in rows. Contract §11 forbids any "read row N" or cell-content
  endpoint, and rows shift with width (Frontend §6.2).
- **The ring** is the core's in-memory buffer of a session's recent raw output (Contract §11: "recent output for
  replay and web pagination"). Its size is a `config.yaml` knob (Frontend §6.2, §8). It is not the worker's ring
  (immortality §4.1). No core has one before E5 (`contract/CHANGELOG.md` known gaps).
- **Ground** is the VT parser's state outside any escape sequence. A **ground start** is a frame boundary where the
  parser was at ground, so a page that starts there never begins mid-escape (vt-ipc `applied`; ruling 5.48).
- **The page cap** is the knob bounding how far back a page may reach for a ground start (`MaxBytes`; conventions V7).
- A **gap** is output declared lost (→ reattach-handshake).

Why it exists: a browser stores parsed cells and cannot hold long history, while the core holds raw bytes cheaply
(Frontend §6.1). So a web client keeps a few thousand lines and asks for more as its user scrolls up. Pagination is
"a hard requirement for web mode" (Frontend §6.2). Each client asks on its own, and a page is a rendering request.
Tu: "scrolling UP doesnt affect new frame or act as new input on the shell, it's a RENDERING request" (Frontend §6.2).

## Path

ground marks (steps 1–3): CO-VT → CO-VT · scrollback amount (step 4): SF-CO · a page (steps 5–9): SF-CO → SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-VT | CO → VT | unix socket | `→ byte-relay: kind 2 output frame` | the core | the VT applies every output frame, gap frames included, in seq order (vt-ipc top, "Order and idempotence") | — | the VT worker |
| 2 | CO-VT | VT → CO | unix socket | `applied` | the VT worker | the core learns, batched, the seqs after which the parser was not at ground (`mid_sequence`), up to `through_seq` | — | the core |
| 3 | — | CO → CO | — | — | the core | keeps each output frame in the session's ring (Contract §11), bounded by the ring-size knob (Frontend §8). When the session ends, its ring is released (`outputPage`) | — | — |
| 4 | SF-CO | CO → SF | WebSocket | `→ session-attach: prefix` (`scrollback`) | the core | the attaching client learns `first_seq`, the oldest seq the ring holds, and `bytes`, what the ring holds up to `through_seq` | — | that client |
| 5 | SF-CO | SF → CO | HTTPS | `GET /sessions/{id}/output` (`before_seq`, `max_bytes`) | a principal that may attach to the session | none | no valid login cookie: 401 `unauthenticated`; a parameter outside `u64`: 422 `invalid_request` | the caller |
| 6 | — | CO → CO | — | — | the core | checks the session, the right to page and that a ring exists | no such session, or one the principal may not see: 404 `session_not_found`, never an empty page; may see it but may not attach: 403 `forbidden`; no ring (an E2 core): 501 `not_implemented` | the caller |
| 7 | — | CO → CO | — | — | the core | picks frames first..last. Last is the newest frame below `before_seq` (absent: the newest in the ring). Older whole frames are added up to `max_bytes`. First is a ground start by step 2's marks. The page stops at a gap. If no ground start lies within `max_bytes`, the page reaches back to the nearest one, at most the page cap | — | — |
| 8 | SF-CO | CO → SF | HTTPS | 200 `application/octet-stream` + `TSB-Page-First-Seq`, `TSB-Page-Last-Seq`, `TSB-Page-More`, `TSB-Page-Gap` | the core | none on the session (rule 10) | — | the caller only |
| 9 | SF-CO | CO → SF | HTTPS | 200, empty body + `TSB-Page-No-Ground`, `TSB-Page-Next-Before-Seq` | the core | none. Sent instead of step 8 when no ground start lies within the page cap | — | the caller only |

### Rules for these steps

Providers:

1. **No ring, no pages.** A core without a ring MUST answer every page request 501 `not_implemented` (`outputPage`;
   `NotImplemented`; `error.schema.json` `x-codes`). Fixture: owed. `C1/output-page-501` grades only the 200 branch
   of an E5 provider, and says no fixture forces the 501.
2. **Login.** The core MUST answer a request without a valid login cookie 401 `unauthenticated` and serve no page.
   Fixture: `C1/output-page-401` (live, required e5).
3. **Unknown session.** The core MUST answer a session that does not exist, or one the principal may not see, 404
   `session_not_found`, never an empty page. Fixture: `C1/output-page-404` (live, e5) grades the unknown session.
   The session that exists but is not visible is owed.
4. **The right to page is the right to attach.** The core MUST serve a page exactly when the principal may mint an
   attach ticket (→ session-attach, `mintWsTicket`): a `session.join.ro` or `.rw` holder, or the owner. Anyone else who may see
   the session MUST get 403 `forbidden` (`outputPage`; CHANGELOG C1 "Pages", D14). Fixture: owed.
5. **Whole frames.** The body MUST be the raw output of whole frames first..last, none split, with last the newest
   frame the ring holds below `before_seq`. The headers MUST name first and last, and `TSB-Page-More` MUST say whether
   older output remains. A client that sends the first seq of one page as the next `before_seq` walks the ring
   backwards. Fixture: `C1/output-page` (live, e5).
6. **Never across a gap.** A page MUST NOT span a gap. When output was lost right before its first byte,
   `TSB-Page-Gap` MUST be true (`outputPage`; D13). Fixture: `C1/output-page-gap` (reference; U2 test owed).
7. **Ground start.** A page MUST start at a ground start by the VT's marks (step 2). It holds at most `max_bytes`
   unless no ground start lies within. Then it reaches back to the nearest one, bounded by the page cap (`outputPage`;
   `MaxBytes`). Fixture: `C1/output-page-ground-start` (reference; U2 test owed).
8. **No ground within the cap.** The core MUST then send an empty body with no first or last seq header,
   `TSB-Page-No-Ground` true and `TSB-Page-Next-Before-Seq`. It MUST NOT send an explicit false end of history
   (`outputPage`). Fixture: `C1/output-page-ground-start` (reference; U2 test owed). See Open item 2.
9. **An ended session.** Its ring is released; the core MUST answer an empty body (`outputPage`). Fixture: owed.
10. **Nothing else.** A page MUST NOT touch the session, the PTY, input or another client. It sends no byte to the
    target, no resize, and no frame to any attached client (`outputPage`; CHANGELOG C1 "Pages"; Frontend §6.2). It
    resets no timer, because a REST call is not an E1 event (session model §9 E1; §10 `activity`, `client_touch`).
    The audit vocabulary has no event for it (`audit-event.schema.json` `$defs`). Fixture: owed.
11. **The scrollback amount.** From E5, every `prefix` the core sends MUST carry `scrollback` (→ session-attach): `first_seq`
    the oldest seq the ring holds, `bytes` what it holds up to `through_seq` (ws-frames `prefix`; CHANGELOG C2, D4).
    Fixture: `C2/attach-scrollback-amount` (live, e5); that `bytes` equals the frames' payload bytes is owed (U2 test).
12. **Ground marks.** The VT worker MUST send `applied`, batched. `mid_sequence` MUST list exactly the seqs after
    which its parser was not at ground, and `through_seq` MUST be the highest seq applied (vt-ipc `applied`; top,
    "Ground marks (D22)"). Required at E5: pages are the marks' only consumer. Fixture: `C4/vt-applied-ground`
    (reference; U4 and U2 tests owed).

Consumers:

13. A consumer MUST NOT page a session whose `prefix` carried no `scrollback` (`outputPage`, receiver rule). So a
    session type with no `prefix` (ws-frames `prefix`: PTY `shell` only) is never paged, and an E2 core, with no ring
    to report, is never asked. Fixture: owed; no fixture grades a consumer.
14. A consumer that continues past a no-ground answer MUST send `TSB-Page-Next-Before-Seq` as its next `before_seq`.
    It MUST NOT read the empty body as the end of history (`outputPage`, `TSB-Page-No-Ground`). Fixture: owed.
15. A consumer MAY keep as much scrollback as it chooses, and page as far back and as often as its user asks. The
    client renders and owns its scrollback (Frontend §6.2), and on the desktop the amount is the operator's (§6.3).
    How the client renders a page above what it already holds is up to its own terminal (Frontend §6.2, Tu).

## Refusals and who is told

- Only the caller learns of a page or a refusal. No attached client is told, and nothing is audited (rule 10).
- A consumer handles an error code it does not know, `not_implemented` included, by its HTTP status (conventions V4).
- 422: `InvalidRequest` and `x-codes` `invalid_request` speak of a request body; this operation has none, so here a
  422 means `before_seq` or `max_bytes` lies outside `u64` (a wording point, not in plan §7).

## Open items

1. **S-1, the restart sentence** (plan §7 S-1; verifier: wording only). No core has a ring before E5, so the known
   gap "Pages after a core restart start at the replay — the ring is not yet rebuilt across a restart at E2" means
   E5: the ring is not rebuilt across a core restart, and afterwards pages reach back only to the worker's replay of
   seqs above the commit point (immortality §4.1; → reattach-handshake, core-restart). Text to fix: that line under "Known gaps" in
   the contract-v0.3 entry of `contract/CHANGELOG.md`; the verifier's wording is "From E5: … older history comes from
   recordings (development-plan §8 row 10)".
2. **G-18, older history from recordings** (plan §7 G-18; already ruled). Ruling 5.48's default: from the ring "for
   now, older history from recordings later", placed at E5 after development-plan §8 row 10 (recording storage); no
   tag named yet. Unspecified meanwhile (not in plan §7): whether `TSB-Page-More: false` at the ring's oldest frame
   means nothing older in the ring, or nothing older at all.
3. **`max_bytes` absent: unspecified** (not in plan §7). `BeforeSeq` says what absent means; `MaxBytes` does not.
4. **Header presence: unspecified** (not in plan §7). Only the first and last seq headers have an absence rule;
   whether `TSB-Page-More`, `-Gap`, `-No-Ground` are sent as false or left out (an ended session's page too) is not said.
5. **A page of a session with no `prefix`: unspecified** (not in plan §7). Rule 13 keeps consumers from asking;
   what the core answers is not said.

## Picture

pending
