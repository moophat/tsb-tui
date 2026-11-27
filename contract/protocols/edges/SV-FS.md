# Edge SV-FS: supervisor to files

| | |
|---|---|
| **Edge** | SV-FS |
| **Ends** | supervisor (SV, process) writes; files (FS, store) |
| **Seam** | C5 |
| **Status** | built (E0-E1) |
| **Transport** | a file |
| **Contract** | `contract/schemas/supervisor-manifest.schema.json` |

This page is an index. The core reads the same file over edge CO-FS (see that catalogue), never over this edge.

## Transport rules

- Path `supervisors/{supervisor_id}/manifest.json`: `supervisor-manifest` top.
- Closed shape: the supervisor writes exactly the declared fields: V2; worker-placement step 1.
- `proto_version` is the C3 version the supervisor and its workers speak; absent means v0.1: V1; worker-placement step 2.
- Every live manifest names one member of the set of supervisors the core addresses: `supervisor-manifest` top; worker-placement step 1.
- A supervisor is live when its `control_sock` accepts a dial; missing or refused means dead: `supervisor-manifest` top; worker-placement step 3. `pid` is informational only and never northbound: V11.
- No credential in the file: V10.

## Records

| Record | `$def` | Protocols and steps | Status |
|---|---|---|---|
| `supervisors/{supervisor_id}/manifest.json`, written by the supervisor | `supervisor-manifest.schema.json` (the whole schema; no `$defs`) | worker-placement 1 (written); read at worker-placement 2, core-restart 9, operator-alerts 14 over CO-FS | built |

Count: 1 record.

## Gaps

- OPEN — only worker-placement step 1 has a supervisor write step. When the supervisor writes it (at start) and whether it rewrites it atomically are not stated in any step; the worker manifest says atomic (`worker-manifest` top; PW-FS), `supervisor-manifest` top does not.
- Who removes the manifest, and when, is not a step. operator-alerts step 14 reads a removed manifest as "that supervisor is gone", and when the core looks outside a restart is unspecified (operator-alerts open items).
- Fixtures grade the shape only: `C5/supervisor-manifest-valid`, `C5/supervisor-manifest-proto-version`. The set rule is owed (worker-placement step 2).
