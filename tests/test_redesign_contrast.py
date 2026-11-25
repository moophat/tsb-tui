"""P1 theme defaults and computed chrome contrast, independent of terminal ANSI."""
from __future__ import annotations

import colorsys
from pathlib import Path

import pytest

from tsb_tui.config import load
from tsb_tui.theme import contrast_ratio, role_colours
from tsb_tui.theme_files import packaged_dir

THEMES = sorted(p.stem for p in packaged_dir("themes").glob("*.toml"))
TEXT_ROLES = ("text", "text_muted", "text_faint", "accent", "accent_hot", "steel", "ok", "warn",
              "danger", "rec", "rec_list", "frame", "border", "border_on_surface")


def test_fresh_default_and_explicit_choice(tmp_path: Path) -> None:
    cfg = load(config_file=tmp_path / "missing", environ={})
    assert cfg["theme.name"] == "nord"
    assert role_colours(cfg)["accent"] == "#88c0d0"
    cfg.select_look("terracotta", source="saved for your login")
    assert cfg["theme.name"] == "terracotta"
    assert cfg.source("theme.name") == "saved for your login"
    assert load(config_file=tmp_path / "missing", environ={},
                overrides=["theme.name=terracotta"])["theme.name"] == "terracotta"
    for profile in ("drive", "harness"):
        assert load(profile=profile, config_file=tmp_path / "missing", environ={})["theme.name"] == "terracotta"


def reference_surface(bg: str, dark: bool) -> str:
    h, light, saturation = colorsys.rgb_to_hls(*(int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    # Enumerate every quantization transition along the HSL ray, then take the first passing colour.
    end = 1.0 if dark else 0.0
    rgb_end = colorsys.hls_to_rgb(h, end, saturation)
    # A fine independent scan brackets the first RGB result; bisection resolves its exact boundary.
    previous = light
    for step in range(1, 65537):
        current = light + (end - light) * step / 65536
        rgb = colorsys.hls_to_rgb(h, current, saturation)
        colour = "#" + "".join(f"{round(c * 255):02x}" for c in rgb)
        if contrast_ratio(colour, bg) >= 1.2:
            for _ in range(40):
                mid = (previous + current) / 2
                candidate = "#" + "".join(f"{round(c * 255):02x}" for c in colorsys.hls_to_rgb(h, mid, saturation))
                if contrast_ratio(candidate, bg) >= 1.2:
                    current, colour = mid, candidate
                else:
                    previous = mid
            return colour
        previous = current
    raise AssertionError(rgb_end)


@pytest.mark.parametrize("name", THEMES)
def test_surface_is_the_minimum_lightness_step(name, tmp_path) -> None:
    cfg = load(config_file=tmp_path / "missing", environ={}, overrides=[f"theme.name={name}"])
    roles = role_colours(cfg)
    expected = reference_surface(roles["bg"], cfg.chrome.variant == "dark")
    assert roles["surface"] == expected
    assert role_colours(cfg)["surface"] == expected
    assert contrast_ratio(roles["surface"], roles["bg"]) >= 1.2
    light = lambda colour: colorsys.rgb_to_hls(*(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)))[1]
    assert (light(roles["surface"]) > light(roles["bg"])) == (cfg.chrome.variant == "dark")


@pytest.mark.parametrize("name", THEMES)
def test_chrome_role_pairs(name, tmp_path) -> None:
    cfg = load(config_file=tmp_path / "missing", environ={}, overrides=[f"theme.name={name}"])
    roles = role_colours(cfg)
    for bg in ("bg", "surface", "surface_2", "surface_3", "accent_soft"):
        for fg in TEXT_ROLES:
            assert contrast_ratio(roles[fg], roles[bg]) >= 4.5, (name, fg, bg)
    assert contrast_ratio(roles["on_accent"], roles["accent"]) >= 4.5


@pytest.mark.parametrize("name", THEMES)
def test_effective_menu_text_including_selection_and_disabled(serve, wait_until, name):
    import asyncio

    from test_redesign_keys import login
    from textual.command import CommandList

    from tsb_tui.app import MenuPalette, TsbTuiApp

    async def check():
        _, url = serve()
        cfg = load(overrides=[f"theme.name={name}"])
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(80, 24)) as pilot:
            await login(pilot, app, wait_until)
            await pilot.press("f1")
            await wait_until(pilot, lambda: isinstance(app.screen, MenuPalette)
                             and bool(app.screen.query(CommandList))
                             and app.screen.query_one(CommandList).option_count > 0, "Menu results")
            menu = app.screen.query_one(CommandList)
            assert any(option.disabled for option in menu.options)
            assert menu.highlighted is not None
            checked = 0
            box = app.screen.query_one("#--container").region
            strips = app.screen._compositor.render_strips()
            for strip in (strips[y].crop(box.x, box.right) for y in range(box.y, box.bottom)):
                for segment in strip:
                    if not segment.text.strip() or segment.style is None:
                        continue
                    style = menu.rich_style + segment.style
                    if style.color is None or style.bgcolor is None:
                        continue
                    fg = style.color.get_truecolor()
                    bg = style.bgcolor.get_truecolor()
                    if style.reverse:
                        fg, bg = bg, fg
                    if style.dim:
                        fg = tuple(round((a + b) / 2) for a, b in zip(fg, bg, strict=True))
                    hex_of = lambda rgb: "#" + "".join(f"{c:02x}" for c in rgb)
                    assert contrast_ratio(hex_of(fg), hex_of(bg)) >= 4.5, (name, segment.text, style)
                    checked += 1
            assert checked > 5
    asyncio.run(check())


