"""The close table (DT13, conventions V3): every close's state and whether the pump reattaches.

Why this matters more than any other model test (Rule 9): a WebSocket close is the one place the
operator's session can be lost by a wrong guess. 4014 must never auto-reattach (client-remove rule
8: "MUST NOT re-attach by itself after 4014"); 4011 is a detach, never an end (session-detach rule
10); a code-less close or 1006 is the transport dying, and only the *record* says whether the
session lives (session model §13); 4001 retries exactly once, because a one-time ticket may have
expired between mint and presentation (session-attach R4); an unknown 4xxx reads as 4010 (V3); a
401 during a reattach goes back to Login (the login this client held no longer works).

The pure `close_outcome` table is checked row by row, then the pump's reattach behaviour is driven
with a fake attachment, so each rule is exercised without a wall clock or a real socket.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from stub_server import SlowChunkedApp, StubServer
from tsb_contract_client import Client

from tsb_tui.model.attachment import (
    CLOSE_BAD_ATTACH,
    CLOSE_DETACHED_BY_SERVER,
    CLOSE_FORBIDDEN,
    CLOSE_INTERNAL,
    CLOSE_KILLED,
    CLOSE_NORMAL,
    CLOSE_REMOVED,
    CLOSE_SESSION_NOT_FOUND,
    CLOSE_TERMINATED,
    CLOSE_TICKET_INVALID,
    AttachmentPump,
    PumpHooks,
    close_outcome,
)

# Every row of DT13's table, with the state it shows and whether the pump reattaches.
ROWS = [
    (CLOSE_KILLED, "ended_killed", "never"),
    (CLOSE_TERMINATED, "ended_terminated", "never"),
    (CLOSE_REMOVED, "removed", "never"),
    (CLOSE_SESSION_NOT_FOUND, "session_gone", "never"),
    (CLOSE_FORBIDDEN, "may_not_join", "never"),
    (CLOSE_BAD_ATTACH, "attach_defect", "never"),
    (CLOSE_TICKET_INVALID, "ticket_refused", "once"),
    (CLOSE_DETACHED_BY_SERVER, "detached", "backoff"),
    (CLOSE_NORMAL, "transport_lost", "backoff"),
    (1001, "transport_lost", "backoff"),
    (1006, "transport_lost", "backoff"),
    (CLOSE_INTERNAL, "transport_lost", "backoff"),
    (None, "transport_lost", "backoff"),
    (4004, "session_gone", "never"),  # the known 4004, repeated for clarity
    (4005, "ended_killed", "never"),  # an unknown 4xxx reads as 4010
    (4999, "ended_killed", "never"),
]


@pytest.mark.parametrize("code,show,reattach", ROWS, ids=[str(r[0]) for r in ROWS])
def test_the_close_table_row(code: int | None, show: str, reattach: str) -> None:
    """Each close's state and reattach rule (DT13). A wrong row loses the operator's session or
    reattaches a session the server ended."""
    outcome = close_outcome(code)
    assert outcome.show == show
    assert outcome.reattach == reattach


def test_only_the_transport_codes_read_the_record() -> None:
    """The record is read only when the close itself does not carry the state: a transport loss,
    and 4004 (which says the session may have ended but not which way). A 4010/4012/4014 already
    carries its state (DT13)."""
    for code in (None, CLOSE_NORMAL, 1006, CLOSE_INTERNAL, CLOSE_SESSION_NOT_FOUND):
        assert close_outcome(code).read_record is True
    for code in (CLOSE_KILLED, CLOSE_TERMINATED, CLOSE_REMOVED, CLOSE_DETACHED_BY_SERVER):
        assert close_outcome(code).read_record is False


def test_only_a_malformed_attach_is_loud() -> None:
    """4000 is this client's own defect and is reported loudly (DT13); every other close is a
    state to show, not a bug."""
    assert close_outcome(CLOSE_BAD_ATTACH).loud is True
    for code in (CLOSE_KILLED, CLOSE_REMOVED, CLOSE_TICKET_INVALID, None):
        assert close_outcome(code).loud is False


# -- the pump's reattach behaviour, with a fake attachment --


class FakeAttachment:
    def __init__(self, *, close_code: int | None = None, close_reason: str = "",
                 messages: list[Any] | None = None) -> None:
        self.close_code = close_code
        self.close_reason = close_reason
        # What `AsyncAttachment.open` leaves behind: it consumes `attached`, so the pump reads
        # these two attributes instead of a frame.
        self.client_id = "c1"
        self.session = {"id": "sess-1", "owner": "fixture-user", "control_holder": None}
        self.closed = False
        self._messages = list(messages or [])
        self.sent: list[bytes] = []
        self.resizes: list[tuple[int, int, float]] = []
        self.handoffs: list[str | None] = []
        self.quiet_answers: list[tuple[str, bool]] = []
        self.stale_answers: list[tuple[str, str]] = []

    async def recv_message(self) -> Any:
        if self._messages:
            return self._messages.pop(0)
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def resize(self, cols: int, rows: int, timeout: float | None = None) -> None:
        self.resizes.append((cols, rows, timeout if timeout is not None else -1.0))

    async def handoff(self, to: str | None) -> None:
        self.handoffs.append(to)

    async def answer_quiet_prompt(self, prompt_id: str, kill: bool) -> None:
        self.quiet_answers.append((prompt_id, kill))

    async def answer_stale(self, prompt_id: str, choice: str) -> None:
        self.stale_answers.append((prompt_id, choice))

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    """An `async_attach` that hands out prepared attachments and can be told to refuse."""

    def __init__(self, attachments: list[Any]) -> None:
        self._attachments = list(attachments)
        self.attempts = 0
        self.record_states: list[str] = []
        self.record_error: Exception | None = None

    async def async_attach(self, session_id: str, cols: int | None = None,
                           rows: int | None = None) -> Any:
        self.attempts += 1
        item = self._attachments.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def session(self, session_id: str) -> dict:
        if self.record_error is not None:
            raise self.record_error
        return {"state": self.record_states.pop(0) if self.record_states else "detached"}


async def _run_pump(client: FakeClient, *, code: int | None, record_states: list[str] | None = None,
                    sleep: Any = None) -> AttachmentPump:
    client.record_states = list(record_states or [])
    sink_calls: list[tuple[str, bytes]] = []
    pump = AttachmentPump(client, "sess-1", sink=lambda role, data: sink_calls.append((role, data)),
                          sleep=sleep or _no_sleep)
    await pump.start()
    await pump.wait_closed()
    return pump


async def _no_sleep(_seconds: float) -> None:
    return None


def test_4014_never_reattaches() -> None:
    """A removed client must not re-attach by itself (client-remove rule 8; DT13). Reattaching
    would defeat the owner's removal."""
    att = FakeAttachment(close_code=CLOSE_REMOVED)
    client = FakeClient([att])
    pump = asyncio.run(_run_pump(client, code=CLOSE_REMOVED))
    assert client.attempts == 1
    assert pump.state.close.show == "removed"


