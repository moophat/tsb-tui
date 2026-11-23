"""The Targets tab of the full Fleet view (design F14, E3 phase 11): the inventory the core lists for this login,
every page loaded in the background, the filter matched locally over the loaded rows, every target string shown as
text, and `⏎` opening New session on the target.

Why it matters: rule 1 (2026-11-21) makes the filter local free text, so `GET /targets` carries `limit` and `cursor`
only and a typed `field:value` is matched literally, never parsed; rule 2 (2026-11-21) loads every page after the
login, so a match beyond the first page shows without any key paging. A CMDB name carrying markup or an escape must
reach the screen as characters (R17), because a terminal takes an escape for a command; and a telnet target opened
with `reuse` is a 502 the operator cannot explain (R19). Server: the contract's in-process fixture server
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
from tsb_tui.screens.fleet_view import GROUP_TARGETS, TAB_TARGETS, FleetView
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



async def _open_targets(app: TsbTuiApp, pilot, wait_until, rows: int | None = None, timeout_s: float = 10.0):
    screen = await fixture_login(app, pilot)
    screen.open_fleet_view(TAB_TARGETS)
    view = screen.fleet_view
    await wait_until(pilot, lambda: len(view.layout.rows) >= (rows or len(_serve.TARGETS))
                     and view.layout.rows[0][0] == "targets", "the targets to be listed", timeout_s)
    return screen, view


async def _select(pilot, view, target_id: str) -> None:
    for _ in range(len(view.layout.rows) + 1):
        if view.selected == ("targets", target_id):
            return
        await pilot.press("down")
    raise AssertionError(f"{target_id} could not be selected: {view.selected}")





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


def test_the_filter_matches_locally_and_asks_the_broker_nothing(serve, wait_until) -> None:
    """Rule 1 (2026-11-21): the typed text is matched locally against the loaded rows, case-insensitive; the title
    carries the filter's form. `GET /targets` carries `limit` and `cursor` only, so a `field:value` text is plain
    text that asks the broker nothing and earns no refusal — the structured query is retired."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            before = len(_gets(serve_app))
            await pilot.press("slash", *"telnet")
            await wait_until(pilot, lambda: [row[1] for row in view.layout.rows] == ["telnet-scenario"],
                             "the filter's survivors")
            # rule 6 + rule 15 (2026-11-22, ruling 19): the filter's single survivor is the table head, so it is
            # selected and the index is its place (1); the matches, the total and the typed text show.
            assert "Targets · 1/1 of 8 · 'telnet'" in view.text, view.text
            assert len(_gets(serve_app)) == before, "a typed filter asked the broker for nothing"
            for _ in range(6):
                await pilot.press("backspace")
            await pilot.press(*("colon" if char == ":" else char for char in "type:rdp"))
            await pilot.pause(0.2)
            assert len(_gets(serve_app)) == before, "a field:value text is matched literally, never parsed"
            assert "not a target type" not in view.text, "the structured query's refusal is gone"
            assert view.layout.rows == (), view.layout.rows

    asyncio.run(check())