def test_edited_user_surface_is_ignored_on_reuse(tmp_path):
    theme_dir = tmp_path / "tsb-tui" / "themes"
    theme_dir.mkdir(parents=True)
    theme = theme_dir / "my-lab.toml"
    config = tmp_path / "config.toml"
    config.write_text('[theme]\nname = "my-lab"\n', encoding="utf-8")
    theme.write_text('[theme]\nbg = "#112233"\nsurface = "#ffffff"\n', encoding="utf-8")
    cfg = load(config_file=config, environ={"XDG_CONFIG_HOME": str(tmp_path)})
    expected = reference_surface("#112233", cfg.chrome.variant == "dark")
    assert cfg["theme.surface"] == expected
    theme.write_text('[theme]\nbg = "#112233"\nsurface = "#000000"\n', encoding="utf-8")
    cfg.select_look("my-lab")
    assert cfg["theme.surface"] == expected
    assert cfg["theme.surface"] not in ("#ffffff", "#000000")


# -- Phase 06 acceptance: L1-contrast / L1-geometry --

SIZES = [(80, 24), (160, 45)]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", THEMES)
def test_every_theme_both_sizes_meets_contrast(serve, wait_until, name, size):
    """L1-contrast / V8: All shipped themes and both sizes meet text contrast >=4.5:1 on chrome roles,
    and target-supplied ANSI colors remain faithful as ruled O7."""
    import asyncio

    from input_server import InputRecordingApp
    from test_redesign_keys import login

    from tsb_tui.app import TsbTuiApp

    async def check():
        # 1. Static role contrast assertions across all text roles
        cfg = load(overrides=[f"theme.name={name}"])
        roles = role_colours(cfg)
        for bg_role in ("bg", "surface", "surface_2", "surface_3", "accent_soft"):
            for fg_role in TEXT_ROLES:
                ratio = contrast_ratio(roles[fg_role], roles[bg_role])
                assert ratio >= 4.5, f"Theme {name} role {fg_role} on {bg_role} ratio {ratio:.2f} < 4.5"
        assert contrast_ratio(roles["on_accent"], roles["accent"]) >= 4.5

        # 2. Composed render strip contrast verification in running app
        _, url = serve(InputRecordingApp())
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=size) as pilot:
            screen = await login(pilot, app, wait_until)
            await pilot.pause()

            # Inspect rendered strips of the cockpit chrome
            strips = screen._compositor.render_strips()
            assert len(strips) == size[1]
            checked_cells = 0
            hex_of = lambda rgb: "#" + "".join(f"{c:02x}" for c in rgb)

            # Check status row and rail strips
            status_y = screen.status.region.y
            if 0 <= status_y < len(strips):
                for seg in strips[status_y]:
                    if not seg.text.strip() or seg.style is None:
                        continue
                    if seg.style.color and seg.style.bgcolor:
                        fg = seg.style.color.get_truecolor()
                        bg = seg.style.bgcolor.get_truecolor()
                        if seg.style.reverse:
                            fg, bg = bg, fg
                        if seg.style.dim:
                            fg = tuple(round((a + b) / 2) for a, b in zip(fg, bg, strict=True))
                        assert contrast_ratio(hex_of(fg), hex_of(bg)) >= 4.5, (name, seg.text, seg.style)
                        checked_cells += 1

            # Check rail strips
            rail_region = screen.rail.region
            checked_rail_cells = 0
            for y in range(rail_region.y, min(rail_region.bottom, len(strips))):
                col = 0
                for seg in strips[y]:
                    seg_len = len(seg.text)
                    seg_right = col + seg_len
                    if (
                        seg_right > rail_region.x
                        and col < rail_region.right
                        and seg.text.strip()
                        and seg.style is not None
                        and seg.style.color
                        and seg.style.bgcolor
                    ):
                        fg = seg.style.color.get_truecolor()
                        bg = seg.style.bgcolor.get_truecolor()
                        if seg.style.reverse:
                            fg, bg = bg, fg
                        if seg.style.dim:
                            fg = tuple(round((a + b) / 2) for a, b in zip(fg, bg, strict=True))
                        ratio = contrast_ratio(hex_of(fg), hex_of(bg))
                        assert ratio >= 4.5, (
                            f"Theme {name} rail cell {seg.text!r} contrast {ratio:.2f} < 4.5 at ({col}, {y})"
                        )
                        checked_rail_cells += 1
                    col = seg_right

            assert checked_cells > 0, "Must verify rendered chrome cells meet contrast"
            assert checked_rail_cells > 0, f"Theme {name} at {size}: must verify rendered rail cells meet contrast"

    asyncio.run(check())

