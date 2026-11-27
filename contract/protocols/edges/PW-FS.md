# Edge PW-FS: protocol worker to files

| | |
|---|---|
| **Edge** | PW-FS |
| **Ends** | protocol worker (PW, process) writes; files (FS, store) |
| **Seam** | C5; `known_hosts` has no contract shape |
| **Status** | built (E0-E1) |
| **Transport** | a file |
| **Contract** | `contract/schemas/worker-manifest.schema.json`, `contract/schemas/spool-entry.schema.json` |

This page is an index. The core reads these files over edge CO-FS (see that catalogue).

## Transport rules

- Worker manifest `workers/{worker_id}.json`: written atomically (a temporary file renamed over the old one) on every change; left in place if the worker crashes: `worker-manifest` top; worker-placement step 9.
- Spool entry `spool/{session_id}.json`: written atomically and fsynced before the worker acts on the end, whether or not a core is attached: `spool-entry` top; session-end-from-outside step 3.
- Both are closed shapes: V2. `proto_version` absent means v0.1: V1.
- No credential in a manifest or the spool: V10.
- `pid` is informational and never northbound: V11.
- The spool carries only the pairs a worker can name: `spool-entry` top; the pair values are V5.

## Records

| Record | `$def` | Protocols and steps | Status |
|---|---|---|---|
| worker manifest, new worker | `worker-manifest.schema.json` | worker-placement 9 | built |
| worker manifest, connection and session listed | same | session-open 15 | built |
| worker manifest, session released | same | session-end-by-tsb 17; session-end-from-outside 15a; reattach-handshake 17 | built |
| worker manifest, ended session kept (retained) | same | session-end-by-tsb 24 | built |
| worker manifest, connection dropped | same | connection-linger 7, 15; session-end-from-outside 4a (after `connection_lost`) | built |
| spool entry, an end observed from outside TSB | `spool-entry.schema.json` | session-end-from-outside 3; link-liveness 13 (to session-end-from-outside) | built |
| spool entry, an end the worker decided (a timer) | same | session-end-by-tsb 22 | built |
| `known_hosts` (worker's recorded host keys) | no contract shape | session-open 9 | built |

Count: 8 rows (2 contract shapes, 1 with none).

## Gaps

- Atomicity of the manifest rewrite is owed: its fixtures grade shape only (`C5/worker-manifest-valid`, `C5/manifest-closed-v0.1`, `C5/manifest-proto-version`). The spool's atomic write and fsync before the end is acted on are graded by `C5/spool-entry-shape` (reference: U3 `worker/tests/test_spool.py`).
- The manifest need not exist when the core reads `worker_created`: the core takes a new worker's version from that reply (`supervisor#/$defs/worker_created`), so the manifest write may follow it; worker-placement step 9 still calls the order unstated.
- Order of the manifest rewrite and `connection_lost`: connection-linger open items.
- `known_hosts` is the worker's own volume and has no shape: session-open terms; tsb-core `docs/system-architecture.md` "State and where it lives". The README's FS list names it since P4 (plan section 1.3 F5).
