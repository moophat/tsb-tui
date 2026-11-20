"""The one action table: what every action is, which key it has, and when it is available (design §2.4, §3.3).

Why these matter (Rule 9): the status row, the Menu, the dialogs' buttons and drive all read this
table, so an action missing a label or a key is one a person cannot find (D40), two on one key is one
that can never be reached, and an enabled-when that disagrees with the contract is a button that sends
a request the core will refuse (typing-ownership steps 5-9, 12) or hides one it would accept. Frames
here are the contract's own, built by `model_frames.py` and validated against the schema (DT6).
"""

from __future__ import annotations

import pytest
from model_frames import (
    refused_frame,
    size_frame,
    state_of,
    token_frame,
)

from tsb_tui.model import actions as A
from tsb_tui.model.attachment import AttachmentState
from tsb_tui.screens.terminal import TerminalScreen

ME, ME_USER = "client-me", "fixture-user"
PEER, PEER_USER = "client-peer", "fixture-peer"
VIEWER, VIEWER_USER = "client-viewer", "fixture-viewer"
THREE = ((ME, ME_USER, True), (PEER, PEER_USER, True), (VIEWER, VIEWER_USER, False))


def _facts(state: AttachmentState | None, selected: str | None = None) -> A.Facts:
    return A.Facts(state, selected)


