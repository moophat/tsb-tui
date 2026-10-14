"""Discoverability: every way in is on screen and acts on one click (design §3.4 test 5; plan phase 10).

Why it matters (Rule 9): the TUI takes exactly one key (Ctrl-\\), and everything else is a letter after it, a click, or
the Menu. A person on a phone has no key to fall back on, and an agent has no eyes, so a way in that is only text, a
hint that needs a second click, or an action that exists in the table but not in the Menu is a feature somebody cannot
reach. These tests hold the three window sizes of the mockups (65x30, 120x40, 200x60): `≡` and `▦▸` on the rail and
`≡ Menu` in the status row are on screen, each acts on one click, the Menu lists every action of the table, and an
agent driving the TUI reaches every action that is enabled in a seeded state by `click` alone, seeing the effect.
Servers: the contract's in-process fixture server (DT7), recording what each client sends.
"""

from __future__ import annotations

import asyncio

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, attached, make_session, other_client
from stub_server import create_owner_session
from textual.command import CommandList, CommandPalette
from textual.css.query import NoMatches
from tsb_tui.app import TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.chrome_dialogs import ConfirmEndScreen, TextScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.prompts import AnswerScreen
from tsb_tui.screens.size_dialog import SizeDialog
from tsb_tui.screens.whos_here import WhosHereScreen
from tsb_tui.widgets.rail import ACT_FLEET, ACT_MENU, Rail
from tsb_tui.widgets.status_row import StatusRow
from tsbc import _serve
from workspace_support import rail_row

SIZES = ((65, 30), (120, 40), (200, 60))
PROMPT_ID = "d" * 32
# Every action a person can reach from a terminal, which is every one the table scopes to the session but the Menu
# itself, read from the table and not from the Menu's own filter: an action that drops out of the Menu must not take
# its own test with it. The dialogs' buttons (Take it, Hand to selected, Ask…) are the dialogs'.
SESSION_ACTIONS = tuple(a for a in A.ACTIONS if a.scope == A.SCOPE_SESSION and a.id != "menu")


def _menu_texts(palette: CommandPalette) -> list[str]:
    try:
        options = palette.query_one(CommandList).options
    except NoMatches:
        return []
    return [str(getattr(option.prompt, "plain", option.prompt)) for option in options]


def test_the_menu_and_the_fleet_are_on_screen_and_one_click_away_at_each_size(serve, screen_text, wait_until) -> None:
    """D5, D11, design §3.4 test 5. At 65x30, 120x40 and 200x60 the rail shows `≡` and `▦▸` in its first two rows and the
    status row shows `≡ Menu`; a click on each does what it says, with no key pressed and nothing sent to the session.
    The words are read from the screen the person sees, not from a widget's model, so a glyph that was drawn off the
    screen, or hidden behind another widget, fails here."""

    async def at(width: int, height: int) -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, height)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            screen.close_sidebar()
            await wait_until(pilot, lambda: screen.status.hit_x("menu") is not None, "the row to fill")
            lines = screen_text(app).splitlines()
            assert len(lines) == height, f"the screen is {len(lines)} lines tall at {height}"
            assert "≡" in lines[0][:4], f"no ≡ on the rail at {width}x{height}: {lines[0]!r}"
            assert "▦▸" in lines[1][:4], f"no ▦▸ on the rail at {width}x{height}: {lines[1]!r}"
            assert "≡ Menu" in lines[-1], f"no ≡ Menu in the status row at {width}x{height}: {lines[-1]!r}"

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_MENU)))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), f"≡ at {width}")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")

            await pilot.click(Rail, offset=(1, rail_row(screen, ACT_FLEET)))
            await wait_until(pilot, lambda: screen.sidebar_open, f"▦▸ at {width}")
            screen.close_sidebar()
            await wait_until(pilot, lambda: not screen.sidebar_open, "the sidebar to close")

            await pilot.click(StatusRow, offset=(screen.status.hit_x("menu") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), f"≡ Menu at {width}")
            assert serve_app.received(screen.pump.state.client_id) == [], "a click reached the session"

    for size in SIZES:
        asyncio.run(at(*size))


def test_the_menu_lists_every_action_of_the_table_at_each_size(serve, wait_until) -> None:
    """D10, design §3.4 test 5. The Menu is the complete list: every action the table offers there is an entry with its
    label at each size, in the table's own order, so no action exists that a person could not find by opening it. An
    action added to the table and left out of the Menu (or the reverse) fails here at whatever size it is dropped."""

    async def at(width: int, height: int) -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, height)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            await wait_until(pilot, lambda: screen.status.hit_x("menu") is not None, "the row to fill")
            await pilot.click(StatusRow, offset=(screen.status.hit_x("menu") + 1, 0))
            want = [action.label for action in SESSION_ACTIONS]
            await wait_until(pilot, lambda: len(_menu_texts(app.screen)) >= len(want), f"the Menu at {width}")
            got = [text.splitlines()[0].strip() for text in _menu_texts(app.screen)]
            assert sorted(got) == sorted(want), f"at {width}x{height} the Menu lists {got}, the table says {want}"
            assert got == [a.label for a in A.menu_actions()], "the Menu is out of the table's own order"

    for size in SIZES:
        asyncio.run(at(*size))


