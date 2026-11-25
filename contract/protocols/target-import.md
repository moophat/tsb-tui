# target-import — preview and confirm a raw target export

| | |
|---|---|
| **ID** | target-import |
| **Purpose** | Preview a supported raw export, then explicitly commit or abort; resume a confirmed receipt without secrets. |
| **Status** | E3b; credential saving requires E4 vault support. |
| **Corpus clauses** | Storage §2, §3, §7.4, §9, §10.1a–§10.1b; credential protection §8.1–§8.4, §10.2, §10.4; Contract §2, §4, §6, §8, §12–§15. |
| **Seams** | C1, C5, C6, C7. |
| **Schemas** | `openapi/tsb.openapi.json`, `schemas/inventory-target.schema.json`, `schemas/import-receipt.schema.json`, `schemas/error.schema.json`, `schemas/vault.schema.json`, `schemas/config.schema.json`. |

## Path

Read from the steps, with core-internal computation omitted:

- Preview: **SF-CO → CO-FS → SF-CO**.
- Save: **SF-CO → CO-FS → CO-BB → CO-BB → CO-DK → CO-BB → CO-FS → CO-DK → CO-FS → CO-RD → CO-BB → SF-CO**. The first CO-BB inspects a receipt; the next opens/stages the vault transaction; CO-DK audits before its CO-BB commit.
- Discard/no-readable-secret commit: same validation/receipt/publication path, without credential-record or credential-audit writes; personal credential mapping is omitted.
- Abort: **SF-CO → CO-FS → CO-BB → SF-CO**, with policy/receipt reads only; never an undo.
- Resume: **SF-CO → CO-FS → CO-BB → CO-FS → CO-DK → CO-FS → CO-RD → CO-BB → SF-CO**, traversing only remaining publication/audit/mapping/index/checkpoint stages. It never traverses the credential-store/audit transaction. Policy is read before the caller-owned receipt; destination state is checked after that receipt identifies it.

No export file crosses CO-FS. The export arrives only inside the HTTPS request. CO-FS holds TSB's own policy, inventory, publication marker and profile data. Redis's target index remains U5-owned, without a new contract wire schema.

## Steps

`POST /target-imports` (`importTargets`), JSON with cookie authentication, carries the closed tagged request. Preview and commit supply padded-base64 raw bytes plus explicit format, and for an own import an optional `share` list (absent means shared); commit also supplies the token and required readable-secret choice. Abort supplies the token only. The fourth branch, `TargetImportResumeRequest`, supplies exactly `action: "resume"` and `import_id`: no token, export, unlock or new choice. Preview returns required public `import_id` beside the opaque token; a surface keeps the ID from preview onward. Every progress/conflict/result repeats it. Field shapes and response definitions are in OpenAPI and the schemas above.

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | SF-CO | SF → CO | HTTPS JSON | `TargetImportRequest` | Cookie principal | Authenticate → Origin → closed envelope. Preview/commit then enforces destination (own under `target.private` if `share` present, shared under `target.edit` if absent) and org permission before export decoding. Abort derives metadata from its token; resume derives it from a caller-owned receipt, never a new choice. | Ordered Refusals below. | Caller. |
| 2 | CO-FS | FS → CO | File reads | Policy; destination `inventory-target` files for preview/commit | Core | Check rights/source/owner and snapshot destination refs/IDs. Resume destination is identified by the receipt at step 5; raw export never becomes a file. | Destination/filter/validation refusal. | Caller, fixed safe error. |
| 3 | — | CO → CO | — | Recognition, bounded parse and canonical preview plan | Core for preview/commit only | Decode base64, check declared-format signature/version/container first; then full parse/duplicate-key/bounds validation. Apply format-specific create/update ownership, credential extraction and list-local refs. In locator order allocate IDs free across all files and earlier assigned batch IDs. | Unsupported signature 422 import_format_unsupported; recognized malformed input 422 invalid_request; invalid rows reported as skips. | Caller. |
| 4 | SF-CO | CO → SF | HTTPS JSON | 200 `TargetImportPreview` | Core | End preview with required public import ID, opaque token, previewed IDs/counters/definitions/mapping rows and credential metadata; no mutation. Drop request/secret buffers. | — | Caller only, no-store. |
| 5 | CO-BB | BB → CO | bbolt read | Complete `ImportReceipt` | Core on commit/abort/resume | Serialize import ID; inspect caller-owned receipt. New commit also checks overlapping incomplete retained receipts of the same caller and destination before any store. Resume authorizes the receipt's recorded destination/choice and continues only its remaining stages, without preview validation or secret access. Abort cannot undo a commit. | No receipt for resume caller: 409 import_preview_stale. Overlap: 409 import_conflict naming existing ID/progress. Other caller learns no receipt. | Caller. |
| 6 | — | CO → CO | — | Choice-independent preview digest, byte commitment and commit choice | Core on a new commit only | Compare SHA-256/RFC 8785 JCS plan digest and exact-byte keyed commitment; under inventory lock recompute refs/IDs/counters. Validate save/discard, destination/class and any new-store vault unlock. Choice is not in preview digest; no second digest. Resume skips this step and retains receipt-assigned IDs/recorded choice. | Stale preview or ordinary new-store refusal; no new effects. | Caller. |
| 7 | CO-BB | CO → BB | Open/stage bbolt transaction | Complete secret-free receipt; sealed credential batch for save | Core through U6 | Prepare full sanitized target definitions/IDs/refs, stored credential reference/layer objects, mappings, preview-plan digest, recorded choice/destination/class and stage. For save stage seals+receipt together; discard/no-secret stages receipt only. No export digest/commitment. | Storage failure aborts transaction. | Caller via progress/import ID. |
| 8 | CO-DK | CO → DK | JSONL append before vault commit | `cred_event`: `cred.store` per new distinct credential | Core through U6, save only | Existing vault audit-before-commit; personal no class, org chosen class; no grant. Discard/no-secret and resume skip. | Failure aborts credential transaction; existing audit-before-commit residual disclosed. | Audit; caller on failure. |
| 9 | CO-BB | CO → BB | Transaction commit | Seals plus complete receipt, or receipt alone | Core through U6 | First durable step: save commits credentials+complete receipt atomically; discard/no-secret commits complete receipt before swap. Any durable receipt suffices for secret-free resume; no receipt awaiting a secret. Resume skips stores/step 9. | 500 progress/import ID; never infer commit from audit alone. | Caller. |
| 10 | CO-FS | CO ↔ FS | Lock, sanitized version writes, fsync, link rename | Receipt's target batch and `ImportPublication` | Core on commit/resume | Publish assigned sanitized definitions in one swap; retain unrelated/export-absent targets and incomplete markers. Found marker proves prior publication and prevents another target act. Resume uses receipt, not new K/export; conflicting current state is not overwritten/resurrected. | Conflict/storage error with actual progress/import ID. | Caller. |
| 11 | CO-DK | CO → DK | JSONL append after publication | `target_write_event` per landed target | Core | Target create/update with source and field names only, before index wait. Resume never repairs/re-emits a missing target audit. | 500, targets retained, audit missing, import ID. | Audit; caller on failure. |
| 12 | CO-FS | CO → FS | Atomic profile read/modify/write | `vault` `profiles_file` | Core for receipt's personal-save choice | Map stored refs only where unmapped; keep/report existing/intervening mappings and preserve unrelated fields. Org/discard/no-secret skips; resume executes recorded plan, never new choice/material. | 500 distinguishes pending mapping from success. | Caller with import ID. |
| 13 | CO-RD | CO ↔ RD | Index writer apply/read | U5 inventory index application | Core, sole writer | After required audit, wait once per batch for applied target plan using existing target-write bound; resume reconciles remaining apply. | 503 pending; audit failure takes precedence with 500. | Caller with import ID. |
| 14 | CO-BB | CO → BB | bbolt write | Complete/reconciled `ImportReceipt` | Core | Record known stages/completion; first later inventory swap removes completed receipt's marker. No credential store or export digest in this checkpoint. | Failure reports partial/unknown progress, never false success. | Caller. |
| 15 | SF-CO | CO → SF | HTTPS JSON | `TargetImportResult` or `TargetImportFailure` | Core | Required import ID in every result/progress/conflict; actual targets/refs/losses/effects. Forget raw/secret buffers. A missing receipt for this caller is stale with no foreign progress. | Registered codes/statuses only. | Caller only, no-store. |

