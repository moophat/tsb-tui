"""The scheme catalogue and its validation (`theme_files`), and the WCAG helpers.

Why these matter (Rule 9): a scheme file is data an operator can drop in themselves, and the TUI
paints a target's colours with it. A loader that accepted a half-formed file would paint a screen
in the wrong colours and say nothing; one that named the file and the field turns that into a
start-up message. `extendedAnsi`'s nulls must mean the xterm value, never black (the ruling), or a
target's 256-colour output is silently wrong. The contrast helpers are the basis for the low-contrast
warning and the minimum-contrast lift of later phases, so their formula is held to the design's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tsb_tui.theme import contrast_ratio, relative_luminance
from tsb_tui.theme_files import (
    Scheme,
    SchemeError,
    find_scheme,
    packaged_dir,
    parse_scheme,
    scheme_catalogue,
)

# A complete, valid scheme; a test changes one field to make its case.
BASE: dict = {
    "name": "Test Scheme",
    "variant": "dark",
    "ansi": [
        "#000000", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
        "#111111", "#220000", "#002200", "#222200", "#000022", "#220022", "#002222", "#eeeeee",
    ],
    "foreground": "#e6e6e6",
    "background": "#0a0a0a",
    "cursor": "#e6e6e6",
    "cursorText": "#0a0a0a",
    "source": {"origin": "test@0", "author": "A", "license": "MIT", "url": "https://example.test"},
}

_REMOVE = object()


def _scheme_file(tmp_path: Path, slug: str = "my-scheme", **overrides) -> Path:
    doc = json.loads(json.dumps(BASE))
    for key, value in overrides.items():
        if value is _REMOVE:
            doc.pop(key, None)
        else:
            doc[key] = value
    path = tmp_path / f"{slug}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# -- parse_scheme --


def test_a_packaged_scheme_parses_with_every_field_and_lowercased_hex(tmp_path: Path) -> None:
    """The loader fills every field a scheme can carry, and stores hex lower case so two files that
    differ only in case are one scheme. A scheme parsed here is what the palette and the picker read."""
    # fails today: no `theme_files` module.
    path = _scheme_file(
        tmp_path,
        name="Test Scheme",
        ansi=["#AABBCC"] + BASE["ansi"][1:],
        foreground="#ABCDEF",
        background="#0A0A0A",
        selectionBackground="#123456",
        selectionForeground="#654321",
        extendedAnsi=["#123456"] + [None] * 239,
    )
    scheme = parse_scheme(path, yours=True)

    assert isinstance(scheme, Scheme)
    assert scheme.slug == "my-scheme" and scheme.yours
    assert scheme.name == "Test Scheme" and scheme.variant == "dark"
    assert scheme.ansi[0] == "#aabbcc" and len(scheme.ansi) == 16
    assert scheme.foreground == "#abcdef" and scheme.background == "#0a0a0a"
    assert scheme.cursor == "#e6e6e6" and scheme.cursor_text == "#0a0a0a"
    assert scheme.selection_background == "#123456" and scheme.selection_foreground == "#654321"
    assert scheme.extended is not None and scheme.extended[0] == "#123456" and scheme.extended[1] is None
    assert scheme.source == BASE["source"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": _REMOVE}, "name"),
        ({"ansi": BASE["ansi"][:15]}, "ansi"),
        ({"ansi": ["#12345"] + BASE["ansi"][1:]}, "ansi[0]"),
        ({"extendedAnsi": ["#123456"] * 239}, "extendedAnsi"),
        ({"extendedAnsi": [0] + [None] * 239}, "extendedAnsi[0]"),
        ({"foo": "bar"}, "foo"),
        ({"variant": "dim"}, "variant"),
    ],
    ids=["no-name", "ansi-15", "ansi-bad-hex", "extended-239", "extended-zero", "unknown-key", "bad-variant"],
)
def test_a_malformed_scheme_is_refused_naming_the_file_and_field(tmp_path: Path, overrides: dict,
                                                                 field: str) -> None:
    """Start-up must fail naming the file and the field: a typo in a scheme must not paint a wrong
    screen silently (Rule 12). The zero case matters most: `0` is a colour and never a null."""
    # fails today: no `theme_files` module.
    path = _scheme_file(tmp_path, **overrides)
    with pytest.raises(SchemeError) as exc:
        parse_scheme(path)
    message = str(exc.value)
    assert str(path) in message, message
    assert field in message, message


def test_a_slug_that_is_not_a_file_stem_is_refused(tmp_path: Path) -> None:
    """A scheme's id is its file stem, `[a-z0-9-]+` (TT8): an uppercase stem would load on a
    case-insensitive filesystem and vanish on a case-sensitive one, so it is refused by name."""
    # fails today: no `theme_files` module.
    path = _scheme_file(tmp_path, slug="My-Scheme")
    with pytest.raises(SchemeError) as exc:
        parse_scheme(path)
    assert "My-Scheme" in str(exc.value)


def test_an_absent_variant_is_computed_from_the_background(tmp_path: Path) -> None:
    """TT9: with no `variant`, a light background is `light` and a dark one `dark`, split at the
    luminance where black and white contrast equally. The picker's dark│light│all filter and
    Textual's `dark` depend on it."""
    # fails today: no `theme_files` module.
    light = parse_scheme(_scheme_file(tmp_path, slug="light-one", variant=_REMOVE, background="#fdf6e3"))
    dark = parse_scheme(_scheme_file(tmp_path, slug="dark-one", variant=_REMOVE, background="#002b36"))
    assert light.variant == "light", relative_luminance("#fdf6e3")
    assert dark.variant == "dark", relative_luminance("#002b36")


