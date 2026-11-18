"""Round 3: scoped hints, reclaimed words and configuration errors."""
import pytest
from test_redesign_round2 import owner_inputs

from tsb_tui.config import ConfigError, load
from tsb_tui.widgets.status_row import Hint, KeysTarget, build_pieces


@pytest.mark.parametrize("name", ["confirm", "menu"])
def test_exit_outlasts_other_hints_without_menu_or_keys(name):
    target = KeysTarget(name, (Hint("Enter", "Do the selected thing", "accept"),
                               Hint("Esc", "close", "close")))
    for width in (120, 80, 40):
        pieces = build_pieces(owner_inputs(target=target), width)
        text = "".join(p.text for p in pieces)
        assert "F1 Menu" not in text and "? keys" not in text
        assert "Esc close" in text
        if width == 40:
            assert "accept" not in {p.action for p in pieces}


def test_stopped_reattach_keeps_its_only_action_at_80():
    pieces = build_pieces(owner_inputs(reattach_stopped=True), 80)
    assert "fleet" in {p.action for p in pieces}
    assert not {"menu", "keys"} & {p.action for p in pieces}


def test_recording_returns_after_scroll_drops_at_65():
    pieces = build_pieces(owner_inputs(), 65)
    assert "scroll" not in {p.action for p in pieces}
    assert "● REC" in "".join(p.text for p in pieces)


@pytest.mark.parametrize("source", ["profile", "file", "environment", "set"])
@pytest.mark.parametrize("bad", ["#zz2233", "#123", "not-a-colour"])
def test_bad_background_names_key_in_config_error(tmp_path, monkeypatch, source, bad):
    from tsb_tui import config

    path = tmp_path / "config.toml"
    env = {}
    overrides = []
    if source == "profile":
        monkeypatch.setattr(config, "_profile_overlay", lambda doc, profile: {"theme.bg": bad})
    elif source == "file":
        path.write_text(f'[theme]\nbg="{bad}"\n')
    elif source == "environment":
        env["TSB_TUI_THEME_BG"] = bad
    else:
        overrides = [f"theme.bg={bad}"]
    with pytest.raises(ConfigError, match=r"theme\.bg"):
        load(config_file=path, environ=env, overrides=overrides)


def test_backfill_uses_existing_priority_and_whole_items():
    from tsb_tui.widgets.status_row import (
        RANK_FACT,
        RANK_NAME,
        RANK_PILL,
        RANK_REC,
        Item,
        _lay,
    )

    items = [Item("pill", ("pill",), RANK_PILL),
             Item("text", ("a fact too long for the available cells",), RANK_FACT),
             Item("text", ("name",), RANK_NAME), Item("rec", ("REC",), RANK_REC)]
    assert "".join(p.text for p in _lay(items, 10)) == "pill  REC"
    assert "".join(p.text for p in _lay(items, 15)) == "pill  name  REC"


def test_countdown_returns_at_40_after_protected_hint_drops():
    from test_status_row import _inputs
    from test_status_row_narrow import NOW, _link, _owner_view_only, _with

    state = _with(_owner_view_only, _link)()
    pieces = build_pieces(_inputs(state, leader="Ctrl+\\", now=NOW), 40)
    text = "".join(p.text for p in pieces)
    assert "ends in 4:12" in text
    assert "F1 Menu" in text and len(text) == 40


@pytest.mark.parametrize("name,exit_label", [("scroll", "Live"), ("pan", "Done"),
                                             ("fleet view", "Back")])
def test_other_contexts_keep_exit_without_global_hints(name, exit_label):
    target = KeysTarget(name, (Hint("n", "Other action", "other"), Hint("Esc", exit_label, "close")))
    text = "".join(p.text for p in build_pieces(owner_inputs(target=target), 40))
    assert "Esc " + exit_label in text
    assert "F1 Menu" not in text and "? keys" not in text
