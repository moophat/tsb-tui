"""Colour scheme and chrome theme files: catalogues, validation and lookup (TT11).

Both kinds live in the package (`tsb_tui/{schemes,themes}/`) and in the user's config directory
(`$XDG_CONFIG_HOME/tsb-tui/{schemes,themes}/`). A user file with a packaged slug replaces it.
This module loads and validates both catalogues; `config.load` resolves the two names and keeps
the selected files in the `Config`.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .theme import relative_luminance

_SLUG = re.compile(r"[a-z0-9-]+")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


class SchemeError(ValueError):
    """A scheme file is malformed or names a scheme that is not found. The message names the file
    and the field that failed, or lists the slugs found when the selected one is missing."""




@dataclass(frozen=True)
class Scheme:
    """A colour scheme: the 16 ANSI colours, foreground, background, optional cursor and selection
    colours, optional extended 256-colour palette, provenance, and whether it is the user's own."""

    slug: str
    name: str
    variant: str
    ansi: tuple[str, ...]  # exactly 16
    foreground: str
    background: str
    cursor: str | None
    cursor_text: str | None
    selection_background: str | None
    selection_foreground: str | None
    extended: tuple[str | None, ...] | None  # None or exactly 240 entries
    source: dict[str, str] | None
    path: Path
    yours: bool


def parse_scheme(path: Path, *, yours: bool = False) -> Scheme:
    """Parse and validate a scheme JSON file, or raise `SchemeError` naming file and field."""
    slug = path.stem
    if not _SLUG.fullmatch(slug):
        raise SchemeError(f"{path}: slug {slug!r}: must match [a-z0-9-]+")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SchemeError(f"{path}: {exc}") from None
    # Decode as UTF-8 here, never let json sniff the file's encoding: a valid UTF-16 or UTF-32
    # file with a BOM would load through json.loads(bytes) and reach the "not UTF-8" rule never.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SchemeError(f"{path}: not UTF-8") from None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemeError(f"{path}: {exc}") from None

    if not isinstance(doc, dict):
        raise SchemeError(f"{path}: top level: must be an object")

    # TT9: unknown keys are refused
    allowed = {"name", "variant", "ansi", "foreground", "background", "cursor", "cursorText",
               "selectionBackground", "selectionForeground", "extendedAnsi", "source"}
    unknown = set(doc) - allowed
    if unknown:
        raise SchemeError(f"{path}: {min(unknown)}: unknown key")

    # Required keys
    if "name" not in doc:
        raise SchemeError(f"{path}: name: required")
    name = doc["name"]
    if not isinstance(name, str) or not name:
        raise SchemeError(f"{path}: name: must be a non-empty string")

    if "ansi" not in doc:
        raise SchemeError(f"{path}: ansi: required")
    if not isinstance(doc["ansi"], list) or len(doc["ansi"]) != 16:
        raise SchemeError(f"{path}: ansi: must be exactly 16 colours")
    ansi = tuple(_parse_colour(path, f"ansi[{i}]", c) for i, c in enumerate(doc["ansi"]))

    if "foreground" not in doc:
        raise SchemeError(f"{path}: foreground: required")
    foreground = _parse_colour(path, "foreground", doc["foreground"])

    if "background" not in doc:
        raise SchemeError(f"{path}: background: required")
    background = _parse_colour(path, "background", doc["background"])

    # TT9: variant computed from background luminance if absent
    if "variant" in doc:
        variant = doc["variant"]
        if variant not in ("dark", "light"):
            raise SchemeError(f"{path}: variant: must be 'dark' or 'light'")
    else:
        variant = "light" if relative_luminance(background) > 0.179 else "dark"

    # Optional colour keys
    cursor = _parse_colour(path, "cursor", doc["cursor"]) if "cursor" in doc else None
    cursor_text = _parse_colour(path, "cursorText", doc["cursorText"]) if "cursorText" in doc else None
    selection_background = (_parse_colour(path, "selectionBackground", doc["selectionBackground"])
                            if "selectionBackground" in doc else None)
    selection_foreground = (_parse_colour(path, "selectionForeground", doc["selectionForeground"])
                            if "selectionForeground" in doc else None)

    # extendedAnsi: None or exactly 240 entries (indexes 16-255), each a colour or null
    extended: tuple[str | None, ...] | None = None
    if "extendedAnsi" in doc:
        ext_list = doc["extendedAnsi"]
        if not isinstance(ext_list, list) or len(ext_list) != 240:
            raise SchemeError(f"{path}: extendedAnsi: must be exactly 240 entries")
        extended = tuple(
            None if entry is None else _parse_colour(path, f"extendedAnsi[{i}]", entry)
            for i, entry in enumerate(ext_list)
        )

    # source: optional object of strings
    source: dict[str, str] | None = None
    if "source" in doc:
        if not isinstance(doc["source"], dict):
            raise SchemeError(f"{path}: source: must be an object")
        source = doc["source"]
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in source.items()):
            raise SchemeError(f"{path}: source: all keys and values must be strings")

    return Scheme(
        slug=slug, name=name, variant=variant, ansi=ansi, foreground=foreground,
        background=background, cursor=cursor, cursor_text=cursor_text,
        selection_background=selection_background, selection_foreground=selection_foreground,
        extended=extended, source=source, path=path, yours=yours
    )


