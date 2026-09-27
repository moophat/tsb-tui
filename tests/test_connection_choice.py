"""409 connection_choice_required in New Session.

Why this matters (Rule 9): error.schema.json is explicit that the broker never picks a connection
to reuse itself when more than one candidate exists (Contract §12) — a surface that just showed
the raw error text and left the operator to guess a `connection_id` blind would defeat the whole
point of the choice being the operator's. `GET /sessions` is the error's own documented remedy
(each session lists its `connection_id`); this proves the screen actually fetches it, offers only
this principal's own candidates for the target that was attempted, preselects none, and retries
the exact create with whichever one is chosen.
"""

from __future__ import annotations

import asyncio

from stub_server import SlowChunkedApp, StubServer
from textual.widgets import Select
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen

TARGET_ID = "stub-target"


async def _login_and_open_new_session(app: TsbTuiApp, pilot) -> None:
    screen = app.screen
    screen.query_one("#username").value = "fixture-user"
    screen.query_one("#password").value = "fixture-password"
    await pilot.click("#login-button")
    await pilot.pause()
    assert isinstance(app.screen, NewSessionScreen)
    screen = app.screen
    screen.query_one("#target-id").value = TARGET_ID


def test_connection_choice_required_offers_candidates_and_retries_with_the_chosen_one() -> None:
    async def _check() -> None:
        stub = SlowChunkedApp(connection_choice_candidates=["stub-conn-a", "stub-conn-b"],
                              recording_on_create=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                await pilot.click("#conn-reuse")
                await pilot.click("#create-button")
                await pilot.pause()

                # Still on New Session — the 409 is not shown as a bare error, it is turned into
                # a picker.
                assert isinstance(app.screen, NewSessionScreen)
                picker = app.screen.query_one("#connection-picker", Select)
                for _ in range(200):
                    if not picker.disabled:
                        break
                    await pilot.pause(0.01)
                assert not picker.disabled, "the picker was never populated from GET /sessions"
                assert picker.value is Select.NULL, "a candidate must never be preselected"
                offered = {value for _, value in picker._options if value is not Select.NULL}
                assert offered == {"stub-conn-a", "stub-conn-b"}

                picker.value = "stub-conn-b"
                await pilot.pause()
                for _ in range(200):
                    if isinstance(app.screen, AttachConfirmScreen):
                        break
                    await pilot.pause(0.01)
                assert isinstance(app.screen, AttachConfirmScreen), (
                    "the retry with the chosen connection_id never completed")
                assert app.screen._session["connection_id"] == "stub-conn-b"

    asyncio.run(_check())


def test_connection_choice_offers_only_this_principals_own_candidates_for_the_target() -> None:
    """GET /sessions can list sessions this principal did not create; the picker must never offer
    one of those as if it were this principal's own to reuse."""

    async def _check() -> None:
        stub = SlowChunkedApp(connection_choice_candidates=["stub-conn-a"], recording_on_create=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target=TARGET_ID)
            async with app.run_test() as pilot:
                await _login_and_open_new_session(app, pilot)
                # A session on the same target, owned by someone else, with its own connection —
                # never a candidate for this principal to pick.
                stub.sessions["stub-other-owner"] = {
                    "id": "stub-other-owner", "target_id": TARGET_ID, "connection_id": "stub-conn-x",
                    "session_type": "shell", "state": "detached", "tier": 1, "owner": "someone-else",
                    "control_holder": None, "created_at": "2026-09-23T12:00:00Z",
                    "term": "xterm-256color", "cols": 80, "rows": 24, "recording": {"active": False},
                }
                await pilot.click("#conn-reuse")
                await pilot.click("#create-button")
                await pilot.pause()
                picker = app.screen.query_one("#connection-picker", Select)
                for _ in range(200):
                    if not picker.disabled:
                        break
                    await pilot.pause(0.01)
                offered = {value for _, value in picker._options if value is not Select.NULL}
                assert offered == {"stub-conn-a"}

    asyncio.run(_check())
