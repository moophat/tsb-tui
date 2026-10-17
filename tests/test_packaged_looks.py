# fails today: the drive and harness profiles take theme.name and theme.term.scheme from the packaged
# default, so their source is `default`, and no packaged theme is checked against the design's table.
"""Packaged looks: the drive and harness profiles pin the look, and every shipped chrome theme meets
the design's contrast table.

Why these matter (Rule 9): drive and every rig render the text snapshots the merge gate compares
(gate 3), so their look must be the packaged one for reasons no one chose at run time — a saved
choice, a config file or a stale environment variable that moved terracotta would change a rig's
picture silently (B8, TT19). The contrast table is the design's floor (§5): a shipped chrome theme
whose faint text drops below 4.5:1 on its surface is unreadable on a phone, and these are the files
the operator ships, so a value that drifts in a committed file must fail here, not on a person's
screen.
"""

from __future__ import annotations

from pathlib import Path

from tsb_tui import config
from tsb_tui.theme import contrast_ratio
from tsb_tui.theme_files import ROLES, packaged_dir, parse_theme

TUI_DIR = Path(__file__).resolve().parents[1]

# The design's contrast table (tools/contrast-check.py:26-32 `PAIRS`), plus TT22's `rec` and
# `rec_list` on `surface` at 4.5. Each entry is (foreground role, background role, minimum ratio).
PAIRS = [
    ("text", "surface", 4.5),
    ("text_muted", "surface", 4.5),
    ("text_faint", "surface", 4.5),
    ("accent", "surface", 4.5),
    ("on_accent", "accent", 4.5),
    ("steel", "surface", 4.5),
    ("ok", "surface", 4.5),
    ("warn", "surface", 4.5),
    ("danger", "surface", 4.5),
    ("border", "surface", 1.3),
    ("border_on_surface", "surface", 1.5),
    ("text", "surface_2", 4.5),
    ("text", "accent_soft", 4.5),
    ("rec", "surface", 4.5),
    ("rec_list", "surface", 4.5),
]


def _packaged_themes() -> list:
    """Every packaged chrome theme file, parsed (the same loader start-up uses)."""
    return [parse_theme(p) for p in sorted(packaged_dir("themes").glob("*.toml"))]


def _hex_themes() -> list:
    """The packaged themes whose every role value is a hex — the ones the contrast table can run on.
    ansi16's roles are ANSI index references (phase 14), so it is excluded here and checked there;
    `test_the_only_non_hex_packaged_theme_is_ansi16` proves nothing else is skipped."""
    return [t for t in _packaged_themes() if all(v.startswith("#") for v in t.roles.values())]


def test_the_drive_and_harness_profiles_pin_the_look() -> None:
    # fails today: the drive and harness profiles take theme.name and theme.term.scheme from the
    # packaged default, so their source is `default`.
    """B8, TT19: drive and the rigs build the text snapshots the merge gate compares (gate 3), so
    their profiles name terracotta and tsb-default outright; a person's saved choice, config file or
    environment can never move the look a rig's snapshot was built on. The source names the profile,
    which is what Settings… shows and what proves the pin — not an inherited default — is in force."""
    for profile in ("drive", "harness"):
        cfg = config.load(profile=profile, config_file=Path("/nonexistent"), environ={})
        assert cfg["theme.name"] == "terracotta", f"{profile}: theme.name is {cfg['theme.name']!r}"
        assert cfg["theme.term.scheme"] == "tsb-default", f"{profile}: scheme is {cfg['theme.term.scheme']!r}"
        assert cfg.source("theme.name") == f"profile {profile}", \
            f"{profile}: theme.name source is {cfg.source('theme.name')!r}, not the profile"
        assert cfg.source("theme.term.scheme") == f"profile {profile}", \
            f"{profile}: scheme source is {cfg.source('theme.term.scheme')!r}, not the profile"


def test_every_packaged_chrome_theme_carries_all_twenty_roles() -> None:
    # fails today: a theme file may carry a subset and nothing checks the shipped files carry all.
    """TT22: a packaged theme is the whole look, not a partial one; a role it lacks silently takes the
    terracotta default, so a shipped theme could paint a light dialog frame on a dark screen and no
    test would say so."""
    for theme in _hex_themes():
        missing = [r for r in ROLES if r not in theme.roles]
        assert not missing, f"{theme.id}: roles missing: {missing}"


def test_every_packaged_pair_meets_its_minimum() -> None:
    # fails today: the packaged themes are never measured against the design's table, so a committed
    # value below the floor ships.
    """Design §5, B8, TT22: these are the files the operator ships, and the floor is what keeps faint
    text readable on a phone; the failure names the theme, the pair and the measured ratio, so the
    file is fixable from the message alone."""
    for theme in _hex_themes():
        for fg, bg, minimum in PAIRS:
            ratio = contrast_ratio(theme.roles[fg], theme.roles[bg])
            assert ratio >= minimum, \
                f"{theme.id}: ({fg}, {bg}) is {ratio:.2f}:1, below the design's minimum {minimum}:1"


def test_every_packaged_theme_suggests_a_packaged_scheme() -> None:
    # fails today: a theme's `scheme` is never checked against the schemes the package ships.
    """TT22: every packaged theme names a scheme the TUI ships, so the picker's suggestion (phase 15)
    and the gallery (phase 6) resolve; a suggestion naming a file outside the package fails at the
    picker, not at start-up, and never reaches the operator."""
    packaged_schemes = {p.stem for p in packaged_dir("schemes").glob("*.json")}
    for theme in _packaged_themes():
        assert theme.scheme is not None, f"{theme.id}: no scheme named"
        assert theme.scheme in packaged_schemes, \
            f"{theme.id}: scheme {theme.scheme!r} is not a packaged scheme"


def test_the_only_non_hex_packaged_theme_is_ansi16() -> None:
    # fails today: there is no rule tying the hex selection to ansi16, so a non-hex theme added later
    # is silently skipped by the contrast checks.
    """TT22, phase 14: ansi16's roles are ANSI index references, not hex, so the contrast table cannot
    run on it — it is checked for distinct indexes there. Every other packaged theme must be hex, so
    this test skips no file: a new theme with a non-hex value that is not ansi16 cannot slip past the
    checks unnoticed."""
    non_hex = sorted(t.id for t in _packaged_themes()
                     if not all(v.startswith("#") for v in t.roles.values()))
    assert non_hex == [] or non_hex == ["ansi16"], f"non-hex packaged themes: {non_hex}"
