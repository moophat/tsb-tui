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

Status: DONE
