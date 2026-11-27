"""The operator alert stream (GET /alerts): a stdlib reader for its text/event-stream body.

The stream is server-sent events (the WHATWG EventSource format): lines of `field: value`, a blank
line ending each event, a line starting with ":" a comment the core sends as a keepalive. Each
`alert` event's data is one AlertEvent (openapi/tsb.openapi.json) as JSON. An event of a type this
client does not know is skipped — a receiver's rule — and so is a comment. The reader holds the
peer to two limits, tsbc._http.MAX_STREAM_LINE per line and MAX_EVENT_DATA per event's data, and
fails the stream past either.
"""

from __future__ import annotations

from typing import Any, Self

from tsbc import _http
from tsbc.schema import loads

EVENT_TYPES = frozenset({"alert"})


class AlertStream:
    """Iterate for the next alert event's data (a dict); the iteration ends when the core ends the
    stream (a logout, an expired login, or broker.health taken away). close() when done."""

    def __init__(self, response: _http.StreamResponse) -> None:
        self._response = response

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> dict[str, Any]:
        event = self.next_event()
        if event is None:
            raise StopIteration
        return event

    def next_event(self) -> dict[str, Any] | None:
        """The next alert, or None at the end of the stream. Raises HttpError on a transport
        failure, or when the read timeout passes (a stream the core keeps alive never idles past
        heartbeat_interval); and StreamLimitError, an HttpError, when a line is longer than
        MAX_STREAM_LINE or one event's data longer than MAX_EVENT_DATA (tsbc._http), where the
        stream is not read further."""
        while True:
            got = _http.next_event(self._response)
            if got is None:
                return None  # the stream ended; an event never completed by a blank line is dropped
            kind, data = got
            if kind not in EVENT_TYPES:
                continue
            try:
                event = loads(data)
            except ValueError:
                continue
            if isinstance(event, dict):
                return event

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
