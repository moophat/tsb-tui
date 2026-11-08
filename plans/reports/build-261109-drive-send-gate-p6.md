# Drive shell marks — build report

**Model:** codex
**Date:** 2026-11-09 (Asia/Saigon)
**Branch:** feat/drive-shell-marks
**Base:** 3e9167d; program v0.14.1

Outcome: the base already contains shell-mark support from PR #13. Inspection found its send-check ordering disagreed with the current ruling after the prompt-pattern integration. Added regression tests first, then moved the shell check after the prompt-pattern checks. No commit or push.

Work breakdown: read the brief, required plan sections, relevant contracts, callers and existing tests; identify the remaining mismatch; add and run regression tests before implementation; repair ordering; clarify the guide; run the requested gates; review the diff and report.

Files written:

- `tsb_tui/drive/commands.py` — shell check now follows both prompt errors.
- `tsb_tui/drive/GUIDE.md` — explicitly names the prompt-pattern check before shell marks.
- `tests/test_drive_shell_marks.py` — two precedence regressions, including proof no bytes reach the stub.
- `plans/reports/build-261109-drive-send-gate-p6.md` — this report.

No attachment edit was needed: the existing processor feeds live output to the attachment's incremental scanner after the engine applies it, excludes dumps, and replaces the scanner on attach. No engine binding change.

Each ruled point and its evidence (test names below are in `tests/test_drive_shell_marks.py`, unless specified):

| Ruling | Implementation and tests |
|---|---|
| 1. Scan received bytes in processor, per-session state, split marks included | Existing `ShellMarks.feed` and `AttachmentPump._feed_item`; `test_prompt_then_command_mark_refuses_line_sends`, `test_split_prompt_and_command_marks_are_kept_across_runs`, `test_escape_at_a_chunk_end_opens_the_mark_in_the_next_chunk`, and `test_plain_text_inside_an_open_mark_is_scanned`. |
| 2. Line-ending sends without full_screen | Existing gate predicate; `test_prompt_then_command_mark_refuses_line_sends` covers type, Enter, Ctrl-J and a permitted partial send; `test_shell_gate_is_last_and_does_not_apply_to_full_screen` covers the exemption. `test_no_prompt_start_means_no_extra_gate` covers a plain stub prompt and the unchanged no-mark path. |
| 3. not_at_prompt with screen and look; guide error coverage | Existing refusal uses the shared screen/ look helper; `test_prompt_then_command_mark_refuses_line_sends` asserts these fields and an unchanged received-input list. `test_guide_lists_shell_mark_refusal_in_error_table` and `tests/test_drive_commands.py::test_the_guide_names_every_command_event_and_error_drive_has` verify documentation. |
| 4. After prompt-pattern check | Reordered the existing shell check. New `test_prompt_mismatch_precedes_shell_mark_refusal` and `test_unusable_prompt_precedes_shell_mark_refusal` prove both earlier errors win and no input is sent. |
| 5. Settled type exit_code, only a new D, data only | Existing send-local serial and attachment identity checks; `test_exit_code_is_reported_only_for_the_settled_send_that_received_it` proves the last result appears once and the next no-mark answer omits it. `test_unsettled_answer_omits_exit_code`, `test_final_codeless_mark_omits_an_earlier_exit_code_from_same_send`, and `test_type_reports_exit_codes_with_options_and_omits_nonnumeric_fields` cover answer boundaries. The guide states drive never acts on it. |
| 6. Attach/reattach reset; dump marks excluded | Existing scanner replacement in `apply_attached`; `test_attach_resets_shell_state_and_dump_does_not_enable_it` exercises detach/attach; `test_dump_marks_and_partial_marks_do_not_survive_reattach` exercises replacement, dump marks and a partial old mark. |

Red-first evidence, preserved before editing production code:

```text
python3 -m pytest -q -p no:cacheprovider tests/test_drive_shell_marks.py -k 'precedes_shell_mark_refusal'
FAILED test_prompt_mismatch_precedes_shell_mark_refusal
  actual not_at_prompt; expected prompt_mismatch
FAILED test_unusable_prompt_precedes_shell_mark_refusal
  actual not_at_prompt; expected prompt_unusable
2 failed, 43 deselected in 3.55s
exit 1
```

The remaining shell-mark tests pre-existed on the base and are compatibility evidence, not newly authored red tests.

Verification uses the test venv and ruff first on PATH:

- `make lint`: exit 0; ruff ran, `All checks passed!`; 66 passed in 7.90s, zero skipped. Import lint clean; leak check clean; engine and headers verified. The docs-branch guard reported its non-PR no-op, separately from test skips.
- `make contract-check`: exit 0; clean program gitlink at v0.14.1.
- `python3 -m pytest -q -p no:cacheprovider tests/test_drive_send_gate.py tests/test_drive_send_gate_fixes.py tests/test_drive_prompt_pattern.py tests/test_drive_prompt_matcher.py tests/test_drive_shell_marks.py tests/test_consumer_musts.py tests/test_drive_commands.py`: exit 0; **263 passed in 209.84s (0:03:29)**, zero failed, zero skipped.
- `git diff --check`: exit 0. Tracked and untracked path review found only the four authorized files listed above.

Inline review with the ak-code-review skill covered the pending diff, the six rulings, exemptions, refusal shape, exit-code lifetime, existing scanner and attach-reset paths. No additional implementation finding remains.

Open decisions/options: none discovered; no new design choice made. Existing parser and no-prompt-start behavior were retained.

Not run: whole suite and lab rigs, as instructed. Tests use the in-process stub; no live broker or sshd was targeted. Forbidden paths were left unchanged.

Checkpoint: done — ordering repair, regression tests, guide and report. Verified — lint, contract pin, 263 requested regression tests and allowed paths. Left — no authorized implementation work.

DONE
