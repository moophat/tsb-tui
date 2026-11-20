"""Round 2's protected hints, drop order and distinct typist colours."""
from __future__ import annotations

import colorsys

import pytest
from model_frames import state_of
from test_status_row import ME, ME_USER, PEER, PEER_USER, THREE, _inputs

from tsb_tui.config import load
from tsb_tui.theme import contrast_ratio, role_colours
from tsb_tui.theme_files import packaged_dir
from tsb_tui.widgets.status_row import Hint, KeysTarget, build_pieces


def owner_inputs(**overrides):
    state = state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)
    return _inputs(state, leader="Ctrl+\\", **overrides)


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("target", [None, KeysTarget("targets", (Hint("n", "New session", "new_session"),)),
                                   KeysTarget("full view", (Hint("⏎", "Open", "pick"),))])
def test_protected_menu_and_keys_at_ruled_sizes(width, target):
    # The row the product draws is keys only (the pill's facts are the session frame's title); with Detach protected too
    # the five protected hints and the pill would not fit 80 columns, and the pill is not on the row.
    pieces = build_pieces(owner_inputs(target=target), width, keys_only=True)
    text = "".join(p.text for p in pieces)
    assert "F1 Menu" in text
    assert "Ctrl+\\ ? keys" in text
    assert len(text) == width
    if target is None:
        assert {"take_back", "scroll"} <= {p.action for p in pieces}


def test_list_primary_action_outlasts_last_ordinary_action():
    target = KeysTarget("targets", (Hint("n", "New session", "new_session"),
                                    Hint("l", "Save login with a long label", "save")))
    pieces = build_pieces(owner_inputs(target=target), 80)
    actions = {p.action for p in pieces}
    assert "new_session" in actions
    assert "save" not in actions


def test_narrow_protected_hints_drop_in_ruled_order():
    seen = []
    for width in range(80, 29, -1):
        pieces = build_pieces(owner_inputs(), width)
        actions = {p.action for p in pieces}
        kept = tuple(name for name in ("scroll", "keys", "detach", "take_back", "menu") if name in actions)
        if not seen or kept != seen[-1]:
            seen.append(kept)
    assert seen == [("scroll", "keys", "detach", "take_back", "menu"), ("keys", "detach", "take_back", "menu"),
                    ("detach", "take_back", "menu"), ("take_back", "menu"), ("menu",)]
    pieces = build_pieces(owner_inputs(), 65)
    assert not {"scroll", "keys"} & {p.action for p in pieces}
    assert {"detach", "take_back", "menu"} <= {p.action for p in pieces}
    assert len("".join(p.text for p in pieces)) == 65


@pytest.mark.parametrize("name", sorted(p.stem for p in packaged_dir("themes").glob("*.toml")))
def test_typist_colours_have_distinct_hues_and_chrome_contrast(name, tmp_path):
    roles = role_colours(load(config_file=tmp_path / "missing", environ={}, overrides=[f"theme.name={name}"]))
    hue = lambda c: colorsys.rgb_to_hls(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0] * 360
    separation = abs(hue(roles["accent"]) - hue(roles["steel"]))
    assert min(separation, 360 - separation) >= 30, (name, separation)
    for fg in ("accent", "steel"):
        for bg in ("bg", "surface", "surface_2", "surface_3", "accent_soft"):
            assert contrast_ratio(roles[fg], roles[bg]) >= 4.5, (name, fg, bg)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
def test_real_session_targets_and_full_view_keep_menu_and_keys(serve, wait_until, size):
    import asyncio

    from input_server import attached
    from stub_server import create_owner_session
    from tsbc import _serve

    from tsb_tui.app import TsbTuiApp
    from tsb_tui.screens.fleet_view import TAB_TARGETS

    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            sid = create_owner_session(app, _serve.TARGET, 80, 24)
            workspace = await attached(pilot, app, sid, wait_until)
            for context in ("session", "full", "targets"):
                if context == "full":
                    workspace.open_fleet_view()
                elif context == "targets":
                    workspace.fleet_view.tab = TAB_TARGETS
                    workspace.fleet_view.focus()
                workspace.refresh_status()
                await pilot.pause()
                assert "F1 Menu" in workspace.status.text, context
                assert "Ctrl+\\ ? keys" in workspace.status.text, context
                assert workspace.status.region.width <= size[0]
    asyncio.run(check())


def test_special_row_keeps_pre_round2_hint_drop_order():
    from tsb_tui.widgets.status_row import EndedStatus

    ended = EndedStatus("ended session with a reason", False,
                        (Hint("c", "New session", "new_session"),
                         Hint("n", "Next session with a long label", "next")))
    pieces = build_pieces(owner_inputs(ended=ended), 65)
    assert "new_session" not in {p.action for p in pieces}
    assert "next" in {p.action for p in pieces}


def test_nord_steel_is_the_first_passing_hsl_lightness_step(tmp_path):
    roles = role_colours(load(config_file=tmp_path / "missing", environ={}, overrides=["theme.name=nord"]))
    h, light, saturation = colorsys.rgb_to_hls(*(int("b48ead"[i:i + 2], 16) / 255 for i in (0, 2, 4)))
    colour = lambda value: "#" + "".join(f"{round(c * 255):02x}" for c in colorsys.hls_to_rgb(h, value, saturation))
    passes = lambda value: all(contrast_ratio(colour(value), roles[bg]) >= 4.5
                               for bg in ("bg", "surface", "surface_2", "surface_3", "accent_soft"))
    previous = light
    for step in range(65537):
        current = light + (1 - light) * step / 65536
        if passes(current):
            for _ in range(40):
                mid = (previous + current) / 2
                if passes(mid):
                    current = mid
                else:
                    previous = mid
            assert roles["steel"] == colour(current)
            assert roles["accent"] == "#88c0d0"
            break
        previous = current
    else:
        pytest.fail("Nord purple has no passing lightness")
