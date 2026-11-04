"""Chrome theme files, chosen by name (`theme.name`): lookup, precedence and refusals.

Why these matter (Rule 9): `theme.name` becomes a selector, not a label — a config that names a
theme which does not exist must stop start-up naming the themes found and both folders, not paint
terracotta quietly; TT12's whole point is the layer order, so each precedence test fails if two
layers swap, and a partial user theme must take its missing roles from the packaged defaults, not
from nothing; terracotta is written twice (the defaults and `themes/terracotta.toml`), so the
shipped default look is pinned to the file the operator can copy and edit.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import pytest
from input_server import InputRecordingApp

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.theme_files import (
    ROLES,
    SchemeError,
    ThemeFileError,
    find_scheme,
    find_theme,
    packaged_dir,
    parse_scheme,
    parse_theme,
    scheme_catalogue,
    theme_catalogue,
)
from tsb_tui.widgets.rail import Rail

TUI_DIR = Path(__file__).resolve().parents[1]
THEMES = TUI_DIR / "tsb_tui" / "themes"
CONFIG_DEFAULT = TUI_DIR / "tsb_tui" / "config_default.toml"


def _style_hex(style) -> str:
    """The style's foreground colour as `#rrggbb` (Rich's `Color` has no `.hex`)."""
    t = style.color.triplet
    return f"#{t.red:02x}{t.green:02x}{t.blue:02x}"


def _user_theme(home: Path, stem: str, text: str) -> Path:
    themes = home / ".config" / "tsb-tui" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    path = themes / f"{stem}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _load_home(home: Path, theme_name: str) -> config.Config:
    """`load` with HOME and XDG_CONFIG_HOME at `home`, no config file, and the theme chosen by `--set`."""
    return config.load(
        config_file=Path("/nonexistent"),
        environ={"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")},
        overrides={"theme.name": theme_name},
    )


def test_a_theme_name_selects_its_file_and_leaves_the_scheme() -> None:
    # fails today: `theme.name` is only a label, every role stays terracotta's.
    """`--set theme.name=nord` gives Nord's chrome for the roles, and moves nothing else: the scheme
    stays `tsb-default` (ruling 2 — the two names never move each other), and the source of a moved
    role names the theme layer, so Settings… can show where a colour came from."""
    cfg = config.load(config_file=Path("/nonexistent"), environ={},
                      overrides=["theme.name=nord"])
    nord = parse_theme(THEMES / "nord.toml")
    assert cfg["theme.accent"] == nord.roles["accent"]
    assert cfg.source("theme.accent") == "theme nord"
    assert cfg["theme.term.scheme"] == "tsb-default"


def test_role_precedence_is_theme_lt_file_lt_env_lt_set(tmp_path: Path) -> None:
    # fails today: there is no theme layer, so a role's source is never `theme nord`.
    """TT12: the named theme file sits above the packaged defaults and below the config file, the
    environment and `--set`; each test below fails if two layers swap, which is how a stale
    environment variable would silently outvote the operator's explicit `--set`."""
    nord = parse_theme(THEMES / "nord.toml")

    cfg = config.load(config_file=Path("/nonexistent"), environ={}, overrides=["theme.name=nord"])
    assert cfg["theme.accent"] == nord.roles["accent"]
    assert cfg.source("theme.accent") == "theme nord"

    home = tmp_path
    cfg_dir = home / ".config" / "tsb-tui"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text('[theme]\naccent = "#102030"\n', encoding="utf-8")
    cfg = config.load(config_file=cfg_dir / "config.toml",
                      environ={"HOME": str(home), "XDG_CONFIG_HOME": str(cfg_dir)},
                      overrides=["theme.name=nord"])
    assert cfg["theme.accent"] == "#102030"
    assert cfg.source("theme.accent").startswith("config file")

    cfg = config.load(config_file=cfg_dir / "config.toml",
                      environ={"HOME": str(home), "XDG_CONFIG_HOME": str(cfg_dir),
                               "TSB_TUI_THEME_ACCENT": "#405060"},
                      overrides=["theme.name=nord"])
    assert cfg["theme.accent"] == "#405060"
    assert cfg.source("theme.accent") == "TSB_TUI_THEME_ACCENT"

    cfg = config.load(config_file=cfg_dir / "config.toml",
                      environ={"HOME": str(home), "XDG_CONFIG_HOME": str(cfg_dir),
                               "TSB_TUI_THEME_ACCENT": "#405060"},
                      overrides=["theme.name=nord", "theme.accent=#708090"])
    assert cfg["theme.accent"] == "#708090"
    assert cfg.source("theme.accent") == "--set"


