# fleet-discovery — a discovery worker writes what devices exist, and the core indexes it

| | |
|---|---|
| **ID** | fleet-discovery |
| **Purpose** | A Type-1 discovery worker writes what its source lists as target files; the core picks each change up into its index, with no request from anyone. |
| **Status** | E3 |
| **Corpus clauses** | Storage §2.1–§2.5, §3, §7.1, §7.3; Contract §4, §8 |
| **Seams** | C5; the index on edge CO-RD is U5's own and has no contract shape (CO-RD) |
| **Schemas** (`contract/schemas/` at this tag) | `inventory-target.schema.json` (the file a worker writes, with its `x-layout`); `config.schema.json` (the `inventory` knobs: `keep_versions`, `staging_max_age`); `common.schema.json#/$defs/id`, `#/$defs/timestamp_any_zone`; the transform fixtures `C5/inventory-layout`, `C5/inventory-yaml-mapping`, `C5/inventory-write-then-observe`, `C5/inventory-bulk-swap` |

At E3 the target files are written by a person or a rig, by the same layout rules (3 and 7); the discovery worker of steps 1–3 is not placed: the operator, 2026-10-19, deferred Type-1 discovery from E3 to an item of its own. Steps 4–8 hold for any writer. They also serve [target-write](target-write.md) unchanged: the core writes the reserved `tsb` source on a principal’s request, under the same writer rules (x-layout rule 11).

Schema paths are relative to `contract/schemas/`. Fixtures are cited as `<seam>/<name>`, under
`contract/fixtures/`. Corpus files, under `srs/`: Storage = `storage-and-discovery-architecture.md`,
Contract = `architecture-contract.md`, session model = `session-model-and-lifecycle.md`.

fleet-discovery writes files; inventory-list reads the index those writes build. The lock, the link swap and the
layout rules are `inventory-target.schema.json`'s `x-layout`, cited here by rule number.

**Words used here.**

- **Source**: a directory under `current/`, named as `common#/$defs/id`. `manual` is the source people edit by hand,
  and `tsb` is the source the core writes through target-write; no discovery source may use either name (layout rules 1, 11).
- **`current`**: the symbolic link to `./v<N>/`, the version the core reads (rule 1).
- **Staging directory**: a uniquely named `staging.<random>/` a bulk change builds in; it is never read (rule 5).
- **The lock**: `inventory/.lock`, an advisory exclusive lock (flock) held by a swapper for the whole swap, by a
  writer for each write cycle and by the core for its cleanup. A reader never takes it (rule 7).

## Path

DW-FS → CO-FS → CO-RD

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 *(not placed)* | — | DW → DW | — | — | the discovery worker | starts, reads its configuration and runs; where its configuration lives has no contract shape (Open item 1) | refuses to run as source `manual` or `tsb` | — |
| 2 *(not placed)* | DW-FS | DW → FS | a file | `inventory-target.schema.json`, written by rule 3 into `current/<source>/<id>.yaml`, under the lock (rule 7) | the discovery worker | one file per device its source lists, except the broker host and every device in its deny list. It MUST write only its own source directory and MUST NOT write Redis (Storage §2.1, §2.2) [C5/inventory-target-valid; C5/inventory-layout, owed by the E3 schema plan] | — | — |
| 3 *(not placed)* | DW-FS | DW → FS | a file | — | the discovery worker | a device its source no longer lists: where the worker's configuration lives decides how it is removed (Open item 1) | — | — |
| 4 | CO-FS | FS → CO | a file change notification (fsnotify, Storage §2.4) | the changed file | the core | reads and validates that file (Storage §2.3 "incremental") [C5/inventory-target-prompt-pattern] | invalid: skipped and logged (rule 9); duplicate IDs: apply the matrix below, logged (rule 9) [C5/inventory-layout, owed by the E3 schema plan and the E3 core plan] | the operator, by a log line for a duplicate id; a skipped file is a log line |
| 5 | CO-RD | CO → RD | a Redis command | `HSET target:<id> …` or `DEL target:<id>` | the core, the only writer (Contract §8) | the index follows the file [C5/inventory-write-then-observe, owed by the E3 core plan and the U11 rig it names] | — | — |
| 6 | — | CO → CO | — | `current` re-pointed (rule 5) | the core | full rebuild: every file's entry written, then every `target:*` key no file backs deleted (found by `SCAN`, never by `FLUSHDB` or `FLUSHALL`); reads during it may mix [C5/inventory-bulk-swap, owed by the E3 core plan and the U11 rig it names]. `current` missing: the index stays and the core logs it (rule 6) | — | a log line |
| 7 | — | CO → CO | — | core start | the core | a full rebuild from files, as step 6; `idx:targets` created when absent, and dropped (never with its documents) and recreated when its definition differs; cleanup by rule 8 under the lock. If Redis is not answering, the core starts anyway, serves sessions from its parsed inventory, and rebuilds when Redis answers [owed by the E3 core plan] | — | — |
| 8 | — | CO → CO | — | a changed timer override | the core | → config-reload: yes, a change to a target file's timer override sends `timers` (Storage §8.1 "with per-target overrides, hot-reloaded") | — | — |

