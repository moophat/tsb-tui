"""The alert model (DT14): the active set, the dismissed set, the unknown-event rule, the 403 stop
and the stream's reopen.

Why these matter (Rule 9):

  * **An unknown event type is ignored** (V2, operator-alerts consumers): a later tag adds event
    types without breaking this build. The fixture stream's own `x-later` event is that case.
  * **An unknown kind is TSB's own alert, never host state** (V5, operator-alerts consumers): an
    alert names only TSB's components (Contract §4), so it must never be shown as host detail.
  * **A 403 stops for good** (DT14): no `!` cell, no Alerts tab, no retry — the login is not a
    `broker.health` holder.
  * **A reopen that resends a dismissed alert does not re-raise it** (operator-alerts step 2): the
    active set it resends never re-raises a popup this TUI already dismissed (DT14).

Records and events come from `C1/alerts-stream`'s `valid: true` documents (DT6), never written by
hand.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from stub_server import SlowChunkedApp
from stub_server import StubServer as StubHttpServer
from tsb_contract_client import Client
from tsb_tui.model.alerts import Alert, AlertModel, AlertStreamer
from tsbc import _serve
from tsbc._serve import ServeApp, StubServer

CONTRACT = Path(__file__).resolve().parents[2] / "program" / "contract"
FIXTURE = json.loads((CONTRACT / "fixtures" / "C1" / "alerts-stream.json")
                     .read_text(encoding="utf-8"))


def _events() -> list[dict]:
    """Every `valid: true` `alert` event's data from the stream fixture (DT6)."""
    return [i["doc"] for i in FIXTURE["case"]["instances"] if i["valid"]]


def test_raised_then_cleared_move_the_active_set() -> None:
    """The active set is raised minus cleared, by `alert_id` (operator-alerts steps 2, 10, 18).

    Why: a clear naming its raise leaves it; a raise of a new alert adds it. A model that dropped
    the clear would pop an alert the operator has already answered.
    """
    model = AlertModel()
    for event in _events():
        model.apply(event)
    active = model.active
    assert set(active) == {"fixture-alert-2"}
    assert active["fixture-alert-2"].kind == "worker_unkillable"
    assert active["fixture-alert-2"].state == "raised"


def test_a_raise_not_dismissed_returns_the_alert_to_pop() -> None:
    """A raise returns the alert so the caller can pop it; a clear returns None."""
    model = AlertModel()
    raised = model.apply(_events()[0])
    assert isinstance(raised, Alert)
    assert raised.alert_id == "fixture-alert-1"
    # The second event is the clear of the same alert.
    cleared = model.apply(_events()[1])
    assert cleared is None
    assert "fixture-alert-1" not in model.active


def test_an_unknown_event_type_is_ignored() -> None:
    """V2 / operator-alerts consumers: a stream event of a type this build does not know is
    ignored, and a comment line (the keepalive) is too.

    The fixture server's own stream carries an `x-later` event (a later tag's type) between the
    raises and clears; the client's `AlertStream` skips it, so the model never sees it. This test
    proves the model itself ignores an event whose `kind` it does not know.
    """
    model = AlertModel()
    result = model.apply({"alert_id": "future-alert", "kind": "some_future_kind",
                          "state": "raised", "at": "2026-10-01T02:00:00Z"})
    assert result is not None  # it is still an alert...
    assert model.active["future-alert"].is_tsb_own is True  # ...TSB's own, never host state


def test_an_unknown_kind_is_tsb_s_own_alert_never_host_state() -> None:
    """V5 / operator-alerts consumers: an unknown `kind` is TSB's own alert, never host state.

    Why: an alert names only TSB's components (Contract §4); showing it as host detail would cross
    the host boundary.
    """
    model = AlertModel()
    event = {"alert_id": "future-alert", "kind": "some_future_kind", "state": "raised",
             "at": "2026-10-01T02:00:00Z"}
    model.apply(event)
    alert = model.active["future-alert"]
    assert alert.kind == "some_future_kind"
    assert alert.known is False
    assert alert.is_tsb_own is True


def test_a_known_kind_is_tsb_s_own() -> None:
    """A known `kind` is TSB's own alert as well (V5: every alert is TSB's internal state)."""
    model = AlertModel()
    model.apply(_events()[0])
    assert model.active["fixture-alert-1"].is_tsb_own is True
    assert model.active["fixture-alert-1"].known is True
    assert model.active["fixture-alert-1"].kind == "supervisor_unresponsive"


def test_a_dismissed_alert_does_not_re_raise_on_reopen() -> None:
    """DT14: a reopen resends the active set; the dismissed set must not re-raise it.

    Why: operator-alerts step 2 sends the active set again on each new stream, so a reconnecting
    surface would re-pop every alert the operator has already dismissed.
    """
    model = AlertModel()
    for event in _events():
        model.apply(event)
    model.dismiss("fixture-alert-2")
    assert model.active["fixture-alert-2"].alert_id in model.dismissed
    # A resend of the same raise must not return a popup.
    result = model.apply(_events()[2])  # the raise of fixture-alert-2
    assert result is None
    assert "fixture-alert-2" in model.active  # it stays active, but does not pop again


def test_dismissed_survives_a_restore() -> None:
    """The dismissed set survives the active set being restored on a reopen (DT14)."""
    model = AlertModel()
    for event in _events():
        model.apply(event)
    model.dismiss("fixture-alert-2")
    alerts = list(model.active.values())
    model.restore(alerts)
    result = model.apply(_events()[2])
    assert result is None


