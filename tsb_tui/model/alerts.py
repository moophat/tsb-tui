"""The operator alert stream (design D22, DT14): one `GET /alerts` per login, in a worker thread.

Why it is a worker thread and not on Textual's loop (DT14): the stream is long-lived and blocks on
each read, so reading it on the UI thread would freeze the TUI. Results are handed out through the
same callback pattern as `FleetPoller`, which the app wraps in `call_from_thread`.

Rules this module owns (DT14, operator-alerts consumers):

  * a 403 is not a holder: no `!` cell, no Alerts tab, no retry — the stream is never opened again
    for that login;
  * an event of a type this client does not know is ignored (V2), as is a comment line (the
    client's `AlertStream` already skips both);
  * an alert `kind` this build does not know is kept as TSB's own alert, never as host state (V5;
    contract README "Senders and receivers");
  * a stream that ends is reopened with D26's backoff, and the active set it resends (operator-
    alerts step 2) never re-raises a popup this TUI already dismissed;
  * `stop()` ends the thread, for logout and quit.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tsb_contract_client import ApiError, HttpError

# The kinds this build has text for. An unknown kind is still TSB's own alert (V5); this set only
# decides which text the TUI shows, never whether the alert exists.
KNOWN_KINDS = frozenset({"supervisor_unresponsive", "worker_unkillable", "vt_unavailable"})

# D26's backoff for a stream that ends, then a steady cadence.
BACKOFF_MS = (500, 1000, 2000, 5000, 10000, 30000)
STEADY_MS = 30000


@dataclass(frozen=True)
class Alert:
    """One active alert. `kind` is kept even when unknown, so a later TUI can label it. Every alert
    is TSB's own internal state, never host state (Contract §4; V5)."""

    alert_id: str
    kind: str
    state: str
    at: str
    subjects: dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.kind in KNOWN_KINDS

    @property
    def is_tsb_own(self) -> bool:
        """Always true: an alert names only TSB's components, never a pid, path or hostname."""
        return True

    @property
    def text(self) -> str:
        return _text_for(self)


def _text_for(alert: Alert) -> str:
    if alert.kind == "supervisor_unresponsive":
        return f"A supervisor stopped answering: {alert.subjects.get('supervisor_id', '?')}."
    if alert.kind == "worker_unkillable":
        return f"A worker will not die: {alert.subjects.get('worker_id', '?')}."
    if alert.kind == "vt_unavailable":
        return "The VT worker is unavailable; grids may not be advancing."
    return "TSB reported an alert."


def alert_from_event(event: dict[str, Any]) -> Alert:
    """Build an `Alert` from one `AlertEvent`. Unknown fields are dropped (V2)."""
    subjects = {key: value for key, value in event.items()
                if key not in ("alert_id", "kind", "state", "at")}
    return Alert(alert_id=event["alert_id"], kind=event.get("kind", ""),
                 state=event.get("state", ""), at=event.get("at", ""), subjects=subjects)


class AlertModel:
    """The active set by `alert_id`, the dismissed set that survives a reopen, and the derived
    state a surface reads. No I/O."""

    def __init__(self, *, popup: bool = True) -> None:
        self.popup = popup
        self.active: dict[str, Alert] = {}
        self.dismissed: set[str] = set()
        self.allowed = True  # false once a 403 says this login is not a broker.health holder

    @classmethod
    def from_config(cls, config: Any) -> AlertModel:
        """`alerts.popup` decides whether a raise pops (DT14, DT19)."""
        return cls(popup=config["alerts.popup"])

    @property
    def count(self) -> int:
        return len(self.active)

    def apply(self, event: dict[str, Any]) -> Alert | None:
        """Apply one `alert` event. Returns the alert if it should raise a popup (a raise not
        already dismissed), else None. A clear naming its raise removes it."""
        alert = alert_from_event(event)
        if alert.state == "raised":
            self.active[alert.alert_id] = alert
            if self.popup and alert.alert_id not in self.dismissed:
                return alert
            return None
        if alert.state == "cleared":
            self.active.pop(alert.alert_id, None)
        return None

    def dismiss(self, alert_id: str) -> None:
        """Dismiss a popup. The alert stays active, and the rail's `!n` count does not drop; a
        reopen that resends it must not re-raise the popup (DT14)."""
        self.dismissed.add(alert_id)

    def mark_forbidden(self) -> None:
        """A 403: this login is not a broker.health holder. No `!` cell, no Alerts tab, no retry."""
        self.allowed = False
        self.active.clear()

    def restore(self, active: list[Alert]) -> None:
        """Replace the active set with a stream's opening set, without re-raising a dismissed one."""
        self.active = {alert.alert_id: alert for alert in active}


class AlertStreamer:
    """Owns the `GET /alerts` worker thread. `on_event(alert)` is called for each raise that should
    pop; `on_change()` after any change to the set. Both run on the worker thread; the app wraps
    them in `call_from_thread`."""

    def __init__(self, client: Any, model: AlertModel | None = None, *, read_timeout_s: float = 30.0,
                 on_event: Callable[[Alert], None] | None = None,
                 on_change: Callable[[], None] | None = None,
                 backoff_ms: tuple[int, ...] = BACKOFF_MS, steady_ms: int = STEADY_MS) -> None:
        self._client = client
        self.model = model if model is not None else AlertModel()
        self._read_timeout = read_timeout_s
        self._on_event = on_event
        self._on_change = on_change
        self._backoff_ms = backoff_ms
        self._steady_ms = steady_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stream: Any = None
        self.opens = 0

    @classmethod
    def from_config(cls, client: Any, model: AlertModel, config: Any, *,
                    on_event: Callable[[Alert], None],
                    on_change: Callable[[], None]) -> AlertStreamer:
        """`alerts.read_timeout_s` bounds each read (DT14, DT19)."""
        return cls(client, model, read_timeout_s=config["alerts.read_timeout_s"],
                   on_event=on_event, on_change=on_change)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """End the stream now: closing it wakes a read that would otherwise wait for the next event (the
        core's comment lines are not events), so the thread does not outlive logout or quit."""
        self._stop.set()
        stream = self._stream
        if stream is not None:
            with contextlib.suppress(Exception):  # best effort: the thread ends either way
                stream.close()
        if self._thread is not None:
            self._thread.join(timeout=min(self._read_timeout, 2.0) + 0.5)
            self._thread = None

    def _run(self) -> None:
        step = 0
        while not self._stop.is_set() and self.model.allowed:
            if step > 0:
                delay = self._backoff_ms[step - 1] if step <= len(self._backoff_ms) else self._steady_ms
                if self._stop.wait(delay / 1000.0):
                    return
            step += 1
            try:
                stream = self._client.alerts(read_timeout=self._read_timeout)
            except ApiError as exc:
                if exc.status == 403:
                    self.model.mark_forbidden()
                    if self._on_change is not None:
                        self._on_change()
                    return
                continue  # a 401 or a 5xx: back off and try again
            except (HttpError, OSError):
                continue
            self.opens += 1
            self._stream = stream
            try:
                self._read_stream(stream)
            except (HttpError, OSError, ValueError):
                pass  # a read timeout, a transport failure or a stream closed by `stop`: back off and reopen
            finally:
                self._stream = None
                with contextlib.suppress(Exception):  # already closed by `stop`
                    stream.close()

    def _read_stream(self, stream: Any) -> None:
        for event in stream:
            if self._stop.is_set():
                return
            alert = self.model.apply(event)
            if alert is not None and self._on_event is not None:
                self._on_event(alert)
            if self._on_change is not None:
                self._on_change()
