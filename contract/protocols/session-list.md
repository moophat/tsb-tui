# session-list — list and read sessions

| | |
|---|---|
| **ID** | session-list |
| **Purpose** | Show a principal the sessions it may see, each as one session record: its state, countdown, attached clients and size facts. |
| **Status** | built (E0–E1); the record grows at E2 (`clients`, `resizable`, `pinned`) |
| **Corpus clauses** | Contract §2, §3, §4; session model §10, §11 (`expiry_notice`); Storage §6, §9, §10.1a; open_decisions §3 |
| **Seam** | C1 |
| **Schemas** (contract-v0.3) | OpenAPI `GET /sessions` (`listSessions`), `GET /sessions/{id}` (`getSession`), `#/components/schemas/SessionList`, `#/components/schemas/SessionRecord`, `#/components/responses/Unauthenticated`, `#/components/responses/SessionNotFound`; `session-record.schema.json`; `common.schema.json#/$defs/session_state`, `common.schema.json#/$defs/session_type`; `error.schema.json` `x-codes` (`unauthenticated`, `session_not_found`, `invalid_request`) |

Words used below:

- A **principal** is a login, a **client** is one attachment to a session, a **surface** is the software (rules page,
  "Terms").
- The **login cookie** is `tsb_session`, set by `POST /auth/login` (→ login; `#/components/securitySchemes/session`).
- The **session record** is the core's description of one session. It is **closed**: a field the schema does not
  declare never reaches a consumer (`session-record.schema.json` top).
- The **countdown** is when the soonest timer that will end the session fires, and which timer that is (session model
  §11 `expiry_notice`).

Why it exists: the broker keeps the session and the surface is disposable, so a surface whose client died finds its
session again here (Contract §3; `C1/session-list-get`), and a user who logs in sees each session and when it will be
killed (session model §11 `expiry_notice`). Every surface uses these two reads; there is no privileged path
(Contract §2; OpenAPI `info`). The same record also reaches a surface in the `POST /sessions` answer (→ session-open) and in
the attach stream's `attached` frame (→ session-attach) (`session-record.schema.json` top). session-list owns only the two reads.

## Path

list (steps 1–3): SF-CO → SF-CO · read one (steps 4–6): SF-CO → SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `GET /sessions` with optional `ended`, `limit`, `cursor` | any principal holding the login cookie | none | no valid cookie: 401 `unauthenticated`; invalid query or cursor without limit: 422 `invalid_request` (rules 8–9) | the caller |
| 2 | — | CO → CO | — | — | the core | picks sessions the principal may see, applies `ended`, and, when `limit` is supplied, fills a page in descending `(created_at, id)` order (rules 8–9); a session it may not see is left out. The rule is unspecified (Open item 1) | — | — |
| 3 | SF-CO | CO → SF | HTTPS | 200 `SessionList` | the core | none: a read is not a byte of any session, so it resets no timer (session model §10 `activity`, `client_touch`) | — | the caller |
| 4 | SF-CO | SF → CO | HTTPS | `GET /sessions/{id}` | any principal holding the login cookie | none | no valid cookie: 401 `unauthenticated` | the caller |
| 5 | — | CO → CO | — | — | the core | checks that session `id` exists and that the principal may see it, by step 2's rule | no such session, or one the principal may not see: 404 `session_not_found` | the caller |
| 6 | SF-CO | CO → SF | HTTPS | 200 `SessionRecord` | the core | none, as step 3 | — | the caller |

### Rules for these steps

1. The core MUST answer both requests only to a valid login cookie, and otherwise 401 `unauthenticated` (OpenAPI
   `security`; `listSessions`, `getSession` responses). Fixture: owed. `C1/session-create-unauthenticated-401` grades
   `POST /sessions` only, and `C1/auth-logout` grades `GET /auth/me` only.