@pytest.mark.parametrize("action_id", ["who", "size", "event_log", "settings", "keys", "about", "end"])
def test_closing_a_dialog_the_menu_opened_gives_the_status_row_its_hints_back(
        action_id, serve, wait_until) -> None:
    """D11, D14. While a dialog has the keyboard the status row says `keys →` and lists the dialog's own keys; when it
    closes the row must go back to the hints of the session, or a person who closed the Event log is left with a row
    that offers nothing (no `≡ Menu`, no `Detach`) and no way to know why. Every dialog the Menu opens registers
    itself with the workspace once and leaves once; the Event log, Settings, All keys and About registered twice and
    left once, so the row stayed on `keys →` for good."""

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            await screen.run_action_id(action_id)
            await wait_until(pilot, lambda: app.screen is not screen and screen.dialogs
                             and "keys →" in screen.status.text, f"{action_id} to open")
            await pilot.pause(0.1)
            assert len(screen.dialogs) == 1, f"{action_id} registered {len(screen.dialogs)} times"
            assert "keys →" in screen.status.text
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs
                             and "keys →" not in screen.status.text, f"{action_id} to give the row back")
            assert "≡ Menu" in screen.status.text

    asyncio.run(check())


def _quiet_prompt() -> dict:
    return {"t": "quiet_prompt", "v": 1, "prompt_id": PROMPT_ID, "code": "quiet_prompt.kill",
            "text": "Kill the session?"}


async def _settle(rig: Rig, predicate, what: str) -> None:
    """Wait, bounded, until `predicate()` holds: a dialog or a toggle shows a few loop turns after its click."""
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


async def _click_menu_entry(rig: Rig, label: str) -> None:
    """What an agent does to run an action from the Menu: click `≡ Menu`, then click the entry. The Menu is a list a
    window may be too short to show whole, so an entry below the fold is brought into view with the list's own scroll
    (what the wheel does) until its label is on the screen; the click itself is the driver's `click`."""
    await _settle(rig, lambda: rig.app.screen is rig.app.workspace() and "≡ Menu" in rig.app.screen.status.text,
                  "the status row to offer the Menu again")
    assert (await rig.cmd("click", label="≡ Menu"))["ok"]
    await _settle(rig, lambda: isinstance(rig.app.screen, CommandPalette)
                  and len(_menu_texts(rig.app.screen)) >= len(SESSION_ACTIONS), "the Menu to fill")
    options = rig.app.screen.query_one(CommandList)
    for _ in range(40):
        if any(line.strip().startswith(label) for line in (await rig.cmd("snapshot"))["text"].splitlines()):
            break
        options.scroll_down(animate=False)
        await rig.pilot.pause()
    else:
        raise AssertionError(f"the Menu never showed {label!r}")
    assert (await rig.cmd("click", label=label))["ok"]


