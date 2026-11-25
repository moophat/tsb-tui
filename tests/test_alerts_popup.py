"""The operator alert popup (design F9, D12, D23; operator-alerts steps 1, 2, 10, 18, 21, 24 and consumers;
Contract §4; conventions V2, V5, V11).

Why these matter (Rule 9): an alert is TSB's own state that an operator must act on (a supervisor that stopped
answering, a worker that will not die), and only a `broker.health` holder may see it. A popup that a person
cannot dismiss, that comes back after they did, or that names a host path would be noise or a leak; one that is
never opened leaves a failing supervisor unseen. It must also never take the keyboard (D23): the operator may be
typing into a session. Servers: the TUI's stub, whose long-lived alert stream a test feeds event by event (DT7),
and the contract's fixture server for its own stream.
"""

from __future__ import annotations

import asyncio
import threading

from prompt_support import NOW, click_laid_out, iso, open_stub_terminal
from stub_server import SlowChunkedApp, StubServer

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.prompts import AlertPopup, AlertStack


def _alert(alert_id: str, kind: str, state: str = "raised", **subjects) -> dict:
    return {"alert_id": alert_id, "kind": kind, "state": state, "at": iso(NOW), **subjects}


def _stub(**kwargs) -> SlowChunkedApp:
    return SlowChunkedApp(delay_s=0.05, alerts_hold_open=True, **kwargs)


async def _terminal(app, pilot, wait_until):
    screen = await open_stub_terminal(app, pilot, wait_until)
    app.start_alerts()
    return screen


def _popups(screen) -> list[AlertPopup]:
    return list(screen.query(AlertPopup))


def test_a_raise_opens_the_popup_and_the_clear_closes_it(wait_until, server_frame_validator) -> None:
    """The popup names TSB's component by id only and says acting on it is host administration (Contract §4):
    F9's lines, and a clear that names the raise takes it away."""

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("al.77e1ab", "supervisor_unresponsive", supervisor_id="sv.3c9d7f"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "the popup")
                popup = _popups(screen)[0]
                assert popup.border_title == "▲ TSB alert · supervisor not answering"
                lines = [str(w.render()) for w in popup.query("Static")]
                assert lines[:3] == [
                    "A supervisor stopped answering: sv.3c9d.",
                    "Its workers and their sessions are untouched.",
                    "Acting on it is host administration, outside TSB."]
                assert lines[3].startswith("raised ") and lines[3].endswith(" · alert al.77e1")
                stub.push_alert(_alert("al.77e1ab", "supervisor_unresponsive", "cleared",
                                       supervisor_id="sv.3c9d7f"))
                await wait_until(pilot, lambda: not _popups(screen), "the popup to close")
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_an_unknown_event_type_is_ignored_and_an_unknown_kind_is_tsbs_own_alert(wait_until) -> None:
    """V2: a later tag's event type is ignored. V5: a kind this build does not know is shown as TSB's own
    alert, never as host state, and the others keep their one-line texts under the generic title."""

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 60)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert({"anything": 1}, kind="x-later")
                stub.push_alert(_alert("a1", "worker_unkillable", worker_id="wk.1234ab", supervisor_id="sv.9999"))
                stub.push_alert(_alert("a2", "vt_unavailable"))
                stub.push_alert(_alert("a3", "grid_on_fire"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 3
                                 and all(len(p.query("Static")) >= 3 for p in _popups(screen)),
                                 "three popups, each composed")
                titles = [p.border_title for p in _popups(screen)]  # newest on top
                assert titles == ["▲ TSB alert · grid on fire", "▲ TSB alert · VT worker unavailable",
                                  "▲ TSB alert · worker will not die"]
                texts = ["\n".join(str(w.render()) for w in p.query("Static")) for p in _popups(screen)]
                assert "TSB reported an alert." in texts[0]
                assert "The VT worker is unavailable; grids may not be advancing." in texts[1]
                assert "A worker will not die: wk.1234." in texts[2]
                assert all("Acting on it is host administration, outside TSB." in t for t in texts)
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_dismiss_removes_one_popup_and_the_alert_stays_active(wait_until) -> None:
    """D12: `!1` stays on the rail after a dismissal, so the model keeps the alert active; each popup is
    dismissed alone."""

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 60)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("a1", "vt_unavailable"))
                stub.push_alert(_alert("a2", "supervisor_unresponsive", supervisor_id="sv.1111"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 2, "two popups")
                top = _popups(screen)[0]
                await click_laid_out(pilot, wait_until, "AlertPopup Choice")  # the first Dismiss
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "one popup to go")
                assert _popups(screen)[0].alert.alert_id != top.alert.alert_id
                assert app.alerts.count == 2
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_a_dismissed_alert_resent_after_a_reopen_stays_dismissed(wait_until) -> None:
    """Operator-alerts step 2: a reconnecting surface gets the active set again. An alert the operator
    dismissed must not pop up again because the stream was reopened."""

    async def _check() -> None:
        gate = threading.Event()
        stub = SlowChunkedApp(delay_s=0.05,
                              alerts_events=[_alert("a1", "supervisor_unresponsive", supervisor_id="sv.1111")])
        stub.alerts_reopen_gate = gate
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "the popup")
                await click_laid_out(pilot, wait_until, "AlertPopup Choice")
                await wait_until(pilot, lambda: not _popups(screen), "the dismissal")
                gate.set()  # now the stream may reopen and resend the active set
                await wait_until(pilot, lambda: stub.alerts_opened >= 2, "the stream to reopen")
                await pilot.pause(0.5)
                assert not _popups(screen), "a dismissed alert came back"
                assert app.alerts.count == 1

    asyncio.run(_check())