def test_4010_and_4012_never_reattach() -> None:
    """A killed or terminated session is over; a reattach would open a session that is gone."""
    for code, show in ((CLOSE_KILLED, "ended_killed"), (CLOSE_TERMINATED, "ended_terminated")):
        att = FakeAttachment(close_code=code)
        client = FakeClient([att])
        pump = asyncio.run(_run_pump(client, code=code))
        assert client.attempts == 1
        assert pump.state.close.show == show


def test_4011_reattaches_with_backoff() -> None:
    """4011 is a detach with the session alive; the pump reattaches (session-detach rule 10;
    DT13). The second attachment is the one that continues."""
    first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
    second = FakeAttachment(close_code=CLOSE_KILLED)  # ends the test on the reattached socket
    client = FakeClient([first, second])
    pump = asyncio.run(_run_pump(client, code=CLOSE_DETACHED_BY_SERVER, record_states=["live"]))
    assert client.attempts == 2
    assert pump.reattach_attempts == 1


def test_1006_from_a_dropped_socket_reattaches_while_the_record_is_live() -> None:
    """A dropped socket (1006) reattaches while the record is live/detached/unreachable
    (DT13; session model §13: the transport dying ends nothing)."""
    first = FakeAttachment(close_code=1006)
    second = FakeAttachment(close_code=CLOSE_KILLED)
    client = FakeClient([first, second])
    pump = asyncio.run(_run_pump(client, code=1006, record_states=["detached"]))
    assert client.attempts == 2
    assert pump.reattach_attempts == 1


