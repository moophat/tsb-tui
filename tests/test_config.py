"""Configuration: precedence, profiles, the operator's rulings, and the knob registry.

Why these matter (Rule 9):

  * An unknown key or a bad type must fail start-up *naming the key* (Rule 12). A silently ignored
    typo in `~/.config/tsb-tui/config.toml` would leave the operator believing a setting took
    effect when it never did.
  * OPEN-1 was ruled on 2026-10-12: auto size is ON by default. The default profile ships
    `size.auto_request = true`; the phone, drive and harness profiles keep it off.
  * D52 was ruled on 2026-10-12: the input guard stays on everywhere, the harness profile included.
    No profile ships a "no value", and start-up never names an open item.
  * Every key in `config_default.toml` must be read by code outside `config.py` (DT19), or a knob
    is a promise nothing keeps. The registry names the owner phase; a key whose phase has not
    landed is pending, and the phase that lands it makes the test prove it is read.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any, ClassVar

import pytest
from stub_server import SlowChunkedApp, StubServer
from test_smoke import _set_creds
from tsb_contract_client import ApiError

from tsb_tui import config, smoke

LEADER_MAP = "keys.leader_map."
CONFIG_DIR = Path(__file__).resolve().parents[1] / "tsb_tui"
PKG = CONFIG_DIR


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_precedence_default_lt_profile_lt_file_lt_env_lt_set(tmp_path: Path) -> None:
    """D30: each layer beats the one below it. A wrong order would let a stale environment
    variable override the operator's explicit `--set`."""
    home = tmp_path
    cfg_dir = home / ".config" / "tsb-tui"
    cfg_dir.mkdir(parents=True)
    _write(cfg_dir / "config.toml", "toasts.max = 7\n")
    # File sets 7; env sets 9; --set sets 11. The last wins.
    cfg = config.load(
        profile="phone",
        config_file=home / ".config" / "tsb-tui" / "config.toml",
        environ={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                 "TSB_TUI_TOASTS_MAX": "9"},
        overrides=["toasts.max=11"],
    )
    assert cfg["toasts.max"] == 11

    # Without --set, the environment beats the file.
    cfg = config.load(
        config_file=home / ".config" / "tsb-tui" / "config.toml",
        environ={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                 "TSB_TUI_TOASTS_MAX": "9", "TSB_TUI_SIZE_AUTO_REQUEST": "true"},
    )
    assert cfg["toasts.max"] == 9

    # Without the environment, the file beats the packaged default (3).
    cfg = config.load(
        config_file=home / ".config" / "tsb-tui" / "config.toml",
        environ={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                 "TSB_TUI_SIZE_AUTO_REQUEST": "true"},
    )
    assert cfg["toasts.max"] == 7

    # The profile beats the packaged default: phone sets fleet_view.refresh_s = 10.
    cfg = config.load(profile="phone", config_file=tmp_path / "absent.toml",
                      environ={"HOME": str(home)})
    assert cfg["fleet_view.refresh_s"] == 10


@pytest.mark.parametrize("profile,auto,guard", [
    ("default", True, True),
    ("phone", False, True),
    ("drive", False, True),
    ("harness", False, True),
])
def test_each_profile_sets_auto_request_and_guard(profile: str, auto: bool, guard: bool) -> None:
    """OPEN-1 and D52 were both ruled on 2026-10-12: the default profile has auto size on, and every
    profile keeps the guard on. A wrong value here would silently ask a phone's target for a size
    (terminal-size §1) or let a viewer's typing reach the core from the harness profile."""
    cfg = config.load(profile=profile, config_file=Path("/nonexistent"), environ={})
    assert cfg["size.auto_request"] is auto
    assert cfg["input.guard"] is guard


