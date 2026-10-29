"""The New session dialog's login fields (login-at-connect phase A5; rulings 2026-10-29 14:05 A13, 15:36 item 6).

Why it matters (Rule 9): the dialog is where an operator types a target account's password. Four things must hold,
and a dialog that breaks any of them is worse than one with no fields. (1) What is typed is what the core receives,
on a new connection, and nowhere else afterwards: not in an attribute, not kept for a retry. (2) Half a login never
reaches the core, which would otherwise answer a stored login's way and surprise the operator. (3) A login that the
deployment records is never sent as consented before the operator has been shown the warning (and the core's own
refusal, which asks for that consent, is shown as the core says it). (4) Empty fields mean "what TSB holds" and, when TSB holds nothing,
lead the operator to the username field instead of a dead end. Server: the contract's in-process fixture server
(`tsbc serve`), which applies the contract's login rules, so what the dialog sends is judged by the contract's own
reading of it. Every secret here is synthetic.
"""

from __future__ import annotations

import asyncio
import threading

from stub_server import SlowChunkedApp, StubServer
from textual.widgets import Button, Input, RadioButton, RadioSet, Static
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen
from tsbc import _serve
from tsbc._serve import ServeApp
from workspace_support import fixture_login

USER = "fixture-account"
PASSWORD = "FIXTURE-NOT-A-PASSWORD-typed"
# What the dialog says; the tests read the rendered text, so a reworded sentence is a deliberate change of both.
NO_LOGIN_HELD = "TSB holds no login for this target. Type one, or cancel."
HALF_LOGIN = "Type both a username and a password, or leave both empty."
RECORDED = "Your login answers are recorded."


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


def _me_reads(serve_app) -> int:
    return len([1 for method, path, _body in serve_app.requests if (method, path) == ("GET", "/auth/me")])


def _text(widget: Static) -> str:
    return str(widget.render())


async def _open_dialog(app, pilot, wait_until, *, target: str | None, target_type: str | None) -> NewSessionScreen:
    screen = await fixture_login(app, pilot)
    app.cfg.set_runtime("size.auto_request", True)
    screen._new_session_dialog(target, target_type)
    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted
                     and bool(app.screen.query("#create-button")), "the New session dialog")
    return app.screen


def _type_login(dialog: NewSessionScreen, user: str = USER, password: str = PASSWORD) -> None:
    dialog.query_one("#login-user", Input).value = user
    dialog.query_one("#login-password", Input).value = password


def _holds(obj: object, secret: str, seen: set[int] | None = None) -> bool:
    """Whether `secret` is reachable as text from `obj`'s own attributes, through dicts, lists, tuples and sets (not
    through other objects: Textual's own machinery is not the dialog's)."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return False
    seen.add(id(obj))
    if isinstance(obj, str):
        return secret in obj
    if isinstance(obj, bytes):
        return secret.encode() in obj
    if isinstance(obj, dict):
        return any(_holds(k, secret, seen) or _holds(v, secret, seen) for k, v in obj.items())
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_holds(item, secret, seen) for item in obj)
    return False


def _attribute_holds(obj: object, secret: str) -> bool:
    return _holds(dict(vars(obj)), secret)


def test_typed_login_is_sent_with_connection_new_and_cleared_after(serve, wait_until) -> None:
    """The body the core sees is `login` with a password auth and `connection: new`, and once the answer is in both
    inputs are empty and nothing the screen or its inputs hold carries the password (the dialog keeps no copy)."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            user_input = dialog.query_one("#login-user", Input)
            password_input = dialog.query_one("#login-password", Input)
            assert password_input.password, "the password field is masked"
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            body = _creates(serve_app)[-1]
            assert body["target_id"] == "normal" and body["connection"] == "new"
            assert body["login"] == {"user": USER, "auth": {"kind": "password", "password": PASSWORD}}, (
                "no recording_acknowledged is sent while nothing is recorded: the operator was shown no warning")
            await wait_until(pilot, lambda: app.screen is not dialog, "the dialog to go")
            assert user_input.value == "" and password_input.value == ""
            for holder in (dialog, user_input, password_input):
                assert not _attribute_holds(holder, PASSWORD), f"{type(holder).__name__} still holds the password"

    asyncio.run(check())


