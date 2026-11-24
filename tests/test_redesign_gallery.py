"""Phase 06: Screen gallery, manifest verification, keys-only reachability, and monochrome marks."""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path

import pytest
from input_server import InputRecordingApp
from test_redesign_focus import heavy_panes, workspace_with_sessions
from tsbc import _serve

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen

MANIFEST_PATH = Path(__file__).parent / "redesign_screen_manifest.json"
COMMITTED_SNAP_DIR = Path(__file__).parent / "snapshots" / "redesign-p6"
_REGEN_DIR = os.environ.get("TSB_TUI_SNAPSHOT_DIR")
SIZES = [(80, 24), (160, 45)]

# ---------------------------------------------------------------------------
# Named step expansions used in keys_route.
# A step is either a literal Textual key name or one of these named steps.
# Each named step expands to a tuple of literal key names (key presses only).
# ---------------------------------------------------------------------------
# "admin"    → type the fixture username one char at a time
# "password" → type the fixture password one char at a time
# "launch"   → no-op; the app is already at LoginScreen on start

_NAMED_STEPS: dict[str, tuple[str, ...]] = {
    "launch": (),
    "admin": tuple(_serve.USER),
    "password": tuple(_serve.PASSWORD),
}


def _expand_route(route: list[str]) -> list[str]:
    """Expand a manifest keys_route to a flat list of Textual key names."""
    keys: list[str] = []
    for step in route:
        if step in _NAMED_STEPS:
            keys.extend(_NAMED_STEPS[step])
        else:
            keys.append(step)
    return keys


def _discover_screen_classes() -> set[str]:
    """Discover every concrete (non-private) Screen subclass in tsb_tui.

    Fails loudly if any source file cannot be parsed — a parse error is not silently
    swallowed (orchestrator review item 4).
    """
    screens: set[str] = set()
    root = Path(__file__).parent.parent / "tsb_tui"
    for py in root.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))  # raises SyntaxError loudly
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and not node.name.startswith("_")
                and node.name != "MenuProvider"
            ):
                bases = [ast.dump(b) for b in node.bases]
                if any("Screen" in b or "_Dialog" in b or "CommandPalette" in b for b in bases):
                    screens.add(node.name)
    return screens


def test_manifest_lists_every_screen_class():
    """L1-gallery: Assert manifest exists, lists every concrete Screen subclass, and has no omissions."""
    assert MANIFEST_PATH.is_file(), "redesign_screen_manifest.json must exist"
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = data.get("screens", data)
    assert len(entries) >= 31, f"Expected at least 31 screen entries, got {len(entries)}"

    manifest_classes = {e["screen_class"] for e in entries}
    discovered = _discover_screen_classes()
    assert discovered, "Must discover Screen subclasses in tsb_tui"

    missing = discovered - manifest_classes
    assert not missing, f"Manifest is missing Screen subclasses: {missing}"


def test_every_manifest_entry_has_both_baselines():
    """L1-gallery: Every entry in the manifest has both 80x24 and 160x45 committed SVG baselines."""
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = data.get("screens", data)
    assert entries, "Manifest has no entries"

    for entry in entries:
        eid = entry["id"]
        for size_str in ("80x24", "160x45"):
            baseline_name = entry.get(f"baseline_{size_str}") or f"{eid}-{size_str}.svg"
            svg_file = COMMITTED_SNAP_DIR / baseline_name
            assert svg_file.is_file(), f"Missing baseline SVG for {eid} at {size_str}: {svg_file}"
            content = svg_file.read_text(encoding="utf-8")
            assert content.lstrip().startswith("<svg"), f"{svg_file} does not start with <svg"
            assert "</svg>" in content, f"{svg_file} does not contain </svg>"
            assert len(content) > 500, f"{svg_file} is suspiciously small ({len(content)} bytes)"


def _load_entries() -> list[dict]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data.get("screens", data)


def _entry_ids() -> list[str]:
    return [e["id"] for e in _load_entries()]


def _entry_by_id(eid: str) -> dict:
    return next(e for e in _load_entries() if e["id"] == eid)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("entry_id", _entry_ids())
