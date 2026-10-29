"""The contract's fixture server, taught to say what it held at the moment it handled `POST /auth/logout`.

Sign out's order is the point of it: every session this TUI had open is detached on the
server before the broker is told to forget the login, so the core sees ordinary detaches whatever the contract's open
item 5 (`login.md`) becomes. Only the server can say what it held at that moment, so its logout handler records it.
Test scaffolding, like `input_server.py`.
"""

from __future__ import annotations

import threading

from tsbc._serve import ServeApp


class LogoutProbeApp(ServeApp):
    """`at_logout`: for each session, its state and how many clients were attached when logout was handled (None until
    then). `logout_status`: answer logout with this HTTP status instead of handling it (a broker that fell over, or a
    login that had already lapsed)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.at_logout: dict[str, tuple[str, int]] | None = None
        self.logout_status: int | None = None

    def _http(self, method, path, headers, body, query=None):
        if (method, path) == ("POST", "/auth/logout"):
            self.at_logout = {sid: (sess["state"], len(self.clients.get(sid, {})))
                              for sid, sess in self.sessions.items()}
            if self.logout_status is not None:
                code = "unauthenticated" if self.logout_status == 401 else "internal"
                return self._err(self.logout_status, code, "the broker did not take it")
        return super()._http(method, path, headers, body, query)


class HeldRequestApp(LogoutProbeApp):
    """What the server saw of each request, and a way to keep one waiting before the server handles it.

    `seen`: `(method, path, principal)` for every request, in arrival order; the principal is the user the request's
    cookie names at that moment (None: no cookie, or one the server no longer holds). Whoever carried a request is
    only knowable here, from the cookie that reached the server.
    `holds`: `(method, path prefix)` -> a `threading.Event`; a matching request waits for it (up to 10 s) before it is
    handled, so a test can answer it later than a timeout. `arrived` is set when one is waiting.
    The wait is in `http`, before the base class takes the server's lock, so every other request still goes through."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, str, str | None]] = []
        self.holds: dict[tuple[str, str], threading.Event] = {}
        self.arrived = threading.Event()

    def http(self, method, path, headers, body):
        self.seen.append((method, path, self._user(headers)))
        for (held_method, prefix), release in list(self.holds.items()):
            if method == held_method and path.startswith(prefix):
                self.arrived.set()
                release.wait(10)
        return super().http(method, path, headers, body)