def test_a_bare_word_filters_locally_and_asks_nothing(serve, wait_until) -> None:
    """Rule 1 (2026-11-21): a word with no `field:` is matched as text against the loaded rows, never refused, and
    asks the broker nothing. A word that matches a group shows every target of that group."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            before = len(_gets(serve_app))
            await pilot.press("slash", *"cisco")
            await pilot.pause(0.2)
            assert "write field:value" not in view.text, "the refusal of a bare word is retired"
            assert len(_gets(serve_app)) == before, "a bare word asked the broker nothing"
            assert view.layout.rows == (), view.layout.rows
            for _ in range(5):
                await pilot.press("backspace")
            await pilot.press(*"lab")
            await pilot.pause(0.2)
            ids = [row[1] for row in view.layout.rows]
            assert ids == ["fixture-nocred", "fixture-target", "rich-name"], ids
            assert "of 8 · 'lab'" in view.text, view.text

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


def test_every_page_is_loaded_after_the_view_opens(serve, wait_until) -> None:
    """Rule 2 (2026-11-21): every published page is fetched in the background after the view opens, following
    `next_cursor` until none; no key asks a page, and once the last page landed the count reads the whole list."""

    async def check() -> None:
        serve_app, url = serve()
        for index in range(600):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until, rows=608, timeout_s=60)
            pages = _gets(serve_app)
            assert all("limit=500" in path for path in pages), pages
            assert "cursor=" not in pages[0] and any("cursor=" in path for path in pages[1:]), pages
            ids = [row[1] for row in view.layout.rows]
            assert len(ids) == 608 and len(set(ids)) == 608, "a page arrived twice or none"
            assert ids == sorted(ids), "the rows are the broker's id order"
            assert "Targets 608" in view.text, view.text

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
        headers = ("NAME", "TYPE", "GROUP", "TAGS", "SOURCE", "SESSIONS", "YOU CAN")
        async with app.run_test(size=(120, 36)) as pilot:
            _screen, view = await _open_targets(app, pilot, wait_until)
            # The header row lays out after the rows are listed; read the columns only once it is laid out, so a
            # not-yet-laid-out header cannot drop a column the test then reads as missing.
            await wait_until(pilot, lambda: all(h in view.text for h in headers),
                             "the table's header row laid out", 30.0)
            wide = view.text
            for header in ("NAME", "TYPE", "GROUP", "TAGS", "SOURCE", "SESSIONS", "YOU CAN"):
                assert header in wide, header
        async with TsbTuiApp(url).run_test(size=(44, 30)) as pilot:
            narrow_app = pilot.app
            _screen, view = await _open_targets(narrow_app, pilot, wait_until)
            # In the narrow view the header row lays out with NAME and TYPE kept; read once it is laid out.
            await wait_until(pilot, lambda: "NAME" in view.text and "TYPE" in view.text,
                             "the narrow table's header row laid out", 30.0)
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


def test_a_poll_asks_limit_and_cursor_only_and_a_503_shows_the_state(serve, wait_until) -> None:
    """Rule 1 (2026-11-21): every `GET /targets` the tab asks carries `limit` and `cursor` only, never a filter
    parameter, and a poll never runs while a page is in flight. A 503 `inventory_unavailable` is the state the tab
    shows; the next newest request answers it and the state goes."""

    async def check() -> None:
        serve_app, url = serve()
        for index in range(600):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        gate, asked = _gated_pages(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            screen._stop_targets_timer()
            before = len(asked)
            screen._fetch_targets(background=True)  # the tick while the chain's page is in flight
            await pilot.pause(0.05)
            assert len(asked) == before, "a poll ran while a page was in flight"
            gate.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "the chain to finish", 40)
            paths = _gets(serve_app)
            assert all("limit=500" in path and "type=" not in path and "tag=" not in path
                       and "group=" not in path and "source=" not in path for path in paths), paths

            scripted = _ScriptedTargets(app)
            scripted.fail[0] = ApiError(503, "inventory_unavailable", "the inventory is not answering")
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: screen.targets.unavailable, "the 503")
            screen._fetch_targets(background=True)
            await wait_until(pilot, lambda: not screen.targets.unavailable and len(screen.targets.rows) == 608,
                             "the poll after the 503 answered", 40)

    asyncio.run(check())


def _gated_pages(served):
    """`served._list_targets` with every cursor page held until the gate opens, so a test lands an old page's reply
    after a newer request. The call runs in `call_api`'s own worker thread, so the UI keeps running while it is held."""
    gate = threading.Event()
    original = served._list_targets
    asked: list[dict] = []

    def gated(query):
        asked.append(dict(query))
        if query.get("cursor") and not gate.is_set():
            gate.wait(timeout=10)
        return original(query)

    served._list_targets = gated
    return gate, asked


def test_a_stale_reply_never_overwrites_the_newer_list(serve, wait_until) -> None:
    """Rule 2 (2026-11-21): a reply applies only when it answers the newest request. A page held in flight, a refresh
    that supersedes it, and the old page landing last must leave the list the newest answer built — never folded
    back, never doubled."""

    async def check() -> None:
        served, url = serve()
        for index in range(600):
            served.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                   "name": f"bulk {index}"}
        gate, asked = _gated_pages(served)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            screen._stop_targets_timer()  # no ticks behind the test: the refresh is the one this test names
            screen._fetch_targets()  # a refresh while the chain's page is in flight supersedes it
            # Wait for the refresh's own first-page request to be recorded (the requests after the view opened),
            # not for the last one to read as a first page: the refresh chains a page right after, so `asked[-1]`
            # flips to a cursor and the old predicate never holds. Waiting on what it reads keeps it deterministic.
            await wait_until(pilot, lambda: len(asked) >= 3 and any(not q.get("cursor") for q in asked[2:]),
                             "the refresh's request", 40)
            gate.set()  # the older page lands after the newest answer
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "the chain to finish", 40)
            ids = [row.id for row in screen.targets.rows]
            assert len(ids) == len(set(ids)), "an old page's rows doubled the list"

    asyncio.run(check())