- **Open sessions are not touched.** A target file changed or removed under an open session ends nothing: an
  inventory change is not among session model §13's ends. Who may still see such a session is session-list's open
  item 11.
- **A discovered telnet target turns `link_timeout` off the way any target does**: `keepaliveInterval: 0` in its own
  target file, which its source writes (link-liveness; `timer_values`). A file rewritten by its driver carries the
  override its source sets for that device.
- **A login script in a file** (`options.scripts`, `login_scripts`) loads and is ignored at this release: no script
  runs (`inventory-target` top) [C5/inventory-target-valid].

## Duplicate IDs and private sources

Validate provenance before resolving duplicates. `private_owner` must match the directory's owner in that owner's private source; a missing or mismatched owner there, or `private_owner` in any other source, makes the file skipped and logged, never loaded as ordinary. `share` is required in an owner source and forbidden elsewhere: a private-source file without a share list, with one that does not name its `private_owner`, with a duplicate entry or more than 256 entries, or a file in any other source carrying `share`, is skipped and logged. An entry of a kind this release does not know matches no principal. `import_ref` outside `tsb` and a validated private source is ignored: the file loads and that key has no meaning there. These keys were previously ignored on this open shape; this provenance validation is a compatibility change. A file in any source not written by the core whose ID starts `g-` is skipped and logged. Apply this matrix on startup, reload and rebuild independently of scan order and the existing index. A private source is `private-` plus the first 32 lower-case hexadecimal digits of SHA-256 of the exact username; each file carries a matching `private_owner` and `share`. Discovery and adapter sources must not use the reserved prefix.

| Candidates for an ID | Loads |
|---|---|
| One ordinary shared and any imported or private conflicts | Ordinary shared only |
| Two or more ordinary shared | None |
| No ordinary shared, one imported shared and any private conflicts | Imported shared only |
| No ordinary shared, two or more imported shared | None |
| No shared and one validated private file | That private file |
| No shared and two or more private files | None |

An import is identified by validated `import_ref`, not its source alone. Index projection and rebuild preserve absent, empty and nonempty own prompt patterns and qualified import references. Refusals name IDs and sources, never host paths or northbound private diagnostics.

## Refusals and who is told

- A worker that names itself `manual` or `tsb` refuses to run (step 1): `manual` is the source people edit by hand, and `tsb` is the core’s source. The configured source-name refusal is owed by the discovery writer’s provider tests; the contract regression check covers both names.
- A file that does not validate is skipped and the rest load; on a change, its entry is dropped, and the core logs
  it, naming the stem and the source. A dropped entry sends no `timers`: its open connections keep the timers they
  run by; a target override that fails against a new `config.yaml` refuses that `config.yaml` instead (→
  config-reload step 2). Duplicate IDs follow the matrix above; the core logs IDs and sources, never paths (rule 9; V11) [C5/inventory-layout].
- The operator is told by a log line for a duplicate id (step 4); a skipped file and a missing `current` are log
  lines (steps 4, 6); no surface is told of an inventory change.

## Security considerations

- **A source writes only its own directory** (Storage §2.1: "no write conflicts"), and nothing but the core writes
  the index (Storage §2.2; Contract §8) [C5/inventory-layout].
- **Discovery holds no user credential** (Storage §7.1: "Credential requirement: none from the user vault").
- **Discovery skips the broker host and the devices in its deny list**, so neither the broker host nor a node the
  deployment protects becomes a target (Contract §4; step 2).
- **Data from a source (a CMDB) is trusted no further than the schema.** A target it adds is reachable only through
  `policy.yaml` and a credential that names it. A secret key (`password`, `privateKeys`, `credential_ref`) is refused
  in the file: secrets never live in inventory, and a credential is resolved per principal (Storage §3.5, §4, §10.1a)
  [C5/inventory-target-valid].

## Open items

1. **Where a discovery worker's configuration lives**, and so how it is started and how a device its source no
   longer lists is removed: the operator has not ruled it (Storage §7.3). The contract names no edge for starting a
   discovery worker, and `config.yaml`'s knob shape carries no discovery keys at this release.
2. **Type-2 session discovery** reports "via Go core API" and has no edge; it is not in this release.

## Picture

pending
