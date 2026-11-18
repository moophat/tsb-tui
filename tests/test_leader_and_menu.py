"""The leader and the Menu (design §2.4; D10, D15, D16, D17).

Why these matter (Rule 9): a terminal is only useful if every key reaches it. A router's `?`, an
editor's Esc and a shell's Ctrl-C are the whole job, and TSB takes exactly one key, Ctrl-\\, which a
router never expects (D17). Everything else the chrome does is reached by that one key and a letter,
by a click, or from the Menu, which lists every action so no key needs remembering (D40). A leader that
swallowed a letter, a Menu that missed an action, or a doubled Ctrl-\\ that sent nothing would each
leave the operator with a screen they cannot drive. Servers: the contract's in-process fixture server,
recording what each client sends (`input_server.py`, DT7).
"""

from __future__ import annotations

import asyncio

import pytest
from input_server import InputRecordingApp, attached, flushed
from stub_server import create_owner_session
from textual.command import CommandList, CommandPalette, SearchIcon
from textual.content import Content
from textual.css.query import NoMatches
from tsbc import _serve
from workspace_support import rail_row

from tsb_tui.app import MenuProvider, TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.size_dialog import SizeDialog
from tsb_tui.screens.terminal import TerminalScreen
from tsb_tui.screens.whos_here import WhosHereScreen
from tsb_tui.widgets.rail import ACT_MENU, Rail

LEADER = "ctrl+backslash"
# The pinned environment (conftest) holds the Menu's timer at 10 s, so `^\ <letter>` never races it;
# the tests that want the Menu from the leader alone set a short one.
MENU_AFTER = "TSB_TUI_MENU_OPEN_ON_LEADER_AFTER_MS"


def _env(monkeypatch, **extra: str) -> None:
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


async def _open(pilot, app, serve_app, wait_until):
    session_id = create_owner_session(app, _serve.TARGET, 80, 24)
    app.permissions = frozenset(app.client.me().get("permissions", ()))
    screen = await attached(pilot, app, session_id, wait_until)
    return screen, screen.pump.state.client_id


def _detached(screen: TerminalScreen) -> bool:
    """Detach left the only session: the workspace is empty and shows the sidebar (design §2.5)."""
    return not screen.slots and screen.sidebar_open


def _menu_texts(palette: CommandPalette) -> list[str]:
    """The Menu's entries as the operator reads them (name, then its help line), or [] before the list exists."""
    try:
        options = palette.query_one(CommandList).options
    except NoMatches:
        return []
    return [str(getattr(option.prompt, "plain", option.prompt)) for option in options]


def test_leader_then_a_letter_runs_the_action_and_sends_nothing_to_the_session(
        serve, monkeypatch, wait_until) -> None:
    """D15: `^\\ d` detaches. The leader and its letter are the operator's, so neither the 0x1c nor the
    `d` may reach the target (a `d` typed into a router's CLI is a real keystroke), and `^\\ w` opens
    Who's here the same way."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)

            await pilot.press(LEADER, "w")
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), "Who's here to open")
            app.screen.dismiss(None)
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")

            await pilot.press(LEADER, "d")
            await wait_until(pilot, lambda: _detached(screen), "Detach to leave")
            assert serve_app.received(client_id) == []

    asyncio.run(_check())


def test_the_leader_alone_opens_the_menu_immediately(serve, monkeypatch, wait_until) -> None:
    """The menu opens immediately even with a configured legacy delay; Esc returns to the session."""
    _env(monkeypatch, **{MENU_AFTER: "1500"})

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await pilot.press(LEADER)
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen and "keys →" not in screen.status.text,
                             "the Menu to close")
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client_id) == []

    asyncio.run(_check())


def test_a_doubled_leader_sends_exactly_one_0x1c_and_opens_nothing(
        serve, monkeypatch, wait_until) -> None:
    """D15: `^\\ ^\\` is how the operator sends Ctrl-\\ (SIGQUIT) to the session on purpose. Exactly one
    byte 0x1c, no Menu, and the leader is not left pending for the next key."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await pilot.press(LEADER, LEADER)
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"\x1c"],
                             "exactly 0x1c to reach the server")
            await pilot.press("x")  # an ordinary key right after: the leader is not pending
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"\x1c", b"x"],
                             "the next key to be an ordinary one")
            assert app.screen is screen, "nothing opened"

    asyncio.run(_check())