@pytest.mark.parametrize(("width", "height"), SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_an_agent_reaches_every_enabled_action_by_click_alone(serve, tmp_path, wait_until, server_frame_validator,
                                                              width, height) -> None:
    """Design §3.4 test 5, success criteria. The seeded state: two sessions open, the one in front held by another client
    of the owner's own login (so Take back is offered) and asking a quiet-prompt question (so Answer is). In it every
    action of the table is enabled, and an agent clicks `≡ Menu` and then the entry for each one, with no key pressed,
    and sees what it does: the dialog, the sidebar, the view, the next session, the keyboard coming back, the end of
    the TUI. The expected effect of each is written down here, so an action that was added to the table but cannot be
    reached by a click, or that a click reaches and nothing happens, fails at the size it fails."""
    prompt = _quiet_prompt()
    server_frame_validator(prompt)

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp(after_attach=(prompt,)))
        first = make_session(url, _serve.USER, 80, 24)
        second = make_session(url, _serve.USER, 80, 24)
        with other_client(url, _serve.USER, first):
            async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("input.guard=true",),
                           size=(width, height)) as rig:
                await rig.login(_serve.USER)
                await rig.attach(first)
                await rig.attach(second)
                assert (await rig.cmd("switch", session=first))["ok"]
                app, screen = rig.app, rig.app.workspace()
                await _settle(rig, lambda: screen.pump.state.quiet_prompt is not None, "the quiet prompt")
                client_id = screen.pump.state.client_id
                facts = screen.facts()
                enabled = [a.id for a in SESSION_ACTIONS if A.blocked(a.id, facts) is None]
                assert enabled == [a.id for a in SESSION_ACTIONS], (
                    f"the seeded state must enable every action; blocked: "
                    f"{[(a.id, A.blocked(a.id, facts)) for a in SESSION_ACTIONS if A.blocked(a.id, facts)]}")

                def front() -> str | None:
                    return screen.front.session_id if screen.front is not None else None

                async def dialog(kind: type, label: str) -> None:
                    await _click_menu_entry(rig, label)
                    await _settle(rig, lambda: isinstance(app.screen, kind), f"{label} to open")
                    app.screen.dismiss(None)
                    await _settle(rig, lambda: app.screen is screen and not screen.dialogs, f"{label} to close")

                async def text_dialog(label: str, title: str) -> None:
                    await _click_menu_entry(rig, label)
                    await _settle(rig, lambda: isinstance(app.screen, TextScreen), f"{label} to open")
                    assert title in (await rig.cmd("snapshot"))["text"], f"{label} did not show {title!r}"
                    app.screen.dismiss(None)
                    await _settle(rig, lambda: app.screen is screen and not screen.dialogs, f"{label} to close")

                reached: list[str] = []

                await dialog(WhosHereScreen, A.BY_ID["who"].label)
                reached.append("who")
                await dialog(SizeDialog, A.BY_ID["size"].label)
                reached.append("size")
                before = screen.auto_size_on
                await _click_menu_entry(rig, A.BY_ID["auto_size"].label)
                await _settle(rig, lambda: screen.auto_size_on != before, "auto size to flip")
                reached.append("auto_size")
                await dialog(AnswerScreen, A.BY_ID["answer"].label)
                reached.append("answer")
                screen.close_sidebar()  # login opened it with no session; Fleet sidebar is a toggle
                await _settle(rig, lambda: not screen.sidebar_open, "the sidebar to close")
                await _click_menu_entry(rig, A.BY_ID["fleet"].label)
                await _settle(rig, lambda: screen.sidebar_open, "the sidebar to open")
                screen.close_sidebar()
                await _settle(rig, lambda: not screen.sidebar_open, "the sidebar to close")
                reached.append("fleet")
                await _click_menu_entry(rig, A.BY_ID["full_view"].label)
                await _settle(rig, lambda: screen.view_open, "the full view to open")
                screen.close_fleet_view()
                reached.append("full_view")
                await dialog(NewSessionScreen, A.BY_ID["new_session"].label)
                reached.append("new_session")
                await _click_menu_entry(rig, A.BY_ID["switch"].label)
                await _settle(rig, lambda: screen.sidebar_open, "the sidebar to open for Switch to…")
                screen.close_sidebar()
                reached.append("switch")
                await text_dialog(A.BY_ID["event_log"].label, "Event log")
                reached.append("event_log")
                await _click_menu_entry(rig, A.BY_ID["pan"].label)
                await _settle(rig, lambda: screen._pan_mode, "Pan to start")
                screen._end_pan()
                reached.append("pan")
                await text_dialog(A.BY_ID["settings"].label, "Settings")
                reached.append("settings")
                await text_dialog(A.BY_ID["keys"].label, "All keys")
                reached.append("keys")
                await text_dialog(A.BY_ID["about"].label, "About tsb-tui")
                reached.append("about")

                assert not screen.pump.state.holds_keyboard, "another client of the owner's holds the keyboard"
                await _click_menu_entry(rig, A.BY_ID["take_back"].label)
                await _settle(rig, lambda: screen.pump.state.holds_keyboard, "the keyboard to come back")
                reached.append("take_back")

                await _click_menu_entry(rig, A.BY_ID["next"].label)
                await _settle(rig, lambda: front() == second, "Next open session to bring the other session forward")
                reached.append("next")
                await _click_menu_entry(rig, A.BY_ID["prev"].label)
                await _settle(rig, lambda: front() == first, "Previous open session to come back")
                reached.append("prev")

                await dialog(ConfirmEndScreen, A.BY_ID["end"].label)
                assert all(s["state"] != "killed" for s in serve_app.sessions.values()), "End… ended it unasked"
                reached.append("end")
                await _click_menu_entry(rig, A.BY_ID["detach"].label)
                await _settle(rig, lambda: screen.slot_for(first) is None, "Detach to leave the session")
                reached.append("detach")
                await _click_menu_entry(rig, A.BY_ID["quit"].label)
                await _settle(rig, lambda: app._exit, "Quit to end the TUI")  # Textual's own flag: no public one
                reached.append("quit")

                never_clicked = {a.id for a in SESSION_ACTIONS} - set(reached)
                assert not never_clicked, f"actions the table has and this test never clicked: {never_clicked}"
                assert serve_app.received(client_id) == [], "a click reached the session as input"

    asyncio.run(check())
