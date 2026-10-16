"""The Theme picker (design B5, laptop layout): it opens from the leader and the Menu, previews the
look as the highlight moves, keeps or puts back the look at its end, and tells the status row.

Why these matter (Rule 9): a person picks a look by moving through lists that show them live; if the
session dimmed under the panel they cannot see what they are choosing, if the preview did not follow
the highlight the picker is a name list, if Esc did not put back the names and their sources a config
file's choice would be silently lost, and if `⏎` did not keep the run-time pick there would be no way
to try a look. The drive rule (TT19) matters because an agent sets the theme with `--set` and must be
told that, not left with a key that does nothing. Servers: the contract's in-process fixture server
(DT7); the Menu click path is test_discoverability's, driven through `Rig`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from drive_support import Rig
from input_server import InputRecordingApp, attached, make_session
from stub_server import record_output
from test_discoverability import _settle
from textual.command import CommandList, CommandPalette
from textual.widgets import Static
from tsb_tui import theme as tsb_theme
from tsb_tui.app import TsbTuiApp
from tsb_tui.model import actions as A
from tsb_tui.screens.theme_picker import PANEL_WIDTH, ThemeList, ThemePicker
from tsb_tui.theme_files import packaged_dir, parse_scheme, parse_theme
from tsb_tui.widgets.status_row import StatusRow
from tsbc import _serve

TERRACOTTA_SURFACE = parse_theme(packaged_dir("themes") / "terracotta.toml").roles["surface"]
TSB_BACKGROUND = parse_scheme(packaged_dir("schemes") / "tsb-default.json").background
DRACULA = parse_scheme(packaged_dir("schemes") / "dracula.json")
SOLARIZED = parse_scheme(packaged_dir("schemes") / "solarized-dark.json")
NORD_SURFACE = parse_theme(packaged_dir("themes") / "nord.toml").roles["surface"]

# -- helpers --


def _rect_fills(svg: str) -> list[tuple[str, float]]:
    """The (fill, x) of every painted rect in an SVG export."""
    return [(fill, float(x)) for fill, x in re.findall(r'<rect fill="(#[0-9a-f]{6})" x="([0-9.]+)"', svg)]


def _notifications(app) -> list[str]:
    return [n.message for n in app._notifications._notifications.values()]


def _user_dir() -> Path:
    """The user's tsb-tui directory, as the pinned environment gives it to the app."""
    return Path(os.environ["XDG_CONFIG_HOME"]) / "tsb-tui"


def _listing_is_painted(picker: ThemePicker) -> bool:
    """The listing's own completion condition: every row and every broken-file line has content. The
    panel fills its rows in `call_after_refresh`, so the row widgets mount before any line is painted;
    a test that reads on the widgets alone can read before a line is painted, so it waits on the painted
    content."""
    rows = list(picker.query("#theme-themes > Static")) + list(picker.query("#theme-schemes > Static"))
    broken = list(picker.query(".broken-theme")) + list(picker.query(".broken-scheme"))
    return bool(rows) and all(r.render().plain.strip() for r in rows) and all(
        b.render().plain.strip() for b in broken)


async def _open_picker(pilot, app, screen, wait_until) -> ThemePicker:
    """Open the picker the way an operator does: the leader and `T`, then wait on the listing's own
    condition — every row and broken line painted — not on the row widgets."""
    await pilot.press("ctrl+backslash", "T")
    await wait_until(pilot, lambda: isinstance(app.screen, ThemePicker), "the picker to open")
    picker = app.screen
    await wait_until(pilot, lambda: _listing_is_painted(picker), "the listing to paint every row")
    return picker


def _move(picker: ThemePicker, kind: str, slug: str) -> None:
    """Move a list's highlight onto the row of `slug` (a scheme) or `id` (a theme), as `↑↓` would."""
    keys = [s.slug for s in picker._schemes] if kind == "schemes" else [t.id for t in picker._themes]
    picker.set_highlight(kind, keys.index(slug))


async def _session(serve, wait_until):
    """A login and one open session with painted output: the state the picker previews over."""
    serve_app, url = serve(InputRecordingApp())
    sid = make_session(url, target="normal")
    record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
    app = TsbTuiApp(url)
    async with app.run_test(size=(120, 40)) as pilot:
        app.client.login(_serve.USER, _serve.PASSWORD)
        screen = await attached(pilot, app, sid, wait_until)
        yield app, screen, pilot


# -- the tests --