The core MUST enforce the ordered gates and bounded parser before any mutation [C1/target-import-cross-origin-403; C1/target-import-scope-forbidden-403; C1/target-import-org-forbidden-403; C5/import-format-recognition-and-bounds]. It MUST implement preview, every explicit confirmation branch and receipt-only resume through this same capability [C1/target-import-preview-no-write; C1/target-import-discard; C1/target-import-abort; C1/target-import-no-secret; C1/target-import-save-personal; C1/target-import-save-org; C1/target-import-resume-after-restart-no-duplicate].

For a shared import, before preview or commit reveals any definition or makes any mutation, the core MUST check each planned create and update against the caller's `target.edit` `targets:` filters and, for an update, check the matched existing target as well. If any row or matched target fails, refuse the entire import with 403 `forbidden`, fixed text and no row detail. An invisible matched target is never revealed; an out-of-filter match and an out-of-filter create receive the same answer [C1/target-import-scope-forbidden-403]. Own imports skip shared filters as design §6 and #148's ruling OPEN-11 rule; a named non-owner never imports into another user's targets.

The core MUST follow steps 5–14's store/receipt/swap/audit/mapping/index order and report durable partial effects without claiming rollback [C1/target-import-partial-failure; C1/target-import-retry-idempotent; C5/inventory-import-atomic-publish; C6/import-receipt-recovery; C6/event-import-target-writes; C6/event-import-credential-stores; C7/import-save-profile-mappings; C1/target-import-resume-after-restart-no-duplicate]. Provider tests are owed where the fixture registry says so.

## Refusals and who is told

The error registry and OpenAPI responses own the error codes and statuses.

| Condition | HTTP / code |
|---|---|
| No valid login | 401 `unauthenticated` |
| Foreign Origin | 403 `forbidden`, before envelope/body processing |
| Invalid closed envelope/action fields/types or choice/destination/class combination | 422 `invalid_request`, before destination/org permission |
| Missing destination right or org save right | 403 `forbidden`, after envelope and before decoding |
| Invalid base64 | 422 `invalid_request`, after permissions |
| Decoded export exceeds `import.max_bytes` | 422 `invalid_request`, fixed text |
| Signature/container/version/encoding not recognized for declared format: missing XML 3.0 root, unsupported Tabby version, SecureCRT INI, archive or binary | 422 `import_format_unsupported` (new), before full parsing |
| Recognized container with syntax, duplicate-key or document-bounds failure | 422 `invalid_request` |
| New store with absent vault / this login's unlock / broker key for chosen broker-unlocked class | 409 `vault_not_initialized` / `vault_locked` / `broker_unlock_unavailable`; resume stores/seals nothing and needs no unlock/key |
| Occupied credential reference outside owned receipt | 409 `credential_exists` |
| Bad/foreign/expired preview token, restart, changed exact bytes/preview plan; resume with no retained receipt for this caller | 409 `import_preview_stale` (new), no foreign receipt disclosure |
| Readable credential but commit omits `credential_choice` | 422 `invalid_request`, fixed text, after parse and stale check; a choice supplied when no readable credential exists is ignored and the result's choice is `none` |
| Conflicting confirmation/abort/current state or new commit overlapping incomplete retained receipt of same caller and destination | 409 `import_conflict` (new), required existing import ID/progress for overlap |
| Storage, audit, mapping or receipt failure | 500 `internal_error`, actual known progress/import ID |
| Required inventory validation unavailable; audited publication's apply exceeds bound | 503 `inventory_unavailable`, actual known progress/import ID where effects occurred |

This table is in evaluation order: authentication → Origin → envelope → destination/org permission → decoding → container recognition → full parsing. Resume resolves the caller-owned receipt after the envelope to obtain its destination/recorded choice; it never decodes an export or validates an old token. Different single-target precedence remains target-write's. No permission failure falls back to another list/tier. Unsupported rows and ambiguous refs are skips, not new error codes.

