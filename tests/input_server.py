"""The contract's in-process fixture server (DT7), taught to record the input bytes each client sends
and to hold one `token` frame back behind a gate.

`tsbc._serve.ServeApp` echoes a holder's input as output and refuses everyone else's, but it keeps no
record of the bytes it was sent, and an input-path test needs exactly that: "this client sent nothing"
is only a fact when the server can say what it received. Two additions, both test scaffolding:

* `received(client_id)`: every binary frame that client sent, in order. The recorder wraps the
  socket's `recv`, so a frame is recorded whether the fixture then forwards it, refuses it or treats
  it as a take-over.
* `hold_token`: a `threading.Event` a test assigns before it hands off. Until the test sets it, the
  hand-off's `token` frame waits (`token_held` is set once it is waiting), so a test can act inside
  the window between a client sending a hand-off and learning the keyboard moved (typing-ownership
  step 5, then the `token` frame of step 16).

Frames arrive from one client in the order it sent them, so "a later text frame has been recorded"
is proof that every binary frame sent before it has been recorded too: tests use that as their
"nothing more is coming" marker instead of waiting on time.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator

from stub_server import resize_requests
from tsb_contract_client import Client
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.terminal import TerminalScreen
from tsbc import _serve
from tsbc._serve import ServeApp

# The size the marker frame asks for; any valid size will do, and the fixture clamps it.
MARKER_SIZE = (91, 33)
# A test that leaves the gate shut must not leave a server thread parked for ever.
GATE_TIMEOUT_S = 20.0


class InputRecordingApp(ServeApp):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._inputs: list[tuple[str | None, bytes]] = []
        self.hold_token: threading.Event | None = None
        self.token_held = threading.Event()

    def received(self, client_id: str) -> list[bytes]:
        return [data for who, data in list(self._inputs) if who == client_id]

    def websocket(self, path, ws) -> None:
        original = ws.recv

        def recv():
            message = original()
            if message.kind == "binary":
                self._inputs.append((self._client_of(ws), message.data))
            return message

        ws.recv = recv
        super().websocket(path, ws)

    def _client_of(self, ws) -> str | None:
        for clients in list(self.clients.values()):
            for client_id, socket in list(clients.items()):
                if socket is ws:
                    return client_id
        return None

    def _send_token(self, sess, reason) -> None:
        gate = self.hold_token
        if gate is not None and reason == "handoff":
            self.token_held.set()
            if not gate.wait(GATE_TIMEOUT_S):
                raise TimeoutError("the token gate was never opened")
        super()._send_token(sess, reason)


def make_session(url: str, user: str = _serve.USER, cols: int = 80, rows: int = 24,
                 target: str = _serve.TARGET) -> str:
    """A session created by `user` through its own client, so the TUI can attach as that user (the
    owner), as another principal, or after another client of the owner's."""
    client = Client(url)
    client.login(user, _serve.PASSWORD)
    return client.create_session(target, "new", "xterm-256color", cols, rows)["session"]["id"]


@contextlib.contextmanager
def other_client(url: str, user: str, session_id: str) -> Iterator[object]:
    """A second client, attached before the TUI under `user`, so it holds the keyboard (the fixture
    gives it to the first client that may type) and the TUI joins as someone who does not."""
    client = Client(url)
    client.login(user, _serve.PASSWORD)
    attachment = client.attach(session_id)
    try:
        yield attachment
    finally:
        attachment.close()


async def attached(pilot, app: TsbTuiApp, session_id: str, wait_until: Callable) -> TerminalScreen:
    """Push the terminal screen and wait until it has attached and painted the session's dump."""
    app.push_screen(TerminalScreen(session_id))
    screen = app.screen
    await wait_until(
        pilot, lambda: screen.pump is not None and screen.pump.state.dump_seen
        and screen.canvas.painted, "the screen to attach and paint the dump")
    return screen


async def flushed(pilot, screen: TerminalScreen, serve_app: InputRecordingApp,
                  wait_until: Callable) -> None:
    """Wait until everything this client sent before now has reached the server. A text frame sent
    afterwards is read after every earlier frame of the same client, so its being recorded proves it
    (and a test that asserts "nothing was sent" is then asserting on a settled record, not on time)."""
    before = len(resize_requests(serve_app))
    await screen.pump.resize(*MARKER_SIZE, timeout=0)
    await wait_until(pilot, lambda: len(resize_requests(serve_app)) > before,
                     "the marker frame to reach the server")
