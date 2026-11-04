"""Switching the look at run time (design B4, TT12, TT14, TT15): a session drawn after a switch is the
same picture as one started with that look.

Why these matter (Rule 9): the picker previews and applies a chrome theme and a terminal scheme while the
operator watches. A canvas that keeps its cached strips after a palette swap shows the old colours on rows
whose content did not change, so a switch looks half-applied; a canvas behind must take the new colours when
it comes forward, not before, or a background session flashes at the wrong moment; a chrome table built once
in `__init__` keeps the start-up roles after a switch. The first two tests are the invariant the parked
latency plan must keep green: a palette swap rebuilds every kept row's strip and asks for a paint although
no row's content key changed, and reads no engine, so a held frame (mode 2026) keeps its last frame in the
new colours.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from input_server import InputRecordingApp, other_client
from textual.app import App
from tsbc import _serve
from workspace_support import fixture_login, named_sessions, open_here

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.term import VTBackend, load
from tsb_tui.theme import TermPalette
from tsb_tui.theme_files import SchemeError, find_scheme, parse_scheme, parse_theme
from tsb_tui.widgets.canvas import Canvas

SCHEME_DIR = Path(__file__).resolve().parents[1] / "tsb_tui" / "schemes"
THEME_DIR = Path(__file__).resolve().parents[1] / "tsb_tui" / "themes"


@pytest.fixture(scope="module")
def lib(term_so: Path):
    return load(term_so)


def _hex(colour) -> str | None:
    """A colour as `#rrggbb`, from Textual's `Color(r, g, b)` (a widget's `styles.background`) or Rich's
    `Color` (a strip's run style), neither of which carries `.hex`."""
    if colour is None:
        return None
    if hasattr(colour, "r") and hasattr(colour, "g") and hasattr(colour, "b"):
        r, g, b = colour.r, colour.g, colour.b
    else:
        t = colour.triplet
        r, g, b = t.red, t.green, t.blue
    return f"#{r:02x}{g:02x}{b:02x}"


class _Host(App):
    def __init__(self, canvas: Canvas) -> None:
        super().__init__()
        self._canvas = canvas

    def compose(self):
        yield self._canvas


def _cfg(**overrides: str) -> config.Config:
    return config.load(config_file=Path("/nonexistent"), environ={},
                       overrides={"size.auto_request": "false", **overrides})


def _canvas(lib, cols: int, rows: int, *, typist: bool = False, **overrides: str) -> Canvas:
    cfg = _cfg(**overrides)
    backend = VTBackend(lib, cols, rows, scrollback_bytes=cfg["term.scrollback_bytes"])
    return Canvas(backend, TermPalette(cfg, cfg.scheme), cfg, typist=typist)


async def _first_paint(pilot, canvas: Canvas) -> None:
    for _ in range(300):
        if canvas.paint_count >= 1:
            return
        await pilot.pause(0.01)
    raise AssertionError("the canvas never painted")


def _red_run_hex(strip) -> str | None:
    """The colour of the run that covers column 0 (the red cell fed at row start)."""
    column = 0
    for segment in strip:
        if column == 0:
            return _hex(segment.style.color)
        column += segment.cell_length
    return None


def _padding_bg_hex(strip) -> str | None:
    """The background of the trailing padding run (the default cell the row is widened to)."""
    last = list(strip)[-1]
    return _hex(last.style.bgcolor)


def test_a_swap_restyles_every_row_although_no_content_changed(lib) -> None:
    # fails today: Canvas has no set_palette; assigning _palette leaves the cached strips in the old colours.
    """TT15, the invariant the latency plan must keep green: a palette swap rebuilds every kept row's strip
    and repaints although no row's content key changed, and reads no engine — so a held frame keeps its last
    frame in the new colours. Feed a red cell on every row, paint, then swap to Dracula: with no new output
    every row's render_line carries Dracula's red and its padding Dracula's background, and `paint_count`
    is unchanged (the engine was not read)."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            # a red (ANSI 1) cell at column 0 of every row, then one paint that caches every row.
            canvas.feed(b"".join(f"\x1b[{i + 1};1H\x1b[31mR{i}".encode() for i in range(10)))
            canvas.repaint_now()
            paints = canvas.paint_count
            dracula = TermPalette(_cfg(), parse_scheme(SCHEME_DIR / "dracula.json"))
            canvas.set_palette(dracula)
            assert canvas.paint_count == paints, "set_palette read the engine"
            for y in range(10):
                strip = canvas.render_line(y)
                assert _red_run_hex(strip) == dracula.scheme.ansi[1], (
                    f"row {y} is not Dracula's red after the swap")
                assert _padding_bg_hex(strip) == dracula.term_bg, (
                    f"row {y}'s padding is not Dracula's background after the swap")

    asyncio.run(_check())


def test_a_canvas_behind_takes_the_new_colours_when_it_comes_to_the_front(lib) -> None:
    # fails today: Canvas has no set_palette, so a background canvas never rebuilds its strips.
    """TT15: a canvas out of front marks itself and rebuilds in `bring_to_front`, not at the swap — its kept
    rows and their strips are untouched until it comes forward, so a background session does not repaint at
    the wrong moment."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            canvas.feed(b"".join(f"\x1b[{i + 1};1H\x1b[31mR{i}".encode() for i in range(10)))
            canvas.repaint_now()
            built = list(canvas._strips)
            rows_before = list(canvas._rows)
            canvas.send_to_back()
            dracula = TermPalette(_cfg(), parse_scheme(SCHEME_DIR / "dracula.json"))
            canvas.set_palette(dracula)
            assert canvas._strips == built, "a background canvas rebuilt its strips at the swap"
            assert canvas._rows == rows_before, "a background canvas dropped its kept rows"
            assert all(s is built[y] for y, s in enumerate(canvas._strips)), "a strip was rebuilt behind"

            canvas.bring_to_front()
            assert any(canvas._strips[y] is not built[y] for y in range(10)), (
                "the strips were not rebuilt when it came to the front")
            for y in range(10):
                assert _red_run_hex(canvas.render_line(y)) == dracula.scheme.ansi[1], (
                    f"row {y} is not Dracula's red once it is in front")

    asyncio.run(_check())


def test_the_canvas_background_follows_term_bg(lib) -> None:
    # fails today: Canvas has no set_palette, so the inline background never follows a scheme swap.
    """TT15, B1: `set_palette` moves the canvas's own background to the new scheme's `background`, and clears
    the inline rule when the palette has no `term_bg` (theme.term.bg = "outer"), so the canvas is see-through
    again and the CSS `$background` applies."""

    async def _check() -> None:
        canvas = _canvas(lib, 40, 10)
        async with _Host(canvas).run_test(size=(40, 10)) as pilot:
            await _first_paint(pilot, canvas)
            dracula = TermPalette(_cfg(), parse_scheme(SCHEME_DIR / "dracula.json"))
            canvas.set_palette(dracula)
            assert _hex(canvas.styles.background) == dracula.term_bg, (
                f"the canvas background is {_hex(canvas.styles.background)}, not Dracula's {dracula.term_bg}")

            outer = TermPalette(_cfg(**{"theme.term.palette": "outer", "theme.term.bg": "outer"}),
                                parse_scheme(SCHEME_DIR / "dracula.json"))
            assert outer.term_bg is None, "an outer palette paints no background"
            canvas.set_palette(outer)
            assert _hex(canvas.styles.background) != dracula.term_bg, (
                "the inline background stayed at Dracula, so the canvas is not see-through")

    asyncio.run(_check())


def test_a_switch_keeps_what_a_higher_layer_set(tmp_path) -> None:
    # fails today: Config has no select_look / look / restore_look.
    """TT12: a switch moves every role whose source is the packaged default or a theme file to the new theme,
    and leaves a role a higher layer set. A config file sets `theme.accent`; `select_look("nord")` leaves it
    and its source, moves every other role to Nord's; `restore_look` puts back values and sources."""

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[theme]\naccent = "#123456"\n', encoding="utf-8")
    cfg = config.load(config_file=cfg_path, environ={})
    terracotta_muted = cfg["theme.text_muted"]
    saved = cfg.look()

    cfg.select_look("nord")
    nord = parse_theme(THEME_DIR / "nord.toml")
    assert cfg["theme.accent"] == "#123456", "a config-file role moved with the theme"
    assert cfg.source("theme.accent") == f"config file {cfg_path}", "the config-file source was overwritten"
    assert cfg["theme.text_muted"] == nord.roles["text_muted"], "a default role did not follow Nord"
    assert cfg.source("theme.text_muted") == "theme nord", "the switched role lost its theme source"

    cfg.restore_look(saved)
    assert cfg["theme.accent"] == "#123456" and cfg.source("theme.accent") == f"config file {cfg_path}"
    assert cfg["theme.text_muted"] == terracotta_muted, "restore did not put the role back"


def test_a_bad_look_changes_nothing(tmp_path) -> None:
    # fails today: Config has no select_look, so a bad name cannot raise.
    """TT12, Rule 12: a switch to a theme that is not found raises `ConfigError` and changes nothing — every
    value and source is as it was."""

    cfg = config.load(config_file=Path("/nonexistent"), environ={})
    before = cfg.look()
    with pytest.raises(config.ConfigError):
        cfg.select_look("nosuch")
    after = cfg.look()
    assert after["name"] == before["name"] and after["scheme_name"] == before["scheme_name"]
    assert after["roles"] == before["roles"], "a failed switch changed a role value or source"


def test_a_failed_unsave_leaves_the_look_unchanged(monkeypatch) -> None:
    # fails today: unsave_names applies the theme fallback, then the scheme fallback. If the scheme fallback
    # raises, the theme name, chrome, roles and sources are already switched, so cfg and the screen disagree.
    """A failed restore must not leave half a look. Both names carry a `saved for x` source; unsave_names
    applies the theme fallback first, then the scheme fallback. When the scheme fallback raises, the config
    keeps the first login's look for the next person, and the app toasts and returns with cfg ahead of the
    screen. unsave_names captures the look and restores it on ConfigError, so a failure changes nothing."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={})
    cfg.select_look("nord", "dracula", source="saved for x")
    assert cfg["theme.name"] == "nord" and cfg["theme.term.scheme"] == "dracula"
    before = cfg.look()

    def boom(slug, user_dir):
        if slug == cfg._defaults["theme.term.scheme"]:
            raise SchemeError(f"scheme {slug!r} is not found")
        return find_scheme(slug, user_dir)

    monkeypatch.setattr(config, "find_scheme", boom)
    with pytest.raises(config.ConfigError):
        cfg.unsave_names()

    after = cfg.look()
    assert after["name"] == before["name"], "the theme name and its source were left switched"
    assert after["scheme_name"] == before["scheme_name"], "the scheme name and its source were left switched"
    assert after["roles"] == before["roles"], "a role value or source leaked from the half-applied look"
    assert after["chrome"] == before["chrome"], "chrome is not the look in force before the call"
    assert after["scheme"] == before["scheme"], "scheme is not the scheme in force before the call"


def test_pilot_a_switch_at_run_time_repaints_the_chrome_and_the_canvases(serve, wait_until) -> None:
    # fails today: TsbTuiApp has no apply_look, so the widgets and canvas keep the start-up look.
    """B4 in the running app: `app.apply_look("paper", "solarized-light")` gives the rail, sidebar and status
    row paper's roles, paints the front canvas Solarized Light's background at once, and the second session's
    canvas the same once `show_front` brings it; Who's here opened afterwards is framed in paper's frame."""

    async def _check() -> None:
        serve_app, url = serve(InputRecordingApp())
        first, second = named_sessions(url, serve_app, "rtr-core-01", "infra-db-02")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            screen.close_sidebar()
            paper = parse_theme(THEME_DIR / "paper.toml")
            solarized = parse_scheme(SCHEME_DIR / "solarized-light.json")

            app.apply_look("paper", "solarized-light")
            await pilot.pause(0.1)

            for widget, key in ((screen.rail, "blank"), (screen.sidebar, "blank"), (screen.status, "text")):
                assert _hex(widget._styles[key].bgcolor) == paper.roles["surface"], (
                    f"{type(widget).__name__} kept the start-up surface, not paper's")

            front_slot = screen.slot_for(second)
            assert _hex(front_slot.canvas.styles.background) == solarized.background, (
                "the front canvas is not Solarized Light's background at once")

            back_slot = screen.slot_for(first)
            screen.show_front(back_slot)
            await pilot.pause(0.1)
            assert _hex(back_slot.canvas.styles.background) == solarized.background, (
                "the second session's canvas did not take the new background when it came to the front")

            with other_client(url, _serve.PEER, back_slot.session_id):
                await wait_until(pilot, lambda: len(back_slot.pump.state.clients) >= 2, "a peer client")
                await screen.run_action_id("who")
                await wait_until(pilot, lambda: app.screen.query("#whos-panel"), "the Who's here dialog")
                frame = app.screen.query_one("#whos-panel").styles.border_top[1].hex.lower()
                assert frame == paper.roles["frame"], (
                    f"Who's here is framed in {frame}, not paper's frame {paper.roles['frame']}")

    asyncio.run(_check())
