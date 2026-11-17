"""Unit tests for bench/paint.py judge() rules under tiny and gated modes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"


def _load(name: str):
    if str(BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(BENCH_DIR))
    spec = importlib.util.spec_from_file_location(f"tsb_tui_bench_{name}", BENCH_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _result(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "grid": "40x12",
        "stream": "yes",
        "keys": 4,
        "lost": 0,
        "latency": {"n": 4.0, "p50": 20.0, "p95": 40.0, "max": 60.0},
        "server": {"pings_unanswered": 0},
        "dropped": False,
        "reattached": False,
        "completed": True,
    }
    row.update(changes)
    return row


def test_tiny_mode_judge_dropped_and_reattached_with_lost_key_passes_with_warning(
    capsys: pytest.CaptureFixture,
) -> None:
    """A tiny-mode judge given a dropped-and-reattached result with one lost key passes with the warning."""
    paint = _load("paint")
    res = _result(dropped=True, reattached=True, completed=True, lost=1)
    bad = paint.judge(res, gate=False)
    assert bad == []
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "yes" in out
    assert "dropped and reattached" in out
    assert "1 keystrokes lost" in out


def test_tiny_mode_judge_dropped_without_reattach_fails() -> None:
    """Dropped without a reattach fails."""
    paint = _load("paint")
    res = _result(dropped=True, reattached=False, completed=True, lost=0)
    bad = paint.judge(res, gate=False)
    assert any("dropped" in why for why in bad)

    res_incomplete = _result(dropped=True, reattached=True, completed=False, lost=0)
    bad_incomplete = paint.judge(res_incomplete, gate=False)
    assert any("dropped" in why for why in bad_incomplete)


def test_tiny_mode_judge_no_drop_with_one_lost_key_fails() -> None:
    """No drop with one lost key fails."""
    paint = _load("paint")
    res = _result(dropped=False, reattached=False, completed=True, lost=1)
    bad = paint.judge(res, gate=False)
    assert any("never reached the screen" in why for why in bad)


def test_gated_judge_given_dropped_result_fails() -> None:
    """A gated judge given a dropped result still fails."""
    paint = _load("paint")
    res = _result(dropped=True, reattached=True, completed=True, lost=0)
    bad = paint.judge(res, gate=True)
    assert any("dropped" in why for why in bad)
