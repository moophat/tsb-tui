# spooled-end-finalize — the core finalizes an end a worker spooled

| | |
|---|---|
| **ID** | spooled-end-finalize |
| **Purpose** | The core finalizes an end a worker spooled that no core has audited, on its return or, while it runs, when a re-dial or a handshake finds the entry, or a `connection_lost` lists a session with an entry: it reads the spool entry, replays a retained session, writes the record, the recording's markers and the audit event once, releases the session, deletes the entry, and a running core closes the session's attached clients. |
| **Status** | built (E1) |
| **Parameter** | The end state the spool entry names: `killed` (end marker code 3), run from session-end-by-tsb step 27; or `terminated` (end marker code 4), run from session-end-from-outside step 19. The end protocol owns the end (the operator, 2026-10-07); this document writes its finalizing steps once for both. |
| **Entered from** | session-end-by-tsb step 27 (`killed`: `orphan`, `idle`, `unattended`, spooled by the worker), session-end-from-outside step 19 (`terminated`: `target_closed`, `connection_lost`, spooled by the worker), each after its → core-restart step (session-end-by-tsb 26, session-end-from-outside 10), and core-restart step 18 case (a). While the core runs, also: session-end-from-outside step 5 (a `connection_lost` listing a session with an entry: not retained, so step 4 applies), session-end-from-outside step 9 (E10 raised, the spool read first), data-socket-redial step 10 (a failed re-dial), reattach-handshake step 15 (`hello_ack` `ended: true`). |
| **Hands to** | reattach-handshake (a retained session's replay, then `release`). |
| **Corpus clauses** | immortality §3, §4.3; session model §12, §15; Contract §4 |
| **Seams** | C2 (a running core's close), C3, C5, C6, C8 |
| **Schemas** (`contract/schemas/` at contract-v0.3) | `spool-entry.schema.json`; `ipc-control.schema.json` (top: "Spool"), `#/$defs/release`; `worker-manifest.schema.json` (top); `continuity-state.schema.json` (top), `#/$defs/session_record`; `ttyrec.schema.json#/$defs/gap_marker`, `#/$defs/end_marker`, `#/x-gap-line`, `#/x-sidecar`; `audit-event.schema.json#/$defs/session_end`; `ws-frames.schema.json#/x-close-codes` (4010, 4012) |

Fixtures are cited as `<seam>/<name>`, under `contract/fixtures/` at contract-v0.3. Corpus files, under
`srs/`: immortality = `immortality-mechanism.md`, session model = `session-model-and-lifecycle.md`,
Contract = `architecture-contract.md`. Socket and file names are relative to TSB's node-local runtime directory.

**Words used here.**

- **The end state** (the parameter): `killed` or `terminated`, as the spool entry's pair says. Where a step differs
  by it, the step says so.
- **Spool**: one file per session end a worker originated or observed, `spool/{session_id}.json`, kept until a
  core audits it (spool-entry top). The worker writes it: session-end-by-tsb step 22 (a timer's end),
  session-end-from-outside step 3 (an end from outside).
- **Retained session**: an ended session whose ring, data socket and manifest entry the worker keeps until
  `release` or until its connection closes (ipc-control `release`).
- **`last_committed_seq`**: the seq of the last output frame or gap marker the core's recording keeps at its commit
  point, `snapshot_seq` (ipc-control `hello`; continuity-state `session_record.recording_last_seq_out`). A retained
  session's handshake realigns it to the recording's end first, record-only and its snapshot deleted, since an ended
  session's grid is never shown again (grid-snapshot rule 10); a session that is not
  retained gets no handshake, and step 4 compares bytes instead.

## C3 0.8 terminal branch

For C3 0.8 retained PTY terminal recovery within [its §1 scope](terminal-output-accounting.md#1-scope-and-acceptance), follow [terminal output accounting](terminal-output-accounting.md)
§§3/8/9 before disposal. A lifecycle spool entry for an already-audited end is still deleted unread;
it supplies no new output proof. Preserve a complete recording end, repair only its original missing
audit, and declare missing output through integrity facts. A missing/closed connection permits no new
exact suffix from stale spool/manifest values; write unknown after the last validated durable frontier
[C6/terminal-close-partial-durable-coverage; C8/audited-end-recording-unchanged].

## Path

CO-FS → (reattach-handshake) → CO-BB → CO-DK → CO-PW → CO-FS; a running core also: SF-CO

## Steps

| n | edge | from → to | transport | message | role | effect | refusal | told |
|---|---|---|---|---|---|---|---|---|
| 1 | CO-FS | FS → CO | a file | `spool-entry.schema.json`, read | the core | The core reads the spool before any E10 decision, and again on a `no_such_session` or a refused session socket (→ core-restart step 8, which owns the read order). A session with an entry MUST end as the entry says, never E10 `worker_lost`, even when its worker has exited or its socket refuses [C5/manifest-rediscovery-v0.2]. An entry for a session already audited is deleted unread (→ core-restart step 8). | — | — |
| 2 | — | CO → CO | — | → reattach-handshake | the core | A session the worker's manifest still lists (retained: session-end-by-tsb step 24 for `killed`, session-end-from-outside step 15 for `terminated`) MUST be replayed by its handshake, `hello_ack` `ended: true`, before the core writes its spooled end (worker-manifest top; spool-entry top; reattach-handshake step 15) [C3/reattach-ended; C5/manifest-rediscovery-v0.2]. The handshake runs "for a live peer only" (worker-manifest top); a manifest a dead worker left listing the session: Open item 4. | — | — |
| 3 | CO-BB | CO → BB | a bbolt write | `continuity-state.schema.json#/$defs/session_record` | the core | The core MUST persist the end the first time it knows it: state, `end_reason`, and `ended_at` as the worker stamped it, held unchanged after (continuity-state `session_record` `end_reason`, `ended_at`) [shape: C6/continuity-record-valid; the worker's stamp: owed]. The nested record then reads the end state, `killed` or `terminated`, with no countdown. | — | any principal reading the record (session-list) |
| 4 | CO-DK | CO → DK | files `<session_id>.ttyrec` + `.dir` | `ttyrec.schema.json#/$defs/gap_marker`, sidecar code 2 | the core | Only for a recorded session that is not retained, when the spool's `out_bytes` exceed the output bytes its recording holds (`out_bytes_committed` plus what it keeps past the commit point, gap markers by their bytes; ttyrec `x-sidecar` reopen): the core MUST write one gap marker before the end marker, `out_bytes` minus those bytes lost, over the span from the stamp of the last output frame or gap marker it recorded to `last_out_ts`, its data the `x-gap-line` line, stamped with the span's end, so the recording never reads as complete when it is not (spool-entry top; ttyrec top; immortality §4.3) [`killed`: C8/gap-before-end; `terminated`: owed: the fixture grades `killed`/`orphan` only]. Its stamp is no later than the end [`killed`: C6/event-spooled-end; `terminated`: owed: the fixture grades `killed`/`orphan` only]. A recording that already holds its end marker gets neither marker again: a core that died after step 5 and before step 8 finds both kept, since no restart cuts them (`x-sidecar` reopen; continuity-state top) [owed]. | — | — |
| 5 | CO-DK | CO → DK | files `<session_id>.ttyrec` + `.dir` | `ttyrec.schema.json#/$defs/end_marker`, sidecar code 3 (`killed`) or 4 (`terminated`) | the core | For a recorded session that does not hold one yet (step 4) the core MUST write exactly one end marker, as the last frame, and after it the recording takes nothing more, not even a replayed frame, its code the spooled `end_state`, stamped `ended_at` (spool-entry top; ttyrec top, `x-sidecar`) [C5/spool-entry-shape; `killed`: C6/event-spooled-end, C8/end-marker-killed; `terminated`: C8/end-marker-terminated, whose description has the core observe the end (Open item 5), and for C6/event-spooled-end owed: the fixture grades `killed`/`orphan` only]. | — | — |
| 6 | CO-DK | CO → DK | a file, the audit log (append-only JSONL) | `audit-event.schema.json#/$defs/session_end` (`session.disconnect`) | the core | `ts` MUST be `ended_at` to the nanosecond, never the time the core came back, with the spooled pair, no `user`, `session_type`, and `recording_path` absent, never empty, when there is no recording (audit-event top, `session_end`; session model §15) [`killed`: C6/event-spooled-end; `terminated`: owed: the fixture grades `killed`/`orphan` only; C5/spool-entry-shape; C6/event-recording-path-absent-not-empty]. The core MUST write it exactly once per session [C5/manifest-rediscovery-v0.2; C8/end-marker-killed]. | — | readers of the audit log |
| 7 | CO-PW | CO → PW | unix socket `control.sock` | → reattach-handshake: `release` | the core | A retained session only, after steps 5–6, in `live`'s place (spool-entry top; ipc-control `release`) [C3/reattach-ended]. | — | the worker, which drops the session (reattach-handshake step 17) |
| 8 | CO-FS | CO → FS | a file | the spool entry the worker wrote (session-end-by-tsb step 22; session-end-from-outside step 3), deleted | the core | Once the audit event is written, the core MUST delete the entry, so every entry is deleted by the core that audits its end and lives only while no core has (spool-entry top) [C5/spool-entry-shape]. | — | — |
| 9 | SF-CO | CO → SF | WebSocket | close 4012 `session_terminated` (`terminated`) or 4010 `session_closed` (`killed`) (`ws-frames.schema.json#/x-close-codes`) | the core | A running core only: an entry found by a failed re-dial, a `no_such_session` or a handshake answering `ended: true` while the core runs (session-end-from-outside step 9, data-socket-redial step 10, reattach-handshake step 15), and an end read from the spool at a `connection_lost` (session-end-from-outside step 5) can have clients attached. The core MUST close every one with the end state's code: "Every such end closes attached clients 4012" for an end from outside (ipc-control top), and 4010 for `killed` (session model §12) [owed]. A returning core has none to close. Where this falls among steps 3–8 is not said. | — | every attached client |

## Refusals and who is told

- **Nothing here refuses a principal.** The core acts on what the spool says.
- **Who is told.** A returning core has no client to close: every client was disconnected with its core (immortality
  §3, "All disconnected"), so no 4010 or 4012 goes out. A running core that finds the entry closes every attached
  client with the end state's code (step 9). A later attach is closed 4004, "No such session, or it has ended"
  (ws-frames `x-close-codes`; session-attach). Principals read the end in the record (session-list); auditors read
  `session.disconnect` (step 6).

## Security considerations

- **Credentials and the host boundary.** The spool carries no credential and no terminal byte
  [C5/spool-entry-shape]. The spool reaches no surface, and the record and the audit event the returning core writes
  name no pid, path or host (Contract §4; conventions V11) [owed].

## Open items

1. **The grid (E2).** Whether the returning core sends VT `close` for a spooled end, or first restores its grid
   (grid-restore), is unspecified (not in plan §7). session-end-by-tsb Open item 7 asks the same for every end.
2. **Order on return.** The stated order is the replay when retained, then gap marker, end marker, audit event,
   `release`, delete (spool-entry top). The first `ping` after a restart goes before the first `hello` on its control socket (core-restart step 22), so before step 2; where step 3's bbolt write falls stays unspecified.
3. **Closed — queue A1.** No restart cuts the recording back to the commit point, so the gap and end
   markers this protocol writes stay once written; a core that finds them on its return writes neither again (step 4).
4. **OPEN — a retained session listed by a dead worker** (step 2). worker-manifest top runs the replay handshake "for
   a live peer only", and a crashed worker's manifest is "left in place"; spool-entry top branches on whether "the
   worker's manifest still lists the ended session". Whether a session still listed by a worker that has died takes
   the "if not" branch, the gap marker from the spool's numbers (step 4), is not said. core-restart step 18 case (a)
   and step 23 read the same text.
5. **OPEN — who stamps a spooled `terminated` marker** (step 5). `C8/end-marker-terminated`'s description stamps it
   "as seen by the component that observed the end (the core at this tag)"; spool-entry top lets a worker spool
   `terminated` / `target_closed` or `connection_lost`, its marker stamped with the worker's `ended_at`.
   `C8/end-marker-killed` names the worker for a spooled end; the terminated fixture's text predates the spool.
   Which text holds, a fixture fix or a rule, is not decided.
6. **OPEN — an end a running core's handshake finds** (step 9; reattach-handshake and data-socket-redial open
   items). spool-entry top: "A core that is attached when the end happens audits it live and deletes the entry";
   ipc-control `release` follows "a handshake whose hello_ack reported ended: true and whose replay is complete".
   An end that lands between a lone loss and `hello_ack` `ended: true` meets both texts; whether it is finalized
   here or as a live end (→ session-end-from-outside) is not said.

## Picture

pending

**Recording hold exception.** While held, write no frame or end marker to either pair. Persist the session end and `session.disconnect` at the end, on the original generation-0 recording path, before a marker exists. If replay is gone, the linked successor holds only the sole end marker and the notice at P declares the unknown remainder. After every end marker sync, write `recording_end_placement` `present` and its end offset to bbolt (terminal-output-accounting §5.3; owed).