def test_a_login_without_broker_health_shows_nothing_and_never_retries(wait_until) -> None:
    """Operator-alerts step 1: no `broker.health` is a 403 and no stream. The TUI shows nothing alert-related
    for that login and does not ask again."""

    async def _check() -> None:
        stub = SlowChunkedApp(delay_s=0.05, alerts_status=403)
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                await wait_until(pilot, lambda: not app.alerts.allowed, "the 403 to be read")
                await pilot.pause(1.2)
                assert stub.alerts_requests == 1, "a 403 must not be retried"
                assert not _popups(screen) and not app.alerts.active

    asyncio.run(_check())


def test_the_popup_never_takes_the_keyboard(wait_until) -> None:
    """D23: the operator may be typing into the session; an alert popup that captured a key would swallow
    it. Enter and Space, which a focused button would take, still reach the session after a click on it."""

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("a1", "vt_unavailable"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "the popup")
                await click_laid_out(pilot, wait_until, "AlertPopup Choice")  # Dismiss, then more alerts
                stub.push_alert(_alert("a2", "vt_unavailable"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "the second popup")
                await pilot.press("p", "enter", "space")
                await wait_until(pilot, lambda: len(stub.client_frames) >= 3, "the keys to reach the session")
                assert stub.client_frames[:3] == [b"p", b"\r", b" "]
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_all_alerts_opens_the_fleet_view_alerts_tab(wait_until) -> None:
    """The popup's [ All alerts ] opens the fleet view's Alerts tab (TAB_ALERTS)."""
    from tsb_tui.screens.fleet_view import TAB_ALERTS

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("a1", "vt_unavailable"))
                stub.push_alert(_alert("a2", "supervisor_unresponsive", supervisor_id="sv.1111"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 2, "two popups")
                await click_laid_out(pilot, wait_until, "AlertPopup Choice")  # dismiss the top one
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "one left")
                await pilot.click(AlertPopup.__name__ + " Choice:last-of-type")
                await wait_until(
                    pilot,
                    lambda: screen.view_open and screen.fleet_view is not None and screen.fleet_view.tab == TAB_ALERTS,
                    "fleet view Alerts tab",
                )
                assert screen.view_open
                assert screen.fleet_view.tab == TAB_ALERTS
                alert_lines = getattr(screen.fleet_view._model, "alert_lines", ()) or ()
                assert len(alert_lines) >= 2, f"Alerts tab must have at least 2 alert rows, got {len(alert_lines)}"
                assert any("vt_unavailable" in line or "VT worker unavailable" in line or "unavailable" in line for line in alert_lines), (
                    f"vt_unavailable alert text missing in alert lines: {alert_lines}"
                )
                assert any("supervisor_unresponsive" in line or "sv.1111" in line or "supervisor" in line.lower() for line in alert_lines), (
                    f"supervisor_unresponsive alert text missing in alert lines: {alert_lines}"
                )
                alerts_view_text = "\n".join(screen.fleet_view.render_line(y).text for y in range(screen.fleet_view.size.height))
                assert "Active alerts" in alerts_view_text, f"Active alerts header missing: {alerts_view_text}"
                assert "unavailable" in alerts_view_text.lower(), f"First alert missing from rendered view: {alerts_view_text}"
                assert "supervisor" in alerts_view_text.lower(), f"Second alert missing from rendered view: {alerts_view_text}"
                await pilot.press("escape")
                await wait_until(pilot, lambda: not screen.view_open, "the fleet view to close")
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_all_alerts_when_fleet_tabs_omit_alerts(wait_until) -> None:
    """When fleet_view.tabs omits Alerts, neither the rail Alerts entry nor the popup [ All alerts ] is offered."""
    from tsb_tui.config import load
    from tsb_tui.screens.prompts import Choice
    from tsb_tui.widgets.rail import ACT_ALERTS

    async def _check() -> None:
        stub = _stub()
        cfg = load(overrides=["fleet_view.tabs=sessions,targets"])
        with StubServer(stub) as url:
            app = TsbTuiApp(url, cfg=cfg)
            async with app.run_test(size=(160, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("a1", "vt_unavailable"))
                await wait_until(pilot, lambda: len(_popups(screen)) == 1, "one popup")
                popup = _popups(screen)[0]
                choice_keys = [c.key for c in popup.query(Choice)]
                assert "alert:all" not in choice_keys, f"[ All alerts ] choice must not be offered: {choice_keys}"
                assert "alert:dismiss" in choice_keys, "Dismiss choice must still be offered"
                assert not any(r.action == ACT_ALERTS for r in screen.rail.rows), "Rail must not offer Alerts entry"
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_with_alerts_popup_off_nothing_pops_but_the_alert_is_still_active(wait_until, monkeypatch) -> None:
    monkeypatch.setenv("TSB_TUI_ALERTS_POPUP", "false")

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                screen = await _terminal(app, pilot, wait_until)
                stub.push_alert(_alert("a1", "vt_unavailable"))
                await wait_until(pilot, lambda: app.alerts.count == 1, "the alert")
                await pilot.pause(0.2)
                assert not _popups(screen) and not screen.query_one(AlertStack).display
                stub.alerts_stop.set()

    asyncio.run(_check())


def test_the_stream_stops_at_quit(wait_until) -> None:
    """DT14: the worker thread must not outlive the app."""

    async def _check() -> None:
        stub = _stub()
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(140, 40)) as pilot:
                await _terminal(app, pilot, wait_until)
                streamer = app._alert_streamer
                await wait_until(pilot, lambda: streamer.opens == 1, "the stream to open")
                assert streamer.running
            deadline = 50
            while streamer.running and deadline:
                await asyncio.sleep(0.1)
                deadline -= 1
            assert not streamer.running and app._alert_streamer is None
            stub.alerts_stop.set()

    asyncio.run(_check())
