"""The status row on a narrow window: what survives, in what order (design D43, §2.2 principle 3, §2.6).

Why these matter (Rule 9): a phone's 65 columns cannot hold everything, and a row cut mechanically loses the
words that matter while keeping the ones that do not: the countdown's value, the owner's warning that typing
takes the keyboard back, "input refused" and the recording mark went, and a `·` was left hanging. The row
must give up the session name first, then keep, in order, who types, the state's key fact, `● REC` and
`≡ Menu`, and it must never show half a phrase or a separator with nothing on one side. Wide rows
(120 and 200 columns) must not change. Pure layout: `build_pieces` over the contract's own frames.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from model_frames import state_of, token_frame
from prompt_support import NOW, iso, notice, validated
from test_status_row import (
    ME,
    ME_USER,
    PEER,
    PEER_USER,
    THREE,
    VIEWER,
    VIEWER_USER,
    _inputs,
)

from tsb_tui.widgets.status_row import ReattachStatus, build_pieces

SESSION = "rtr-core-01"


def _row(state, width: int, **overrides) -> str:
    row = "".join(p.text for p in build_pieces(_inputs(state, now=NOW, **overrides), width))
    assert len(row) == width, f"the row is {len(row)} cells at {width}: {row!r}"
    return row


def _apply(state, *frames: dict):
    for frame in frames:
        state.apply_frame(validated(frame) if frame["t"] != "token" else frame)
    return state


def _you_type():
    return state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)


def _owner_view_only():
    return state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)


def _someone_else_types():
    return state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)


def _read_only_right():
    return state_of(me=(VIEWER, VIEWER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)


def _nobody_types():
    state = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    state.apply_frame(token_frame(None, "owner"))
    return state


def _owner_took_it():
    state = state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    state.apply_frame(token_frame((ME, ME_USER), "takeover"))
    return state


def _with(make: Callable, *frames: Callable[[object], dict]):
    def build():
        state = make()
        return _apply(state, *(frame(state) for frame in frames))
    return build


def _link(state):
    return notice(252, "link_timeout", session_id=state.session_id)


def _idle(state):
    return notice(2 * 86400 + 23 * 3600 + 60, "idle", session_id=state.session_id)


def _data_path(state):
    return {"t": "data_path", "v": 1, "session_id": state.session_id, "state": "lost", "at": iso(NOW)}


def _stale(state):
    return {"t": "stale", "v": 1, "session_id": state.session_id, "state": "pending", "cause": "grid_lost",
            "at": iso(NOW)}


def _refused(reason: str):
    return lambda state: {"t": "input_refused", "v": 1, "reason": reason}


# (id, the state, the key fact at its shortest wording, the part of it every wording keeps, words that stay
# whenever the session name does)
STATES = [
    ("you-type", _you_type, None, None, ()),
    ("owner-view-only", _owner_view_only, "you own it: typing takes it", "you own it: typing takes it", ("fixture-peer types",)),
    ("someone-else-types", _someone_else_types, "fixture-user types", "fixture-user types", ()),
    ("read-only-right", _read_only_right, "your right is read-only", "your right is read-only", ("fixture-user types",)),
    ("nobody-types", _nobody_types, "put it aside", "aside", ()),
    ("owner-took-it", _owner_took_it, "took it (owner)", "(owner)", ()),
    ("notice-link", _with(_you_type, _link), "▲ ends in 4:12", "ends in 4:12", ()),
    ("notice-idle", _with(_you_type, _idle), "▲ ends in 2d 23h", "ends in 2d 23h", ()),
    ("data-path-lost", _with(_you_type, _data_path), "input refused", "input refused", ()),
    ("refused-not-typist", _with(_someone_else_types, _refused("not_typist")), "input refused", "refused", ()),
    ("unreachable", _with(_you_type, _refused("unreachable")), "◌ unreachable · input refused", "input refused", ()),
    ("stale-someone-else", _with(_owner_view_only, _stale), "▲ stale · input refused", "input refused", ()),
    ("stale-holder", _with(_you_type, _stale), "▲ stale", "▲ stale", ()),
]
IDS = [s[0] for s in STATES]


# The two states whose whole row fits 65 columns with the name on it, so nothing has to leave.
NAME_FITS_AT_65 = {"you-type", "someone-else-types"}


def _recording(state) -> bool:
    return "● REC" in _row(state, 200)


def _dangling(row: str) -> list[str]:
    """Every `·` must join two words: one space each side and a word beyond each."""
    bad = []
    for m in re.finditer("·", row):
        before, after = row[:m.start()], row[m.end():]
        if not (before.endswith(" ") and after.startswith(" ") and not before.endswith("  ")
                and not after.startswith("  ") and before.strip() and after.strip()):
            bad.append(row[max(m.start() - 8, 0):m.end() + 8])
    return bad


@pytest.mark.parametrize(("state_id", "make", "fact", "always", "stays"), STATES, ids=IDS)
def test_at_65_columns_the_key_fact_rec_and_menu_stay_and_the_session_name_goes(
        state_id, make, fact, always, stays) -> None:
    """The phone row (65 columns) is the one that was cut mechanically: `ends…`, "input refused", the owner's
    typing warning and `● REC` were lost to the session name that came before them. The state's key fact, REC
    and the Menu must be on the row whole, the name must be what gave way, and no `·` may dangle."""
    state = make()
    row = _row(state, 65)
    if fact is not None:  # the shortest wording at the very most; a longer one is fine where there is room
        assert always in row, f"{state_id}: the key fact {fact!r} is not on the row: {row!r}"
    if _recording(state):
        assert "● REC" in row, f"{state_id}: REC is not on the row: {row!r}"
    assert "≡ Menu ^\\" in row, row
    if state_id not in NAME_FITS_AT_65:
        assert SESSION not in row, f"{state_id}: the name is on a row that had to give something up: {row!r}"
    assert not _dangling(row), f"{state_id}: a dangling separator in {row!r}"
    assert "…" not in row, f"{state_id}: a phrase was cut with an ellipsis: {row!r}"
    pill = re.match(r"‹(you type|view only|nobody types)›", row)
    assert pill, f"{state_id}: the pill is not first: {row!r}"


@pytest.mark.parametrize(("state_id", "make", "fact", "always", "stays"), STATES, ids=IDS)
def test_the_session_name_is_the_first_thing_to_leave_as_the_window_narrows(state_id, make, fact, always, stays) -> None:
    """The survival order is a promise about every width, not just a phone's: wherever the session name is still
    on the row, so are the pill, the key fact, REC and the rest of the words; once it is gone it does not come
    back as the window narrows further; and no width ever shows a dangling separator."""
    state = make()
    name_gone = False
    for width in range(200, 39, -1):
        row = _row(state, width)
        assert not _dangling(row), f"{state_id} at {width}: {row!r}"
        assert "≡ Menu ^\\" in row, f"{state_id} at {width}: {row!r}"
        has_name = SESSION in row
        if has_name:
            assert not name_gone, f"{state_id}: the name came back at {width}: {row!r}"
            assert ("● REC" in row or not _recording(state)) and all(words in row for words in stays), (
                f"{state_id} at {width}: {row!r}")
            if always is not None:
                assert always in row, f"{state_id} at {width}: {row!r}"
        else:
            name_gone = True


@pytest.mark.parametrize(("state_id", "make", "fact", "always", "stays"), STATES, ids=IDS)
def test_wide_rows_are_the_full_words_in_the_order_they_always_were(state_id, make, fact, always, stays) -> None:
    """Narrow-window rules must cost a wide window nothing (D43): at 120 and 200 columns the pill, who types, the
    session, the state's whole wording, REC and the Menu are all there, in F12's order, with the whole text of
    the key fact (not a shortened one)."""
    # A stale session's row carries a long fact on top of who types and the warning, and is full at 200 (the
    # notice itself is the banner), so 120 columns is a narrow row for it.
    for width in ((200,) if state_id.startswith("stale") else (120, 200)):
        row = _row(make(), width)
        assert row.index("≡ Menu") > row.index("● REC")
        assert SESSION in row, row
        assert "…" not in row.replace("End…", ""), row
        assert not _dangling(row)


WIDE = {
    "you-type": "‹ you type ›  rtr-core-01  ● live  ● REC",
    "owner-view-only": "‹ view only ›  fixture-peer types · you own it: typing takes it · rtr-core-01  ● live  ● REC",
    "someone-else-types": "‹ view only ›  fixture-user types · rtr-core-01  ● live  ● REC",
    "read-only-right": "‹ view only ›  fixture-user types · your right is read-only · rtr-core-01  ● live  ● REC",
    "nobody-types": "‹ nobody types ›  fixture-user put the keyboard aside · rtr-core-01  ● live  ● REC",
    "owner-took-it": "‹ view only ›  fixture-user took the keyboard (owner) · rtr-core-01  ● live  ● REC",
    "refused-not-typist": "‹ view only ›  fixture-user types · rtr-core-01  your input was refused: you do not hold the keyboard  ● REC",
    "notice-link": "‹ you type ›  rtr-core-01  ▲ not answering · ends in 4:12 unless it answers  ● REC",
    "notice-idle": "‹ you type ›  rtr-core-01  ▲ no bytes either way · ends in 2d 23h · any byte keeps it  ● REC",
    "data-path-lost": "‹ you type ›  rtr-core-01  ◐ restoring the data path · input refused for now  ● REC",
    "unreachable": "‹ you type ›  rtr-core-01  ◌ unreachable · TSB cannot reach its worker · input refused · not ended  ● REC",
}


@pytest.mark.parametrize(("state_id", "make", "fact", "always", "stays"), [s for s in STATES if s[0] in WIDE], ids=list(WIDE))
def test_the_row_at_120_and_200_columns_is_unchanged(state_id, make, fact, always, stays) -> None:
    """Exact text, as the row read before the survival order existed: the left part of the row at 120 and at 200
    columns is the full wording of every phrase, so a change to the narrow rules that touches a wide window is
    caught here and not by an operator."""
    for width in (120, 200):
        assert _row(make(), width).startswith(WIDE[state_id]), (state_id, width)


def test_the_narrowest_rows_keep_the_pill_and_the_menu_whole() -> None:
    """D43: when even the pill, the key fact and REC cannot all fit, the Menu is still the last to go and the
    words that leave are whole phrases: the row is never a half word."""
    state = _with(_you_type, _link)()
    for width in (30, 36, 40, 45):
        row = _row(state, width)
        assert row.endswith("≡ Menu ^\\") and row.startswith("‹you type›"), row
        assert "…" not in row and not _dangling(row), row


def test_a_65_column_row_stale_lost_its_data_path_and_ending_keeps_the_countdown_rec_and_menu() -> None:
    """Qodo review finding 1: with every state warning on the same rank, a 65-column row kept the stale warning
    and dropped the countdown's value and `● REC`; the countdown and REC outrank the other state warnings, so
    they are what stays while the warnings give way."""
    state = _with(_you_type, _stale, _data_path, _link)()
    row = _row(state, 65)
    assert "ends in 4:12" in row, row
    assert "● REC" in row, row
    assert "≡ Menu ^\\" in row, row
    assert not _dangling(row), row


def test_a_65_column_row_where_a_viewer_s_keys_were_refused_keeps_keys_not_sent() -> None:
    """Qodo review finding 2: the "keys not sent" note shared the who-types phrase's rank and came after it, so
    at 65 columns it was dropped first; it outranks the phrase now (the pill already says who types)."""
    state = _read_only_right()
    row = _row(state, 65, keys_not_sent=True)
    assert "your keys were not sent" in row, row
    assert "≡ Menu ^\\" in row, row
    assert not _dangling(row), row


def test_a_reattach_row_keeps_when_the_next_try_comes_and_drops_the_reason_first() -> None:
    """F12 "reattaching": the operator waiting on a phone needs to know when the next try is, more than why the
    broker closed the view; the reason is what goes, whole, with no dangling `·`."""
    state = _you_type()
    wait = ReattachStatus(1, 60.0, "the broker closed this view; the session lives on")
    wide = _row(state, 120, reattach=wait)
    assert wide.startswith("◐ reattaching · the broker closed this view; the session lives on · try 1 in 60 s")
    narrow = _row(state, 65, reattach=wait)
    assert narrow.startswith("◐ reattaching · try 1 in 60 s") and narrow.endswith("≡ Menu ^\\"), narrow
    assert "broker" not in narrow and not _dangling(narrow)