def test_the_leader_and_T_open_the_picker_and_the_session_stays_visible_beside_it(
        serve, wait_until) -> None:
    """B5, D40. The picker opens from `^\\ T` and from a click on the Menu's "Theme…", and the session
    is drawn undimmed beside it: the canvas keeps the scheme's own background right of the rail and
    the rail keeps terracotta's surface, so the person sees what the pick would do. A dimming overlay
    (Textual's default `background: $background 60%` under a modal) hides exactly that.

    fails today: no action on `T`, so the leader key does nothing and the Menu has no entry."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, target="normal")
        record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            app.client.login(_serve.USER, _serve.PASSWORD)
            screen = await attached(pilot, app, sid, wait_until)
            await _open_picker(pilot, app, screen, wait_until)
            lines = "\n".join(s.text for s in app.screen._compositor.render_strips()).splitlines()
            assert "Theme" in lines[0], f"the panel title is not on the top border: {lines[0]!r}"
            assert "ops@infra-db-02" in lines[0][4:], "the session is not drawn beside the panel"
            fills = _rect_fills(app.export_screenshot())
            assert any(f == TSB_BACKGROUND and 4 <= x < 66 for f, x in fills), (
                "the canvas lost the scheme's background under the picker")
            assert any(f == TERRACOTTA_SURFACE and x < 4 for f, x in fills), (
                "the rail lost terracotta's surface under the picker")
            # the Menu's own way in: click `≡ Menu`, scroll to the entry, click it (the same path an
            # agent uses in test_discoverability, run here in the pilot — drive blocks `theme` by design).
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.screen is screen, "the picker to close")
            await pilot.click(StatusRow, offset=(screen.status.hit_x("menu") + 1, 0))
            await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette), "the Menu to open")
            options = app.screen.query_one(CommandList)
            row = None
            for _ in range(40):
                lines = [strip.text for strip in app.screen._compositor.render_strips()]
                row = next((i for i, line in enumerate(lines) if line.strip().startswith("Theme…")), None)
                if row is not None:
                    break
                options.scroll_down(animate=False)
                await pilot.pause()
            assert row is not None, "the Menu never showed Theme…"
            x = len(lines[row][:lines[row].find("Theme…")]) + 2
            await pilot.click(offset=(x, row))
            await wait_until(pilot, lambda: isinstance(app.screen, ThemePicker), "the picker from the Menu click")

    asyncio.run(check())


def test_moving_the_lists_previews_the_look_at_once(serve, wait_until) -> None:
    """B5, ruling 2. Moving the scheme list to Dracula repaints the front canvas in Dracula's
    background while the rail keeps terracotta's surface; moving the chrome list to nord repaints the
    rail in Nord's surface. If a highlight did not paint, the picker would be a name list and the
    operator would choose blind.

    fails today: no picker, so a highlight move is nothing."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        fills = _rect_fills(app.export_screenshot())
        assert any(f == DRACULA.background and x >= 4 for f, x in fills), (
            "the canvas did not preview Dracula's background")
        assert any(f == TERRACOTTA_SURFACE and x < 4 for f, x in fills), (
            "the rail moved with the scheme (ruling 2 says it must not)")
        _move(picker, "themes", "nord")
        await pilot.pause(0.2)
        fills = _rect_fills(app.export_screenshot())
        assert any(f == NORD_SURFACE and x < 4 for f, x in fills), (
            "the rail did not preview Nord's surface")
        await gen.aclose()

    asyncio.run(check())


def test_scheme_rows_show_the_full_name_and_a_space_before_the_variant(serve, wait_until) -> None:
    """The two display defects found on `picker-T1c-120x40` (orchestrator, 2026-10-16): a row must be
    readable as name, sample, note. "Catppuccin Macchiato" showed as "Catppuccin Macchiat" — the ruled
    column is as wide as the longest name in the lists, `…` only when the panel is narrower — and the
    scheme sample ran into its variant (`●●●●●●adark · MIT`) while the chrome rows are spaced. The mock
    separates them (tools/mock-picker.py:196-207): the swatch block ends with a space, so a scheme row
    reads `●●●●●●a  dark · MIT` and a chrome row `●◆▲a    dark`. If the sample runs into the note the
    person cannot tell where the sample ends, and a cut name cannot be told apart from another pick.

    fails today: the name column is fixed at 19 cells and the scheme swatch has no trailing space."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        await _open_picker(pilot, app, screen, wait_until)
        lines = [strip.text for strip in app.screen._compositor.render_strips()]
        row = next((line for line in lines if "Catppuccin Macchiato" in line), None)
        assert row is not None, f"the longest scheme name is cut: {[l for l in lines if 'Catppuccin' in l]!r}"
        assert re.search(r"●●●●●●a {2}dark · MIT", row), f"the sample runs into the variant: {row!r}"
        assert any(re.search(r"●◆▲a\s{2,}dark", line) for line in lines), "the chrome rows lost their spacing"
        await gen.aclose()

    asyncio.run(check())


def test_esc_puts_back_both_names_their_sources_and_the_canvas(serve, wait_until) -> None:
    """B5, TT12. A config file sets `theme.name = "paper"`; the picker previews nord and Dracula;
    Esc must put back the name, its source (the config file), the scheme, and the canvas. If the
    restore only moved colours and not sources, the operator's config-file choice would be lost for
    the rest of the run.

    fails today: no picker, so nothing to put back."""

    async def check() -> None:
        cfg_dir = _user_dir()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text('[theme]\nname = "paper"\n', encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        assert app.cfg["theme.name"] == "paper" and app.cfg.source("theme.name").startswith("config file")
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "themes", "nord")
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs,
                         "the picker to close")
        assert app.cfg["theme.name"] == "paper", "Esc lost the config file's theme"
        assert app.cfg.source("theme.name").startswith("config file"), "Esc lost the name's source"
        assert app.cfg["theme.term.scheme"] == "tsb-default", "Esc did not put the scheme back"
        fills = _rect_fills(app.export_screenshot())
        assert any(f == TSB_BACKGROUND and x >= 4 for f, x in fills), "the canvas did not go back"
        await gen.aclose()

    asyncio.run(check())


def test_enter_keeps_the_previewed_scheme_for_this_run(serve, wait_until) -> None:
    """OPEN-T6 a (ruled): `⏎` uses the highlighted pick and closes the picker; the name's source is
    `this run`, so nothing is written to any file. If `⏎` closed without keeping, the preview would
    be a toy.

    fails today: no picker and no `⏎` path, so nothing keeps a previewed look."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        await pilot.press("enter")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs,
                         "the picker to close on `⏎`")
        assert app.cfg["theme.term.scheme"] == "dracula", "`⏎` did not keep the highlighted scheme"
        assert app.cfg.source("theme.term.scheme") == "this run", "the kept name's source is not this run"
        await gen.aclose()

    asyncio.run(check())


