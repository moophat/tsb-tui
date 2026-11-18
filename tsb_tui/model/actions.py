"""Every action once: its label, description, key and when it is available (design §2.4, §3.3).

The status row's hints, the Menu, the dialogs' buttons and (phase 9) `tsb-tui drive` all read this one
table, so a key never exists without a visible way in (D40) and drive never has an action a person
does not. A later phase adds its actions here and nowhere else.

An action has three ways in (design §2.4): a click on its hint or its Menu entry, and its key after the
leader. `scope` says where the key is read: `session` actions are reached from the terminal, with the
leader first; `who` and `size` actions are the buttons of their dialog, with a plain key while that
dialog has the keyboard (D14). Two actions may share a letter only when their scopes differ.

What an action needs is decided here, from the models, and never by the widget that shows it: `blocked`
returns the reason an action is not available now, or None when it is, so the Menu, the status row and
the dialogs agree and each can say *why* a button is grey.

Nothing here imports Textual (design §3.3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .attachment import AttachmentState

SCOPE_SESSION = "session"
SCOPE_WHO = "who"
SCOPE_SIZE = "size"
SCOPE_THEME = "theme"
SCOPES = (SCOPE_SESSION, SCOPE_WHO, SCOPE_SIZE, SCOPE_THEME)

GROUP_SESSION = "This session"
GROUP_FLEET = "Fleet"
GROUP_VIEW = "View"
GROUP_SETTINGS = "Settings and help"
# The Menu's groups, in the order the design lists them (§2.4).
GROUPS = (GROUP_SESSION, GROUP_FLEET, GROUP_VIEW, GROUP_SETTINGS)

# How the leader key is printed in the status row (`≡ Menu ^\`), by the Textual key name the
# `keys.leader` knob holds. The design's own reasons (§2.4) rule out every Ctrl-letter: they are
# readline's, a router's or the session's. These four are the control keys that are none of those.
LEADER_LABELS: dict[str, str] = {
    "ctrl+backslash": "Ctrl+\\",
    "ctrl+right_square_bracket": "^]",
    "ctrl+circumflex_accent": "^^",
    "ctrl+underscore": "^_",
}


def leader_label(name: str) -> str:
    """The printed form of the leader key, e.g. `^\\` for `ctrl+backslash`."""
    return LEADER_LABELS[name]


@dataclass(frozen=True)
class Facts:
    """What an action's availability reads: the attachment's state (None before the first attach), inside a
    dialog the client the operator selected, and how many sessions are open here (the rail's chips)."""

    state: AttachmentState | None
    selected: str | None = None
    open_sessions: int = 1
    under_drive: bool = False
    alternate_screen: bool = False  # the session in front is on its alternate screen (no scrollback there)
    permissions: frozenset[str] = frozenset()
    target_type: str | None = None  # the type of the session in front's target, as the inventory knows it


Blocked = Callable[[Facts], "str | None"]


@dataclass(frozen=True)
class Action:
    id: str
    label: str  # the Menu's name for it; an ellipsis marks one that opens a dialog
    description: str  # one line, shown beside the label in the Menu
    key: str | None  # the letter after the leader (scope `session`) or the dialog's own key; None: the leader alone
    group: str
    scope: str
    hint: str | None  # the status row's word for it; None: no hint of its own
    blocked: Blocked
    glyph: str = ""  # the mark that leads its hint (`≡ Menu ^\`), when it has one
    in_menu: bool = True
    permission: str | None = None


def _attached(facts: Facts) -> str | None:
    state = facts.state
    if state is None or state.client_id is None or state.closed:
        return "not attached to a session"
    return None


def _always(facts: Facts) -> str | None:
    return None


def _reuse_blocked(facts: Facts) -> str | None:
    """"New session on this connection" reuses a supplied-login connection; a telnet connection carries
    one session (session model §5), so there is nothing to reuse."""
    if (why := _attached(facts)) is not None:
        return why
    if facts.target_type == "telnet":
        return "a telnet connection carries one session; there is nothing to reuse"
    return None


def _theme_blocked(facts: Facts) -> str | None:
    # TT19: drive sets the theme with --set, so its Theme action is listed and blocked with that reason.
    return ("drive sets the theme with --set theme.name=… --set theme.term.scheme=…"
            if facts.under_drive else None)


def _scroll_blocked(facts: Facts) -> str | None:
    """Scroll back needs a session and its scrollback. The engine keeps none for the alternate screen: the viewport
    stays on the active area while a full-screen program runs."""
    if (why := _attached(facts)) is not None:
        return why
    if facts.alternate_screen:
        return "the session is on its alternate screen (a full-screen program); its own keys scroll it"
    return None


def _any_open(facts: Facts) -> str | None:
    return None if facts.open_sessions >= 1 else "no session is open here"


def _another_open(facts: Facts) -> str | None:
    return None if facts.open_sessions >= 2 else "no other session is open here"


def _holder_name(state: AttachmentState) -> str | None:
    return None if state.holder is None else state.holder.get("user")


def _in_flight(state: AttachmentState) -> str | None:
    # Two hand-offs in one round trip would leave `handoff_in_flight` cleared by the first answer.
    return "a hand-off is in flight" if state.handoff_in_flight else None


def _client(state: AttachmentState, client_id: str | None) -> dict | None:
    for client in state.clients:
        if client.get("client_id") == client_id:
            return client
    return None


def _end_blocked(facts: Facts) -> str | None:
    """End session… acts on the session in front. Whether this login may end it is the core's to say (the owner
    or an admin holding `session.terminate`: session-end-by-tsb step 1), so only a session that is not there, or
    already over, is refused here."""
    return _attached(facts)


def _take_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    if not state.is_owner_client:
        return "only the owner's clients can take the keyboard"
    if state.holds_keyboard:
        return "this client already holds the keyboard"
    return _in_flight(state)


def _hand_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    if not state.may_type:
        return "only the keyboard holder or the owner can hand it over"
    if (why := _in_flight(state)) is not None:
        return why
    if facts.selected is None:
        return "select a client first"
    if facts.selected == state.client_id:
        return "that is this client: use Take it"
    chosen = _client(state, facts.selected)
    if chosen is None:
        return "that client is no longer attached"
    if facts.selected == state.holder_client_id:
        # `to` the client that already holds it is a case the contract leaves open (typing-ownership
        # open item 11), so nothing is sent for it.
        return f"{chosen.get('user')} already holds the keyboard"
    return None


def _aside_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    if not state.is_owner_client:
        return "only the owner can put the keyboard aside"
    if state.holder is None:
        return "nobody holds the keyboard"
    return _in_flight(state)


def _remove_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    if not state.is_owner_client:
        return "only the owner can remove a client"
    if facts.selected is None or _client(state, facts.selected) is None:
        return "select a client first"
    return None


def _answer_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    return None if state.quiet_prompt is not None else "no question is waiting"


def _take_back_blocked(facts: Facts) -> str | None:
    return _take_blocked(facts)


def _ask_size_blocked(facts: Facts) -> str | None:
    if (why := _attached(facts)) is not None:
        return why
    state = facts.state
    assert state is not None
    if not state.holds_keyboard:
        who = _holder_name(state)
        return f"only the typist can ask; {who} types now" if who else "only the typist can ask; nobody types now"
    if state.size_final:
        return "the target clamped the last request; ask again after the window changes"
    return None


# The actions, in the order the Menu lists them within a group. A later phase appends here.
ACTIONS: tuple[Action, ...] = (
    Action("menu", "Menu", "every action, searchable", None, GROUP_SESSION, SCOPE_SESSION, "Menu",
           _always, glyph="≡", in_menu=False),
    Action("who", "Who's here…", "who types; hand off, take, put aside", "w", GROUP_SESSION,
           SCOPE_SESSION, "Who's here", _attached),
    Action("size", "Size…", "the target's size · ask for my size · auto size", "r", GROUP_SESSION,
           SCOPE_SESSION, "Size", _attached),
    Action("auto_size", "Auto size on or off", "ask for my window's size when it changes, while I type",
           "R", GROUP_SESSION, SCOPE_SESSION, None, _always),
    Action("detach", "Detach", "leave it running", "d", GROUP_SESSION, SCOPE_SESSION, "Detach", _any_open),
    Action("end", "End session…", "ends it for everyone; asks first", "x", GROUP_SESSION, SCOPE_SESSION,
           "End…", _end_blocked),
    Action("fleet", "Fleet sidebar", "open or close it beside the terminal", "f", GROUP_FLEET,
           SCOPE_SESSION, "Fleet", _always, glyph="▦"),
    Action("answer", "Answer the question…", "the session asked whether to kill it: keep it or kill it",
           "a", GROUP_SESSION, SCOPE_SESSION, "Answer: kill the session?", _answer_blocked),
    Action("full_view", "Full fleet view", "every session you own or may join, full screen", "F",
           GROUP_FLEET, SCOPE_SESSION, None, _always),
    Action("take_back", "Take back…", "the keyboard, in a session of yours someone else types in", "t",
           GROUP_FLEET, SCOPE_SESSION, "Take back", _take_back_blocked),
    Action("new_session", "New session…", "on a target you can open", "c", GROUP_FLEET, SCOPE_SESSION,
           None, _always),
    Action("new_on_connection", "New session on this connection", "reuse this session's authentication", None,
           GROUP_SESSION, SCOPE_SESSION, None, _reuse_blocked),
    Action("switch", "Switch to…", "pick a session from the fleet sidebar, or its number after the leader", "s",
           GROUP_FLEET, SCOPE_SESSION, None, _always),
    Action("next", "Next open session", "the next session open here", "n", GROUP_FLEET, SCOPE_SESSION, None,
           _another_open),
    Action("prev", "Previous open session", "the previous session open here", "p", GROUP_FLEET,
           SCOPE_SESSION, None, _another_open),
    Action("event_log", "Event log", "what this session's frames told this client, newest last", "e",
           GROUP_VIEW, SCOPE_SESSION, None, _attached),
    Action("scroll", "Scroll back", "older output this client holds; Esc returns to live", "[", GROUP_VIEW,
           SCOPE_SESSION, "Scroll", _scroll_blocked),
    Action("pan", "Pan", "move the window over a bigger grid with the arrow keys", "v", GROUP_VIEW,
           SCOPE_SESSION, None, _attached),
    Action("theme", "Theme…", "chrome and terminal colours, previewed as you move", "T", GROUP_VIEW,
           SCOPE_SESSION, None, _theme_blocked),
    Action("settings", "Settings…", "the knobs in force and where each came from", None, GROUP_SETTINGS,
           SCOPE_SESSION, None, _always),
    Action("keys", "All keys", "every action's key", "?", GROUP_SETTINGS, SCOPE_SESSION, "keys", _always),
    Action("about", "About", "the TUI, the contract client and the engine", None, GROUP_SETTINGS,
           SCOPE_SESSION, None, _always),
    Action("credentials", "Credentials…", "your saved logins: list, map to targets, delete", "C", GROUP_SETTINGS,
           SCOPE_SESSION, None, _always),
    Action("devices", "Devices…", "your trusted devices: list and revoke", "V", GROUP_SETTINGS, SCOPE_SESSION, None,
           _always),
    Action("accounts", "Accounts…", "list, create, disable, enable and set passwords", None, GROUP_SETTINGS,
           SCOPE_SESSION, None, _always, permission="account.admin"),
    Action("password_change", "Change my password…", "prove your current password and set a new one", None,
           GROUP_SETTINGS, SCOPE_SESSION, None, _always),
    Action("lock_now", "Lock now", "lock your vault everywhere and this screen", "L", GROUP_SETTINGS, SCOPE_SESSION,
           None, _always),
    Action("sign_out", "Sign out", "detach everything here and sign out; every session keeps running", None,
           GROUP_SETTINGS, SCOPE_SESSION, None, _always),
    Action("quit", "Quit", "leave the TUI; every session keeps running", ":q", GROUP_SETTINGS,
           SCOPE_SESSION, None, _always),
    # Who's here's buttons (F5).
    Action("take", "Take it", "the owner takes the keyboard", "t", GROUP_SESSION, SCOPE_WHO, "Take it",
           _take_blocked, in_menu=False),
    Action("hand", "Hand to selected", "the selected client gets the keyboard", "h", GROUP_SESSION,
           SCOPE_WHO, "Hand to selected", _hand_blocked, in_menu=False),
    Action("aside", "Put it aside", "nobody types until the owner hands it out", "e", GROUP_SESSION,
           SCOPE_WHO, "Put it aside", _aside_blocked, in_menu=False),
    Action("remove", "Remove selected…", "disconnect that client; the session lives on", "x",
           GROUP_SESSION, SCOPE_WHO, "Remove…", _remove_blocked, in_menu=False),
    # The picker's buttons (B5), keyed by the picker's own bindings; the key is the printed form.
    Action("theme_use", "Use", "keep the look previewed for this run", "⏎", GROUP_SESSION, SCOPE_THEME,
           "Use", _always, in_menu=False),
    Action("theme_save", "Save", "write this look to the per-login state file", "s", GROUP_SESSION, SCOPE_THEME,
           "Save", _always, in_menu=False),
    Action("theme_cancel", "Cancel", "put back what was in use", "Esc", GROUP_SESSION, SCOPE_THEME,
           "put back", _always, in_menu=False),
    # Size's button (F11); its auto toggle is the session action `auto_size`.
    Action("ask_size", "Ask for my size", "asks the target to take my window's size; it may answer "
           "differently", "r", GROUP_SESSION, SCOPE_SIZE, "Ask", _ask_size_blocked, in_menu=False),
)

BY_ID: dict[str, Action] = {action.id: action for action in ACTIONS}
assert len(BY_ID) == len(ACTIONS), "two actions share an id"


def leader_actions() -> tuple[Action, ...]:
    """The actions whose key is read after the leader."""
    return tuple(a for a in ACTIONS if a.scope == SCOPE_SESSION and a.key is not None)


# The ids `keys.leader_map` may rebind (config.py registers one knob each).
LEADER_MAP_IDS: tuple[str, ...] = tuple(a.id for a in leader_actions())


class KeymapError(ValueError):
    """A `keys.leader_map` override that cannot be used. The message names the key."""


def leader_keys(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """The letter after the leader for each action id, the table's own unless `keys.leader_map`
    rebinds it. Raises `KeymapError` for an unknown action, a value that is not one printable
    character, or two actions left on one letter."""
    keys = {a.id: a.key for a in leader_actions() if a.key is not None}
    for action_id, letter in (overrides or {}).items():
        if action_id not in keys:
            raise KeymapError(f"keys.leader_map.{action_id}: no action of that name takes a key after the leader")
        if len(letter) != 1 or not letter.isprintable() or letter.isspace():
            raise KeymapError(
                f"keys.leader_map.{action_id}: expected one printable character, got {letter!r}")
        if letter == "/":
            raise KeymapError(f"keys.leader_map.{action_id}: '/' is reserved for Menu search")
        keys[action_id] = letter
    seen: dict[str, str] = {}
    for action_id, letter in keys.items():
        if letter in seen:
            raise KeymapError(
                f"keys.leader_map: {seen[letter]!r} and {action_id!r} are both on {letter!r} after the leader")
        seen[letter] = action_id
    return keys


def action_for_leader_key(letter: str, overrides: Mapping[str, str] | None = None) -> Action | None:
    """The action a letter pressed after the leader runs, or None."""
    for action_id, bound in leader_keys(overrides).items():
        if bound == letter:
            return BY_ID[action_id]
    return None


def chord_starts(overrides: Mapping[str, str] | None = None) -> frozenset[str]:
    """The first characters of the keys that take two characters after the leader (`:q` for Quit): the character
    is pressed, and the next one decides (design §2.4, D38)."""
    return frozenset(letter[0] for letter in leader_keys(overrides).values() if len(letter) > 1)


def action_for_chord(first: str, second: str, overrides: Mapping[str, str] | None = None) -> Action | None:
    """The action a two-character key after the leader runs, or None."""
    for action_id, bound in leader_keys(overrides).items():
        if bound == first + second:
            return BY_ID[action_id]
    return None


def blocked(action_id: str, facts: Facts) -> str | None:
    """Why this action is not available now, or None when it is."""
    action = BY_ID[action_id]
    if not permitted(action, facts):
        return "not permitted"
    return action.blocked(facts)


def menu_actions() -> tuple[Action, ...]:
    """What the Menu lists, in the design's groups."""
    return tuple(sorted((a for a in ACTIONS if a.in_menu), key=lambda a: GROUPS.index(a.group)))


def permitted(action: Action, facts: Facts) -> bool:
    return action.permission is None or action.permission in facts.permissions
