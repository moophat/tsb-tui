# inventory-list — list and read the targets a principal may see

| | |
|---|---|
| **ID** | inventory-list |
| **Purpose** | List and read the targets a principal may see, from the core's index: filters, a page at a time, one record by id, with an optional caller-relative SSH login hint, never credential material. |
| **Status** | E3; optional login hints published E4, required E5 |
| **Corpus clauses** | Storage §2.2, §2.3, §9, §10.1a; Contract §1, §4, §6 |
| **Seams** | C1; the index on edge CO-RD is U5's own and has no contract shape (CO-RD) |
| **Schemas** (`contract/schemas/` at this tag) | OpenAPI `GET /targets` (`listTargets`), `GET /targets/{id}` (`getTarget`), `#/components/schemas/TargetRecord`, `#/components/schemas/TargetList`, `#/components/parameters/TargetId`, `#/components/parameters/TargetLimit`, `#/components/responses/Unauthenticated`, `#/components/responses/InvalidRequest`, `#/components/responses/TargetNotFound`, `#/components/responses/InventoryUnavailable`; `error.schema.json` (`x-codes`: `unauthenticated`, `invalid_request`, `target_not_found`, `inventory_unavailable`); `inventory-target.schema.json` (the file a record comes from); `common.schema.json#/$defs/id`, `#/$defs/timestamp` |

