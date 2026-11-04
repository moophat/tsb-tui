"""The Targets tab of the full Fleet view (design F14, E3 phase 11): the inventory the core lists for this login,
queried through the broker, with every target string shown as text, and `⏎` opening New session on the target.

Why it matters (Rule 9): the tab is how an operator finds one device among many, so a filter that was matched here
instead of in the broker would answer a different question at 10 000 targets; a CMDB name carrying markup or an escape
must reach the screen as characters (R17), because a terminal takes an escape for a command; and a telnet target
opened with `reuse` is a 502 the operator cannot explain (R19). Server: the contract's in-process fixture server
(`tsbc serve`), whose `TARGETS` hold the telnet record and the one with markup in its name.
"""

from __future__ import annotations

import asyncio
import threading
import unicodedata

from textual.widgets import Button, RadioButton, Static
from tsb_contract_client import ApiError
from tsbc import _serve
from tsbc._serve import ServeApp
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.fleet_view import TAB_TARGETS, FleetView
from tsb_tui.screens.new_session import NewSessionScreen


class _ScriptedTargets:
    """`Client.list_targets` with a chosen call's answer held or refused, so a test can land an older reply after a
    newer one. The call runs in `call_api`'s own worker thread, so the UI keeps running while it is held."""

    def __init__(self, app: TsbTuiApp) -> None:
        self._real = app.client.list_targets
        self.calls: list[dict] = []
        self.fail: dict[int, Exception] = {}
        self._gates: dict[int, tuple[threading.Event, threading.Event]] = {}
        self._lock = threading.Lock()
        app.client.list_targets = self._list  # type: ignore[method-assign]

    def hold(self, index: int) -> tuple[threading.Event, threading.Event]:
        reached, release = threading.Event(), threading.Event()
        self._gates[index] = (reached, release)
        return reached, release

    def _list(self, **kwargs) -> dict:
        with self._lock:
            index = len(self.calls)
            self.calls.append(kwargs)
            gate, failure = self._gates.get(index), self.fail.get(index)
        if gate is not None:
            reached, release = gate
            reached.set()
            assert release.wait(10), "a held targets reply was never released"
        if failure is not None:
            raise failure
        return self._real(**kwargs)



async def _open_targets(app: TsbTuiApp, pilot, wait_until, rows: int | None = None):
    screen = await fixture_login(app, pilot)
    screen.open_fleet_view(TAB_TARGETS)
    view = screen.fleet_view
    await wait_until(pilot, lambda: len(view.layout.rows) >= (rows or len(_serve.TARGETS))
                     and view.layout.rows[0][0] == "targets", "the targets to be listed")
    return screen, view


async def _select(pilot, view, target_id: str) -> None:
    for _ in range(len(view.layout.rows) + 1):
        if view.selected == ("targets", target_id):
            return
        await pilot.press("down")
    raise AssertionError(f"{target_id} could not be selected: {view.selected}")


async def _type_query(pilot, text: str) -> None:
    await pilot.press("slash", *("colon" if char == ":" else "space" if char == " " else char for char in text),
                      "enter")


def _gets(serve_app) -> list[str]:
    return [path for method, path, _ in serve_app.requests if method == "GET" and path.startswith("/targets")]


def test_the_targets_tab_lists_what_get_targets_answers_ordered_by_id(serve, wait_until) -> None:
    """The tab shows the broker's list, not a list of its own: the rows are `TARGETS` in id order (the broker orders
    by id), `telnet-scenario` reads `telnet`, and the line under the table names the selected target's host and port
    so the operator can tell two devices of one name apart."""

    async def check() -> None:
        _app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            assert [row[1] for row in view.layout.rows] == sorted(_serve.TARGETS)
            text = view.text
            line = next(ln for ln in text.splitlines() if "telnet scenario" in ln)
            assert " telnet " in line and "scenario" in line
            assert "Targets 8" in text, "the tab's label carries the count once the broker has answered"
            await _select(pilot, view, "telnet-scenario")
            details = view.text
            assert "host 192.0.2.30" in details and "port 23" in details and "discovered by manual" in details
            assert "sessions here: none" in details

    asyncio.run(check())


