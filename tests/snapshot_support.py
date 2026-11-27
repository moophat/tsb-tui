"""Shared capture helper: wait bounded until consecutive frames are identical."""
from __future__ import annotations

import difflib
import os
import re
from collections.abc import Callable
from pathlib import Path

from tsb_tui.widgets.canvas import Canvas


def _pending_canvas_paints(app) -> list[tuple[Canvas, float]]:
    """Return attached canvases on the screen stack that have a paint pending on their timer."""
    screens = getattr(app, "screen_stack", None)
    if not screens:
        screen = getattr(app, "screen", None)
        screens = [screen] if screen is not None else []
    pending = []
    for screen in screens:
        if hasattr(screen, "walk_children"):
            for c in screen.walk_children(Canvas):
                if c.is_attached and not c._released and (c.in_front or c._paint_background) and not c.painted:
                    interval = (1.0 / c._max_fps) if getattr(c, "_max_fps", None) else 0.02
                    pending.append((c, interval))
    return pending


async def settle_snapshot(
    app,
    pilot,
    path: Path,
    title: str,
    terminal_tag: str | None = None,
    *,
    update_env: str | None = None,
    regen_env: str | None = None,
    simplify: bool = True,
    normalizer: Callable[[str], str] | None = None,
    timeout_refreshes: int = 50,
) -> str:
    """Capture screenshots waiting bounded until two consecutive frames one app refresh apart are identical."""

    def capture() -> str:
        svg = app.export_screenshot(title=title, simplify=simplify)
        if terminal_tag is not None:
            svg = re.sub(r"terminal-\d+", terminal_tag, svg)
        return svg

    def norm(svg_text: str) -> str:
        return normalizer(svg_text) if normalizer is not None else svg_text

    prev = capture()
    prev_norm = norm(prev)
    settled = None
    for _ in range(timeout_refreshes):
        pending = _pending_canvas_paints(app)
        if pending:
            delay = max(interval for _, interval in pending) + 0.005
            await pilot.pause(delay)
        else:
            await pilot.pause()
        curr = capture()
        curr_norm = norm(curr)
        pending_after = _pending_canvas_paints(app)
        if curr_norm == prev_norm and not pending_after:
            settled = curr
            break
        prev = curr
        prev_norm = curr_norm

    if settled is None:
        waited_for = (
            "pending canvas paint"
            if _pending_canvas_paints(app)
            else "consecutive captures differed"
        )
        raise AssertionError(
            f"Screen never settled: {waited_for} after {timeout_refreshes} refreshes waiting for {path.name}"
        )

    assert settled.lstrip().startswith("<svg"), f"export_screenshot returned non-SVG for {path.name}"
    assert "</svg>" in settled, f"{path.name} capture does not contain </svg>"
    assert len(settled) > 500, f"{path.name} is suspiciously small ({len(settled)} bytes)"

    if regen_env:
        val = os.environ.get(regen_env)
        if val:
            dest_dir = Path(val)
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / path.name).write_text(settled, encoding="utf-8")
            return settled

    if update_env and os.environ.get(update_env) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settled, encoding="utf-8")
        return settled

    assert path.is_file(), f"Committed baseline missing: {path}"
    baseline_text = path.read_text(encoding="utf-8")

    norm_baseline = norm(baseline_text)
    norm_settled = norm(settled)

    if norm_settled != norm_baseline:
        diff_lines = list(
            difflib.unified_diff(
                norm_baseline.splitlines(keepends=True),
                norm_settled.splitlines(keepends=True),
                fromfile=f"committed:{path.name}",
                tofile=f"captured:{path.name}",
                n=3,
            )
        )
        diff_msg = "".join(diff_lines[:100])
        raise AssertionError(
            f"Snapshot mismatch for {path.name}:\n{diff_msg}"
        )

    return settled