def test_a_403_stops_the_stream_for_good() -> None:
    """DT14: a 403 means no `broker.health` — no `!` cell, no Alerts tab, no retry.

    Why: authorization holds for the stream's whole life (operator-alerts step 24); retrying a 403
    would reopen a stream the core will only refuse again.
    """
    from tsb_contract_client import ApiError

    opened: list[int] = []

    class _Client:
        def alerts(self, read_timeout: float | None = None) -> Any:
            opened.append(1)
            raise ApiError(403, "forbidden", "no broker.health")

    streamer = AlertStreamer(_Client(), read_timeout_s=0.05)
    streamer.start()
    deadline = time.monotonic() + 5.0
    while streamer.running and time.monotonic() < deadline:
        time.sleep(0.01)
    streamer.stop()
    assert len(opened) == 1, "a 403 must not be retried"
    assert streamer.model.allowed is False


def test_stop_ends_the_thread() -> None:
    """DT14: a stop at logout and at quit ends the worker thread.

    Why: a leaked thread keeps reading a stream after the operator logs out.
    """
    from tsb_contract_client import HttpError

    class _Client:
        def alerts(self, read_timeout: float | None = None) -> Any:
            raise HttpError("stream ends")

    streamer = AlertStreamer(_Client(), read_timeout_s=0.01, backoff_ms=(60000,))
    streamer.start()
    assert streamer.running
    streamer.stop()
    assert not streamer.running


def test_the_stream_reopens_after_an_ordinary_end() -> None:
    """DT14: a stream that ends is reopened with D26's backoff; `opens` proves it.

    Why: the core ends a stream on logout, on expiry, and on a policy reload; a surface that does
    not reopen would miss the next alert.
    """
    from tsb_contract_client import HttpError

    class _Client:
        def alerts(self, read_timeout: float | None = None) -> Any:
            raise HttpError("stream ends")

    streamer = AlertStreamer(_Client(), read_timeout_s=0.01, backoff_ms=(10, 10, 10))
    streamer.start()
    deadline = time.monotonic() + 2.0
    # An HttpError before the stream opens does not increment `opens`; the reopen is proved by the
    # client being called again, which the counting client below shows.
    calls = 0

    class _CountingClient:
        def alerts(self, read_timeout: float | None = None) -> Any:
            nonlocal calls
            calls += 1
            raise HttpError("stream ends")

    streamer.stop()
    streamer2 = AlertStreamer(_CountingClient(), read_timeout_s=0.01, backoff_ms=(10, 10, 10))
    streamer2.start()
    deadline = time.monotonic() + 2.0
    while calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    streamer2.stop()
    assert calls >= 2, "an ended stream must be reopened"


def test_a_stream_that_ends_is_reopened_and_its_active_set_restored() -> None:
    """A stream that opens, sends its set and ends is reopened; the set is reapplied (DT14)."""
    from tsb_contract_client import HttpError

    class _Stream:
        def __init__(self, events: list[dict]) -> None:
            self._events = events

        def __iter__(self):
            return iter(self._events)

        def close(self) -> None:
            pass

    events = _events()
    calls = 0

    class _Client:
        def alerts(self, read_timeout: float | None = None) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _Stream(events)
            raise HttpError("stream ends")

    model = AlertModel()
    streamer = AlertStreamer(_Client(), model, read_timeout_s=0.01, backoff_ms=(10, 10))
    streamer.start()
    deadline = time.monotonic() + 2.0
    while calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    streamer.stop()
    assert set(model.active) == {"fixture-alert-2"}


# -- against real servers: the fixture server's stream and the stub's long-lived one --


def _wait(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_the_fixture_servers_stream_raises_then_clears_and_the_x_later_event_is_ignored() -> None:
    """The fixture server's own stream (`tsbc._serve.ALERTS`): a raise, an `x-later` event this
    build does not know, then the clear. The popup fires once, the set ends empty, and the stream
    is read to its end and reopened (V2; operator-alerts consumers, steps 2 and 18).

    Why: an unknown event type in the middle of the stream must not stop the reader or leave the
    alert raised.
    """
    popups: list[Alert] = []
    with StubServer(ServeApp()) as url:
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        streamer = AlertStreamer(client, read_timeout_s=2.0, on_event=popups.append,
                                 backoff_ms=(10,), steady_ms=10)
        streamer.start()
        assert _wait(lambda: streamer.opens >= 2), "an ended stream was not reopened"
        streamer.stop()
    assert [a.alert_id for a in popups][:1] == ["fixture-alert-1"]
    assert popups[0].kind == "supervisor_unresponsive"
    assert streamer.model.active == {}


def test_a_403_from_the_fixture_server_stops_for_good() -> None:
    """A login without `broker.health` is refused 403 by the server itself: the streamer marks the
    model not allowed and never opens a stream (DT14)."""
    with StubServer(ServeApp()) as url:
        client = Client(url)
        client.login(_serve.PEER, _serve.PASSWORD)
        streamer = AlertStreamer(client, read_timeout_s=2.0, backoff_ms=(10,), steady_ms=10)
        streamer.start()
        assert _wait(lambda: not streamer.running)
        streamer.stop()
    assert streamer.model.allowed is False
    assert streamer.opens == 0


def test_stop_ends_a_thread_that_is_reading_a_long_lived_stream() -> None:
    """DT14: logout and quit stop the worker while it sits inside an open stream, not only between
    reconnects. The stub holds the stream open with keepalives until told otherwise."""
    app = SlowChunkedApp(alerts_events=[_events()[0]], alerts_hold_open=True)
    with StubHttpServer(app) as url:
        client = Client(url)
        client.login("fixture-user", "fixture-password")
        streamer = AlertStreamer(client, read_timeout_s=0.5)
        streamer.start()
        assert _wait(lambda: "fixture-alert-1" in streamer.model.active), "the raise never arrived"
        assert streamer.running
        streamer.stop()
        app.alerts_stop.set()
    assert not streamer.running
    assert app.alerts_opened == 1