Schema paths are relative to `contract/schemas/` (OpenAPI: `contract/openapi/tsb.openapi.json`).
Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/`. Corpus files, under `srs/`:
Storage = `storage-and-discovery-architecture.md`, Contract = `architecture-contract.md`.

inventory-list reads what any writer of inventory files writes. The core also writes one source, `tsb`, on a principal’s request through the API: [target-write](target-write.md). A target record is
the index's answer, not the file: `options`, `user`, a credential reference and a login script never reach it.
The record preserves the own `prompt_pattern`, including its absence or empty value. When it is absent, the core reads the release-owned template named by `vendor` to supply an effective pattern; it never compiles or acts on that pattern.

**Words used here.**

- **Index**: the core's Redis search index over the target files, the only writer being the core (Storage §2.2;
  Contract §8). It has no contract shape.
- **Visible**: a shared target requires `target.discover` and a matching `targets:` filter; an own target is visible to every enabled caller its share list names while its owner is enabled, regardless of `target.discover` or target filters (SL-1, SL-2 DEFAULTs). Visibility is not reachability: session creation still requires `session.create` and a matching target filter.
- **Login hint**: response-only metadata classification for this caller, never part of the shared index or target YAML. Its single registry is OpenAPI `TargetRecord/$defs/login_hint` (V5).
- **Page**: one answer of at most `limit` targets, ordered by `id` ascending; `next_cursor` continues after the last
  `id` returned.

## Path

SF-CO → CO-RD → SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `GET /targets` with optional filters `type`, `tag` (repeatable), `group`, `discovered_by`, `limit`, `cursor` | a principal with the cookie | — | no cookie: 401 `unauthenticated` [C1/targets-list-unauthenticated-401]; a parameter outside this list, or a value outside its range (`limit` is 1..500, a sanity bound): 422 `invalid_request` [C1/targets-list-filtered] | the caller |
| 2 | — | CO → CO | — | — | the core | the visible set combines shared targets allowed by `target.discover` and a matching filter with own targets naming the enabled caller while their owner is enabled, regardless of discover or filters (SL-1, SL-2) | no `target.discover`: shared targets omitted; own targets remain visible [C1/targets-list-no-discover-empty] | — |
| 3 | CO-RD | CO → RD | a Redis command | `FT.SEARCH idx:targets` with the filters, always with `LIMIT`, paged to the page's end (Storage §2.2) | the core | reads the index; nothing here writes it | Redis unavailable: 503 `inventory_unavailable` [C1/targets-unavailable-503] | the caller |
| 4 | SF-CO | CO → SF | HTTPS | 200 `TargetList` | the core | visible targets only, ordered by `id`; own targets not naming the caller never affect cursor or page fullness; records of own targets carry `share` and `owner` (SL-5 DEFAULT); the core fills the page by searching further windows of the index until it holds `limit` visible targets or the index is exhausted, so `next_cursor` (naming the last target returned) is present when the page is full and visible targets may follow, and absent on the last page; after visibility, cursor and final page selection, annotate eligible SSH rows by the rules below, preparing one context only if eligible rows exist; no broker-host path (V11) [C1/target-login-hint-saved; C1/targets-list; C1/targets-list-paged; C1/target-record-shape] | — | the caller |
| 5 | SF-CO | SF → CO | HTTPS | `GET /targets/{id}` | as step 1 | — | as step 1 | the caller |
| 6 | CO-RD | CO → RD | a Redis command | the record of that id | the core | — | absent or invisible: 404 `target_not_found`, word for word alike [C1/target-get-unknown-404; C1/target-get-invisible-404]; Redis unavailable: 503 | the caller |
| 7 | SF-CO | CO → SF | HTTPS | 200 `TargetRecord` | the core | annotate the single eligible SSH row with the same request-context rules below [C1/target-get; C1/target-login-hint-saved] | — | the caller |

- **Paging.** A page holds at most `limit` targets in `id` order. The core fills the page by searching further
  windows of the index until it holds `limit` visible targets or the index is exhausted, so `next_cursor` continues
  after the last target RETURNED, never after a target the principal cannot see. A page holds fewer than `limit` only
  when it is the last, and then has no `next_cursor`; an empty page is only the last. A target added or removed
  between two pages may be missed or seen once: pages are not a snapshot [C1/targets-list-paged, owed by the E3 core
  plan].
- **`last_seen` is UTC with `Z`.** A target file may carry `last_seen` in any zone (`inventory-target`'s
  `timestamp_any_zone`); the record is that instant written in UTC with `Z`, so a record never carries an offset. One
  the core cannot convert makes the file invalid — fleet-discovery's case, not a record's [C1/target-record-shape].
- **A serial record carries no `port`.** A serial device path is a broker-host path and never leaves the core (V11;
  Contract §4) [C1/target-record-shape].

## Caller-relative login hint

The following requirements are graded by `C1/target-login-hint-*`, all published e4 and
required-green e5. Reference provider tests remain owed in `fixtures/graded-by.json`.

- **Eligibility.** The core MUST look up the returned record's id in the same runtime target store
  create uses. Both the returned record and runtime target MUST be SSH. The core MUST use one
  captured runtime target view for visibility, target-scoped `session.create`, protocol eligibility
  and selector matching. It MUST omit the field without resolution when the runtime target is absent,
  runtime visibility fails or target-scoped `session.create` fails. Telnet, echo, serial and unknown
  types carry no hint; echo's synthetic credential-free candidate is never a saved login. The core
  MUST NOT compare indexed host/tags/type to runtime fields to compute a hint, use indexed data as a
  credential-matching fallback, or publish a synchronization-lag state. Existing index visibility gates
  still run first [C1/target-login-hint-authorization; C1/target-login-hint-runtime-absent;
  C1/target-login-hint-protocol-omission].
- **One request context.** After final page selection, the core MUST prepare one metadata resolution
  context only if eligible rows exist, or one for an eligible single read. It MUST read the caller's
  profiles once under the existing gate, copy personal reference/kind and structural sealed status,
  scan grants once retaining only the caller's, read relevant org metadata in bounded storage
  transactions, parse selectors once, then release storage locks before matching returned targets.
  No cross-request resolution cache, coordinated snapshot, reservation or persisted hint exists.
  Inputs are best effort per request; create reads fresh state and rechecks. Preparation/evaluation
  MUST preserve create's error order: target-specific missing mappings affect only that target;
  profile read failure affects all eligible rows; grant-stage errors never override earlier personal
  errors. Matching cost can grow with targets and selectors; constant-count preparation reads are
  not a constant total cost [C1/target-login-hint-saved; C1/target-login-hint-recheck;
  C1/target-login-hint-resolution-faults; supporting U2/U6 white-box tests owed].
- **Shared candidates, no selection.** The core MUST use create's new-connection, no-supplied-login,
  no-explicit-choice metadata rules: the caller's target mapping and matching caller org grants,
  no personal/org precedence, deduplicating `(reference, layer)`. Two grants of one org reference
  count once; equal reference names across personal and org layers count twice. Unmapped entries,
  other users' mappings and unmatched grants do not count. A missing mapped entry MUST remain an
  error even if an org alternative exists [C1/target-login-hint-create-parity;
  C1/target-login-hint-dedup-layers; C1/target-login-hint-caller-scope].
- **Classification.** For every eligible present runtime SSH target the core MUST emit one value:
  successful zero candidates (including metadata no-resolving) is `none`, one distinct candidate
  `saved`, two or more `choice_required`. Every other resolution fault is the fixed `unavailable`,
  including missing mapped entries, dangling matching org references, malformed selectors/records,
  and an unsealed personal record backing a candidate. A caller grant with a dangling reference or
  broken org entry faults only targets selected by its well-formed selectors, an unselecting grant
  contributes nothing, and a malformed selector faults every eligible SSH target of that caller.
  The unsealed-record check is a hint-only structural health
  check, never decryption or a change to create's refusals. An unmapped unsealed personal vault
  alone MUST NOT poison a valid org-only result. Faults MUST NOT change the inventory's successful
  status or erase other records; index failure retains its existing 503 path
  [C1/target-login-hint-none; C1/target-login-hint-saved; C1/target-login-hint-choice-required;
  C1/target-login-hint-unavailable; C1/target-login-hint-resolution-faults].
- **Metadata, never material.** The core MUST NOT decrypt, retrieve or pass credential material,
  inspect unlock state, unlock or extend an unlock for a hint. It reads only the caller's mapping,
  personal reference/kind index and structural sealed status, caller grants and matching org metadata.
  Locking the caller's vault or the storing administrator's org vault MUST leave classification
  unchanged, including kind `none` and broker-unlocked entries. `saved` and `choice_required` mean
  candidates exist in metadata; establishment can still return `no_resolving_credential` when
  material is missing, or require an unlock or fail authentication. No future permission, liveness,
  reusable connection or authentication success is promised [C1/target-login-hint-locked-vault;
  C1/target-login-hint-metadata-not-material; supporting U2/U6 white-box tests owed].
- **Disclosure and caching.** The core MUST return no credential reference, candidate list, kind,
  label, login name, secret, storing administrator identity, unlock state, profile path or host detail
  in the hint or its error text. A resolution fault is only the enum, never a diagnostic. It MUST
  send `Cache-Control: no-store` (alone or with other directives) on any response carrying a hint, including `unavailable`, and MUST
  NOT persist it in YAML, the shared Redis record or audit. Caller-relative candidate listing needs
  no audit line, as credential listing has none [C1/target-login-hint-never-revealed;
  C1/target-login-hint-saved; C1/target-login-hint-unavailable].
- **Consumer behavior.** A consumer MUST suppress an absent or unknown hint, retain the target and
  normal connect flow, and never infer `none` from type, grants, lock state, a previous create or
  local storage. `unavailable` MUST suppress saved/none availability wording. `choice_required`
  offers the existing connect-time operator choice, never auto-selection. The hint conveys no
  permission [C1/target-login-hint-older-core-and-unknown; C1/target-login-hint-choice-required].

C1 fixtures assert wire status, fields/omissions, fixed values, forbidden disclosure and headers
only. Internal no-read gates, one-context/read counts, absence of material/unlock access or
extension, shared resolver parity and error ordering belong to the supporting U2/U6 tests named
as owed in the grading index, not to a separate C1 one-context fixture.

## Refusals and who is told

- **The gate never confirms that a target exists.** An absent target and one outside the principal's filters are
  answered 404 alike, word for word, as session-open step 2 already requires [C1/target-get-invisible-404, owed by
  the E3 core plan].
- **No credential material is retrieved** by either operation. Eligible SSH rows resolve metadata candidates only, as above. "A target that is visible with no resolving credential is a
  legitimate state: listed in inventory, not connectable" (Storage §10.1a). A surface reads the record's `type` to
  ask `connection: new` for a telnet target; it never resolves a credential from a record.
- **The index being down** answers both operations 503 `inventory_unavailable`, and session create still works
  [C1/targets-unavailable-503, owed by the E3 core plan].
- **Who is told.** Only the caller. A refused list writes no audit event: the vocabulary has none for it.

## Security considerations

- **The gate never confirms that an invisible target exists** (step 2; Storage §9, §10.1a).
- **A serial device path never leaves the core** (V11; Contract §4) [C1/target-record-shape].
- **Target strings come from sources TSB does not control** (a CMDB, a hand-written file), so a surface renders
  `name`, `group` and `tags` as text, never as markup or terminal escapes, and the schema refuses control characters
  in them [C5/inventory-target-valid; C1/target-record-shape].

## Open items

- **A rebuild on demand.** SF-CO plans "rebuild the index on demand". This release defers it: the index is rebuilt
  at start and on a file change (fleet-discovery), never by a request.

## Picture

pending
