"""New session — a searchable list of the loaded inventory, the add-target path, and the choices the contract asks for.

`n` opens it on the pages the workspace already loaded (rule 2, P3): typing filters the rows by literal name, host
text, group, tags and label — nothing is resolved, nothing new is asked of the broker. `⏎` on a row creates at once
when there is one way to open it; a choice shows only where the contract asks for one: a live connection of this
login (Contract §15.3), a `credential_choice_required` or `connection_choice_required` answer, or a gate (the
recording notice, a first-use host key, a typed login). A target you have a session on gets an ask: `go to
session N` first and highlighted, then `another session` and `new connection`. A no-match query shows the Add row
for a login with `target.edit`, or the ruled words for one without; the form opens with the typed text in the
address field when it reads as an IP address or a host name, nothing else inferred, and a successful save returns
to this screen with the saved record selected — the create stays the operator's press.

Every refusal is one ruled line per code (the table below): what happened, why, and one recovery key; nothing
retries by itself. The retired `no such target` and `a target id is required` are gone: a text that matches no row
is guidance, and the screen never asks the broker for `GET /targets/{id}`.

The login fields (login-at-connect phase A): Username and Password, both empty meaning "use what TSB holds". What
is typed goes to the core in the create's `login` and nowhere else unless Remember was chosen; the dialog clears
both fields whatever the answer. The recording warning and the host-key acknowledgement happen at the attach gate
(screens/attach_confirm.py), not here.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import (
    Horizontal,
    Vertical,
)
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
from ..widgets.sidebar import _target_facts, _target_label, target_title

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

# The ruled refusal table (P4 L1-open-refusals): one line per code — what happened, why, and the person's own
# recovery key; nothing retries by itself. `<label>` is the row's label.
REFUSAL_COPY = {
    "target_connect_failed": "Could not connect to {label} · the target did not accept the connection · r tries again",
    "target_auth_failed": "The target refused the login · check the username and password · Enter tries again",
    "hostkey_changed": "The host key changed for {label} · nothing was opened · Esc closes",
    NO_RESOLVING_CREDENTIAL: "No saved login fits this target · type the username and password · Enter tries again",
    VAULT_LOCKED: "Your vault is locked · type your passphrase · Enter unlocks and repeats once",
    ORG_CREDENTIAL_LOCKED: "The admin who holds that saved login has not unlocked it · your passphrase cannot help "
                           "· ask the admin · Esc closes",
    "mapped_credential_missing": "The saved login this target maps to is gone · pick another, or type a username "
                                 "and password · Enter tries again",
    "device_not_trusted": "This device is not trusted for that login · nothing was opened · Esc closes",
    # After a pick from the list, a 404 means the target left the caller's view; the no-match wording is for a query.
    "target_not_found": "{label} is no longer in your list · r refreshes the list",
    "inventory_unavailable": "The broker's inventory is unavailable · r retries when it answers",
}

# The ruled recovery codes whose key is `r`: the person's own press repeats the create (the target did not accept
# it) or refetches the list (the target left the view, the inventory is down). Nothing retries by itself. When one
# of these refusals shows, the keyboard leaves the filter to the Create button so `r` reaches the refusal, not typing.
R_RECOVERY = ("target_connect_failed", "target_not_found", "inventory_unavailable")

# A telnet connection carries one session (DEFAULT 22): the worker refuses a reuse, so the dialog never asks one.
TELNET = "telnet"
TELNET_NO_REUSE = "a telnet connection carries one session"
TARGET_REQUIRED = "pick a target from the list, or type text to search — the full view is Ctrl+\\ F"

# The add-target continuation: after a save the screen watches for the saved row, bounded to 5 seconds. If the
# list did not refresh by then, it shows the Targets tab's own inventory-down state (the plan's existing copy) and
# keeps the saved id, so a later successful list selects it. No endless timer, no new copy.
PENDING_WATCH_TICKS = 50  # 50 ticks of the 0.1 s watch is 5 seconds after the save
TARGETS_UNAVAILABLE_COPY = "Could not load targets · r retry"

# The no-match guidance, ruled in P4 (the same words for a login without target.edit).
NO_MATCH_ADD = "+ Add '{query}' as a target"
NO_MATCH_NO_RIGHTS = "No target matches '{query}' · ask an admin to add it · Esc clears"

# The ask for a target you have a session on: the session you have, this window's number, then the two opens.
GO_TO_SESSION = "go to session {number}"
ANOTHER_SESSION = "another session"
NEW_CONNECTION = "new connection"

# The login fields (login-at-connect phase A). The warning is the fixed sentence of ruling B7; the other three are
# the dialog's own words.
RECORDED_LOGIN_WARNING = "Your login answers are recorded."
HALF_LOGIN = "Type both a username and a password, or leave both empty."
LOGIN_NEEDS_NEW = "a typed login is for a new connection"

# An address reads as an IP address or a host name: the literal forms only, no lookup. The text goes to the form's
# address field as the operator typed it; single words are names, not addresses.
_IPV4 = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
_IPV6 = re.compile(r"[0-9a-fA-F:]+")
_HOSTNAME = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


def reads_as_address(text: str) -> bool:
    """True for a typed text that is an IP address (v4, or v6 with at least one colon) or a dotted host name."""
    if not text:
        return False
    if _IPV4.fullmatch(text):
        return all(0 <= int(part) <= 255 for part in text.split("."))
    if ":" in text and _IPV6.fullmatch(text):
        return True
    return bool(_HOSTNAME.fullmatch(text))


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
    NewSessionScreen #target-rows, NewSessionScreen #no-match, NewSessionScreen #ask-lines { height: auto; }
    NewSessionScreen #new-session-error { height: auto; width: 1fr; }
    NewSessionScreen #new-session-core { height: auto; width: 1fr; }
    NewSessionScreen #reuse-box { height: auto; }
        NewSessionScreen #target-rows { max-height: 3; }
    NewSessionScreen #create-button, NewSessionScreen #cancel-button, NewSessionScreen #device-login-start {
        height: 1; min-height: 1; border: none; margin: 0; padding: 0 1;
    }
    NewSessionScreen #login-remember { height: 1; border: none; padding: 0; }
    # The dialog must fit every control inside a 24-row window (L1-focus/geometry): the painted target window is
    # 3 rows, and the filter, the two choice RadioSets and the login row are compacted to their content height.
    NewSessionScreen #new-session-filter { height: 1; border: none; }
    NewSessionScreen #connection-choice, NewSessionScreen #credential-choice { height: auto; border: none; padding: 0; }
    NewSessionScreen #login-row > Input { height: 1; border: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "row_up", "Up"),
        Binding("down", "row_down", "Down"),
        Binding("k", "row_up"),
        Binding("j", "row_down"),
        # Non-priority: Enter on a focused control (Cancel, a device-login button, Remember, the picker, a credential
        # choice) belongs to that control; `⏎` in the filter is handled by its submit event (`on_input_submitted`).
        Binding("enter", "act_row", "Open"),
        Binding("r", "refresh_list", "Refresh list"),
        # `/` enters the filter, `a` adds a target (shown only with `target.edit`). Non-priority:
        # while the filter holds the keys these are typing, and they act when the focus is on the dialog's controls.
        Binding("slash", "focus_filter", "Filter"),
        Binding("a", "add_target", "Add target"),
    ]

    def __init__(self, target: str | None = None, target_type: str | None = None, *, refusal: str = "") -> None:
        super().__init__()
        self._initial_target = target or ""
        # The type of the target the screen was opened on (the Targets tab knows it); rows carry their own type
        # from the loaded pages, so no read is asked for a typed text.
        self._types: dict[str, str] = {target: target_type} if target and target_type is not None else {}
        # The target id the create that raised connection_choice_required was attempted against
        # — the picker's retry (on_select_changed) reuses it rather than re-reading the filter,
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
        # The search state: the filter text, the rows it matches, the highlighted row (an index into the painted
        # window, plus the Add row at len(rows)), and the ask the open press raised (rows of words, none pressed).
        # The highlight is a target identity, not a position: `selection follows the entity` (P3 rule), so the create
        # uses the id the operator sees marked, never whatever row now sits at that index. A prefilled target that is
        # not in the loaded list keeps its own id until the operator moves onto a row.
        self._filter_text = ""
        self._sel = 0
        self._sel_id = self._initial_target or None
        self._ask: list[tuple[str, str]] = []
        self._ask_sel = 0
        self._ask_target = ""
        # A create is in flight: the guard that stops a second Enter, a second Create press, or the vault-unlock
        # repeat opening a duplicate session on the broker. Set when the create is sent, cleared when it resolves.
        self._creating = False
        # The saved record the continuation is waiting for, once the Add row's form has saved, with
        # the one timer that watches for it. The watch is bounded to PENDING_WATCH_TICKS; when it ends without the
        # row landing, the screen shows the inventory-down state and keeps the saved id.
        self._pending_select = ""
        self._pending_timer = None
        self._pending_ticks = 0
        # The code of the refusal currently on screen whose recovery key is `r` (R_RECOVERY): the person's own `r`
        # press repeats the create for `target_connect_failed`, refetches and repaints for the other two. Set when
        # such a refusal shows, cleared when the refusal clears; nothing retries by itself.
        self._recovery_code: str | None = None
        # True while the add-target watch's inventory-down message is the line on screen: it goes when the saved row
        # lands, and any refusal that shows over it clears this flag so the landing never erases an unrelated refusal.
        self._watch_copy_shown = False

    def compose(self) -> ComposeResult:
        with Vertical(id="new-session-panel"):
            yield Static("New session", id="new-session-title")
            yield Input(placeholder="filter targets", id="new-session-filter")
            yield Static("", id="new-session-position", markup=False)
            yield Static("", id="target-rows", markup=False)
            yield Static("", id="no-match", markup=False)
            yield Static("", id="ask-lines", markup=False)
            with RadioSet(id="connection-choice"):
                yield RadioButton("New connection (fresh authentication)", value=True, id="conn-new")
                yield RadioButton("Reuse a connection already open to this target", id="conn-reuse")
            yield Static("", id="connection-note", markup=False)
            yield Static("", id="new-session-target", markup=False)
            # Shown when reuse is asked for: the id field on the radio's press, the picker on the core's 409.
            with Vertical(id="reuse-box"):
                yield Input(placeholder="connection id (blank: the only candidate, if there is one)",
                            id="connection-id", disabled=True)
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
            yield Static("", id="new-session-core", markup=False)
            yield Button("New connection" if self._refusal else "Create session", id="create-button", variant="primary")
            yield Button("Cancel", id="cancel-button")

    def on_mount(self) -> None:
        # The children mount as the screen mounts; every work that touches widget ids runs once they are in the
        # DOM, in this handler, which Textual dispatches after the compose.
        self._login_inputs = tuple(self.query("#login-user, #login-password"))
        self._hide_credential_choice()
        # The reuse box and its picker show only when reuse is asked for; hidden at rest so the dialog fits a 24-row window.
        self.query_one("#reuse-box").display = False
        if self._initial_target:
            # The prefilled target is the highlighted row; its line names it as the Targets tab handed it over.
            self._select_initial()
        self.query_one("#new-session-filter", Input).focus()
        self._show_type()
        self._paint_rows()
        self.app.call_api(self.app.client.me, on_done=self._on_me, on_error=self._on_me_error)

    def _select_initial(self) -> None:
        rows = self._all_rows()
        for index, row in enumerate(rows):
            if row.id == self._initial_target:
                self._sel = index
                self.query_one("#new-session-target", Static).update(
                    f"{as_text(self._initial_target)} · {as_text(self._types.get(self._initial_target, row.type))}")
                break

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
        if event.input.id == "new-session-filter":
            self._filter_text = event.input.value
            self._ask = []
            self._follow_selection()
            self._paint_rows()
            self._show_type()
        elif event.input.id in ("login-user", "login-password"):
            self._show_type()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """`⏎` in the filter is the open press on the highlighted row. Enter on any other focused control belongs to
        that control, so this handler runs only for the filter."""
        if event.input.id == "new-session-filter":
            self.action_act_row()

    def _follow_selection(self) -> None:
        """The highlight follows the entity: a filter that leaves rows puts the mark on the first match. The
        supplied identity is kept only when the filter clears to a list that does not hold it (the prefilled target
        the screen was handed); a typed query follows its first match."""
        rows = self._matched_rows()
        if rows:
            self._sel = min(self._sel, len(rows) - 1)
            if self._filter_text.strip():
                self._sel = 0
                self._sel_id = rows[self._sel].id
            elif self._initial_target and not any(r.id == self._initial_target for r in rows):
                self._sel_id = self._initial_target
            else:
                self._sel_id = rows[self._sel].id
        else:
            self._sel = 0
            self._sel_id = self._initial_target or None

    # -- the inventory and the painted rows --

    @property
    def selected_target_id(self) -> str | None:
        """The id of the highlighted row, for a test that needs the entity, not the painted line."""
        return self._selected_id()

    def _selected_id(self) -> str | None:
        """The target the create acts on: the highlighted row's id, the Add row as None, and the supplied identity
        when the screen was handed a target that is not in the loaded list. Never the row that happens to sit at
        the stale index."""
        rows = self._matched_rows()
        if self._add_row_shown() and self._sel >= len(rows):
            return None
        return self._sel_id

    def _all_rows(self):
        workspace = self.app.workspace()
        return tuple(workspace.targets.rows) if workspace is not None else ()

    def _matched_rows(self):
        needle = self._filter_text.strip().lower()
        rows = self._all_rows()
        if not needle:
            return rows
        out = []
        for row in rows:
            if (needle in row.name.lower() or needle in row.host.lower()
                    or needle in row.group.lower()
                    or any(needle in tag.lower() for tag in row.tags)
                    or needle in _target_label(row).lower()):
                out.append(row)
        return tuple(out)

    def _open_numbers(self) -> dict[str, list[int]]:
        """The number each session open in this window has on the rail, per target."""
        workspace = self.app.workspace()
        numbers: dict[str, list[int]] = {}
        if workspace is None:
            return numbers
        for index, slot in enumerate(workspace.slots):
            if not slot.closed:
                numbers.setdefault(slot.target, []).append(index + 1)
        return numbers

    def _row_line(self, row) -> str:
        """One painted row: the mark, the label, and the ruled facts (host, open numbers, group, type word)."""
        _, facts = _target_facts(row, self._open_numbers().get(row.id, ()), "", row.group, 40)
        label = _target_label(row)
        line = f"{label} · {facts}" if facts else label
        return line

    def _rights(self) -> bool:
        return "target.edit" in getattr(self.app, "permissions", frozenset())

    def _add_row_shown(self) -> bool:
        return bool(self._filter_text.strip()) and not self._matched_rows() and self._rights()

    def _paint_rows(self) -> None:
        rows = self._matched_rows()
        all_rows = self._all_rows()
        query = self._filter_text.strip()
        width = max(self.app.workspace()._window_width(), 40) if self.app.workspace() else 80
        index = f"{min(self._sel + 1, len(rows))}/{len(rows)}" if rows else "0/0"
        position = target_title(index, len(rows), len(all_rows), len(all_rows), query, False, width)
        self.query_one("#new-session-position", Static).update(position)
        lines: list[str] = []
        window = rows[:3] if self._sel < 3 else rows[max(0, self._sel - 2): self._sel + 1]
        for row in window:
            mark = "▸" if (rows.index(row) == self._sel and not self._ask) else " "
            lines.append(f"{mark} {self._row_line(row)}")
        if self._add_row_shown():
            add = NO_MATCH_ADD.format(query=as_text(query))
            mark = "▸" if self._sel == len(rows) and not self._ask else " "
            lines.append(f"{mark} {add}")
        self.query_one("#target-rows", Static).update("\n".join(lines))
        no_match = ""
        if not rows and query and not self._rights():
            no_match = NO_MATCH_NO_RIGHTS.format(query=as_text(query))
        self.query_one("#no-match", Static).update(no_match)
        ask_lines = [f"{'▸' if i == self._ask_sel else ' '} {label}" for i, (label, _) in enumerate(self._ask)]
        self.query_one("#ask-lines", Static).update("\n".join(ask_lines))

    # -- the keys --

    def action_row_up(self) -> None:
        if self._ask:
            self._ask_sel = max(0, self._ask_sel - 1)
            self._paint_rows()
            return
        rows = self._matched_rows()
        limit = len(rows) + (1 if self._add_row_shown() else 0)
        if limit:
            self._sel = max(0, self._sel - 1)
            self._set_selection(rows)
            self._paint_rows()
            self._show_type()

    def action_row_down(self) -> None:
        if self._ask:
            self._ask_sel = min(len(self._ask) - 1, self._ask_sel + 1)
            self._paint_rows()
            return
        rows = self._matched_rows()
        limit = len(rows) + (1 if self._add_row_shown() else 0)
        if limit:
            self._sel = min(limit - 1, self._sel + 1)
            self._set_selection(rows)
            self._paint_rows()
            self._show_type()

    def _set_selection(self, rows) -> None:
        """The highlight moves onto a row: its id becomes the selection; the Add row (at len(rows)) selects no target."""
        if self._add_row_shown() and self._sel >= len(rows):
            self._sel_id = None
        elif rows:
            self._sel_id = rows[min(self._sel, len(rows) - 1)].id

    def action_refresh_list(self) -> None:
        """The ruled recovery for a refusal whose key is `r`: the person's press repeats the create for
        `target_connect_failed`, refetches and repaints the list for `target_not_found` and `inventory_unavailable`.
        With no such refusal on screen, `r` is the ordinary foreground read of the list. Nothing is sent by a refusal
        itself; this is the person's own press."""
        workspace = self.app.workspace()
        code = self._recovery_code
        self._recovery_code = None
        if code == "target_connect_failed":
            # The person's `r` repeats the create on the target it was attempted against, with the mode, connection
            # id and credential that were submitted — never the radio and input state read afresh. A create in
            # flight is not pressed again.
            if self._creating:
                return
            if self._last_create is not None:
                target_id, connection, connection_id, credential = self._last_create
                self._create(target_id, connection, connection_id, None, credential)
            else:
                # The failed create carried a typed login, which the refusal cleared; it is never repeated silently.
                # Show the refusal again and focus the login fields so the person retypes what was lost.
                self._show_refusal("target_connect_failed")
                self.query_one("#login-user", Input).focus()
            return
        if workspace is not None:
            workspace._fetch_targets()
            if code in ("target_not_found", "inventory_unavailable"):
                self._paint_rows()  # the person's press repaints; the rows land from the refetch (finding 12's notification)

    def _refusal_label(self) -> str:
        """The refusal line's `<label>`: the pending target's row label, or its id as text when the row is gone."""
        workspace = self.app.workspace()
        row = workspace.targets.row(self._pending_target_id) \
            if workspace is not None and self._pending_target_id else None
        return _target_label(row) if row is not None else as_text(self._pending_target_id or "")

    def _show_refusal(self, code: str) -> None:
        """Re-render a ruled refusal line on the pending target (used by the typed-login recovery): the watch's
        inventory-down message, if it was showing, is replaced by this refusal."""
        self.query_one("#new-session-error", Static).update(REFUSAL_COPY[code].format(label=self._refusal_label()))
        self._watch_copy_shown = False

    def action_focus_filter(self) -> None:
        """`/` enters the filter. While it already holds the keys this is typing, so the binding
        fires only when the focus is on the dialog's controls."""
        self.query_one("#new-session-filter", Input).focus()

    def action_add_target(self) -> None:
        """`a` adds a target, shown only to a login holding `target.edit`. It opens
        the existing form with the typed text in the address field when it reads as an address; without that permission
        it does nothing (no Add row, no target-create)."""
        if self._rights():
            self._open_form()

    def action_act_row(self) -> None:
        """`⏎` acts on the highlighted row: an ask option, the Add row, or open-direct on a target row. A create in
        flight is not pressed again."""
        if self._creating:
            return
        if self._ask:
            self._act_ask()
            return
        rows = self._matched_rows()
        if self._add_row_shown() and self._sel == len(rows):
            self._open_form()
            return
        target_id = self._selected_id()
        if not target_id:
            return
        self._open_target(target_id)

    def action_cancel(self) -> None:
        """Back one level: an ask goes back to the list, a filter clears first, and the query stays when a form
        is dismissed. Every session open in the workspace is as it was."""
        if self._ask:
            self._ask = []
            self._paint_rows()
            return
        if self._filter_text:
            self.query_one("#new-session-filter", Input).value = ""
            return
        self.app.pop_screen()

    def _open_form(self) -> None:
        """The Add row opens the target form with the typed text in the address field when it reads as an IP
        address or a host name; type, port, name and login stay empty. A successful save lands back here with the
        saved record selected; nothing is created by itself."""
        from .target_form import TargetFormScreen

        query = self._filter_text.strip()
        definition = {"host": query} if reads_as_address(query) else None
        # Pushed from this screen: Textual delivers the dismiss result to this screen's callback.
        self.app.push_screen(TargetFormScreen(definition), self._after_form)

    def _after_form(self, record: dict | None) -> None:
        if record is None:
            return  # the form was dismissed: this screen stays as it was, query intact
        self._filter_text = ""
        self.query_one("#new-session-filter", Input).value = ""
        target_id = str(record.get("id", ""))
        # The form's own save callback runs in the workspace, which is where its push lives; this screen keeps
        # its own continuation state and refreshes the list for the record the save returned.
        # The workspace's own save path (`a` in the Targets tab) does this refresh; the Add row's save runs its
        # own callback, so the continuation asks for the read here.
        self.app.workspace().target_saved(record)
        self._pending_select = target_id
        self._pending_ticks = 0
        if self.app.workspace().targets.row(target_id) is not None:
            self._check_pending()
        else:
            # One bounded timer until the record lands; it stops with the watch, never a chain of timers.
            self._pending_timer = self.app.set_interval(0.1, self._check_pending)
        self._paint_rows()

    def _check_pending(self) -> None:
        workspace = self.app.workspace()
        if workspace is None or not self._pending_select:
            self._stop_pending_watch()
            return
        row = workspace.targets.row(self._pending_select)
        if row is not None:
            rows = self._all_rows()
            for index, entry in enumerate(rows):
                if entry.id == self._pending_select:
                    self._sel = index
                    self._sel_id = self._pending_select
                    break
            self._pending_select = ""
            self._stop_pending_watch()
            self._clear_watch_copy()
            self._paint_rows()
            return
        # The saved row has not landed. Bound the watch to 5 seconds after the save (PENDING_WATCH_TICKS); when it
        # ends, show the Targets tab's own inventory-down state and keep the saved id, so a later successful list
        # selects it. No endless timer, no new copy. The message names `r` as the recovery key, so the keyboard moves
        # off the filter to Create and `r` refetches the list, never types into the search.
        self._pending_ticks += 1
        if self._pending_ticks >= PENDING_WATCH_TICKS:
            self._stop_pending_watch()
            self.query_one("#new-session-error", Static).update(TARGETS_UNAVAILABLE_COPY)
            self._watch_copy_shown = True
            self._recovery_code = "inventory_unavailable"
            self.query_one("#create-button", Button).focus()

    def _stop_pending_watch(self) -> None:
        timer = getattr(self, "_pending_timer", None)
        if timer is not None:
            timer.stop()
            self._pending_timer = None

    def notify_target_rows_changed(self) -> None:
        """The workspace's target rows changed (a page landed, a refresh, a saved target arrived): repaint the
        painted window, and if a saved target from the add-target watch has now landed, select it. The workspace's
        own row-change notification, not a poll — no timer runs here."""
        workspace = self.app.workspace()
        if self._pending_select and workspace is not None and workspace.targets.row(self._pending_select) is not None:
            for index, entry in enumerate(self._all_rows()):
                if entry.id == self._pending_select:
                    self._sel = index
                    self._sel_id = self._pending_select
                    break
            self._pending_select = ""
            self._stop_pending_watch()
        self._clear_watch_copy()
        self._rederive_mark()  # the mark follows the entity through the changed rows, so Enter acts on the marked row
        self._paint_rows()
        self._show_type()

    def _rederive_mark(self) -> None:
        """The painted index is re-derived from the selected id, so after a repaint the mark and Enter point at the
        same target. When the id is not in the filtered rows the fallback (follow the first match, or clamp to the
        row at the stale index) is a design decision this phase leaves open; the index is left as it is."""
        for index, row in enumerate(self._matched_rows()):
            if row.id == self._sel_id:
                self._sel = index
                return

    def _clear_watch_copy(self) -> None:
        """The add-target watch's inventory-down message goes when the saved row lands, before the repaint. A refusal
        that showed over it clears this flag, so an unrelated refusal is never erased here."""
        if self._watch_copy_shown:
            self.query_one("#new-session-error", Static).update("")
            self._watch_copy_shown = False
            self._recovery_code = None

    # -- open-direct and the asks --

    def _sessions_for(self, target_id: str) -> list[dict]:
        """This login's session records on this target, from the fleet's merged view. The workspace's live
        attachment records are passed in (the same read its other session views make), so a session opened in
        this window counts as a live connection before the next fleet poll lists it."""
        workspace = self.app.workspace()
        if workspace is None or workspace.fleet is None:
            return []
        scan = workspace.fleet.scan(open_here=[slot.session_id for slot in workspace.slots if not slot.closed],
                                    live=workspace._live())
        return [record for record in scan.merged.values()
                if record.get("target_id") == target_id and record.get("owner") == self.app.username]

    def _live_connections(self, target_id: str) -> list[str]:
        """This login's connection ids with a session of theirs not ended on the target (the same read the
        picker's remedy makes: `GET /sessions`, each session's `connection_id`)."""
        sessions = self._sessions_for(target_id)
        return list(dict.fromkeys(
            record["connection_id"] for record in sessions
            if record.get("connection_id") and record.get("state") not in ("ended", "killed", "terminated")))

    def _open_target(self, target_id: str) -> None:
        """`⏎` on a target row: the ask for a target with a session when this login has a session on it, the §15.3
        choice when a live connection exists, and one create at once when there is one way. A telnet target never
        gets the reuse ask (DEFAULT 22: a telnet connection carries one session)."""
        workspace = self.app.workspace()
        # The number each open session on this target has on the rail is counted over all of `workspace.slots`,
        # closed ones included (the same count `_open_numbers` and the rail use), so the ask's number matches them.
        numbers = [index + 1 for index, slot in enumerate(workspace.slots)
                   if not slot.closed and slot.target == target_id]
        live = [] if self._is_telnet(target_id) else self._live_connections(target_id)
        if numbers:
            first = min(numbers)
            sid = workspace.slots[first - 1].session_id
            options = [(GO_TO_SESSION.format(number=first), ("go", sid))]
            if live:
                options.append((ANOTHER_SESSION, ("reuse", live[0])))
            options.append((NEW_CONNECTION, ("new", None)))
            self._ask = options
            self._ask_sel = 0
            self._ask_target = target_id
            self._paint_rows()
            return
        if live and not self._typed_login():
            self._ask = [(ANOTHER_SESSION, ("reuse", live[0])), (NEW_CONNECTION, ("new", None))]
            self._ask_sel = 0
            self._ask_target = target_id
            self._paint_rows()
            return
        self._create_from_row(target_id)

    def _act_ask(self) -> None:
        _label, (kind, arg) = self._ask[self._ask_sel]
        self._ask = []
        if kind == "go":
            self.app.pop_screen()
            self.app.workspace().bring_forward(arg)
            return
        # The create acts on the target the ask was raised for, with the mode that option named — never a row
        # re-read by index (the list can change under the ask), and never the reuse radio left from before.
        self._create_from_row(self._ask_target, reuse_connection=arg if kind == "reuse" else None,
                              connection_mode=kind)

    # -- the create (unchanged machinery, now keyed on the selected row) --

    def _typed_login(self) -> bool:
        return bool(self.query_one("#login-user", Input).value or self.query_one("#login-password", Input).value)

    def _clear_login(self) -> None:
        """Both fields empty, and the dialog holds no other copy: the values live in the two inputs and in the one call
        that sends them. `query`, not `query_one`: the dialog may already have been taken away by a 401."""
        for box in getattr(self, "_login_inputs", self.query("#login-user, #login-password")):
            box.value = ""

    def on_unmount(self) -> None:
        self._stop_pending_watch()
        self._clear_login()
        self._login_inputs = ()

    def _type_of(self, target_id: str) -> str | None:
        """The row's type from the loaded pages; the prefilled target's, handed over by the Targets tab, otherwise."""
        workspace = self.app.workspace()
        row = workspace.targets.row(target_id) if workspace is not None and target_id else None
        return row.type if row is not None else self._types.get(target_id)

    def _is_telnet(self, target_id: str) -> bool:
        return self._type_of(target_id) == TELNET

    def _show_type(self) -> None:
        """The selected row's target line and the telnet rule: with a telnet target the reuse choice is greyed
        and says why, and `new` is the choice."""
        target_id = self._selected_id() or ""
        target_type = self._type_of(target_id)
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
            self.query_one("#reuse-box").display = reuse
        elif event.radio_set.id == "credential-choice":
            event.stop()
            self._pick_credential(event.radio_set.pressed_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-button":
            target_id = self._selected_id()
            if not target_id:
                self.query_one("#new-session-error", Static).update(TARGET_REQUIRED)
                return
            self._create_from_row(target_id)
        elif event.button.id == "cancel-button":
            self.action_cancel()
        elif event.button.id == "device-login-start":
            if not self.me_ready or self.app.screen_lock.locked:
                return
            target = self._selected_id() or ""
            size = self._create_size()
            if self._type_of(target) != "ssh":
                self.query_one("#new-session-error", Static).update("Choose an SSH target to answer login prompts.")
                return
            self._clear_login()
            self.app.pop_screen()
            self.app.workspace().open_device_login(target, TERM, size, recording_login=self._recording_login)

    def _create_from_row(self, target_id: str, reuse_connection: str | None = None,
                         connection_mode: str | None = None) -> None:
        if self._creating:
            return  # a create is already in flight; a second press never sends another
        error = self.query_one("#new-session-error", Static)
        if self._half_login(target_id):
            error.update(HALF_LOGIN)
            return
        error.update("")
        self._recovery_code = None  # a new create clears the prior refusal's recovery key
        self._watch_copy_shown = False  # a new create clears the watch's message too
        self._hide_credential_choice()
        self._pending_target_id = target_id
        self._unlock_tried = False
        self.query_one("#create-button", Button).disabled = True
        self._start_create(target_id, reuse_connection, connection_mode)

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

    def _start_create(self, target_id: str, reuse_connection: str | None = None,
                      connection_mode: str | None = None) -> None:
        if self._half_login(target_id):
            # The fields changed while the row's type was being checked.
            self.query_one("#new-session-error", Static).update(HALF_LOGIN)
            self.query_one("#create-button", Button).disabled = False
            return
        if connection_mode is not None:
            # An ask option carries its own mode, so the create does what that option said, never the radio state
            # left on screen from before the ask.
            connection = connection_mode
            connection_id = reuse_connection if connection_mode == "reuse" else None
        else:
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

    def _create_size(self) -> tuple[int, int]:
        return create_size(self.app)

    def _create(self, target_id: str, connection: str, connection_id: str | None, login: dict | None = None,
                credential: tuple[str, str] | None = None) -> None:
        cols, rows = self._create_size()
        self._creating = True  # a create is in flight; the guard refuses a second press until it resolves
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
        self._creating = False  # the create resolved; the guard opens again for the next press
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
        self._creating = False  # the create resolved with an error; the guard opens again for a recovery press
        self.query_one("#create-button", Button).disabled = False
        self.query_one("#new-session-core", Static).update("")
        if isinstance(exc, ApiError) and exc.code in REFUSAL_COPY:
            label = self._refusal_label()
            # The ruled line, with the core's own message under it (the row never replaces what the broker said).
            self.query_one("#new-session-error", Static).update(REFUSAL_COPY[exc.code].format(label=label))
            self.query_one("#new-session-core", Static).update(as_text(exc.message))
            self._watch_copy_shown = False  # a refusal replaces any watch message it shows over
            if exc.code in R_RECOVERY:
                # The refusal names `r` as the person's recovery key. Move the keyboard off the filter to the Create
                # button so a press of `r` reaches the refusal, not typing into the search.
                self._recovery_code = exc.code
                self.query_one("#create-button", Button).focus()
        if (isinstance(exc, ApiError) and exc.code == NO_RESOLVING_CREDENTIAL
                and self.query_one("#login-fields").display):
            # Empty fields meant "what TSB holds", and it holds nothing (ruling A13): the dialog stays open and says
            # what to do, with the cursor where the login begins. The toast `notify_api_error` raises is unchanged.
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
        if isinstance(exc, ApiError) and exc.code not in REFUSAL_COPY:
            # A code the table does not name (a malformed credential list, any other answer): the core's own words.
            self.query_one("#new-session-error", Static).update(as_text(exc.message))
            self.app.notify_api_error(exc)
        if not isinstance(exc, ApiError):
            # A transport failure (a broker connection failure, an HTTP response with no API body) is not a ruled
            # refusal code; the app's own transport handling tells the operator, never a silent re-enable of Create.
            self.query_one("#new-session-error", Static).update(as_text(str(exc)))
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
        # Keys-only traversal: the offered choice takes focus, so Enter and the arrows act on the candidates, not
        # the target row behind it.
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
            # retry (a fresh "new" connection, or reuse if one reopens). The box goes back to its resting state.
            self.query_one("#reuse-box").display = False
            self.query_one("#new-session-error", Static).update(
                "no connection of this principal's own to this target is listed anymore — retry")
            return
        # The picker lives inside `#reuse-box`, hidden at rest; showing the picker alone leaves it invisible, so the
        # box is shown with it and the operator can reach the choice by keyboard.
        self.query_one("#reuse-box").display = True
        picker.set_options([(connection_id, connection_id) for connection_id in candidates])
        picker.disabled = False
        picker.display = True
        picker.focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "connection-picker" or event.value is Select.NULL:
            return
        target_id = self._pending_target_id
        if target_id is None:
            return
        event.select.disabled = True
        self.query_one("#reuse-box").display = False  # the choice is made; the box returns to its resting state
        self.query_one("#new-session-error", Static).update("")
        self.query_one("#create-button", Button).disabled = True
        self._create(target_id, "reuse", str(event.value))
