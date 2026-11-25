# TSB protocols — terms and rules

**Status:** normative, 2026-10-07. The documents in this folder carry the contract's normative text, one TSB protocol
per document. They ship and are released with the schemas and fixtures of the same release. A schema states a
message's shape; a protocol document states the flow; where they disagree it is a defect to fix in the next release.

Everything here describes the v1 target. Each protocol carries a status tag: `built`, or the epoch that builds it
(`E2` … `E6`).

## Terms

There are three nouns. Everything else is an attribute of one of them.

| Term | What it is | Identified by |
|---|---|---|
| **component** | A box in [`docs/system-architecture.md` in tsb-core](https://github.com/moophat/tsb-core/blob/main/docs/system-architecture.md). Kinds: **process** (core, protocol worker, VT worker, supervisor, discovery worker), **store** (files, bbolt, Redis, disk: written and read, never a dialogue), **external** (surface, target: not TSB's) | a two-letter code (below) |
| **edge** | A pair of components that exchange messages (the graph word: components are the nodes, a protocol's path is a walk over edges). One edge carries several TSB protocols. Its transport is an attribute, never a reason to split it | the two codes, `XX-YY` |
| **TSB protocol** | A flow with one purpose, whose path crosses one or more edges, written as numbered steps | a plain name in kebab-case that says what the flow does, using the contract's or the corpus's own term where one exists (`typing-ownership`, `quiet-prompt`); never a code, never an edge prefix; the file has the same name |

The attributes:

- **step**, a record: `n · edge · from → to · transport · message · role · effect · refusal · told`.
  - **transport**: HTTPS (the alert stream, server-sent events, is HTTPS too), the WebSocket, a unix socket, a file,
    a bbolt write, a Redis command (E3), SSH, telnet, serial; and, on edge SV-PW only (inside U3, not contract), fork
    and exec with an inherited socketpair, and a signal, then the reap.
  - Three special forms: a step inside one component (the core clamps, decides, computes) has edge `—` and
    from = to; a step that runs another protocol has message `→ <name>`; a step built at a later epoch than its
    protocol carries its own status tag. A message that belongs to another protocol but matters here keeps its real
    edge, with message `→ <name>: <message>`; the edge column only ever holds an edge ID or `—`.
  - **message**: the one thing that crosses the edge in this step (a request, a frame, a command or event, a record),
    named as the contract's schema names it.
  - **role**: who may act: owner, keyboard holder, view-only client, admin, the core, a worker, and so on. A login
    (principal), a client (one attachment) and a surface (the software) are three different things.
  - **effect**: what changes. **refusal**: what happens when the actor is not allowed. **told**: who learns of it.
- **protocol**: `ID · purpose · steps · status · corpus clauses · seam · schemas`. The edges and roles a protocol
  touches are read off its steps, never listed separately.
- **convention**: a rule every protocol obeys, documented once in [`conventions.md`](conventions.md).
- **seam**: the contract's current folder unit, `C1` … `C8`. Kept only as a mapping from edge to seam until the
  contract is re-cut. It is not a fourth naming scheme.

"Lane" is not used here: in the spine and the root README a lane is a parallel line of work. An edge is always
written with its ID ("edge CO-PW"), so it never reads as the corpus's "the edge", the estate of devices TSB reaches.

## Component codes

| Code | Component | Kind |
|---|---|---|
| SF | surface (TUI, web, desktop, any API client) | external |
| CO | core | process |
| SV | supervisor | process |
| PW | protocol worker | process |
| VT | VT worker | process |
| DW | discovery worker | process |
| TG | target | external |
| FS | files (`config.yaml`, `policy.yaml`, inventory YAML, manifests, spool; the worker's `known_hosts`, which has no contract shape) | store |
| BB | bbolt | store |
| RD | Redis | store |
| DK | disk (recordings and the audit log) | store |

nginx terminates TLS and passes traffic through. It is not an edge end.

## Edges (v1 target)

| Edge | Between | Seam today |
|---|---|---|
| SF-CO | surface ↔ core, through nginx | C1, C2; C7 from E4 |
| CO-SV | core ↔ supervisor | C3 |
| SV-PW | supervisor ↔ protocol worker (inside U3; not contract) | — |
| CO-PW | core ↔ protocol worker | C3 |
| CO-VT | core ↔ VT worker | C4 |
| PW-TG | protocol worker ↔ target (the devices' own protocols) | — |
| SV-FS, PW-FS | supervisor, protocol worker → files (manifests, spool; the worker's `known_hosts`) | C5 |
| DW-FS | discovery worker → files (inventory) | C5 |
| CO-FS | core ↔ files (`config.yaml` has the knob shape `config.schema.json`; the target file has `inventory-target.schema.json`) | C1 (`policy.yaml`), C5 |
| CO-RD | core ↔ Redis (the target index; the vault's Part 2 from E4) | C6; built, and the index is U5's own, with no contract shape; Part 2 is contract (`vault.schema.json` `x-redis`) |
| CO-DK | core ↔ disk (the core also reads it: a recording's end found on restart, the alert set rebuilt from the audit log) | C8; C6 (audit) |

## A protocol document

1. **Header**: ID, purpose in one sentence, status tag, corpus clauses, seam(s), and links to its schemas in
   `contract/schemas/` at the pinned tag. The schemas stay the shape of every message; a document never restates a
   field list. OPEN — links or paths: every draft names its schemas as code paths, not links, because this
   checkout's `contract/` is not the pinned tag and a relative link would land on the wrong version; whether the
   headers become links when the set moves into `contract/` is the operator's.
2. **Path**: the edges it crosses, in order — one line, read off the step table, never written independently of it.
3. **Steps**: the step table.
4. **Refusals and who is told**, where the step table does not already say it.
5. **Security considerations**: only when the protocol touches a credential, who may type, or the host boundary
   (Contract §4).
6. **Open items**: questions the operator has not ruled. An open item is written as open, never as a default.
7. **Picture**: the architecture drawing with this protocol's path highlighted and its steps numbered, generated from
   the step table into `<name>.png` beside the document. Pending until the generator exists.

Plain words. A wire term is explained the first time it appears.

## Normative words

- **MUST / MUST NOT** (RFC 2119 and RFC 8174, in capitals only when a requirement is meant) for every provider
  requirement: core, protocol worker, VT worker, supervisor. What legitimately varies is a `config.yaml` knob
  (spine 5.36), never the implementer's judgment.
- **Consumer side** (surfaces, any API client): MUST for what keeps the system correct; **MAY** only where the corpus
  gives a surface freedom, and every MAY cites that clause.
- **No SHOULD.**
- Every MUST names the fixture that grades it, or says **owed** when no fixture grades it yet (`conventions.md`
  defines "owed" and "reference, test owed").

## IDs and registries

- An ID — component code, edge, protocol — is never renamed or reused. A retired ID stays listed, marked retired.
- The contract already holds the other registries: close codes, error codes, reason values. `conventions.md` points
  at them and copies nothing.

## Files

| Path | What |
|---|---|
| `README.md` | this page |
| `<name>.md` | one TSB protocol |
| `edges/<EDGE>.md` | one edge's catalogue: every message that crosses it, its transport rules, the protocols that use it |
| `conventions.md` | versioning and additivity, close and error codes, IDs and timestamps, knobs, receiver rules |

## Index

37 protocols, the conventions every one obeys, and 13 edge catalogues. Each protocol is one flow; its path crosses
the edges named in its steps. Status: `built` or the epoch that builds it.

**Signing in and finding targets and sessions**

| ID | What it does | Status |
|---|---|---|
| [login](login.md) | log in with TSB's own password, hold the login as a cookie, log out | built; grows E4 |
| [account-administration](account-administration.md) | manage login accounts and change an own password | E4b |
| [target-import](target-import.md) | preview, commit, abort or resume raw target imports | E3b |
| [target-write](target-write.md) | create, replace, delete and read editable targets in the core-owned tsb source | E4 |
| [inventory-list](inventory-list.md) | list and read the targets a principal may see, from the core's index | E3 |
| [session-list](session-list.md) | list and read the sessions a principal may see | built; grows E2 |

**A session's life**

| ID | What it does | Status |
|---|---|---|
| [session-open](session-open.md) | open a shell on a target: credential, connection (reuse or new), shell, offered once live | built; grows E2, E4 |
| [device-login](device-login.md) | answer the device's own SSH login questions in the pane before any session exists; the socket then becomes the attach | E4 |
| [session-attach](session-attach.md) | attach a client: ticket, current screen (prefix and dump), who else is here, one redraw, live | built; grows E2 |
| [session-detach](session-detach.md) | one client stops being attached; the session lives on | built; grows E2 |
| [client-remove](client-remove.md) | the owner removes one client | E2 |
| [session-end-by-tsb](session-end-by-tsb.md) | a session ended by TSB or through it (owner, admin, idle, unattended, orphan), the core present or gone | built |
| [session-end-from-outside](session-end-from-outside.md) | a session ended from outside TSB (target, connection, worker), the core present or gone | built |
| [spooled-end-finalize](spooled-end-finalize.md) | the core finalizes an end a worker spooled (`killed` or `terminated`), on its return or when a running core's re-dial or handshake finds it: record, recording, audit, once; a running core also closes the attached clients | built |

**At the keyboard** — the northbound controls

| ID | What it does | Status |
|---|---|---|
| [typing-ownership](typing-ownership.md) | typing ownership: who may type and request the size, and how that right moves | built; grows E2 |
| [size-control](size-control.md) | size control: the holder's target-size request, clamped and answered | built; grows E2 |
| [scroll-control](scroll-control.md) | scroll control: older output, page by page, on request | E5 (published E2) |
| [stale-screen](stale-screen.md) | stale screen after lost output: refuse input, the holder chooses the remedy | E2 |
| [byte-relay](byte-relay.md) | the bytes both ways, input recorded before it is sent | built; grows E2 |
| [quiet-prompt](quiet-prompt.md) | unanswered input: ask the owner "Kill the session?" | built |
| [session-countdown](session-countdown.md) | the countdown: when each session ends and which timer ends it | built |

**Keeping sessions alive** — southbound

| ID | What it does | Status |
|---|---|---|
| [worker-placement](worker-placement.md) | get a protocol worker for a new connection from a supervisor | built |
| [reattach-handshake](reattach-handshake.md) | the handshake: bring a session's data path to live, loss declared as a gap | built; grows E2 |
| [data-socket-redial](data-socket-redial.md) | a lone data-socket loss: re-dial once, refuse input meanwhile | built |
| [link-liveness](link-liveness.md) | the link to the target stopped answering: probe, report, end | built; grows E2, E3 |
| [connection-linger](connection-linger.md) | keep an empty connection briefly for reuse, then close it | built |
| [hung-worker](hung-worker.md) | a worker that stops answering is killed through its supervisor | built |
| [orphan-session](orphan-session.md) | while the core is gone: the worker keeps sessions `orphan-pending` and runs their timers; an end goes to its end protocol | built |
| [core-restart](core-restart.md) | stop the core without ending a session; on start, take everything back | built; grows E2 |

**The screen** — the VT worker

| ID | What it does | Status |
|---|---|---|
| [grid-snapshot](grid-snapshot.md) | snapshot the grid and move the commit point in one write | E2 |
| [grid-restore](grid-restore.md) | keep every grid through a VT worker's death or a core restart | E2 |

**The vault and the locks** — C7, and C1's E4 operations

| ID | What it does | Status |
|---|---|---|
| [vault-unlock](vault-unlock.md) | unlock the principal's vault for one login (untrusted or trusted), regenerate Part 2, wipe a vault | E4 |
| [device-trust](device-trust.md) | register a device's key, tie a login to it, prove it by a signed challenge, list and revoke devices | E4 |
| [vault-lock](vault-lock.md) | the core purges a user's unlocks on idle across surfaces, at the hard cap, or on "lock now" | E4 |
| [screen-lock](screen-lock.md) | a client hides its sessions after the screen-lock time; the login password, re-checked by the core, resumes it | E4 |

**Operating TSB**

| ID | What it does | Status |
|---|---|---|
| [operator-alerts](operator-alerts.md) | TSB's own operator alerts, to `broker.health` holders only | built; grows E2 |
| [config-reload](config-reload.md) | apply a changed `policy.yaml` (built) or `config.yaml` (E3) | built; E3 |
| [fleet-discovery](fleet-discovery.md) | a discovery worker writes what devices exist as target files; the core indexes each change | E3 |

**Every protocol obeys** [conventions.md](conventions.md) (V1–V16).

**Edge catalogues** — every message on an edge and which protocols use it:
[SF-CO](edges/SF-CO.md) · [CO-SV](edges/CO-SV.md) · [SV-PW](edges/SV-PW.md) · [CO-PW](edges/CO-PW.md) ·
[CO-VT](edges/CO-VT.md) · [PW-TG](edges/PW-TG.md) · [SV-FS](edges/SV-FS.md) · [PW-FS](edges/PW-FS.md) ·
[DW-FS](edges/DW-FS.md) · [CO-FS](edges/CO-FS.md) · [CO-BB](edges/CO-BB.md) · [CO-RD](edges/CO-RD.md) ·
[CO-DK](edges/CO-DK.md)

**Not in the contract yet** (named, no document until their tag): file-transfer, command-exec (E5) ·
recording-playback (v1.0) · ownership-transfer (not placed).

**Open items** in every document are parked for the contract-v0.3 approval step:
tsb-core `plans/reports/queue-261007-operator-decisions.md`.

## Terminal output accounting

[Terminal output accounting](terminal-output-accounting.md) specifies C3 0.8 PTY sealing and accounted disposal, code-9
incompleteness and linked audit facts, including explicit unrecorded context and unknown-on-close.
An older reader refuses code 9. Comparison/export includes the integrity facts with the recording.
All new terminal fixtures are published/required-green e4; reference provider tests remain owed.
