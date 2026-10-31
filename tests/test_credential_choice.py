"""409 credential_choice_required in New Session (phase 12 § D; Contract §12; session-open step 3; E4 DEFAULT 51).

Why it matters (Rule 9): when two stored credentials resolve for a target the core picks none, so the choice is the
operator's and a surface that picks one, or pre-selects one, has made a login decision for a person. Proved here: (1) the
candidates are read from `ApiError.detail["candidates"]` (the contract client at v0.11.1) and shown as plain text, "yours" for
`personal` and "granted" for `org`, one credential reference carried by two layers shown as two lines; (2) none is chosen
until the operator picks, and no create is sent before then; (3) the create is repeated, as it was, with the picked
`credential_ref` and `credential_layer`, for either layer; a pick that then meets a locked vault goes through the passphrase
prompt and the repeat still carries the pick; (4) a candidate list that is missing or malformed offers nothing and shows
the core's own message, and no create is repeated (never a guess); (5) a pick that meets `org_credential_locked` keeps that
message and never asks the operator's passphrase, which cannot help; (6) a reference or layer carrying markup shows as
literal text. Server: `tsbc serve` (`VaultServe`), which answers the choice itself; a subclass spoils the candidate list for
(4).
"""

from __future__ import annotations

import asyncio

import pytest
from test_new_session_login import _gate_up, _open_dialog
from test_vault_unlock import _answer, _prompt
from textual.widgets import Button, RadioButton, RadioSet
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.vault_prompts import PassphraseScreen
from tsbc import _serve
from vault_support import CHAIN, VaultServe, flat

CHOICE = "#credential-choice"


def _two_layers(serve_app: VaultServe, user: str = _serve.USER, unlock_class: str = "broker_unlocked") -> None:
    """`normal` resolves to a personal credential `netops` and to an org credential of the same name, granted to the user."""
    serve_app.hold_credential(user, "netops")
    serve_app.map_target(user, "normal", "netops")
    serve_app.org_creds["netops"] = {"reference": "netops", "unlock_class": unlock_class, "kind": "password",
                                     "login_name": "svc", "secret": "FIXTURE", "stored_by": _serve.PEER}
    serve_app.grants["g-1"] = {"grant_id": "g-1", "user": user, "selectors": ["*"], "credential_ref": "netops",
                               "granted_by": _serve.PEER, "granted_at": "x"}


def _creates(serve_app: VaultServe) -> list:
    return serve_app.calls("POST", "/sessions")


def _offered(dialog: NewSessionScreen) -> list[str]:
    return [str(button.label) for button in dialog.query(f"{CHOICE} RadioButton")]


async def _choice_up(app, pilot, wait_until) -> NewSessionScreen:
    await wait_until(pilot, lambda: bool(app.screen.query(CHOICE)) and app.screen.query_one(CHOICE).display
                     and bool(app.screen.query(f"{CHOICE} RadioButton")), "the credential choice")
    return app.screen


async def _create_pressed(app, pilot, wait_until) -> NewSessionScreen:
    dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
    dialog.query_one("#create-button", Button).press()
    return dialog


def _button_for(dialog: NewSessionScreen, word: str) -> RadioButton:
    found = [b for b in dialog.query(f"{CHOICE} RadioButton") if word in str(b.label)]
    assert len(found) == 1, f"one line says {word!r}: {_offered(dialog)}"
    return found[0]


async def _pick(app, pilot, wait_until, dialog: NewSessionScreen, word: str) -> None:
    """Click the candidate whose line says `word`, once the layout has given it a place: a click made before the buttons
    are laid out lands on nothing (the full suite once timed out waiting on a pick that was never made), so a miss fails here
    with its own words."""
    button = _button_for(dialog, word)
    await wait_until(pilot, lambda: button.region.width > 0 and button.region.height > 0, f"the {word!r} line to be laid out")
    assert await pilot.click(f"#{button.id}"), f"the click landed on the {word!r} line"


