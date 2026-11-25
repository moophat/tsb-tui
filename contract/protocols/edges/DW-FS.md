# Edge DW-FS: discovery worker to files

| | |
|---|---|
| **Edge** | DW-FS |
| **Ends** | discovery worker (DW, process) writes; files (FS, store) |
| **Seam** | C5 |
| **Status** | not placed |
| **Transport** | a file |

The target file is `inventory-target.schema.json`, and fleet-discovery steps 2 and 3 use this edge.

## Transport rules

- The edge is one-way, DW to files: plan section 1.3 F1.
- A writer of inventory files writes only its own source directory, never `manual` or `tsb`, and never Redis: fleet-discovery step 2; Storage §2.1, §2.2; Contract §8.
- Each file is written atomically — a temporary name a reader ignores, renamed over the target — under `inventory/.lock`: layout rule 3; fleet-discovery step 2 [C5/inventory-layout].

## Records

| Record | Contract shape | Protocols | Epoch |
|---|---|---|---|
| target file `current/<source>/<id>.yaml`, written by any writer of inventory files | `inventory-target.schema.json` | fleet-discovery 2, 3 | not placed |

fleet-discovery's path is DW-FS, CO-FS, CO-RD. Its CO-FS and CO-RD halves are listed in those catalogues.

## Gaps

- The target file has `inventory-target.schema.json`; the inventory index is U5's own, with no contract shape (CO-RD), and the endpoints are inventory-list's.
- How the core starts or configures a discovery worker has no edge (plan section 1.3 F1; proposal P5: an edge CO-DW, or a discovery worker that reads its configuration from files, which makes DW-FS two-way). The operator's call; fleet-discovery open item 1.
- Type-2 session discovery reports "via Go core API" and has no edge: plan section 1.3 F2 (P6).