**Wire-shape detail — Revision 3 item 10.** `TargetImportCredentialPreview` is closed and requires `reference` (`common.credential_ref`), `kind` (`common.credential_kind`), bounded `login_name` (`common.login_name`, metadata not a secret), `target_ids` (array of `common.id`). `TargetImportProgress.credentials_stored` is an array of closed `{reference, layer, kind, target_ids}` objects, with existing `common.credential_ref`/`common.credential_layer`; use the same layer enum as credential-choice candidates. Every progress object requires `import_id`. `TargetImportPreview` and every `TargetImportResult` expose it directly beside their other fields; every conflict includes authorized progress repeating it. For overlap, it is the **existing incomplete** import's ID. The token stays opaque; a client retains the public ID from preview onward rather than parsing the token.

Only the authorized caller receives progress. `error.import_progress` is allowed for `internal_error`, `inventory_unavailable`, and `import_conflict`, required on conflicts that report this caller's import. No other error discloses receipt existence. Missing/foreign/expired receipt is the same stale response. The operator sees existing target/credential audits and loader diagnostics by ID/source, never a path. No import-summary event; missing target audit is reported, not repaired. All answers are no-store and safe fixed error text [C1/target-import-errors; C1/target-import-never-echoes-secrets; C6/event-import-target-writes; C1/target-import-resume-after-restart-no-duplicate].

## Canonical preview, receipt and recovery

The preview digest is SHA-256 of RFC 8785 JCS serialization of `{share, format, plan, not_carried}` exactly as retained in the receipt, `share` omitted for a shared import. Plan rows are ordered by locator index and carry `locator`, `source_ref`, `change`, `target_id`, `n`, complete sanitized internal `definition`, `credentials` of `{reference, kind}`, and `mapping`. No credential layer or choice enters this digest. Private ownership is included in the internal definition but omitted from the public preview definition. At commit the selected choice and destination are recorded beside the digest; the stored layer follows destination. The core never persists an export digest or commitment in the receipt.

An import ID is `imp-` plus 16 random bytes encoded as unpadded lower-case RFC 4648 base32. For each new row, allocate `g-` plus the first 26 lower-case unpadded base32 characters of HMAC-SHA256 over the length-prefixed label `tsb-import-target-id`, import ID, source reference and counter. Each text is UTF-8 prefixed by a four-byte big-endian length; the counter is an eight-byte big-endian unsigned integer prefixed with length eight. Check all files and earlier assigned rows under the lock. Re-import updates retain IDs. Single creates use label `tsb-create-target-id`, a fresh random 16-byte value in base32 in place of import ID, and an empty source reference. The credential reference is `import-<short>-<n>`, where short is the first eight base32 characters of the import ID and n counts distinct passwords in first-occurrence order from one.

The opaque preview token is bounded base64url and binds the caller, share list (omitted for shared), format, import ID, expiration, plan digest and the HMAC-SHA256 byte commitment. Its MAC input is the UTF-8 label `tsb-import-token` prefixed by its four-byte big-endian length, followed by the descriptor without a length prefix. The commitment input is the length-prefixed `tsb-import-export-bytes` label followed by the exact export bytes without a length prefix. K is 32 bytes drawn from a CSPRNG and held only in process memory; restart invalidates preview tokens, not receipts. A successful 200 `aborted` spends the token: a subsequent commit or abort with it is 409 `import_preview_stale`.

Save commits seals and the complete secret-free receipt in one transaction after credential audits; discard or no-secret commit persists the receipt before target publication. Publish one marker-bearing inventory swap, audit each landed target, update personal mappings only where unambiguous and unmapped, then wait for index apply. Resume by caller-owned import ID alone uses the receipt, not the export, token, unlock or another credential store. The publication marker prevents a repeated target act. If a crash leaves target audits uncertain, report audit `unknown`; never repair or re-emit them. A matching incomplete receipt blocks an overlapping commit until completion or expiry and returns its ID/progress. No cross-store rollback is claimed.

## Format recognition, parsing bounds and common mapping

**Ruled:** OPEN-02, OPEN-06; research dispositions 1/2/4 as revised by Revision 3 items 3/6/7/8/9. The `format` enum is explicit: byte signatures validate that declaration rather than silently selecting a different importer. No filename extension is trusted. The container/signature/version check runs before full document parsing: unsupported recognition (including XML without the VanDyke 3.0 root, a Tabby version outside 1/2, archive or binary) is `import_format_unsupported`; a recognized container whose syntax, duplicate keys or document bounds fail is `invalid_request`. Invalid base64 remains invalid_request at decoding. Recognition never silently switches importers. The four mapping tables below describe supported inputs, not all releases or configurations of the named products.

**Limits:** decoded bytes bounded by `import.max_bytes` (deployment DEFAULT 4 MiB); input entries by `import.max_entries` (DEFAULT 5000, including skipped rows); YAML nesting depth `import.max_yaml_depth` (deployment DEFAULT 32). Every mapped field obeys its existing schema bound. Schema/config owners define fields/ranges without schema defaults. Preview TTL DEFAULT 10 minutes (`import.preview_ttl`); receipt retention DEFAULT 7 days (`import.receipt_retention`). Core enforces bounds without plaintext body spooling or unbounded decoded allocations [C5/import-format-recognition-and-bounds; C5/import-no-plaintext-persistence].

Disable YAML object construction/custom tags/includes and XML DTD/external-entity/network resolution. Never execute proxy commands, scripts, macros, hooks or startup commands. The open inventory shape is not a whitelist for unknown foreign keys. Count depth/entries before expansion can bypass limits; no fetched reference or host file is an input. Provider validation owed [C5/import-format-recognition-and-bounds; C1/target-import-unsupported-rows; C5/import-no-plaintext-persistence].