def test_a_target_name_holding_markup_or_an_escape_is_shown_as_text(serve, wait_until) -> None:
    """R17: a CMDB name is not the operator's text. `rich-name`'s `[bold red]rich[/bold red]` appears literally and
    unstyled (markup off), and a name carrying an ESC sequence reaches no segment the screen paints as a control
    (Rich keeps ESC in `Text.plain`, so markup off alone would let it through to the terminal)."""

    async def check() -> None:
        serve_app, url = serve()
        serve_app.targets["zz-escape"] = {
            **_serve.TARGETS["normal"], "id": "zz-escape", "name": "evil\x1b[2J\x1b]0;pwned\x07name",
            "group": "g\x9bx", "tags": ["t\x1b[1m"]}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until, rows=len(_serve.TARGETS) + 1)
            assert "[bold red]rich[/bold red]" in view.text
            segments = [segment for y in range(len(view.layout.lines)) for segment in view.render_line(y)]
            literal = next(seg for seg in segments if "[bold red]rich[/bold red]" in seg.text)
            assert literal.style == view._styles["text"], "the markup in a name must not style anything"
            for segment in segments:
                assert not any(unicodedata.category(ch) == "Cc" for ch in segment.text), repr(segment.text)
            assert "evil␛[2J␛]0;pwned␇name" in view.text

    asyncio.run(check())


def test_the_query_filters_through_the_broker(serve, wait_until) -> None:
    """`/type:telnet` reaches the broker as `GET /targets?type=telnet` and the tab shows what it answered (one row);
    `/type:rdp`, which the broker refuses 422, keeps the previous rows and shows the broker's message in the query
    line, so a typo costs the operator nothing they had."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            await _type_query(pilot, "type:telnet")
            await wait_until(pilot, lambda: [row[1] for row in view.layout.rows] == ["telnet-scenario"],
                             "the broker's filtered list")
            assert any(path.startswith("/targets?") and "type=telnet" in path for path in _gets(serve_app))
            assert "Targets 1" in view.text
            await _type_query(pilot, "x")  # a bare word: refused here, nothing asked of the broker
            before = len(_gets(serve_app))
            await _type_query(pilot, "type:telnet tag:telnet")
            await wait_until(pilot, lambda: len(_gets(serve_app)) > before, "the tagged query to reach the broker")
            assert "tag=telnet" in _gets(serve_app)[-1] and "type=telnet" in _gets(serve_app)[-1]

            # Clear the line and ask for a type the broker refuses.
            await pilot.press("slash")
            for _ in range(len(view.query_text)):
                await pilot.press("backspace")
            await pilot.press("t", "y", "p", "e", "colon", "r", "d", "p", "enter")
            await wait_until(pilot, lambda: "not a target type" in view.text, "the broker's refusal in the query line")
            assert [row[1] for row in view.layout.rows] == ["telnet-scenario"], "the previous rows stay"

    asyncio.run(check())


def test_a_bare_word_is_refused_with_the_field_list_and_asks_nothing(serve, wait_until) -> None:
    """11.2: `GET /targets` has no text search, so a word without `field:` is refused in the query line with the fields
    that exist, and no request is made: matching it here over the rows fetched so far would be a partial answer
    that looks like the broker's."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            before = len(_gets(serve_app))
            await _type_query(pilot, "cisco")
            await wait_until(pilot, lambda: "write field:value" in view.text, "the refusal in the query line")
            assert "type:" in view.text and "source:" in view.text
            assert len(_gets(serve_app)) == before and len(view.layout.rows) == len(_serve.TARGETS)

    asyncio.run(check())


