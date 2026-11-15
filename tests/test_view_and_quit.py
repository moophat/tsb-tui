"""View, Settings and help, Quit and End session… (design §2.4, D28, D32, D38, D40; session-end-by-tsb).

Why it matters (Rule 9): "help is never a prerequisite" (D40) fails if All keys misses an action, or lists a key the
action does not have; Settings… that cannot say where a value came from sends an operator hunting through four layers
of configuration; a Quit that ends a session would turn leaving a terminal into killing a router's session, and one that
does not say which sessions it left running leaves the operator guessing; End session… that does not ask first, or does
not name what it ends, is one stray click from ending someone else's work. Servers: the contract's in-process fixture
server (DT7), which refuses `DELETE` from a login that is not the owner exactly as the core does (403).
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from pathlib import Path

import tsb_contract_client
from conftest import REPO_ROOT
from input_server import InputRecordingApp
from tsbc import _serve
from workspace_support import (
    fixture_login,
    named_sessions,
    open_here,
    select_in_sidebar,
)

from tsb_tui import config
from tsb_tui.app import MenuPalette, TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.chrome_dialogs import (
    ENGINE_PIN_COMMIT,
    ConfirmEndScreen,
    EventLogScreen,
    TextScreen,
    keys_lines,
    settings_lines,
)

LEADER = "ctrl+backslash"
PIN = REPO_ROOT / "vendor" / "libghostty-vt" / "PIN"


def _pinned_commit() -> str:
    return next(line.split(":", 1)[1].strip() for line in PIN.read_text().splitlines()
                if line.startswith("upstream_commit:"))


def test_the_engine_commit_about_names_is_the_one_pin_records() -> None:
    """DT2, plan phase 8: About names `PIN`'s engine commit. The shared object itself reports `0.1.0-dev` and no commit
    (`test_term_abi.py`), and `PIN` is outside the installed package, so the commit is carried as a constant here and
    this test is what keeps it equal to `PIN`: a pin bump that moves one and not the other fails here, not in an
    About screen that names an engine the TUI is not running."""
    assert ENGINE_PIN_COMMIT == _pinned_commit()


def _cfg(**overrides) -> config.Config:
    return config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true",
                                                                               **overrides})


def _body(app) -> str:
    return "\n".join(str(w.render()) for w in app.screen.query("#text-body"))


def _deletes(serve_app) -> list[str]:
    return [r[1] for r in serve_app.requests if r[0] == "DELETE"]


def _left_message(app) -> str:
    return "\n".join(getattr(r, "plain", str(r)) for r in app._exit_renderables)


# -- All keys, About, Settings --


def test_all_keys_lists_every_actions_key_from_the_table_and_follows_a_rebinding() -> None:
    """D40: the list is written from the one action table, so an action with a key is in it with that key, the
    letter after the leader is the one `keys.leader_map` says, and Quit's `:q` is there (design §2.4)."""
    text = "\n".join(keys_lines(_cfg()))
    for action in A.ACTIONS:
        if action.scope != A.SCOPE_SESSION or action.key is None:
            continue
        assert f"^\\ {action.key}" in text and action.label in text, f"{action.id} is missing from All keys"
    assert "^\\ :q" in text and "^\\ ?" in text and "^\\ F" in text and "^\\ x" in text
    for scope_key in ("t", "h", "e"):  # Who's here's own keys
        assert f"  {scope_key} " in text
    rebound = "\n".join(keys_lines(_cfg(**{"keys.leader_map.detach": "D"})))
    detach = [line for line in rebound.splitlines() if "Detach" in line]
    assert detach and detach[0].strip().startswith("^\\ D"), detach


def test_all_keys_lists_the_theme_picker_keys_under_its_own_scope() -> None:
    """D40: a dialog's own keys are listed under its scope, as Who's here's and Size's are. The picker's
    `⏎` Use and `Esc` put back must show under "(while it is open)", so an operator who cannot see them
    on the panel finds them in help.

    fails today: `DIALOG_SCOPES` has no theme scope, so the picker's keys are in All keys nowhere."""
    lines = "\n".join(keys_lines(_cfg())).splitlines()
    headings = [line for line in lines if line.endswith("(while it is open)")]
    assert any("Theme picker" in line for line in headings), f"the picker's keys are not listed: {headings}"
    i = next(i for i, line in enumerate(lines) if "Theme picker" in line)
    section = "\n".join(lines[i + 1:i + 4])  # three rows now: `⏎`, `s`, `Esc`
    assert "⏎" in section and "Use" in section, f"the picker's `⏎` is not listed: {section!r}"
    assert "Esc" in section and "put back" in section, f"the picker's `Esc` is not listed: {section!r}"