def test_a_user_scheme_reads_yours_and_a_broken_one_is_listed_where_it_cannot_be_chosen(
        serve, wait_until) -> None:
    """TT10, OPEN-T6 c. A user file is marked `yours`; a user file that failed to load is listed
    after the list with its file and field, and is not in the list, so the highlight can never reach
    it. If a broken file were choosable, `⏎` would apply a look the config must refuse.

    fails today: no picker lists user files at all."""

    async def check() -> None:
        schemes_dir = _user_dir() / "schemes"
        schemes_dir.mkdir(parents=True, exist_ok=True)
        good = {"name": "My Lab", "ansi": ["#000000", "#ff0000", "#00ff00", "#ffff00", "#0000ff",
                                           "#ff00ff", "#00ffff", "#ffffff"] * 2,
                "foreground": "#d0d0d0", "background": "#101020"}
        (schemes_dir / "my-lab.json").write_text(json.dumps(good), encoding="utf-8")
        (schemes_dir / "broken.json").write_text(json.dumps({"name": "Broken"}), encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        lines = "\n".join(s.text for s in app.screen._compositor.render_strips())
        mine = [line for line in lines.splitlines() if "My Lab" in line]
        assert mine and "yours" in mine[0], f"a user scheme is not marked yours: {mine}"
        # Read the listing from its widgets, not from a screen line: the line starts with the file's full
        # path and folds at the panel's 50 columns, so a screen line can split the file's name in two.
        broken = [s.render().plain for s in picker.query(".broken-scheme")]
        assert any("broken.json" in b and "ansi: required" in b for b in broken), (
            f"the broken file is not listed: {broken}")
        slugs = [s.slug for s in picker._schemes]
        assert "my-lab" in slugs, "the good user file is not in the list"
        assert "broken" not in slugs, "a broken file is in the list, where the highlight can reach it"
        schemes_list = picker.query_one("#theme-schemes", ThemeList)
        # ruling 3: the broken-file line sits inside the scheme list's scroll region, after its last row, so
        # it scrolls with the list; `highlightable_rows()` is the count the highlight can reach, which excludes it.
        assert any(b.parent is schemes_list for b in picker.query(".broken-scheme")), (
            "the broken-file line is outside the scheme list's scroll region")
        row_count = schemes_list.highlightable_rows()
        assert row_count == len(slugs), "the highlight can land on a row that is not an item"
        await gen.aclose()

    asyncio.run(check())


def test_tab_moves_between_the_two_lists_and_nowhere_else(serve, wait_until) -> None:
    """TT20. `Tab` switches between the two lists; the panel's buttons take no focus, so `Tab` never
    lands on them. If `Tab` walked the buttons, a keyboard-only operator would lose the list they are
    previewing.

    fails today: no picker, so no lists to move between."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        lists = {"theme-themes": picker.query_one("#theme-themes"), "theme-schemes": picker.query_one("#theme-schemes")}
        assert app.focused is lists["theme-themes"], "the chrome list does not have the focus on open"
        seen: set[str] = set()
        for _ in range(4):
            await pilot.press("tab")
            await pilot.pause(0.05)
            seen.add(app.focused.id)
        assert seen == {"theme-themes", "theme-schemes"}, f"`Tab` went outside the two lists: {seen}"
        await gen.aclose()

    asyncio.run(check())


def test_the_status_row_gives_the_picker_its_hints_and_gives_the_row_back(
        serve, wait_until) -> None:
    """D14, TT20. While the picker has the keys the row reads `keys → theme` with its four hints;
    when it closes the row must go back to the session's hints. A row left on a closed dialog offers
    nothing, and a person cannot see which keys are live.

    fails today: no picker, so the row never reads `keys → theme`."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        await _open_picker(pilot, app, screen, wait_until)
        text = screen.status.text
        assert "keys → theme" in text, f"the row does not name the picker: {text!r}"
        for hint in ("↑↓ choose", "Tab chrome│colours", "⏎ Use", "Esc put back"):
            assert hint in text, f"the row is missing the hint {hint!r}: {text!r}"
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs
                         and "keys →" not in screen.status.text, "the row to give the keys back")
        assert "≡ Menu" in screen.status.text
        await gen.aclose()

    asyncio.run(check())


