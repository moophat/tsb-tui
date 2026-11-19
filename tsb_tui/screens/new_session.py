"""New Session — the target (from the Targets tab of the full Fleet view, `--target`, or typed), and the connection
choice Contract §15.3 requires from this first tag. A telnet connection carries one session, so for a telnet target
the dialog asks `new` and the reuse choice is greyed (DEFAULT 22); the type of a target typed in is read from
`GET /targets/{id}` before the create. Every string from a target record is shown as text, never as markup. The recording warning and the host-key
acknowledgement happen right before the attach itself (screens/attach_confirm.py), not here —
every attach passes through there, including a later re-attach from Sessions, so gating on it at
creation time only would miss that case.

An ssh target's dialog also takes a login for this one connection (login-at-connect phase A, rulings A13 and 15:36 (6)):
Username and Password, both empty meaning "use what TSB holds". What is typed goes to the core in the create's `login`
and nowhere else unless Remember was chosen: the completion holds the submitted login for that save,
and the dialog clears both fields whatever the answer."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
)
from tsb_contract_client import ApiError

from .._transport import HttpError
from ..model.targets import as_text

TERM = "xterm-256color"

# error.schema.json: reuse with two or more candidate connections and no connection_id — the
# broker presents the choice and never picks one itself (Contract §12).
CONNECTION_CHOICE_REQUIRED = "connection_choice_required"
# error.schema.json: no stored login resolves for this principal and target (Storage §10.1a); also app.py's.
NO_RESOLVING_CREDENTIAL = "no_resolving_credential"
# error.schema.json, from the vault release: the credential needs this login's unlock (ask the passphrase and repeat once), or
# a user-unlocked org credential's storing administrator holds none (say so; the operator's passphrase cannot help).
VAULT_LOCKED = "vault_locked"
ORG_CREDENTIAL_LOCKED = "org_credential_locked"
# error.schema.json, from the vault release: two or more stored credentials resolve, and the core picks none (Contract §12).
# The body's `candidates` are each a credential_ref with its credential_layer; the operator's pick is sent back with the create.
CREDENTIAL_CHOICE_REQUIRED = "credential_choice_required"
LAYER_WORDS = {"personal": "yours", "org": "granted"}
CHOICE_TITLE = "More than one saved login fits this target. Choose the one to connect with:"
ORG_LOCKED_NOTE = "The administrator who stored it must unlock; your passphrase cannot help."

# A telnet connection carries one session (DEFAULT 22): the worker refuses a reuse, so the dialog never asks one.
TELNET = "telnet"
TELNET_NO_REUSE = "a telnet connection carries one session"
TARGET_REQUIRED = "a target id is required — pick one in the Targets tab of the full view (Ctrl+\\ F)"
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


def credential_candidates(detail: dict) -> list[tuple[str, str]] | None:
    """The `candidates` of a `credential_choice_required` body as (reference, layer) pairs, in the order the core gave
    them, or None when the list is missing or is not what error.schema.json allows (two to 64 entries, each an object with a
    text `credential_ref` and a `credential_layer` this surface can name). Nothing is repaired or dropped: a list that cannot
    be offered whole is offered not at all, because the choice is the operator's and a partial list is a guess."""
    raw = detail.get("candidates")
    if not isinstance(raw, list) or not 2 <= len(raw) <= 64:
        return None
    found: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        ref, layer = entry.get("credential_ref"), entry.get("credential_layer")
        if not isinstance(ref, str) or not ref or layer not in LAYER_WORDS:
            return None
        found.append((ref, layer))
    return found


def create_size(app: Any) -> tuple[int, int]:
    """The creator's size (terminal-size §4: session open carries it; size-control step 15), which is the
    canvas the window gives: the window minus the rail and the status row, as an explicit request asks for.
    Auto size controls later window changes, not the size carried when opening a session."""
    return app.workspace().fit_size()