def test_all_keys_about_and_settings_open_from_the_table_and_say_what_they_are(serve, wait_until) -> None:
    """D40: Settings…, All keys and About are actions of the table (the Menu lists them), each a dialog over the
    workspace; About names the TUI, the contract client and the engine's pinned commit (DT2, PIN)."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            for action_id in ("settings", "keys", "about"):
                await screen.run_action_id(action_id)
                await wait_until(pilot, lambda: isinstance(app.screen, TextScreen)
                                 and app.screen.query("#text-body") and _body(app).strip(), f"{action_id} to open")
                if action_id == "settings":
                    assert app.screen.query_one(".panel").styles.border_top[1].hex.lower() == app.cfg["theme.frame"]  # fails today: $primary is the accent
                if action_id == "about":
                    text = _body(app)
                    assert _pinned_commit()[:7] in text, f"About does not name the engine's pinned commit: {text}"
                    assert tsb_contract_client.CONTRACT_VERSION in text and "tsb-tui" in text
                if action_id == "keys":
                    assert "^\\ :q" in _body(app)
                await pilot.press("escape")
                await wait_until(pilot, lambda: app.screen is screen, f"{action_id} to close")

    asyncio.run(_check())


def test_settings_says_where_each_value_came_from(tmp_path, monkeypatch) -> None:
    """D30, D40: default, a profile, the config file, the environment, `--set` and a change made during the run are
    each named, so an operator who sees `view.pan_step = 4` knows which layer to edit to change it."""
    file = tmp_path / "config.toml"
    file.write_text('[view]\npan_step = 3\nchrome = "full"\n', encoding="utf-8")
    cfg = config.load(profile="phone", config_file=file,
                      environ={"TSB_TUI_VIEW_PAN_STEP": "4", "TSB_TUI_RENDER_MAX_FPS": "20"},
                      overrides=["toasts.max=2"])
    cfg.set_runtime("size.auto_request", True)
    by_key = {line.split(" = ")[0].strip(): line for line in settings_lines(cfg) if " = " in line}
    assert by_key["theme.name"].endswith("(default)")
    assert by_key["view.follow_cursor"].endswith("(profile phone)"), by_key["view.follow_cursor"]
    assert by_key["view.chrome"].endswith(f"(config file {file})"), "the file wins over the profile"
    assert by_key["view.pan_step"].endswith("(TSB_TUI_VIEW_PAN_STEP)"), "the environment wins over the file"
    assert by_key["toasts.max"].endswith("(--set)")
    assert by_key["size.auto_request"].endswith("(this run)")
    assert settings_lines(cfg)[0].startswith("profile phone")
    assert len(by_key) == len(cfg.as_dict()), "every knob in force is listed"


def test_the_settings_first_line_names_both_orders_of_precedence(tmp_path) -> None:
    # fails today: the first line names only the names order; the roles order is not in settings_lines.
    """Phase 10 step 6 (D30): the first line must name both orders - names "default, profile, saved for your
    login, config file, environment, --set" and roles "default, theme, profile, config file, environment, --set" -
    so an operator who sees a chrome colour they cannot change knows which layer to edit for a role, which is a
    different order from a name."""
    file = tmp_path / "config.toml"
    file.write_text('[theme]\nname = "paper"\n', encoding="utf-8")
    cfg = config.load(profile="default", config_file=file)
    first = settings_lines(cfg)[0]
    assert "names: default, profile, saved for your login, config file, environment, --set" in first, \
        f"the names order: {first!r}"
    assert "roles: default, theme, profile, config file, environment, --set" in first, \
        f"the roles order: {first!r}"


def test_a_view_knob_outside_its_values_fails_start_up_naming_the_key(tmp_path) -> None:
    """DT19, Rule 12: a typo in `fleet_view.group_by`, `fleet_view.tabs`, `mouse.canvas_clicks`, `view.inspector`,
    `motion.level` or `view.pan_step` is loud at start-up, never a knob that quietly does nothing."""
    import pytest

    for key, value in (("fleet_view.group_by", "owner"), ("fleet_view.tabs", "targets"),
                       ("fleet_view.tabs", "sessions,wat"), ("mouse.canvas_clicks", "always"),
                       ("view.inspector", "dock"), ("view.inspector", "float"), ("view.inspector", "off"), ("motion.level", "full"), ("view.pan_step", "0"),
                       ("fleet_view.details_from", "0")):
        with pytest.raises(config.ConfigError) as exc:
            _cfg(**{key: value})
        assert key in str(exc.value), (key, value)
    ok = _cfg(**{"fleet_view.tabs": "sessions,targets,alerts", "fleet_view.group_by": "target"})
    assert ok["fleet_view.group_by"] == "target"


# -- Quit --


def test_quit_leaves_the_sessions_running_and_prints_which_by_the_menu_the_chord_and_ctrl_q_in_a_view(
        serve, wait_until) -> None:
    """D38: Menu ▸ Quit, `^\\ :q` and `Ctrl-Q` in a full view each leave the TUI, send nothing to any session and end
    none (no `DELETE`), and print the sessions that keep running, so the operator knows what is still out there."""

    async def _quit_by(how: str) -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            if how == "menu":
                await screen.run_action_id("quit")
            elif how == "chord":
                await pilot.press(LEADER, "colon", "q")
            else:
                screen.open_fleet_view()
                await wait_until(pilot, lambda: screen.view_open and screen.fleet_view.has_focus, "the view's keys")
                await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: app._exit, f"quit by {how}")
            message = _left_message(app)
            assert "rtr-core-01" in message and "infra-db-02" in message, message
            assert "keep running" in message
            assert _deletes(serve_app) == [], "quitting must never end a session"

    for how in ("menu", "chord", "view"):
        asyncio.run(_quit_by(how))


def test_quit_with_no_session_open_still_says_every_session_keeps_running(serve, wait_until) -> None:
    """D38: the message is never silent: with nothing open it says so, and says the sessions keep running."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            await app.action_quit()
            await wait_until(pilot, lambda: app._exit, "quit")
            assert "No session was open here" in _left_message(app) and "keeps running" in _left_message(app)

    asyncio.run(_check())