def test_under_drive_theme_is_blocked_with_the_set_reason_and_T_tells_the_agent(
        serve, tmp_path, wait_until) -> None:
    """TT19. Under drive the `theme` action is listed and blocked with the `--set` reason, and the
    leader and `T` say so as a toast without opening a picker (drive sets the theme with `--set`). An
    agent left with a key that silently did nothing, or a picker under drive that writes the saved
    choice, breaks the drive contract.

    fails today: no `theme` action exists, so the table lists nothing to block."""

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, target="normal")
        async with Rig(url, tmp_path=tmp_path, profile="harness", overrides=("input.guard=true",),
                       size=(120, 40)) as rig:
            await rig.login(_serve.USER)
            await rig.attach(sid)
            screen = rig.app.workspace()
            facts = screen.facts()
            assert A.blocked("theme", facts) == (
                "drive sets the theme with --set theme.name=… --set theme.term.scheme=…")
            await rig.pilot.press("ctrl+backslash", "T")
            await _settle(rig, lambda: any("drive sets the theme" in m for m in _notifications(rig.app)),
                          "the not-available toast")
            assert not isinstance(rig.app.screen, ThemePicker), "a picker opened under drive"

    asyncio.run(check())


# -- the phone layout (B5, TT20) --


async def _phone(serve, wait_until):
    """A login and one painted session in a 65x30 window: the phone the picker lays itself out for."""
    serve_app, url = serve(InputRecordingApp())
    sid = make_session(url, target="normal")
    record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
    app = TsbTuiApp(url)
    async with app.run_test(size=(65, 30)) as pilot:
        app.client.login(_serve.USER, _serve.PASSWORD)
        screen = await attached(pilot, app, sid, wait_until)
        screen.close_sidebar()
        yield app, screen, pilot