def test_a_transport_loss_does_not_reattach_when_the_record_is_ended() -> None:
    """A transport loss reads the record; if it is killed or terminated the session is over, so
    the pump stops (DT13). A surface that reattached would loop on a dead session."""
    first = FakeAttachment(close_code=1006)
    client = FakeClient([first])
    pump = asyncio.run(_run_pump(client, code=1006, record_states=["killed"]))
    assert client.attempts == 1
    assert pump.reattach_attempts == 0


def test_4001_retries_exactly_once() -> None:
    """4001 retries once with a new ticket, then never (session-attach R4; DT13). A ticket may
    have expired between mint and presentation; a second refusal is final."""
    first = FakeAttachment(close_code=CLOSE_TICKET_INVALID)
    second = FakeAttachment(close_code=CLOSE_TICKET_INVALID)
    client = FakeClient([first, second])
    pump = asyncio.run(_run_pump(client, code=CLOSE_TICKET_INVALID))
    assert client.attempts == 2  # the first attach, then the one retry
    assert pump.reattach_attempts == 1


def test_4001_retry_that_succeeds_keeps_reading() -> None:
    """A 4001 retry that attaches continues the stream; the retry is not a reattach loop."""
    first = FakeAttachment(close_code=CLOSE_TICKET_INVALID)
    second = FakeAttachment(close_code=CLOSE_KILLED)
    client = FakeClient([first, second])
    pump = asyncio.run(_run_pump(client, code=CLOSE_TICKET_INVALID))
    assert client.attempts == 2
    assert pump.state.close.show == "ended_killed"  # the second socket's close


def test_reattach_attachrefused_4003_stops() -> None:
    """A reattach refused 4003 stops with its row (DT13): the principal may no longer join."""
    from tsb_contract_client import AttachRefused

    first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
    client = FakeClient([first, AttachRefused(CLOSE_FORBIDDEN, "no longer")])
    pump = asyncio.run(_run_pump(client, code=CLOSE_DETACHED_BY_SERVER, record_states=["live"]))
    assert client.attempts == 2
    assert pump.state.close.show == "may_not_join"


def test_reattach_attachrefused_4004_stops() -> None:
    """A reattach refused 4004 stops: the session is gone or ended (DT13)."""
    from tsb_contract_client import AttachRefused

    first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
    client = FakeClient([first, AttachRefused(CLOSE_SESSION_NOT_FOUND, "gone")])
    pump = asyncio.run(_run_pump(client, code=CLOSE_DETACHED_BY_SERVER, record_states=["live"]))
    assert client.attempts == 2
    assert pump.state.close.show == "session_gone"


def test_reattach_mint_403_or_404_stops_as_its_row() -> None:
    """A mint 403 or 404 during a reattach stops as its row (DT13)."""
    from tsb_contract_client import ApiError

    for status, show in ((403, "may_not_join"), (404, "session_gone")):
        first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
        client = FakeClient([first, ApiError(status, "x", "no")])
        pump = asyncio.run(_run_pump(client, code=CLOSE_DETACHED_BY_SERVER, record_states=["live"]))
        assert client.attempts == 2
        assert pump.state.close.show == show


def test_reattach_mint_401_goes_back_to_login() -> None:
    """A mint 401 during a reattach means the login no longer works: back to Login, and stop
    (DT13). A surface that retried a 401 would loop forever on an expired session."""
    from tsb_contract_client import ApiError

    logins: list[bool] = []
    first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
    client = FakeClient([first, ApiError(401, "unauthenticated", "log in")])
    pump = AttachmentPump(client, "sess-1", sink=lambda *_: None,
                          hooks=PumpHooks(on_login_needed=lambda: logins.append(True)),
                          sleep=_no_sleep)

    async def go() -> None:
        await pump.start()
        await pump.wait_closed()

    asyncio.run(go())
    assert client.attempts == 2
    assert logins == [True]


def test_reattach_5xx_keeps_backing_off() -> None:
    """An HttpError or a 5xx during a reattach keeps backing off, then the last step's cadence
    (DT13). The test proves the pump tries again past a 5xx rather than stopping."""
    from tsb_contract_client import ApiError

    first = FakeAttachment(close_code=CLOSE_DETACHED_BY_SERVER)
    third = FakeAttachment(close_code=CLOSE_KILLED)
    client = FakeClient([first, ApiError(500, "internal", "boom"), third])
    pump = asyncio.run(_run_pump(client, code=CLOSE_DETACHED_BY_SERVER,
                                 record_states=["live", "live"]))
    assert client.attempts == 3  # attach, refused 5xx, attach again
    assert pump.state.close.show == "ended_killed"