def test_an_alert_that_arrives_while_the_tui_is_leaving_draws_nothing(serve, wait_until) -> None:
    """D38: the alert stream runs on a thread of its own and hands each event to the UI loop, so one can land while the
    screens are coming down; drawing it on a popup stack that is being unmounted raised `MountError` in the thread
    (seen as an unhandled thread exception in the suite). Once the TUI is leaving nothing listens any more."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            heard: list[int] = []
            app.watch_alerts(lambda: heard.append(1))
            app.alerts_changed()
            assert heard == [1], "before leaving, a listener hears a change"
            await app.action_quit()
            await wait_until(pilot, lambda: app._exit, "quit")
            app.alerts_changed()
            assert heard == [1], "after leaving, nothing is called"

    asyncio.run(_check())


def test_a_colon_then_a_letter_that_is_not_a_command_goes_nowhere_and_ctrl_q_still_belongs_to_the_session(
        serve, wait_until) -> None:
    """An unknown chord leaves the menu open; Esc returns keys to the session."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            state = screen.pump.state
            await pilot.press(LEADER, "colon")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "the menu to open")
            await pilot.press("z")
            assert isinstance(app.screen, MenuPalette)
            assert "no shortcut" in str(app.screen.query_one("#menu-key-message").render())
            await pilot.press("escape")
            assert app.screen is screen
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: serve_app.received(state.client_id) == [b"\x11"],
                             "Ctrl-Q to reach the session, and nothing else")
            assert not app._exit

    asyncio.run(_check())


# -- End session… --