def test_on_a_phone_the_picker_is_the_lower_half_beside_the_rail_with_one_list(serve, wait_until) -> None:
    """TT20, design B5 (phone). At 65 columns the panel takes the lower half of the area beside the
    rail and shows one list at a time, so the person sees the session above it and the rail's chips
    throughout. If the laptop layout were drawn at phone width, the panel would fill the window's
    height and cover what a pick is supposed to show live.

    fails today: the laptop layout fills the whole height and shows both lists."""

    async def check() -> None:
        gen = _phone(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        lines = [s.text for s in app.screen._compositor.render_strips()]
        assert all(line[3] == "│" for line in lines[:29]), "the rail is not drawn on every row"
        top = next(i for i, line in enumerate(lines) if "╭" in line)
        assert top >= 15, f"the panel's top border is at row {top}, not the lower half of 30 rows"
        assert picker.query_one("#theme-themes").styles.display == "block", "the chrome list is hidden"
        assert picker.query_one("#theme-schemes").styles.display == "none", "both lists are shown"
        text = "\n".join(lines)
        assert "terracotta" in text and "Catppuccin Mocha" not in text, "both lists are shown"
        await gen.aclose()

    asyncio.run(check())


def test_tab_shows_the_other_list_on_the_phone_and_marks_the_active_tab(serve, wait_until) -> None:
    """TT20 (phone): `Tab` switches the tab and the focus moves with it, so the keys stay on the list
    that is on screen, and the tab line shows which one that is. If `Tab` left the focus on the hidden
    list, `↑↓` would move a highlight the person cannot see.

    fails today: there is no tab line, and both lists are shown at every width."""

    async def check() -> None:
        gen = _phone(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        await pilot.press("tab")
        await pilot.pause(0.2)
        assert app.focused.id == "theme-schemes", "`Tab` did not move the focus to the other list"
        assert picker.query_one("#theme-schemes").styles.display == "block"
        assert picker.query_one("#theme-themes").styles.display == "none"
        lines = [s.text for s in app.screen._compositor.render_strips()]
        text = "\n".join(lines)
        assert "Dracula" in text and "terracotta" not in text, "the chrome list is still shown"
        tab_line = next(line for line in lines if "Chrome │ Terminal colours" in line)
        assert "Tab switches" in tab_line, f"the tab line: {tab_line!r}"
        content = picker.query_one("#theme-tabs", Static).render()
        plain = content.plain
        active = content.get_style_at_offset(plain.index("Terminal colours"))
        assert active.underline and active.foreground.hex.lower() == app.cfg["theme.accent_hot"], (
            f"the active tab is not marked: {active!r}")
        other = content.get_style_at_offset(plain.index("Chrome"))
        assert not other.underline and other.foreground.hex.lower() == app.cfg["theme.text_muted"], (
            f"the inactive tab is not muted: {other!r}")
        await gen.aclose()

    asyncio.run(check())


def test_a_window_that_shrinks_re_lays_the_picker_and_widening_gives_the_laptop_back(
        serve, wait_until) -> None:
    """TT20: a window that changes size re-lays the picker out without closing it and with its
    highlights kept — the previewed pick must survive the resize. If the picker had to close and
    reopen, the person's place in both lists and the live preview would be lost mid-pick.

    fails today: the picker draws the laptop panel at every width, so shrinking changes nothing."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "themes", "nord")
        _move(picker, "schemes", "dracula")
        hi_t, hi_s = picker.highlight("themes"), picker.highlight("schemes")
        await pilot.resize_terminal(65, 30)
        await pilot.pause(0.3)
        lines = [s.text for s in app.screen._compositor.render_strips()]
        top = next(i for i, line in enumerate(lines) if "╭" in line)
        assert top >= 15, f"the panel's top border is at row {top}, not the lower half"
        assert picker.highlight("themes") == hi_t and picker.highlight("schemes") == hi_s, (
            "the resize lost a highlight")
        assert any("▸" in line and "nord" in line for line in lines), "the chrome highlight is gone"
        assert picker.query_one("#theme-schemes").styles.display == "none", "the phone layout shows both lists"
        await pilot.resize_terminal(120, 40)
        await pilot.pause(0.3)
        panel = picker.query_one("#theme-panel")
        assert panel.styles.width.value == PANEL_WIDTH, f"the laptop panel is {panel.styles.width.value} columns wide"
        assert picker.query_one("#theme-themes").styles.display == "block"
        assert picker.query_one("#theme-schemes").styles.display == "block", "the laptop layout shows one list"
        assert picker.query_one("#theme-tabs").styles.display == "none", "the tab line is on the laptop"
        await gen.aclose()

    asyncio.run(check())


def test_the_phone_status_row_gives_the_picker_use_and_back(serve, wait_until) -> None:
    """TT20, mock :259-260. At compact width the row's hints are `⏎ Use` and `Esc back`; the laptop's
    four hints do not fit a phone row. A person on the phone must see exactly the keys that work
    there, not a `↑↓` hint for a list they cannot see.

    fails today: the picker always gives the laptop's four hints."""

    async def check() -> None:
        gen = _phone(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        await _open_picker(pilot, app, screen, wait_until)
        text = screen.status.text
        assert "⏎ Use" in text and "Esc back" in text, f"the compact hints: {text!r}"
        assert "↑↓ choose" not in text and "Tab chrome│colours" not in text, f"the laptop hints: {text!r}"
        await gen.aclose()

    asyncio.run(check())


def test_use_applies_the_pick_and_closes_only_when_the_pair_still_previews(serve, wait_until) -> None:
    """OPEN-T6 a (ruled): `⏎` applies the highlighted pair and closes only when it previews cleanly. A
    user file that turns invalid between the preview and `⏎` is a toast naming the file and the field,
    the picker stays open, and the look in use stays. Closing on a failed preview leaves the run
    pointing at a scheme the config cannot load, with no reason shown.

    fails today: `⏎` closes without re-applying, so the broken file becomes the pick and no toast shows."""

    async def check() -> None:
        schemes_dir = _user_dir() / "schemes"
        schemes_dir.mkdir(parents=True, exist_ok=True)
        good = {"name": "My Lab", "ansi": ["#000000", "#ff0000", "#00ff00", "#ffff00", "#0000ff",
                                           "#ff00ff", "#00ffff", "#ffffff"] * 2,
                "foreground": "#d0d0d0", "background": "#101020"}
        path = schemes_dir / "my-lab.json"
        path.write_text(json.dumps(good), encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "schemes", "my-lab")
        await pilot.pause(0.2)
        assert app.cfg["theme.term.scheme"] == "my-lab", "the preview did not apply the user scheme"
        path.write_text(json.dumps({"name": "My Lab"}), encoding="utf-8")  # the file turns invalid
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ThemePicker), "`⏎` closed the picker on a failed preview"
        toasts = _notifications(app)
        assert any("my-lab.json" in m and "ansi" in m for m in toasts), (
            f"no toast naming the file and the field: {toasts}")
        assert app.cfg["theme.term.scheme"] == "my-lab", "the look in use did not stay"
        await gen.aclose()

    asyncio.run(check())


def test_a_narrow_panel_truncates_the_note_first_and_keeps_the_name_and_the_swatch(
        serve, wait_until) -> None:
    """The ruled column widths: the markers, the name's reserved width and the swatch block are never
    given up; the note gives cells first with `…`, then the name, and no computed width is negative. A
    name cut to make room for the note cannot be told apart from another pick, and a negative width
    slices the name away.

    fails today: `_fit_columns` subtracts the longest note from the row, so at 49 columns the name
    column is 17 (the longest name is cut) and the note is never truncated; at 35 it is -1."""

    async def check() -> None:
        gen = _phone(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        await pilot.resize_terminal(49, 30)
        await pilot.pause(0.3)
        # the rail is 4 columns, the border and padding 4: the panel's content is 41 cells
        assert picker._inner == 41, f"the panel content is {picker._inner} cells at 49 columns"
        assert picker._name_w == 20, f"the name column is {picker._name_w}, not the reserved 20"
        lines = [s.text for s in app.screen._compositor.render_strips()]
        assert any("dark ·…" in line for line in lines), (
            f"the note was not truncated first: {[line for line in lines if 'terracotta' in line]!r}")
        await pilot.press("tab")  # the scheme list, whose longest name is 20 cells
        await pilot.pause(0.3)
        lines = [s.text for s in app.screen._compositor.render_strips()]
        row = next((line for line in lines if "Catppuccin Macchiato" in line), None)
        assert row is not None, (
            f"the longest name was cut to make room for the note: {[l for l in lines if 'Catppuccin' in l]!r}")
        await pilot.resize_terminal(35, 30)
        await pilot.pause(0.3)
        # `MIN_NAME` in the picker: the name keeps one glyph and `…`; the note's room is never negative
        assert picker._name_w >= 2 and picker._inner - picker._note_col >= 0, (
            f"a negative width at inner {picker._inner}: name_w={picker._name_w}, note_col={picker._note_col}")
        await gen.aclose()

    asyncio.run(check())


def test_the_status_note_names_the_chrome_theme_being_previewed(serve, wait_until) -> None:
    """OPEN-T6 d1 (ruled): while a preview is on, the row names what is previewed — the theme id when
    only the chrome differs, the scheme name when only the scheme, both when both differ. A row naming
    the scheme in use while a chrome theme is previewed sends the operator to the wrong list.

    fails today: the note always names the highlighted scheme, so previewing nord says
    `· previewing TSB Default`."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "themes", "nord")
        await pilot.pause(0.2)
        text = screen.status.text
        assert "· previewing nord" in text, f"the row does not name the previewed chrome theme: {text!r}"
        assert "previewing TSB Default" not in text, f"the row names the scheme in use: {text!r}"
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        text = screen.status.text
        assert "· previewing nord · Dracula" in text, f"both differ, the row says: {text!r}"
        await gen.aclose()

    asyncio.run(check())


def test_every_chrome_colour_the_picker_paints_comes_from_the_role_table(
        serve, wait_until, monkeypatch) -> None:
    """TT14: the picker paints from `theme.role_colours(config)`, the one reader of the role knobs,
    never from `cfg["theme.<role>"]` directly. Phase 14's ansi16 theme is where the two readers part
    company; a picker that reads the knobs paints a chrome the rest of the TUI does not show.

    fails today: `_row_content`, `_fill_head`, `_fill_tabs` and `_layout` read `cfg["theme.…"]`, so a
    role the table resolves differently is painted from the knob."""

    async def check() -> None:
        gen = _phone(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        real = tsb_theme.role_colours

        def table(cfg):
            roles = dict(real(cfg))
            roles["text_muted"] = "#010203"
            return roles

        monkeypatch.setattr(tsb_theme, "role_colours", table)
        picker = await _open_picker(pilot, app, screen, wait_until)
        content = picker.query_one("#theme-tabs", Static).render()
        plain = content.plain
        tab = content.get_style_at_offset(plain.index("Terminal colours"))
        assert tab.foreground.hex.lower() == "#010203", f"the tab line did not paint from the table: {tab!r}"
        row = picker.query_one("#theme-themes", ThemeList).children[0].render()
        note = row.get_style_at_offset(picker._note_col)
        assert note.foreground.hex.lower() == "#010203", (
            f"the note column did not paint from the table: {note!r}")
        await gen.aclose()

    asyncio.run(check())


def test_a_partial_theme_swatch_paints_the_packaged_defaults_and_never_crashes(serve, wait_until) -> None:
    """TT12 (ruled): a theme file may carry any subset of the twenty roles and the roles it lacks keep
    the packaged defaults. The picker's swatch must paint those **resolved** roles, not the file's own
    table: on 41800ac `swatch_styles` indexed `item.roles["steel"]`, so a user theme without `steel`
    raised KeyError inside the fill and the panel never showed the row.

    fails today: a partial user theme crashes the fill (KeyError), so the listing is never painted."""

    async def check() -> None:
        themes_dir = _user_dir() / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)
        (themes_dir / "partial.toml").write_text('[theme]\naccent = "#123456"\n', encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        ids = [t.id for t in picker._themes]
        assert "partial" in ids, "the partial theme is not in the list"
        row = picker.query_one("#theme-themes", ThemeList).children[ids.index("partial")].render()
        surface = row.get_style_at_offset(picker._swatch_col).background
        assert surface.hex.lower() == TERRACOTTA_SURFACE, (
            f"the swatch's background is not the packaged default surface: {surface!r}")
        accent = row.get_style_at_offset(picker._swatch_col + 1).foreground
        assert accent.hex.lower() == "#123456", f"the theme's own accent is not painted: {accent!r}"
        steel = row.get_style_at_offset(picker._swatch_col + 2).foreground
        assert steel.hex.lower() == parse_theme(packaged_dir("themes") / "terracotta.toml").roles["steel"], (
            f"the missing role is not the packaged default: {steel!r}")
        await gen.aclose()

    asyncio.run(check())


def test_using_an_edited_user_theme_again_reapplies_its_css_colours(serve, wait_until) -> None:
    # fails today: `_push_look` re-assigns `self.theme` only on a name change, so a user file edited while the
    # picker is open and Used again keeps the dialogs' CSS-backed colours on the old file.
    """TT13, B4 (Qodo round 2, ruling 1): a person edits a role in their active user theme file, then Uses it
    again. The rebuilt theme has the same id, so Textual never re-reads its $ variables; the dialogs' CSS-backed
    colours must be re-applied through Textual's own refresh, not left on the old file. If they stayed, the
    operator edits a theme and sees no change in any dialog."""

    async def check() -> None:
        cfg_dir = _user_dir()
        themes_dir = cfg_dir / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text('[theme]\nname = "my-lab"\n', encoding="utf-8")
        (themes_dir / "my-lab.toml").write_text('[theme]\nsurface = "#112233"\n', encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        assert app.cfg["theme.surface"] == "#112233", "the active user theme's surface did not load"
        await _open_picker(pilot, app, screen, wait_until)
        fills = _rect_fills(app.export_screenshot())
        assert any(f == "#112233" and x >= 66 for f, x in fills), "the panel is not the theme's surface"
        (themes_dir / "my-lab.toml").write_text('[theme]\nsurface = "#445566"\n', encoding="utf-8")
        # Use it again: the highlighted chrome row is `my-lab`, the pick in use leads the list.
        await pilot.press("enter")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs, "the picker to close")
        assert app.cfg["theme.surface"] == "#445566", "Use did not reload the edited role"
        # Reopen the picker: its panel background is the CSS `$surface`, so a re-applied theme shows the new colour.
        await pilot.press("ctrl+backslash", "T")
        await wait_until(pilot, lambda: isinstance(app.screen, ThemePicker), "the picker to reopen")
        await wait_until(pilot, lambda: _listing_is_painted(app.screen), "the listing to paint")
        fills = _rect_fills(app.export_screenshot())
        assert any(f == "#445566" and x >= 66 for f, x in fills), (
            "the dialog kept the old CSS surface after Use on an edited theme")
        assert not any(f == "#112233" and x >= 66 for f, x in fills), "the panel still shows the old surface"
        await gen.aclose()

    asyncio.run(check())


def test_a_scheme_only_preview_move_does_not_re_apply_the_css(serve, wait_until, monkeypatch) -> None:
    # fails today: `_push_look`'s same-name guard is always true after the switch branch sets `_theme_name`, so
    # every preview move, including a scheme-only one, runs a full CSS re-apply.
    """B4 (review round 1): a scheme-only preview move changes only the terminal palette; the chrome theme's
    content is unchanged, so its CSS variables must not be re-applied on every arrow key. If they were, each
    ↑↓ in the scheme list repaints the whole chrome."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        calls: list[int] = []
        real = app.refresh_css
        monkeypatch.setattr(app, "refresh_css", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        picker = await _open_picker(pilot, app, screen, wait_until)
        # The first preview move is the first `_push_look`, which legitimately refreshes once; establish that
        # baseline, then a scheme-only move on the unchanged chrome theme must not re-apply the CSS.
        _move(picker, "schemes", "dracula")
        calls.clear()
        _move(picker, "schemes", "solarized-dark")
        assert app.cfg["theme.term.scheme"] == "solarized-dark", "the scheme preview did not apply"
        assert calls == [], f"a scheme-only move re-applied the CSS: {len(calls)} refreshes"
        await gen.aclose()

    asyncio.run(check())


def test_a_real_chrome_switch_refreshes_only_through_textuals_watcher(serve, wait_until, monkeypatch) -> None:
    # fails today: the always-true same-name guard makes `_push_look` call `refresh_css` a second time on a real
    # chrome switch, on top of Textual's own watcher.
    """B4 (review round 1): a real chrome switch changes the theme name, so setting `self.theme` runs Textual's
    watcher, which schedules the CSS refresh; `_push_look` must not add a second explicit refresh. If it did, a
    switch repaints the chrome twice."""

    async def check() -> None:
        gen = _session(serve, wait_until)
        app, _, _ = await gen.__anext__()
        calls: list[int] = []
        real = app.refresh_css
        monkeypatch.setattr(app, "refresh_css", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        app.apply_look("nord", "tsb-default")  # a real chrome switch: terracotta -> nord
        assert app.cfg["theme.name"] == "nord", "the chrome switch did not apply"
        assert calls == [], f"_push_look added a second CSS refresh on a real switch: {len(calls)}"
        await gen.aclose()

    asyncio.run(check())


def test_the_chrome_list_shows_six_rows_on_the_laptop_and_scrolls_beyond(serve, wait_until) -> None:
    # fails today: `#theme-themes` has `height: auto` and no overflow, so with many user themes a highlighted row
    # can sit out of view, the list never scrolls, and on the phone it does not take the panel's height.
    """TT20, B5 (Qodo round 2, ruling 2): the chrome list shows at most 6 rows on the laptop layout and scrolls
    beyond that, so a highlight move keeps its row in view; on the phone, where one list shows at a time, it takes
    the panel's height and scrolls; the schemes list keeps its sizing. If the chrome list could not scroll, a
    person with many user themes highlights a row they cannot see."""

    async def check() -> None:
        themes_dir = _user_dir() / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)
        for n in range(8):
            (themes_dir / f"user{n}.toml").write_text(f'[theme]\nsurface = "#{n:02x}1122"\n', encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        themes_list = picker.query_one("#theme-themes", ThemeList)
        assert len(picker._themes) > 6, "the test did not load more than six themes"
        assert themes_list.styles.max_height.value == 6, (
            f"the chrome list is clamped to {themes_list.styles.max_height.value}, not 6")
        assert themes_list.styles.overflow_y == "auto", "the chrome list does not scroll"
        schemes_list = picker.query_one("#theme-schemes", ThemeList)
        # Parity with the schemes list, which shows its scrollbar (`overflow-y: auto`, default visibility).
        assert themes_list.styles.scrollbar_visibility == schemes_list.styles.scrollbar_visibility, (
            "the chrome list's scrollbar visibility does not match the schemes list")
        assert schemes_list.styles.max_height is None, (
            "the schemes list gained a max-height; ruling 2 says it keeps its sizing")
        # Move the highlight onto the last theme row: it sits below the six visible rows, so the list scrolls it in.
        _move(picker, "themes", picker._themes[-1].id)
        await pilot.pause(0.3)
        lines = [s.text for s in app.screen._compositor.render_strips()]
        assert any("▸" in line and picker._themes[-1].id in line for line in lines), (
            "the highlighted chrome row below six rows never scrolled into view")
        # On the phone, where one list shows at a time, the chrome list takes the panel's height and scrolls.
        await pilot.resize_terminal(65, 30)
        await pilot.pause(0.3)
        assert picker._compact is True, "the window did not become compact"
        assert picker.query_one("#theme-themes", ThemeList).styles.height.unit.name == "FRACTION", (
            "the phone chrome list does not take the panel's height")
        await gen.aclose()

    asyncio.run(check())


def test_a_broken_file_line_sits_inside_its_list_and_the_highlight_never_reaches_it(serve, wait_until) -> None:
    # fails today: the broken-file lines are siblings of the list, outside its scroll region, so they do not scroll
    # with it and `ThemeList` has no row count that excludes them.
    """TT10 (Qodo round 2, ruling 3): each catalogue's broken-file lines go inside that list's scroll region, after
    its last row, so they scroll with the list; `ThemeList.highlightable_rows()` excludes them, so the highlight
    never lands on one. If a broken line were outside the scroll region it would not move with a long list, and if
    it counted as a row the highlight could land on a file that cannot load."""

    async def check() -> None:
        themes_dir = _user_dir() / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)
        for n in range(8):
            (themes_dir / f"user{n}.toml").write_text(f'[theme]\nsurface = "#{n:02x}1122"\n', encoding="utf-8")
        (themes_dir / "broken.toml").write_text("[theme]\nsurface = notahex\n", encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        themes_list = picker.query_one("#theme-themes", ThemeList)
        broken = list(picker.query(".broken-theme"))
        assert broken, "no broken-file line shown"
        assert all(b.parent is themes_list for b in broken), "the broken line is outside the list's scroll region"
        children = list(themes_list.children)
        assert children[-len(broken):] == broken, "the broken line is not after the last item row"
        assert themes_list.highlightable_rows() == len(picker._themes), (
            "the highlight's row count counts the broken line")
        _move(picker, "themes", picker._themes[-1].id)
        assert picker.highlight("themes") == len(picker._themes) - 1, "the highlight is not on an item row"
        await gen.aclose()

    asyncio.run(check())


def test_a_failed_preview_leaves_the_highlight_on_the_row_that_is_previewed(serve, wait_until) -> None:
    # fails today: `set_highlight` stores the new index before `preview()`, so when a file fails to load the toast
    # shows but `_hl` points at the broken row while the ▸ stays on the previous one.
    """OPEN-T6 a (Qodo round 2, ruling 4): on a failed preview `_hl` stays at the previous index, where the ▸ is, so
    the next ↑/↓ moves from the visible row and the status note names what is actually previewed. If `_hl` advanced
    onto a file that cannot load, the note would name a scheme that is not in use and the next key move would step
    from a row the operator cannot see."""

    async def check() -> None:
        schemes_dir = _user_dir() / "schemes"
        schemes_dir.mkdir(parents=True, exist_ok=True)
        good = {"name": "My Lab", "ansi": ["#000000", "#ff0000", "#00ff00", "#ffff00", "#0000ff",
                                           "#ff00ff", "#00ffff", "#ffffff"] * 2,
                "foreground": "#d0d0d0", "background": "#101020"}
        path = schemes_dir / "my-lab.json"
        path.write_text(json.dumps(good), encoding="utf-8")
        gen = _session(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        # Preview a valid scheme (Dracula), then the file turns invalid; moving onto My Lab must fail.
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        assert app.cfg["theme.term.scheme"] == "dracula", "the valid preview did not apply"
        path.write_text(json.dumps({"name": "My Lab"}), encoding="utf-8")  # the file turns invalid
        _move(picker, "schemes", "my-lab")
        await pilot.pause(0.2)
        assert picker.highlight("schemes") == [s.slug for s in picker._schemes].index("dracula"), (
            "the highlight advanced onto the broken file; ruling 4 says _hl stays on the previewed row")
        toasts = _notifications(app)
        assert any("my-lab.json" in m and "ansi" in m for m in toasts), f"no toast naming the file: {toasts}"
        text = screen.status.text
        assert "previewing Dracula" in text, f"the note does not name the previewed scheme: {text!r}"
        assert "My Lab" not in text, f"the note names the broken file that is not previewed: {text!r}"
        await gen.aclose()

    asyncio.run(check())