def test_the_default_profile_starts_with_no_override_and_auto_size_on() -> None:
    """OPEN-1 ruled: the default profile ships `size.auto_request = true`, so start-up needs no
    profile, file, environment or `--set` — the ruling removed the start-up refusal that demanded
    one."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={})
    assert cfg["size.auto_request"] is True
    assert cfg["input.guard"] is True


def test_the_harness_profile_keeps_the_guard_on_and_output_counts() -> None:
    """The harness profile sets auto_request false and drive.output_events counts, so a rig sees
    output counts; D52 ruled that its guard stays on like every other profile."""
    cfg = config.load(profile="harness", config_file=Path("/nonexistent"), environ={})
    assert cfg["size.auto_request"] is False
    assert cfg["input.guard"] is True
    assert cfg["drive.output_events"] == "counts"


def test_an_unknown_key_fails_naming_it(tmp_path: Path) -> None:
    """Rule 12: a typo is loud. The key is named, and start-up stops."""
    bad = _write(tmp_path / "config.toml", "toast.max = 3\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=bad, environ={}, overrides={"size.auto_request": "true"})
    assert "toast.max" in str(exc.value)


def test_an_unknown_env_key_fails_naming_it() -> None:
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=Path("/nonexistent"), environ={"TSB_TUI_NOPE_KEY": "1"},
                    overrides={"size.auto_request": "true"})
    assert "TSB_TUI_NOPE_KEY" in str(exc.value)


def test_a_bad_type_fails_naming_the_key() -> None:
    """A boolean key given a non-boolean string is refused, not coerced to truthy."""
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=Path("/nonexistent"), environ={},
                    overrides={"size.auto_request": "maybe"})
    assert "size.auto_request" in str(exc.value)


def test_a_value_no_phase_builds_is_refused_naming_the_key() -> None:
    """DT19: `view.narrow_mode = "wrap"` and `watch.held = "auto"` are refused at load, because no
    phase builds them at E2."""
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=Path("/nonexistent"), environ={},
                    overrides={"size.auto_request": "true", "view.narrow_mode": "wrap"})
    assert "view.narrow_mode" in str(exc.value)
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=Path("/nonexistent"), environ={},
                    overrides={"size.auto_request": "true", "watch.held": "auto"})
    assert "watch.held" in str(exc.value)


def test_the_prompt_and_gate_values_that_would_break_a_contract_rule_or_have_no_design_are_refused() -> None:
    """DT19: `prompts.capture_unblocking = true` has no design (the quiet prompt and alerts never capture keys),
    and `gates.hostkey_tick = false` would let a first-use host key through unacknowledged, which
    session-open step 22 forbids. Both are refused at load, naming the key; an unknown recording gate too."""
    base = {"size.auto_request": "true"}
    for key, value in (("prompts.capture_unblocking", "true"), ("gates.hostkey_tick", "false"),
                       ("gates.recording", "sometimes")):
        with pytest.raises(config.ConfigError) as exc:
            config.load(config_file=Path("/nonexistent"), environ={}, overrides={**base, key: value})
        assert key in str(exc.value)
    cfg = config.load(config_file=Path("/nonexistent"), environ={},
                      overrides={**base, "gates.recording": "every-attach"})
    assert cfg["gates.recording"] == "every-attach"



def test_the_file_and_the_default_ship_the_same_keys() -> None:
    """Every key in `config_default.toml` has a type and an owner, and every typed key is in the
    file. A key in one and not the other is a knob the registry or the file cannot see (DT19)."""
    default = tomllib.loads((CONFIG_DIR / "config_default.toml").read_text(encoding="utf-8"))

    def flatten(node: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for name, value in node.items():
            key = f"{prefix}{name}"
            if isinstance(value, dict):
                out |= flatten(value, key + ".")
            else:
                out.add(key)
        return out

    file_keys = flatten({k: v for k, v in default.items() if k not in ("profile", "profiles")})
    # A profile overlay may name a key the default top level omits, so the check is against the
    # typed key set, not the file's top level.
    profile_keys: set[str] = set()
    for section in default.get("profiles", {}).values():
        profile_keys |= flatten(section)
    assert profile_keys <= set(config.KNOB_TYPES), \
        f"a profile names an untyped key: {profile_keys - set(config.KNOB_TYPES)}"

    typed = set(config.KNOB_TYPES)
    # Every typed key is in the file: no knob ships unset since OPEN-1 and D52 were ruled.
    # `[keys.leader_map]` is an optional table: one key per action, left out of the packaged file.
    missing = {k for k in typed - file_keys if not k.startswith(LEADER_MAP)}
    assert not missing, f"typed-but-not-in-file: {missing}"
    assert file_keys <= typed, f"in-file-but-not-typed: {file_keys - typed}"
    owned = set(config.KNOB_OWNERS)
    assert typed == owned, f"typed-but-no-owner: {typed - owned}; owner-but-not-typed: {owned - typed}"


def test_every_owned_key_is_referenced_outside_config() -> None:
    """DT19: a key whose owner phase has landed must be read by code outside `config.py`. The test
    lists the pending keys so the last phase's run proves none is left; a key read nowhere is a
    knob the operator can set that nothing honours."""
    owned = config.knobs_owned_through()
    pending = config.knobs_pending()
    assert set(owned).isdisjoint(pending)

    sources = [
        path.read_text(encoding="utf-8")
        for path in PKG.rglob("*.py")
        if path.name != "config.py"
    ]
    haystack = "\n".join(sources)
    # The leader map is one family read as a whole, through `Config.leader_overrides()`.
    missing = [key for key in owned
               if (("leader_overrides" if key.startswith(LEADER_MAP) else key) not in haystack)]
    # A pending key is allowed to be unreferenced; an owned one is not.
    assert not missing, f"owned keys read nowhere outside config.py: {missing} (pending: {pending})"


def test_after_the_last_phase_every_key_but_the_inspectors_is_read() -> None:
    """DT19, phase 10. The last phase owns no key, so no key is "not landed yet": the registry's current phase is the
    last one and the only key left unread is `view.inspector`, which the operator left out of E2 (the Inspector and
    Zoom) and which therefore accepts its default alone. A key that slipped in as pending, or a second knob left
    unread, would be a promise nothing keeps."""
    assert config.CURRENT_PHASE == 10
    assert config.knobs_pending() == ["view.inspector"]
    assert config.KNOB_OWNERS["view.inspector"] == config.UNASSIGNED
    assert config.load(config_file=Path("/nonexistent"), environ={},
                       overrides={"size.auto_request": "true"})["view.inspector"] == "auto"
    for refused in ("float", "off"):
        with pytest.raises(config.ConfigError, match="view.inspector"):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={"size.auto_request": "true", "view.inspector": refused})


def test_the_owned_keys_are_the_keys_of_phases_one_to_nine() -> None:
    """The registry marks each key with its owner phase (DT19). Phases 1 to 9 own the keys they read
    (the engine, the theme, the canvas, the input path, the status row, the Menu and the leader, the prompts,
    the gate, the reattach, the alerts and the toasts, the rail, the sidebar, the glyph set and the chrome, the full
    view, the mouse, the pan and the motion level, and `tsb-tui drive`); every later phase's key is pending, so the check above is
    meaningful, not vacuous. `view.inspector` is pending too, because the Inspector's content is not decided and
    nothing reads the knob. Spelled out, so reading a new knob is a deliberate change to this list."""
    assert set(config.knobs_owned_through()) == {
        "drive.answer_timeout_ms", "drive.send_gate", "drive.brake_look_max_ms",
        "drive.default_cols", "drive.default_rows", "drive.events", "drive.output_events", "drive.reply_max_lines",
        "drive.settled_ms", "drive.switch_on_action", "drive.wait_timeout_max_ms", "drive.wait_timeout_ms",
        "alerts.popup", "alerts.read_timeout_s", "broker.timeout_s", "event_log.lines", "fleet_view.refresh_s",
        "fleet_view.details_from", "fleet_view.group_by", "fleet_view.show_ended", "fleet_view.tabs",
        "keys.leader_map.end", "keys.leader_map.event_log", "keys.leader_map.full_view", "keys.leader_map.keys",
        "keys.leader_map.pan", "keys.leader_map.quit", "motion.level", "mouse.canvas_clicks", "mouse.enabled",
        "mouse.tooltips", "view.pan_step", "scroll.wheel_lines", "scroll.match_style", "scroll.current_match_style",
        "scroll.selection_style", "copy.osc52", "copy.max_bytes", "copy.join_wrapped",
        "gates.hostkey_tick", "glyphs.set", "gates.recording", "gates.recording_bar_s",
        "input.focus_reports", "input.guard",
        "keys.leader", "keys.leader_map.answer", "keys.leader_map.auto_size", "keys.leader_map.detach",
        "keys.leader_map.fleet", "keys.leader_map.new_session", "keys.leader_map.next",
        "keys.leader_map.prev", "keys.leader_map.scroll", "keys.leader_map.size", "keys.leader_map.switch",
        "keys.leader_map.take_back", "keys.leader_map.theme", "keys.leader_map.who",
        "keys.leader_map.credentials", "keys.leader_map.devices", "keys.leader_map.lock_now",
        "menu.open_on_leader_after_ms", "menu.search",
        "prompts.capture_unblocking", "prompts.input_guard_ms",
        "rail.chip_mark", "rail.chips", "rail.needs_you", "rail.needs_you_scope", "rail.phone", "rail.side",
        "rail.width", "reattach.auto", "reattach.backoff_ms",
        "render.max_fps", "render.paint_background", "sessions.id_min_hex",
        "sidebar.close_on_pick", "sidebar.details_from", "sidebar.groups", "sidebar.open_when_no_session",
        "sidebar.start_filter", "sidebar.wide_from", "sidebar.width", "sidebar.width_phone",
        "sidebar.width_wide", "size.auto_request", "size.debounce_ms",
        "status.hint_priority", "status.keys_indicator", "status.position", "status.show",
        "status.stale_banner_bg", "status.stale_banner_fg", "status.stale_banner_pad",
        "term.library", "term.scrollback_bytes",
        "theme.accent", "theme.accent_hot", "theme.accent_soft", "theme.bg", "theme.border",
        "theme.border_on_surface", "theme.danger", "theme.frame", "theme.name", "theme.ok", "theme.on_accent",
        "theme.rec", "theme.rec_list",
        "theme.steel", "theme.surface", "theme.surface_2", "theme.surface_3",         "theme.term.colors", "theme.term.bg", "theme.term.cursor_colour", "theme.term.cursor_typist",
        "theme.term.cursor_viewer", "theme.term.fg", "theme.term.minimum_contrast", "theme.term.palette",
        "theme.term.scheme",
        "theme.text", "theme.text_faint", "theme.text_muted", "theme.warn",
        "toasts.max", "toasts.seconds", "view.chrome", "view.follow_cursor", "view.narrow_mode",
        "watch.held", "watch.take_back_brings_to_front"}
    assert "view.inspector" in config.knobs_pending()
    assert "fleet_view.group_by" not in config.knobs_pending()
    assert "input.guard" not in config.knobs_pending()


def test_defaults_match_the_design_knob_file_values() -> None:
    """The packaged defaults are the design's, except the documented changes. Spot-check the values
    phase 2 fixes: scrollback, the guard default, focus reports, the alert timeout, the drive
    profile's filter."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={},
                      overrides={"size.auto_request": "true"})
    assert cfg["term.scrollback_bytes"] == 4194304
    assert cfg["input.guard"] is True
    assert cfg["input.focus_reports"] is False
    assert cfg["alerts.read_timeout_s"] == 30
    assert cfg["broker.timeout_s"] == 70
    assert cfg["event_log.lines"] > 0
    drive = config.load(profile="drive", config_file=Path("/nonexistent"), environ={})
    assert drive["sidebar.start_filter"] == "all"
    assert drive["mouse.enabled"] is False


