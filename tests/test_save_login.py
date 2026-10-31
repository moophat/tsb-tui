"""Saving a login for a target (phase 12 § C; plan D15, DEFAULT 68; the operator's 2026-10-29 12:15 ruling; OPEN-12.2 (b)).

Why it matters (Rule 9): this is the one function login-at-connect will call, and the only path by which a target
password or private key leaves the TUI towards the vault. What must hold: (1) the steps run in the plan's order and ask
for what they need: the salt is read; an uninitialised vault is offered init first; a login holding no unlock is asked
the passphrase and the store repeated; the store carries the username and the secret exactly once each; then one mapping
call names every picked target. (2) A username is required, so a password entry without one cannot be sent. (3) A failed
mapping after a stored credential is told as exactly that, and the credential stays (listed, and left to map or delete).
(4) A reference the user already uses is refused by the core (O32) and shown; nothing is replaced, nothing mapped. (5) A
private key is pasted into a masked multi-line field or read once from a regular file of at most 16 KiB the user names;
an encrypted key is refused with the core's own message (DEFAULT 11); neither the path nor the text is logged, kept
after the send, or written anywhere (OPEN-12.2 (b)). (6) Cancelling the passphrase ends the save as `cancelled`.
Server: `tsbc serve` (`VaultServe`). Every secret is synthetic.
"""

from __future__ import annotations

import asyncio

from test_vault_unlock import _answer, _prompt
from textual import events
from textual.widgets import Button, Input, RadioButton
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.credentials import KeyField, SaveLoginScreen
from tsb_tui.screens.vault_prompts import InitScreen
from tsb_tui.vault_flow import SaveOutcome, save_login
from tsbc import _serve
from vault_support import CHAIN, RECORDING_LINE, VaultServe
from workspace_support import fixture_login

VAULT_PATHS = ("/vault/salt", "/vault/credentials", "/vault/unlock", "/vault/init", "/profiles/batch")
PASSWORD = "FIXTURE-TARGET-PASSWORD-1"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nRklYVFVSRS1OT1QtQS1LRVk=\n-----END OPENSSH PRIVATE KEY-----\n"


def _vault_calls(serve_app: VaultServe) -> list[tuple[str, str]]:
    """The save's own requests: what it reads and writes, not the credentials screen's list."""
    return [(m, p) for m, p, _ in serve_app.requests if p in VAULT_PATHS and (m, p) != ("GET", "/vault/credentials")]


def _locked_vault_serve() -> VaultServe:
    return VaultServe(salt=CHAIN.salt, accept=CHAIN.wire)


def test_an_uninitialised_vault_is_offered_init_first_and_the_store_and_one_mapping_follow(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot, _serve.PEER)
            task = asyncio.create_task(save_login(app, ["normal", "fixture-target"], "oper", password=PASSWORD,
                                                  reference="mine", label="the lab pair"))
            await _prompt(app, pilot, wait_until, InitScreen)
            await _answer(app, pilot, CHAIN.passphrase, CHAIN.passphrase)
            outcome = await asyncio.wait_for(task, 20)
            assert isinstance(outcome, SaveOutcome)
            assert (outcome.status, outcome.reference, outcome.stored, outcome.mapped) == ("saved", "mine", True, True)
            assert _vault_calls(serve_app) == [
                ("GET", "/vault/salt"), ("POST", "/vault/salt"), ("POST", "/vault/init"),
                ("POST", "/vault/credentials"), ("POST", "/profiles/batch")]
            assert serve_app.calls("POST", "/vault/credentials") == [
                {"reference": "mine", "kind": "password", "login_name": "oper", "secret": PASSWORD,
                 "label": "the lab pair"}]
            assert serve_app.calls("POST", "/profiles/batch") == [
                {"mappings": [{"target_id": "normal", "credential_ref": "mine"},
                              {"target_id": "fixture-target", "credential_ref": "mine"}]}], (
                "one mapping call names every picked target")

    asyncio.run(check())


