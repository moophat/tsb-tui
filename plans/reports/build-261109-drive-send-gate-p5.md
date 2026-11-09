# Drive target prompt check — build report

**Model:** codex
**Date:** 2026-11-09 (Asia/Saigon)
**Branch:** feat/drive-send-gate-prompt-check
**Base:** tsb-tui 7e3dd5f; program v0.14.1

Outcome: implemented and verified within the authorized scope. No commit or push.

Work breakdown: inspect the accepted brief and contracts; repair and run the supplied drive tests before integration; review and repair the matcher; implement attachment reads and the final send check; extend regression coverage; update the guide; run the authorized checks and review the diff.

Files written:

- `tsb_tui/drive/prompt_pattern.py`
- `tsb_tui/drive/commands.py`
- `tsb_tui/drive/GUIDE.md`
- `tests/test_drive_prompt_matcher.py`
- `tests/test_drive_prompt_pattern.py`
- `tests/stub_server.py`
- `plans/reports/build-261109-drive-send-gate-p5.md`

Delivery and evidence:

| Item | Implementation | Tests in `tests/test_drive_prompt_pattern.py` |
|---|---|---|
| 1 | Read `get_target` at open/attach; cache for the attachment; 404 means no pattern until reattach | `test_new_reads_target_pattern_for_the_opened_session`, `test_target_edits_are_seen_only_on_next_attach`, `test_missing_target_stays_unchecked_until_reattach` |
| 2 | Whole trimmed cursor row, after existing checks; mismatch names the pattern; line sends only | `test_a_target_pattern_refuses_a_line_send_whose_cursor_row_does_not_match`, `test_matching_trimmed_prompt_sends_and_typed_suffix_refuses`, `test_the_pattern_checks_only_line_sends_not_marked_full_screen`, `test_actual_full_screen_line_send_bypasses_pattern`, `test_bare_enter_after_silence_is_checked` |
| 3 | Unusable patterns fail closed; failed reads retry before synchronous checks and sending | `test_an_unusable_pattern_or_an_unreadable_record_fails_closed_and_is_fetched_again`, `test_bad_pattern_fails_closed_without_refetch`, `test_failed_read_recovers_at_next_checked_send`, `test_unchecked_send_does_not_retry_failed_read`, `test_transport_failure_retries_and_rechecks_output_after_await`, `test_non_object_target_record_is_retried`, `test_no_look_error_precedes_unusable_pattern` |
| 4 | No command pattern field added; no target field preserves existing send behavior | `test_command_prompt_cannot_override_target_pattern`, `test_no_pattern_or_a_404_target_record_means_the_look_alone`, `test_the_pattern_never_lets_through_a_send_the_look_refuses`; existing send-gate tests |
| 5 | Guide documents error codes, attachment caching, 404 behavior, whole-row/end coverage, confirmation/password prompts, and the construction limit | Reviewed against implementation and schema; fixture support verified by `test_stub_server_serves_target_prompt_pattern` |

Red-first evidence:

- The supplied drive tests initially failed collection: `TypeError: scenario() missing 1 required positional argument: 'test'`. Their fixture constructor also did not accept their proposed arguments.
- Repaired the decorator and fixture target-record route, corrected out-of-dialect `\s` examples, and synchronized the exemption test with the existing echo/quiet-window behavior.
- Before changing `commands.py`, ran `python3 -m pytest -q -p no:cacheprovider tests/test_drive_prompt_pattern.py`: **2 failed, 3 passed**, exit 1. Both expected `prompt_mismatch` and `prompt_unusable` refusals instead answered `ok: true, sent: true`.

Matcher review:

The supplied matcher was not accepted on the strength of its 22 tests. Retained Thompson fragments and epsilon-closure simulation; repaired quantity parsing/repetition, character-class ranges and escapes, Unicode category validation, and dialect checking. Removed unsupported XSD multi-character escapes mistakenly accepted as I-Regexp. Added zero/exact/bounded/unbounded group repetitions, leading-zero bounds, invalid/Unicode digits, class endpoints and hyphens, bad categories, CR/LF semantics, invalid syntax, construction bounds, and long ambiguous-input tests.

The named standard's grammar is in [RFC 9485 §3](https://www.rfc-editor.org/rfc/rfc9485.html#section-3), not §5. This is a section-reference correction, not a dialect change. I-Regexp excludes `\s`, `\d`, `\w`, `\i`, and `\c` and their uppercase variants; permits single-character escapes including `\n`, `\r`, `\t`, `\-`, and `\^`; and dot excludes both CR and LF. `\p`/`\P` accept exactly the grammar's general categories, using `unicodedata.category`. Contract length/control-character restrictions are also enforced.

Resource refusals, never approximations:

- More than 4,096 allocated Thompson states, including nested bounded repetition; for example `(a{100}){100}`.
- Quantifier bounds above 4,096, refused before allocating copies; for example `a{4097}`.
- Python parser recursion exhaustion, translated into an explicit construction-limit error.

All become `prompt_unusable`, with an explanation. There is no additional unsupported valid grammar feature. Empty source is refused by the contract's minimum length, while empty branches/groups remain valid grammar.

`(a|a)*b` is tested with matching and nonmatching rows of 100,000 `a` characters, with a combined 3-second ceiling. The NFA retains a bounded active-state set; it does not explore backtracking paths.

Verification on the final code revision, using the prescribed test-venv/local-bin PATH:

- `make lint`: **exit 0**. Ruff: `All checks passed!`; **66 passed in 8.19s**, none skipped. Import lint clean; leak check clean; engine object, headers and PIN verified. The docs-branch guard explicitly reported `no event, not pull_request — no-op`; this was not a skipped pytest test.
- `python3 -m pytest -q -p no:cacheprovider tests/test_drive_prompt_pattern.py tests/test_drive_prompt_matcher.py tests/test_consumer_musts.py tests/test_drive_send_gate*.py`: **174 passed in 122.87s (0:02:02)**, **0 failed, 0 skipped**, exit 0.
- `git diff --check`: exit 0. Tracked and untracked path review found only the seven authorized files listed above.
- Inline code review covered attachment identity, retry ordering, gate precedence, RFC grammar, quantifier construction, epsilon cycles, and invalid record handling. No unresolved finding remains.

A preceding combined run passed 172 tests. Final review then added non-object response recovery and new-session coverage, and moved fetch preparation before synchronous checks; the final 174-test run above supersedes that earlier evidence. The new-session test initially exposed its missing test setup (`size.auto_request` was off); enabling the existing fixture's standard auto-size override fixed the setup, and its narrow run passed before the final combined run.

Open decisions: none discovered; no option selection is pending.

Not run: the whole suite or lab rigs, per this brief. No live broker, sshd, target, or workstation terminal experiment was used. Required checks may contain environment-dependent no-op guards; these are reported separately from pytest skips.

Checkpoint: done — all five delivery items, matcher review, guide, and report. Verified — final lint and 174 requested regression tests, no skips; path boundaries clean. Left — no authorized implementation work; whole-suite/lab proof remains outside this brief.

DONE
