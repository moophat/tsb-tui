# Drive send gate build

**AI-Model:** codex
**Branch:** feat/drive-send-gate
**Design:** .review/plan.md revision 4.2; .review/phase-01-gate.md

## Done so far
- Read AGENTS.md, the phase and design, repository status, affected input and drive paths.
- Confirmed clean starting tree at a1d4305 and existing sibling program checkout.
- `make contract-init`: exit 0, pinned v0.12.1 (53caef07e91e).
- Added 48 tests covering the in-scope rows plus send wait/timeout and take. Red recorded below.
- User restrictions override ledger rotation/updates and any automatic commits or journal writes.

- Implemented received/applied item counters, output bytes, keyboard/size/stale counters and attachment generations.
- Implemented persistent per-session history from processor output events, atomic screen looks and ordered checks, `take`, typed-source marker, send wait and config restrictions. Not yet verified.

- Implemented GUIDE updates and explicit look acquisition at the 20 existing sending call sites; CRLF sends changed to CR.
- Focused first implementation run: **44 passed, 4 failed in 53.65s**. Four scaffold fixes (clock, legacy encoding evidence, UI sequence) rechecked: **4 passed, 44 deselected in 5.80s**.

- Broader drive/touched/direct-input run: **411 passed, 1 failed in 327.03s; exit 1** (`/tmp/drive-send-gate-drive.log`). Failure: old two-driver event wait started after the new send wait had observed the refusal; migrated to assert the delivered event.
- Review identified queued keyboard/stale frames must remain strict for young brakes; added per-cause receive/apply counters to preserve that invariant.

- Targeted review checks: **5 passed, 48 deselected in 21.48s**, covering reconnect history, pending stale state, queued keyboard frame and both guard settings.
- Gate/two-driver/attachment run: **161 passed, 1 failed in 171.14s**. The failure exposed a test-output ordering race (late fixture echo correctly triggered `screen_changed` before `unsettled`); added same-size response barriers so the test isolates its intended check without weakening it.

- Final gate file: **49 passed in 62.27s; exit 0** (`/tmp/drive-send-gate-final-gate.log`). Earlier attachment/two-driver run had 161 passing tests including both driver variants; its sole failure was the test ordering race now fixed.
- Reviewed the pending diff with the code-review skill; no remaining supported design or correctness finding. No independent subagent review claimed.

- `make lint`: **exit 0**, 51 tooling tests passed in 3.77s; ruff, import lint, leak check and vendored-engine checks passed. The docs-branch guard explicitly reported its local no-event no-op. Log: `/tmp/drive-send-gate-lint.log`.
- `make contract-check`: exit 0, pinned v0.12.1 unchanged.
- Starting the user-requested full suite exactly once; no separate `make test` invocation.

## Steps remaining
1. Write all in-scope tests and record their red run.
2. Implement counters, persistent history, looks, ordered gate checks, take, typed-source check.
3. Config restrictions, send settle waits, guide, migrate existing callers.
4. Focused tests, drive tests, lint, one full suite; inspect allowed-path diff.

## Verification
Tests-first red run on unchanged a1d4305: `python -m pytest tests/test_drive_send_gate.py -q -p no:cacheprovider` → **47 failed, 1 passed in 53.10s; exit 1**. Log: `/tmp/drive-send-gate-red.log`. The read-reply/history/holder-wait regression guard passes. The scroll/guard regression test needs its scaffold corrected (`do_scroll`, not a nonexistent method); existing scroll/guard tests remain regression guards. No production edits preceded the red run. All commands foreground; no background processes started.

Status: NEEDS_CONTEXT

## Orchestrator note

The lane's 55-minute limit cut this job at 01:42, while its one full-suite run was going, so the Status line above is its running placeholder. Its focused runs and lint are above. The orchestrator proved the red on the old head as a whole tree (48 failed, 1 passed) and CI runs the whole suite.