def _parse_colour(path: Path, field: str, value: Any) -> str:
    """Parse a hex colour, case-insensitive, stored lower case."""
    if not isinstance(value, str):
        raise SchemeError(f"{path}: {field}: must be a string")
    if not _HEX.fullmatch(value):
        raise SchemeError(f"{path}: {field}: must be #rrggbb")
    return value.lower()


def packaged_dir(kind: str) -> Path:
    """The packaged directory for `kind` ('schemes' or 'themes')."""
    return Path(__file__).parent / kind


def user_kind_dir(user_dir: Path, kind: str) -> Path:
    """The user's directory for `kind` under `$XDG_CONFIG_HOME/tsb-tui/` (`user_dir` is the
    `tsb-tui` directory itself)."""
    return user_dir / kind


def _missing_scheme(slug: str, user_dir: Path) -> SchemeError:
    """The refusal for a scheme name that is not an id or is not found: the message names the slugs
    found and both directories, so a typo is fixable from the message alone."""
    user_schemes_dir = user_kind_dir(user_dir, "schemes")
    user_slugs = sorted(p.stem for p in user_schemes_dir.glob("*.json")) if user_schemes_dir.is_dir() else []
    packaged_slugs = sorted(p.stem for p in packaged_dir("schemes").glob("*.json"))
    found = ", ".join(packaged_slugs + user_slugs) or "none"
    return SchemeError(
        f"theme.term.scheme = {slug!r}: no scheme found (found: {found}; "
        f"searched {packaged_dir('schemes')} and {user_schemes_dir})"
    )


def find_scheme(slug: str, user_dir: Path) -> Scheme:
    """Find and parse the scheme by slug, user file first, then packaged. TT8: the name must match
    the id rule `[a-z0-9-]+` before any lookup, so a traversal name can never reach a file outside
    the folders; raise `SchemeError` if it does not, listing both directories and the slugs found."""
    if not _SLUG.fullmatch(slug):
        raise _missing_scheme(slug, user_dir)

    user_path = user_kind_dir(user_dir, "schemes") / f"{slug}.json"
    if user_path.is_file():
        return parse_scheme(user_path, yours=True)

    packaged_path = packaged_dir("schemes") / f"{slug}.json"
    if packaged_path.is_file():
        return parse_scheme(packaged_path, yours=False)

    raise _missing_scheme(slug, user_dir)