def test_the_scroll_wheel_step_is_three_rows_and_a_step_below_one_is_refused_naming_the_key() -> None:
    """`scroll.wheel_lines` is how many rows one wheel turn moves the scroll view: 3 unless set, and a step that moves
    nothing (0, or less) is refused at load, naming the key, not left as a wheel that does nothing."""
    assert config.load(config_file=Path("/nonexistent"), environ={},
                       overrides={"size.auto_request": "true"})["scroll.wheel_lines"] == 3
    assert config.load(config_file=Path("/nonexistent"), environ={"TSB_TUI_SCROLL_WHEEL_LINES": "7"},
                       overrides={"size.auto_request": "true"})["scroll.wheel_lines"] == 7
    for refused in ("0", "-2"):
        with pytest.raises(config.ConfigError, match="scroll.wheel_lines"):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={"size.auto_request": "true", "scroll.wheel_lines": refused})


def test_the_search_styles_default_to_attributes_and_a_string_rich_cannot_parse_is_refused_naming_the_key() -> None:
    """`scroll.match_style` and `scroll.current_match_style` are Rich style strings the scroll view's search marks use:
    underline, and reverse bold, unless set (attributes, so they read on every theme). A string Rich cannot parse is
    refused at load, naming the key, not left to fail on the first search."""
    base = {"size.auto_request": "true"}
    loaded = config.load(config_file=Path("/nonexistent"), environ={}, overrides=base)
    assert loaded["scroll.match_style"] == "underline"
    assert loaded["scroll.current_match_style"] == "reverse bold"
    environ = {"TSB_TUI_SCROLL_MATCH_STYLE": "bold italic", "TSB_TUI_SCROLL_CURRENT_MATCH_STYLE": "underline"}
    chosen = config.load(config_file=Path("/nonexistent"), environ=environ, overrides=base)
    assert chosen["scroll.match_style"] == "bold italic"
    assert chosen["scroll.current_match_style"] == "underline"
    for key in ("scroll.match_style", "scroll.current_match_style"):
        for refused in ("sparkly", "bold on"):
            with pytest.raises(config.ConfigError, match=key):
                config.load(config_file=Path("/nonexistent"), environ={}, overrides={**base, key: refused})