2. Subject to rules 8–10, the core MUST list every session the principal may see, and no other (`#/components/schemas/SessionList`: "Every
   session the principal may see"). Fixture: `C1/session-list-get` (required-green e0) grades that the principal's
   own new session is listed. That no other session is listed is owed.
3. The core MUST answer an id the principal may not see exactly as an id that does not exist: 404
   `session_not_found` (`#/components/responses/SessionNotFound`; `error.schema.json` `x-codes`). Fixture:
   `C1/session-list-get` grades the unknown id. The id that exists but is not visible is owed.
4. Every record the core sends MUST be valid against `session-record.schema.json` at contract-v0.3, its state rules
   included (the schema's `oneOf` and `anyOf`), and every list against `SessionList`. Fixtures:
   `C1/session-record-shape` (e0), `C1/session-record-shape-v0.2` (e1), `C1/session-record-shape-v0.3` (e2),
   `C1/session-unreachable` (e1, reference: graded by the U2 and U11 tests it names). The runner also validates each
   live answer of `C1/session-list-get` against its response schema (`contract/tsbc/_exchange.py`, top).
5. On every record of a session not ended, the core MUST put the countdown, and from contract-v0.3 also `clients`,
   `resizable` and `pinned`. On an ended record it MUST put no countdown
   (`session-record.schema.json#/properties/ends_at`, `#/properties/clients`, `#/properties/resizable`,
   `#/properties/pinned`). Fixtures: the countdown present, `C1/session-countdown` (e1); absent once ended,
   `C1/session-record-shape-v0.2`. `clients`, `resizable` and `pinned` present on a live v0.3 record: owed.
   `clients` leans on D5 (Open item 4).
6. An ended session (`killed` or `terminated`) MUST stay readable by `GET /sessions/{id}`, in its end state, for as
   long as the core retains its record (OpenAPI `getSession` description). Fixtures: `C1/session-delete` (`killed`,
   e0, live); `C1/session-ended-unexpectedly` (`terminated`, e0, reference).
7. A consumer MUST read the record by V2 and V5. In particular, a `state` it does not know is not attachable
   (`session-record.schema.json#/properties/state`; `common.schema.json#/$defs/session_state`), and a `session_type`
   it does not know is an opaque type, never an error (`common.schema.json#/$defs/session_type`). Fixture: owed; no
   fixture grades a consumer. Rendering the countdown is → session-countdown's; the recording warning shown before attaching is
   → session-attach's.

8. `GET /sessions` accepts optional `ended`, enum `include` | `exclude` | `only`, default
   `include`. `include` includes retained ended sessions; `exclude` removes every state defined as ended by
   `common.schema.json#/$defs/session_state`, including a later end state; `only` selects exactly that ended
   set, the complement of `exclude`. Apply the filter before filling a page, never widening step 2's visibility.
   Without `limit` or `cursor`, return the full retained visible list selected by `ended`; an old no-parameter
   request stays complete (V1). Invalid values are 422 `invalid_request`, with fixed rule text and no supplied
   value echoed (V4). Fixtures: `C1/session-list-ended-exclude`, `-include`, `-invalid`,
   `C1/session-list-ended-only`, `C1/session-list-legacy-complete` (published e4, required-green e5;
   reference, U2 tests owed).

   An older core can refuse new parameters with 422 or ignore them. A surface requesting exclusion MUST
   still drop ended records received from an older core; one requesting `only` MUST still select ended
   records locally. On a paging 422 it may fall back once to a legacy request without the unsupported
   parameters, MUST cap rendering and say the history is truncated, and MUST NOT repeat an unbounded
   fallback on every poll. An answer exceeding `limit` does not prove paging support: cap rendering and
   stop the walk. Consumer grading is owed. The thin client's `sessions()` retains its list return and
   no-argument request; `list_sessions(*, ended=None, limit, cursor=None)` returns a page envelope,
   never auto-drains and never automatically falls back (client tests).

9. **Paging.** Optional `limit` opts into paging and MUST be an integer from 1 to 500 (a sanity bound,
   no default and no page-size knob). Every paging request MUST include it: `cursor` alone is 422
   `invalid_request`. Order a page by creation time newest first, ties by session id descending:
   descending `(created_at, id)`, comparing timestamps as instants. The core keeps a creation-time index
   to serve this order; the index has no contract shape. A page MUST contain at most `limit` visible
   matching records, filling past invisible or filtered records until full or exhausted. A short page
   MUST be the last; an empty page can only be the last. Optional `next_cursor` MUST be absent on a
   short or empty page and on an unpaged answer, and present only when more visible matching records may follow.
   A full final page MAY omit it or carry one whose next page is empty; no look-ahead is required.
   It continues after the last record returned, never an invisible record.

   The cursor is an unsigned opaque encoded position consisting of that record's creation time and id,
   1..256 characters. A consumer copies it unchanged and never parses it. It carries no host detail,
   grants nothing, and binds neither principal nor filter: the core MUST re-evaluate visibility and
   `ended` on each page. Continuation MUST be strictly after the position in the declared descending
   order even if its record was removed; existence of the boundary record MUST NOT be required.
   Pages are not snapshots: creations, removals, permission changes and ended-state transitions between
   pages can change membership; a record may be missed or seen once. Restart from page one for a fresh
   walk. Paging bounds response records, not total search work for sparse selections. An `ended=exclude`
   answer's cost MUST NOT grow with the number of retained ended records: the core finds live sessions
   without reading the ended history. How it does so is the core's; no index or registry shape crosses the
   contract.
   The U2 provider performance test is owed with `C1/session-list-paged`.

   Malformed cursors MUST return 422 `invalid_request` with exactly `cursor is invalid`, never the
   supplied value. Other query
   refusals use fixed rule text without supplied values. Fixtures: `C1/session-list-paged`,
   `C1/session-list-cursor-without-limit`, `C1/session-list-cursor-invalid` (published e4,
   required-green e5; reference, U2 tests owed). Unknown/duplicate parameters and numeric limit parsing
   follow the existing strict query-refusal rules; provider coverage is owed.

10. **Ended-record retention.** `sessions.ended_retention` is one core-wide positive Go duration in
    `config.yaml`, required by the implementing core, optional in the additive schema, with no schema
    or code default (V7). Deployment templates supply seven days (`168h`); there is no test-login or
    per-target exception. A fully finalised ended record MUST NOT be removed earlier than this duration
    after its persisted `ended_at`; when `ended_at` is absent, age from its persisted `activity_at`
    (last byte either way, session model §10), never its creation or last-client-input time.

    Only a fully finalised end qualifies: its end marker and audit are written and worker release and
    reconciliation obligations complete. Live, detached, orphan-pending and unreachable sessions MUST
    NOT be removed. Pending end/audit/release obligations MUST survive retention and startup
    reconciliation; recheck eligibility atomically so a pending writer cannot resurrect a removed
    record. Removal MUST affect only its session-store record and associated grid snapshot, never a
    shared connection, sibling session, audit line or recording. Recording retention remains OPEN in
    open_decisions §2; this rule decides no recording or audit retention.

    Removal timing is the core's implementation (startup, then periodic bounded batches), with no
    contract sweep knob or timing fixture. Until actually removed a record MUST remain listed (subject
    to visibility and `ended`) and GET-readable, including across restart or a failed removal. A
    consumer can therefore meet a record older than its retention, never one removed early. After
    removal GET MUST answer the existing 404 `session_not_found`, indistinguishable from unknown or
    invisible. Fixtures: `C1/session-retention-safety`, `C1/session-get-removed-404`,
    `C5/config-sessions-ended-retention` (published e4, required-green e5; reference, U2 tests owed).
    They grade eligibility and completed removal, never the sweep's timing.

### Who writes the record's fields

session-list reads these values and never changes them.

| Fields | Written by |
|---|---|
| `id`, `target_id`, `connection_id`, `session_type`, `tier`, `created_at`, `term`, `recording` | → session-open |
| `state` `live`, `detached` | → session-attach, session-detach, client-remove |
| `state` `killed` · `state` `terminated` | → session-end-by-tsb · → session-end-from-outside; either, for an end spooled while the core was gone → spooled-end-finalize |
| `state` `unreachable`, `unreachable_reason` | → core-restart; a `hello` answered `unsupported_proto` or `unknown_cmd` → reattach-handshake |
| `owner` | → session-open (set at creation; nothing moves it at this tag) |
| `control_holder`, `clients` | → typing-ownership (who is attached: session-attach, session-detach, client-remove) |
| `ends_at`, `ends_by`, `ending` (the countdown) | → session-countdown |
| `cols`, `rows`, `resizable`, `pinned` | → size-control |
| a stale screen | no field. The record is closed and has none at contract-v0.3; staleness is told on the attach stream only (→ stale-screen) |

## Refusals and who is told

- `getSession` declares no 403. A principal that may see a session may read it, so `forbidden` ("can see the object
  but may not do this to it", `error.schema.json` `x-codes`) never answers a read (OpenAPI `getSession` responses).
- Only the caller learns of a read or a refusal. No attached client is told, and the audit vocabulary has no event for
  a read (`audit-event.schema.json` `$defs`).
- A consumer that meets an error code it does not know handles the answer by its HTTP status (V4).

## Private target disclosure

When a principal is authorized to see or join an existing session on a target whose share list does not name them, every session record, list and attach answer exposes its target ID only. It must not expose the target's name, host or definition. Session access does not confer inventory access or a credential.

## Security considerations

- **Visibility is a permission.** Authentication grants scoped access to sessions and targets (Contract §4). A
  session the principal may not see is absent from its list and reads as one that does not exist (rules 2, 3), so an
  id alone tells a caller nothing. Who may see which session is open (Open item 1).
- **A record names people.** It carries `owner` and each attached client's `user`
  (`session-record.schema.json#/properties/owner`, `#/properties/clients`). Whoever may see a session learns those
  logins.
- **The host boundary applies** (Contract §4; V11). The record is closed, so the recording's broker path, which is
  audit data (Storage §6), cannot reach a consumer: `C1/session-record-shape` grades a record carrying
  `recording_path` as invalid. An error's `message` carries no host path, and tsbc fails a live response whose
  message names one (`error.schema.json` top); here that is `C1/session-list-get`'s 404.

## Open items

1. **OPEN — which sessions a principal may see.** The contract says "may see" (`SessionList`, `SessionNotFound`,
   `x-codes` `session_not_found`) without a rule. The corpus defines visibility for targets only: `target.discover`
   plus the `targets:` filter (Storage §10.1a; open_decisions §3). The fixtures presume that a principal who "can see
   the target" and holds `session.create` on it sees another principal's session there (contract `README.md`
   "Inputs", `peer_user`; `C1/session-delete-not-owner-403` expects 403, not 404). No clause names the gate:
   `target.discover`, a join permission, `session.create`, ownership, or a combination. Steps 2 and 5 are
   unspecified until it is ruled. Not in plan §7.
2. **Resolved — ended sessions in the list.** Rule 8 includes them by default and permits excluding them.
3. **Resolved — ended-record retention.** Rule 10 defines `sessions.ended_retention`, restart readability
   until removal and the indistinguishable 404 after removal; cleanup scheduling belongs to the core.
4. **OPEN — D5 leaned on.** `clients` on the record (rule 5) is D5, which is in neither the agreed nor the open
   list on the approval page (queue A11). Not ruled.

## Picture

pending