Foreign display name maps to `name`; decoded folder path segments join `/` into `group`, omitted at root; no folder tags. Extract a native reference before generating a TSB ID. The qualified lookup tuple is `(format, destination, source_ref)`, exact strings after format decoding, only in `tsb` or this caller's private source. Missing refs skip as `missing_source_ref`; all ambiguous export/destination matches skip as `ambiguous_source_ref`, while other rows proceed. Unknown/invalid rows skip/report without saving their credentials. Two unrelated exports' equal native refs intentionally match in the same list. Changes are previewed before commit [C1/target-import-reimport; C5/inventory-import-reference-rebuild].

### Source labels carried from the supplied research

| Label | Supplied source claim and scope |
|---|---|
| **T-primary** | `Eugeny/tabby`, tag `v1.0.215`, `tabby-core` profile/config/profiles/vault services and `Profile`/`ProfileGroup` interfaces; `tabby-ssh` SSH options/password storage, `tabby-telnet` options and stream processing. Research calls these primary source. [Tabby source tree](https://github.com/Eugeny/tabby/tree/v1.0.215), [Profile API](https://api.tabby.sh/interfaces/tabby_core.Profile.html), [SSH options API](https://api.tabby.sh/interfaces/tabby_ssh.SSHProfileOptions.html). |
| **S-primary** | VanDyke “Importing and Exporting SecureCRT Settings”, configuration/session option help, backup/settings tip, 7.3.3/8.3/9.6 release notes. Research labels container/settings and configuration-passphrase behavior primary; it supplies titles, not pinned evidence for every XML field. |
| **S-secondary** | `HyperSine/how-does-SecureCRT-encrypt-password`, research cites commit `9dc6b2f`, documentation and `securecrt_cipher.py`. Exact cipher/framing claims are reverse-engineered **secondary** evidence, not a published VanDyke cryptographic specification. [Secondary project](https://github.com/HyperSine/how-does-SecureCRT-encrypt-password). |
| **M-primary** | [Mobatek MobaXterm documentation](https://mobaxterm.mobatek.net/documentation.html): session export/configuration, bookmark sections, password management/master password. Does not formally specify positional bookmark fields or password cipher. |
| **M-secondary** | Community `.mxtsessions` specifications/importers (research names Devolutions and moba2ssh without exact revisions) for positional fields/folder-reference behavior; `HyperSine/how-does-MobaXterm-encrypt-password`, research cites commit `6c7fd17`, for password internals. These mappings **rest on secondary sources**, graded against sanitized samples. [Secondary password project](https://github.com/HyperSine/how-does-MobaXterm-encrypt-password). |
| **Y-authority** | TSB Storage §3 and `contract/schemas/inventory-target.schema.json`: local target fields, types and limits. Foreign extraction never overrides these limits. |

These labels report the supplied research's claims. They do not certify that this design lane independently fetched the sources or checked a real current export. In particular, a synthetic sample is not proof of a vendor's current output. The orchestrator's real MobaXterm export check remains a release gate.

### Tabby mapping table

Password: `options.password`; inline private key: none.

**Recognition/version/encoding (T-primary, research §1.1):** one UTF-8 YAML mapping, config `version: 1` or `version: 2`, `profiles` sequence. Version 1 uses literal group strings; version 2 uses `groups` with IDs/parent links. LF/CRLF accepted. A whole-config-encrypted form has `encrypted: true`, encrypted `vault` and no readable profiles: it is outside the supported readable-profile export, not a request for a Tabby passphrase. Recognize from bytes, never a client filename. Duplicate YAML keys are malformed.

**Native reference:** nonempty `profiles[].id`; preserves identity under rename/move in the researched format. No invented host/name/row-number fallback. Mapping fixture `C1/target-import-preview-tabby`; bounds/recognition fixture `C5/import-format-recognition-and-bounds`.

| Source field or form | Mapping / handling | Evidence |
|---|---|---|
| Re-import update ownership | Overwrite/remove this format's `name`, `group`, `type`, host/port/SSH-user and carried SSH options (`auth`, `x11`, `skipBanner`, `agentForward`, algorithms members, forwardedPorts, keepaliveInterval/keepaliveCountMax) or carried Telnet stream/input options. Remove an owned field absent from the export; root removes group. Omitted port removes the stored port and the effective protocol default applies. Preserve tags/vendor/own prompt and presence/last_seen/unmapped options/private_owner/import_ref; no wholesale options replacement. Foreign jumpHost/readyTimeout are dropped, so preexisting TSB values remain unmapped and unchanged. | Revision 3 item 3 with items 6/8 |
| `profiles[].id` | Persist as `import_ref.source_ref`; new TSB ID generated separately. | T-primary |
| `type: ssh`, `type: telnet` | Same target type. `serial` skips `local_serial`; local/plugin/other types skip `unsupported_type`. Builtin/template profiles are not connectable imported targets. | T-primary; ruled loss policy |
| `name` | `name`, existing text bound/control rules. | T-primary |
| `group`, `groups[].id/name/parentGroupId` | Resolve folder ancestors, join display-name segments `/`. Version-1 literal/nonmatching group string remains literal as researched; absent/null/empty means root. No folder tags. Cycles/invalid row metadata cannot be followed indefinitely. | T-primary claim in research §1.4 |
| SSH `options.host/port/user` | Same SSH options; absent port omits the stored field and uses effective default 22; login name is also used for a carried password's store metadata. | T-primary; Y-authority |
| SSH `auth`, `x11`, `skipBanner`, `agentForward`, `algorithms.{hmac,kex,cipher,serverHostKey,compression}`, `forwardedPorts` | Carry only these documented TSB fields, as inert options where Storage so states; no proxy or execution behavior is enabled. | T-primary; Storage §3.2 |
| SSH `keepaliveInterval` | **`ceil(milliseconds / 1000)` whole seconds, with 0→0**. Validate existing integer-seconds bound. | T-primary; research disposition 4, retained by Revision 3 item 8 |
| SSH `readyTimeout` | **Drop/report `unsupported_option`**: no inventory schema field/bound; no field is added in this release. Supersedes the research's carry/convert recommendation. | Revision 3 item 8 |
| SSH `jumpHost` | **Drop/report `unsupported_option`**: a foreign Tabby profile ID is not a TSB ID; no remapping/route inference. | Revision 3 item 6 |
| SSH `keepaliveCountMax` | Same count, existing integer bound. | T-primary; Y-authority |
| SSH `options.password` | Clear legacy/manual password: readable carried secret, extracted only in memory, never copied into YAML/results. | T-primary; baseline credential handling below |
| SSH `options.privateKeys` | Paths/file URIs → `key_file_reference`; `vault://` → `unreadable_credential`; strip all. Never read a path or decode a Tabby vault. | T-primary |
| Nonempty encrypted top-level `vault.contents` with readable profiles | Foreign vault unavailable without user secret; report unreadable credentials without claiming per-profile ciphertext-to-password resolution. No master-password API. OS-keychain-only secrets are absent from the file and cannot be fetched. | T-primary |
| Telnet `host/port` | Same Telnet fields; omitted stored port uses effective default 23. No account field in Telnet target. | T-primary; Y-authority |
| Telnet `inputMode`, `inputNewlines`, `outputMode`, `outputNewlines`, `input.backspace` | Carry documented inert Storage §3.3 fields; never transform live traffic in the core. | T-primary; Y-authority |
| `proxyCommand`, scripts/login scripts, macros, startup hooks | Drop/report each unsupported option, never carry/run. Includes Telnet expect/send secrets; no script inference or password harvesting from script text. | Ruled OPEN-02 |
| UI icon/color/weight/terminal settings; SSH input/proxy settings without TSB field; group defaults; `reuseSession`; any other foreign option | Drop/report structurally per option; never use imported values to execute or silently invent another target field. `reuseSession` is not operational. | T-primary; ruled allowlist; Contract §15.3 |
| Serial `port`, baud/data/parity/flow settings | Entire client-local serial row skipped; no serial path or options are published. | T-primary; ruled local-serial refusal |

Timer fixture `C5/import-tabby-timer-units` covers 0, 1, 1000, 1001 milliseconds and overflow for **keepaliveInterval only**, plus readyTimeout drop/report without a schema addition. Plain copying keepalive milliseconds into seconds fails it. Group inheritance is not a new TSB behavior: unsupported group defaults are reported rather than executed or merged speculatively.

### SecureCRT mapping table

Password fields: `Password V2` and legacy `Password`; inline private key: none.

**Recognition/version/encoding (S-primary claim, research §2):** only one XML 1.0 UTF-8 settings export, optional UTF-8 BOM, root **`<VanDyke version="3.0">`**, with a `Sessions` key hierarchy. Accept LF/CRLF as XML text. Other root/version/encoding/container is unsupported. Standalone typed session INI has no name/folder/reference in its bytes and is **`import_format_unsupported`**, not a host-derived target. No archive input. Disable entity/DTD resolution.

**Native reference:** XML-decoded `key/@name` path strictly under `Sessions`, including the session leaf, joined `/`. Display name is the leaf; `group` is its ancestor path. No UUID exists; rename/move makes a new source reference and leaves the earlier TSB target in place. `Is Session` and `Protocol Name` distinguish sessions from folder/default data. `Default` is a template, not a target. Fixture `C1/target-import-preview-securecrt`; standalone/archive negatives in `C5/import-format-recognition-and-bounds`.

| XML property (`name`) or structure | Mapping / handling | Evidence |
|---|---|---|
| Re-import update ownership | Overwrite/remove SecureCRT-owned name/group/type/host/port/SSH-user and mapped keepaliveInterval/x11/agentForward. Owned omitted fields are removed (disabled/absent mapped keepalive removes its override); root removes group and absent port uses the effective protocol default. Preserve tags/vendor/own prompt and presence/last_seen/unmapped options/private_owner/import_ref. SSH1 is skipped, never an update to SSH2. | Revision 3 items 3/7 |
| `Sessions` nested `key` names | Leaf → `name`, ancestors → `group`, full decoded path → source reference. Root has no group. | S-primary claim |
| `dword` values | XML decimal integers, not the eight-hex-digit values of rejected INI files. | S-primary claim |
| `Is Session` | 1 identifies a session; 0/folder/default data does not create a target. | S-primary claim |
| `Protocol Name` | Only **SSH2** → `ssh`; **SSH1** skips/reports `unsupported_type`. Telnet → `telnet`; Serial skips `local_serial`; Telnet/SSL, RDP, SFTP, RLogin, TAPI, Raw, Local Shell and unknown kinds skip `unsupported_type`. No silent protocol upgrade. | S-primary claim; ruled loss policy |
| `Hostname` | `options.host` for SSH/Telnet. | S-primary claim |
| `[SSH2] Port` | `options.port` if present; absent stored port uses effective default 22. No SSH1 row imports. | S-primary claim |
| Telnet `Port` / `[Telnet] Port` | `options.port`, default 23 if absent. | S-primary claim |
| SSH `Username` | `options.user`; account metadata for carried credential, not a public target record field. | S-primary claim |
| `Idle NO-OP Check`, `Idle NO-OP Timeout` | Enabled interval → SSH `options.keepaliveInterval`, integer seconds already; no Tabby millisecond conversion. | S-primary claim |
| `Forward X11` | 0/1 → inert `options.x11` false/true. | S-primary claim; Storage §3.2 |
| `Forward Agent` / `Enable OpenSSH Agent Forwarding` | 0/1 → inert `options.agentForward` false/true. | S-primary claim |
| `Password`, `Password V2` | Credential fields only. Baseline is `unreadable_credential`; the isolated bytes-only decoder DEFAULT below upgrades a verified readable value to a carried secret. Never copy ciphertext into targets/answers. | S-primary for feature; **S-secondary** for algorithms |
| `Credential Title` | External global-manager reference → `unreadable_credential`; do not resolve it. | S-primary claim |
| `Identity Filename`, `Identity Filename V2` | Strip/report `key_file_reference`, without path expansion or access. | S-primary claim |
| Saved-password flags | Metadata only; no secret is implied when export omits it. No target field. | S-primary claim |
| `Firewall Name`, cipher/MAC/KEX lists, compression/forward tables | No documented safe 1:1 TSB mapping in the research; drop/report, never invent a jump-host ID or algorithm translation. | S-primary claim; ruled allowlist |
| Logon Script, Use Script File, Expect Send/Expect Send V2, Automate Logon | Drop/report individually, never execute or mine script text for a login. | Ruled OPEN-02 |
| Emulation, logical rows/columns, description, other unmapped fields | Drop/report; no copying UI settings into target options or profile defaults. | S-primary claim; Y-authority |
| Serial device/baud/parity/flow properties | Whole row skipped as local serial, never a broker-device request. | S-primary claim; ruled OPEN-02 |

Where researched alias properties coexist inconsistently, the provider cannot invent a winner; malformed/invalid entry handling reports the row instead of silently selecting settings. Such inputs are not a broadened promise of every SecureCRT configuration.

### MobaXterm mapping table — secondary-source observed dialect

Password: none readable; inline private key: none.

**Recognition (M-primary container claim; M-secondary positional claim, research §3):** one `.mxtsessions` bookmark export or INI configuration whose bytes contain `[Bookmarks]` / `[Bookmarks_<N>]`, `SubRep=`, `ImgNum=` and bookmark lines `name=#<icon>#<type>%...`. No format-version header exists; support is the **observed signature/positional dialect**, not a fabricated product-version range. Filename and icon number do not select the protocol. Research's encoding rule is UTF-8 first (optional UTF-8 BOM), otherwise Windows-1252; LF/CRLF text parsing, with native sample CRLF. Unsupported containers/encodings are refused, not guessed into another importer.

**Parsing and identity:** split a bookmark at its first `=`; RHS `#` blocks give protocol block `h[2]`; its `%` fields are `p`. Display key is the left side with trailing spaces before `=` removed. `SubRep` backslashes become `/` for both group and reference. Reference is that folder plus display key, or display key at root. Section numbers/icons are not references. Duplicate keys remain visible to ambiguity handling, not an INI parser's last-write-wins collapse. There is no immutable UUID; rename/move changes reference. Folder/reference rules **rest on secondary evidence** as required by the ruling. Fixture `C1/target-import-preview-mobaxterm` uses sanitized samples; the orchestrator still owes a real export check before release.

| Observed source | Mapping / handling | Evidence |
|---|---|---|
| Re-import update ownership | Overwrite/remove observed-format-owned name/group/type/host/port/SSH-user/x11/agentForward; absent/empty mapped values remove their owned field, with empty port using the effective protocol default. Preserve tags/vendor/own prompt and presence/last_seen/unmapped options/private_owner/import_ref. No inferred gateway, script or proxy field becomes owned/mapped. | Revision 3 item 3; positional mapping still M-secondary |
| Section `SubRep`, bookmark key | `/`-joined `group`, key → `name`, folder/key → `import_ref.source_ref`. Root omits group. `ImgNum`/section suffix ignored as UI metadata, not identity. | **M-secondary** observed mapping; M-primary container context |
| `p[0]` | 0 SSH, 1 Telnet; 8 local serial skipped; RDP/VNC/local/file/browser/Mosh/SFTP/FTP/WSL/unknown types skipped `unsupported_type`. | **M-secondary** |
| SSH/Telnet `p[1]`, `p[2]` | Host, numeric port; empty port omits the stored field and uses effective SSH 22/Telnet 23. | **M-secondary**; Y-authority bounds |
| SSH `p[3]` | `options.user`, omitted if empty. Telnet same-position user is unsupported and reported, not a Telnet account field. | **M-secondary**; Y-authority |
| SSH `p[5]` | Inert x11: -1/1 true, 0 false. | **M-secondary** |
| SSH `p[6]` | Compression flag has no corresponding TSB boolean field; drop/report. | **M-secondary** |
| SSH `p[7]`, startup macro `h[4]` | Drop/report startup command/macro; never execute. | **M-secondary**; ruled OPEN-02 |
| SSH gateway `p[8..10]` | Drop/report per option; raw foreign host/user/port is not a TSB `jumpHost` ID. No implicit target creation or routing. | **M-secondary** |
| SSH `p[14]`, `p[15]` | Target/gateway key-file references: strip/report `key_file_reference`; no variable expansion or file read. | **M-secondary** |
| SSH `p[16]` | Inert agent forwarding flag as researched. | **M-secondary** |
| Proxy `p[18..21]`, sidebar/display/remaining fields | Drop/report per unmapped option. No proxy command, automatic gateway or guessed keepalive conversion. | **M-secondary** |
| `h[3]` terminal/font/display block, `h[5]` comment, `h[6]` color | Drop/report. Research suggests a possible Telnet backspace mapping but does not establish a required mapping; no unsupported display setting reaches YAML. | **M-secondary**; ruled drop-without-supported-field policy |
| `.mxtsessions` credentials | Research says none exported; key-file references alone carry no secret. Never fetch Registry credentials. | M-primary/M-secondary labels as in research §3.5 |
| INI `[Passwords]`, `[Credentials]` | Credential fields are stripped and reported unreadable; no password is carried. | M-primary feature; **M-secondary** cipher/context |
| Serial COM/baud/data/parity/flow fields | Whole row skipped `local_serial`; no mapping to a broker-local serial device. | **M-secondary**; ruled local-serial skip |

The secondary source set is incomplete evidence for all possible positional variants. Support remains the observed dialect graded by sanitized sample fixtures; a builder cannot substitute a new positional schema or claim a real export was checked. The orchestrator owns the pre-release real-export check and any newly exposed design gap.

### Plain YAML mapping table

Password: none; inline private key: none.

**Recognition (Y-authority and OPEN-02):** one YAML document, root sequence of TSB target definitions or one target mapping; UTF-8 text. No wrapper with an inferred export format, archive or multi-file path is accepted. Duplicate keys are malformed. Each entry's `id` is its source reference, never a request to use/replace that TSB ID. Native target fields/limits come from Storage §3 and the inventory schema; retain its scalar-text mapping at string-typed keys. This input is TSB target YAML, not Tabby's config wrapper or a client-generated conversion protocol. Fixture `C1/target-import-preview-yaml`.

| Source field | Mapping / handling | Evidence |
|---|---|---|
| Re-import update ownership | Overwrite/remove YAML-owned name/group/type/host/port/SSH-user and the explicitly carried known timer/size/inert option fields. An owned field absent from the entry is removed; omitted port uses effective protocol default. Preserve all other keys, including tags/vendor/last_seen/unmapped options/private_owner/import_ref. Own prompt is preserved when absent; explicit YAML own prompt, empty included, is the §7 exception that replaces it and detaches. YAML tags/vendor initialize creates but remain unchanged on updates. | Revision 3 item 3 plus existing own-prompt exception |
| `id` | Exact decoded reference → core-owned `import_ref.source_ref`; independent generated/retained TSB ID. Missing/unusable skips; duplicates all ambiguous. | OPEN-02/-06; Y-authority |
| `type`, `name`, `group`, `tags` | Supported fields within existing bounds; tags initialize creates but are preserved on re-import updates. No folder tags. Client-local serial rows skipped, unsupported kinds reported. | Y-authority; ruled import policy |
| SSH/Telnet `options.host`, `options.port`, SSH `options.user` | Same target semantics and defaults as inventory schema; no millisecond conversion for TSB YAML. | Y-authority |
| Named TSB timer/size overrides | Carry known inventory-schema fields with their existing units/ranges. No new timer default or core interpretation. | Y-authority |
| Documented inert SSH/Telnet fields | Same allowlist as the TSB fields in the tables above, within their authoritative types; do not copy arbitrary options because the stored shape is open. | Storage §3.2–§3.3; OPEN-02 |
| `vendor` | Open `common.id` pointer on create, no inference; preserve existing vendor on re-import update. | OPEN-13; Storage §3.6 |
| Explicit `prompt_pattern`, including `""` | Own pattern, detached; absence on re-import preserves previous presence/value. No effective pattern accepted as own. | OPEN-10; Storage §3.6 |
| `discovered_by`, `share`, `owner`, prior import provenance | Destination source/owner/share list/provenance are core-owned; never trust an export to select another destination, set a share list, or overwrite another format's identity. Original export ID remains this YAML source reference. | OPEN-02/-06/-08 |
| Secret/credential fields forbidden by inventory schema; scripts/macros/proxy commands; unknown keys | Do not persist them. Inventory schema is not a plaintext credential-export dialect. Report invalid/unsupported row/field according to the common parse/loss rules; no invented inline-key or credential-reference resolution. | Y-authority; ruled OPEN-02 |

An import's share list, or its absence, is its destination, not export data: every target the import writes, created or updated, takes it, and an export's own `share` or `owner` key is never read.

## Baseline credential handling and confirmation

Only credentials attached to valid carried target rows participate in save. A carried password must pass existing vault kind, login-name and secret validation. No supported format carries an inline private key. Imported username/secret values are never target audit values. Existing schemas own those bounds; this protocol adds no foreign master-password field or key-file capability.

The core MUST extract a readable clear carried secret in request memory, remove it from every target representation, and request explicit save/discard confirmation before effects [C1/target-import-save-personal; C1/target-import-discard; C5/import-no-plaintext-persistence]. Foreign credentials requiring a user secret or external state MUST be reported `unreadable_credential`; key-file references MUST be reported without paths or reads [C1/target-import-key-file-report; C1/target-import-no-secret; C1/target-import-never-echoes-secrets]. Vendor-encoded bytes default to unreadable in this baseline; the next isolated section is the sole exception under the operator-question DEFAULT.

Personal save stores one entry per distinct `(kind, login name, secret)` **within the import**, references `import-<short import id>-<n>`, compares only in memory, and never deduplicates by decrypting existing vault entries. Personal Layer 2a maps only after sealed entries/targets exist; a different existing mapping remains and preview reports `keep`, result `kept`. Both carry the newly imported reference, not the old mapping. On resume an existing mapping equal to the planned reference reports `mapped`. Re-import with a changed secret creates another entry, preserves the earlier ref and keeps any existing mapping. Org save uses explicit org/class and `vault.admin`, with no map/grant. The core MUST preserve these boundaries [C7/import-save-profile-mappings; C6/event-import-credential-stores; C1/target-import-save-org].

A new store needs initialization and this login's unlock under the real vault lock. Once seals and the complete receipt commit, resume uses the receipt without token/export/unlock/material or another credential store, including after restart/preview expiry. A stale preview without a durable receipt still requires a fresh preview; no receipt for this caller is stale. Recorded choice/destination/class govern resume, never a new confirmation choice. No cross-store rollback or exactly-once audit is promised. Target audits follow publication; a missing one is reported, not repaired. The core MUST communicate the actual known stages using `TargetImportProgress` [C1/target-import-save-vault-locked-409; C1/target-import-partial-failure; C1/target-import-retry-idempotent; C6/import-receipt-recovery].

## Optional policy section — bytes-only vendor-password decoding DEFAULT

**Ruled research disposition 3, `[plan] DEFAULT, veto by line`, pending the operator's answer.** This section alone upgrades otherwise unreadable vendor-encoded credentials into ordinary carried secrets. If the operator chooses “every vendor-encoded password is unreadable”, **delete this section and its dedicated fixture obligation**. All mapping tables already route credential fields through baseline handling; messages, confirmation choices, vault transactions, mappings, error codes and receipts need no redesign. Do not interpret “optional” as builder choice: this section applies until explicitly vetoed.

For SecureCRT, select `Password V2` when nonempty; use legacy `Password` only when `Password V2` is absent or empty. Without this section both are unreadable. MobaXterm passwords remain unreadable.

**Bytes-only test:** a decoder can use the uploaded bytes plus public fixed algorithm constants, never a user-supplied foreign passphrase, broker/client OS keychain, Registry, machine probe, guessed hostname/login, filesystem file or network fetch. If necessary material is absent or a documented integrity check fails, report `unreadable_credential` and import the target without it. Decoded values live only in request memory and receive the same explicit save/discard choice as clear carried secrets. An unsupported cipher form is not permission to guess another cipher. Decoder failure does not put ciphertext/error dumps into preview or logs.

| Credential form | Research algorithm claim; treatment | Source label |
|---|---|---|
| SecureCRT legacy `Password`, `u<hex>` | Double Blowfish-CBC with public fixed keys and zero IV, framed UTF-16LE text; bytes-only recovery eligible. Preserve source framing/validation, not a new guessed cipher. | **S-secondary**, research §2.5; exact implementation and known answers must be checked against the cited algorithm source. |
| SecureCRT `Password V2`, `02:<hex>` | AES-256-CBC, zero IV, SHA-256 of configuration passphrase; try **empty passphrase only**, validate researched length/digest framing. A nonempty required passphrase or failed validation is unreadable; never request/brute-force it. | **S-secondary** algorithm; S-primary passphrase feature |
| SecureCRT `Password V2`, `03:<hex>` | Research claims AES-256-CBC with 16-byte salt and 48-byte key/IV from `bcrypt_pbkdf` at 16 rounds; empty passphrase only, validate documented framing/digest. This is a secondary-source claim, not independent verification by this lane. | **S-secondary**, research §2.5 |
| MobaXterm with master password; Tabby vault; passphrase-protected SecureCRT | User-secret-dependent, unreadable; no foreign master-password API. | T-primary / S-primary / M-primary feature claims; secondary details remain labeled |

MobaXterm passwords are unreadable in this release. No new decoding input or environment access is introduced. The supplied research's synthetic ciphertext is **not** a valid cryptographic known-answer vector; do not use “parser accepted the sample” as evidence of successful decryption.

The core MUST prove each accepted decoder variant with sanitized, non-authenticating known-answer input/output vectors, with integrity/truncation/wrong-context/user-secret-required negatives and memory/persistence checks [C5/import-vendor-password-known-answers; U2/U6 provider tests owed]. Keep the exact cryptographic algorithms and source-derived vectors together under that obligation. No vector, decoder implementation or external-source verification is claimed here; unsupported/unverified algorithm claims go to red-team before builder work, never become an invented cipher.

## Security considerations

- The deployment MUST configure every proxy in front of `/target-imports` never to buffer its request body to disk, including for HTTP/2. Disable proxy request buffering (for example `proxy_request_buffering off`) or use strictly memory-only buffering for this route [C5/import-no-plaintext-persistence, U10 owed].

- The core MUST bind preview to caller, share list (omitted for shared), format, import ID, expiration, exact-byte keyed commitment and sanitized-plan digest, using only process-memory K. Restart invalidates old previews but not retained receipt-only resume. Deterministic imported IDs use the length-prefixed HMAC formula above; single creates use tsb-create-target-id and fresh 16-byte random input. Locked allocation checks every target file and earlier batch-assigned ID in locator order; no ID list in token [C1/target-import-preview-binding; C5/inventory-generated-id-collisions].
- The core MUST retain no raw export/plaintext across requests or in files/staging/versions/logs/audit/Redis/receipts/tokens. No export digest/commitment of any kind is stored in a receipt; its digest is of the sanitized canonical preview plan only. The keyed raw-byte commitment stays solely in the transient token, never durable state. Only confirmed vault save persists secrets, sealed under existing encryption rules [C1/target-import-never-echoes-secrets; C5/import-no-plaintext-persistence; C6/import-receipt-recovery].
- Surfaces MUST hold the user-supplied export only for the active request, forget it after the answer and reacquire it for commit; neither surface nor core follows export key paths. Existing unlock flows are used before resending, with no foreign master-password input [C1/target-import-surface-memory; C1/target-import-key-file-report].
- The core MUST apply membership and owner state on own targets and current disabled-account checks consistently despite stale index data. Being named or session visibility does not imply a credential, unlock, session-create permission or target-filter bypass; admin grants/forced recording retain their selectors. Non-owner allowed session viewers receive target ID only [C1/target-private-owner-only; C1/target-private-disabled-owner; C1/target-private-policy-gates; C1/target-private-grant-resolution; C1/target-private-forced-recording; C1/target-private-session-disclosure].
- No host path, PID, host context, parser excerpt, key filename or raw foreign row appears northbound. New IDs, source refs, locators and field reports are bounded data, rendered as text, not markup/terminal escapes. No import opens a session, probes a target, executes a command, or changes the worker's connect-time broker-host refusal [C1/target-import-never-echoes-secrets; C1/target-import-unsupported-rows; C5/import-no-plaintext-persistence].
- The core MUST preserve absent versus empty own prompt through all storage/index paths and serve effective patterns read-only without compilation. Credential §10.4's import request exception must be reflected in V10's formerly exhaustive credential-wire list [C5/inventory-prompt-presence; C5/inventory-prompt-presence-rebuild; C1/target-prompt-own-effective-shape].

## Open items and release gates

Provider tests, corpus alignment and release approval remain outstanding. The operator's vendor-password question has the explicitly active, isolated DEFAULT above, not an unspecified parser policy. Catalogue patterns remain labeled proposals for red-team.

The Revision 2 cross-family red-team is complete and this text applies its ruled findings; no independent review of this revised text is claimed. Outstanding evidence/authorization: orchestrator's real MobaXterm export check; independent checking of the labeled source claims and decoder known-answer vectors before implementation/release; corpus amendment/export; operator go on that combined PR; provider unit plans/tests and integrated E3b gate. None is implied by publication of this protocol. If evidence exposes a new design gap or LOCKED conflict, stop and report options rather than inventing semantics. Sync-adapter/Type-2 discovery questions remain in their separate owning items, outside this protocol.

**`C1/target-import-preview-mobaxterm` remains grade `reference` until the orchestrator's real-export check is recorded**; it is not live-graded on secondary/synthetic samples. The new `C1/target-import-resume-after-restart-no-duplicate` is reference, owed U2/U5/U6, and checks actual store counts/IDs/refs across credential-stage crash/restart/resume. Existing retry tests must not equate a stale-token refusal with proof against duplicate stores.

Some fixtures cited here are still owed and not yet published. Published E3b fixtures have `mark: {"published":"e3b","required-green":"e3b"}` and grades and provider owners recorded in the fixtures and grader index. Internal behavior needs real reference-provider results, not a wire-only fixture or owed-entry PASS. The profile order e4b → e3b → e5 preserves all old profile classifications and does not change a code repo's EPOCH.

## Picture

Pending the house-format generator, as the rules page permits. No picture is required for this design deliverable.