def test_the_selection_style_defaults_to_reverse_and_a_string_rich_cannot_parse_is_refused_naming_the_key() -> None:
    """`scroll.selection_style` is the Rich style string the scroll view marks the selected lines with: reverse unless
    set (an attribute, so it reads on every theme). A string Rich cannot parse is refused at load, naming the key."""
    base = {"size.auto_request": "true"}
    assert config.load(config_file=Path("/nonexistent"), environ={}, overrides=base)["scroll.selection_style"] == "reverse"
    chosen = config.load(config_file=Path("/nonexistent"), environ={"TSB_TUI_SCROLL_SELECTION_STYLE": "bold underline"},
                         overrides=base)
    assert chosen["scroll.selection_style"] == "bold underline"
    for refused in ("sparkly", "bold on"):
        with pytest.raises(config.ConfigError, match="scroll.selection_style"):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={**base, "scroll.selection_style": refused})


def test_the_copy_knobs_default_to_on_100000_bytes_and_joined_and_a_limit_below_one_is_refused_naming_the_key() -> None:
    """`copy.osc52` (the clipboard write) and `copy.join_wrapped` are on unless set, `copy.max_bytes` is 100000 bytes of
    text, and a limit that would refuse every copy (0, or less) is refused at load, naming the key."""
    base = {"size.auto_request": "true"}
    loaded = config.load(config_file=Path("/nonexistent"), environ={}, overrides=base)
    assert loaded["copy.osc52"] is True and loaded["copy.join_wrapped"] is True and loaded["copy.max_bytes"] == 100000
    environ = {"TSB_TUI_COPY_OSC52": "false", "TSB_TUI_COPY_JOIN_WRAPPED": "false", "TSB_TUI_COPY_MAX_BYTES": "4096"}
    chosen = config.load(config_file=Path("/nonexistent"), environ=environ, overrides=base)
    assert chosen["copy.osc52"] is False and chosen["copy.join_wrapped"] is False and chosen["copy.max_bytes"] == 4096
    for refused in ("0", "-1"):
        with pytest.raises(config.ConfigError, match="copy.max_bytes"):
            config.load(config_file=Path("/nonexistent"), environ={}, overrides={**base, "copy.max_bytes": refused})


# -- the REST timeout knob --


def _load_with(**overrides: str) -> config.Config:
    """The packaged defaults plus `--set` overrides, in the pinned environment conftest sets (the
    engine path the app needs), with no operator config file."""
    return config.load(overrides=overrides)