def test_enter_on_a_telnet_target_opens_new_session_with_connection_new(serve, wait_until) -> None:
    """DEFAULT 22: `⏎` on `telnet-scenario` opens New session on it; the dialog names the target and `telnet`, the
    reuse choice is greyed, and Create sends `connection: new` with no `connection_id`, because the worker refuses a
    telnet reuse (a 502 the operator could not explain). The session then opens in the workspace through its gate."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until)
            app.cfg.set_runtime("size.auto_request", True)
            await _select(pilot, view, "telnet-scenario")
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted
                             and bool(app.screen.query("#create-button")), "the New session dialog")
            dialog = app.screen
            assert str(dialog.query_one("#new-session-target", Static).render()) == "telnet-scenario · telnet"
            assert dialog.query_one("#conn-reuse", RadioButton).disabled
            assert "a telnet connection carries one session" in str(dialog.query_one("#connection-note", Static).render())
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: isinstance(app.screen, AttachConfirmScreen), "the attach gate")
            created = [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]
            assert len(created) == 1 and created[0]["target_id"] == "telnet-scenario"
            assert created[0]["connection"] == "new" and "connection_id" not in created[0]
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(screen.slots) == 1 and screen.slots[0].pump is not None
                             and screen.slots[0].pump.state.dump_seen, "the session to open in the workspace")

    asyncio.run(check())


def test_a_next_page_is_asked_when_the_selection_reaches_the_last_row(serve, wait_until) -> None:
    """11.3: one page of a stated `limit`, the next on demand, and the count reads `N+` while the broker has more. The
    broker keeps the filter and the order across pages; the tab only asks for the next page when the operator reaches
    the end of what it has."""

    async def check() -> None:
        serve_app, url = serve()
        for index in range(120):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until, rows=100)
            assert len(view.layout.rows) == 100 and "Targets 100+" in view.text
            assert all("cursor" not in path for path in _gets(serve_app)), "the first page has no cursor"
            await pilot.press("end")
            await wait_until(pilot, lambda: len(view.layout.rows) == 128, "the next page")
            assert "Targets 128" in view.text and "cursor=" in _gets(serve_app)[-1]
            assert [row[1] for row in view.layout.rows] == sorted(row[1] for row in view.layout.rows)

    asyncio.run(check())


class _InventoryDown(ServeApp):
    def _list_targets(self, query):
        return self._err(503, "inventory_unavailable", "the inventory is not answering")


def test_an_unavailable_inventory_is_a_state_the_tab_shows(serve, wait_until) -> None:
    """503 `inventory_unavailable` is the inventory being down, not a bug: the tab says so in words and the view stays
    usable (Esc still goes back), where a crash would take the operator's open sessions' workspace with it."""

    async def check() -> None:
        _app, url = serve(_InventoryDown())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.open_fleet_view(TAB_TARGETS)
            view = screen.fleet_view
            await wait_until(pilot, lambda: "the inventory is unavailable" in view.text, "the unavailable state")
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "Esc to close the view")

    asyncio.run(check())


def test_the_table_loses_columns_in_a_stated_order_and_keeps_name_and_type(serve, wait_until) -> None:
    """F14's seven columns leave from the right as the view narrows (YOU CAN, SOURCE, TAGS, SESSIONS, GROUP) and NAME and
    TYPE stay, so a phone still tells which device and what kind."""

    async def check() -> None:
        _app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            wide = view.text
            for header in ("NAME", "TYPE", "GROUP", "TAGS", "SOURCE", "SESSIONS", "YOU CAN"):
                assert header in wide, header
        async with TsbTuiApp(url).run_test(size=(44, 30)) as pilot:
            narrow_app = pilot.app
            _screen, view = await _open_targets(narrow_app, pilot, wait_until)
            text = view.text
            assert "NAME" in text and "TYPE" in text
            assert "SOURCE" not in text and "YOU CAN" not in text and "TAGS" not in text

    asyncio.run(check())


def test_a_detached_session_is_offered_for_reattach_and_an_ended_one_is_not(serve, wait_until) -> None:
    """11.1: YOU CAN says `attach` wherever this login sees a session that has not ended, a detached one included,
    because a detached session is exactly the one an operator reattaches to. A killed one never earns it, and nothing
    claims `open`, since whether a create works is unknown before it. SESSIONS counts what is not ended."""
    from input_server import make_session, other_client
    from tsb_contract_client import Client

    async def check() -> None:
        serve_app, url = serve()
        live = make_session(url, _serve.USER, target="normal")
        make_session(url, _serve.USER, target="alt-screen")  # created, never attached: detached
        ended = make_session(url, _serve.USER, target="stale-pending")
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        client.close(ended)
        assert serve_app.sessions[ended]["state"] in ("killed", "terminated")
        app = TsbTuiApp(url)
        with other_client(url, _serve.USER, live):
            async with app.run_test(size=(120, 36)) as pilot:
                _screen, view = await _open_targets(app, pilot, wait_until)

                # The row settles last: SESSIONS and YOU CAN come from one render of the same records
                # (`terminal.py:_target_entries`), but as the view narrows the YOU CAN column leaves first
                # (`TARGET_DROP_ORDER`), so a not-yet-settled layout can show the count with no `attach`.
                # Wait for both cells on the row itself, bounded — the test still fails when `attach` never
                # appears, and asserts nothing looser than before.
                def live_row(prefix: str) -> str:
                    return next((ln for ln in view.text.splitlines() if ln.lstrip(" ▸").startswith(prefix)), "")

                def settled(prefix: str, count: str) -> bool:
                    row = live_row(prefix)
                    return count in row and "attach" in row

                await wait_until(pilot, lambda: settled("normal ", "1 * live") or settled("normal ", "1 ● live"),
                                  "the normal row to show its count and `attach` on one settled row")
                await wait_until(pilot, lambda: settled("alt screen", "1 ○") or settled("alt screen", "1 o"),
                                  "the detached row to show its count and `attach` on one settled row")
                lines = view.text.splitlines()

                def row(prefix: str) -> str:
                    return next(ln for ln in lines if ln.lstrip(" ▸").startswith(prefix))

                assert "attach" in row("normal ")
                detached = row("alt screen")
                assert "attach" in detached and ("1 ○" in detached or "1 o" in detached)
                assert "attach" not in row("stale pending") and "open" not in row("stale pending")
                await _select(pilot, view, "normal")
                assert f"s.{live[:6]}" in view.text.splitlines()[-1] and "live" in view.text.splitlines()[-1]

    asyncio.run(check())


def test_the_tab_reads_the_inventory_while_it_is_shown_and_not_after(serve, wait_until, monkeypatch) -> None:
    """The tab fetches on opening and then on `fleet_view.refresh_s` while it is shown, as the Sessions tab polls; once
    the view is closed nothing asks the broker for targets again (a view nobody looks at must not cost the core)."""
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "1")

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(_gets(serve_app)) >= 3, "the tab to read the inventory again", 15)
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "the view to close")
            settled = len(_gets(serve_app))
            await pilot.pause(2.5)
            assert len(_gets(serve_app)) == settled

    asyncio.run(check())


def test_a_poll_asks_the_query_being_fetched_and_a_503_keeps_it(serve, wait_until) -> None:
    """A query entered but not yet answered is the one the tab is fetching: a refresh while it is pending must ask
    that query, never the one still on show, or the poll's reply becomes the newest and the operator's query is
    dropped. A 503 to the new query says the inventory is down but must not put the old query back under the poll."""
    async def check() -> None:
        _app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until)
            scripted = _ScriptedTargets(app)
            reached, release = scripted.hold(0)
            screen._run_query("type:telnet")
            await wait_until(pilot, reached.is_set, "the new query's request")

            screen._fetch_targets(background=True)  # what the timer would do while the query is pending
            await wait_until(pilot, lambda: len(scripted.calls) >= 2, "the poll")
            assert scripted.calls[1]["type"] == "telnet", "a poll ran the query still on show, not the one pending"
            await wait_until(pilot, lambda: [row.id for row in screen.targets.rows] == ["telnet-scenario"],
                             "the poll's page")
            release.set()  # the older reply lands after the newer one
            await pilot.pause(0.1)
            assert [row.id for row in screen.targets.rows] == ["telnet-scenario"]

            scripted.fail[2] = ApiError(503, "inventory_unavailable", "the inventory is not answering")
            screen._run_query("type:ssh")
            await wait_until(pilot, lambda: screen.targets.unavailable, "the 503")
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: len(scripted.calls) >= 4, "the poll after the 503")
            assert scripted.calls[3]["type"] == "ssh", "a 503 must keep the new query as the one polled"

    asyncio.run(check())


def test_an_old_querys_error_never_shows_beside_newer_results(serve, wait_until) -> None:
    """`invalid_request` is the broker's refusal of a query, so it belongs to the query line only while that query is
    the one on show: an older query's refusal landing after a newer query's rows would put a message on screen about
    a list the operator is not looking at."""
    async def check() -> None:
        _app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until)
            scripted = _ScriptedTargets(app)
            reached, release = scripted.hold(0)
            scripted.fail[0] = ApiError(422, "invalid_request", "type rdp is not a target type")
            screen._run_query("type:rdp")
            await wait_until(pilot, reached.is_set, "the refused query's request")
            screen._run_query("type:ssh")
            await wait_until(pilot, lambda: [row.id for row in screen.targets.rows] == sorted(
                row_id for row_id, record in _serve.TARGETS.items() if record["type"] == "ssh"), "the newer query")
            release.set()  # the older query's refusal lands last
            await pilot.pause(0.1)
            assert screen._targets_query_error == "" and "not a target type" not in view.text

    asyncio.run(check())


def test_a_reopened_view_never_duplicates_a_page(serve, wait_until) -> None:
    """A page request carries its generation and cursor: an old page's reply landing after the view was reopened must
    neither append those rows to the new list nor clear the in-flight flag of the page the reopened view is waiting
    for, or that page is asked twice and its rows double."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(120):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until, rows=100)
            scripted = _ScriptedTargets(app)
            old_page, release_old = scripted.hold(0)
            screen._fetch_targets(more=True)
            await wait_until(pilot, old_page.is_set, "the first view's page request")

            screen.close_fleet_view()
            screen.open_fleet_view(TAB_TARGETS)
            await wait_until(pilot, lambda: len(view.layout.rows) == 100 and screen.targets.next_cursor is not None,
                             "the reopened view's first page")
            new_page, release_new = scripted.hold(2)  # call 1 is the reopened view's first page
            screen._fetch_targets(more=True)
            await wait_until(pilot, new_page.is_set, "the reopened view's page request")

            release_old.set()  # the first view's page lands after the reopened view asked for its own
            await pilot.pause(0.1)
            assert len(screen.targets.rows) == 100, "an old page was appended to the reopened list"
            screen._fetch_targets(more=True)  # a page is already in flight: this must not ask for another
            await pilot.pause(0.05)
            assert len(scripted.calls) == 3, "an old page's reply cleared the reopened view's in-flight flag"
            release_new.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 128, "the reopened view's next page")

    asyncio.run(check())