def test_a_login_holding_no_unlock_is_asked_the_passphrase_and_the_store_is_repeated(
        serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            task = asyncio.create_task(save_login(app, "normal", "oper", password=PASSWORD, reference="mine"))
            await _prompt(app, pilot, wait_until)
            assert RECORDING_LINE in screen_text(app)
            await _answer(app, pilot, CHAIN.passphrase)
            outcome = await asyncio.wait_for(task, 20)
            assert outcome.status == "saved" and outcome.stored and outcome.mapped
            assert _vault_calls(serve_app) == [
                ("GET", "/vault/salt"), ("POST", "/vault/credentials"), ("POST", "/vault/unlock"),
                ("POST", "/vault/credentials"), ("POST", "/profiles/batch")]
            first, second = serve_app.calls("POST", "/vault/credentials")
            assert first == second == {"reference": "mine", "kind": "password", "login_name": "oper",
                                       "secret": PASSWORD}
            assert serve_app.unlock_bodies == [{"mode": "untrusted", "part1": CHAIN.wire[0], "part2": CHAIN.wire[1]}]

    asyncio.run(check())


def test_a_login_already_unlocked_is_never_asked(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            outcome = await asyncio.wait_for(
                save_login(app, "normal", "oper", password=PASSWORD, reference="mine"), 20)
            assert outcome.status == "saved"
            assert serve_app.unlock_bodies == []

    asyncio.run(check())


def test_a_save_without_a_username_cannot_be_sent(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            outcome = await save_login(app, "normal", "", password=PASSWORD, reference="mine")
            assert outcome.status == "error" and not outcome.stored
            assert "username" in outcome.message.lower()
            assert not _vault_calls(serve_app), "nothing reaches the core"
            # and the form refuses it the same way
            await app.workspace().run_action_id("credentials")
            await wait_until(pilot, lambda: bool(app.screen.query("#cred-save")), "the credentials screen")
            app.screen.query_one("#cred-save", Button).press()
            await wait_until(pilot, lambda: isinstance(app.screen, SaveLoginScreen) and bool(app.screen.query("#save-send")),
                             "the form")
            form = app.screen
            form.query_one("#save-targets", Input).value = "normal"
            form.query_one("#save-password", Input).value = PASSWORD
            form.query_one("#save-reference", Input).value = "mine"
            form.query_one("#save-send", Button).press()
            await wait_until(pilot, lambda: "username is required" in screen_text(app).lower(), "the refusal")
            assert not _vault_calls(serve_app)

    asyncio.run(check())


def test_a_failed_mapping_after_a_stored_credential_is_told_and_the_credential_stays(serve, wait_until) -> None:
    class RefusesMapping(VaultServe):
        def vault_http(self, method, path, headers, body, query, user):
            if (method, path) == ("POST", "/profiles/batch"):
                return self._err(422, "invalid_request", "a target is not yours to map")
            return super().vault_http(method, path, headers, body, query, user)

    async def check() -> None:
        serve_app, url = serve(RefusesMapping(salt=CHAIN.salt, accept=CHAIN.wire))
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            outcome = await save_login(app, "normal", "oper", password=PASSWORD, reference="mine")
            assert outcome.status == "error" and outcome.stored and not outcome.mapped
            assert "stored" in outcome.message and "not mapped" in outcome.message
            assert "mine" in serve_app.creds[_serve.USER], "the credential stays"
            assert [c["reference"] for c in app.client.credentials()] == ["mine"]

    asyncio.run(check())


def test_a_reference_already_in_use_is_refused_and_shown_and_nothing_is_mapped(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        serve_app.hold_credential(_serve.USER, "mine", secret="THE-FIRST")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            outcome = await save_login(app, "normal", "oper", password=PASSWORD, reference="mine")
            assert outcome.status == "error" and not outcome.stored
            assert outcome.message == "that reference is in use", "the core's own words"
            assert serve_app.creds[_serve.USER]["mine"]["secret"] == "THE-FIRST", "nothing is replaced"
            assert not serve_app.calls("POST", "/profiles/batch")

    asyncio.run(check())


def test_cancelling_the_passphrase_ends_the_save_as_cancelled(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            task = asyncio.create_task(save_login(app, "normal", "oper", password=PASSWORD, reference="mine"))
            await _prompt(app, pilot, wait_until)
            await pilot.press("escape")
            outcome = await asyncio.wait_for(task, 20)
            assert outcome.status == "cancelled" and not outcome.stored and not outcome.mapped
            assert not serve_app.calls("POST", "/profiles/batch") and not serve_app.unlock_bodies

    asyncio.run(check())


def test_a_key_is_stored_as_kind_key_with_its_text(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            outcome = await save_login(app, "normal", "oper", key=KEY, reference="mykey")
            assert outcome.status == "saved"
            assert serve_app.calls("POST", "/vault/credentials") == [
                {"reference": "mykey", "kind": "key", "login_name": "oper", "secret": KEY}]

    asyncio.run(check())


def test_an_encrypted_key_is_refused_with_the_cores_message(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            outcome = await save_login(app, "normal", "oper", key=KEY + "ENCRYPTED", reference="mykey")
            assert outcome.status == "error" and not outcome.stored
            assert outcome.message == "an encrypted private key is not accepted: decrypt it first"
            assert not serve_app.calls("POST", "/profiles/batch")

    asyncio.run(check())


async def _open_form(app, pilot, wait_until) -> SaveLoginScreen:
    await app.workspace().run_action_id("credentials")
    await wait_until(pilot, lambda: bool(app.screen.query("#cred-save")), "the credentials screen")
    app.screen.query_one("#cred-save", Button).press()
    await wait_until(pilot, lambda: isinstance(app.screen, SaveLoginScreen) and bool(app.screen.query("#save-send")),
                     "the form")
    return app.screen


def _fill(form: SaveLoginScreen, **fields: str) -> None:
    for name, value in fields.items():
        form.query_one(f"#save-{name}", Input).value = value


def test_the_form_reads_a_key_from_a_regular_file_once_and_keeps_neither_path_nor_text(
        serve, wait_until, tmp_path) -> None:
    async def check() -> None:
        key_file = tmp_path / "id_fixture"
        key_file.write_text(KEY)
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            form = await _open_form(app, pilot, wait_until)
            _fill(form, targets="normal", username="oper", reference="mykey")
            form.query_one("#kind-key", RadioButton).value = True
            await pilot.pause()
            _fill(form, **{"key-path": str(key_file)})
            key_file.write_text("REPLACED-AFTER-TYPING")     # read when the save is sent, not when the path is typed
            key_file.write_text(KEY)
            form.query_one("#save-send", Button).press()
            await wait_until(pilot, lambda: bool(serve_app.calls("POST", "/profiles/batch")), "the save")
            assert serve_app.calls("POST", "/vault/credentials")[0]["secret"] == KEY
            await wait_until(pilot, lambda: app.screen is not form, "the form to close")
            for holder in (form, *form.query(Input), *form.query(KeyField)):
                assert not _holds(vars(holder), KEY) and not _holds(vars(holder), str(key_file)), type(holder).__name__

    asyncio.run(check())


def test_a_key_pasted_into_the_masked_field_is_sent_and_dropped(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            form = await _open_form(app, pilot, wait_until)
            _fill(form, targets="normal", username="oper", reference="mykey")
            form.query_one("#kind-key", RadioButton).value = True
            await pilot.pause()
            field = form.query_one("#save-key", KeyField)
            field.focus()
            await pilot.pause()
            field.post_message(events.Paste(KEY))
            await pilot.pause()
            assert "OPENSSH PRIVATE KEY" not in screen_text(app) and "RklYVFVSRS1" not in screen_text(app), "masked"
            assert "•" in screen_text(app)
            form.query_one("#save-send", Button).press()
            await wait_until(pilot, lambda: bool(serve_app.calls("POST", "/profiles/batch")), "the save")
            assert serve_app.calls("POST", "/vault/credentials")[0] == {
                "reference": "mykey", "kind": "key", "login_name": "oper", "secret": KEY}
            assert field.value == "" and not _holds(vars(field), KEY)

    asyncio.run(check())


def test_a_key_path_that_is_not_a_regular_file_of_at_most_16_kib_is_refused_before_anything_is_sent(
        serve, wait_until, screen_text, tmp_path) -> None:
    async def check() -> None:
        big = tmp_path / "big"
        big.write_text("A" * (16 * 1024 + 1))
        small = tmp_path / "small"
        small.write_text("A" * (16 * 1024))      # exactly 16 KiB is allowed
        binary = tmp_path / "binary"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        serve_app, url = serve(_locked_vault_serve())
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            form = await _open_form(app, pilot, wait_until)
            _fill(form, targets="normal", username="oper", reference="mykey")
            form.query_one("#kind-key", RadioButton).value = True
            await pilot.pause()
            for path, words in ((tmp_path, "regular file"), (big, "16 KiB"), (tmp_path / "absent", "cannot read"),
                                (binary, "text")):
                _fill(form, **{"key-path": str(path)})
                form.query_one("#save-send", Button).press()
                await wait_until(pilot, lambda w=words: w in screen_text(app), f"the refusal naming {words}")
                assert not _vault_calls(serve_app), "nothing reaches the core"
            _fill(form, **{"key-path": str(small)})
            form.query_one("#save-send", Button).press()
            await wait_until(pilot, lambda: bool(serve_app.calls("POST", "/vault/credentials")), "16 KiB is accepted")

    asyncio.run(check())


def _holds(obj, needle: str, seen: set | None = None) -> bool:
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return False
    seen.add(id(obj))
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, bytes):
        return needle.encode() in obj
    if isinstance(obj, dict):
        return any(_holds(k, needle, seen) or _holds(v, needle, seen) for k, v in obj.items())
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_holds(item, needle, seen) for item in obj)
    return False