def test_a_5001_close_reads_as_4010() -> None:
    """A 4xxx the client does not know reads as 4010 (V3; DT13). The state is an ended session,
    never a reason to reattach."""
    att = FakeAttachment(close_code=4999)
    client = FakeClient([att])
    pump = asyncio.run(_run_pump(client, code=4999))
    assert client.attempts == 1
    assert pump.state.close.show == "ended_killed"


# -- the same rows against a real socket: the stub closes it, the real client re-attaches --


async def _stub_pump(app: SlowChunkedApp, url: str, *, until, timeout_s: float = 10.0) -> AttachmentPump:
    """Attach a real `Client` to the stub and read until `until(pump)` holds, then stop. A bounded
    wait on a condition, never a sleep for an outcome (the plan's test conventions)."""
    client = Client(url)
    client.login("fixture-user", "fixture-password")
    session_id = client.create_session("stub-target", "new", "xterm-256color", 80, 24)["session"]["id"]
    pump = AttachmentPump(client, session_id, sink=lambda *_: None, backoff_ms=(10, 10, 10), steady_ms=10)
    await pump.start()
    try:
        async with asyncio.timeout(timeout_s):
            while not until(pump):
                if pump._task is not None and pump._task.done():
                    break
                await asyncio.sleep(0.01)
    finally:
        await pump.stop()
    return pump


def test_stub_4012_ends_and_never_reattaches() -> None:
    """A terminated session closes 4012 on a live socket: the state is ended and nothing re-attaches
    (DT13). A pump that retried would open a session the core already ended."""
    app = SlowChunkedApp(delay_s=0.0, max_chunks=50, stream_close=(CLOSE_TERMINATED, 2))
    with StubServer(app) as url:
        pump = asyncio.run(_stub_pump(app, url, until=lambda p: p.state.closed))
    assert pump.state.close.show == "ended_terminated"
    assert app.attaches == 1


def test_stub_4011_is_a_detach_and_the_pump_attaches_again_with_a_new_ticket() -> None:
    """4011 is the server detaching a live session (session-detach 10, 12): the pump must come back,
    and each attach is its own ticket mint, so the second attach is a second `attach` frame."""
    app = SlowChunkedApp(delay_s=0.0, max_chunks=50, stream_close=(CLOSE_DETACHED_BY_SERVER, 2))
    with StubServer(app) as url:
        pump = asyncio.run(_stub_pump(app, url, until=lambda p: app.attaches >= 2))
    assert pump.reattach_attempts >= 1
    assert app.attaches >= 2


def test_stub_1006_from_a_dropped_socket_reattaches_while_the_record_is_live() -> None:
    """A socket dropped with no close frame is the client's 1006 (tsbc/_aws.py): the record still
    says live, so the pump reattaches (DT13; session model 13: the transport dying ends nothing)."""
    app = SlowChunkedApp(delay_s=0.0, max_chunks=50, drop_after_chunks=2)
    with StubServer(app) as url:
        pump = asyncio.run(_stub_pump(app, url, until=lambda p: app.attaches >= 2))
    assert pump.reattach_attempts >= 1
    assert app.attaches >= 2


def test_stub_4001_on_a_reattach_is_retried_exactly_once() -> None:
    """After a 4011 the reattach is refused 4001 each time: one retry with a new ticket, then it
    stops (DT13). Three attaches in all: the first, the reattach, the one retry."""
    app = SlowChunkedApp(delay_s=0.0, max_chunks=50, stream_close=(CLOSE_DETACHED_BY_SERVER, 2),
                         attach_close_code_after_first=CLOSE_TICKET_INVALID)
    with StubServer(app) as url:
        pump = asyncio.run(_stub_pump(app, url, until=lambda p: False))
    assert app.attaches == 3
    assert pump.state.close.show == "ticket_refused"


def test_account_close_never_reattaches_reads_no_record_and_goes_login():
    row = close_outcome(4017)
    assert row.reattach == "never" and row.go_login and not row.read_record
    att = FakeAttachment(close_code=4017)
    client = FakeClient([att])
    pump = asyncio.run(_run_pump(client, code=4017))
    assert client.attempts == 1 and pump.reattach_attempts == 0
    assert close_outcome(4998) == close_outcome(4010)
