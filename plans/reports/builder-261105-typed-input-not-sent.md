# Typed input not sent

**AI-Model:** codex
**Branch:** fix/tui-typed-input-not-sent

## Steps and checkpoints

- [x] Read instructions, inspect input path, status notes, pump and drive callers; contract pin verified.
- [x] Add regressions: initial unchanged-code run had 6 failures and 6 passes; recovery regression also fails with HEAD send_input loaded only in a separate Python process.
- [x] Implement typed drop accounting, reconnecting status note and truthful pump send; first 13-case run passes, including real reattach.
- [x] Run permitted focused tests and make lint; inspect allowed-path diff. All final regression cases pass; see historical scaffolding failure below.

The explicit task overrides ledger rotation and updates. No .aiworkflow files will be changed.
The existing attach hook provides a natural clearing point for the reconnecting note.
Drive already returns sent=false on a failed send. Only terminal.py send_input calls pump.send in production.

## Changes and evidence

1. `TerminalScreen.send_input` counts and logs typed drops at both the missing-client/closed-state check
   (`reason=not_attached`) and `ConnectionClosed` (`reason=connection_closed`). Key and paste tests assert the
   source, byte count, reason, counter increment, exact status sentence and server-side absence of the dropped byte.
   Derived focus/reply input remains silent at both failure points.
2. The notice uses `_scroll_notice` and `refresh_status`, like the scroll-drop notice. `StatusInputs.input_notice`
   carries this existing notice to the status row outside scroll mode, including while its reattach row is active.
   Existing scroll-note clearing remains effective; `_on_attached` also clears this specific note for the front slot.
   The real socket-drop test verifies that it remains visible through a blocked reconnect and clears on reattach.
3. `AttachmentPump.send` raises the published `ConnectionClosed` exception for an absent or closed attachment;
   otherwise it delegates to the transport. Tests assert the exception and that no underlying send was attempted.
4. Caller audit: `rg` of production `.send(` calls finds exactly one caller of **pump.send**:
   `TerminalScreen.send_input`, which catches `ConnectionClosed` and returns False after the typed-drop handling.
   Its consumers are key and paste handlers (visibility now happens within send_input), mouse reporting (uses the
   boolean), focus/engine replies (silent derived input), and drive's `_send` (turns False into a DriveError carrying
   `sent=False`). `smoke.py` sends through contract attachments directly, not AttachmentPump; unchanged.
   `tests/test_typed_input_not_sent.py` is the only test directly invoking pump.send; it expects ConnectionClosed.
5. Drive code is unchanged. Tests inspect the actual JSON answer for closed state, a send raising ConnectionClosed,
   and a closed underlying attachment: `ok=False`, `error=not_attached`, `sent=False`, no `queued`, and
   `why` includes `nothing was sent`. Before the fix, the last case answered `ok=True, queued=1`.
6. Recovery evidence includes the normal attached-frame application on the existing socket, followed by a flush
   marker and an assertion that only fresh `y` arrived. A second test aborts a real fixture socket, gates the next
   async_attach, types `x` during the gap, releases the gate, and proves the old socket received nothing and the new
   socket received only `y`. No buffer or replay path was added.

## Before-fix proof (no on-disk reversion)

The initial 12-case test file ran before changing production code: **6 failed, 6 passed in 8.70s**.
Failures: all three typed-key drop cases, both absent/closed pump cases, and drive's closed-pump false success.
The silent derived-input cases and drive's existing explicit-False cases passed already; these are compatibility
checks. Making them fail before the fix would misrepresent the existing behavior. Likewise no-replay alone already
worked; the recovery tests additionally require the missing visible drop notice, and fail there before the fix.

The later real-reattach test was also run in a separate Python process with HEAD's original send_input compiled
from its AST and assigned only in that process. It failed specifically at the missing exact status notice during
reconnect (**1 failed in 1.01s**). No working file was reverted. An expanded baseline run similarly loads HEAD's
send_input, _on_attached and AttachmentPump.send in an isolated process for the final parameterized file:
**10 failed, 6 passed in 41.73s**. Failures include both key and paste variants; compatibility checks remain passing.

## Verification and limitations

- Before production edits: `env -u NO_COLOR python -m pytest tests/test_terminal_keys.py -q -p no:cacheprovider`:
  **2 passed in 3.52s**. `make contract-check`: clean at v0.12.1 (`53caef07e91e`).
- Fixed implementation: new regression file plus `test_status_row.py` and `test_status_row_narrow.py`:
  **95 passed in 21.48s**, no skips.
- Broader allowed run (`test_typed_input_not_sent.py`, `test_input_path.py`, `test_derived_input.py`,
  `test_model_attachment.py`, `test_terminal_keys.py`, `test_scroll_view.py`, `test_ended_and_reattach.py`):
  **177 passed, 1 failed in 173.70s**, no skips. All **165 existing tests passed**. The sole failure was the new
  recovery test scaffolding described below, corrected and rerun successfully within the 95-test final run.
- `make lint`: **All checks passed; 51 passed in 3.73s**; import lint clean, leak check clean, engine object and
  headers verified against PIN. The docs branch guard reports its standard no-event/no-PR no-op.
