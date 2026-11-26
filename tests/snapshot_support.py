"""Shared capture helper: wait bounded until consecutive frames are identical."""
from __future__ import annotations

import difflib
import os
import re
from collections.abc import Callable
from pathlib import Path


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
        await pilot.pause()
        curr = capture()
        curr_norm = norm(curr)
        if curr_norm == prev_norm:
            settled = curr
            break
        prev = curr
        prev_norm = curr_norm

    if settled is None:
        raise AssertionError(
            f"Screen never settled: consecutive captures differed after {timeout_refreshes} refreshes waiting for {path.name}"
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