def scheme_catalogue(user_dir: Path) -> tuple[list[Scheme], list[tuple[Path, str]]]:
    """Load every scheme from both directories. Returns (good schemes, bad files as (path, message)).
    A user file with a packaged slug replaces it and is marked `yours`. Order by slug within each
    directory (the picker sorts for display)."""
    good: dict[str, Scheme] = {}
    bad: list[tuple[Path, str]] = []

    # Packaged first
    for path in sorted(packaged_dir("schemes").glob("*.json")):
        try:
            scheme = parse_scheme(path, yours=False)
            good[scheme.slug] = scheme
        except SchemeError as exc:
            bad.append((path, str(exc)))

    # User files, replacing packaged ones
    user_schemes_dir = user_kind_dir(user_dir, "schemes")
    if user_schemes_dir.is_dir():
        for path in sorted(user_schemes_dir.glob("*.json")):
            try:
                scheme = parse_scheme(path, yours=True)
                good[scheme.slug] = scheme
            except SchemeError as exc:
                bad.append((path, str(exc)))

    return (sorted(good.values(), key=lambda s: s.slug), bad)


# The twenty chrome roles (design B3: the seventeen of `config_default.toml` plus `frame`, `rec`
# and `rec_list`). A theme file may carry any subset; a role it lacks keeps the packaged default.
ROLES = ("bg", "surface", "surface_2", "surface_3", "border", "border_on_surface",
         "text", "text_muted", "text_faint", "accent", "accent_hot", "accent_soft",
         "on_accent", "steel", "ok", "warn", "danger", "frame", "rec", "rec_list")


class ThemeFileError(ValueError):
    """A chrome theme file is malformed, or the selected theme is not found. The message names the
    file and the field that failed, or lists the themes found when the selected one is missing."""


@dataclass(frozen=True)
class ChromeTheme:
    """A chrome theme file: its id (the file stem), name, variant, suggested scheme (never looked
    up — ruling 2), the roles it carries, provenance, and whether it is the user's own.
    `variant` is None when the file carries no `variant` and no `bg` to compute it from; the layer
    that resolves the roles computes it from the theme's resolved `bg` (OPEN-T5, ruled 2026-10-15)."""

    id: str
    name: str
    variant: str | None
    scheme: str | None
    roles: dict[str, str]  # role name -> lower-case hex
    path: Path
    yours: bool


def parse_theme(path: Path, *, yours: bool = False) -> ChromeTheme:
    """Parse and validate a chrome theme TOML file, or raise `ThemeFileError` naming file and field.
    The file holds one top-level table, `[theme]`, whose keys are the roles plus `name`, `variant`
    and `scheme`; a role may be absent (a partial theme, TT12)."""
    theme_id = path.stem
    if not _SLUG.fullmatch(theme_id):
        raise ThemeFileError(f"{path}: id {theme_id!r}: must match [a-z0-9-]+")

    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise ThemeFileError(f"{path}: not UTF-8") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ThemeFileError(f"{path}: {exc}") from None

    # TT8: one top-level table, [theme]
    extra = sorted(k for k in doc if k != "theme")
    if extra:
        raise ThemeFileError(f"{path}: {extra[0]}: the file must hold one [theme] table")
    section = doc.get("theme")
    if not isinstance(section, dict):
        raise ThemeFileError(f"{path}: theme: must be a table")

    allowed = set(ROLES) | {"name", "variant", "scheme"}
    for key in sorted(section):
        if key not in allowed:
            raise ThemeFileError(f"{path}: {key}: unknown key")

    # TT8: `name`, when present, equals the file stem; a partial theme is named by its stem
    name = theme_id
    if "name" in section:
        nm = section["name"]
        if not isinstance(nm, str) or nm != theme_id:
            raise ThemeFileError(f"{path}: name: must equal the file stem {theme_id!r}")

    variant = None
    if "variant" in section:
        if section["variant"] not in ("dark", "light"):
            raise ThemeFileError(f"{path}: variant: must be 'dark' or 'light'")
        variant = section["variant"]

    # Ruling 2: `scheme` is a suggestion only; it is never looked up here
    scheme = None
    if "scheme" in section:
        sc = section["scheme"]
        if not isinstance(sc, str) or not _SLUG.fullmatch(sc):
            raise ThemeFileError(f"{path}: scheme: must match [a-z0-9-]+")
        scheme = sc

    roles: dict[str, str] = {}
    for role in ROLES:
        if role in section:
            value = section[role]
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ThemeFileError(f"{path}: {role}: must be #rrggbb")
            roles[role] = value.lower()

    # OPEN-T5 (ruled 2026-10-15): a file with no `variant` gets it computed from its `bg` luminance,
    # as a scheme's is (TT9's rule). With no `bg` either, the theme's bg is resolved first (TT12:
    # missing roles come from the packaged defaults), so `config.load` computes it.
    if variant is None and "bg" in roles:
        variant = "light" if relative_luminance(roles["bg"]) > 0.179 else "dark"

    return ChromeTheme(id=theme_id, name=name, variant=variant, scheme=scheme,
                       roles=roles, path=path, yours=yours)