def test_a_user_file_with_a_packaged_slug_wins_and_is_yours(tmp_path: Path) -> None:
    """The user's file replaces the packaged one of the same slug and is marked `yours`: the operator
    who copies a scheme in to tweak it must get their version, and the picker must say so."""
    # fails today: no `theme_files` module.
    user_dir = tmp_path / "tsb-tui"
    (user_dir / "schemes").mkdir(parents=True)
    _scheme_file(user_dir / "schemes", slug="dracula", name="My Dracula", foreground="#010203")

    scheme = find_scheme("dracula", user_dir)
    assert scheme.yours and scheme.path == user_dir / "schemes" / "dracula.json"
    assert scheme.name == "My Dracula" and scheme.foreground == "#010203"


def test_a_broken_user_file_is_listed_and_the_rest_of_the_catalogue_loads(tmp_path: Path) -> None:
    """A user file that fails validation is collected as `(path, message)`, never raised: one bad
    file the operator dropped in must not make every scheme unlistable (within the picker, phase 8)."""
    # fails today: no `theme_files` module.
    user_dir = tmp_path / "tsb-tui"
    (user_dir / "schemes").mkdir(parents=True)
    (user_dir / "schemes" / "broken.json").write_text("{not json", encoding="utf-8")
    _scheme_file(user_dir / "schemes", slug="good-one")

    good, bad = scheme_catalogue(user_dir)
    slugs = {s.slug for s in good}
    assert "good-one" in slugs and "tsb-default" in slugs  # packaged schemes still load
    assert len(bad) == 1 and bad[0][0] == user_dir / "schemes" / "broken.json"
    assert "broken" in bad[0][1]


def test_an_unknown_slug_lists_what_was_found_and_both_folders(tmp_path: Path) -> None:
    """B3's unknown-theme wording, applied to schemes: the message names the slugs that do exist and
    the folders searched, so a typo is fixable from the message alone."""
    # fails today: no `theme_files` module.
    user_dir = tmp_path / "tsb-tui"
    user_dir.mkdir()
    with pytest.raises(SchemeError) as exc:
        find_scheme("nope", user_dir)
    message = str(exc.value)
    assert "nope" in message and "tsb-default" in message
    assert str(packaged_dir("schemes")) in message and str(user_dir / "schemes") in message


# -- WCAG helpers --


def test_the_wcag_contrast_matches_the_design_formula() -> None:
    """The helpers reproduce `tools/contrast-check.py`'s formula exactly: black on white is the
    maximum 21.0, and tsb-default's foreground on its background is the design's 15.86 (design §5),
    so the low-contrast warning of later phases reads the same numbers the design published."""
    # fails today: no `relative_luminance` or `contrast_ratio`.
    assert contrast_ratio("#ffffff", "#000000") == 21.0
    assert round(contrast_ratio("#e6e6e6", "#0a0a0a"), 2) == 15.86