def test_the_leader_works_while_the_attach_is_still_starting_and_other_keys_go_to_the_app(
        serve, monkeypatch, wait_until) -> None:
    """D15, D40: the status row offers `^\\ d Detach` while attaching (an attach to a slow or dead worker
    must be cancellable), so the leader has to work before there is a session. A key that is not the
    leader has nowhere to go yet and is left to the app (the next test). The attach is held by replacing
    the worker that makes it."""
    _env(monkeypatch)

    async def never_attaches(self, slot, cols: int, rows: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(TerminalScreen, "_attach_and_pump", never_attaches)

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: screen.status is not None and "attaching" in screen.status.text,
                             "the row to say it is attaching")
            assert screen.pump is None and "Ctrl+\\ d Detach" in screen.status.text

            await pilot.press(LEADER, "d")
            await wait_until(pilot, lambda: _detached(screen), "Detach to leave")

    asyncio.run(_check())


def test_ctrl_q_still_quits_while_the_attach_is_starting(serve, monkeypatch, wait_until) -> None:
    """The leader is the only key the screen takes before there is a session; every other key is left to
    the app, so an operator stuck on a slow attach can still quit with the app's own key."""
    _env(monkeypatch)

    async def never_attaches(self, slot, cols: int, rows: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(TerminalScreen, "_attach_and_pump", never_attaches)

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            app.push_screen(TerminalScreen(session_id))
            screen = app.screen
            await wait_until(pilot, lambda: screen.status is not None and "attaching" in screen.status.text,
                             "the row to say it is attaching")
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: app._exit, "the app to quit")

    asyncio.run(_check())


# Every key except Ctrl-\ (D17), with the bytes a terminal sends for it: the printable keys a router's
# CLI uses (`?`, `|`, `:`, `/`), the leader-map letters themselves typed without the leader, Esc, Tab,
# the F-keys, and Ctrl keys including readline's, tmux's and Textual's own reserved ones.
NEVER_TAKEN = [
    ("a", b"a"), ("?", b"?"), ("|", b"|"), (":", b":"), ("/", b"/"),
    ("d", b"d"), ("w", b"w"), ("r", b"r"), ("R", b"R"), ("f", b"f"), ("x", b"x"), ("c", b"c"),
    ("escape", b"\x1b"), ("tab", b"\t"), ("f9", b"\x1b[20~"),
    ("ctrl+a", b"\x01"), ("ctrl+b", b"\x02"), ("ctrl+d", b"\x04"), ("ctrl+p", b"\x10"),
    ("ctrl+q", b"\x11"), ("ctrl+z", b"\x1a"), ("ctrl+right_square_bracket", b"\x1d"),
]


def test_every_key_but_the_leader_reaches_the_session(serve, monkeypatch, wait_until) -> None:
    """D17, design §3.4 test 1 ("every accelerator checked to reach the session when it does not follow
    the leader"): printable keys (including the letters the leader uses), Esc, Tab, F-keys and every Ctrl
    key but Ctrl-\\ are sent, in order, as the bytes the session's terminal would see."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            login = app.screen
            await pilot.press("f1")
            await pilot.pause()
            assert app.screen is login
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            for key, _ in NEVER_TAKEN:
                await pilot.press(key)
            wanted = [data for _, data in NEVER_TAKEN]
            await wait_until(pilot, lambda: serve_app.received(client_id) == wanted,
                             f"every key to reach the server, got {serve_app.received(client_id)!r}")
            assert app.screen is screen, "a key opened something it must not"
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "F1 Menu")
            assert serve_app.received(client_id) == wanted
            await pilot.press("escape", LEADER, "L")
            from tsb_tui.screens.lock import LockScreen

            await wait_until(pilot, lambda: isinstance(app.screen, LockScreen), "lock")
            locked = app.screen
            await pilot.press("f1")
            await pilot.pause()
            assert app.screen is locked
            assert serve_app.received(client_id) == wanted

    asyncio.run(_check())


def test_a_key_after_the_leader_without_a_binding_stays_in_the_menu(
        serve, monkeypatch, wait_until) -> None:
    """An unbound key neither runs an action nor enters the session."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            _screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await pilot.press(LEADER, "z")
            await pilot.pause()
            assert serve_app.received(client_id) == []
            assert isinstance(app.screen, CommandPalette)

    asyncio.run(_check())


