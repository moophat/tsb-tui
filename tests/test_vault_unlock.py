"""The untrusted unlock on `vault_locked`, and init (phase 12 § A, § B; vault-unlock steps 1b-6, 9; credential §5.1, §7.4).

Why it matters (Rule 9): this is the only place the TUI handles the vault passphrase. Five things must hold, and a TUI that
breaks any of them either cannot open the vault or leaks what opens it. (1) A create the core answers `vault_locked` asks
the passphrase, derives the halves C7's way from the salt the core serves, sends them in an untrusted unlock and repeats
the create once: proved with C7's own passphrase, salt and halves, against a server that refuses every other pair, as the
core's GCM tag does. (2) Every passphrase prompt, init's included, says that typed keys may be recorded inside a TSB
session (DEFAULT 62). (3) Init shows cred §7.4's warning in plain words before the confirm, and sends nothing until the
passphrase has been typed twice the same. (4) An unlock the core refuses leaves the prompt up with Appendix B's message
and the core's own words for a limit, and never repeats the create; an org credential whose administrator holds no unlock
is shown as that, and the operator's passphrase is never asked for it, since it cannot help. (5) argon2id runs off the UI
thread with progress shown, and the TUI never registers a device or sends a challenge (DEFAULT 64). (6) A wrong passphrase
shows Appendix B's message with the count the core served in `ApiError.detail["attempts_remaining"]` ("Incorrect passphrase.
N attempts remaining."), and the plain message when the count is absent or is no count.
Server: `tsbc serve` (`VaultServe`: a served salt, a refused wrong pair, the passphrase limiter). Every secret is synthetic
or C7's published vector.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from test_new_session_login import _gate_up, _open_dialog
from textual.widgets import Button, Input
from tsbc import _serve
from vault_support import CHAIN, RECORDING_LINE, VaultServe, flat
from workspace_support import fixture_login

from tsb_tui import vault_crypto
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.vault_prompts import InitScreen, PassphraseScreen


def _locked_target(serve_app: VaultServe) -> None:
    """`normal` resolves to a personal credential, and no login holds an unlock."""
    serve_app.hold_credential(_serve.USER, "mine")
    serve_app.map_target(_serve.USER, "normal", "mine")


def _creates(serve_app: VaultServe) -> list:
    return serve_app.calls("POST", "/sessions")


async def _prompt(app, pilot, wait_until, kind=PassphraseScreen):
    await wait_until(pilot, lambda: isinstance(app.screen, kind) and app.screen.is_mounted
                     and bool(app.screen.query("#vault-continue")), "the passphrase prompt")
    return app.screen


def _prompt_panel_text(app) -> str:
    """The prompt panel's own painted words: the workspace behind the modal paints into the same compositor
    rows, so a whole-screen read interleaves its text with the panel's and a wrapped phrase is never one
    contiguous run. The read is the compositor's strips cut to the panel's region, breaks taken out."""
    if not isinstance(app.screen, PassphraseScreen) or not app.screen.is_mounted:
        return ""
    panels = app.screen.query(".panel")
    if not panels:
        return ""
    x, y, width, height = panels.first().region
    rows = app.screen._compositor.render_strips()[y:y + height]
    return flat("\n".join(row.text.rstrip()[x:x + width] for row in rows))


async def _answer(app, pilot, passphrase: str, again: str | None = None) -> None:
    screen = app.screen
    screen.query_one("#vault-passphrase", Input).value = passphrase
    if again is not None:
        screen.query_one("#vault-passphrase-again", Input).value = again
    screen.query_one("#vault-continue", Button).press()
    await pilot.pause()


def test_a_create_answered_vault_locked_unlocks_with_c7s_halves_and_repeats_the_create(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            prompt = await _prompt(app, pilot, wait_until)
            assert RECORDING_LINE in screen_text(app), "every passphrase prompt carries the recording line"
            assert prompt.query_one("#vault-passphrase", Input).password, "the passphrase is masked"
            assert len(_creates(serve_app)) == 1, "the create waits for the unlock"
            await _answer(app, pilot, CHAIN.passphrase)
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the create to be repeated")
            assert serve_app.unlock_bodies == [{"mode": "untrusted", "part1": CHAIN.wire[0], "part2": CHAIN.wire[1]}]
            assert _creates(serve_app)[0] == _creates(serve_app)[1], "the create is repeated as it was"
            order = [(m, p) for m, p, _ in serve_app.requests
                     if (m, p) in (("GET", "/vault/salt"), ("POST", "/vault/unlock"), ("POST", "/sessions"))]
            assert order == [("POST", "/sessions"), ("GET", "/vault/salt"), ("POST", "/vault/unlock"),
                             ("POST", "/sessions")]
            await _gate_up(app, pilot, wait_until)

    asyncio.run(check())


def test_a_vault_locked_again_brings_the_prompt_back_and_an_unlocked_one_never_asks(serve, wait_until) -> None:
    """The unlock is the core's until the vault locks: a second create asks nothing; after "lock now" from anywhere the
    core answers `vault_locked` again and the prompt returns."""

    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, CHAIN.passphrase)
            await _gate_up(app, pilot, wait_until)
            await pilot.press("escape")     # back from the attach gate to the workspace
            await wait_until(pilot, lambda: app.workspace() is app.screen, "the workspace")
            workspace = app.workspace()
            workspace._new_session_dialog("normal", "ssh")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen)
                             and bool(app.screen.query("#create-button")), "a second dialog")
            app.screen.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 3, "the second create")
            assert len(serve_app.unlock_bodies) == 1, "an unlocked login is never asked again"
            await _gate_up(app, pilot, wait_until)
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.workspace() is app.screen, "the workspace")
            serve_app.unlocked.clear()      # the vault locked (idle, the hard cap, or lock now from elsewhere)
            app.workspace()._new_session_dialog("normal", "ssh")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen)
                             and bool(app.screen.query("#create-button")), "a third dialog")
            app.screen.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)

    asyncio.run(check())


def test_a_wrong_passphrase_keeps_the_prompt_with_appendix_bs_message_and_repeats_no_create(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            prompt = await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "not the passphrase")
            await wait_until(pilot, lambda: "Incorrect passphrase" in screen_text(app), "Appendix B's message")
            assert app.screen is prompt and prompt.query_one("#vault-passphrase", Input).value == "", (
                "the prompt stays, and the wrong passphrase is not kept")
            assert RECORDING_LINE in screen_text(app)
            assert len(_creates(serve_app)) == 1, "a refused unlock repeats nothing"
            await _answer(app, pilot, CHAIN.passphrase)
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the create after the right passphrase")

    asyncio.run(check())


def test_a_rate_limited_unlock_shows_the_cores_own_message_and_invents_no_time(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire, attempts=1))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "wrong one")
            await wait_until(pilot, lambda: "Incorrect passphrase" in screen_text(app), "the first refusal")
            await _answer(app, pilot, "wrong two")
            await wait_until(pilot, lambda: "too many passphrase attempts; try again later" in screen_text(app),
                             "the core's own message for the limit")
            assert " in " not in screen_text(app).split("try again later")[1].splitlines()[0], "no time is invented"
            assert len(_creates(serve_app)) == 1

    asyncio.run(check())


def test_an_org_credential_whose_administrator_holds_no_unlock_asks_no_passphrase(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        serve_app.org_creds["org-key"] = {"reference": "org-key", "unlock_class": "user_unlocked", "kind": "password",
                                          "login_name": "svc", "secret": "FIXTURE", "stored_by": _serve.USER}
        serve_app.grants["g-1"] = {"grant_id": "g-1", "user": _serve.PEER, "selectors": ["*"],
                                   "credential_ref": "org-key", "granted_by": _serve.USER, "granted_at": "x"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot, _serve.PEER)
            app.cfg.set_runtime("size.auto_request", True)
            app.workspace()._new_session_dialog("normal", "ssh")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen)
                             and bool(app.screen.query("#create-button")), "the dialog")
            dialog = app.screen
            dialog.query_one("#create-button", Button).press()
            await wait_until(pilot, lambda: "administrator holds no unlock" in flat(screen_text(app)), "the core message")
            assert app.screen is dialog and not isinstance(app.screen, PassphraseScreen)
            assert "your passphrase cannot help" in flat(screen_text(app)).lower()
            assert serve_app.unlock_bodies == [] and not serve_app.calls("GET", "/vault/salt")

    asyncio.run(check())


def test_init_shows_the_warning_and_the_recording_line_and_sends_nothing_until_both_entries_match(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        from tsb_tui.vault_flow import save_login

        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot, _serve.PEER)      # a login with no vault
            task = asyncio.create_task(save_login(app, "normal", "oper", password="FIXTURE-PW", reference="mine"))
            prompt = await _prompt(app, pilot, wait_until, InitScreen)
            # Wait on the screen state itself, never on timing: a fresh mount is queryable before its first
            # paint, so a read straight after `_prompt` can race the warning onto the screen.
            await wait_until(pilot, lambda: all(words in _prompt_panel_text(app) for words in
                                                ("forgotten passphrase cannot be recovered",
                                                 "credentials stored under it are lost",
                                                 "keep it in a password manager"))
                             and RECORDING_LINE in _prompt_panel_text(app),
                             "the warning and the recording line on the prompt")
            text = _prompt_panel_text(app)
            for words in ("forgotten passphrase cannot be recovered", "credentials stored under it are lost",
                          "keep it in a password manager"):
                assert words in text, words
            assert RECORDING_LINE in text
            assert prompt.query_one("#vault-passphrase", Input).password
            assert prompt.query_one("#vault-passphrase-again", Input).password
            await _answer(app, pilot, CHAIN.passphrase, "another passphrase")
            await wait_until(pilot, lambda: "do not match" in screen_text(app), "the mismatch")
            assert serve_app.init_bodies == [], "nothing is sent for entries that differ"
            await _answer(app, pilot, CHAIN.passphrase, CHAIN.passphrase)
            outcome = await asyncio.wait_for(task, 20)
            assert serve_app.init_bodies == [{"part1": CHAIN.wire[0], "part2": CHAIN.wire[1]}]
            assert outcome.status == "saved"

    asyncio.run(check())


def test_a_passphrase_the_profile_refuses_derives_and_sends_nothing(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "bell\u0007")
            await wait_until(pilot, lambda: "cannot be used" in screen_text(app), "the refusal")
            assert serve_app.unlock_bodies == []

    asyncio.run(check())


def test_argon2id_runs_off_the_ui_thread_with_progress_shown(serve, wait_until, screen_text, monkeypatch) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        reached, release = threading.Event(), threading.Event()
        real = vault_crypto.halves_b64
        seen: dict = {}

        def held(passphrase, salt):
            seen["thread"] = threading.current_thread()
            reached.set()
            assert release.wait(10)
            return real(passphrase, salt)

        monkeypatch.setattr(vault_crypto, "halves_b64", held)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, CHAIN.passphrase)
            await wait_until(pilot, reached.is_set, "the derivation to start")
            assert seen["thread"] is not threading.main_thread()
            await wait_until(pilot, lambda: "Deriving" in screen_text(app), "progress while it runs")
            assert serve_app.unlock_bodies == []
            release.set()
            await wait_until(pilot, lambda: len(_creates(serve_app)) == 2, "the create after the derivation")

    asyncio.run(check())


def test_the_tui_never_registers_a_device_or_sends_a_challenge(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, CHAIN.passphrase)
            await _gate_up(app, pilot, wait_until)
        forbidden = {("POST", "/devices"), ("POST", "/devices/challenges"), ("POST", "/auth/bind"),
                     ("POST", "/vault/part2")}
        assert not [r for r in serve_app.requests if (r[0], r[1]) in forbidden]
        assert all(body.get("mode") == "untrusted" for body in serve_app.unlock_bodies)

    asyncio.run(check())


class NoCount(VaultServe):
    """`tsbc serve` whose wrong-passphrase answer carries no count, or a value that is no count."""

    def __init__(self, replace=..., **kwargs) -> None:
        super().__init__(**kwargs)
        self.replace = replace

    def _unlock(self, cookie, user, body):
        answer = super()._unlock(cookie, user, body)
        if answer[0] == 409 and self.replace is ...:
            del answer[2]["error"]["attempts_remaining"]
        elif answer[0] == 409:
            answer[2]["error"]["attempts_remaining"] = self.replace
        return answer


def test_a_wrong_passphrase_shows_the_attempts_remaining_the_core_served(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire, attempts=5))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "not the passphrase")
            await wait_until(pilot, lambda: "Incorrect passphrase. 4 attempts remaining." in flat(screen_text(app)),
                             "Appendix B's message with the count")
            await _answer(app, pilot, "still not it")
            await wait_until(pilot, lambda: "Incorrect passphrase. 3 attempts remaining." in flat(screen_text(app)),
                             "the count the core served the second time")
            assert "4 attempts" not in flat(screen_text(app)), "the old count is gone"

    asyncio.run(check())


def test_a_wrong_passphrase_with_one_attempt_left_reads_in_the_singular(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire, attempts=2))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "not the passphrase")
            await wait_until(pilot, lambda: "Incorrect passphrase. 1 attempt remaining." in flat(screen_text(app)),
                             "the singular for a count of one")
            assert "1 attempts remaining" not in flat(screen_text(app)), "only the count of one is singular"

    asyncio.run(check())


@pytest.mark.parametrize("served", [..., "many", True, -1, 2.5, None], ids=["absent", "text", "bool", "negative", "float", "null"])
def test_a_wrong_passphrase_with_no_count_shows_the_plain_message(served, serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(NoCount(served, salt=CHAIN.salt, accept=CHAIN.wire))
        _locked_target(serve_app)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            await _answer(app, pilot, "not the passphrase")
            await wait_until(pilot, lambda: "Incorrect passphrase." in screen_text(app), "Appendix B's message")
            assert "attempts remaining" not in flat(screen_text(app)) and "attempt" not in flat(screen_text(app))

    asyncio.run(check())