def _missing_theme(theme_id: str, user_dir: Path) -> ThemeFileError:
    """The refusal for a theme name that is not an id or is not found: the message names the ids
    found and both directories, so a typo is fixable from the message alone."""
    user_themes_dir = user_kind_dir(user_dir, "themes")
    user_ids = sorted(p.stem for p in user_themes_dir.glob("*.toml")) if user_themes_dir.is_dir() else []
    packaged_ids = sorted(p.stem for p in packaged_dir("themes").glob("*.toml"))
    found = ", ".join(packaged_ids + user_ids) or "none"
    return ThemeFileError(
        f"no theme {theme_id!r} (found: {found}; "
        f"searched {packaged_dir('themes')} and {user_themes_dir})"
    )


def find_theme(theme_id: str, user_dir: Path) -> ChromeTheme:
    """Find and parse the theme by id, user file first, then packaged. TT8: the name must match the
    id rule `[a-z0-9-]+` before any lookup, so a traversal name can never reach a file outside the
    folders; raise `ThemeFileError` if it does not, listing both directories and the ids found."""
    if not _SLUG.fullmatch(theme_id):
        raise _missing_theme(theme_id, user_dir)

    user_path = user_kind_dir(user_dir, "themes") / f"{theme_id}.toml"
    if user_path.is_file():
        return parse_theme(user_path, yours=True)

    packaged_path = packaged_dir("themes") / f"{theme_id}.toml"
    if packaged_path.is_file():
        return parse_theme(packaged_path, yours=False)

    raise _missing_theme(theme_id, user_dir)


def theme_catalogue(user_dir: Path) -> tuple[list[ChromeTheme], list[tuple[Path, str]]]:
    """Load every theme from both directories. Returns (good themes, bad files as (path, message)).
    A user file with a packaged id replaces it and is marked `yours`. Order by id within each
    directory (the picker sorts for display)."""
    good: dict[str, ChromeTheme] = {}
    bad: list[tuple[Path, str]] = []

    # Packaged first
    for path in sorted(packaged_dir("themes").glob("*.toml")):
        try:
            theme = parse_theme(path, yours=False)
            good[theme.id] = theme
        except ThemeFileError as exc:
            bad.append((path, str(exc)))

    # User files, replacing packaged ones
    user_themes_dir = user_kind_dir(user_dir, "themes")
    if user_themes_dir.is_dir():
        for path in sorted(user_themes_dir.glob("*.toml")):
            try:
                theme = parse_theme(path, yours=True)
                good[theme.id] = theme
            except ThemeFileError as exc:
                bad.append((path, str(exc)))

    return (sorted(good.values(), key=lambda t: t.id), bad)