class NewSessionScreen(Screen):
    DEFAULT_CSS = """
    NewSessionScreen #new-session-panel { height: auto; max-height: 100%; }
    NewSessionScreen #create-button, NewSessionScreen #cancel-button, NewSessionScreen #device-login-start {
        height: 1; min-height: 1; border: none; margin: 0; padding: 0 1;
    }
    NewSessionScreen #login-remember { height: 1; border: none; padding: 0; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, target: str | None = None, target_type: str | None = None, *, refusal: str = "") -> None:
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
        # The arguments of the create last sent, and whether this attempt already asked the passphrase: a create answered
        # `vault_locked` unlocks and repeats once, never more (phase 12 § B). A create that carried a typed login is never
        # kept (the dialog holds no copy of it, and the core never answers it `vault_locked`: a login resolves no credential).
        # The credential choice the operator picked is part of what is repeated; the candidates on offer and the counter
        # that keeps each offering's button ids its own (a removed button's id is free only once its removal is done).
        self._last_create: tuple[str, str, str | None, tuple[str, str] | None] | None = None
        self._unlock_tried = False
        self._candidates: list[tuple[str, str]] = []
        self._offers = 0
        self.me_ready = False
        self._refusal = refusal

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
            # Filled on 409 credential_choice_required only (_offer_credential_choice): the core's candidates as plain
            # text, none pressed, and a press is the pick.
            yield Static(CHOICE_TITLE, id="credential-choice-title", markup=False)
            yield RadioSet(id="credential-choice")
            # Shown for an ssh target only (`_show_type`): a telnet login is typed in the session.
            with Vertical(id="login-fields"):
                yield Static(RECORDED_LOGIN_WARNING, id="login-recording-warning", markup=False)
                # Side by side: the dialog must still show Create on a 24-row window.
                with Horizontal(id="login-row"):
                    yield Input(placeholder="username", id="login-user")
                    yield Input(placeholder="password", password=True, id="login-password")
                yield Checkbox("Remember", id="login-remember")
            yield Button("Type the login in the terminal", id="device-login-start")
            yield Static(self._refusal, id="new-session-error", markup=False)
            yield Button("New connection" if self._refusal else "Create session", id="create-button", variant="primary")
            yield Button("Cancel", id="cancel-button")

    def on_mount(self) -> None:
        self._login_inputs = tuple(self.query("#login-user, #login-password"))
        self._hide_credential_choice()
        self.query_one("#target-id", Input).focus()
        self._show_type()
        self.app.call_api(self.app.client.me, on_done=self._on_me, on_error=self._on_me_error)

    def _on_me(self, me: dict) -> None:
        self.app.screen_lock.apply(me.get("locks"))      # the lock settings are re-read whenever the TUI reads /auth/me
        if self.app.screen is not self:
            return
        self._recording_login = (me.get("recording") or {}).get("login_input") is True
        self.me_ready = True
        self._show_type()

    def _on_me_error(self, exc: ApiError | HttpError | OSError) -> None:
        # Not read means no warning; a login that would be recorded is then refused by the core (409) and shown as
        # such. Only a 401 is the operator's to be told, and routes to Login.
        if isinstance(exc, ApiError) and exc.status == 401:
            self.app.notify_api_error(exc)
        self.me_ready = True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("target-id", "login-user", "login-password"):
            self._show_type()

    def _typed_login(self) -> bool:
        return bool(self.query_one("#login-user", Input).value or self.query_one("#login-password", Input).value)

    def _clear_login(self) -> None:
        """Both fields empty, and the dialog holds no other copy: the values live in the two inputs and in the one call
        that sends them. `query`, not `query_one`: the dialog may already have been taken away by a 401."""
        for box in getattr(self, "_login_inputs", self.query("#login-user, #login-password")):
            box.value = ""

    def on_unmount(self) -> None:
        self._clear_login()
        self._login_inputs = ()

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
        self.query_one("#device-login-start").display = (
            target_type == "ssh" and getattr(self.app, "device_login_unavailable", None) is not self.app.client)
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
        elif event.radio_set.id == "credential-choice":
            event.stop()
            self._pick_credential(event.radio_set.pressed_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-button":
            self._attempt_create()
        elif event.button.id == "cancel-button":
            self.action_cancel()
        elif event.button.id == "device-login-start":
            if not self.me_ready or self.app.screen_lock.locked:
                return
            target = self.query_one("#target-id", Input).value.strip()
            size = self._create_size()
            if self._types.get(target) != "ssh":
                self.query_one("#new-session-error", Static).update("Choose an SSH target to answer login prompts.")
                return
            self._clear_login()
            self.app.pop_screen()
            self.app.workspace().open_device_login(target, TERM, size, recording_login=self._recording_login)

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
        self._hide_credential_choice()
        self._pending_target_id = target_id
        self._unlock_tried = False
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

    def _create_size(self) -> tuple[int, int]:
        return create_size(self.app)

    def _create(self, target_id: str, connection: str, connection_id: str | None, login: dict | None = None,
                credential: tuple[str, str] | None = None) -> None:
        cols, rows = self._create_size()
        self._last_create = (target_id, connection, connection_id, credential) if login is None else None
        remember = self.query_one("#login-remember", Checkbox).value
        self.app.call_api(
            self.app.client.create_session, target_id, connection, TERM, cols, rows, connection_id, login, credential,
            on_done=lambda created: self._on_create_done(created, login if remember else None),
            on_error=self._on_create_error,
        )

    def _on_create_done(self, created: dict, login: dict | None = None) -> None:
        if self.app.screen_lock.locked:
            self.app.screen_lock.after_unlock(lambda: self._on_create_done(created, login))
            return
        if self.app.screen is self and login is not None:
            self.app.run_worker(self._save_created(
                created, login["user"], login["auth"]["password"]), exclusive=False)
            return
        self._finish_create_done(created)

    async def _save_created(self, created: dict, user: str, password: str) -> None:
        from .. import vault_flow
        from .device_login import proposed_reference, save_message

        app = self.app
        workspace = app.workspace()
        client, generation = app.client, app._login_gen
        target = created["session"]["target_id"]
        message = ""

        def show_saved(slot):
            if message and app.workspace() is workspace and app.client is client and app._login_gen == generation:
                workspace.set_login_status(slot, message)

        try:
            saving = vault_flow.save_login(app, target, user, password=password,
                                           reference=proposed_reference(target, user))
            self._finish_create_done(created, then=show_saved)
            outcome = await saving
            message = save_message(outcome, target)
            if app.workspace() is workspace and workspace is not None:
                slot = workspace.slot_for(created["session"]["id"])
                if slot is not None:
                    show_saved(slot)
        finally:
            password = ""

    def _finish_create_done(self, created: dict, *, then=None) -> None:
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
        self.app.workspace().open_session(created["session"], created.get("hostkey"), then=then)

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
        if (isinstance(exc, ApiError) and exc.code == CREDENTIAL_CHOICE_REQUIRED
                and self._offer_credential_choice(exc)):
            return
        if isinstance(exc, ApiError) and exc.code == VAULT_LOCKED and not self._unlock_tried:
            self._unlock_tried = True
            self.query_one("#create-button", Button).disabled = True
            self.app.run_worker(self._unlock_then_repeat(), exclusive=False)
            return
        if isinstance(exc, ApiError) and exc.code == ORG_CREDENTIAL_LOCKED:
            self.query_one("#new-session-error", Static).update(f"{exc.message}. {ORG_LOCKED_NOTE}")
            return
        if isinstance(exc, ApiError):
            self.query_one("#new-session-error", Static).update(exc.message)
        self.app.notify_api_error(exc)

    async def _unlock_then_repeat(self) -> None:
        """The core answered the create `vault_locked`: ask the passphrase, unlock in untrusted mode and repeat the
        create once with what was sent before (phase 12 § B)."""
        from ..vault_flow import unlock

        result = await unlock(self.app)
        if not self.is_attached:
            return
        if result.status == "unlocked" and self._last_create is not None:
            target_id, connection, connection_id, credential = self._last_create
            self._create(target_id, connection, connection_id, None, credential)
            return
        self.query_one("#create-button", Button).disabled = False
        self.query_one("#new-session-error", Static).update(
            "The vault was not unlocked, so no session was created." if result.status == "cancelled"
            else result.message)

    def _hide_credential_choice(self) -> None:
        self._candidates = []
        for widget in self.query("#credential-choice-title, #credential-choice"):
            widget.display = False

    def _offer_credential_choice(self, exc: ApiError) -> bool:
        """The core found two or more stored logins for this target and picked none (Contract §12): show them as text,
        "yours" for the principal's own and "granted" for an org credential, press none for the operator and send nothing
        until one is pressed. False when there is nothing to offer (a list that is missing or malformed, or a create
        that carried a typed login, which the core never answers this way): the caller then shows the core's own message
        as it does for any refusal. The list is shown in the order the core gave it."""
        candidates = credential_candidates(exc.detail)
        if candidates is None or self._last_create is None:
            return False
        self._candidates = candidates
        self._offers += 1
        choice = self.query_one("#credential-choice", RadioSet)
        choice.remove_children()
        choice.mount(*(
            RadioButton(Content(f"{as_text(ref)} · {LAYER_WORDS[layer]}"), id=f"credential-{self._offers}-{index}")
            for index, (ref, layer) in enumerate(candidates)))
        self.query_one("#credential-choice-title", Static).display = True
        choice.display = True
        choice.focus()
        return True

    def _pick_credential(self, index: int | None) -> None:
        """The operator pressed one candidate: repeat the create as it was, with that credential_ref and credential_layer."""
        if index is None or not 0 <= index < len(self._candidates) or self._last_create is None:
            return
        target_id, connection, connection_id, _ = self._last_create
        choice = self._candidates[index]
        self._hide_credential_choice()
        self.query_one("#new-session-error", Static).update("")
        self.query_one("#create-button", Button).disabled = True
        self._unlock_tried = False
        self._create(target_id, connection, connection_id, None, choice)

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
