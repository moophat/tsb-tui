"""New Session — the target (from the Targets tab of the full Fleet view, `--target`, or typed), and the connection
choice Contract §15.3 requires from this first tag. A telnet connection carries one session, so for a telnet target
the dialog asks `new` and the reuse choice is greyed (DEFAULT 22); the type of a target typed in is read from
`GET /targets/{id}` before the create. Every string from a target record is shown as text, never as markup. The recording warning and the host-key
acknowledgement happen right before the attach itself (screens/attach_confirm.py), not here —
every attach passes through there, including a later re-attach from Sessions, so gating on it at
creation time only would miss that case.

An ssh target's dialog also takes a login for this one connection (login-at-connect phase A, rulings A13 and 15:36 (6)):
Username and Password, both empty meaning "use what TSB holds". What is typed goes to the core in the create's `login`
and nowhere else: the dialog keeps no copy, and clears both fields whatever the answer."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, RadioButton, RadioSet, Select, Static
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text

TERM = "xterm-256color"

# error.schema.json: reuse with two or more candidate connections and no connection_id — the
# broker presents the choice and never picks one itself (Contract §12).
CONNECTION_CHOICE_REQUIRED = "connection_choice_required"
# error.schema.json: no stored login resolves for this principal and target (Storage §10.1a); also app.py's.
NO_RESOLVING_CREDENTIAL = "no_resolving_credential"

# A telnet connection carries one session (DEFAULT 22): the worker refuses a reuse, so the dialog never asks one.
TELNET = "telnet"
TELNET_NO_REUSE = "a telnet connection carries one session"
TARGET_REQUIRED = "a target id is required — pick one in the Targets tab of the full Fleet view (^\\ F)"
NO_SUCH_TARGET = "no such target"
# The inventory could not answer for this id (a 503, a transport error): the type is unknown, so the create asks
# `connection: new`, which is safe for every protocol, and says what it could not read.
TYPE_UNKNOWN = "the target's type could not be read — creating a new connection"


# The login fields (login-at-connect phase A). The warning is the fixed sentence of ruling B7; the other three are
# the dialog's own words, except NO_LOGIN_HELD, which is the A13 ruling's.
RECORDED_LOGIN_WARNING = "Your login answers are recorded."
HALF_LOGIN = "Type both a username and a password, or leave both empty."
LOGIN_NEEDS_NEW = "a typed login is for a new connection"
NO_LOGIN_HELD = "TSB holds no login for this target. Type one, or cancel."


# What the dialog says when the create size has no rule: with auto size off nothing in the design says what
# size a new session is created with, and guessing one would be a size request the operator never made
# (terminal-size §4). Loud, and nothing reaches the broker. OPEN-1 was ruled on 2026-10-12, so the default
# profile has auto size on and this is what an operator who turned it off sees.
CREATE_SIZE_OFF = ("auto size is off, so there is no size to create this session with — the size a new "
                   "session carries is this window's, and asking for one is what auto size is "
                   "(terminal-size §4); turn auto size on (--set size.auto_request=true, or ^\\ R) to create")


def create_size(app: Any) -> tuple[int, int] | None:
    """The creator's size (terminal-size §4: session open carries it; size-control step 15), which is the
    canvas the window gives: the window minus the rail and the status row, as an explicit request asks for.
    None when `size.auto_request` is off, because nothing then says what size to carry."""
    if not app.cfg["size.auto_request"]:
        return None
    return app.workspace().fit_size()


class NewSessionScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, target: str | None = None, target_type: str | None = None) -> None:
        super().__init__()
        self._initial_target = target or ""
        # The type of each target id this dialog knows: the one it was opened on (the Targets tab knows it), and
        # each one read from `GET /targets/{id}` before a create on a typed id.
        self._types: dict[str, str] = {target: target_type} if target and target_type is not None else {}
        # The target id the create that raised connection_choice_required was attempted against
        # — the picker's retry (on_select_changed) reuses it rather than re-reading the Input,
        # which the operator may have already changed while the GET /sessions round trip was in
        # flight.
        self._pending_target_id: str | None = None
        # `GET /auth/me`'s `recording.login_input`, read when the dialog opens: whether a login sent now would be
        # recorded. Absent, or unread (an error), is false; the core's own 409 is the backstop.
        self._recording_login = False

    def compose(self) -> ComposeResult:
        with Vertical(id="new-session-panel"):
            yield Static("New session", id="new-session-title")
            yield Static("", id="new-session-target", markup=False)
            yield Input(value=as_text(self._initial_target), placeholder="target id", id="target-id")
            with RadioSet(id="connection-choice"):
                yield RadioButton("New connection (fresh authentication)", value=True, id="conn-new")
                yield RadioButton("Reuse a connection already open to this target", id="conn-reuse")
            yield Static("", id="connection-note", markup=False)
            yield Input(placeholder="connection id (blank: the only candidate, if there is one)",
                       id="connection-id", disabled=True)
            # Populated only on 409 connection_choice_required (_offer_connection_choice below);
            # none preselected (error.schema.json: the broker never picks one itself).
            yield Select[str]([], id="connection-picker",
                             prompt="choose a connection to reuse", disabled=True)
            # Shown for an ssh target only (`_show_type`): a telnet login is typed in the session.
            with Vertical(id="login-fields"):
                yield Static(RECORDED_LOGIN_WARNING, id="login-recording-warning", markup=False)
                # Side by side: the dialog must still show Create on a 24-row window.
                with Horizontal(id="login-row"):
                    yield Input(placeholder="username", id="login-user")
                    yield Input(placeholder="password", password=True, id="login-password")
            yield Static("", id="new-session-error", markup=False)
            yield Button("Create session", id="create-button", variant="primary")
            yield Button("Cancel", id="cancel-button")

    def on_mount(self) -> None:
        self.query_one("#target-id", Input).focus()
        self._show_type()
        self.app.call_api(self.app.client.me, on_done=self._on_me, on_error=self._on_me_error)

    def _on_me(self, me: dict) -> None:
        if self.app.screen is not self:
            return
        self._recording_login = (me.get("recording") or {}).get("login_input") is True
        self._show_type()

    def _on_me_error(self, exc: ApiError | HttpError | OSError) -> None:
        # Not read means no warning; a login that would be recorded is then refused by the core (409) and shown as
        # such. Only a 401 is the operator's to be told, and routes to Login.
        if isinstance(exc, ApiError) and exc.status == 401:
            self.app.notify_api_error(exc)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("target-id", "login-user", "login-password"):
            self._show_type()

    def _typed_login(self) -> bool:
        return bool(self.query_one("#login-user", Input).value or self.query_one("#login-password", Input).value)

    def _clear_login(self) -> None:
        """Both fields empty, and the dialog holds no other copy: the values live in the two inputs and in the one call
        that sends them. `query`, not `query_one`: the dialog may already have been taken away by a 401."""
        for box in self.query("#login-user, #login-password"):
            box.value = ""

    def _is_telnet(self, target_id: str) -> bool:
        return self._types.get(target_id) == TELNET

    def _show_type(self) -> None:
        """F4's first line (the target and its type) and the telnet rule: with a telnet target the reuse choice is
        greyed and says why, and `new` is the choice."""
        target_id = self.query_one("#target-id", Input).value.strip()
        target_type = self._types.get(target_id)
        self.query_one("#new-session-target", Static).update(
            f"{as_text(target_id)} · {as_text(target_type)}" if target_type is not None and target_id else "")
        telnet = self._is_telnet(target_id)
        typed = not telnet and self._typed_login()
        self.query_one("#login-fields").display = not telnet
        self.query_one("#login-recording-warning", Static).display = typed and self._recording_login
        reuse = self.query_one("#conn-reuse", RadioButton)
        reuse.disabled = telnet or typed
        self.query_one("#connection-note", Static).update(
            TELNET_NO_REUSE if telnet else LOGIN_NEEDS_NEW if typed else "")
        if telnet or typed:
            self.query_one("#conn-new", RadioButton).value = True
            self.query_one("#connection-id", Input).disabled = True

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "connection-choice":
            reuse = event.pressed.id == "conn-reuse"
            self.query_one("#connection-id", Input).disabled = not reuse

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-button":
            self._attempt_create()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Back to the workspace: every session open there is as it was."""
        self.app.pop_screen()

    def _attempt_create(self) -> None:
        error = self.query_one("#new-session-error", Static)
        target_id = self.query_one("#target-id", Input).value.strip()
        if not target_id:
            error.update(TARGET_REQUIRED)
            return
        if self._half_login(target_id):
            error.update(HALF_LOGIN)
            return
        error.update("")
        self._pending_target_id = target_id
        self.query_one("#create-button", Button).disabled = True
        if target_id not in self._types:
            self._read_target(target_id)
            return
        self._start_create(target_id)

    def _half_login(self, target_id: str) -> bool:
        """One of the two login fields filled and the other empty, on a target that shows them: the core is never
        asked, because which login was meant would be its guess."""
        if self._is_telnet(target_id):
            return False
        return bool(self.query_one("#login-user", Input).value) != bool(self.query_one("#login-password", Input).value)

    def _login_to_send(self, target_id: str) -> dict | None:
        """The create's `login`, built from the fields as they are now; None for empty fields (what TSB holds) and for
        a telnet target (its login is typed in the session, and the core refuses one here). The acknowledgement is sent
        only when the operator was shown the warning, which is when `_show_type` showed it."""
        user = self.query_one("#login-user", Input).value
        password = self.query_one("#login-password", Input).value
        if self._is_telnet(target_id) or not user or not password:
            return None
        login: dict = {"user": user, "auth": {"kind": "password", "password": password}}
        if self._recording_login:
            login["recording_acknowledged"] = True
        return login

    def _start_create(self, target_id: str) -> None:
        if self._half_login(target_id):
            # The fields changed while the type of a typed id was being read.
            self.query_one("#new-session-error", Static).update(HALF_LOGIN)
            self.query_one("#create-button", Button).disabled = False
            return
        connection = "reuse" if self.query_one("#conn-reuse", RadioButton).value else "new"
        connection_id = self.query_one("#connection-id", Input).value.strip() or None
        if self._is_telnet(target_id):
            connection, connection_id = "new", None  # a telnet connection carries one session (DEFAULT 22)
        if connection_id and connection != "reuse":
            connection_id = None
        login = self._login_to_send(target_id)
        if login is not None:
            connection, connection_id = "new", None  # a login is for a fresh authentication
        self._create(target_id, connection, connection_id, login)

    def _read_target(self, target_id: str) -> None:
        """A typed id has no record behind it here: read it (`GET /targets/{id}`) so the telnet rule applies."""
        self.app.call_api(self.app.client.get_target, target_id,
                          on_done=lambda record: self._on_target_read(target_id, record),
                          on_error=self._on_read_error)

    def _on_target_read(self, target_id: str, record: dict) -> None:
        if self.app.screen is not self:
            return
        self._types[target_id] = str(record.get("type", ""))
        self._show_type()
        self._start_create(target_id)

    def _on_read_error(self, exc: ApiError | HttpError | OSError) -> None:
        if self.app.screen is not self:
            self.app.notify_api_error(exc)
            return
        if isinstance(exc, ApiError) and exc.status == 404:
            self.query_one("#create-button", Button).disabled = False
            self.query_one("#new-session-error", Static).update(NO_SUCH_TARGET)
            return
        if isinstance(exc, ApiError) and exc.status == 401:
            self.query_one("#create-button", Button).disabled = False
            self.app.notify_api_error(exc)
            return
        # Anything else (a 503, a transport error) means the type could not be read, not that the target is
        # absent: create anyway, asking `connection: new`, which is safe whichever protocol it turns out to be.
        # The fallback create is one create: Create stays greyed from the moment it starts until it ends
        # (`_on_create_error` re-enables it, `_on_create_done` takes the dialog away), so a second click
        # meanwhile starts nothing.
        # It carries what the operator typed, as `_start_create` does, and makes the same check of it: never a silent
        # fall back to the credential the core holds. The core refuses a login on a target that is not ssh.
        target_id = self._pending_target_id
        if target_id is None:
            self.query_one("#new-session-error", Static).update(TYPE_UNKNOWN)
            self.query_one("#create-button", Button).disabled = False
            return
        if self._half_login(target_id):
            self.query_one("#new-session-error", Static).update(HALF_LOGIN)
            self.query_one("#create-button", Button).disabled = False
            return
        self.query_one("#new-session-error", Static).update(TYPE_UNKNOWN)
        self._create(target_id, "new", None, self._login_to_send(target_id))

    def _create_size(self) -> tuple[int, int] | None:
        return create_size(self.app)

    def _create(self, target_id: str, connection: str, connection_id: str | None, login: dict | None = None) -> None:
        size = self._create_size()
        if size is None:
            self.query_one("#new-session-error", Static).update(CREATE_SIZE_OFF)
            self.query_one("#create-button", Button).disabled = False
            return
        cols, rows = size
        self.app.call_api(
            self.app.client.create_session, target_id, connection, TERM, cols, rows, connection_id, login,
            on_done=self._on_create_done, on_error=self._on_create_error,
        )

    def _on_create_done(self, created: dict) -> None:
        from .attach_confirm import open_attach

        self._clear_login()
        # A completion that arrives after its dialog has gone — a 401 routed to Login while the request was in
        # flight (`notify_api_error` → `_to_login`, which pops to the base) — is discarded: nothing is popped, so
        # the login stack stays as it is, and nothing is opened through a workspace that no longer exists. The
        # session the create made lives on the broker as a detached session, and the operator attaches to it
        # after logging in (the ruling `plans/reports/rulings-261016-tui-reattach-login.md` item 3: nothing
        # reattaches on its own across a logout).
        if self.app.screen is not self or self.app.workspace() is None:
            return
        # The dialog goes first, then the new session opens in the workspace under it, beside the others.
        self.app.pop_screen()
        open_attach(self.app, created["session"], created.get("hostkey"))

    def _on_create_error(self, exc: ApiError | HttpError | OSError) -> None:
        # Local widget updates first: `notify_api_error` may switch this screen away (a 401
        # routes to Login), and this screen should not still be queried after that happens.
        self._clear_login()
        self.query_one("#create-button", Button).disabled = False
        if (isinstance(exc, ApiError) and exc.code == NO_RESOLVING_CREDENTIAL
                and self.query_one("#login-fields").display):
            # Empty fields meant "what TSB holds", and it holds nothing (ruling A13): the dialog stays open and says
            # what to do, with the cursor where the login begins. The toast `notify_api_error` raises is unchanged.
            self.query_one("#new-session-error", Static).update(NO_LOGIN_HELD)
            self.query_one("#login-user", Input).focus()
            self.app.notify_api_error(exc)
            return
        if isinstance(exc, ApiError) and exc.code == CONNECTION_CHOICE_REQUIRED:
            self._offer_connection_choice()
            return
        if isinstance(exc, ApiError):
            self.query_one("#new-session-error", Static).update(exc.message)
        self.app.notify_api_error(exc)

    def _offer_connection_choice(self) -> None:
        """error.schema.json's own remedy for connection_choice_required: `GET /sessions` lists
        each session's connection_id. Offer the distinct ones this principal holds to this
        target — never one this principal cannot see, and never the broker's own guess."""
        self.query_one("#new-session-error", Static).update(
            "several connections could be reused — choose one below")
        self.app.call_api(self.app.client.sessions,
                          on_done=self._on_choice_sessions, on_error=self._on_create_error)

    def _on_choice_sessions(self, sessions: list[dict]) -> None:
        target_id = self._pending_target_id
        candidates = list(dict.fromkeys(
            session["connection_id"] for session in sessions
            if session["target_id"] == target_id and session["owner"] == self.app.username
        ))
        picker = self.query_one("#connection-picker", Select)
        if not candidates:
            # Between the 409 and this GET /sessions landing, every one of this principal's own
            # connections to the target closed — nothing left to offer; the operator can only
            # retry (a fresh "new" connection, or reuse if one reopens).
            self.query_one("#new-session-error", Static).update(
                "no connection of this principal's own to this target is listed anymore — retry")
            return
        picker.set_options([(connection_id, connection_id) for connection_id in candidates])
        picker.disabled = False
        picker.focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "connection-picker" or event.value is Select.NULL:
            return
        target_id = self._pending_target_id
        if target_id is None:
            return
        event.select.disabled = True
        self.query_one("#new-session-error", Static).update("")
        self.query_one("#create-button", Button).disabled = True
        self._create(target_id, "reuse", str(event.value))
