"""tsb-tui configuration: the packaged defaults, a profile, the operator's TOML file, `TSB_TUI_*`
and `--set key=value`, in that order of precedence (design D30).

Why this shape (Rule 12): an unknown key, a value of the wrong type, or a value no phase builds
fails start-up *naming the key*, so a typo in a config file is loud instead of silently ignored.

The registry (`KNOB_OWNERS`) names the phase that reads each key (DT19). A key whose owner phase
has not landed yet is *pending*; the phase that owns it asserts, in its test, that every key it
owns is read by code outside this module. `CURRENT_PHASE` is bumped by each phase that lands.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.errors import StyleSyntaxError
from rich.style import Style

from . import glyphs
from .drive.events import EVENT_NAMES, OUTPUT_MODES
from .model.actions import LEADER_LABELS, LEADER_MAP_IDS, KeymapError, leader_keys
from .model.fleet import CHIP_MARKS, CHIP_SOURCES, GROUP_ORDER, NEEDS_REASONS
from .theme import relative_luminance, role_colours, surface_colour
from .theme_files import ROLES, SchemeError, ThemeFileError, find_scheme, find_theme

# The phase whose keys are built so far. Every phase that reads knobs bumps this; phase 10, the last, owns
# no key and sets it to 10, so every key but the Inspector's is read by code outside this module. DT19.
CURRENT_PHASE = 10

# The owner of a knob no phase has built yet: it is validated at load and read by nothing, so it is pending
# whatever phase has landed (DT19). `view.inspector` is here until the Inspector's content is decided.
UNASSIGNED = 99

# A value no phase builds at E2 is refused by name (DT19).
NOT_BUILT_AT_E2: dict[str, set[str]] = {
    "view.narrow_mode": {"wrap"},
    "watch.held": {"auto"},
    # The quiet prompt and alerts never capture keys; what `true` would do is not designed.
    "prompts.capture_unblocking": {True},
    # A first-use host key MUST be acknowledged before the attach (session-open step 22).
    "gates.hostkey_tick": {False},
    # An action on a session behind the one in front brings it to the front first (D49); what `false` would do is
    # not designed, so it is not built.
    "drive.switch_on_action": {False},
}

PROFILES = ("default", "phone", "drive", "harness")

# The values the rail, the sidebar and the chrome knobs may hold (phase 7). A value outside its list fails
# start-up naming the key (Rule 12, DT19).
RAIL_SIDES = ("left", "right")
RAIL_PHONE = ("full", "edge")
NEEDS_YOU_SCOPES = ("mine_or_open_here",)
START_FILTERS = ("mine", "all")
CLOSE_ON_PICK = ("phone", "always", "never")
CHROMES = ("auto", "full", "compact")
# The rail is three glyph cells and the hairline: no narrower rail shows a chip (design D41).
MIN_RAIL_WIDTH = 4
# The sidebar needs room for its header (`▦ Fleet  ◂ close ^\ f`).
MIN_SIDEBAR_WIDTH = 24

# Phase 1: theme.term.bg validation
_HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")

# The values the full Fleet view's and the mouse's and the view's knobs may hold (phase 8). `targets` is a tab of
# E3: it is accepted in `fleet_view.tabs` (the knob file lists it) and shown once it exists.
FLEET_TABS = ("sessions", "targets", "alerts")
GROUP_BYS = ("none", "target")
CANVAS_CLICKS = ("session_if_asked",)
# The Inspector is left out of E2 (nothing defines its content): the default loads, any other value is refused.
INSPECTORS = ("auto",)
MOTION_LEVELS = ("none", "basic")
# What the canvas accepts for its motion knobs: a paint rate it can honour, and the cursor-follow modes
# (the values `Canvas` itself accepts). Checked here so a bad value fails at load, not when the first
# session attaches and builds a canvas.
FOLLOW_CURSORS = ("auto", "on", "off")
MIN_MAX_FPS = 1
MAX_MAX_FPS = 240

# The words `status.hint_priority` may hold: the design's hints (§2.4, D43). Most are built by a later
# phase; a name outside the list is a typo and would silently do nothing.
HINT_NAMES = frozenset({"menu", "fleet", "who", "detach", "end", "size", "scroll", "inspector", "keys",
                        "answer", "take_back"})
STATUS_POSITIONS = ("bottom", "top")

# The type of each knob. `bool` is checked before `int`, because Python's bool is an int subclass.
KNOB_TYPES: dict[str, type] = {
    "update.check": bool,
    "update.interval_h": int,
    "update.url": str,
    "theme.name": str,
    "theme.bg": str,
    "theme.surface": str,
    "theme.surface_2": str,
    "theme.surface_3": str,
    "theme.border": str,
    "theme.border_on_surface": str,
    "theme.text": str,
    "theme.text_muted": str,
    "theme.text_faint": str,
    "theme.accent": str,
    "theme.accent_hot": str,
    "theme.accent_soft": str,
    "theme.on_accent": str,
    "theme.steel": str,
    "theme.ok": str,
    "theme.warn": str,
    "theme.danger": str,
    "theme.frame": str,
    "theme.rec": str,
    "theme.rec_list": str,
    "theme.term.palette": str,
    "theme.term.scheme": str,
    "theme.term.bg": str,
    "theme.term.fg": str,
    "theme.term.cursor_colour": str,
    "theme.term.cursor_typist": str,
    "theme.term.cursor_viewer": str,
    "theme.term.colors": list,
    "theme.term.minimum_contrast": float,
    "glyphs.set": str,
    "term.library": str,
    "term.scrollback_bytes": int,
    "rail.side": str,
    "rail.width": int,
    "rail.phone": str,
    "rail.chips": list,
    "rail.chip_mark": list,
    "rail.needs_you": list,
    "rail.needs_you_scope": str,
    "sidebar.width": int,
    "sidebar.width_phone": int,
    "sidebar.width_wide": int,
    "sidebar.wide_from": int,
    "sidebar.groups": list,
    "sidebar.start_filter": str,
    "sidebar.details_from": int,
    "sidebar.close_on_pick": str,
    "sidebar.open_when_no_session": bool,
    "fleet_view.tabs": list,
    "fleet_view.group_by": str,
    "fleet_view.show_ended": bool,
    "fleet_view.details_from": int,
    "fleet_view.refresh_s": int,
    "status.show": bool,
    "status.position": str,
    "status.hint_priority": list,
    "status.keys_indicator": bool,
    "status.stale_banner_fg": str,
    "status.stale_banner_bg": str,
    "status.stale_banner_pad": int,
    "menu.search": bool,
    "menu.open_on_leader_after_ms": int,
    "mouse.enabled": bool,
    "mouse.canvas_clicks": str,
    "mouse.tooltips": bool,
    "keys.leader": str,
    "size.auto_request": bool,
    "size.debounce_ms": int,
    "input.guard": bool,
    "input.focus_reports": bool,
    "view.chrome": str,
    "view.narrow_mode": str,
    "view.follow_cursor": str,
    "view.pan_step": int,
    "scroll.wheel_lines": int,
    "scroll.match_style": str,
    "scroll.current_match_style": str,
    "scroll.selection_style": str,
    "copy.osc52": bool,
    "copy.max_bytes": int,
    "copy.join_wrapped": bool,
    "view.inspector": str,
    "render.max_fps": int,
    "render.paint_background": bool,
    "sessions.id_min_hex": int,
    "watch.held": str,
    "watch.take_back_brings_to_front": bool,
    "prompts.input_guard_ms": int,
    "prompts.capture_unblocking": bool,
    "gates.recording": str,
    "gates.recording_bar_s": int,
    "gates.hostkey_tick": bool,
    "reattach.auto": bool,
    "reattach.backoff_ms": list,
    "broker.url": str,
    "broker.timeout_s": int,
    "alerts.popup": bool,
    "alerts.read_timeout_s": int,
    "toasts.seconds": int,
    "toasts.max": int,
    "motion.level": str,
    "event_log.lines": int,
    "drive.default_cols": int,
    "drive.default_rows": int,
    "drive.answer_timeout_ms": int,
    "drive.wait_timeout_ms": int,
    "drive.wait_timeout_max_ms": int,
    "drive.settled_ms": int,
    "drive.send_gate": bool,
    "drive.brake_look_max_ms": int,
    "drive.reply_max_lines": int,
    "drive.events": list,
    "drive.output_events": str,
    "drive.switch_on_action": bool,
}
# `[keys.leader_map]`: one letter per action that takes a key after the leader (design §2.4).
KNOB_TYPES.update({f"keys.leader_map.{action_id}": str for action_id in LEADER_MAP_IDS})

# The phase that reads each key (DT19). A key owned by a phase at or below CURRENT_PHASE must be
# read by code outside this module; the owning phase's test proves it.
KNOB_OWNERS: dict[str, int] = {
    "update.check": 9,
    "update.interval_h": 9,
    "update.url": 9,
    "theme.name": 3,
    "theme.bg": 3,
    "theme.surface": 3,
    "theme.surface_2": 3,
    "theme.surface_3": 3,
    "theme.border": 3,
    "theme.border_on_surface": 3,
    "theme.text": 3,
    "theme.text_muted": 3,
    "theme.text_faint": 3,
    "theme.accent": 3,
    "theme.accent_hot": 3,
    "theme.accent_soft": 3,
    "theme.on_accent": 3,
    "theme.steel": 3,
    "theme.ok": 3,
    "theme.warn": 3,
    "theme.danger": 3,
    "theme.frame": 3,
    "theme.rec": 3,
    "theme.rec_list": 3,
    "theme.term.palette": 3,
    "theme.term.scheme": 3,
    "theme.term.bg": 3,
    "theme.term.fg": 3,
    "theme.term.cursor_colour": 3,
    "theme.term.cursor_typist": 3,
    "theme.term.cursor_viewer": 3,
    "theme.term.colors": 3,
    "theme.term.minimum_contrast": 3,
    "glyphs.set": 7,
    "term.library": 3,
    "term.scrollback_bytes": 3,
    "rail.side": 7,
    "rail.width": 7,
    "rail.phone": 7,
    "rail.chips": 7,
    "rail.chip_mark": 7,
    "rail.needs_you": 7,
    "rail.needs_you_scope": 7,
    "sidebar.width": 7,
    "sidebar.width_phone": 7,
    "sidebar.width_wide": 7,
    "sidebar.wide_from": 7,
    "sidebar.groups": 7,
    "sidebar.start_filter": 7,
    "sidebar.details_from": 7,
    "sidebar.close_on_pick": 7,
    "sidebar.open_when_no_session": 7,
    "fleet_view.tabs": 8,
    "fleet_view.group_by": 8,
    "fleet_view.show_ended": 8,
    "fleet_view.details_from": 8,
    "fleet_view.refresh_s": 2,
    "status.show": 5,
    "status.position": 5,
    "status.hint_priority": 5,
    "status.keys_indicator": 5,
    "status.stale_banner_fg": 5,
    "status.stale_banner_bg": 5,
    "status.stale_banner_pad": 5,
    "menu.search": 5,
    "menu.open_on_leader_after_ms": 5,
    "mouse.enabled": 8,
    "mouse.canvas_clicks": 8,
    "mouse.tooltips": 8,
    "keys.leader": 5,
    "size.auto_request": 3,
    "size.debounce_ms": 3,
    "input.guard": 4,
    "input.focus_reports": 4,
    "view.chrome": 7,
    "view.narrow_mode": 3,
    "view.follow_cursor": 3,
    "view.pan_step": 8,
    "scroll.wheel_lines": 8,
    "scroll.match_style": 8,
    "scroll.current_match_style": 8,
    "scroll.selection_style": 8,
    "copy.osc52": 8,
    "copy.max_bytes": 8,
    "copy.join_wrapped": 8,
    "view.inspector": UNASSIGNED,
    "render.max_fps": 3,
    "render.paint_background": 3,
    "sessions.id_min_hex": 7,
    "watch.held": 7,
    "watch.take_back_brings_to_front": 7,
    "prompts.input_guard_ms": 6,
    "prompts.capture_unblocking": 6,
    "gates.recording": 6,
    "gates.recording_bar_s": 6,
    "gates.hostkey_tick": 6,
    "reattach.auto": 6,
    "reattach.backoff_ms": 6,
    "broker.url": 2,
    "broker.timeout_s": 2,
    "alerts.popup": 6,
    "alerts.read_timeout_s": 2,
    "toasts.seconds": 6,
    "toasts.max": 6,
    "motion.level": 8,
    "event_log.lines": 2,
    "drive.default_cols": 9,
    "drive.default_rows": 9,
    "drive.answer_timeout_ms": 9,
    "drive.wait_timeout_ms": 9,
    "drive.wait_timeout_max_ms": 9,
    "drive.settled_ms": 9,
    "drive.send_gate": 9,
    "drive.brake_look_max_ms": 9,
    "drive.reply_max_lines": 9,
    "drive.events": 9,
    "drive.output_events": 9,
    "drive.switch_on_action": 9,
}
KNOB_OWNERS.update({f"keys.leader_map.{action_id}": 5 for action_id in LEADER_MAP_IDS})
# The letters of the actions phase 7 adds to the table (Switch to…, next and previous open session).
KNOB_OWNERS.update({f"keys.leader_map.{action_id}": 7 for action_id in ("switch", "next", "prev")})
# And phase 8's: End session…, the full view, the event log, Pan, All keys and Quit.
KNOB_OWNERS.update({f"keys.leader_map.{action_id}": 8
                    for action_id in ("end", "full_view", "event_log", "pan", "keys", "quit")})

# `profile` selects a profile; `[profiles.*]` are the packaged default's overlays, not knobs. The packaged
# file holds both; the operator's file may hold only `profile`, and a `[profiles.*]` table there is refused
# by `_read_file` (a copy of the default file would otherwise let a copied key override the profile).
_NON_KNOB_TOP = frozenset({"profile", "profiles"})


class ConfigError(Exception):
    """Start-up cannot proceed: an unknown key, a bad type, or a value the plan refuses."""


def _env_name(key: str) -> str:
    return "TSB_TUI_" + key.upper().replace(".", "_")


_ENV_TO_KEY = {_env_name(key): key for key in KNOB_TYPES}
# A collision would make one env var ambiguous; the key set is fixed and small.
assert len(_ENV_TO_KEY) == len(KNOB_TYPES), "TSB_TUI_* env names collide"


def _parse_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ConfigError(f"{key}: expected a boolean, got {value!r}")


def _parse_int(key: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{key}: expected an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ConfigError(f"{key}: expected an integer, got {value!r}")


def _parse_float(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{key}: expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ConfigError(f"{key}: expected a number, got {value!r}")


def _parse_list(key: str, value: Any) -> list:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ConfigError(f"{key}: expected a list, got {value!r}")


_PARSERS = {bool: _parse_bool, int: _parse_int, float: _parse_float, list: _parse_list}


def _coerce(key: str, value: Any) -> Any:
    """A value from the file, the environment or `--set`, held to the key's declared type."""
    wanted = KNOB_TYPES.get(key)
    if wanted is None:
        raise ConfigError(f"{key}: unknown configuration key")
    if wanted is str:
        if not isinstance(value, str):
            raise ConfigError(f"{key}: expected a string, got {value!r}")
        return value
    return _PARSERS[wanted](key, value)