def test_a_reopened_view_never_appends_an_old_pages_rows(serve, wait_until) -> None:
    """Rule 2 (2026-11-21): a page request carries its generation. The view reopened while an old page is in flight
    applies only its own chain: the old reply never appends its rows to the reopened list."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(600):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        gate, asked = _gated_pages(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            screen._stop_targets_timer()
            screen.close_fleet_view()
            screen.open_fleet_view(TAB_TARGETS)  # reopened: the model resets and asks its own first page
            await wait_until(pilot, lambda: len(asked) >= 3 and any(not q.get("cursor") for q in asked[2:]),
                             "the reopened view's first page", 40)
            gate.set()  # the first view's page lands after the reopened view asked for its own
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "the reopened view's own chain", 40)
            ids = [row.id for row in screen.targets.rows]
            assert len(ids) == len(set(ids)), "a page arrived twice"

    asyncio.run(check())


def test_a_poll_never_folds_the_loaded_pages_back(serve, wait_until) -> None:
    """Rule 2 (2026-11-21): a refresh re-reads from the first page and chains every page behind it; a reply older
    than the newest request never shrinks what the chain loaded. Both halves: (1) a tick while the chain's page is
    in flight asks nothing (rule 12: no overlapping passes), measured from a deterministic start once the chained
    page's request is recorded in `asked`; (2) with every page loaded, a poll re-reads afresh and the list never
    folds back."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(600):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        gate, asked = _gated_pages(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 36)) as pilot:
            screen, _view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            screen._stop_targets_timer()
            # First half: a tick while the chain's page is in flight asks nothing (rule 12). Deterministic start:
            # wait until the last request recorded in `asked` is the chained page the gate holds (its cursor present),
            # so a foreground chain is in flight and no superseding first page is pending; then the tick, a brief
            # pause, and nothing new must have been asked.
            await wait_until(pilot, lambda: asked and asked[-1].get("cursor"), "the chained page's request", 40)
            before = len(asked)
            screen._fetch_targets(background=True)  # the tick while the chain's page is in flight
            await pilot.pause(0.05)
            assert len(asked) == before, "a poll ran while a page was in flight"
            # Second half: release the gate, finish the chain, then a poll with every page loaded re-reads afresh.
            gate.set()
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "the chain to finish", 40)
            before = len(asked)
            screen._fetch_targets(background=True)  # a poll with every page loaded: it re-reads afresh
            await wait_until(pilot, lambda: len(asked) >= before + 2, "the poll's chain")
            await wait_until(pilot, lambda: len(screen.targets.rows) == 608 and not screen.targets.next_cursor,
                             "the poll's chain to finish", 40)
            assert len(screen.targets.rows) == 608, "a refresh folded the loaded pages back"

    asyncio.run(check())


def test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive(serve, wait_until) -> None:
    """Rule 2 (2026-11-21): every page loads in the background, so a click on the last row loaded selects it and the
    chain brings the rest; no key, click or reach-to-the-last-row asks for a page."""
    async def check() -> None:
        serve_app, url = serve()
        for index in range(600):
            serve_app.targets[f"bulk-{index:03d}"] = {**_serve.TARGETS["normal"], "id": f"bulk-{index:03d}",
                                                      "name": f"bulk {index}"}
        gate, asked = _gated_pages(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 140)) as pilot:
            screen, view = await _open_targets(app, pilot, wait_until, rows=500, timeout_s=40)
            y = max(index for index, line in enumerate(view.layout.lines) if line.group == GROUP_TARGETS)
            last = view.layout.lines[y].session_id
            before = len(asked)
            await pilot.click(FleetView, offset=(5, y))
            assert view.selected == ("targets", last), (view.selected, last)
            assert len(asked) == before, "a click asked for a page: the chain is the only pager"
            gate.set()
            await wait_until(pilot, lambda: len(view.layout.rows) == 608 and not screen.targets.next_cursor,
                             "the chain to finish", 40)

    asyncio.run(check())
