"""The new chrome roles at their sites (design B3, §1.4-§1.7): `frame`, `rec`, `rec_list`, and the
hairline on `surface`.

Why these matter (Rule 9): the accent means "you type" (design §2.8), so a dialog frame drawn in it
says something false; the status row's `● REC` is a live recording fact while REC on a sidebar or
fleet row is a column mark, and one colour for both blurs the two; a hairline drawn in the `border`
colour vanishes on `surface`, where the rail and sidebar draw it. Each role is its own knob, so a
theme file can set each one.
"""

from __future__ import annotations

import asyncio
import pathlib

from input_server import InputRecordingApp
from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.fleet_view import FleetView
from tsb_tui.widgets.rail import Rail
from tsb_tui.widgets.sidebar import Sidebar
from tsb_tui.widgets.status_row import StatusRow


def _style_hex(style) -> str:
    """The style's foreground colour as `#rrggbb` (Rich's `Color` has no `.hex`)."""
    t = style.color.triplet
    return f"#{t.red:02x}{t.green:02x}{t.blue:02x}"


def test_the_login_panel_is_framed_in_the_frame_role_not_the_accent(serve, wait_until) -> None:
    # fails today: $primary is the accent.
    """The login panel is framed in `frame`, not the accent: the accent means "you type" (design §2.8),
    and a frame in it says something false before anyone has typed anything."""

    async def _check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_until(pilot, lambda: app.screen.query("#login-panel"), "the login panel")
            border = app.screen.query_one("#login-panel").styles.border_top[1].hex.lower()
            assert border == app.cfg["theme.frame"], f"the frame is {border}, not the frame role"
            assert border != app.cfg["theme.accent"], "the accent says 'you type'; a frame must not wear it"

    asyncio.run(_check())


def test_each_role_knob_drives_its_site() -> None:
    # fails today: theme.rec is an unknown key.
    """Each role knob drives its site: the status row's REC takes `theme.rec`, the sidebar and fleet
    rows take `theme.rec_list`, and every hairline on `surface` (rail, sidebar, fleet view) takes
    `theme.border_on_surface` — three distinct colours, so a theme can quiet the list REC and brighten
    the hairlines without touching the accent or `theme.border`."""
    cfg = config.load(overrides=["theme.rec=#123456", "theme.rec_list=#234567",
                                 "theme.border_on_surface=#345678"])
    leader = cfg["keys.leader"]
    assert _style_hex(StatusRow(cfg)._styles["rec"]) == "#123456"
    sidebar = Sidebar(cfg, leader=leader)
    fleet = FleetView(cfg, leader=leader)
    assert _style_hex(sidebar._styles["rec"]) == "#234567"
    assert _style_hex(fleet._styles["rec"]) == "#234567"
    for style in (Rail(cfg)._styles["hairline"], Rail(cfg)._border,
                  sidebar._styles["hairline"], sidebar._border, fleet._styles["hairline"]):
        assert _style_hex(style) == "#345678"


def test_no_stylesheet_names_the_accent_frame_or_the_derived_hover_any_more() -> None:
    # fails today: eight sites name them.
    """No dialog stylesheet names `$primary` or `$surface-lighten-1` any more, so a theme's `frame` and
    `surface_3` reach every dialog: the accent frame and the hover derived from `surface` ignored both
    knobs, and a theme file could not change them."""
    pkg_dir = pathlib.Path(__file__).resolve().parents[1] / "tsb_tui"
    sources = "\n".join(p.read_text(encoding="utf-8") for p in pkg_dir.rglob("*.py"))
    named = [token for token in ("$primary", "$surface-lighten-1") if token in sources]
    assert not named, f"{named} is still named in a stylesheet under {pkg_dir}"
