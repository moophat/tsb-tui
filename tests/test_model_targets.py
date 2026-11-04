"""The targets model (design F14, E3 phase 11): the query the broker answers, the stale-reply rule, the type a TUI
does not know, and the one function every target string passes before a widget sees it.

Why it matters (Rule 9): the inventory can hold ten thousand targets, so the filter must be the broker's, not a match
over the rows fetched so far; a slow page that lands after a newer query would show targets the operator filtered
out; a connected device's name comes from a CMDB the operator does not control, so a control character in it must
never reach the terminal as one (R17).
"""

from __future__ import annotations

import unicodedata

import pytest

from tsb_tui.model.targets import (
    PAGE_LIMIT,
    Query,
    QueryError,
    TargetsModel,
    as_text,
    make_row,
    parse_query,
)


def _record(target_id: str, **fields) -> dict:
    return {"id": target_id, "type": "ssh", "name": target_id, "group": "lab", "tags": [],
            "discovered_by": "manual", "last_seen": "2026-10-01T00:00:00Z", "host": "192.0.2.1", "port": 22,
            **fields}


class _Client:
    """What `Client.list_targets` receives, recorded: the query reaches the broker as filters, never as a match here."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_targets(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"targets": []}


def test_a_stale_targets_reply_never_overwrites_a_newer_one() -> None:
    """Two requests in flight and the older one answering last: the rows of the newer query stand, as the fleet
    model's rule does for `GET /sessions`. Without it a slow page for an old query replaces the list the operator is
    reading with targets they filtered out."""
    model = TargetsModel()
    older, newer = model.begin(), model.begin()
    wide, narrow = Query(), Query(type="telnet")
    assert model.apply(newer, narrow, {"targets": [_record("t1", type="telnet")]})
    assert not model.apply(older, wide, {"targets": [_record("a"), _record("b"), _record("t1", type="telnet")]})
    assert [row.id for row in model.rows] == ["t1"] and model.query == narrow
    # A next page of a query that is no longer on show is dropped too: rows never mix two queries.
    page = model.begin()
    assert not model.apply(page, wide, {"targets": [_record("z")]}, append=True)
    assert [row.id for row in model.rows] == ["t1"]


def test_the_query_maps_to_the_index_filters() -> None:
    """`type:telnet tag:a tag:b group:g source:s` becomes the four filters of `list_targets`, so the broker
    filters (F14: "the query uses Storage section 2.2's indexed fields"); every tag named must be on the target, so tags
    stay a repeatable list."""
    query = parse_query("type:telnet tag:a tag:b group:g source:s")
    client = _Client()
    client.list_targets(**query.filters(), limit=PAGE_LIMIT)
    assert client.calls == [{"type": "telnet", "tags": ("a", "b"), "group": "g", "discovered_by": "s",
                             "limit": PAGE_LIMIT}]
    assert parse_query("") == Query() and parse_query("   ").filters()["tags"] == ()


@pytest.mark.parametrize("text", ["cisco", "type:ssh core", "host:x", "type:", "type:a type:b", "group:a group:b"])
def test_a_query_the_broker_cannot_answer_is_refused_with_the_fields(text: str) -> None:
    """`GET /targets` has no text search: a bare word is refused with the field list (never matched on the client, which
    would answer a different question than the broker's), and so is an unknown field, an empty value or a repeated
    single-valued field."""
    with pytest.raises(QueryError) as caught:
        parse_query(text)
    assert "type:" in str(caught.value) or "twice" in str(caught.value) or "needs a value" in str(caught.value)
    if text in ("cisco", "type:ssh core", "host:x"):
        assert "tag:" in str(caught.value) and "source:" in str(caught.value)


def test_an_unknown_target_type_is_shown_as_is_and_is_never_an_error() -> None:
    """Conventions V5: a type a later tag adds is opaque to this build. The row reads `rdp`; nothing raises and the
    count still counts it."""
    model = TargetsModel()
    assert model.apply(model.begin(), Query(), {"targets": [_record("pc-1", type="rdp")]})
    assert model.rows[0].type == "rdp" and model.count_label() == "1"


def test_the_count_says_more_while_a_next_page_may_follow() -> None:
    """11.3: one page and the next on demand, so the count is `N+` while `next_cursor` is present and exact once the
    broker has no more; before the first page it says nothing, because 0 would claim an empty inventory."""
    model = TargetsModel()
    assert model.count_label() is None
    model.apply(model.begin(), Query(), {"targets": [_record("a"), _record("b")], "next_cursor": "b"})
    assert model.count_label() == "2+" and model.next_cursor == "b"
    model.apply(model.begin(), Query(), {"targets": [_record("c")]}, append=True)
    assert [row.id for row in model.rows] == ["a", "b", "c"] and model.count_label() == "3"


def test_a_missing_name_is_the_id_and_an_unavailable_inventory_has_no_count() -> None:
    """A record without a name reads as its id (the operator always has something to pick); 503 is a state the tab
    shows, so the count goes rather than claiming an empty inventory."""
    assert make_row({"id": "core-rtr-01", "type": "ssh"}).name == "core-rtr-01"
    model = TargetsModel()
    model.apply(model.begin(), Query(), {"targets": [_record("a")]})
    assert model.mark_unavailable(model.begin()) and model.rows == () and model.count_label() is None


def test_a_reopened_view_drops_a_reply_to_a_request_sent_before_it() -> None:
    """A reopened view starts a new generation: a request still in flight when it opens was sent for the view the
    operator left, so applying it (even the tick a reset is) would put the old query's rows back on screen.
    `reset()` must move the generation past every request already sent, not merely note the last one applied."""
    model = TargetsModel()
    model.apply(model.begin(), Query(), {"targets": [_record("old")]})
    in_flight = model.begin()
    model.reset()
    assert not model.apply(in_flight, Query(), {"targets": [_record("stale")]})
    assert model.rows == () and not model.fetched


def test_as_text_leaves_no_control_character() -> None:
    """R17: Rich keeps ESC in `Text.plain`, so markup off is not enough. Every C0 and C1 control is replaced (ESC by its
    control picture, the Unicode U+2400 block), while printable text, markup brackets and non-ASCII letters pass
    unchanged: the operator still reads `[bold red]rich[/bold red]` exactly as the CMDB wrote it."""
    for code in [*range(0x20), 0x7F, *range(0x80, 0xA0)]:
        shown = as_text(f"a{chr(code)}b")
        assert not any(unicodedata.category(ch) == "Cc" for ch in shown), hex(code)
        assert shown.startswith("a") and shown.endswith("b") and len(shown) == 3
    assert as_text("\x1b[31mred\x1b[0m") == "␛[31mred␛[0m"
    assert as_text("\x07") == "␇" and as_text("\x7f") == "␡" and as_text("\x9b") == "�"
    for plain in ("[bold red]rich[/bold red]", "Tên máy · 路由器", "core-rtr-01"):
        assert as_text(plain) == plain
    assert as_text(None) == "" and as_text(22) == "22"

