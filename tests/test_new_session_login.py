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
from tsbc import _serve
from tsbc._serve import ServeApp
from vault_support import VaultServe
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.attach_confirm import AttachConfirmScreen
from tsb_tui.screens.new_session import NewSessionScreen

USER = "fixture-account"
PASSWORD = "FIXTURE-NOT-A-PASSWORD-typed"
# What the dialog says; the tests read the rendered text, so a reworded sentence is a deliberate change of both.
NO_LOGIN_HELD = "No saved login fits this target · type the username and password · Enter tries again"
HALF_LOGIN = "Type both a username and a password, or leave both empty."
RECORDED = "Your login answers are recorded."


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


def _me_reads(serve_app) -> int:
    return len([1 for method, path, _body in serve_app.requests if (method, path) == ("GET", "/auth/me")])


ACTIVITY = ("POST", "/auth/activity")


def _half_login_calls(requests) -> list[tuple[str, str]]:
    """The requests a half login can cause: the create (POST /sessions) and the target read (GET /targets,
    GET /targets/{id}). Named by method and path so a failure says which request leaked; anything on the app's own
    timers (the fleet poll, the activity signal) is not what a press sends, so it is not counted."""
    return [(method, path) for method, path, _body in requests
            if (method, path) == ("POST", "/sessions")
            or (method == "GET" and (path == "/targets" or path.startswith("/targets/")))]


def _core_calls(requests) -> list[tuple[str, str, object]]:
    """The requests a "nothing reached the core" check counts: everything the TUI sends the core except the screen
    lock's user-activity signal (phase 12 § G). That signal is fired by user input, at most once per served interval,
    and is not part of any operation these tests are about, so a keypress inside a counted window can add it by timing.
    The standing GET /alerts stream is also excluded; it can start inside the window independently of the press.
    A create, a target read, a `/auth/me`: all still count, so the check still fails when the operation under test is
    sent. Takes the request log (`serve_app.requests`) or a slice of it, so a test can count only its own window."""
    return [r for r in requests if (r[0], r[1]) not in (ACTIVITY, ("GET", "/alerts"))]


def _text(widget: Static) -> str:
    return str(widget.render())


async def _open_dialog(app, pilot, wait_until, *, target: str | None, target_type: str | None) -> NewSessionScreen:
    screen = await fixture_login(app, pilot)
    app.cfg.set_runtime("size.auto_request", True)
    screen._new_session_dialog(target, target_type)
    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted
                     and bool(app.screen.query("#create-button")), "the New session dialog")
    return app.screen


async def _gate_up(app, pilot, wait_until) -> None:
    """A create that succeeds ends in the attach gate, which is pushed after the dialog goes and focuses its own widget
    in `on_mount`. A test that ends before that has the app torn down mid-mount (issue 469), so a test that sees a
    create succeed ends here: the gate is the active screen and its focus target (the host key tick when one is
    shown, Attach otherwise) exists and holds the focus."""

    def ready() -> bool:
        gate = app.screen
        if not (isinstance(gate, AttachConfirmScreen) and gate.is_mounted):
            return False
        target = gate.query("#hostkey-ack") or gate.query("#attach-button")
        return bool(target) and app.focused is target.first()

    await wait_until(pilot, ready, "the attach gate to be up and focused")


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
            await _gate_up(app, pilot, wait_until)
            assert user_input.value == "" and password_input.value == ""
            for holder in (dialog, user_input, password_input):
                assert not _attribute_holds(holder, PASSWORD), f"{type(holder).__name__} still holds the password"

    asyncio.run(check())


class _ListStub(SlowChunkedApp):
    """`SlowChunkedApp` with a `GET /targets` list route, so the New session screen has a row to highlight."""

    def http(self, method, path, headers, body):
        if method == "GET" and path.split("?", 1)[0] == "/targets":
            record = {"id": "normal", "type": "ssh", "name": "normal", "group": "stub", "tags": [],
                      "discovered_by": "manual", "last_seen": "2026-09-23T12:00:00Z", "host": "192.0.2.20", "port": 22}
            return 200, [], {"targets": [record]}
        return super().http(method, path, headers, body)


def test_a_refused_login_also_clears_the_fields_and_keeps_the_dialog_open(wait_until) -> None:
    """Whatever the answer: a 502 target_auth_failed ends in the same empty fields, with the core's message shown and
    no copy kept for a second try (the operator types it again)."""

    async def check() -> None:
        stub = _ListStub(create_error=(502, "target_auth_failed", "the target refused the login"))
        with StubServer(stub) as url:
            app = TsbTuiApp(url)
            async with app.run_test(size=(120, 40)) as pilot:
                dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
                await wait_until(pilot, lambda: app.workspace().targets.row("normal") is not None, "the target row")
                _type_login(dialog)
                dialog.query_one("#create-button", Button).press()
                await wait_until(pilot, lambda: bool(stub.creates), "the create")
                await wait_until(pilot, lambda: not dialog.query_one("#create-button", Button).disabled,
                                 "the refusal to land")
                assert stub.creates[-1]["login"]["auth"]["password"] == PASSWORD
                assert app.screen is dialog
                assert dialog.query_one("#login-user", Input).value == ""
                assert dialog.query_one("#login-password", Input).value == ""
                assert _text(dialog.query_one("#new-session-core", Static)) == "the target refused the login"
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
            await wait_until(pilot, lambda: _me_reads(serve_app) == 2, "the read of /auth/me")
            before = len(_half_login_calls(serve_app.requests))
            for user, password in ((USER, ""), ("", PASSWORD)):
                _type_login(dialog, user, password)
                dialog.query_one("#create-button", Button).press()
                await pilot.pause(0.05)
                assert _text(dialog.query_one("#new-session-error", Static)) == HALF_LOGIN
                assert not dialog.query_one("#create-button", Button).disabled
                leaked = _half_login_calls(serve_app.requests)
                assert len(leaked) == before, f"a half login reached the core: {leaked[before:]}"
            assert _creates(serve_app) == [] and isinstance(app.screen, NewSessionScreen)

    asyncio.run(check())