def test_the_menu_lists_every_action_of_the_table_with_its_key_and_is_searchable(
        serve, monkeypatch, wait_until) -> None:
    """Design §3.4 test 5, D10: every action in the table appears in the Menu (but the Menu itself and
    the dialogs' own buttons), each with its description and key; typing narrows the list and Enter runs
    the match. Ctrl-P stays the session's: the Menu opens only from the leader, `≡ Menu` or its click."""
    _env(monkeypatch, **{MENU_AFTER: "100"})

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            _screen, _client_id = await _open(pilot, app, serve_app, wait_until)
            await pilot.press("ctrl+p")  # the session's key, never the Menu's
            assert not CommandPalette.is_open(app)

            await pilot.press(LEADER)
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
            palette = app.screen
            wanted = [a.label for a in A.menu_actions()]
            await wait_until(pilot, lambda: len(_menu_texts(palette)) == len(wanted),
                             f"the Menu to list {len(wanted)} actions")
            listed = " ".join(_menu_texts(palette))
            for action in A.menu_actions():
                assert action.label in listed, f"{action.id} is not in the Menu"
                option = next(o for o in palette.query_one(CommandList).options if o.hit.text == action.label)
                assert option.disabled or action.description[:8] in option.prompt.plain
            leader = A.leader_label(LEADER)
            assert f"{leader} w" in listed and f"{leader} d" in listed

            await pilot.press("/", *"size")
            await wait_until(pilot, lambda: _menu_texts(palette) and "Size…" in _menu_texts(palette)[0]
                             and len(_menu_texts(palette)) < len(wanted), "the Menu to narrow to Size")
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, SizeDialog),
                             "Size to open from the Menu")

    asyncio.run(_check())


def test_menu_search_off_keeps_the_whole_list_whatever_is_typed(serve, monkeypatch, wait_until) -> None:
    """`menu.search`: the knob is read. Off, the Menu is the complete list and a query narrows nothing;
    on, a query that matches no action returns none. (The provider is asked directly: the palette
    feeds it every keystroke, and "nothing narrowed" is not something a test can wait for.)"""
    _env(monkeypatch, TSB_TUI_MENU_SEARCH="false")

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, _ = await _open(pilot, app, _serve_app, wait_until)
            provider = MenuProvider(screen)
            wanted = len(A.menu_actions())
            assert len([hit async for hit in provider.search("qqqq")]) == wanted
            app.cfg.set_runtime("menu.search", True)
            assert [hit async for hit in provider.search("qqqq")] == []

    asyncio.run(_check())


def test_keys_indicator_off_keeps_the_pill_while_a_dialog_has_the_keys(
        serve, monkeypatch, wait_until) -> None:
    """`status.keys_indicator` (D14): off, `keys → …` never replaces the pill, whatever has the keys."""
    _env(monkeypatch, TSB_TUI_STATUS_KEYS_INDICATOR="false")

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            screen, _ = await _open(pilot, app, _serve_app, wait_until)
            await pilot.press(LEADER, "w")
            await wait_until(pilot, lambda: isinstance(app.screen, WhosHereScreen), "Who's here to open")
            await wait_until(pilot, lambda: "Esc close" in screen.status.text, "the row to show the dialog's hints")
            assert screen.status.text.startswith("‹ you type ›") and "keys →" not in screen.status.text

    asyncio.run(_check())


