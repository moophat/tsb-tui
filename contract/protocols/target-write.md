# target-write — create, replace, read and delete a target definition

| | |
|---|---|
| **ID** | target-write |
| **Purpose** | A principal edits the eight target fields through the public API; the core writes its own source and waits for the index. |
| **Status** | E4 |
| **Corpus clauses** | Storage §2.1–§2.5, §3.5, §9, §10.1; Contract §4, §8, §13; open_decisions §3, §9 |
| **Seams** | C1, C5, C6 |
| **Schemas** | `openapi/tsb.openapi.json`: `createTarget`, `getTargetDefinition`, `replaceTarget`, `deleteTarget`, `TargetDefinition`, `TargetRecord`, `TargetWriteRecord`, `Me.permissions`; `schemas/error.schema.json`; `schemas/policy.schema.json#/$defs/permission`; `schemas/inventory-target.schema.json` (`x-layout`); `schemas/audit-event.schema.json#/$defs/target_write_event` |

Paths above are relative to `contract/`. Fixtures below are under `contract/fixtures/`.
The operator's target-write direction and the orchestrator's revision 2 rulings govern this flow. `target.edit` governs shared writes, while `target.private` governs the enabled caller’s own-target writes (owner only). Neither is implied by `*` or by the other. Shared writes use the existing target filters; own-target management is independent of shared filters.

## Path

SF-CO → CO-FS → CO-DK → CO-RD → SF-CO

The definition read follows SF-CO → CO-RD (visibility) → CO-FS (definition) → SF-CO; it never writes or audits.

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS | `POST /targets`, body `TargetCreateRequest`; `PUT /targets/{id}`, body `TargetDefinition`; `DELETE /targets/{id}`; `GET /targets/{id}/definition` | the cookie's principal | authenticate, check Origin, check `target.edit` or `target.private`, check no query parameter (422), then kind right on create (`target.private` for `share`, `target.edit` without); body id equals path id on PUT | 401 `unauthenticated` without cookie; 403 `forbidden` without permission; create and replace only: 422 `invalid_request` for query parameters, shape, secret keys, telnet user, supplied own id, or mismatched id [C1/target-create-secret-field-422; C1/target-replace; C1/target-write-query-parameter-422; C1/target-share-create-refusals] | caller |
| 2 | —; CO-RD for definition visibility | CO → CO; CO → RD for definition visibility | —; Redis read | visibility, permission, filters, source | the core | after the first right gate and shape, check identity, visibility and source: where the id lives determines the required right; shared writes require matching filters, own-target writes require owner holding `target.private`; an absent or invisible ID answers 404 word for word alike | 404 `target_not_found` alike for absent/invisible (owed U2 `TestEveryWriteWithoutThePermissionIs403AndInvisibleIs404`); 403 `forbidden` without destination permission, a named non-owner [C1/target-share-write-owner-only], outside filters [C1/target-create-outside-filter-403, owed U2], or another source [C1/target-edit-other-source-403; C1/target-definition-read]; definition read: 503 `inventory_unavailable` if visibility cannot be decided (owed U2) | caller |
| 3 | — for write mapping; CO-FS for definition read | CO → CO for write mapping; FS → CO for definition read | —; file read | definition → inventory file for writes; file → TargetDefinition for reads | the core | definition read: read the file and return the editable fields, without a write. Create and replace: map host, port, user under `options`; id, type, name, group, tags at top; set `discovered_by` to the selected source. Absent port means 22 for ssh, 23 for telnet. Quote timestamps and YAML 1.1 boolean/null strings (layout rule 10), then validate through the inventory loader. Replace the original eight fields; vendor, own prompt and share list are preserved when omitted, null vendor clears it, null prompt is invalid, null share is invalid, and empty prompt detaches. Carry `last_seen` and noneditable `options.*` unchanged. Optional original fields omitted by PUT are removed; vendor and own prompt follow the preservation rule above. Changing ssh to telnet drops user. Last write wins | create and replace only: loader refusal 422; definition read returns only the editable fields [C1/target-definition-read]; mapping and preservation owed U5 `TestEveryDefinitionTheWriterWritesLoadsBackEqualThroughRead` | caller |
| 4 | CO-FS | CO ↔ FS | flock, file read | `inventory/.lock`, exclusive; read `current` under it | the core | wait for a bulk swap to finish, then use its new current; create checks files in every source, not only the index; replace/delete checks where the id lives and ownership again under the lock | create id held anywhere: 409 `target_id_taken`, nothing written [C1/target-create-id-taken-409]; absent/other source: 404/403 [C1/target-edit-other-source-403]; disk/lock cases owed U5/U2 | caller |
| 5 | CO-FS | CO → FS | file write | target's source `<id>.yaml` | the core | create directory if absent with siblings' mode; write `.<id>.yaml.tmp`, fsync file, rename, fsync directory; delete removes the file and fsyncs directory; release lock | layout [C5/inventory-layout unchanged]; write/lock durability owed U5 `TestAWriteIsAtomicAndUnderTheLock` | — |
| 6 | CO-DK | CO → DK | audit JSONL append | `target_write_event`: `target.create`, `target.update`, `target.delete` | the core | as soon as the file lands and before the index wait, audit ts, user, target, detail.source and field names only. Create: fields set except id; update: changed fields, `[]` on no-op; delete: no fields | audit failure: 500 `internal_error`, change took effect and audit line is missing; takes precedence whatever the index does; client re-reads before retrying; the landed change remains [C6/event-target-create; C6/event-target-update; C6/event-target-delete; owed U2 `TestEachWriteIsAuditedWithFieldNamesNeverValues`] | audit log; caller on failure |
| 7 | CO-RD | CO → RD | Redis commands | watch re-read and index writer apply | the core | watch debounces 100 ms, re-reads tree, swaps parsed inventory, submits to asynchronous writer; wait for writer apply holding expected fields or deleted id absent, never for submit | after 5 s: 503 `inventory_unavailable`; the change took effect and is audited but is not yet listed; reload/restart indexes it [C1/target-write-index-timeout-503, reference, owed U2] (owed U2 `TestTheIndexWaitIsBoundedAndSays503WithTheFileWritten`, `TestTheIndexWaitWaitsForTheWritersApplyNotTheSubmit`) | caller |
| 8 | SF-CO | CO → SF | HTTPS | 201/200 `TargetWriteRecord`, 200 `TargetDefinition`, or 204 | the core | create/replace record equals indexed GET apart from `login_hint`, which a write answer never carries; delete removes target; existing sessions remain open [C1/target-create; C1/target-replace; C1/target-delete; C1/target-definition-read] | — | caller |