def test_a_refused_login_also_clears_the_fields_and_keeps_the_dialog_open(wait_until) -> None:
    """Whatever the answer: a 502 target_auth_failed ends in the same empty fields, with the core's message shown and
    no copy kept for a second try (the operator types it again)."""

    async def check() -> None:
        stub = SlowChunkedApp(create_error=(502, "target_auth_failed", "the target refused the login"))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
                _type_login(dialog)
                dialog.query_one("#create-button", Button).press()
                await wait_until(pilot, lambda: bool(stub.creates), "the create")
                await wait_until(pilot, lambda: not dialog.query_one("#create-button", Button).disabled,
                                 "the refusal to land")
                assert stub.creates[-1]["login"]["auth"]["password"] == PASSWORD
                assert app.screen is dialog
                assert dialog.query_one("#login-user", Input).value == ""
                assert dialog.query_one("#login-password", Input).value == ""
                assert _text(dialog.query_one("#new-session-error", Static)) == "the target refused the login"
                assert not _attribute_holds(dialog, PASSWORD)

    asyncio.run(check())


def test_half_a_login_is_refused_before_any_call(serve, wait_until) -> None:
    """One field without the other is the operator's slip, and sending either would let the core guess which login
    was meant: the dialog says so and nothing reaches the core (not the create, not a read of the target)."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: _me_reads(serve_app) == 1, "the read of /auth/me")
            before = len(serve_app.requests)
            for user, password in ((USER, ""), ("", PASSWORD)):
                _type_login(dialog, user, password)
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.05)
                assert _text(dialog.query_one("#new-session-error", Static)) == HALF_LOGIN
                assert not dialog.query_one("#create-button", Button).disabled
                assert len(serve_app.requests) == before, "a half login reached the core"
            assert _creates(serve_app) == [] and isinstance(app.screen, NewSessionScreen)

    asyncio.run(check())


def test_reuse_is_disabled_while_a_login_is_typed(serve, wait_until) -> None:
    """A login is for a fresh authentication, so while the fields hold text the reuse choice is greyed with a one-line
    reason and `new` is the choice, even if reuse was the one picked; when the fields are empty it is back."""

    async def check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            reuse = dialog.query_one("#conn-reuse", RadioButton)
            note = dialog.query_one("#connection-note", Static)
            await pilot.click("#conn-reuse")
            await pilot.pause()
            assert reuse.value and not reuse.disabled and _text(note) == ""
            dialog.query_one("#login-user", Input).value = USER
            await pilot.pause()
            assert reuse.disabled and not reuse.value and dialog.query_one("#conn-new", RadioButton).value
            assert dialog.query_one("#connection-id", Input).disabled
            assert _text(note) and "\n" not in _text(note), "one line of reason"
            dialog.query_one("#login-user", Input).value = ""
            await pilot.pause()
            assert not reuse.disabled and _text(note) == ""
            assert dialog.query_one("#connection-choice", RadioSet) is not None

    asyncio.run(check())


def test_no_resolving_credential_focuses_the_username(serve, wait_until) -> None:
    """Empty fields mean "what TSB holds". When it holds nothing the dialog stays open, says so in the operator's
    terms and puts the cursor where the login begins; typing one then opens the session on that same target."""

    async def check() -> None:
        serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=_serve.NOCRED, target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            assert "login" not in _creates(serve_app)[0], "empty fields send no login"
            await wait_until(pilot, lambda: _text(dialog.query_one("#new-session-error", Static)) == NO_LOGIN_HELD,
                             "the dialog's sentence")
            assert app.screen is dialog
            assert app.focused is dialog.query_one("#login-user", Input)
            assert not dialog.query_one("#create-button", Button).disabled
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the second create")
            assert _creates(serve_app)[1]["login"]["user"] == USER and _creates(serve_app)[1]["connection"] == "new"
            await wait_until(pilot, lambda: app.screen is not dialog, "the dialog to go")

    asyncio.run(check())


def test_telnet_targets_show_no_login_fields(serve, wait_until) -> None:
    """A telnet login is typed in the session, so its dialog has no fields to fill; an ssh target's has them."""

    async def check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="telnet-scenario", target_type="telnet")
            assert not dialog.query_one("#login-fields").display
            dialog.query_one("#target-id", Input).value = "normal"
            dialog._types["normal"] = "ssh"
            dialog._show_type()
            await pilot.pause()
            assert dialog.query_one("#login-fields").display
            assert dialog.query_one("#login-user", Input).display and dialog.query_one("#login-password", Input).display

    asyncio.run(check())