class _RecordingClient:
    """A stand-in for `tsb_contract_client.Client` that records how each builder called it. It is
    never asked to talk to a broker: the tests check the constructor's arguments, nothing on the wire."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, base_url: str, ca_file: str | None = None, *, timeout: float = 10.0) -> None:
        self.calls.append({"base_url": base_url, "ca_file": ca_file, "timeout": timeout})
        self.timeout = timeout  # the real Client carries the one value it passes to every call

    def login(self, *args: Any) -> None:
        raise ApiError(401, "unauthenticated", "the recording stand-in never talks to a broker")


@pytest.fixture
def stub_url():
    """A live loopback stub (cleartext is allowed off-loopback only for the real path's checks):
    the tests check how each builder constructs its client, never a round trip."""
    with StubServer(SlowChunkedApp(max_chunks=0, echo_client_frames=True)) as url:
        yield url


def test_the_rest_timeout_reaches_the_client(monkeypatch, tmp_path, stub_url) -> None:
    """`broker.timeout_s` is the timeout every REST call waits on. The app builds its one client
    with the knob and `smoke.run` builds its client with the same knob from the config it loads;
    with no override both get the packaged 70, where the contract client's own default was 10.
    A knob that reached the registry but not the client is a promise nothing keeps."""
    import tsb_tui.app as app_mod
    import tsb_tui.smoke as smoke_mod

    _RecordingClient.calls = []
    monkeypatch.setattr(app_mod, "Client", _RecordingClient)
    monkeypatch.setattr(smoke_mod, "Client", _RecordingClient)
    _set_creds(monkeypatch, tmp_path)

    cfg33 = _load_with(**{"broker.timeout_s": "33"})
    app = app_mod.TsbTuiApp(stub_url, target="stub-target", cfg=cfg33)
    assert app.client.timeout == 33
    smoke.run(stub_url, "stub-target", None, cfg=cfg33)
    assert [call["timeout"] for call in _RecordingClient.calls] == [33, 33]

    _RecordingClient.calls = []
    default = _load_with()
    app = app_mod.TsbTuiApp(stub_url, target="stub-target", cfg=default)
    assert app.client.timeout == 70
    smoke.run(stub_url, "stub-target", None, cfg=default)
    assert [call["timeout"] for call in _RecordingClient.calls] == [70, 70]


def test_a_rest_timeout_below_one_second_is_refused_naming_the_key() -> None:
    """Rule 12: a `broker.timeout_s` below 1 fails start-up naming the key — a 0 s or negative wait
    can never be answered in, and a silently ignored typo would leave the operator believing a
    timeout took effect when it never did."""
    for bad in ("0", "-5"):
        with pytest.raises(config.ConfigError, match=rf"broker\.timeout_s = {bad}: expected at least 1"):
            _load_with(**{"broker.timeout_s": bad})


def test_text_faint_passes_on_the_surface_the_chrome_draws_it_on() -> None:
    # fails today: the packaged default is #7a7a7a, 4.0:1 on surface.
    """Design §1.7: the rail and sidebar draw `text_faint` on `surface`, where it must pass; the
    packaged default is #858585 (4.76:1 on surface), not the old #7a7a7a."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={})
    assert cfg["theme.text_faint"] == "#858585"


def test_the_default_roles_are_the_packaged_terracotta_roles() -> None:
    """TT12: a theme file that lacks a role keeps the packaged default, and a partial theme's swatch
    paints `Config.default_roles`. If that table drifted from the packaged theme, a partial theme would
    paint colours no file carries, and its swatch would not match the chrome a switch to it produces."""
    from tsb_tui.theme_files import packaged_dir, parse_theme

    cfg = config.load(config_file=Path("/nonexistent"), environ={})
    terracotta = parse_theme(packaged_dir("themes") / "terracotta.toml")
    assert cfg.default_roles == {role: value.lower() for role, value in terracotta.roles.items()}, (
        f"the packaged default roles are not the packaged theme's: {cfg.default_roles}")

def test_the_env_name_maps_every_key_uniquely() -> None:
    """A collision in the `TSB_TUI_*` names would make one variable ambiguous."""
    names = [config._env_name(key) for key in config.KNOB_TYPES]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"TSB_TUI_[A-Z0-9_]+", name) for name in names)


# -- the status row, Menu and leader knobs (phase 5) --


def _load(**overrides: str) -> config.Config:
    return config.load(config_file=Path("/nonexistent"), environ={},
                       overrides={"size.auto_request": "true", **overrides})


def test_a_leader_the_chrome_cannot_print_is_refused_naming_the_key() -> None:
    """D15, Rule 12: `keys.leader` is both the key the screen takes and the label the status row prints.
    A value with no printed form (a Ctrl-letter, which readline and routers use; a typo) must stop
    start-up naming the key, not leave a row that prints nothing or a leader that cannot be pressed."""
    assert _load(**{"keys.leader": "ctrl+right_square_bracket"})["keys.leader"] == "ctrl+right_square_bracket"
    for bad in ("ctrl+a", "f9", "ctrl+backslsh"):
        with pytest.raises(config.ConfigError, match=r"keys\.leader"):
            _load(**{"keys.leader": bad})