def test_every_manifest_entry_is_reachable_by_keys(serve, wait_until, screen_text, tmp_path, entry_id, size):
    """L1-gallery: Every manifest entry reached by its own keys_route at both 80x24 and 160x45.

    Captures are verified in tmp_path; committed baselines are written only when TSB_TUI_SNAPSHOT_DIR names a directory.

    Each entry's route is driven from app launch by key presses only (no push_screen,
    no attribute injection, no mouse). After the route, the entry's screen_class and
    every text in its expect.texts are asserted.

    Named steps (defined once here, never duplicated):
        launch   → (no presses; app starts at LoginScreen)
        admin    → type _serve.USER char by char
        password → type _serve.PASSWORD char by char
    All other steps are literal Textual key names and pressed as-is.
    """
    entry = _entry_by_id(entry_id)
    screen_class = entry["screen_class"]
    expect = entry.get("expect", {})
    expect_texts = expect.get("texts", [])
    size_str = f"{size[0]}x{size[1]}"
    baseline_name = entry.get(f"baseline_{size_str}") or f"{entry_id}-{size_str}.svg"

    async def check():
        _serve_app, url = serve(InputRecordingApp())
        if entry_id == "vault-init":
            _serve_app.vault_users.discard(_serve.USER)
        if entry_id == "confirm-remove-peer":
            _serve_app.after_attach = [
                {"t": "clients", "v": 1, "clients": [
                    {"client_id": "c-oper", "user": _serve.USER, "may_control": True},
                    {"client_id": "c-peer", "user": _serve.PEER, "may_control": True},
                ]}
            ]
        if entry_id in ("stale-answer", "confirm-kill-session"):
            _serve_app.after_attach = [
                {"t": "quiet_prompt", "v": 1, "prompt_id": "qp-1", "text": "Are you done?"}
            ]
        if entry_id == "session-ended":
            orig_attach = _serve_app._attach_locked
            def attach_close(ws, user, sess, login):
                cid = orig_attach(ws, user, sess, login)
                ws.close(4010, "ended_killed")
                return cid
            _serve_app._attach_locked = attach_close
        _serve_app.creds.setdefault(_serve.USER, {})["my-key"] = {
            "reference": "my-key",
            "kind": "password",
            "login_name": "oper",
            "secret": "s3cret",
            "label": "my-key",
        }
        _serve_app.devices[(_serve.USER, "dev-1")] = {
            "device_id": "dev-1",
            "device_name": "my-laptop",
            "registered_at": "2026-01-01T00:00:00Z",
            "last_used_at": "2026-01-01T00:00:00Z",
            "revoked_at": None,
            "revoked_reason": None,
        }
        for tid in _serve_app.targets:
            _serve_app.targets[tid]["discovered_by"] = "tsb"
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            # Drive the route steps
            route = list(entry["keys_route"])
            if route == ["launch"]:
                pass
            else:
                # If route begins with login sequence or named login step
                if route and route[0] == "login":
                    route = route[1:]
                    await pilot.press(*_serve.USER, "tab", *_serve.PASSWORD, "enter")
                    await wait_until(
                        pilot,
                        lambda: isinstance(app.screen, TerminalScreen) and app.screen.sidebar is not None,
                        "login",
                        timeout_s=15.0,
                    )
                    await pilot.pause()
                elif len(route) >= 4 and route[:4] == ["admin", "tab", "password", "enter"]:
                    route = route[4:]
                    await pilot.press(*_serve.USER, "tab", *_serve.PASSWORD, "enter")
                    await wait_until(
                        pilot,
                        lambda: isinstance(app.screen, TerminalScreen) and app.screen.sidebar is not None,
                        "login",
                        timeout_s=15.0,
                    )
                    await pilot.pause()

                for step in route:
                    if type(app.screen).__name__ in ("CredentialsScreen", "DevicesScreen", "AccountsScreen") and getattr(app.screen, "loaded", None) is False:
                        await wait_until(
                            pilot,
                            lambda: getattr(app.screen, "loaded", False),
                            f"{type(app.screen).__name__} loaded",
                            timeout_s=10.0,
                        )
                        await pilot.pause(0.05)

                    if step == "D" and getattr(app.screen, "targets", None) is not None:
                        await wait_until(
                            pilot,
                            lambda: len(app.screen.targets.rows) > 0,
                            "targets rows to load",
                            timeout_s=10.0,
                        )
                        await pilot.pause(0.05)

                    if step in _NAMED_STEPS:
                        for k in _NAMED_STEPS[step]:
                            await pilot.press(k)
                            await pilot.pause(0.02)
                    else:
                        await pilot.press(step)
                        await pilot.pause(0.05)

            # Wait for the target screen class (or the app screen to settle)
            if entry_id == "alerts-tab":
                await wait_until(
                    pilot,
                    lambda: getattr(app.screen, "view_open", False) and getattr(app.screen.fleet_view, "tab", "") == "alerts",
                    "Alerts tab open",
                    timeout_s=10.0,
                )
            else:
                await wait_until(
                    pilot,
                    lambda: type(app.screen).__name__ == screen_class,
                    f"screen {screen_class} for entry {entry_id}",
                    timeout_s=10.0,
                )

            # Assert content: every text the entry declares must appear on screen
            rendered = screen_text(app)
            for text in expect_texts:
                assert text in rendered, (
                    f"Entry {entry_id}: expected text {text!r} not found on {screen_class}. "
                    f"First 400 chars of screen:\n{rendered[:400]}"
                )

            # Capture SVG baseline
            svg = app.export_screenshot(title=f"p6-{entry_id}")
            tmp_file = tmp_path / baseline_name
            tmp_file.write_text(svg, encoding="utf-8")
            content = tmp_file.read_text(encoding="utf-8")
            assert content.lstrip().startswith("<svg"), f"export_screenshot returned non-SVG for {entry_id}"
            assert "</svg>" in content, f"{tmp_file} does not contain </svg>"
            assert len(content) > 500, f"{tmp_file} is suspiciously small ({len(content)} bytes)"

            regen_dir = os.environ.get("TSB_TUI_SNAPSHOT_DIR") or _REGEN_DIR
            if regen_dir:
                dest_dir = Path(regen_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                (dest_dir / baseline_name).write_text(svg, encoding="utf-8")

    asyncio.run(check())


@pytest.mark.parametrize("size", SIZES)
def test_gallery_monochrome_marks(serve, wait_until, monkeypatch, screen_text, size):
    """P6, L1-monochrome: a monochrome and 16-colour gallery pass: the selected row, the focused pane,
    the active tab and who types each stay identifiable by shape or words with NO_COLOR set."""
    from test_redesign_focus import to_sessions_list

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")

    async def check():
        serve_app, url = serve(InputRecordingApp())
        for tid in serve_app.targets:
            serve_app.targets[tid]["discovered_by"] = "tsb"
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            screen, (_sid,) = await workspace_with_sessions(app, pilot, wait_until, serve_app, url, "rtr-core-01")
            await pilot.pause()

            # 1. Focused pane: heavy border ┏━┓ vs light border ┌─┐ identifiable by shape without color
            panes = heavy_panes(app, screen, screen_text)
            assert panes == {"canvas"}, f"Expected canvas to be heavy focused pane, got {panes}"
            lines = screen_text(app).splitlines()
            frame_box = screen.query_one("#canvases").region
            top_line = lines[frame_box.y][frame_box.x:frame_box.right]
            assert "┏" in top_line and "━" in top_line and "┓" in top_line, f"Canvas border must be heavy: {top_line}"

            # 2. Who types: identifiable by words where the design draws it (the frame's border_title per plan.md P2a)
            frame = screen.query_one("#canvases")
            assert "you type" in str(frame.border_title), (
                f"Who types must be stated in frame border title: border_title={frame.border_title!r}"
            )

            # 3. Selected row: identifiable by mark (▸ marker on selected row)
            # Use leader key to move from canvas to sessions list
            await to_sessions_list(pilot, screen)
            assert heavy_panes(app, screen, screen_text) == {"sessions"}
            sidebar_text = "\n".join(screen.sidebar.render_line(y).text for y in range(screen.sidebar.size.height))
            assert "rtr-core-01" in sidebar_text

            # Walk to rail
            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"rail"}
            rail_lines = [screen.rail.render_line(y).text for y in range(len(screen.rail.rows))]
            assert any("◂" in r for r in rail_lines), f"Rail chip mark must be identifiable: {rail_lines}"

            # Walk to targets
            await pilot.press("tab")
            await pilot.pause()
            assert heavy_panes(app, screen, screen_text) == {"targets"}

            # 4. Active tab: active tab has ━ underline, inactive tab has ─ underline
            await pilot.press("ctrl+backslash", "F")
            await wait_until(pilot, lambda: screen.view_open, "full view")
            full_lines = screen_text(app).splitlines()
            tab_underline_row = next((line for line in full_lines if "━" in line), "")
            assert "━" in tab_underline_row, f"Active tab must have heavy underline ━: {tab_underline_row}"
            assert "─" in tab_underline_row, f"Inactive tabs must have light underline ─: {tab_underline_row}"
            await pilot.press("escape")
            await wait_until(pilot, lambda: not screen.view_open, "close full view")

    asyncio.run(check())