def _flatten(node: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for name, value in node.items():
        key = f"{prefix}{name}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, key + "."))
        else:
            flat[key] = value
    return flat


def _default_path(environ: Mapping[str, str]) -> Path:
    if environ.get("XDG_CONFIG_HOME"):
        base = environ["XDG_CONFIG_HOME"]
    elif sys.platform == "win32":
        appdata = environ.get("APPDATA") or str(Path(environ.get("HOME", "~")).expanduser() / "AppData" / "Roaming")
        base = appdata
    else:
        base = str(Path(environ.get("HOME", "~")).expanduser() / ".config")
    return Path(base) / "tsb-tui" / "config.toml"


def default_config_path(environ: Mapping[str, str]) -> Path:
    """Use the launcher's config file if present, otherwise the OS config file."""
    home = environ.get("TSB_TUI_HOME")
    if home:
        portable = Path(home) / "config.toml"
        if portable.is_file():
            return portable
    return _default_path(environ)


def resolve_broker(cli_value: str | None, cfg: Config) -> str | None:
    """The explicit flag wins over the layered broker.url knob."""
    return cli_value or cfg["broker.url"] or None


def _load_defaults() -> dict[str, Any]:
    text = (Path(__file__).with_name("config_default.toml")).read_text(encoding="utf-8")
    doc = tomllib.loads(text)
    return _flatten({k: v for k, v in doc.items() if k not in _NON_KNOB_TOP})


def _profile_overlay(doc: Mapping[str, Any], profile: str) -> dict[str, Any]:
    profiles = doc.get("profiles", {})
    if isinstance(profiles, Mapping):
        for name, entry in profiles.items():
            if isinstance(entry, Mapping) and "drive.send_gate" in _flatten(entry):
                raise ConfigError(f"profiles.{name}.drive.send_gate: config file only")
    section = profiles.get(profile, {}) if isinstance(profiles, Mapping) else {}
    if not isinstance(section, Mapping):
        raise ConfigError(f"profiles.{profile}: must be a table")
    return _flatten(section)


def _read_file(path: Path | None) -> tuple[dict[str, Any], str | None]:
    """(knob values, profile named in the file). An absent file is not an error."""
    if path is None or not path.is_file():
        return {}, None
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    profile = doc.get("profile")
    if profile is not None and not isinstance(profile, str):
        raise ConfigError(f"{path}: profile must be a string")
    if "profiles" in doc:
        # A `[profiles.*]` table belongs to the packaged file, not the operator's: a copied key in it would
        # be read as a knob overlay and override the profile in force, so it is refused, never dropped.
        key = next(iter(_flatten({"profiles": doc["profiles"]})), "profiles")
        why = ("drive.send_gate is config file only" if key.endswith(".drive.send_gate") else
               "a [profiles.*] table is not a knob and cannot be set here")
        raise ConfigError(f"{path}: {key}: {why}")
    knobs = _flatten({k: v for k, v in doc.items() if k not in _NON_KNOB_TOP})
    for key in knobs:
        if key not in KNOB_TYPES:
            raise ConfigError(f"{path}: {key}: unknown configuration key")
    return knobs, profile


def _env_overlay(environ: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, raw in environ.items():
        if not name.startswith("TSB_TUI_") or name in {"TSB_TUI_PROFILE", "TSB_TUI_HOME"}:
            continue
        if name == "TSB_TUI_DRIVE_SEND_GATE":
            raise ConfigError(f"{name}: drive.send_gate is config file only")
        key = _ENV_TO_KEY.get(name)
        if key is None:
            raise ConfigError(f"{name}: unknown configuration key")
        values[key] = raw
    return values


def _set_overlay(overrides: Iterable[str] | Mapping[str, Any]) -> dict[str, Any]:
    items = overrides.items() if isinstance(overrides, Mapping) else (
        item.partition("=")[::2] for item in overrides
    )
    values: dict[str, Any] = {}
    for key, raw in items:
        key = key.strip()
        if key == "drive.send_gate":
            raise ConfigError("drive.send_gate: config file only")
        if key not in KNOB_TYPES:
            raise ConfigError(f"{key}: unknown configuration key")
        values[key] = raw
    return values


class Config:
    """The resolved knobs. Read a key with `config["a.b"]` or `config.get("a.b")`."""

    def __init__(self, values: Mapping[str, Any], profile: str,
                 sources: Mapping[str, str] | None = None, *,
                 user_dir: Path | None = None,
                 scheme: Any = None, chrome: Any = None,
                 defaults: Mapping[str, Any] | None = None,
                 profile_values: Mapping[str, Any] | None = None) -> None:
        self._values = dict(values)
        self.profile = profile
        self._sources = dict(sources or {})
        # What `load` resolved alongside the knobs: the directory the user's theme and scheme files
        # live in, the selected colour scheme, and the selected chrome theme (TT28). `load` is the
        # only builder. The packaged defaults a switch falls back to when a new theme lacks a role
        # (phases 7, 10). The profile values (phase 10) are what unsave_names() falls back to.
        self.user_dir = user_dir
        self.scheme = scheme
        self.chrome = chrome
        self._defaults = dict(defaults) if defaults is not None else {}
        self._profile_values = dict(profile_values) if profile_values is not None else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __contains__(self, key: str) -> bool:
        return key in self._values

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    @property
    def default_roles(self) -> dict[str, str]:
        """The packaged default chrome roles, keyed by role name (`surface`, `text_muted`): what a theme
        file that lacks a role falls back to (TT12), and what a partial theme's swatch paints."""
        return role_colours(self._defaults)

    def source(self, key: str) -> str:
        """Where the value in force came from: the packaged default, a profile, the config file, the environment,
        `--set`, or a change made during this run (Settings… shows it)."""
        return self._sources.get(key, "default")

    def set_runtime(self, key: str, value: Any) -> None:
        """Change one knob for this run only, as the operator does from a dialog (the Size dialog's
        auto toggle). Nothing is written back to the config file."""
        if key not in KNOB_TYPES:
            raise ConfigError(f"{key}: unknown configuration key")
        self._values[key] = _coerce(key, value)
        self._sources[key] = "this run"

    def select_look(self, theme_id: str | None = None, scheme_slug: str | None = None, *,
                    source: str = "this run") -> None:
        """Switch the look at run time (B4, TT12). Load and validate the named theme and/or scheme; a
        failure raises `ConfigError` and changes nothing. Set the given names with `source`. For a new
        theme, every role whose source is the packaged default or a theme file takes the new theme's value
        (source `theme <id>`), or the packaged default when the theme lacks it; a role set by any other
        layer stays. Update `chrome` and `scheme`."""
        new_chrome = None
        if theme_id is not None:
            try:
                new_chrome = find_theme(theme_id, self.user_dir)
            except ThemeFileError as exc:
                raise ConfigError(f"theme.name = {theme_id!r}: {exc}") from None
        new_scheme = None
        if scheme_slug is not None:
            try:
                new_scheme = find_scheme(scheme_slug, self.user_dir)
            except SchemeError as exc:
                raise ConfigError(str(exc)) from None

        if theme_id is not None:
            self._values["theme.name"] = theme_id
            self._sources["theme.name"] = source
            for role in ROLES:
                key = f"theme.{role}"
                src = self._sources.get(key, "default")
                if src == "default" or src.startswith("theme "):
                    if role in new_chrome.roles:
                        self._values[key] = new_chrome.roles[role]
                        self._sources[key] = f"theme {new_chrome.id}"
                    else:
                        self._values[key] = self._defaults[key]
                        self._sources[key] = "default"
            surface_key = "theme.surface"
            if self._sources.get(surface_key, "default").startswith(("default", "theme ")):
                bg = self._values["theme.bg"]
                variant = new_chrome.variant or ("light" if relative_luminance(bg) > 0.179 else "dark")
                self._values[surface_key] = surface_colour(bg, variant)
                self._sources[surface_key] = f"theme {new_chrome.id}"
            self.chrome = new_chrome
            if self.chrome.variant is None:
                # OPEN-T5 (ruled): a theme file with no variant gets it computed from its resolved bg, as load does.
                self.chrome = replace(self.chrome, variant=(
                    "light" if relative_luminance(self._values["theme.bg"]) > 0.179 else "dark"))
        if scheme_slug is not None:
            self._values["theme.term.scheme"] = scheme_slug
            self._sources["theme.term.scheme"] = source
            self.scheme = new_scheme

    def look(self) -> dict[str, Any]:
        """The look in force: the two names (value and source), every role (value and source), chrome,
        scheme — everything `restore_look` needs to put it back (TT12)."""
        return {
            "name": (self._values["theme.name"], self._sources.get("theme.name", "default")),
            "scheme_name": (self._values["theme.term.scheme"], self._sources.get("theme.term.scheme", "default")),
            "roles": {role: (self._values[f"theme.{role}"], self._sources.get(f"theme.{role}", "default"))
                      for role in ROLES},
            "chrome": self.chrome,
            "scheme": self.scheme,
        }

    def restore_look(self, saved: dict[str, Any]) -> None:
        """Put back a look `look()` captured: the names, every role value and source, chrome and scheme."""
        self._values["theme.name"], self._sources["theme.name"] = saved["name"]
        self._values["theme.term.scheme"], self._sources["theme.term.scheme"] = saved["scheme_name"]
        for role, (value, src) in saved["roles"].items():
            self._values[f"theme.{role}"] = value
            self._sources[f"theme.{role}"] = src
        self.chrome = saved["chrome"]
        self.scheme = saved["scheme"]

    def unsave_names(self) -> None:
        """Each name whose source starts with `saved for ` goes back to its profile value (source
        `profile <name>`) or its packaged default (source `default`), from what `load` stored (phase 7).
        TT16: a login without an entry after one with it gets the default names back.

        Apply each fallback through select_look so chrome, scheme, and all role values/sources match
        the fallback look, not just the two names. A failure changes nothing: the first fallback is
        applied before the second, so if the second raises we restore the captured look and re-raise."""
        before = self.look()
        try:
            for key in ("theme.name", "theme.term.scheme"):
                if self._sources.get(key, "").startswith("saved for "):
                    if key in self._profile_values:
                        fallback_value = self._profile_values[key]
                        fallback_source = f"profile {self.profile}"
                    else:
                        fallback_value = self._defaults[key]
                        fallback_source = "default"

                    # Apply through select_look so chrome/scheme/roles all match the fallback
                    if key == "theme.name":
                        self.select_look(theme_id=fallback_value, source=fallback_source)
                    else:  # theme.term.scheme
                        self.select_look(scheme_slug=fallback_value, source=fallback_source)
        except ConfigError:
            self.restore_look(before)
            raise

    def leader_overrides(self) -> dict[str, str]:
        """`[keys.leader_map]` as the action ids it rebinds."""
        return {key.removeprefix("keys.leader_map."): value for key, value in self._values.items()
                if key.startswith("keys.leader_map.")}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Config) and self._values == other._values and self.profile == other.profile


def load(*, profile: str | None = None, config_file: Path | str | None = None,
         environ: Mapping[str, str] | None = None,
         overrides: Iterable[str] | Mapping[str, Any] = ()) -> Config:
    """Resolve the configuration, or raise `ConfigError` naming the key that failed."""
    env = dict(os.environ if environ is None else environ)
    defaults_text = (Path(__file__).with_name("config_default.toml")).read_text(encoding="utf-8")
    defaults_doc = tomllib.loads(defaults_text)
    file_path = Path(config_file) if config_file is not None else default_config_path(env)
    file_values, file_profile = _read_file(file_path)

    chosen = profile or env.get("TSB_TUI_PROFILE") or file_profile or "default"
    if chosen not in PROFILES:
        raise ConfigError(f"profile: {chosen!r} is not one of {', '.join(PROFILES)}")

    merged = _load_defaults()
    defaults = dict(merged)  # the packaged defaults a run-time switch falls back to (TT28, phases 7, 10)
    sources: dict[str, str] = {key: "default" for key in merged}

    def layer(values: Mapping[str, Any], source: str | Callable[[str], str]) -> None:
        merged.update(values)
        for key in values:
            sources[key] = source(key) if callable(source) else source

    # TT12: build the four overlays first, resolve `theme.name` through them (defaults < profile
    # < file < env < --set), find its file, and layer the theme's roles between the packaged
    # defaults and the profile. A role the theme lacks keeps the packaged default (terracotta).
    profile_values = _profile_overlay(defaults_doc, chosen)
    env_values = _env_overlay(env)
    set_values = _set_overlay(overrides)

    name = merged["theme.name"]
    for overlay in (profile_values, file_values, env_values, set_values):
        if "theme.name" in overlay:
            name = _coerce("theme.name", overlay["theme.name"])

    # TT10, TT28: resolve the two files inside `load`, so a bad or missing one is a start-up
    # ConfigError naming the file and the field, reported by every entry point.
    user_config_dir = _default_path(env).parent
    try:
        chrome = find_theme(name, user_config_dir)
    except ThemeFileError as exc:
        raise ConfigError(f"theme.name = {name!r}: {exc}") from None

    theme_roles = dict(chrome.roles)
    theme_bg = theme_roles.get("bg", defaults["theme.bg"])
    variant = chrome.variant or ("light" if relative_luminance(theme_bg) > 0.179 else "dark")
    theme_roles["surface"] = surface_colour(theme_bg, variant)
    layer({f"theme.{role}": value for role, value in theme_roles.items()}, f"theme {chrome.id}")
    layer(profile_values, f"profile {chosen}")
    layer(file_values, f"config file {file_path}")
    layer(env_values, _env_name)
    layer(set_values, "--set")

    for key in merged:
        if key not in KNOB_TYPES:
            raise ConfigError(f"{key}: unknown configuration key")

    resolved = {key: _coerce(key, value) for key, value in merged.items()}

    for key, refused in NOT_BUILT_AT_E2.items():
        if resolved.get(key) in refused:
            raise ConfigError(f"{key} = {resolved[key]!r}: not built at E2")
    if resolved.get("gates.recording") not in ("first-attach", "every-attach"):
        raise ConfigError(
            f"gates.recording = {resolved.get('gates.recording')!r}: expected first-attach or every-attach")

    _check_chrome_knobs(resolved)
    _check_theme_knobs(resolved)
    _check_fleet_knobs(resolved)
    _check_broker_knobs(resolved)
    if resolved["update.interval_h"] < 1:
        raise ConfigError("update.interval_h: expected at least 1")

    # OPEN-T5 (ruled 2026-10-15): a theme file with no `variant` gets it computed from its `bg`
    # luminance, as a scheme's is (TT9's rule). TT12: the rule reads the `theme.bg` in force after
    # every layer, so an operator who sets `theme.bg` light gets `dark=False`; a theme file that
    # carries `bg` keeps the variant parse_theme computed from it.
    if chrome.variant is None:
        chrome = replace(chrome, variant="light" if relative_luminance(resolved["theme.bg"]) > 0.179 else "dark")

    try:
        scheme = find_scheme(resolved["theme.term.scheme"], user_config_dir)
    except SchemeError as exc:
        raise ConfigError(str(exc)) from None

    return Config(resolved, chosen, sources, user_dir=user_config_dir, scheme=scheme, chrome=chrome,
                  defaults=defaults, profile_values=profile_values)


def _check_chrome_knobs(values: Mapping[str, Any]) -> None:
    """The status row, Menu and leader knobs hold values the chrome can honour, or start-up fails
    naming the key (Rule 12, DT19)."""
    leader = values["keys.leader"]
    if leader not in LEADER_LABELS:
        raise ConfigError(
            f"keys.leader = {leader!r}: not a leader the TUI can use (one of {', '.join(LEADER_LABELS)})")
    overrides = {key.removeprefix("keys.leader_map."): value for key, value in values.items()
                 if key.startswith("keys.leader_map.")}
    try:
        leader_keys(overrides)
    except KeymapError as exc:
        raise ConfigError(str(exc)) from None
    if values["status.position"] not in STATUS_POSITIONS:
        raise ConfigError(
            f"status.position = {values['status.position']!r}: expected one of {', '.join(STATUS_POSITIONS)}")
    unknown = [name for name in values["status.hint_priority"] if name not in HINT_NAMES]
    if unknown:
        raise ConfigError(
            f"status.hint_priority: unknown hint {unknown[0]!r} (one of {', '.join(sorted(HINT_NAMES))})")
    for key in ("status.stale_banner_fg", "status.stale_banner_bg"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", values[key]):
            raise ConfigError(f"{key} = {values[key]!r}: expected a #rrggbb colour")
    if not 0 <= values["status.stale_banner_pad"] <= 8:
        raise ConfigError(f"status.stale_banner_pad = {values['status.stale_banner_pad']}: expected 0 to 8")
    if values["menu.open_on_leader_after_ms"] < 1:
        raise ConfigError("menu.open_on_leader_after_ms: expected at least 1")


def _check_theme_knobs(values: Mapping[str, Any]) -> None:
    """Phase 1: theme.term.bg holds "scheme", "outer" or #rrggbb.
    Phase 3: theme.term.cursor_colour holds "role" or "scheme"; theme.term.colors is [] or exactly 16.
    Phase 12: theme.term.minimum_contrast is 1.0 (off) to 21.0, and above 1.0 is refused under
    palette = "outer", where the colours are unknown (TT21)."""
    bg = values["theme.term.bg"]
    if bg not in ("scheme", "outer") and not _HEX_PATTERN.fullmatch(bg):
        raise ConfigError(f"theme.term.bg = {bg!r}: expected 'scheme', 'outer' or #rrggbb")

    cursor_colour = values["theme.term.cursor_colour"]
    if cursor_colour not in ("role", "scheme"):
        raise ConfigError(f"theme.term.cursor_colour = {cursor_colour!r}: expected 'role' or 'scheme'")

    colors = values["theme.term.colors"]
    if not isinstance(colors, list):
        raise ConfigError("theme.term.colors: expected a list")
    if colors and len(colors) != 16:
        raise ConfigError(f"theme.term.colors: expected [] or exactly 16 colours, got {len(colors)}")

    minimum_contrast = values["theme.term.minimum_contrast"]
    if not 1.0 <= minimum_contrast <= 21.0:
        raise ConfigError(
            f"theme.term.minimum_contrast = {minimum_contrast}: expected 1.0 (off) to 21.0")
    if minimum_contrast > 1.0 and values["theme.term.palette"] == "outer":
        raise ConfigError(
            f"theme.term.minimum_contrast = {minimum_contrast}: refused under theme.term.palette "
            "= 'outer', where the colours are unknown")


def _one_of(values: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> None:
    if values[key] not in allowed:
        raise ConfigError(f"{key} = {values[key]!r}: expected one of {', '.join(allowed)}")


def _names_from(values: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> None:
    """A list knob that names some of `allowed`, each at most once: a typo would silently drop a chip, a group or
    a reason, so it fails naming the key."""
    named = values[key]
    for name in named:
        if name not in allowed:
            raise ConfigError(f"{key}: unknown name {name!r} (one of {', '.join(allowed)})")
    if len(set(named)) != len(named):
        raise ConfigError(f"{key}: a name is listed twice")


def _check_fleet_knobs(values: Mapping[str, Any]) -> None:
    """The rail, the sidebar, the glyph set and the chrome hold values the chrome can honour (phase 7)."""
    try:
        glyphs.check(values["glyphs.set"])
    except glyphs.GlyphError as exc:
        raise ConfigError(str(exc)) from None
    _one_of(values, "rail.side", RAIL_SIDES)
    _one_of(values, "rail.phone", RAIL_PHONE)
    _one_of(values, "rail.needs_you_scope", NEEDS_YOU_SCOPES)
    _one_of(values, "sidebar.start_filter", START_FILTERS)
    _one_of(values, "sidebar.close_on_pick", CLOSE_ON_PICK)
    _one_of(values, "view.chrome", CHROMES)
    _names_from(values, "rail.chips", CHIP_SOURCES)
    _names_from(values, "rail.chip_mark", CHIP_MARKS)
    _names_from(values, "rail.needs_you", NEEDS_REASONS)
    _names_from(values, "sidebar.groups", GROUP_ORDER)
    if values["rail.width"] < MIN_RAIL_WIDTH:
        raise ConfigError(f"rail.width = {values['rail.width']}: expected at least {MIN_RAIL_WIDTH} (three "
                          "glyph cells and the hairline)")
    for key in ("sidebar.width", "sidebar.width_phone", "sidebar.width_wide"):
        if values[key] < MIN_SIDEBAR_WIDTH:
            raise ConfigError(f"{key} = {values[key]}: expected at least {MIN_SIDEBAR_WIDTH}")
    for key in ("sidebar.wide_from", "sidebar.details_from"):
        if values[key] < 1:
            raise ConfigError(f"{key} = {values[key]}: expected at least 1")
    if values["sessions.id_min_hex"] < 1:
        raise ConfigError(f"sessions.id_min_hex = {values['sessions.id_min_hex']}: expected at least 1")
    _check_view_knobs(values)
    _check_drive_knobs(values)
    if values["fleet_view.refresh_s"] < 0:
        raise ConfigError(f"fleet_view.refresh_s = {values['fleet_view.refresh_s']}: expected 0 (manual) or more")


def _check_broker_knobs(values: Mapping[str, Any]) -> None:
    """`broker.timeout_s` is a real wait: below 1 s nothing can be answered in it."""
    if values["broker.timeout_s"] < 1:
        raise ConfigError(f"broker.timeout_s = {values['broker.timeout_s']}: expected at least 1")


def _check_view_knobs(values: Mapping[str, Any]) -> None:
    """The full Fleet view, the mouse, the pan and the motion level hold values the TUI can honour (phase 8)."""
    _names_from(values, "fleet_view.tabs", FLEET_TABS)
    if "sessions" not in values["fleet_view.tabs"]:
        raise ConfigError("fleet_view.tabs: must list 'sessions' (the full view has no other tab built)")
    _one_of(values, "fleet_view.group_by", GROUP_BYS)
    _one_of(values, "mouse.canvas_clicks", CANVAS_CLICKS)
    _one_of(values, "view.inspector", INSPECTORS)
    _one_of(values, "motion.level", MOTION_LEVELS)
    _one_of(values, "view.follow_cursor", FOLLOW_CURSORS)
    if not MIN_MAX_FPS <= values["render.max_fps"] <= MAX_MAX_FPS:
        raise ConfigError(f"render.max_fps = {values['render.max_fps']!r}: expected an integer from "
                          f"{MIN_MAX_FPS} to {MAX_MAX_FPS}")
    if values["fleet_view.details_from"] < 1:
        raise ConfigError(f"fleet_view.details_from = {values['fleet_view.details_from']}: expected at least 1")
    if values["view.pan_step"] < 1:
        raise ConfigError(f"view.pan_step = {values['view.pan_step']}: expected at least 1")
    if values["scroll.wheel_lines"] < 1:
        raise ConfigError(f"scroll.wheel_lines = {values['scroll.wheel_lines']}: expected at least 1")
    if values["copy.max_bytes"] < 1:
        raise ConfigError(f"copy.max_bytes = {values['copy.max_bytes']}: expected at least 1")
    for key in ("scroll.match_style", "scroll.current_match_style", "scroll.selection_style"):
        try:
            Style.parse(values[key])
        except StyleSyntaxError as exc:
            raise ConfigError(f"{key} = {values[key]!r}: not a style Rich can parse ({exc})") from exc


def _check_drive_knobs(values: Mapping[str, Any]) -> None:
    """`tsb-tui drive`'s knobs hold values drive can honour (phase 9): the events it may write, the output counts it
    may give, and sizes and limits that mean something."""
    _names_from(values, "drive.events", EVENT_NAMES)
    _one_of(values, "drive.output_events", OUTPUT_MODES)
    for key in ("drive.default_cols", "drive.default_rows", "drive.wait_timeout_max_ms", "drive.reply_max_lines",
                "drive.answer_timeout_ms", "drive.brake_look_max_ms"):
        if values[key] < 1:
            raise ConfigError(f"{key} = {values[key]}: expected at least 1")
    for key, floor in (("drive.wait_timeout_ms", 5000), ("drive.settled_ms", 800)):
        if values[key] < floor:
            raise ConfigError(f"{key} = {values[key]}: expected at least {floor}")


def knobs_pending(through: int = CURRENT_PHASE) -> list[str]:
    """Keys whose owner phase has not landed yet, sorted. Empty once every phase has landed."""
    return sorted(key for key, owner in KNOB_OWNERS.items() if owner > through)


def knobs_owned_through(through: int = CURRENT_PHASE) -> list[str]:
    """Keys the phases up to `through` own; each must be read outside this module (DT19)."""
    return sorted(key for key, owner in KNOB_OWNERS.items() if owner <= through)