def test_the_leader_map_rebinds_by_action_name_and_refuses_a_clash() -> None:
    """Design §2.4: `[keys.leader_map] detach = "q"`. Two actions on one letter would leave one of them
    unreachable, so it is refused naming both; a value that is not one printable character is refused
    naming its key; an action that takes no key after the leader is an unknown key."""
    cfg = _load(**{"keys.leader_map.detach": "q"})
    assert cfg.leader_overrides() == {"detach": "q"}
    with pytest.raises(config.ConfigError, match="both on 'w'"):
        _load(**{"keys.leader_map.detach": "w"})
    with pytest.raises(config.ConfigError, match=r"keys\.leader_map\.detach"):
        _load(**{"keys.leader_map.detach": "dd"})
    with pytest.raises(config.ConfigError, match="unknown configuration key"):
        _load(**{"keys.leader_map.menu": "m"})


def test_status_and_menu_knobs_refuse_values_the_chrome_cannot_honour(tmp_path: Path) -> None:
    """DT19: a position that is neither top nor bottom, a hint name the design does not have (a typo would
    silently never drop), and a leader delay of zero (the Menu would open before any letter could follow)
    stop start-up naming the key."""
    assert _load(**{"status.position": "top"})["status.position"] == "top"
    with pytest.raises(config.ConfigError, match=r"status\.position"):
        _load(**{"status.position": "left"})
    with pytest.raises(config.ConfigError, match=r"status\.hint_priority.*'sizee'"):
        _load(**{"status.hint_priority": "menu,sizee"})
    with pytest.raises(config.ConfigError, match=r"menu\.open_on_leader_after_ms"):
        _load(**{"menu.open_on_leader_after_ms": "0"})
    # The same file form works: a `[keys.leader_map]` table in the TOML.
    path = _write(tmp_path / "config.toml", '[keys.leader_map]\ndetach = "q"\n')
    cfg = config.load(config_file=path, environ={}, overrides={"size.auto_request": "true"})
    assert cfg.leader_overrides() == {"detach": "q"}


def test_a_knob_changed_for_this_run_is_typed_and_never_written_back(tmp_path: Path) -> None:
    """The Size dialog's auto toggle changes `size.auto_request` for the run (design D18). The new value
    is held to the key's type, an unknown key is refused, and the config file is untouched."""
    path = _write(tmp_path / "config.toml", "[size]\nauto_request = false\n")
    before = path.read_text(encoding="utf-8")
    cfg = config.load(config_file=path, environ={})
    cfg.set_runtime("size.auto_request", True)
    assert cfg["size.auto_request"] is True
    with pytest.raises(config.ConfigError, match="expected a boolean"):
        cfg.set_runtime("size.auto_request", "maybe")
    with pytest.raises(config.ConfigError, match="unknown configuration key"):
        cfg.set_runtime("size.nope", 1)
    assert path.read_text(encoding="utf-8") == before


# -- the rail, the sidebar, the glyph set and the chrome (phase 7) --


@pytest.mark.parametrize("key, bad", [
    ("glyphs.set", "emoji"), ("rail.side", "top"), ("rail.phone", "wide"), ("rail.needs_you_scope", "everyone"),
    ("sidebar.start_filter", "some"), ("sidebar.close_on_pick", "sometimes"), ("view.chrome", "huge"),
])
def test_a_choice_knob_outside_its_values_fails_naming_the_key(key: str, bad: str) -> None:
    """Rule 12: a typo in a config file must not silently mean the default. Each knob that names one of a few
    values refuses any other, and the message names the key."""
    with pytest.raises(config.ConfigError, match=re.escape(key)):
        config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true", key: bad})


def test_theme_term_bg_accepts_the_three_forms_and_refuses_other_values() -> None:
    """Phase 1: theme.term.bg accepts "scheme", "outer" or #rrggbb and refuses "red" naming the key.

    fails today: the key does not exist."""
    assert _load(**{"theme.term.bg": "scheme"})["theme.term.bg"] == "scheme"
    assert _load(**{"theme.term.bg": "outer"})["theme.term.bg"] == "outer"
    assert _load(**{"theme.term.bg": "#123abc"})["theme.term.bg"] == "#123abc"
    with pytest.raises(config.ConfigError, match=re.escape("theme.term.bg")):
        _load(**{"theme.term.bg": "red"})


def test_theme_term_scheme_resolves_the_file_and_a_missing_one_lists_what_exists() -> None:
    """Phase 3, TT10: a selected scheme is resolved at `load`, so a missing one is a start-up
    `ConfigError` naming the slug, the shipped slugs and both folders searched; a good one is kept on
    the `Config` for the palette to read."""
    # fails today: `theme.term.scheme` is an unknown key.
    cfg = _load(**{"theme.term.scheme": "dracula"})
    assert cfg.scheme.slug == "dracula" and cfg.scheme.name == "Dracula"
    assert cfg.user_dir.name == "tsb-tui"

    with pytest.raises(config.ConfigError) as exc:
        _load(**{"theme.term.scheme": "no-such-scheme"})
    message = str(exc.value)
    assert "no-such-scheme" in message and "tsb-default" in message
    assert "schemes" in message