def test_the_activity_signal_in_a_counted_window_is_not_what_nothing_reached_the_core_counts(
    serve, wait_until
) -> None:
    """The flake (#497) and the #498 Qodo finding: the screen lock's user-activity signal is fired by user input, so a
    keypress inside a counted window can add it by timing. Here the served `activity_interval` is 0, so every keypress
    sends one. The baseline is the raw index into the request log taken right before the press, so the window is only
    what the press sends: over that window the activity signal must arrive (a keypress fired it), the "nothing reached
    the core" check (`_core_calls` of the window) is empty because the signal is not an operation under test, and a
    create inside the same window makes that check non-empty. A baseline taken from the filtered count, reading
    cumulative history, would let an activity request from before the press satisfy the window."""

    async def check() -> None:
        serve_app, url = serve(VaultServe(locks={"activity_interval": 0, "screen_lock_after": 100000}))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            start = len(serve_app.requests)       # the raw index right before the press: the window is what it sends
            await pilot.press("tab")              # a real keypress: the activity signal fires on it
            await wait_until(pilot, lambda: any((m, p) == ACTIVITY for m, p, _b in serve_app.requests[start:]),
                             "the activity signal inside the window")
            window = serve_app.requests[start:]
            # Deterministically cover a standing alert-stream request landing inside this window.
            window = [*window, ("GET", "/alerts", None)]
            assert any((m, p) == ACTIVITY for m, p, _b in window), "the press sent no activity signal inside the window"
            assert _core_calls(window) == [], "an operation reached the core in the window"
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            assert _core_calls(serve_app.requests[start:]) != [], "a create inside the window was not counted"

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
            # The focus moves to the username field after the sentence lands; read it once the field holds the keys, so
            # the assertion does not catch the focus still on the target field.
            await wait_until(pilot, lambda: app.focused is dialog.query_one("#login-user", Input),
                             "the focus on the username field", 30.0)
            assert app.focused is dialog.query_one("#login-user", Input)
            assert not dialog.query_one("#create-button", Button).disabled
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the second create")
            assert _creates(serve_app)[1]["login"]["user"] == USER and _creates(serve_app)[1]["connection"] == "new"
            await _gate_up(app, pilot, wait_until)

    asyncio.run(check())


def test_telnet_targets_show_no_login_fields(serve, wait_until) -> None:
    """A telnet login is typed in the session, so its dialog has no fields to fill; an ssh target's has them."""

    async def check() -> None:
        _serve_app, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="telnet-scenario", target_type="telnet")
            assert not dialog.query_one("#login-fields").display
            dialog.query_one("#new-session-filter", Input).value = "normal"
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
            await _gate_up(app, pilot, wait_until)

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
            await _gate_up(app, pilot, wait_until)

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
            await wait_until(pilot, lambda: _me_reads(serve_app) == 2, "the read of /auth/me")
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
            await _gate_up(app, pilot, wait_until)

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


def test_the_create_carries_the_typed_login_on_a_selected_row(serve, wait_until) -> None:
    """Qodo round 1, thread 4, on the search path. A typed login on a selected ssh row goes ahead on a new
    connection, carrying what the operator typed. A silent fall back to the core's own stored credential would open the
    session as another account than the one typed."""

    async def check() -> None:
        serve_app, url = serve(_GatedServe())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            _type_login(dialog)
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: bool(_creates(serve_app)), "the create")
            body = _creates(serve_app)[-1]
            assert body["connection"] == "new"
            assert body.get("login") == {"user": USER, "auth": {"kind": "password", "password": PASSWORD}}, body
            await _gate_up(app, pilot, wait_until)
            assert not _attribute_holds(dialog, PASSWORD)

    asyncio.run(check())


def test_the_create_refuses_half_a_login_on_a_selected_row(serve, wait_until) -> None:
    """Qodo round 1, thread 4, on the search path. The fields changed to half a login (one field then emptied):
    the create makes the same half-login check as `_start_create` and sends nothing, instead of creating with no
    login at all."""

    async def check() -> None:
        serve_app, url = serve(_GatedServe())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            _type_login(dialog)
            dialog.query_one("#login-password", Input).value = ""
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: _text(dialog.query_one("#new-session-error", Static)) == HALF_LOGIN,
                             "the half-login refusal")
            assert not dialog.query_one("#create-button", Button).disabled
            await pilot.pause(0.1)
            assert _creates(serve_app) == [] and app.screen is dialog

    asyncio.run(check())