def test_the_recorded_login_warning_shows_when_me_says_so(serve, wait_until) -> None:
    """`GET /auth/me` says the deployment records a login: the warning is above the fields once they hold text (not
    before, not for empty fields), and what is sent carries the operator's acknowledgement. With the knob off there
    is no warning and no acknowledgement field (absent means false)."""

    async def check() -> None:
        serve_app, url = serve(ServeApp(login_input=True))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            warning = dialog.query_one("#login-recording-warning", Static)
            await wait_until(pilot, lambda: dialog._recording_login is True, "the read of /auth/me")
            assert not warning.display, "nothing typed, nothing to warn of"
            _type_login(dialog)
            await pilot.pause()
            assert warning.display and _text(warning) == RECORDED
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            assert _creates(serve_app)[-1]["login"]["recording_acknowledged"] is True
            await wait_until(pilot, lambda: app.screen is not dialog, "the dialog to go")

        quiet_app, quiet_url = serve(ServeApp(login_input=False))
        app = TsbTuiApp(quiet_url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: ("GET", "/auth/me") in [(m, p) for m, p, _b in quiet_app.requests],
                             "the read of /auth/me")
            await pilot.pause(0.05)
            _type_login(dialog)
            await pilot.pause()
            assert not dialog.query_one("#login-recording-warning", Static).display
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(quiet_app)), "the create")
            assert "recording_acknowledged" not in _creates(quiet_app)[-1]["login"]

    asyncio.run(check())


def test_a_409_not_acknowledged_is_shown_as_the_core_says_it_and_nothing_is_kept(serve, wait_until) -> None:
    """The knob was turned on after the dialog read `/auth/me`: the create (sent without an acknowledgement, because
    the operator had been shown no warning) is refused 409 and nothing was opened. The dialog says what the core said,
    keeps no copy of what was typed and sends nothing more by itself."""

    async def check() -> None:
        serve_app, url = serve(ServeApp(login_input=False))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: _me_reads(serve_app) == 1, "the read of /auth/me")
            serve_app.login_input = True
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the refused create")
            await wait_until(pilot, lambda: not dialog.query_one("#create-button", Button).disabled,
                             "the refusal to land")
            assert "recording_acknowledged" not in _creates(serve_app)[0]["login"]
            assert "recorded" in _text(dialog.query_one("#new-session-error", Static))
            assert app.screen is dialog and not serve_app.connections, "nothing was opened"
            assert dialog.query_one("#login-user", Input).value == ""
            assert dialog.query_one("#login-password", Input).value == ""
            assert not _attribute_holds(dialog, PASSWORD)
            await pilot.pause(0.05)
            assert len(_creates(serve_app)) == 1, "the dialog sent nothing on its own"

    asyncio.run(check())


def test_create_can_be_clicked_on_a_24_row_window_with_the_login_and_its_warning_showing(serve, wait_until) -> None:
    """The fields and the warning add rows to a dialog that already filled an 80x24 window: Create must stay where a
    click reaches it, or an operator on a small terminal could type a login and not send it."""

    async def check() -> None:
        serve_app, url = serve(ServeApp(login_input=True))
        app = TsbTuiApp(url)
        async with app.run_test(size=(80, 24)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: dialog._recording_login is True, "the read of /auth/me")
            _type_login(dialog)
            await pilot.pause()
            assert dialog.query_one("#login-recording-warning", Static).display
            await pilot.click("#create-button")
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            assert _creates(serve_app)[-1]["login"]["user"] == USER

    asyncio.run(check())