def test_the_chrome_theme_resolved_at_load_is_kept_on_the_config() -> None:
    """TT28: `load` keeps the selected chrome theme on the `Config`, as it keeps the scheme, so
    `build_theme(config, chrome)` takes the resolved file as an argument; a missing one is a
    start-up `ConfigError` like a missing scheme."""
    # fails today: there is no themes folder, and `Config` carries no chrome.
    cfg = _load(**{"theme.name": "nord"})
    assert cfg.chrome.id == "nord" and cfg.chrome.yours is False
    assert cfg.chrome.variant == "dark"
    with pytest.raises(config.ConfigError):
        _load(**{"theme.name": "no-such-theme"})


def test_a_broken_selected_user_scheme_fails_naming_the_file_and_the_field(tmp_path: Path) -> None:
    """Phase 3, TT10/Rule 12: a user scheme that is selected and malformed stops start-up naming the
    file and the field, so a typo is fixable from the message rather than painting a wrong screen."""
    # fails today: `theme.term.scheme` is an unknown key.
    config_home = tmp_path / "config"
    schemes = config_home / "tsb-tui" / "schemes"
    schemes.mkdir(parents=True)
    (schemes / "mine.json").write_text('{"name":"Mine","ansi":["#000000"]}', encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.load(config_file=Path("/nonexistent"),
                    environ={"XDG_CONFIG_HOME": str(config_home), "HOME": str(tmp_path)},
                    overrides={"size.auto_request": "true", "theme.term.scheme": "mine"})
    message = str(exc.value)
    assert "mine.json" in message and "ansi" in message


def test_theme_term_cursor_colour_and_colors_refuse_values_the_palette_cannot_use() -> None:
    """Phase 3, Rule 12: `cursor_colour` is "role" or "scheme", and `colors` is [] or exactly 16.
    Both are checked at `load` naming the key, so the failure is at start-up, not when a canvas is
    built for the first attached session."""
    # fails today: `theme.term.cursor_colour` is an unknown key.
    assert _load(**{"theme.term.cursor_colour": "role"})["theme.term.cursor_colour"] == "role"
    assert _load(**{"theme.term.cursor_colour": "scheme"})["theme.term.cursor_colour"] == "scheme"
    with pytest.raises(config.ConfigError, match=re.escape("theme.term.cursor_colour")):
        _load(**{"theme.term.cursor_colour": "blue"})
    with pytest.raises(config.ConfigError, match=re.escape("theme.term.colors")):
        _load(**{"theme.term.colors": "#010101,#020202,#030303"})


def test_a_motion_knob_the_canvas_cannot_honour_fails_at_load_naming_the_key() -> None:
    """`render.max_fps` and `view.follow_cursor` are range/choice-checked at load, not when a canvas is built.

    Why (Qodo #229, ruling 7; Rule 12): the canvas is built per attached session, so an out-of-range
    `render.max_fps` (0, 1000) or an unknown `view.follow_cursor` used to raise only when the first
    session attached — a late, unexplained failure instead of a start-up error naming the key. The
    canvas's own accepted values are the contract: an integer 1-240, and one of auto, on, off. The
    ends of those ranges and each legal value still load, so the check refuses only what the canvas
    refuses and cannot reject a legal 240 or `off`.
    """
    for bad in ("0", "241", "-1"):
        with pytest.raises(config.ConfigError, match=re.escape("render.max_fps")):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={"size.auto_request": "true", "render.max_fps": bad})
    for bad in ("always", "auto,on", ""):
        with pytest.raises(config.ConfigError, match=re.escape("view.follow_cursor")):
            config.load(config_file=Path("/nonexistent"), environ={},
                        overrides={"size.auto_request": "true", "view.follow_cursor": bad})
    for fps in ("1", "240"):
        cfg = config.load(config_file=Path("/nonexistent"), environ={},
                          overrides={"size.auto_request": "true", "render.max_fps": fps})
        assert cfg["render.max_fps"] == int(fps)
    for value in ("auto", "on", "off"):
        cfg = config.load(config_file=Path("/nonexistent"), environ={},
                          overrides={"size.auto_request": "true", "view.follow_cursor": value})
        assert cfg["view.follow_cursor"] == value


@pytest.mark.parametrize("key, bad", [
    ("rail.chips", "open_here,nothing"), ("rail.chip_mark", "front,front"), ("rail.needs_you", "boredom"),
    ("sidebar.groups", "needs_you,everyone"),
])
def test_a_list_knob_naming_something_unknown_or_twice_fails_naming_the_key(key: str, bad: str) -> None:
    """A name outside the list would silently drop a chip, a mark, a reason or a group, and a name listed twice
    means nothing: both fail, naming the knob."""
    with pytest.raises(config.ConfigError, match=re.escape(key)):
        config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true", key: bad})


@pytest.mark.parametrize("key, bad", [
    ("rail.width", "3"), ("sidebar.width", "10"), ("sidebar.width_phone", "10"), ("sidebar.width_wide", "10"),
    ("sidebar.wide_from", "0"), ("sidebar.details_from", "0"), ("sessions.id_min_hex", "0"),
    ("fleet_view.refresh_s", "-1"),
])
def test_a_width_or_a_count_too_small_to_draw_fails_naming_the_key(key: str, bad: str) -> None:
    """A rail narrower than its three glyph cells and hairline, a sidebar with no room for its header, or a
    negative poll interval cannot be drawn: start-up refuses them naming the key."""
    with pytest.raises(config.ConfigError, match=re.escape(key)):
        config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true", key: bad})


