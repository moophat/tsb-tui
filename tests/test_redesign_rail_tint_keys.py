"""The rail's needs-you count, the side panel's two tints, the leader's letters, the protected detach hint, the ended
row, the single keyboard notice and the Ctrl+] passthrough. Keys only (Pilot keys); each family runs at 80x24 and
160x45 where it renders."""
from __future__ import annotations

import asyncio
import colorsys
import contextlib
import re

import pytest
from input_server import InputRecordingApp, flushed, other_client
from model_frames import state_of
from stub_server import emit_live_output
from test_redesign_keys import login as key_login
from tsbc import _serve
from workspace_support import named_sessions, open_here

from tsb_tui.app import MenuPalette, TsbTuiApp
from tsb_tui.config import load
from tsb_tui.model import actions as A
from tsb_tui.screens.chrome_dialogs import keys_lines
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.theme import contrast_ratio, role_colours
from tsb_tui.theme_files import packaged_dir
from tsb_tui.widgets.rail import ACT_CHIP, ACT_MORE, ACT_NEEDS
from tsb_tui.widgets.status_row import StatusInputs, build_pieces

SIZES = [(80, 24), (160, 45)]
LEADER = "ctrl+backslash"
DETACH = "ctrl+right_square_bracket"
THEMES = sorted(p.stem for p in packaged_dir("themes").glob("*.toml"))
# Every text role the side panel draws.
PANEL_ROLES = ("text", "text_muted", "text_faint", "accent", "steel", "warn", "danger", "rec_list",
               "border_on_surface")


def _hexed(rgb) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


def _painted(app, screen_text, region, y) -> str:
    return screen_text(app).splitlines()[region.y + y][region.x:region.right]


# -- the needs-you count on the rail --


def _expected_row(size, count, ascii_set):
    """The Sessions row the ruling words: `▲1 Sessions`, `▲12 Sessio…`, `▲1`; with none the plain `▸` rows."""
    wide = size[0] > 80
    if count == 0:
        text = "▸ Sessions" if wide else "▸"
    elif wide:
        head = f"▲{count} "
        room = 11 - len(head)
        text = head + ("Sessions" if len("Sessions") <= room else "Sessions"[:room - 1] + "…")
    else:
        text = f"▲{count}"
    if ascii_set:
        text = text.replace("▲", "!").replace("▸", ">")
    return text.ljust(11 if wide else 3)


@pytest.mark.parametrize("ascii_set", [False, True])
@pytest.mark.parametrize("count", [0, 1, 12])
@pytest.mark.parametrize("size", SIZES)
def test_the_sessions_row_leads_with_the_needs_you_count(serve, wait_until, screen_text, monkeypatch, size, count,
                                                         ascii_set):
    if ascii_set:
        monkeypatch.setenv("TSB_TUI_GLYPHS_SET", "ascii")

    async def check():
        serve_app, url = serve(InputRecordingApp())
        broken = named_sessions(url, serve_app, *[f"rtr-{i:02d}" for i in range(count)])
        for sid in broken:
            serve_app.sessions[sid]["state"] = "unreachable"
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            screen._refresh_fleet()
            await wait_until(pilot, lambda: screen.rail._model.need_you == count, f"the rail to count {count}")
            await pilot.pause()
            y = next(i for i, row in enumerate(screen.rail.rows) if row.action == ACT_NEEDS)
            row = screen.rail.rows[y]
            shown = row.text if not ascii_set else _ascii(row.text)
            assert shown == _expected_row(size, count, ascii_set), (size, count, row.text)
            assert len(row.text) == screen.rail.rail_width - 1
            painted = _painted(app, screen_text, screen.rail.region, y)
            assert painted.strip(" │┃") == _expected_row(size, count, ascii_set).strip(), painted
    asyncio.run(check())


def _ascii(text):
    from tsb_tui import glyphs

    return glyphs.render(text, glyphs.ASCII)


# -- the two tints of the side panel --