class _GatedServe(ServeApp):
    """The fixture server with two holds a test can lean on: `GET /auth/me` answers only once `me_gate` is set, and
    `GET /targets/{id}` answers 503 (the inventory down) once `read_gate` is set. Both off by default."""

    def __init__(self) -> None:
        super().__init__()
        self.me_gate: threading.Event | None = None
        self.me_started = threading.Event()
        self.read_gate: threading.Event | None = None
        self.read_started = threading.Event()

    def http(self, method, path, headers, body):
        if (method, path) == ("GET", "/auth/me") and self.me_gate is not None:
            self.me_started.set()
            self.me_gate.wait(10)
        if method == "GET" and path.startswith("/targets/") and self.read_gate is not None:
            self.read_started.set()
            self.read_gate.wait(10)
            return 503, [], {"error": {"code": "inventory_unavailable", "message": "the inventory is not answering"}}
        return super().http(method, path, headers, body)


def test_the_me_answer_after_the_dialog_is_gone_does_nothing(serve, wait_until) -> None:
    """Qodo round 1, thread 3. The operator cancels before `GET /auth/me` returns: the late answer must not touch the
    dialog's widgets (it has none left to query), as `_on_target_read` and `_on_read_error` already hold."""

    async def check() -> None:
        serve_app, url = serve(_GatedServe())
        gate = threading.Event()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            app.cfg.set_runtime("size.auto_request", True)
            serve_app.me_gate = gate
            screen._new_session_dialog("normal", "ssh")
            await wait_until(pilot, lambda: serve_app.me_started.is_set(), "the dialog's read of /auth/me")
            dialog = app.screen
            assert isinstance(dialog, NewSessionScreen)
            dialog.query_one("#cancel-button", Button).press()
            await wait_until(pilot, lambda: app.screen is not dialog, "the dialog to go")
            seen: list[BaseException] = []
            try:
                dialog._on_me({"recording": {"login_input": True}})
            except Exception as exc:  # noqa: BLE001 - what the late callback raises is the finding
                seen.append(exc)
            assert not seen, f"the late /auth/me answer touched a dialog that is gone: {seen!r}"
            assert dialog._recording_login is False, "the late answer was acted on"
            gate.set()
            await pilot.pause(0.1)

    asyncio.run(check())


def test_the_create_after_a_failed_target_read_carries_the_typed_login(serve, wait_until) -> None:
    """Qodo round 1, thread 4. The type of a typed id could not be read (the inventory is down): the create that goes
    ahead anyway carries what the operator typed, on a new connection. A silent fall back to the core's own stored
    credential would open the session as another account than the one typed."""

    async def check() -> None:
        serve_app, url = serve(_GatedServe())
        serve_app.read_gate = threading.Event()
        serve_app.read_gate.set()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            dialog.query_one("#target-id", Input).value = "normal"
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the fallback create")
            body = _creates(serve_app)[-1]
            assert body["connection"] == "new"
            assert body.get("login") == {"user": USER, "auth": {"kind": "password", "password": PASSWORD}}, body
            await wait_until(pilot, lambda: app.screen is not dialog, "the dialog to go")
            assert not _attribute_holds(dialog, PASSWORD)

    asyncio.run(check())


def test_the_create_after_a_failed_target_read_refuses_half_a_login(serve, wait_until) -> None:
    """Qodo round 1, thread 4. The fields changed while the type was being read (a login typed whole, one field then
    emptied): the fallback create makes the same half-login check as `_start_create` and sends nothing, instead of
    creating with no login at all."""

    async def check() -> None:
        serve_app, url = serve(_GatedServe())
        serve_app.read_gate = threading.Event()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target=None, target_type=None)
            dialog.query_one("#target-id", Input).value = "normal"
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: serve_app.read_started.is_set(), "the read of the target")
            dialog.query_one("#login-password", Input).value = ""
            serve_app.read_gate.set()
            await wait_until(pilot, lambda: _text(dialog.query_one("#new-session-error", Static)) == HALF_LOGIN,
                             "the half-login refusal")
            assert not dialog.query_one("#create-button", Button).disabled
            await pilot.pause(0.1)
            assert _creates(serve_app) == [] and app.screen is dialog

    asyncio.run(check())
