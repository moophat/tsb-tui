"""Regression coverage for the seven ruled P1 review findings."""
from __future__ import annotations

import asyncio

import pytest
from test_redesign_keys import login

from tsb_tui.app import MenuPalette, TsbTuiApp
from tsb_tui.config import load
from tsb_tui.screens.chrome_dialogs import FLEET_VIEW_KEYS, SIDEBAR_KEYS, TextScreen
from tsb_tui.screens.new_session import TARGET_REQUIRED, NewSessionScreen
from tsb_tui.theme import contrast_ratio, surface_colour
from tsb_tui.widgets.status_row import RANK_NAME, Item, Piece, _fit


def test_menu_shortcut_over_settings_uses_workspace(serve, wait_until):
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test() as pilot:
            workspace = await login(pilot, app, wait_until)
            await workspace.run_action_id("settings")
            await wait_until(pilot, lambda: isinstance(app.screen, TextScreen), "Settings")
            # Shortcut mode is selected by pending leader state; preserve that context across F1.
            workspace._leader_pending = True
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette), "Menu over Settings")
            assert not app.screen.search_mode
            await pilot.press("c")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "shortcut action")
    asyncio.run(check())


@pytest.mark.parametrize("layer", ["file", "environment", "command line"])
def test_surface_uses_final_background(tmp_path, layer):
    config = tmp_path / "config.toml"
    config.write_text('[theme]\nname="nord"\n' + ('bg="#112233"\n' if layer == "file" else ''))
    cfg = load(config_file=config,
               environ={"TSB_TUI_THEME_BG": "#112233"} if layer == "environment" else {},
               overrides=["theme.bg=#112233"] if layer == "command line" else [])
    assert cfg["theme.surface"] == surface_colour("#112233", cfg.chrome.variant)


@pytest.mark.parametrize("layer", ["file", "environment", "command line"])
def test_surface_override_is_ignored_on_load_and_switch(tmp_path, layer):
    config = tmp_path / "config.toml"
    config.write_text('[theme]\n' + ('surface="#ffffff"\n' if layer == "file" else ''))
    cfg = load(config_file=config,
               environ={"TSB_TUI_THEME_SURFACE": "#ffffff"} if layer == "environment" else {},
               overrides=["theme.surface=#ffffff"] if layer == "command line" else [])
    assert cfg["theme.surface"] == surface_colour(cfg["theme.bg"], cfg.chrome.variant)
    cfg.select_look("terracotta")
    assert cfg["theme.surface"] == surface_colour(cfg["theme.bg"], cfg.chrome.variant)


def test_alerts_hint_matches_new_session_key(serve, wait_until):
    from tsb_tui.screens.fleet_view import TAB_ALERTS

    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            workspace = await login(pilot, app, wait_until)
            await pilot.press("ctrl+backslash", "F")
            await pilot.pause()
            workspace.fleet_view.tab = TAB_ALERTS
            workspace.fleet_view.focus()
            hint = next((h for h in workspace._fleet_view_keys_target().hints if h.key == "n"), None)
            assert hint is not None and hint.label == "New session"
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "Alerts n")
    asyncio.run(check())


def test_name_retries_after_dropping_hint_and_never_becomes_bare_ellipsis():
    left = [Item("text", ("a-long-session-name",), RANK_NAME)]
    hints = [("low", [Piece("l Low", "hint")]), ("menu", [Piece("F1 Menu", "hint")])]
    pieces, kept = _fit(left, hints, ["low"], 19)
    assert [name for name, _ in kept] == ["menu"]
    assert "".join(p.text for p in pieces) == "a-long-se…"
    for width in range(9, 40):
        pieces, _ = _fit(left, hints, ["low"], width)
        name = "".join(p.text for p in pieces)
        assert not name.endswith("…") or len(name) >= 5


def test_help_names_n_new_session_and_a_add_target():
    assert ("n", "New session") in SIDEBAR_KEYS
    assert ("n", "New session") in FLEET_VIEW_KEYS
    assert ("a", "Targets tab: Add target (needs target.edit)") in FLEET_VIEW_KEYS
    assert not any(key == "c" and "new session" in label.lower() for key, label in FLEET_VIEW_KEYS)
    assert "Ctrl+\\ F" in TARGET_REQUIRED and "^\\" not in TARGET_REQUIRED


@pytest.mark.parametrize(("variant", "background"), [("dark", "#ffffff"), ("light", "#000000")])
def test_user_theme_falls_back_to_opposite_lightness_direction(tmp_path, variant, background):
    themes = tmp_path / "tsb-tui" / "themes"
    themes.mkdir(parents=True)
    (themes / "opposite.toml").write_text(f'[theme]\nvariant="{variant}"\nbg="{background}"\n')
    cfg = load(config_file=tmp_path / "missing", environ={"XDG_CONFIG_HOME": str(tmp_path)},
               overrides=["theme.name=opposite"])
    assert contrast_ratio(cfg["theme.surface"], background) >= 1.2
    cfg.select_look("opposite")
    assert contrast_ratio(cfg["theme.surface"], background) >= 1.2


def test_unreachable_surface_is_refused_with_background(monkeypatch):
    from tsb_tui import theme

    monkeypatch.setattr(theme, "contrast_ratio", lambda fg, bg: 1.0)
    with pytest.raises(theme.ThemeError, match="background #112233.*choose another background"):
        theme.surface_colour("#112233", "dark")


def test_theme_refusal_names_theme_and_background(tmp_path, monkeypatch):
    from tsb_tui import theme_files
    from tsb_tui.theme import ThemeError

    path = tmp_path / "broken.toml"
    path.write_text('[theme]\nbg="#112233"\n')
    def refused(bg, variant):
        raise ThemeError(f"background {bg}: cannot derive a surface; choose another background")
    monkeypatch.setattr(theme_files, "surface_colour", refused)
    with pytest.raises(theme_files.ThemeFileError, match="theme 'broken'.*#112233.*choose another background"):
        theme_files.parse_theme(path, yours=True)


@pytest.mark.parametrize("width", [65, 120])
def test_keyboard_hints_outlast_name_and_ordinary_hints(width):
    from model_frames import state_of
    from test_status_row import ME, ME_USER, PEER, PEER_USER, THREE, _inputs

    from tsb_tui.widgets.status_row import build_pieces

    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    pieces = build_pieces(_inputs(state), width)
    actions = {p.action for p in pieces}
    assert {"take_back", "scroll"} <= actions
    assert not any(p.text.startswith("rtr-") for p in pieces)
    assert {"menu", "keys"} <= actions
    assert len("".join(p.text for p in pieces)) == width