def test_two_candidates_of_one_name_and_two_layers_are_shown_with_no_default_and_nothing_is_sent_until_one_is_picked(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        _two_layers(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await _create_pressed(app, pilot, wait_until)
            dialog = await _choice_up(app, pilot, wait_until)
            lines = _offered(dialog)
            assert len(lines) == 2 and all("netops" in line for line in lines), lines
            assert sorted("yours" in line for line in lines) == [False, True]
            assert sorted("granted" in line for line in lines) == [False, True]
            assert not any("personal" in line or "org" in line.split() for line in lines), (
                "the layer is said as yours and granted")
            assert "yours" in flat(screen_text(app)) and "granted" in flat(screen_text(app)), "it is on the screen"
            radios = dialog.query_one(CHOICE, RadioSet)
            assert radios.pressed_button is None, "no candidate is pre-selected (Contract §12)"
            await pilot.pause(0.3)
            assert len(_creates(serve_app)) == 1, "nothing is created until the operator picks"
            assert isinstance(app.screen, NewSessionScreen), "the dialog stays"

    asyncio.run(check())


@pytest.mark.parametrize(("word", "layer"), [("granted", "org"), ("yours", "personal")])
def test_the_create_is_repeated_with_the_picked_layer(serve, wait_until, word, layer) -> None:
    """Both layers through one passphrase-free path: the personal credential is a mapped one with no secret to open."""

    async def check() -> None:
        serve_app, url = serve(VaultServe())
        _two_layers(serve_app)
        serve_app.creds[_serve.USER]["netops"]["kind"] = "none"        # a personal login that needs no unlock
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await _create_pressed(app, pilot, wait_until)
            dialog = await _choice_up(app, pilot, wait_until)
            await _pick(app, pilot, wait_until, dialog, word)
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the repeated create")
            first, second = _creates(serve_app)
            assert "credential_ref" not in first and "credential_layer" not in first
            assert (second.pop("credential_ref"), second.pop("credential_layer")) == ("netops", layer)
            assert second == first, "the create is repeated as it was, plus the pick"
            await _gate_up(app, pilot, wait_until)

    asyncio.run(check())


def test_a_pick_that_meets_a_locked_vault_goes_through_the_prompt_and_the_repeat_still_carries_it(
        serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _two_layers(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await _create_pressed(app, pilot, wait_until)
            dialog = await _choice_up(app, pilot, wait_until)
            await _pick(app, pilot, wait_until, dialog, 'yours')
            await _prompt(app, pilot, wait_until)
            assert len(_creates(serve_app)) == 2, "the pick is sent, the vault answers locked, the prompt waits"
            await _answer(app, pilot, CHAIN.passphrase)
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 3, "the create after the unlock")
            picks = [(c.get("credential_ref"), c.get("credential_layer")) for c in _creates(serve_app)]
            assert picks == [(None, None), ("netops", "personal"), ("netops", "personal")]
            await _gate_up(app, pilot, wait_until)

    asyncio.run(check())


class SpoiledChoice(VaultServe):
    """`tsbc serve` whose 409 credential_choice_required carries a candidate list the contract does not allow."""

    def __init__(self, spoil, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spoil = spoil

    def _create(self, user, body, cookie=""):
        answer = super()._create(user, body, cookie)
        if answer[0] == 409 and answer[2]["error"]["code"] == "credential_choice_required":
            self.spoil(answer[2]["error"])
        return answer


def _drop(error: dict) -> None:
    del error["candidates"]


def _not_a_list(error: dict) -> None:
    error["candidates"] = {"credential_ref": "netops", "credential_layer": "org"}


def _empty(error: dict) -> None:
    error["candidates"] = []


def _one_only(error: dict) -> None:
    error["candidates"] = error["candidates"][:1]


def _no_layer(error: dict) -> None:
    del error["candidates"][1]["credential_layer"]


def _unknown_layer(error: dict) -> None:
    error["candidates"][1]["credential_layer"] = "team"


def _not_an_object(error: dict) -> None:
    error["candidates"][0] = "netops"


def _ref_not_text(error: dict) -> None:
    error["candidates"][0]["credential_ref"] = 7


@pytest.mark.parametrize("spoil", [_drop, _not_a_list, _empty, _one_only, _no_layer, _unknown_layer, _not_an_object,
                                   _ref_not_text], ids=lambda f: f.__name__.strip("_"))
def test_a_missing_or_malformed_candidate_list_offers_nothing_and_shows_the_cores_message(
        spoil, serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(SpoiledChoice(spoil))
        _two_layers(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _create_pressed(app, pilot, wait_until)
            await wait_until(pilot, lambda: "choose a credential" in flat(screen_text(app)), "the core's message")
            await pilot.pause(0.3)
            assert app.screen is dialog
            assert not list(dialog.query(f"{CHOICE} RadioButton")), "nothing is offered"
            assert not dialog.query(CHOICE) or not dialog.query_one(CHOICE).display
            assert len(_creates(serve_app)) == 1, "and nothing is repeated: the TUI never guesses"
            assert not isinstance(app.screen, PassphraseScreen)
            assert dialog.query_one("#create-button", Button).disabled is False, "the operator can try again"

    asyncio.run(check())


def test_a_pick_answered_org_credential_locked_keeps_its_message_and_never_asks_the_passphrase(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _two_layers(serve_app, unlock_class="user_unlocked")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _create_pressed(app, pilot, wait_until)
            await _choice_up(app, pilot, wait_until)
            await _pick(app, pilot, wait_until, dialog, 'granted')
            await wait_until(pilot, lambda: "administrator holds no unlock" in flat(screen_text(app)), "the core's message")
            assert "your passphrase cannot help" in flat(screen_text(app)).lower()
            assert app.screen is dialog and not isinstance(app.screen, PassphraseScreen)
            assert serve_app.unlock_bodies == [] and not serve_app.calls("GET", "/vault/salt")
            assert len(_creates(serve_app)) == 2

    asyncio.run(check())


def test_a_reference_carrying_markup_shows_as_literal_text(serve, wait_until) -> None:
    """A reference is a name a person reads and a surface renders as text (common.schema.json). The core's own pattern keeps
    markup out of a reference, but the TUI draws what it is given and never interprets it."""

    async def check() -> None:
        def markup(error: dict) -> None:
            error["candidates"][0]["credential_ref"] = "[b]bold[/b]"

        serve_app, url = serve(SpoiledChoice(markup))
        _two_layers(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await _create_pressed(app, pilot, wait_until)
            dialog = await _choice_up(app, pilot, wait_until)
            assert any(line.startswith("[b]bold[/b]") for line in _offered(dialog)), _offered(dialog)

    asyncio.run(check())
