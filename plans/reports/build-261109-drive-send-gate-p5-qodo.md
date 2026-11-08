# Prompt-pattern review fixes — PR #35

**Model:** codex (moophat0)
**Branch / starting head:** feat/drive-send-gate-prompt-check / 3e9167d
**Date:** 2026-11-09 (Asia/Saigon)

## Scope and checks

Implement the four operator rulings, preserve send history, write one regression test per fix,
prove each fails on the unchanged PR head, then run the requested lint and test selection.
No commit or push. Writes are limited to the brief's allowed paths; no ledger rotation or updates.

- [x] Inspect attach, prompt reads, close paths, and regression scaffolding.
- [x] Add four regression tests before production edits.
- [x] Confirm all four failures are behavioral on the unchanged head.
- [x] Implement the four rulings and rerun the regressions.
- [x] Run required lint and broader tests; inspect final diff and record evidence.

Initial red run: 4 failed, 23 deselected in 7.33s. Three failures exposed the requested defects;
the gate-off test initially used a forbidden CLI override and was corrected to use the config file.
The corrected four-test red run is the authoritative evidence below.

## Fixes and regression evidence

| Ruling | Implementation | Regression in `tests/test_drive_prompt_pattern.py` |
|---|---|---|
| 1 | `tsb_tui/drive/commands.py:772`: after the awaited target read, reject a closed attachment, closed slot, or slot no longer owned by this screen with existing `attach_failed` and the slot's departure reason. | `test_attach_does_not_front_a_slot_closed_during_target_read` (line 333): block the synchronous target read with thread events, detach while blocked, release it, assert attachment failure and no second fronting. |
| 2 | `tsb_tui/drive/commands.py:770`: only read the target during attach when `drive.send_gate` is on. | `test_attach_with_send_gate_off_never_reads_target` (line 360): config-file gate off; assert no target-client calls or prompt cache entry. |
| 3 | `tsb_tui/drive/commands.py:948`: require matching target id; otherwise retain an explanatory unreadable-record reason and mark for retry. | `test_target_record_id_must_match_and_is_retried` (line 381): missing and different ids refuse checked sends as `prompt_unusable`; next checked send refetches and uses the recovered pattern. |
| 4 | `tsb_tui/drive/commands.py:828`, `tsb_tui/drive/runner.py:139`, `tsb_tui/screens/terminal.py:2279`: evict only prompt cache on slot release and attachment close. | `test_prompt_cache_is_dropped_on_every_close_without_losing_history` (line 402): detach, broker client removal, terminal release, broker end; cache disappears, reattach creates its own, identical send-history object and count survive. |

The terminal hook is necessary: `on_unmount` calls `_release_slot` directly and bypasses
`_close_slot`. The existing slot-closed tap moved into the common release path so terminal
closure also notifies drive, once. Broker end/removal can retain the final screen, so the
existing close event also evicts the prompt entry without waiting for chip removal.
Two pre-existing successful target-read doubles now include their target id.

## Red first (unchanged production code at 3e9167d)

Command (test venv and ruff directories prepended to PATH):

```sh
python3 -m pytest -q -p no:cacheprovider tests/test_drive_prompt_pattern.py -k 'closed_during_target_read or send_gate_off_never or record_id_must or every_close'
```

Authoritative corrected red run: **4 failed, 23 deselected in 5.36s**, exit 1, none skipped.
Each requested regression failed at its business assertion:

- Ruling 1: expected `attach_failed`; got `ok: True`, session still fronted with `chip: None`.
- Ruling 2: expected `reads == []`; got `['fixture-target']`.
- Ruling 3: expected `prompt_unusable`; got `ok: True`, `sent: True` for an unreadable identity.
- Ruling 4: expected no prompt cache after detach; the session's `PromptCheck` remained.

No production edit preceded these runs. The lifecycle regression was then strengthened to
use a real fixture-server client removal rather than calling chip removal directly.

## Verification

- Focused prompt-pattern file: **27 passed in 37.49s**, exit 0, none skipped.
- First lint run caught four introduced test/import style errors; all corrected.
- Final `make lint`: exit 0, ruff **All checks passed**, **66 tooling tests passed in 7.73s**;
  import lint, leak check, engine hashes and PIN passed. No ruff SKIP. The docs guard's
  documented local no-event no-op is not a skipped test.
- Required combined test selection: **265 passed in 209.67s (0:03:29)**, exit 0, none skipped.
- Additional terminal close/reattach/multiple-session/sign-out selection: **44 passed in 60.41s**, exit 0, none skipped.
- Final diff review: production changes follow the four rulings, send history is untouched;
  `git diff --check` passes. No commit, push, or persistent development server started.

## Open decisions

None. No contract or design decision was added or changed.

Review used the ak-code-review specification checklist against the four operator rulings:
all four present, no unrelated behavior added. Inspection covered post-await ownership and
closed-state checks, retry behavior, eviction before/after asynchronous reads, terminal
unmount's release route, broker final views, and preservation of the separate send history.
No unresolved finding within this scope. Terminal unmount is covered through its shared
release path plus the existing lifecycle tests; no live broker or lab E2E was run.

Required combined command (after `make lint`, with test venv and ruff first on PATH):

```sh
python3 -m pytest -q -p no:cacheprovider tests/test_drive_prompt_pattern.py tests/test_drive_prompt_matcher.py tests/test_drive_shell_marks.py tests/test_consumer_musts.py tests/test_drive_commands.py tests/test_drive_send_gate*.py
```

Additional close-path command:

```sh
python3 -m pytest -q -p no:cacheprovider tests/test_attach_and_close.py tests/test_ended_and_reattach.py tests/test_several_sessions.py tests/test_sign_out.py
```

**Final status:** DONE. All requested checks passed; no open decisions. Changes remain uncommitted and unpushed.