def test_a_partial_user_theme_takes_the_missing_roles_from_the_defaults(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so `theme.name = mine` is refused at start-up.
    """TT12: a user theme with one role keeps every other role at the packaged default — a person
    who changes only the accent must not get black-on-black because their file lists one colour."""
    _user_theme(tmp_path, "mine", '[theme]\naccent = "#123456"\n')
    cfg = _load_home(tmp_path, "mine")
    assert cfg["theme.accent"] == "#123456"
    assert cfg.source("theme.accent") == "theme mine"
    assert cfg["theme.bg"] == "#0a0a0a"
    assert cfg.source("theme.bg") == "default"
    assert cfg.chrome.yours is True


def test_a_user_theme_named_like_a_packaged_one_wins(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so a user file has never chosen the roles.
    """B2/B3: the user's own file replaces the packaged theme with the same id, and is marked
    `yours` so the picker (phase 8) can show which file is in force."""
    _user_theme(tmp_path, "nord", '[theme]\nname = "nord"\naccent = "#abcdef"\n')
    cfg = _load_home(tmp_path, "nord")
    assert cfg["theme.accent"] == "#abcdef"
    assert cfg.chrome.yours is True


def test_an_unknown_theme_names_the_themes_found_and_both_folders(tmp_path: Path) -> None:
    # fails today: `theme.name` is a label, so start-up accepts any name and paints terracotta.
    """R6: `theme.name` changes meaning from a label to a selector, so a config that names a theme
    that does not exist must stop start-up listing the themes found and both folders — the person
    needs to see what is there, not just that the name failed."""
    with pytest.raises(config.ConfigError) as exc:
        _load_home(tmp_path, "nosuch")
    message = str(exc.value)
    for theme_id in ("terracotta", "paper", "contrast", "nord", "dracula"):
        assert theme_id in message, f"{theme_id} is not listed in {message}"
    assert message.count("themes") >= 2, f"both folders are not named in {message}"


def test_a_user_theme_with_a_typo_names_file_and_field(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so a user theme file is never read.
    """TT10, Rule 12: a selected user file with an unknown key stops start-up naming the file and
    the key — a silently dropped colour would paint the screen in defaults and say nothing."""
    path = _user_theme(tmp_path, "mine", '[theme]\nacent = "#123456"\n')
    with pytest.raises(config.ConfigError) as exc:
        _load_home(tmp_path, "mine")
    message = str(exc.value)
    assert "acent" in message and str(path) in message


def test_a_role_that_is_not_a_colour_names_file_and_field(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so a user theme file is never read.
    """TT10, Rule 12: a role value that is not `#rrggbb` stops start-up naming the file and the
    role; a half-length hex would paint an unpredictable colour for one knob alone."""
    path = _user_theme(tmp_path, "mine", '[theme]\nbg = "#12345"\n')
    with pytest.raises(config.ConfigError) as exc:
        _load_home(tmp_path, "mine")
    message = str(exc.value)
    assert "bg" in message and str(path) in message


def test_a_theme_named_differently_from_its_stem_is_refused(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so `name` is never checked.
    """TT8: a theme's id is its file stem, and the file's `name` must equal it — a file named
    `mine.toml` claiming `other` would make the picker show one name and load another."""
    _user_theme(tmp_path, "mine", '[theme]\nname = "other"\n')
    with pytest.raises(config.ConfigError) as exc:
        _load_home(tmp_path, "mine")
    message = str(exc.value)
    assert "name" in message and "mine.toml" in message


def test_terracotta_is_written_twice_and_kept_equal() -> None:
    # fails today: there is no themes folder.
    """TT27: `config_default.toml`'s `[theme]` keeps every role (its header promises every knob and
    its default) and `themes/terracotta.toml` holds the same values; a test asserts they are equal,
    so the shipped default look is pinned to the file a user copies to write their own theme."""
    defaults = tomllib.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))["theme"]
    terracotta = tomllib.loads((THEMES / "terracotta.toml").read_text(encoding="utf-8"))["theme"]
    for role in ROLES:
        assert defaults[role] == terracotta[role], f"{role} differs between the two files"
    assert terracotta["name"] == "terracotta"


def test_a_theme_without_variant_gets_it_from_its_bg(tmp_path: Path) -> None:
    # fails today: there is no themes folder, so a user theme file is never read.
    """OPEN-T5 (ruled 2026-10-15): a user theme file with no `variant` gets it computed from its
    `bg` luminance, as a scheme's is (TT9's rule), because Textual's `dark` needs it (TT13); a
    partial theme with no `bg` takes the packaged default's, which is what the rule then reads."""
    light = _user_theme(tmp_path, "light-mine", '[theme]\nbg = "#fcfbf9"\n')
    dark = _user_theme(tmp_path, "dark-mine", '[theme]\nbg = "#101010"\n')
    assert parse_theme(light, yours=True).variant == "light"
    assert parse_theme(dark, yours=True).variant == "dark"

    _user_theme(tmp_path, "nores", '[theme]\naccent = "#123456"\n')
    cfg = _load_home(tmp_path, "nores")
    assert cfg.chrome.variant == "dark"  # terracotta's bg is dark


def test_the_app_registers_tsb_paper_and_leaves_textuals_own_themes(serve, wait_until) -> None:
    # fails today: the app registers `terracotta` and `dark` is hard-coded True.
    """TT13: each chrome theme registers as `tsb-<id>`, so it never replaces Textual's own `nord`
    or `dracula` a person may be using, and paper's light variant reaches Textual's `dark` flag;
    the rail's entry rows take paper's accent, so the chrome is visibly the chosen one."""
    cfg = config.load(overrides=["theme.name=paper"])

    async def _check() -> None:
        _fixture_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url, cfg=cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_until(pilot, lambda: app.screen.query("#login-panel"), "the login panel")
            assert app.theme == "tsb-paper"
            assert app._registered_themes["tsb-paper"].dark is False
            assert app._registered_themes["nord"].primary.lower() == "#88c0d0"
            assert app._registered_themes["dracula"].primary.lower() == "#bd93f9"
            assert _style_hex(Rail(cfg)._styles["entry"]) == cfg["theme.accent"]

    asyncio.run(_check())


def test_an_empty_theme_file_is_refused_naming_the_theme_table(tmp_path: Path) -> None:
    # fails today: parse_theme raises KeyError on doc["theme"], so an empty file crashes the loader.
    """Rule 12: a comment-only theme file — the operator opened the editor and saved — must be
    refused naming the file and the field `theme`, like every other refusal, not crash the loader."""
    path = _user_theme(tmp_path, "mine", "# my theme, not written yet\n")
    with pytest.raises(ThemeFileError) as exc:
        parse_theme(path, yours=True)
    message = str(exc.value)
    assert "theme" in message and str(path) in message, message


def test_a_file_that_is_not_utf8_is_refused_naming_the_file(tmp_path: Path) -> None:
    # fails today: the raw UnicodeDecodeError escapes parse_scheme and parse_theme, crashing the loader.
    """Rule 12: a file in another encoding must be refused naming the file with "not UTF-8", so a
    selected one fails start-up through `config.load`'s usual conversion and a catalogue lists it
    among the bad files, never killing every scheme."""
    config_dir = tmp_path / ".config" / "tsb-tui"
    scheme_path = config_dir / "schemes" / "bad.json"
    scheme_path.parent.mkdir(parents=True)
    scheme_path.write_bytes(b'{"name": "x"}\xff')
    theme_path = _user_theme(tmp_path, "mine", "[theme]\n")
    theme_path.write_bytes(b'[theme]\naccent = "#123456"\n\xff')

    with pytest.raises(SchemeError) as exc:
        parse_scheme(scheme_path)
    assert "not UTF-8" in str(exc.value) and str(scheme_path) in str(exc.value), str(exc.value)
    with pytest.raises(ThemeFileError) as exc:
        parse_theme(theme_path)
    assert "not UTF-8" in str(exc.value) and str(theme_path) in str(exc.value), str(exc.value)

    _, bad = scheme_catalogue(config_dir)
    assert any(p == scheme_path for p, _ in bad), "the bad scheme is not listed among the bad files"
    _, bad_themes = theme_catalogue(config_dir)
    assert any(p == theme_path for p, _ in bad_themes), "the bad theme is not listed among the bad files"


def test_a_valid_utf16_scheme_is_refused_as_not_utf8(tmp_path: Path) -> None:
    # fails today: json.loads sniffs the UTF-16 BOM itself, so a valid UTF-16 scheme loads.
    """Rule 12: the loader decodes the file's bytes as UTF-8, not whatever encoding the parser
    sniffs: a valid scheme in UTF-16 is refused naming the file with "not UTF-8", like any other
    unreadable file, so the two parsers share one rule."""
    config_dir = tmp_path / ".config" / "tsb-tui"
    scheme_path = config_dir / "schemes" / "utf16-scheme.json"
    scheme_path.parent.mkdir(parents=True)
    doc = ('{"name":"UTF-16","variant":"dark","ansi":[' + ",".join(['"#000000"'] * 16) + '],'
           '"foreground":"#e6e6e6","background":"#0a0a0a"}')
    scheme_path.write_bytes(b"\xff\xfe" + doc.encode("utf-16-le"))

    with pytest.raises(SchemeError) as exc:
        find_scheme("utf16-scheme", config_dir)
    message = str(exc.value)
    assert "not UTF-8" in message and str(scheme_path) in message, message


def test_a_name_that_is_not_an_id_is_refused_before_any_lookup(tmp_path: Path) -> None:
    # fails today: the name is joined onto the folders, so `../outside` selects a file outside them.
    """TT8: the requested name must match [a-z0-9-]+ before any lookup; a traversal name is refused
    with the same message as an unknown name, listing what was found and both folders."""
    config_dir = tmp_path / ".config" / "tsb-tui"
    (config_dir / "themes").mkdir(parents=True)
    (config_dir / "schemes").mkdir(parents=True)
    (config_dir / "outside.toml").write_text('[theme]\naccent = "#123456"\n', encoding="utf-8")
    (config_dir / "evil.json").write_text(
        '{"name":"Evil","variant":"dark","ansi":[' + ",".join(['"#000000"'] * 16) + '],'
        '"foreground":"#e6e6e6","background":"#0a0a0a"}', encoding="utf-8")

    with pytest.raises(ThemeFileError) as exc:
        find_theme("../outside", config_dir)
    message = str(exc.value)
    for theme_id in ("terracotta", "paper", "contrast", "nord", "dracula"):
        assert theme_id in message, f"{theme_id} is not listed in {message}"
    assert message.count("themes") >= 2, f"both folders are not named in {message}"

    with pytest.raises(SchemeError) as exc:
        find_scheme("../evil", config_dir)
    message = str(exc.value)
    assert "tsb-default" in message and str(packaged_dir("schemes")) in message, message
    assert str(config_dir / "schemes") in message, message


def test_the_readme_precedence_lists_only_layers_that_exist() -> None:
    # fails today: the README lists a "saved choice" layer that arrives only with theming PR 4.
    """The README must document only precedence layers the code implements: the picker's saved
    choice is not built, so a reader who relies on it gets a layer that does not move."""
    readme = (TUI_DIR / "README.md").read_text(encoding="utf-8")
    assert "saved choice" not in readme, "the README documents a precedence layer that is not built"


def test_a_themes_variant_follows_the_bg_in_force_after_every_layer(tmp_path: Path) -> None:
    # fails today: the variant is computed from the packaged default's bg, before any layer applies.
    """TT12: a theme with neither `variant` nor `bg` gets its variant from the `theme.bg` in force
    after every layer — the operator who sets `theme.bg` light must get Textual's `dark` flag False."""
    _user_theme(tmp_path, "mine", '[theme]\naccent = "#123456"\n')
    cfg = config.load(
        config_file=Path("/nonexistent"),
        environ={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / ".config")},
        overrides={"theme.name": "mine", "theme.bg": "#fdf6e3"},
    )
    assert cfg["theme.bg"] == "#fdf6e3"
    assert cfg.chrome.variant == "light", f"variant not computed from the bg in force: {cfg.chrome.variant}"
