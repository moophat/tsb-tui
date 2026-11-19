# Build report: the full Sessions view builds rows only for the window it paints (tsb-tui #100)

Base: main 9638d91. Model: claude-sonnet-5-5 (native sub). No commit, no push.

## Files changed

- `tsb_tui/screens/fleet_view.py`: `FleetViewModel` gains `lazy_rows: RowSource | None`. A `RowSource` holds the
  records plus four readers that build no row (`head`: id, target, ended; `search`; `cells`: the ten column cells;
  `row`: builds one `FleetRow`). The layout filters, orders (live first, then ended, each in listed order), counts,
  sizes the columns and groups by target from those readers, and calls `row` only inside the lazy line closure for a
  line in the painted window. The filtered table and its column sizes are cached on the model for the filter and
  show-ended key, so a keystroke that moves the selection does not walk the list again. `built_rows` wraps a
  hand-made model's `rows`, so `visible_rows` and the existing layout tests keep working.
- `tsb_tui/screens/terminal.py` (the full view's model and its callers only): `_fleet_view_model` takes the
  `FleetScan` the callers already hold (its target counts, per-record login and merged records replace the second
  scan the function did), builds `RowSource` instead of a `FleetRow` per record, and reads one clock per model.
  `_fleet_row` splits into `_fleet_cells` (the ten cells from the record alone, used for column sizing and for the
  built row) and the rest. Both callers (`_sync_fleet_widgets`, `_refresh_view_clocks`) pass the scan.
  Needs-you rows stay built eagerly (that list is short by construction).
- `tests/test_sidebar_painted_rows.py` -> `tests/test_fleet_view_painted_rows.py`; `tests/golden_sidebar_painted.py`
  -> `tests/golden_fleet_view_painted.py`; `tests/sidebar_painted_golden.json` -> `tests/fleet_view_painted_golden.json`
  (old golden retired; new one captured from the unchanged 9638d91 package via `git archive`, regenerated twice,
  byte-identical).

## Tests (13 in the moved file; 11 moved by intent, see mapping)

Red = on 9638d91 with the new tests. Of 13, 2 are behavioural red; 11 were green on 9638d91 because they pin
behaviour that must not change.

| Test | 9638d91 | After |
|---|---|---|
| one_rebuild_builds_rows_only_for_the_lines_the_view_paints | RED `14000 <= 35+2`, rows 14000 | green |
| a_tick_moves_the_clocks_of_the_open_view_and_builds_only_the_painted_rows | RED rows 14001 vs 33 painted | green |
| painted_view_matches_the_golden_at_a_long_list (6 views: 100, 1000, 14000, ended bottom, ended grouped by target mid-list, filtered) | green (golden is its own output) | green |
| selection_moves_over_the_listed_rows_as_before (also jump to end, scroll-to-selection) | green | green |
| selection_follows_its_session_when_the_list_changes (deep row, poll adds a session) | green | green |
| filter_and_show_ended_count_every_record_not_the_window | green | green |
| columns_are_sized_by_every_listed_row_not_the_window | green | green |
| a_clock_that_changes_a_row_s_width_is_painted_as_a_rebuild_paints_it | green | green |
| triggers_before_a_refresh_run_one_rebuild | green | green |
| opening_the_view_shows_the_current_rows | green | green |
| a_relayout_after_a_tick_builds_its_rows_at_the_current_clock | green | green |
| the_open_full_view_clocks_move_with_the_tick | green | green |
| the_one_pass_over_the_records_gives_what_the_three_scans_give | green | green |

The sidebar-widget-only cases (details clocks, out-age width, selection in the unframed sidebar) were not moved:
the side panel's painted window keeps its own coverage in `tests/test_sidebar.py`; those three test the unframed
`sidebar_view`/widget, not the full view. The one-pass scan test is unchanged. Margin: the view builds one row and
one entry per painted line, bound `painted + 2` (the side panel's constant).

## Numbers at 14,000 records (95% ended), one `_sync_fleet_widgets` with the full view open, best of 5

| | rebuild | entries | rows |
|---|---|---|---|
| 9638d91, ended hidden | 360 ms | 14000 | 14000 |
| after, ended hidden | 37 ms | 32 | 32 |
| 9638d91, show ended | 443 ms | 14000 | 14000 |
| after, show ended | 189 ms | 32 | 32 |

What remains is O(records) work that builds no row: the merged-record scan, the filter, and (show ended) sizing
the columns from every listed row's cells (~125 ms of the 189). Sizing by every listed row is kept so column
widths do not shift while scrolling (tested); making it window-bound would change widths: not done, decision
for the orchestrator if wanted.

## Commands (PYTHONPATH = clone's program/contract/client and clone root, python the repo's test venv, NO_COLOR unset, each to its own file)

- touched files `tests/test_fleet_view_painted_rows.py tests/test_fleet_view.py`: exit 0, 31 passed
- `make lint`: exit 0
- `make contract-check`: exit 0 (program at v0.18.0)
- `make test` (final, after the test fixes): exit 0, 2356 passed, 3 skipped (CI-image-only installers tests), 41 min

## Not verified / notes

- First full `make test` (40 min) had 2 failures, both in the new test file's own timing: the late alert stream
  landing a second rebuild inside the trigger-coalescing test, and a width read before the view got its size. Both
  fixed in the tests (wait for the alert answer; wait for the view's width); the file then passed 3 of 3 alone.
- The golden masks the alert count (`Alerts N`), which the fixture's alert stream sets at its own time.
- The view's tick still rebuilds the model per second while open (as before); it now costs the ms above, not 14,000 rows.
- No visual check in a real terminal.

## Round 1 — a copied view model must not share the cached table (Qodo)

Model: deepseek-v4.1-flash (opencode-go). No commit, no push.

Qodo found that `FleetViewModel.cache` was an ordinary init field: `dataclasses.replace(model, ...)` handed the copy
the same dict, and `_table` returned the cached table whenever the `(needle, show_ended)` key matched, never checking
which rows it was built from. A later `replace(model, rows=...)` painted, counted and sized the original's rows.

`tsb_tui/screens/fleet_view.py`: the field is now `field(default_factory=dict, init=False, compare=False,
repr=False)`. `replace` does not pass an `init=False` field, so the copy gets a fresh empty cache; every other
behaviour is unchanged. `tsb_tui/screens/fleet_view.py:197`.

`tests/test_fleet_view_painted_rows.py`: `test_a_copied_model_does_not_share_the_cached_table` builds a model, reads
its table (so the cache is populated), copies it with `replace(..., rows=<different rows>)`, and asserts the copy
lists the new rows while the original still lists its own. Recorded RED before the fix (the copy returned
`['old-1', 'old-2']`, expected `['new-1']`); green after.

Commands, each to its own file, `NO_COLOR` unset, `PYTHONPATH` = this clone's `program/contract/client` and root,
the repo's test venv:

- `tests/test_fleet_view_painted_rows.py`: exit 0, 14 passed
- `tests/test_sidebar_painted_rows.py`: exit 0, 2 passed
- `make lint`: exit 0

## Round 2 — the cache is reused across a tabs-only copy and missed across a rows copy (Qodo)

Model: deepseek-v4.1-flash (opencode-go). No commit, no push.

Round 1 made `FleetViewModel.cache` per instance (`init=False`). Qodo then found the other half: `FleetView._build`
calls `replace(model, tabs=self.tabs)` on every layout (reached by `_move`, `_jump`, `_relayout`), so each arrow key
started with an empty cache and rescanned every record (14,000 at scale).

Ruled fix. `tsb_tui/screens/fleet_view.py`: the cache is an ordinary field again,
`field(default_factory=dict, compare=False, repr=False)`, so `replace` hands the copy the same dict
(`fleet_view.py:198`). `_table` stores beside the `(needle, show_ended)` key the two objects the row source is read
from - `model.rows` and `model.lazy_rows`, compared with `is` - and returns the cached table only when the key
matches and both identities match; otherwise it rebuilds and replaces the entry (`fleet_view.py:245`). The heads pass
is cached under the same pair of identities, so a copy with other rows rebuilds it too and a copy that only changed
`tabs` reuses it. A model built for a poll is a new instance with a new dict, so a poll still starts clean.

`tests/test_fleet_view_painted_rows.py`: round 1's `test_a_copied_model_does_not_share_the_cached_table` is kept
unchanged and stays green. Added `test_a_keystroke_that_only_moves_the_selection_does_not_rescan_the_records`: after a
layout on the 14,000-record fleet with a filter set, it wraps the model's lazy source so the heads pass and the filter
are counted, installs the wrapped source (a tabs-only `replace` follows), resets the counts, and moves the selection
once with `_move(1)`. RED first on round 1's tree: the move rescanned the heads of 14,000 records
(`assert 14000 == 0`); green after the fix (0 heads, 0 filter calls).

Commands, each to its own file with the exit code checked, `NO_COLOR` unset, `PYTHONPATH` = this clone's
`program/contract/client` and root, the repo's test venv:

- round 2 test alone, on round 1's tree (the source change stashed): exit 1, 1 failed, the head count 14000
- `tests/test_fleet_view_painted_rows.py` and `tests/test_sidebar_painted_rows.py`: exit 0, 17 passed (15 and 2)
- `make lint`: exit 0

Status: complete; the two test files pass and `make lint` exits 0.
Summary: the cache is shared by `replace` and guarded by the row source's identity, so a tabs-only copy reuses it and
a rows copy rebuilds it; one new test, red on round 1's tree, pins the keystroke path.
