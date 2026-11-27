# Edge CO-RD: core and Redis

| | |
|---|---|
| **Edge** | CO-RD |
| **Ends** | core (CO, process) and Redis (RD, store) |
| **Seam** | C6 |
| **Status** | built for the target index; the vault's Part 2 from the vault release (E4) |
| **Transport** | a Redis command (rules page, plan proposal P8) |

The core is the only writer of the target index (Contract §8). The index itself has no contract shape: it is U5's own.

## Transport rules

The core applies membership and owner state to own targets even when the index is stale. Its index projection and rebuild preserve own prompt presence and qualified import references; it persists no secret or caller-relative login hint.

- **Part 2** (from the vault release): written only for a device on the allowlist and only after `part1 ‖ part2`
  opened the user's set; read only behind the allowlist check; never written in untrusted mode; deleted by a
  revocation, and with Redis down a revocation still succeeds, the Part 2 left behind expiring by its TTL (DEFAULT 43,
  48). Never flushed: Part 2 is regenerable, so its loss forces a regeneration, never a credential loss (credential §9)
  [C7/part2-ttl-renewed; C7/part2-missing-409-uncounted; C7/revoke-redis-down-succeeds].
- The core writes one entry per target (`HSET target:<id> …`) and deletes one (`DEL target:<id>`), so the index follows the file: fleet-discovery 5 [C5/inventory-write-then-observe].
- A search always carries `LIMIT`, paged to the page's end, ordered by `id`: inventory-list 3 [C1/targets-list-paged].
- A full rebuild writes every file's entry, then deletes every `target:*` key no file backs, found by `SCAN` — never `FLUSHDB` or `FLUSHALL`: fleet-discovery 6 [C5/inventory-bulk-swap].
- `idx:targets` is created when absent, and dropped (never with its documents) and recreated when its definition differs: fleet-discovery 7.
- Redis not answering: the core starts anyway, serves sessions from its parsed inventory, and rebuilds when Redis answers; the two target operations answer 503 `inventory_unavailable` (inventory-list 3, 6) [C1/targets-unavailable-503].

## Records

| Record | Contract shape | Protocols | Epoch |
|---|---|---|---|
| inventory index: the core rebuilds it from the target files and answers target lists from it | none: the index is U5's own; the records it answers are `TargetRecord` and `TargetList` | inventory-list 3, 6; fleet-discovery 5, 6, 7; target-write 2 (definition visibility), 7 (after audit, waits for writer apply, not submit; bounded at 5 s) | built (this release) |
| vault Part 2, `vault:part2:{user_id}:{device_id}`, 16 raw bytes, TTL `vault.part2_ttl` | `vault.schema.json` `x-redis` | W: device-trust 5 (register), vault-unlock 7 (TTL renewed by a trusted unlock; stored afresh by a regeneration). R: vault-unlock 4 (trusted unlock). Deleted: device-trust 11 (revoke), vault-unlock 11 (wipe, through its revocations) | E4 |

Paths: inventory-list is SF-CO, CO-RD, SF-CO; fleet-discovery is DW-FS, CO-FS, CO-RD; vault-unlock is SF-CO, CO-BB, CO-RD, then session-open; device-trust is SF-CO, CO-BB, CO-RD.

## Gaps

- The inventory endpoints are inventory-list's; the vault seam is C7 from E4.
- The target index has no `$def`: it is U5's own. Part 2 is contract because its revocation semantics are
  (credential §7.2).