For `GET /targets/{id}/definition`, steps 1–2 check authentication, Origin, target permission, no query parameters, visibility, filters where applicable, and where the id lives, using CO-RD for visibility. Once allowed, read the file on CO-FS (the read side of step 3) and return
only `TargetDefinition` in step 8 [C1/target-definition-read]. The read never performs steps 4–7; unavailable
visibility answers 503 `inventory_unavailable` [owed U2]. Request-shape and loader 422 refusals apply only to
create and replace, never delete or the definition read.

Requirements: the core MUST enforce steps 1–2 [cited C1 fixtures; invisible and 401 cases owed U2]; MUST perform
steps 3–5 under the existing inventory writer rules [C5/inventory-layout; mapping/atomicity tests owed U5]; MUST
audit every landed write before the index wait (step 6), including a no-op update, with field names only on create
and update and no fields on delete [C6/event-target-*; U2 audit failure tests owed]; MUST wait for apply with the
bound in step 7 only after the audit line is written [C1/target-write-index-timeout-503; owed U2 tests named there]. The definition read MUST
never return keys outside `TargetDefinition` [C1/target-definition-read].

## Share list and generated IDs

Create kind is chosen by the presence of `share` in the body: present makes the caller's own target under `target.private` (owner must be in the list, no duplicate, existing accounts only, no kind entries); absent makes a shared target under `target.edit`. Shared creates with no ID and every own-target create use the ruled `tsb-create-target-id` HMAC allocation with a fresh 16-byte random input, checking all target files under the inventory lock. A supplied own ID or supplied shared ID beginning `g-` is invalid (422); old host-written files remain loadable. PUT keeps a required, immutable path-equal ID; on an own target, omitted `share` preserves the stored list, while `share` on a shared target is 422. Any query parameter on create, replace, delete or definition read is 422 `invalid_request`. There is no move between shared and own targets and no change of owner: copy and delete are separate actions. Writes audit the selected shared or validated owner source with field names only before the index wait; existing sessions live on.

## Vendor prompt catalogue

The catalogue is read-only product data shipped with the core release. Any authenticated principal may list or read templates; there is no instance template write, inferred vendor, generic fallback, personal overlay or template audit. A missing own `prompt_pattern` follows the release template named by `vendor`; a present pattern, including empty, detaches. The core validates bounds and characters without compiling or acting on patterns. The patterns below are proposed product data, synthesized from the labeled research claims, and require release review.

