# Edge CO-FS: core and files

| | |
|---|---|
| **Edge** | CO-FS |
| **Ends** | core (CO, process) and files (FS, store) |
| **Seam** | C1 (`policy.yaml`), C5 (manifests, spool, `config.yaml`, the target file) |
| **Status** | built (E0-E1); `config.yaml` reload E3; VT socket directory E2 |
| **Transport** | a file read; the core also deletes spool entries and writes its tsb inventory source |

This page is an index. The core reads what SV-FS and PW-FS write (see those catalogues).

## Transport rules

Import publication writes sanitized definitions and the secret-free publication marker in one locked version swap; raw exports never become files. Personal save updates only the caller’s profile mappings and preserves existing mappings. See [target-import](../target-import.md).

The core owns validated own-target inventory files carrying share under the reserved private source, with co-presence enforced by the loader, and reads release-owned vendor prompt data. Ownership, share list and prompt presence survive writes and rebuilds; raw import exports are never filesystem messages.

- `policy.yaml` is parsed, then validated against `contract/schemas/policy.schema.json` (policy top); a file that does not validate is refused: config-reload step 2. A refused file changes nothing: the last accepted file stays in force (config-reload step 2; its open item 2 is closed).
- The core watches both `config.yaml` and `policy.yaml` and rereads the one that changed, no restart: config-reload step 1.
- The inventory layout, the atomic write, the link swap, the missing `current`, the lock, retention and the refusals are `inventory-target.schema.json`'s `x-layout` rules 1–11: fleet-discovery steps 4, 6, 7; target-write steps 3–5 write the reserved `tsb` source under the lock, atomically with file and directory fsync.
- No TSB API writes `config.yaml` or `policy.yaml`; an admin edits them out of band: config-reload (who changes it); plan section 1.3 F7.
- The core reads the spool before any E10 decision, and again on `no_such_session` or a refused session socket: core-restart step 8.
- The core deletes a spool entry once its audit event is written; every entry is deleted by the core that audits its end: session-end-from-outside step 16; spooled-end-finalize step 8. A late open's entry is deleted with nothing audited, before `release`: session-open step 14.
- A supervisor or worker is live when its socket accepts a dial, never by `pid`: `supervisor-manifest` top, `worker-manifest` top; core-restart steps 9-11.
- Knobs live in `config.yaml`, never in a schema: V7.
- `pid` and paths in manifests never go northbound: V11.

## Records

| Record | `$def` or shape | Direction | Protocols and steps | Status |
|---|---|---|---|---|
| `policy.yaml` | `policy.schema.json` (`users`: `permission`, `target_filter`, `user`; `recording.enabled`, decision 5.40a) | FS to CO | session-open 2; config-reload 1, 2, 8-11; operator-alerts 23 (via config-reload) | built |
| `config.yaml` | `config.schema.json` (the knob sections; every other key is deployment-local) | FS to CO | core-restart 5; config-reload 1, 2 | built; the reload is this release's |
| target file `current/<source>/<id>.yaml` | `inventory-target.schema.json` (open top and `options`; the secret keys refused) | FS to CO; CO to FS for the `tsb` source | session-open 2; fleet-discovery 4; target-write 3–5 | E3; writes E4 |
| `current` (the symbolic link to `./v<N>/`) | no contract shape: layout rule 1 | FS to CO | fleet-discovery 6, 7 | E3 |
| supervisor manifest | `supervisor-manifest.schema.json` | FS to CO | worker-placement 2; core-restart 9; operator-alerts 14 (removed) | built |
| worker manifest | `worker-manifest.schema.json` | FS to CO | core-restart 11 | built |
| spool entry, read | `spool-entry.schema.json` | FS to CO | core-restart 8; session-end-from-outside 9; spooled-end-finalize 1; data-socket-redial 10 | built |
| spool entry, deleted | `spool-entry.schema.json` | CO to FS | session-end-from-outside 16; spooled-end-finalize 8; session-open 14 | built; session-open 14 is this release's |
| VT socket directory `vt/` (one `{vt_id}.sock` each) | no contract shape | FS to CO | grid-restore 1 | E2 |

Count: 9 rows (6 with a contract shape, 2 with none, 1 directory listing).

## Gaps

- The inventory index itself has no contract shape: it is U5's own (CO-RD).
- Rights that end while a client is attached after a `policy.yaml` reload: config-reload, session-attach G-8, typing-ownership B3 (open).
- How policy is cached and invalidated stays OPEN (open_decisions section 3): config-reload step 8.
- Whether a policy reload that removes a principal ends its logins remains open (login open item 6); E4b permits a stored enabled account with no policy block to log in with no rights.
- When the core reads `vt/`, and when it notices a removed supervisor manifest outside a restart: grid-restore open items (step 1); operator-alerts open items.
- The spool is read before an E10 decision, not before E9: session-end-from-outside open items.
- No fixture reloads a policy: config-reload step 8 (owed).

## E4b account configuration

`policy.yaml` gains `account.admin` (`policy.schema.json#/$defs/permission`), read by
account-administration step 2, hot-reloaded under config-reload step 8 [C5/policy-account-admin].
`config.yaml` gains `auth.account_password_min_length`, integer 1–1024 characters, deployment DEFAULT 15;
read for newly set account passwords. The non-timer restart rule remains config-reload step 2's
[C5/config-account-password-min-length]. Account requests never write either file.

## Terminal output accounting

| Message or record | Owner | Contract and grading |
|---|---|---|
| terminal rediscovery | terminal-output-accounting §§8/9 | PTY scope (terminal-output-accounting §1); existing manifests/ended records identify absent retained resources; no new worker disk record; unknown after durable frontier, never exact stale counters [C6/terminal-close-core-restart] |
