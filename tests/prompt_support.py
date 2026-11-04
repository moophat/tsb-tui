"""Helpers for the prompt and state tests: a stub session opened on the terminal screen, frames built from the
contract's schema (DT6), and the clock."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen

STUB_TARGET = "stub-target"
# A fixed wall clock for the countdown: the tests pass it to `app.wall_clock`.
NOW = 1_800_000_000.0


def iso(epoch: float) -> str:
    """A contract timestamp (UTC, `Z`) for an epoch second."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def notice(ends_in_s: float, ends_by: str, ending: bool = True, session_id: str = "stub-session-1") -> dict:
    return {"t": "notice", "v": 1, "session_id": session_id, "ends_at": iso(NOW + ends_in_s),
            "ends_by": ends_by, "ending": ending}


def create_stub_session(app: TsbTuiApp, name: str = STUB_TARGET) -> str:
    app.client.login("fixture-user", "fixture-password")
    app.username = "fixture-user"  # what the login screen records: it tells the owner's clients apart
    return app.client.create_session(name, "new", "xterm-256color", 80, 24)["session"]["id"]


async def open_stub_terminal(app: TsbTuiApp, pilot, wait_until: Callable, *, frozen_clock: bool = True,
                             ) -> TerminalScreen:
    """Log in, create a session on the stub and push the terminal screen; wait until it attached."""
    if frozen_clock:
        app.wall_clock = lambda: NOW
    session_id = create_stub_session(app)
    app.push_screen(TerminalScreen(session_id))
    screen = app.screen
    await wait_until(pilot, lambda: screen.pump is not None and screen.pump.state.client_id is not None,
                     "the screen to attach")
    return screen


async def click_laid_out(pilot, wait_until: Callable, selector: str) -> None:
    """Click a widget once it has a place on screen. A prompt appears the moment its frame is applied, and
    its layout follows a refresh later: a click before that lands on whatever is underneath."""
    screen = pilot.app.screen
    await wait_until(pilot, lambda: screen.query_one(selector).region.width > 0 and
                     screen.query_one(selector).display, f"{selector} to be laid out")
    await pilot.pause()
    await pilot.click(selector)


_CONTRACT = Path(__file__).resolve().parents[1] / "program" / "contract"


def validated(frame: dict) -> dict:
    """A frame a test builds, checked against the contract's server `$def` (DT6) before it is sent."""
    from tsbc.schema import Registry

    errors = Registry.from_tree(_CONTRACT).validate(frame, "schemas/ws-frames.schema.json#/$defs/server")
    assert not errors, f"frame {frame.get('t')} breaks the contract schema: {errors[0]}"
    return frame