def test_end_session_asks_first_naming_the_session_then_sends_delete(serve, wait_until) -> None:
    """session-end-by-tsb step 1: from the Menu's action, a sidebar row (`x`) and a view row (`x`), the operator is
    asked once, with the session named; Esc or Keep it sends nothing; End it sends `DELETE /sessions/{id}`."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first)

            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-text"), "the question")
            asked = str(app.screen.query_one("#end-text").render())
            assert asked.startswith("End rtr-core-01 · s.") and "everyone" in asked, asked
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Esc to cancel")
            assert _deletes(serve_app) == []

            await select_in_sidebar(app, pilot, wait_until, second)
            await pilot.press("x")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-text"), "the question from a row")
            assert "infra-db-02" in str(app.screen.query_one("#end-text").render())
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "Esc to cancel")
            assert _deletes(serve_app) == []

            screen.open_fleet_view()
            view = screen.fleet_view
            await wait_until(pilot, lambda: any(r[1] == second for r in view.layout.rows), "the view's rows")
            while view.selected_session != second:
                await pilot.press("j")
            await pilot.press("x")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-confirm"), "the question from the view")
            await pilot.click("#end-confirm")
            await wait_until(pilot, lambda: _deletes(serve_app) == [f"/sessions/{second}"], "DELETE")
            await wait_until(pilot, lambda: any("You ended infra-db-02" in n.message
                                                for n in app._notifications._notifications.values()),
                             "the toast")

    asyncio.run(_check())


def test_end_session_on_a_session_the_login_does_not_own_says_the_core_refused(serve, wait_until) -> None:
    """session-end-by-tsb step 1: a login that can see a session but is neither its owner nor an admin gets 403, and the
    session lives on. The TUI does not guess beforehand (the core is the authority) and shows the refusal plainly."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (theirs,) = named_sessions(url, serve_app, "fw-edge-01", user=_serve.PEER)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            screen.fleet.filter_mode = "all"
            await wait_until(pilot, lambda: theirs in screen.fleet.records, "the poll to list it")
            screen.end_session(theirs)
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-confirm"), "the question")
            await pilot.click("#end-confirm")
            await wait_until(pilot, lambda: any("Only the owner of fw-edge-01" in n.message
                                                for n in app._notifications._notifications.values()),
                             "the refusal to be shown")
            assert serve_app.sessions[theirs]["state"] != "killed"

    asyncio.run(_check())


def test_end_dialog_x_key_ends_the_session(serve, wait_until) -> None:
    """tsb-core #440: pressing `x` in the End dialog dismisses with True and sends DELETE, exactly as clicking
    [ End it ] does. Before the fix ConfirmEndScreen had no BINDINGS beyond Esc, so pressing `x` sent the
    letter to the session through the workspace instead of dismissing the dialog with True."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-confirm"), "the End dialog")
            await pilot.press("x")
            await wait_until(pilot, lambda: _deletes(serve_app) == [f"/sessions/{sid}"],
                             "DELETE after x")

    asyncio.run(_check())


def test_end_dialog_k_key_keeps_the_session(serve, wait_until) -> None:
    """tsb-core #440: pressing `k` in the End dialog dismisses with False and sends nothing, exactly as
    clicking [ Keep it ] does. Before the fix ConfirmEndScreen had no BINDINGS beyond Esc, so pressing `k`
    sent the letter to the session instead of dismissing the dialog with False."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and app.screen.query("#end-keep"), "the End dialog")
            await pilot.press("k")
            await wait_until(pilot, lambda: app.screen is screen, "dialog to close after k")
            assert _deletes(serve_app) == [], "k must not send DELETE"

    asyncio.run(_check())