def _owner_holds() -> AttachmentState:
    """This client is the owner's and holds the keyboard; a peer and a viewer are attached."""
    return state_of(me=(ME, ME_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)


def _agent_holds() -> AttachmentState:
    """This client is the owner's; the peer holds the keyboard (an agent in the owner's session)."""
    return state_of(me=(ME, ME_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)


def _peer_holds_owned_by_someone_else() -> AttachmentState:
    """This client is a peer who holds the keyboard in somebody else's session."""
    return state_of(me=(PEER, PEER_USER), owner=ME_USER, holder=(PEER, PEER_USER), clients=THREE)


def _viewer() -> AttachmentState:
    """This client is a view-only principal in somebody else's session; the owner holds the keyboard."""
    return state_of(me=(VIEWER, VIEWER_USER), owner=ME_USER, holder=(ME, ME_USER), clients=THREE)


# The actions that have no key after the leader: the Menu itself, and the entries of Settings and help whose key the
# design does not name (mockup F3 prints `^\\ ?` for All keys and `:q` for Quit only). Sign out is Menu-only:
# a costly slip must never be one keystroke away. New session on this connection is menu-only too: it lives in the
# session menu.
MENU_ONLY = frozenset({"settings", "about", "sign_out", "new_on_connection", "accounts", "password_change"})


def test_every_action_has_a_label_a_description_a_group_and_a_key() -> None:
    """D40, design §3.3. Help is never a prerequisite: an action with no label cannot be listed, one
    with no description is a bare word in the Menu, and one with no key is reachable only by a click
    (the Menu action's key is the leader alone, which is its own way in)."""
    for action in A.ACTIONS:
        assert action.label.strip(), action.id
        assert action.description.strip(), action.id
        assert action.group in A.GROUPS, action.id
        assert action.scope in A.SCOPES, action.id
        if action.id == "menu":
            assert action.key is None, "the Menu is the leader alone (D15)"
        elif action.id in MENU_ONLY:
            # The design's table (§2.4) gives these no key: the Menu and its Settings and help group are their way in.
            assert action.key is None, f"{action.id} was given a key the design does not give it"
        elif action.id == "quit":
            assert action.key == ":q", "Quit is `^\\ :q` (D38)"
        elif action.scope == A.SCOPE_THEME:
            # The picker's own buttons: their keys are printed forms its bindings act on, `Esc` as the
            # mock prints it, not letters after the leader.
            assert action.key, f"{action.id} has no key"
        else:
            assert action.key and len(action.key) == 1, f"{action.id} has no key after the leader"


def test_no_two_actions_share_a_key_in_one_scope() -> None:
    """Design §2.4. A letter after the leader that names two actions runs one and hides the other;
    the same letter is fine in two scopes, because a dialog's own key is read only while that dialog
    has the keyboard (`r` opens Size from the terminal and asks inside it)."""
    seen: dict[tuple[str, str], str] = {}
    for action in A.ACTIONS:
        if action.key is None:
            continue
        slot = (action.scope, action.key)
        assert slot not in seen, f"{action.id} and {seen[slot]} both take {action.key!r} in {action.scope}"
        seen[slot] = action.id


def test_every_session_action_has_a_handler_on_the_terminal_screen() -> None:
    """D40: no key without a way in, and no way in without something behind it. An action the Menu lists
    and the screen cannot run would notify and do nothing; each session action has `do_<id>`."""
    for action in A.ACTIONS:
        if action.scope == A.SCOPE_SESSION:
            assert callable(getattr(TerminalScreen, f"do_{action.id}", None)), action.id


def test_the_menu_lists_every_action_except_itself_and_the_dialog_buttons() -> None:
    """Design §3.4 test 5: every action is in the Menu. Two kinds are not, and each has a stated way in:
    the Menu itself (the leader alone, `≡ Menu`) and a dialog's buttons (they are on that dialog)."""
    listed = {a.id for a in A.menu_actions()}
    for action in A.ACTIONS:
        if action.id in listed:
            continue
        assert action.id == "menu" or action.scope in (A.SCOPE_WHO, A.SCOPE_SIZE, A.SCOPE_THEME), (
            f"{action.id} is in neither the Menu nor a dialog")
    assert {"who", "size", "auto_size", "detach", "fleet"} <= listed


def test_the_menu_groups_follow_the_designs_order() -> None:
    """Design §2.4 (F3): This session first, then Fleet."""
    groups = [a.group for a in A.menu_actions()]
    assert groups == sorted(groups, key=A.GROUPS.index)


# -- Who's here's buttons, by role (typing-ownership steps 5-9, 12; client-remove) --


def test_take_it_is_for_an_owners_client_that_does_not_hold_the_keyboard() -> None:
    """Typing-ownership step 9: an owner's client takes the keyboard with `handoff` to itself. Not for
    the one that already holds it (nothing to take), and not for anyone else (the core would answer
    `not_allowed`: step 12)."""
    assert A.blocked("take", _facts(_agent_holds())) is None
    assert "already holds" in A.blocked("take", _facts(_owner_holds()))
    assert "only the owner" in A.blocked("take", _facts(_peer_holds_owned_by_someone_else()))
    assert "only the owner" in A.blocked("take", _facts(_viewer()))


def test_take_back_is_offered_in_the_cases_take_it_is() -> None:
    """D45: Take back is the owner's way to the keyboard from the leader and the quiet prompt; it is offered
    in the same cases as Take it and for the same reasons."""
    assert A.blocked("take_back", _facts(_agent_holds())) is None
    assert "already holds" in A.blocked("take_back", _facts(_owner_holds()))
    assert "only the owner" in A.blocked("take_back", _facts(_viewer()))
    assert A.blocked("take_back", _facts(None)) is not None


def test_answer_is_offered_only_while_a_question_waits() -> None:
    """The quiet prompt is the core's, so `^\\ a` has nothing to answer until one arrives, and after the answer."""
    state = _owner_holds()
    assert A.blocked("answer", _facts(state)) == "no question is waiting"
    state.apply_frame({"t": "quiet_prompt", "v": 1, "prompt_id": "q" * 32, "code": "quiet_prompt.kill",
                       "text": "Kill the session?"})
    assert A.blocked("answer", _facts(state)) is None
    state.quiet_answered()
    assert A.blocked("answer", _facts(state)) == "no question is waiting"


def test_hand_to_selected_is_for_the_holder_or_the_owner_and_needs_another_client() -> None:
    """Typing-ownership steps 5 and 7: the holder hands the keyboard on, and the owner reassigns it. A
    viewer who is not the owner has no such right (`not_allowed`). The selection must be another client
    that is still attached; the one that already holds it is refused here too, because `handoff` to the
    current holder is a case the contract leaves open (open item 11) and nothing is sent for it. A view-only
    target stays selectable on purpose: the core answers `view_only` and the dialog shows it."""
    holder = _owner_holds()
    assert A.blocked("hand", _facts(holder, PEER)) is None
    assert A.blocked("hand", _facts(holder, VIEWER)) is None, "the core says view_only, inside the dialog"
    assert "select a client" in A.blocked("hand", _facts(holder, None))
    assert "use Take it" in A.blocked("hand", _facts(holder, ME))
    assert "no longer attached" in A.blocked("hand", _facts(holder, "client-gone"))

    agent = _agent_holds()  # the owner is not the holder: a reassign (step 7)
    assert A.blocked("hand", _facts(agent, VIEWER)) is None
    assert "already holds" in A.blocked("hand", _facts(agent, PEER))

    assert A.blocked("hand", _facts(_peer_holds_owned_by_someone_else(), VIEWER)) is None
    assert "only the keyboard holder or the owner" in A.blocked("hand", _facts(_viewer(), PEER))


def test_put_it_aside_is_for_the_owner_while_somebody_holds_the_keyboard() -> None:
    """Typing-ownership step 6: only an owner's client may revoke (`handoff` to null). With nobody holding
    it there is nothing to put aside, and the contract has no word for a revoke from escrow (open item 11)."""
    assert A.blocked("aside", _facts(_owner_holds())) is None
    assert A.blocked("aside", _facts(_agent_holds())) is None
    assert "only the owner" in A.blocked("aside", _facts(_peer_holds_owned_by_someone_else()))
    escrow = state_of(me=(ME, ME_USER), owner=ME_USER, holder=None, clients=THREE)
    assert "nobody holds" in A.blocked("aside", _facts(escrow))


def test_remove_is_for_the_owner_with_a_client_selected() -> None:
    """Client-remove step 1 and Frontend §9: the owner's kill button. Everyone else would be answered 403
    `forbidden`, so the button is not offered; with no client selected there is nothing to name in the
    confirmation (rule 9)."""
    assert A.blocked("remove", _facts(_owner_holds(), PEER)) is None
    assert A.blocked("remove", _facts(_agent_holds(), PEER)) is None
    assert "select a client" in A.blocked("remove", _facts(_owner_holds(), None))
    assert "only the owner" in A.blocked("remove", _facts(_peer_holds_owned_by_someone_else(), VIEWER))


def test_no_move_of_the_keyboard_is_offered_while_a_hand_off_is_in_flight() -> None:
    """DT4 (b): until the `token` frame answers, a second hand-off would race the first. Take it, Hand to
    selected and Put it aside wait for the answer, and come back when it arrives."""
    state = _owner_holds()
    state.handoff_in_flight = True
    for action_id in ("hand", "aside"):
        assert "in flight" in A.blocked(action_id, _facts(state, PEER)), action_id
    agent = _agent_holds()
    agent.handoff_in_flight = True
    assert "in flight" in A.blocked("take", _facts(agent))

    state.apply_frame(token_frame((PEER, PEER_USER), "handoff"))
    assert A.blocked("aside", _facts(state)) is None


def test_a_refused_hand_off_is_no_longer_in_flight() -> None:
    """Typing-ownership steps 12-13: a refused hand-off moves nothing and no `token` frame follows, so it
    must stop being "in flight" at the refusal, or derived input (DT4 b) and every move button would stay
    blocked for good after one `view_only`."""
    state = _owner_holds()
    state.handoff_in_flight = True
    state.apply_frame(refused_frame("handoff", "view_only"))
    assert not state.handoff_in_flight
    assert A.blocked("hand", _facts(state, VIEWER)) is None


def test_nothing_session_bound_is_available_before_the_first_attach() -> None:
    """Before `attached` there is no client id, no holder, no clients: Who's here and Size have nothing
    to show and a request has nowhere to go. Detach, Fleet and Menu always work (to cancel an attach)."""
    for action_id in ("who", "size", "take", "hand", "aside", "remove", "ask_size"):
        assert A.blocked(action_id, _facts(None)) == "not attached to a session", action_id
    for action_id in ("detach", "fleet", "menu", "auto_size"):
        assert A.blocked(action_id, _facts(None)) is None, action_id


# -- Size (size-control surface rules; typing-ownership step 14) --


def test_ask_for_my_size_is_the_typists_alone_and_says_who_types() -> None:
    """Size-control step 2 and typing-ownership step 14: only the keyboard holder's request counts, and a
    viewer's is answered `not_typist`, so the button is grey and names who types now."""
    assert A.blocked("ask_size", _facts(_owner_holds())) is None
    assert A.blocked("ask_size", _facts(_agent_holds())) == "only the typist can ask; fixture-peer types now"
    escrow = state_of(me=(ME, ME_USER), owner=ME_USER, holder=None, clients=THREE)
    assert A.blocked("ask_size", _facts(escrow)) == "only the typist can ask; nobody types now"


def test_a_clamped_answer_closes_the_button_until_the_window_changes() -> None:
    """Terminal-size §3, size-control surface rules: "a clamped answer is final ... and does not ask again
    until its own window changes". After one the button is grey, and a window change opens it."""
    state = _owner_holds()
    state.note_size_request(30, 10)
    state.apply_frame(size_frame(40, 12, "clamped_floor"))
    assert "clamped" in A.blocked("ask_size", _facts(state))
    state.window_changed()
    assert A.blocked("ask_size", _facts(state)) is None


def test_an_applied_answer_does_not_close_the_button() -> None:
    """Only a *clamped* answer is final: an applied size is just the size now, and asking again for
    another one after a window change is the ordinary case."""
    state = _owner_holds()
    state.note_size_request(100, 30)
    state.apply_frame(size_frame(100, 30, "applied"))
    assert A.blocked("ask_size", _facts(state)) is None
    assert state.last_size_answer == {"asked": (100, 30), "cols": 100, "rows": 30, "reason": "applied"}


def test_a_size_another_client_asked_for_is_not_this_clients_answer() -> None:
    """Size-control step 12: an applied size reaches every client. With no request of ours outstanding it
    is the session's new size and nobody's answer, so the dialog's "last answer" stays what this client
    really asked."""
    state = _owner_holds()
    state.apply_frame(size_frame(90, 30, "applied"))
    assert state.last_size_answer is None
    assert (state.size_cols, state.size_rows) == (90, 30)


# -- the leader's letters (D15, D16) --


def test_the_leader_letters_are_the_tables_own_unless_the_operator_rebinds_one() -> None:
    """`keys.leader_map` rebinds a letter by action name (design §2.4). The default letters are the
    table's; an override replaces exactly one, and an unknown action, a value that is not one printable
    character, or two actions left on one letter is refused naming the key, never ignored (Rule 12)."""
    default = A.leader_keys()
    assert default["detach"] == "d" and default["who"] == "w" and default["size"] == "r"
    assert A.leader_keys({"detach": "q"})["detach"] == "q"
    assert A.action_for_leader_key("q", {"detach": "q"}).id == "detach"
    assert A.action_for_leader_key("d", {"detach": "q"}) is None
    for bad, message in (({"nope": "q"}, "no action of that name"), ({"detach": "qq"}, "one printable"),
                         ({"detach": " "}, "one printable"), ({"detach": "w"}, "both on 'w'")):
        with pytest.raises(A.KeymapError, match=message):
            A.leader_keys(bad)


# -- switching between the sessions open here (design §2.4: `^\ 1`-`^\ 9`, `n`, `p`, `s`) --


def test_next_and_previous_need_another_session_open_and_switch_to_never_does() -> None:
    """`n` and `p` step through the sessions open here: with one open there is nothing to step to, so the Menu
    says why instead of the key doing nothing. Switch to… opens the sidebar, which is how a session that is not
    open yet is reached, so it works with none open."""
    for count, blocked in ((0, True), (1, True), (2, False), (5, False)):
        facts = A.Facts(None, open_sessions=count)
        for action_id in ("next", "prev"):
            assert (A.blocked(action_id, facts) is not None) is blocked, (action_id, count)
        assert A.blocked("switch", facts) is None


def test_detach_is_offered_only_while_a_session_is_open_here() -> None:
    """With nothing open there is nothing to detach: the Menu names that, and the status row offers no Detach."""
    assert A.blocked("detach", A.Facts(None, open_sessions=0)) == "no session is open here"
    assert A.blocked("detach", A.Facts(None, open_sessions=1)) is None


def test_new_on_connection_is_not_offered_on_a_telnet_session() -> None:
    """A telnet connection carries one session (session model §5), so there is nothing to reuse: the action is
    shown unavailable with that reason; on an ssh supplied-login connection it stays available."""
    state = _owner_holds()
    assert A.blocked("new_on_connection", A.Facts(state, target_type="ssh")) is None
    reason = A.blocked("new_on_connection", A.Facts(state, target_type="telnet"))
    assert reason is not None and "one session" in reason


def test_the_new_actions_have_their_own_letters_after_the_leader() -> None:
    """`s` (tmux's own for choose) and the right and left arrows (next, previous: `n` is New session now): reachable with
    the leader, and rebindable by name like the others."""
    keys = A.leader_keys()
    assert (keys["switch"], keys["next"], keys["prev"]) == ("s", "→", "←")
    assert A.leader_keys({"next": "j"})["next"] == "j"