| Vendor ID | Pattern — **proposal** | Research's source label / example basis | Known limitation |
|---|---|---|---|
| `cisco-ios` | `[A-Za-z0-9._-]+(\([A-Za-z0-9._-]+\))?[>#]` | **Primary claim:** Cisco IOS/IOS-XE CLI command-mode documentation; `router>`, `router(config-if)#`. Pattern synthesized. | Custom prompts/spaces/ROMMON excluded; single-token output can resemble prompt. |
| `cisco-ios-xr` | `[A-Za-z0-9]+/[0-9A-Za-z]+/[0-9A-Za-z]+/[0-9A-Za-z]+:[A-Za-z0-9._-]+(\([A-Za-z0-9._-]+\))?#` | **Primary claim:** Cisco IOS XR “CLI Tips, Techniques, and Shortcuts”; `RP/0/RP0/CPU0:router#`. Pattern synthesized. | Excludes sysadmin-VM prompt form without the slash prefix. |
| `cisco-nxos` | `[A-Za-z0-9._-]+(\([A-Za-z0-9._-]+\))?[>#]` | **Primary claim:** Nexus NX-OS Fundamentals, command modes; `switch(config-vrf)#`. Pattern synthesized. | Custom prompt and underlying shell excluded; single-token false positives possible. |
| `juniper-junos` | `[A-Za-z0-9._-]+@[A-Za-z0-9._-]+[>#]` | **Primary claim:** Junos CLI User Guide, operational/configuration modes. Pattern synthesized. | Excludes root C-shell `%`; email-like output can resemble a prompt. |
| `arista-eos` | `[A-Za-z0-9._-]+(\([A-Za-z0-9._,:+-]+\))?[>#]` | **Primary claim:** Arista EOS User Manual, CLI command modes and interface ranges. Pattern synthesized. | Excludes custom timestamps/spaces and Bash; prompt-like single-token output possible. |
| `huawei-vrp` | `(HRP_[MS])?(<[A-Za-z0-9._-]+>\|\[[~*]?[A-Za-z0-9._/-]+\])` | **Primary claim:** Huawei VRP command views and USG HRP prompt indicators. Pattern synthesized; table escape before alternation pipe is Markdown only. | Nonmatching sysname characters excluded; standalone XML tags/bracketed headers can match. |
| `mikrotik-routeros` | `\[[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\]( /[A-Za-z0-9._/-]+)? >` | **Primary claim:** MikroTik Console/CLI documentation; `[admin@MikroTik] >`. Pattern synthesized. | Excludes identity spaces, some submenu spacing and Safe Mode `<SAFE>`. |
| `fortinet-fortios` | `[A-Za-z0-9._-]+( \([A-Za-z0-9._-]+\))? [#$]` | **Primary claim:** FortiOS CLI basics/permissions/sub-shells, `FortiGate (global) #`. Pattern synthesized. | Object names with spaces/slashes excluded; word-plus-marker output can match. |
| `paloalto-panos` | `[A-Za-z0-9._-]+@[A-Za-z0-9._-]+(\([A-Za-z0-9._-]+\))?[>#]` | **Primary claim:** PAN-OS CLI Quick Start / High Availability guide; HA-state prompt suffix. Pattern synthesized. | Non-ASCII/custom names excluded; email-like output can match. |
| `posix-shell` | `([^ \t]+ )?[$#]` | **Primary claim:** POSIX Shell Variables (PS1), Bash “Controlling the Prompt”. Pattern synthesized. | Narrow proposal: misses `bash-5.2$`, common no-space prompts and Zsh `%`; bare `$`/`#` can be output. |

The Huawei pattern as a literal (no Markdown table escaping) is:

```text
(HRP_[MS])?(<[A-Za-z0-9._-]+>|\[[~*]?[A-Za-z0-9._/-]+\])
```


## Refusals and who is told

Only the caller receives a refusal. An absent target and an invisible target are answered alike, word for word,
404 `target_not_found`; no file is written on a validation/authorization/id conflict refusal. Another source's
visible target answers 403 with fixed text: "This target comes from <source> and is not edited through TSB."
`discovered_by` already exposes that source in the public record. A timed-out index or failed audit does not undo
the file write. Audit failure takes precedence over the index and answers 500 `internal_error`: the dedicated
`TargetWriteNotAudited` response says the change took effect and its audit line is missing; a client re-reads before
retrying. Only with the audit line written can the index wait time out to 503 `inventory_unavailable`, whose message
says the change took effect and is audited but is not yet listed. A definition read instead answers 503 when its
visibility decision cannot be made; it has no write or audit line. The vault’s `ActNotAudited`
response is never used here. A foreign-origin browser write is 403 `forbidden` on POST,
PUT and DELETE under the existing same-origin gate [C1/auth-cross-origin-403; writes owed U2].

## Security considerations

- The definition is closed and has no secret field. The inventory loader's refused `password`, `privateKeys`,
  `credential_ref` keys still apply [C1/target-create-secret-field-422; C5/inventory-target-valid]. A saved login is
  a separate vault resource; this API never saves one.
- Only the core on a principal's request writes reserved source `tsb` or an owner's own source; no discovery writer or adapter writes either.
  Another source's files are never edited [C1/target-edit-other-source-403; x-layout rule 11; disk check owed U5/U2].
- The worker's deny list stays at connect (session-open step 8): no core resolver or deny-list check at create.
  A refused address answers 502 `target_connect_failed` at connect [C3/target-address-refused].
- No host filesystem path appears northbound (Contract §4). The target account user is visible only in the editor's
  definition; public `TargetRecord` remains unchanged [C1/target-definition-read].
- Live sessions are untouched by replace/delete. A subsequent session create on a deleted id is 404; changed host
  applies to the next connection [owed U2 `TestADeleteRemovesTheFileAndTheIndexEntryAndLeavesSessionsOpen`].

## Open items

No unruled target-write design point. Reference fixtures and disk/index/audit tests remain owed by phases 3–4;
operator-authored corpus alignment and release approval remain release gates. Adapters remain outside this flow.

## Picture

pending