def _walk(start: str, dark: bool, ratio: float) -> str:
    """The smallest HSL-lightness step from `start`, in the theme's direction, that reaches `ratio` against it."""
    h, light, saturation = colorsys.rgb_to_hls(*(int(start[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    end = 1.0 if dark else 0.0

    def colour(value):
        return "#" + "".join(f"{round(c * 255):02x}" for c in colorsys.hls_to_rgb(h, value, saturation))

    steps = 8192
    previous = light
    for step in range(1, steps + 1):
        current = light + (end - light) * step / steps
        if contrast_ratio(colour(current), start) >= ratio:
            lo, hi = previous, current
            for _ in range(40):
                mid = (lo + hi) / 2
                if contrast_ratio(colour(mid), start) >= ratio:
                    hi = mid
                else:
                    lo = mid
            return colour(hi)
        previous = current
    raise AssertionError("the walk never reached the ratio")


def _section_backgrounds(screen, y_from, y_to):
    """Every (text, foreground, background) of the section's painted cells, bars included."""
    cells = []
    for y in range(y_from, y_to):
        for seg in screen.sidebar.render_line(y):
            if seg.style is None or seg.style.bgcolor is None:
                continue
            fg = seg.style.color.get_truecolor() if seg.style.color is not None else None
            cells.append((seg.text, None if fg is None else _hexed(fg), _hexed(seg.style.bgcolor.get_truecolor())))
    return cells


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", THEMES)
def test_targets_keep_the_surface_and_sessions_walk_one_step_further(serve, wait_until, name, size):
    async def check():
        _, url = serve(InputRecordingApp())
        cfg = load(overrides=[f"theme.name={name}"])
        roles = role_colours(cfg)
        dark = cfg.chrome.variant == "dark"
        sessions_tint = _walk(roles["surface"], dark, 1.1)
        # The two tints against the session background and against each other.
        assert contrast_ratio(roles["surface"], roles["bg"]) >= 1.2
        assert contrast_ratio(sessions_tint, roles["bg"]) >= 1.2
        assert contrast_ratio(sessions_tint, roles["surface"]) >= 1.1
        for role in PANEL_ROLES:
            for tint in (roles["surface"], sessions_tint):
                assert contrast_ratio(roles[role], tint) >= 4.5, (name, role, tint)
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 0, "the targets")
            await pilot.pause()
            sidebar = screen.sidebar
            bars = {roles["accent_soft"], roles["surface_3"]}
            top = _section_backgrounds(screen, 0, sidebar.targets_height)
            bottom = _section_backgrounds(screen, sidebar.targets_height, sidebar.size.height)
            assert {bg for _, _, bg in top} - bars == {roles["surface"]}, "Targets keeps the P1 surface"
            assert {bg for _, _, bg in bottom} - bars == {sessions_tint}, "Sessions is the next step"
            for section in (top, bottom):
                for text, fg, bg in section:
                    if text.strip() and fg is not None:
                        assert contrast_ratio(fg, bg) >= 4.5, (name, text, fg, bg)
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_with_no_colour_the_sections_stay_apart_by_their_frames_and_marks(serve, wait_until, monkeypatch, size):
    monkeypatch.setenv("NO_COLOR", "1")

    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.pause()
            lines = screen.sidebar.text.splitlines()
            top, split = lines[0], screen.sidebar.targets_height
            assert top[0] in "┌┏" and "▦ Targets" in top
            assert lines[split][0] in "┌┏" and "▸ Sessions" in lines[split]
            assert lines[split - 1][0] in "└┗" and lines[-1][0] in "└┗"
    asyncio.run(check())


# -- one letter, one meaning --


@pytest.mark.parametrize("size", SIZES)
def test_answer_is_y_after_the_leader_and_a_does_nothing(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            client = screen.pump.state.client_id

            def notes():
                return [n.message for n in app._notifications._notifications.values()]

            await pilot.press(LEADER, "a")
            await pilot.pause()
            assert "a has no shortcut." in str(app.screen.query_one("#menu-key-message").render()), (
                "a means nothing after the leader now")
            assert not any("no question is waiting" in m for m in notes()), notes()
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")
            await pilot.press(LEADER, "y")
            await wait_until(pilot, lambda: any("Answer the question" in m and "no question is waiting" in m
                                                for m in notes()), "y to reach Answer")
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client) == [], "neither letter reaches the session"
            keys = {a.id: key for a, key, _ in screen.menu_entries()}
            assert keys["answer"].endswith(" y")
    asyncio.run(check())


def test_the_leader_table_gives_answer_y():
    assert A.leader_keys()["answer"] == "y"
    assert "a" not in A.leader_keys().values()
    assert A.action_for_leader_key("a") is None


# -- the detach hint under 80 columns --


def _hint_ids(state, width):
    inputs = StatusInputs(state=state, session="rtr-core-01", range_label="", keys_not_sent=False, leader="^\\",
                          leader_keys=A.leader_keys(), hint_priority=["menu", "answer", "take_back", "fleet", "who",
                                                                      "detach", "end", "size", "scroll", "inspector",
                                                                      "keys"],
                          keys_indicator=True, target=None, attaching=False)
    seen = []
    for piece in build_pieces(inputs, width):
        if piece.action and not piece.role.startswith("pill") and piece.action not in seen:
            seen.append(piece.action)
    return seen


ME, ME_USER, PEER, PEER_USER = "client-me", "fixture-user", "client-peer", "fixture-peer"
THREE = ((ME, ME_USER, True), (PEER, PEER_USER, True), ("client-viewer", "fixture-viewer", False))


def test_below_80_the_protected_hints_leave_scroll_keys_detach_take_back_and_the_menu_last():
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    present = set(_hint_ids(state, 200))
    assert {"detach", "take_back", "scroll", "keys", "menu"} <= present
    assert "detach" in _hint_ids(state, 80), "protected at the ruled size"
    gone = []
    for width in range(79, 29, -1):
        now = set(_hint_ids(state, width))
        assert now <= present, f"a hint came back at {width}"
        gone += [h for h in ("fleet", "who", "size", "end", "scroll", "keys", "detach", "take_back", "menu")
                 if h in present - now]
        present = now
    protected_order = [h for h in gone if h in ("scroll", "keys", "detach", "take_back", "menu")]
    assert protected_order == ["scroll", "keys", "detach", "take_back"], gone
    assert "menu" in present and "detach" not in present
    # Detach outlives every ordinary hint of the row.
    assert gone.index("detach") > max(gone.index(h) for h in ("fleet", "who", "size", "end") if h in gone)


@pytest.mark.parametrize("width", [79, 70])
def test_the_rendered_row_under_80_keeps_detach_while_ordinary_hints_have_gone(serve, wait_until, width):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(width, 24)) as pilot:
            screen = await key_login(pilot, app, wait_until)
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            await pilot.pause()
            text = screen.status.text
            assert "Ctrl+] detach" in text and "F1 Menu" in text, text
            assert "Who's here" not in text and "Size" not in text, text
    asyncio.run(check())


# -- the rail's rows --


@pytest.mark.parametrize("size", SIZES)
def test_a_session_that_ended_shows_a_cross_ahead_of_the_activity_mark(serve, monkeypatch, wait_until, size):
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")

    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            doomed, other = named_sessions(url, serve_app, "core-rtr-01", "sw-2", cols=size[0] - 6)
            await open_here(app, pilot, wait_until, doomed, other)

            def row():
                return next(r.text for r in screen.rail.rows if r.action == ACT_CHIP and r.arg == doomed)

            emit_live_output(serve_app, serve_app.sessions[doomed], b"printed in the background\r\n")
            await wait_until(pilot, lambda: "•" in row(), "the activity mark")
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[doomed])
            await wait_until(pilot, lambda: screen.slot_for(doomed).ended is not None, "the session to end")
            await wait_until(pilot, lambda: "✕" in row(), "the cross in the mark cell")
            assert "•" not in row(), "the cross comes ahead of the activity mark"
            assert row() == ("1 core-rt…✕" if size[0] > 80 else "1✕ "), row()
            assert screen.slot_for(doomed) is not None and screen.slot_for(other) is not None
    asyncio.run(check())


def test_the_ascii_set_draws_the_ended_cross_as_x(serve, monkeypatch, wait_until):
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")
    monkeypatch.setenv("TSB_TUI_GLYPHS_SET", "ascii")

    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await key_login(pilot, app, wait_until)
            doomed, other = named_sessions(url, serve_app, "core-rtr-01", "sw-2")
            await open_here(app, pilot, wait_until, doomed, other)
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[doomed])
            await wait_until(pilot, lambda: screen.slot_for(doomed).ended is not None, "the session to end")
            await wait_until(pilot, lambda: any(line.startswith("1x") for line in screen.rail.text.splitlines()),
                             "an x in the mark cell")
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_rail_lists_only_the_sessions_open_in_this_window(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        mine, held = named_sessions(url, serve_app, "rtr-core-01", "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, mine)
                screen._refresh_fleet()
                await wait_until(pilot, lambda: held in screen.fleet.records, "the poll to list the fleet")
                screen._sync_fleet_widgets()
                await pilot.pause()
                assert [r.arg for r in screen.rail.rows if r.action == ACT_CHIP] == [mine]
    asyncio.run(check())


# -- the single keyboard notice --


@pytest.mark.parametrize("size", SIZES)
def test_the_frame_notice_replaces_the_toast_when_the_keyboard_comes_to_you(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            with other_client(url, _serve.PEER, sid):
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, sid)
                assert not screen.pump.state.holds_keyboard
            await wait_until(pilot, lambda: screen.pump.state.holds_keyboard, "the keyboard to come to this client")
            await wait_until(pilot, lambda: "you type now" in screen_text(app), "the notice in the frame")
            await pilot.pause(0.2)
            titles = [n.title for n in app._notifications._notifications.values()]
            assert "keyboard" not in titles and "keyboard · you took it" not in titles, titles
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_taking_the_keyboard_back_says_so_in_the_frame_only(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        mine, held = named_sessions(url, serve_app, "rtr-core-01", "k8s-node-07")
        with other_client(url, _serve.PEER, held):
            app = TsbTuiApp(url)
            async with app.run_test(size=size) as pilot:
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, held, mine)
                state = screen.slot_for(held).pump.state
                assert not state.holds_keyboard
                await pilot.press(LEADER, "1")
                await wait_until(pilot, lambda: screen.front.session_id == held, "the session in front")
                await pilot.press(LEADER, "t")
                await wait_until(pilot, lambda: state.holds_keyboard, "the keyboard to come to this client")
                # The frame's line is cut with an ellipsis where the window is narrow (80 columns keeps its start).
                wanted = "you type now: you took it" + (f" from {_serve.PEER}" if size[0] > 80 else "")
                await wait_until(pilot, lambda: wanted in screen_text(app), "the notice in the frame")
                await pilot.pause(0.2)
                titles = [n.title for n in app._notifications._notifications.values()]
                assert "keyboard · you took it" not in titles, titles
    asyncio.run(check())


# -- Ctrl+] after the leader --


@pytest.mark.parametrize("size", SIZES)
def test_leader_then_ctrl_right_bracket_sends_the_byte_to_the_session(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            client = screen.pump.state.client_id
            await pilot.press(LEADER, DETACH)
            await wait_until(pilot, lambda: b"\x1d" in serve_app.received(client), "Ctrl+] to reach the session")
            await flushed(pilot, screen, serve_app, wait_until)
            assert serve_app.received(client) == [b"\x1d"]
            assert app.screen is screen and not screen.leader_pending, "the Menu is closed"
            assert len(screen.slots) == 1, "the session was not detached"
    asyncio.run(check())


def test_all_keys_says_the_leader_then_ctrl_right_bracket_sends_it(tmp_path):
    cfg = load(config_file=tmp_path / "absent", environ={})
    text = "\n".join(keys_lines(cfg))
    assert "Ctrl+\\ Ctrl+] sends Ctrl+]" in text, text
    assert re.search(r"Ctrl\+\] detaches", text)



# -- the review round --

@pytest.mark.parametrize("size", SIZES)
def test_menu_opened_directly_ctrl_right_bracket_detaches(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            (sid,) = named_sessions(url, serve_app, "rtr-core-01")
            await open_here(app, pilot, wait_until, sid)
            client = screen.pump.state.client_id
            await pilot.press("f1")  # the Menu, opened directly: no leader is pending
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "the Menu to open")
            await pilot.press(DETACH)
            await wait_until(pilot, lambda: not screen.slots, "the front session to detach")
            await wait_until(pilot, lambda: app.screen is screen, "the Menu to close")
            assert serve_app.received(client) == [], serve_app.received(client)
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_rail_numbers_the_open_sessions_first(serve, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        ids = named_sessions(url, serve_app, *(f"rtr-{i:02d}" for i in range(11)))
        held, first, second = ids[:9], ids[9], ids[10]
        with contextlib.ExitStack() as stack:
            for sid in held:
                stack.enter_context(other_client(url, _serve.PEER, sid))  # nine a peer login holds
            app = TsbTuiApp(url, cfg=load(overrides=["rail.chips=mine_held_by_others,open_here"]))
            async with app.run_test(size=size) as pilot:
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, first, second)
                screen._refresh_fleet()
                await wait_until(pilot, lambda: all(s in screen.fleet.records for s in held),
                                 "the poll to list the fleet")
                screen._sync_fleet_widgets()
                await pilot.pause()
                chips = [r for r in screen.rail.rows if r.action == ACT_CHIP]
                assert [r.arg for r in chips] == [first, second], [r.text for r in chips]
                assert chips[0].text.startswith("1") and chips[1].text.startswith("2"), \
                    [r.text for r in chips]
                assert [r for r in screen.rail.rows if r.action == ACT_MORE] == [], "no open session is pushed out"
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_frame_notice_clears_when_the_keyboard_leaves(serve, wait_until, screen_text, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        (sid,) = named_sessions(url, serve_app, "rtr-core-01")
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            with other_client(url, _serve.PEER, sid):  # the peer attaches first and holds the keyboard
                screen = await key_login(pilot, app, wait_until)
                await open_here(app, pilot, wait_until, sid)
                assert not screen.pump.state.holds_keyboard
            await wait_until(pilot, lambda: screen.pump.state.holds_keyboard, "the keyboard to come to this client")
            await wait_until(pilot, lambda: "you type now" in screen_text(app), "the notice in the frame")
            with serve_app.lock:
                serve_app.sessions[sid]["control_holder"] = {"client_id": "stub-other", "user": _serve.PEER}
                serve_app._send_token(serve_app.sessions[sid], "takeover")
            await wait_until(pilot, lambda: not screen.pump.state.holds_keyboard, "the keyboard to leave")
            await wait_until(pilot, lambda: "you type now" not in screen_text(app), "the notice to clear at once",
                             timeout_s=2.0)
            titles = [n.title for n in app._notifications._notifications.values()]
            assert "keyboard" in titles, titles  # leaving keeps its toast
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_keys_row_while_the_filter_is_edited_names_the_editing_keys(serve, wait_until, size):
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await pilot.pause()
            assert screen.sidebar.editing
            hints = [(h.key, h.label) for h in screen.status_inputs().target.hints]
            assert hints == [("⏎", "Done"), ("Esc", "Clear")], hints
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_a_custom_theme_the_tint_cannot_carry_puts_sessions_on_the_targets_surface(serve, wait_until, size):
    async def check():
        _, url = serve(InputRecordingApp())
        cfg = load(overrides=["theme.name=nord", "theme.text_muted=#a0acc0"])
        roles = role_colours(cfg)
        tint = _walk(roles["surface"], True, 1.1)
        assert contrast_ratio(roles["text_muted"], roles["surface"]) >= 4.5, "muted passes on the Targets surface"
        assert contrast_ratio(roles["text_muted"], tint) < 4.5, "muted fails on the second step"
        for role in PANEL_ROLES:
            if role != "text_muted":
                assert contrast_ratio(roles[role], tint) >= 4.5, (role, "still passes on the tint")
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await wait_until(pilot, lambda: len(screen.sidebar.model.targets) > 0, "the targets")
            await pilot.pause()
            sidebar = screen.sidebar
            bars = {roles["accent_soft"], roles["surface_3"]}
            top = _section_backgrounds(screen, 0, sidebar.targets_height)
            bottom = _section_backgrounds(screen, sidebar.targets_height, sidebar.size.height)
            assert {bg for _, _, bg in top} - bars == {roles["surface"]}, "Targets keeps the surface"
            assert {bg for _, _, bg in bottom} - bars == {roles["surface"]}, "Sessions falls back to the surface"
            for section in (top, bottom):
                for text, fg, bg in section:
                    if text.strip() and fg is not None:
                        assert contrast_ratio(fg, bg) >= 4.5, (text, fg, bg)
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_enter_on_the_rail_rows_reads_the_fleet_like_a_click(serve, wait_until, monkeypatch, size):
    monkeypatch.setenv("TSB_TUI_FLEET_VIEW_REFRESH_S", "0")  # manual: only an open's read polls
    async def check():
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            await wait_until(pilot, lambda: screen.poller.calls >= 1, "the poller's first poll")
            await pilot.press("shift+tab")  # the rail
            await pilot.press(LEADER, "f")  # closes the panel; the rail keeps the keys
            await pilot.pause()
            assert not screen.sidebar_open and screen.rail.has_focus
            before = screen.poller.calls
            await pilot.press("down", "enter")  # Targets
            await wait_until(pilot, lambda: screen.sidebar_open and screen.focus_owner == "targets",
                             "the panel on Targets")
            await wait_until(pilot, lambda: screen.poller.calls > before, "the fleet read on Enter")
            await pilot.press("shift+tab")  # targets -> the rail
            await pilot.pause()
            assert screen.rail.has_focus
            before = screen.poller.calls
            await pilot.press("up", "enter")  # Sessions, back at the first row
            await wait_until(pilot, lambda: screen.sidebar_open and screen.focus_owner == "sessions",
                             "the panel on Sessions")
            await wait_until(pilot, lambda: screen.poller.calls > before, "the fleet read on Enter")
    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_the_ended_screen_answers_n_and_the_arrows(serve, monkeypatch, wait_until, size):
    async def check():
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen = await key_login(pilot, app, wait_until)
            one, two, three = named_sessions(url, serve_app, "core-rtr-01", "sw-2", "sw-3")
            await open_here(app, pilot, wait_until, one, two, three)
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[three])
            await wait_until(pilot, lambda: screen.ended is not None, "the session to end")
            hints = [(h.key, h.label) for h in screen.status_inputs().ended.hints]
            assert hints[0] == ("n", "New session here"), hints
            assert ("→", "Next open session") in hints and ("←", "Previous open session") in hints, hints
            assert not any(key == "c" for key, _ in hints), hints  # `c` stays unadvertised
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session on n")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen and screen.ended is not None, "back to the end")
            await pilot.press("c")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "New session on c")
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen and screen.ended is not None, "back to the end")
            await pilot.press("left")
            await wait_until(pilot, lambda: screen.front is not None and screen.front.session_id == two
                             and screen.ended is None, "the previous open session")
            with serve_app.lock:
                serve_app._kill(serve_app.sessions[two])
            await wait_until(pilot, lambda: screen.ended is not None, "the session to end")
            await pilot.press("right")
            await wait_until(pilot, lambda: screen.front is not None and screen.front.session_id == one
                             and screen.ended is None, "the next open session")
    asyncio.run(check())