def test_the_phone_profile_makes_the_chrome_compact_and_the_defaults_match_the_knob_file() -> None:
    """The phone profile sets `view.chrome = "compact"` (design §2.6), and the packaged rail and sidebar values are
    the knob file's, so what the mockups show is what a default start-up draws."""
    phone = config.load(profile="phone", config_file=Path("/nonexistent"), environ={})
    assert phone["view.chrome"] == "compact" and phone["size.auto_request"] is False
    default = config.load(config_file=Path("/nonexistent"), environ={}, overrides={"size.auto_request": "true"})
    assert (default["rail.width"], default["sidebar.width"], default["sidebar.width_phone"],
            default["sidebar.width_wide"], default["sidebar.wide_from"]) == (4, 32, 30, 42, 160)
    assert default["rail.chips"] == ["open_here", "mine_held_by_others"]
    assert default["sidebar.close_on_pick"] == "phone" and default["sidebar.open_when_no_session"] is True
    assert default["glyphs.set"] == "unicode" and default["view.chrome"] == "auto"


@pytest.mark.parametrize(("key", "value", "words"), [
    ("drive.events", "gate,teleport", "drive.events"),
    ("drive.events", "token,token", "listed twice"),
    ("drive.output_events", "all", "drive.output_events"),
    ("drive.default_cols", "0", "drive.default_cols"),
    ("drive.reply_max_lines", "0", "drive.reply_max_lines"),
    ("drive.wait_timeout_ms", "-1", "drive.wait_timeout_ms"),
    ("drive.switch_on_action", "false", "not built"),
])
def test_a_drive_knob_drive_cannot_honour_fails_start_up_naming_the_key(key: str, value: str, words: str) -> None:
    """DT19, Rule 12: an event name that is not one, an output mode that is not built, a size that is nothing, or a
    switch behaviour no phase builds (what `false` would do for an action on a background session is not designed) must
    stop start-up naming the key, so an agent's typo is loud and never a silently different drive."""
    with pytest.raises(config.ConfigError, match=words):
        config.load(profile="drive", config_file=Path("/nonexistent"), environ={}, overrides={key: value})


def test_the_drive_and_harness_profiles_keep_the_guard_on() -> None:
    """Frontend §9, honesty rule 1: `input.guard` is the default (on) in every profile, the harness
    included — D52 was ruled on 2026-10-12 — while the harness sets output counts on and the mouse
    alone. `--set input.guard=false` is still how a rig switches it off for a test; the default is
    what it is."""
    drive = config.load(profile="drive", config_file=Path("/nonexistent"), environ={})
    assert drive["input.guard"] is True and drive["drive.output_events"] == "off" and drive["mouse.enabled"] is False
    harness = config.load(profile="harness", config_file=Path("/nonexistent"), environ={})
    assert harness["input.guard"] is True and harness["drive.output_events"] == "counts"
    assert harness["mouse.enabled"] is True
    for value in ("true", "false"):
        switched = config.load(profile="harness", config_file=Path("/nonexistent"), environ={},
                               overrides={"input.guard": value})
        assert switched["input.guard"] is (value == "true")


def test_a_profiles_table_in_the_operators_file_is_refused_naming_it(tmp_path: Path) -> None:
    """D30, Rule 12 (Qodo #229, ruling 8): `[profiles.*]` is the packaged file's, not the operator's. An
    operator who copies the whole default file would otherwise carry a `[profiles.phone]` table, and a
    copied key in it would override the profile in force (a copied `size.auto_request = true` would undo
    the phone, drive and harness profiles' OPEN-1 value) — dropped silently before. Start-up refuses it,
    naming it; the `profile` selector alone is still allowed in the operator's file."""
    bad = _write(tmp_path / "config.toml", '[profiles.phone]\nsize.auto_request = true\n')
    with pytest.raises(config.ConfigError, match="profiles"):
        config.load(config_file=bad, environ={})
    # The selector is not the overlays: `profile = "phone"` in the operator's file still chooses a profile.
    chosen = _write(tmp_path / "config.toml", 'profile = "phone"\n')
    cfg = config.load(config_file=chosen, environ={})
    assert cfg.profile == "phone" and cfg["size.auto_request"] is False


def test_default_config_path_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """P8: on Windows config home is %APPDATA%\\tsb-tui and XDG_CONFIG_HOME still wins."""
    monkeypatch.setattr(sys, "platform", "win32")

    env = {"APPDATA": "/mock/appdata"}
    assert config._default_path(env) == Path("/mock/appdata") / "tsb-tui" / "config.toml"

    env_xdg = {"APPDATA": "/mock/appdata", "XDG_CONFIG_HOME": "/mock/xdg_config"}
    assert config._default_path(env_xdg) == Path("/mock/xdg_config") / "tsb-tui" / "config.toml"