def test_the_menu_runs_an_action_that_is_available_and_says_why_one_is_not(
        serve, monkeypatch, wait_until) -> None:
    """D40: the Menu is the complete list, so an action that cannot run now is still listed, says so, and
    explains itself when chosen (a toast), instead of vanishing. Here: Who's here before the attach has
    nothing to show."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, _ = await _open(pilot, app, serve_app, wait_until)
            screen.pump.state.client_id = None  # as before the first `attached`
            await pilot.press(LEADER, "w")
            await wait_until(pilot, lambda: any("not attached" in n.message for n in app._notifications._notifications.values()),
                             "the toast saying why Who's here is not available")
            assert app.screen is screen

    asyncio.run(_check())


def test_the_menu_names_a_key_only_where_there_is_one_mutes_what_is_unavailable_and_searches_with_a_slash(
        serve, monkeypatch, wait_until) -> None:
    """Mockup F3. `^\\` alone opens the Menu, so printing it as the key of Settings… or About sends the operator to
    press the key they are already pressing; an action that cannot run now must look unlike one that can (it is listed
    so the Menu is complete, D40, and its reason is kept), in the theme's muted colour; and the search icon is a `/`,
    which every font has, not an emoji that a terminal font without it draws as a hole."""
    _env(monkeypatch, **{MENU_AFTER: "100"})

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, _ = await _open(pilot, app, serve_app, wait_until)
            screen.pump.state.client_id = None  # as before the first `attached`: Who's here has nothing to show
            muted = app.cfg["theme.text_muted"]
            hits = {hit.text: hit async for hit in MenuProvider(screen).discover()}
            entries = screen.menu_entries()
            assert any(why is not None for _, _, why in entries) and any(not keys for _, keys, _ in entries)
            for action, keys, why in entries:
                hit = hits[action.label]
                if keys:
                    assert keys in hit.help, f"{action.id} lost its key"
                else:
                    assert "Ctrl+\\" not in hit.help, f"{action.id} has no key but its help names one"  # fails today
                styled = [span for span in hit.display.spans if span.style == muted]
                assert bool(styled) is (why is not None), f"{action.id}: muted exactly when it is unavailable"
                if why is not None:
                    assert f"not available: {why}" in Content.from_markup(hit.help).plain

            await pilot.press(LEADER)
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
            # The palette composes its children after it opens; wait for the icon rather than query at once.
            await wait_until(pilot, lambda: [i.icon for i in app.screen.query(SearchIcon)] == ["/"],
                             "the search icon to be the ASCII slash")  # fails today: Textual's emoji

    asyncio.run(_check())


def test_keys_leader_map_rebinds_a_letter_and_the_old_letter_runs_nothing(
        serve, monkeypatch, wait_until) -> None:
    """Rebinding Detach to q leaves d unbound and the menu open, while q detaches."""
    _env(monkeypatch, TSB_TUI_KEYS_LEADER_MAP_DETACH="q")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(200, 24)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await wait_until(pilot, lambda: "Ctrl+\\ q Detach" in screen.status.text, "the row to print the new letter")
            assert "Ctrl+\\ d Detach" not in screen.status.text

            await pilot.press(LEADER, "d")
            await pilot.pause()
            assert serve_app.received(client_id) == []
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("q")
            await wait_until(pilot, lambda: _detached(screen), "q to detach")

    asyncio.run(_check())


def test_keys_leader_chooses_the_key_and_the_row_prints_it(serve, monkeypatch, wait_until) -> None:
    """`keys.leader`: the key and the label the row prints are one knob. Ctrl-] as the leader prints `^]`,
    runs `^] d`, and Ctrl-\\ is then an ordinary key that reaches the session (D17: only the leader is taken)."""
    _env(monkeypatch, TSB_TUI_KEYS_LEADER="ctrl+right_square_bracket")

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 24)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await wait_until(pilot, lambda: "≡ Menu ^]" in screen.status.text, "the row to print the leader")

            await pilot.press(LEADER)
            await wait_until(pilot, lambda: serve_app.received(client_id) == [b"\x1c"],
                             "Ctrl-\\ to reach the session")
            await pilot.press("ctrl+right_square_bracket", "d")
            await wait_until(pilot, lambda: _detached(screen), "^] d to detach")

    asyncio.run(_check())


@pytest.mark.parametrize("width", [65, 120, 200])
def test_every_menu_action_is_reachable_by_a_click_alone(
        serve, monkeypatch, wait_until, screen_text, width) -> None:
    """Design §3.4 test 5, success criteria: at 65, 120 and 200 columns the Menu opens from a click on
    `≡ Menu`, and an entry runs from a click on it, with no key pressed: here Who's here and then, from the
    Menu again, Size."""
    _env(monkeypatch)

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 40)) as pilot:
            screen, client_id = await _open(pilot, app, serve_app, wait_until)
            await wait_until(pilot, lambda: screen.status.hit_x("scroll") is not None, "the row to fill")

            for action_id, expected in (("who", WhosHereScreen), ("size", SizeDialog)):
                await pilot.click(Rail, offset=(1, rail_row(screen, ACT_MENU)))
                await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
                await wait_until(pilot, lambda: len(_menu_texts(app.screen)) == len(A.menu_actions()),
                                 "the Menu to fill")
                # Find the entry's name on the screen, as a person would, and click there.
                label = A.BY_ID[action_id].label
                rows = screen_text(app).splitlines()
                y = next(i for i, row in enumerate(rows) if label in row)
                await pilot.click(offset=(rows[y].index(label) + 2, y))
                await wait_until(pilot, lambda expected=expected: isinstance(app.screen, expected),
                                 f"{action_id} to open from a click in the Menu")
                app.screen.dismiss(None)
                await wait_until(pilot, lambda: app.screen is screen and "keys →" not in screen.status.text,
                                 "the dialog to close")
            assert serve_app.received(client_id) == [], "a click reached the session"

    asyncio.run(_check())