- One earlier test-scaffolding failure called attachment.close while the pump reader was active, violating its
  single-reader rule. Corrected the test to abort the transport. The corrected real-reconnect case passes in the
  final regression/status run. No production change was needed for that test correction.
- Full suite, benchmarks, load tests, real broker/sshd, lab/staging harness, and manual terminal/phone behavior
  were not run. All test traffic uses fixtures/doubles. CI owns full-suite verification.
- No commits or pushes; no ledgers, rulings, contract or program files modified.

Final path audit: only the three production files, the new regression file, and this report changed.
`git diff --check` passes. Nothing remains to implement or verify within the requested local scope.

## Review follow-up — #9 / head 8ceb93e

**AI-Model:** codex

The ruled follow-up supersedes the original screen-level note ownership and scroll clearing behavior above.
Outcome: each slot owns its reconnect warning; only an actively reconnecting front slot renders it; attaching
that slot anywhere clears it. Ended/stopped input is counted without replacing the existing status text.
Scope remains tsb_tui/, tests/ and this report, with no commits, pushes, ledger writes or program edits.

- [x] Reproduce all six new cases against unchanged production code: 6 failed, 16 deselected in 5.10s.
- [x] Move warning ownership to SessionSlot, clear on any attach, gate rendering to active reattach,
  and count discarded workspace keys after an end while preserving existing end actions.
- [x] Verify touched tests, nearby terminal/status tests, make lint and allowed paths.

Before-fix evidence was collected by adding only the tests and report, then running:
`env -u NO_COLOR python -m pytest tests/test_typed_input_not_sent.py -k 'belongs_to_its_slot or reconnecting_is_over' -q -p no:cacheprovider`.
All six cases failed before any production edit, without reverting files or substituting implementation methods.
The same command after the fix reports **6 passed, 16 deselected in 5.19s**.

### Review findings 1 and 2: slot ownership and scroll independence

- Added `SessionSlot.input_notice`, alongside `keys_not_sent`, for this slot's dropped-input warning.
- Drop logging uses the supplied slot, including if another session becomes front while a send is pending.
- `_on_attached` clears the supplied slot's warning before the front-only status refresh, so a background attach
  clears it too. The warning is never read from or written into `_scroll_notice`.
- The four parameter cases in `test_reconnect_warning_belongs_to_its_slot` prove: a healthy front session shows
  no warning; returning to the still-disconnected session restores its warning even after closing a scroll view;
  a background attach clears the owning slot's field and rendered warning; opening, writing a scroll-drop
  notice, and closing the scroll view neither clear nor overwrite the reconnect warning.
- Each case also flushes fixture traffic and verifies neither attachment received the dropped byte.

### Review finding 3: active reattach only, final status preserved

- `status_inputs` supplies only the front slot's notice, and only while that slot has a pending reattach,
  has not stopped reattaching, and has no ended view. The existing status-row renderer is unchanged.
- Ordinary keys on the ended workspace now enter `_send_key` and the normal closed-attachment drop path, so
  they count and log without being transmitted. The existing `c` and `n` workspace actions are preserved.
- The ended/stopped parameter cases in `test_input_after_reconnecting_is_over_preserves_status_and_counts_drop`
  begin with a reconnect warning, transition to permanent close (4010) or stopped reattach, verify the proper
  status words, then type another key. They assert byte-for-byte unchanged status text, no reconnect warning,
  exactly one new counted/logged drop, and no received bytes after a flush marker.
- The original key/paste regression setup now explicitly models an active reattach: the ruled warning no
  longer appears merely because a state is closed. Derived focus/reply cases additionally assert that the slot
  warning field stays empty. No transport, drive, wire-format or contract behavior changed in this follow-up.

### Follow-up verification and limits

- Focused final command:
  `env -u NO_COLOR python -m pytest tests/test_typed_input_not_sent.py tests/test_ended_and_reattach.py tests/test_scroll_view.py tests/test_status_row.py tests/test_status_row_narrow.py tests/test_terminal_keys.py tests/test_input_path.py -q -p no:cacheprovider`
  reports **145 passed in 85.47s**, no skips. This includes all 22 cases in the touched regression file,
  existing ended-dialog/workspace actions, retry handling, scrolling, status layout, terminal keys and input routing.
- Final `make lint`: **All checks passed; 51 passed in 3.67s**. Import lint and leak check clean; engine object
  and headers verified. Docs branch guard reports its normal no-event/no-PR no-op.
- `git diff --check`: clean. Final diff contains only `tsb_tui/screens/terminal.py`,
  `tests/test_typed_input_not_sent.py` and this report; index unchanged. No commits or pushes.
- All test/lint process sessions started for this follow-up have exited. No server was left running.
- Not verified: full suite, benches, load, a real broker/sshd, lab/staging harness, or manual terminal/phone use.
  These were excluded by the shared-machine scope. CI remains responsible for the full suite.
- Done: all three ruled findings and all six new before/after regressions. Pending: none within this scope.

Status: DONE