def test_a_poll_never_folds_the_loaded_pages_back(serve, wait_until) -> None:
    """A refresh re-reads only as many rows as the operator has paged through, but it must not run while a page is
    in flight: its request would be the newer one and its shorter answer would replace the page just asked for.
    And a refresh that lands older than a page must not shrink what that page extended."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(250):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=100)
            scripted = _ScriptedTargets(app)
            page_reached, release_page = scripted.hold(0)
            screen._fetch_targets(more=True)
            await wait_until(pilot, page_reached.is_set, "the next page's request")

            screen._fetch_targets(background=True)  # the tick while the page is in flight
            await pilot.pause(0.05)
            assert len(scripted.calls) == 1, "a refresh ran while a page was in flight"
            release_page.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 200, "the page to extend the list")

            refresh = len(scripted.calls)
            refresh_reached, release_refresh = scripted.hold(refresh)
            screen._fetch_targets(background=True)
            await wait_until(pilot, refresh_reached.is_set, "the refresh's request")
            screen._fetch_targets(more=True)  # a page asked for after the refresh was sent: the newer request
            await wait_until(pilot, lambda: len(screen.targets.rows) == 258, "the page after the refresh")
            release_refresh.set()  # the shorter refresh lands last
            await pilot.pause(0.1)
            assert len(screen.targets.rows) == 258, "an older refresh folded the loaded pages back"

    asyncio.run(check())


def test_a_refused_query_keeps_the_shown_list_pageable(serve, wait_until, monkeypatch) -> None:
    """The ruling of 2026-10-22: a 422 `invalid_request` is the broker's answer to one query, and the rows on show
    were fetched with another. The query wanted goes back to the shown one, so the next page and the polls ask for
    what the operator is looking at. Left as the refused query, paging stops (the tab refuses to page while a
    different query is wanted) and every poll re-asks a query the broker already refused, so an operator with 10 000
    targets loses the list they had for one typo."""
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")  # no timer: the test asks each read itself

    async def check() -> None:
        serve_app, url = serve()
        for index in range(120):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until, rows=100)
            assert len(view.layout.rows) == 100 and screen.targets.next_cursor is not None
            first_page = len(_gets(serve_app))

            screen._run_query("type:rdp")  # the broker refuses this type; the rows stay
            await wait_until(pilot, lambda: "not a target type" in view.text, "the refusal in the query line")
            assert len(view.layout.rows) == 100, "a refused query took the rows that were shown"
            assert len(_gets(serve_app)) == first_page + 1

            screen._fetch_targets(more=True)  # the selection is at the last row: ask for the next page
            await wait_until(pilot, lambda: len(view.layout.rows) == 128,
                             "the next page of the query on show")
            page = _gets(serve_app)[-1]
            assert "cursor=" in page and "rdp" not in page, f"the page was asked for the refused query: {page}"

            screen._fetch_targets(background=True)  # what the tab's timer does on its tick
            await wait_until(pilot, lambda: len(_gets(serve_app)) > first_page + 2, "the poll after the refusal")
            poll = _gets(serve_app)[-1]
            assert "rdp" not in poll, f"a poll re-asked the refused query: {poll}"
            assert len(view.layout.rows) == 128, "the rows shown did not survive the refusal"

    asyncio.run(check())


def test_a_click_on_the_last_loaded_target_asks_for_the_next_page(serve, wait_until) -> None:
    """F14's next page is asked when the selection reaches the last target loaded. A mouse has no `↓`, so a click on
    that row must ask it too, or an operator on a touch screen can never see past the first page."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(120):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 140)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until, rows=100)
            last = view.layout.rows[-1][1]
            y = next(index for index, line in enumerate(view.layout.lines) if line.session_id == last)
            await pilot.click(FleetView, offset=(5, y))
            await wait_until(pilot, lambda: len(view.layout.rows) == 128, "a click on the last row to ask for a page")

    asyncio.run(check())