def test_end_dialog_keys_line_names_x_k_and_esc(serve, wait_until) -> None:
    """tsb-core #440: while the End dialog is open the status row shows `keys → confirm` and names x, k and Esc,
    so a person with no mouse knows every key available. Before the fix ConfirmEndScreen had no status_target
    override beyond _Dialog's default (Esc only), so x and k were absent from the keys line."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, _serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await screen.run_action_id("end")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmEndScreen)
                             and "keys → confirm" in screen.status.text, "the End dialog")
            keys_line = screen.status.text
            assert "x End it" in keys_line, f"x not in keys line: {keys_line!r}"
            assert "k Keep it" in keys_line, f"k not in keys line: {keys_line!r}"
            assert "Esc" in keys_line, f"Esc not in keys line: {keys_line!r}"
            # x comes before k (AnswerScreen order: End it, Keep it, Esc)
            assert keys_line.index("x End it") < keys_line.index("k Keep it"), \
                f"x should precede k: {keys_line!r}"

    asyncio.run(_check())


# -- Pan --


def test_pan_moves_the_window_by_pan_step_until_esc_and_any_other_key_is_the_sessions(serve, monkeypatch,
                                                                                    wait_until) -> None:
    """D4, D28, `view.pan_step`: `^\\ v` hands the arrow keys to the window over a grid bigger than the canvas; each
    moves it `view.pan_step` cells and the status row says so; Esc ends it. Any other key ends Pan and is the session's,
    never swallowed (D17)."""

    async def _check() -> None:
        monkeypatch.setenv("TSB_TUI_VIEW_PAN_STEP", "5")
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01", cols=150, rows=60)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            state = screen.pump.state
            canvas = screen.canvas
            await wait_until(pilot, lambda: canvas.backend.cols == 150, "the grid at the session's size")
            canvas.pan_to(0, 0)
            await pilot.press(LEADER, "v")
            await wait_until(pilot, lambda: "keys → pan" in screen.status.text, "Pan to take the keys")
            await pilot.press("right", "right", "down")
            await wait_until(pilot, lambda: (canvas.view_x, canvas.view_y) == (10, 5), "the arrows to pan")
            assert "cols 11-" in screen.status.text, "the status row says which columns are shown"
            await pilot.press("left")
            await wait_until(pilot, lambda: canvas.view_x == 5, "← to pan back")
            await pilot.press("escape")
            await wait_until(pilot, lambda: "keys → pan" not in screen.status.text, "Esc to end Pan")
            await pilot.press("right")  # an arrow now is the session's, not Pan's: it goes out to the session
            await wait_until(pilot, lambda: len(serve_app.received(state.client_id)) == 1, "the arrow to reach the session")

            await pilot.press(LEADER, "v")
            await wait_until(pilot, lambda: "keys → pan" in screen.status.text, "Pan again")
            await pilot.press("x")
            await wait_until(pilot, lambda: b"x" in serve_app.received(state.client_id), "x to reach the session")
            assert "keys → pan" not in screen.status.text

    asyncio.run(_check())


# -- the event log --


def test_the_event_log_lists_what_this_client_was_told_and_folds_bursts_of_output(serve, wait_until) -> None:
    """D22, D32: the client-side event log of the session in front, oldest first, each line with its time and what the
    frame said; a burst of output is one line, so the lines that matter (`attached`, `prefix`, `token`) are not buried."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            screen.close_sidebar()
            state = screen.pump.state
            for key in "abcd":  # the fixture echoes a holder's input as output: one chunk each
                await pilot.press(key)
            await wait_until(pilot, lambda: len(state.log.of_kind("output")) >= 3, "the echoes to arrive")
            chunks = len(state.log.of_kind("output"))
            await screen.run_action_id("event_log")
            await wait_until(pilot, lambda: isinstance(app.screen, EventLogScreen)
                             and app.screen.query("#text-body"), "the event log")
            body = _body(app)
            kinds = [line.split()[1] for line in body.splitlines() if line.strip()]
            assert "attached" in kinds and "prefix" in kinds, body
            assert kinds.index("attached") < kinds.index("prefix"), "oldest first"
            assert not any(a == b == "output" for a, b in pairwise(kinds)), \
                "two output lines in a row: a burst was not folded"
            folded = [line for line in body.splitlines() if " output " in line]
            assert len(folded) < chunks, f"{chunks} output chunks were not folded: {body}"
            assert sum(int(line.split("output")[1].split()[0]) for line in folded) == chunks, body

    asyncio.run(_check())


# -- motion --


def test_motion_level_is_textuals_animation_level(serve, monkeypatch, wait_until) -> None:
    """D37, `motion.level`: nothing animates on the canvas or the rail, and "none" also stops Textual's own animation
    (the drive and harness profiles set it, so a headless run never waits on a tween)."""

    async def _level(value: str | None) -> str:
        if value is not None:
            monkeypatch.setenv("TSB_TUI_MOTION_LEVEL", value)
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 30)) as pilot:
            await wait_until(pilot, lambda: app.is_running, "the app")
            return app.animation_level

    assert asyncio.run(_level(None)) == "basic"
    assert asyncio.run(_level("none")) == "none"
