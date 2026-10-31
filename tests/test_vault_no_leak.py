"""No vault secret outlives its use (phase 12 § B, § C; OPEN-12.2 (b); cred §1.2, §12).

Why it matters (Rule 9): the passphrase, the hash derived from it, its two halves, a target password, a key's text and the
path it was read from are each needed once, then dropped, and a compromised log or state file must not be the place they
were left. The test runs the whole of what handles them (an unlock on `vault_locked`, a save of a login with a key read
from a file, a screen lock and its password) and then reads every place a secret could have been left: each log record at
DEBUG, standard output and error, every file under the TUI's home (the state file included), and a dump of the screen
after each step. It looks for each secret as typed and in the forms a leak takes: UTF-8 hex, and base64 (standard and URL
safe). A secret the TUI never held could pass this test; one it left behind cannot.
Server: `tsbc serve` (`VaultServe`). Every secret is synthetic or C7's published vector.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from test_new_session_login import _gate_up, _open_dialog
from test_screen_lock import Clock, _dump, _locked
from test_vault_unlock import _prompt
from textual.widgets import Button, Input
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.lock import LockScreen
from tsb_tui.vault_flow import save_login
from tsbc import _serve
from vault_support import CHAIN, VaultServe

TARGET_PASSWORD = "FIXTURE-TARGET-PASSWORD-77"
KEY_TEXT = "-----BEGIN OPENSSH PRIVATE KEY-----\nRklYVFVSRS1MRUFLLUNIRUNL\n-----END OPENSSH PRIVATE KEY-----\n"


def _forms(secret: bytes) -> dict[str, bytes]:
    return {"as typed": secret, "hex": secret.hex().encode(),
            "base64": base64.b64encode(secret), "base64url": base64.urlsafe_b64encode(secret)}


def _secrets(key_path: Path) -> dict[str, bytes]:
    return {
        "passphrase": CHAIN.passphrase.encode(),
        "master hash": CHAIN.master_hash, "part 1": CHAIN.part1, "part 2": CHAIN.part2,
        "target password": TARGET_PASSWORD.encode(), "login password": _serve.PASSWORD.encode(),
        "key text": KEY_TEXT.encode(), "key path": str(key_path).encode(),
    }


def _files(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_passphrase_hash_part_password_or_key_is_left_in_a_log_the_state_files_or_a_screen_dump(
        serve, wait_until, caplog, capsys, tmp_path) -> None:
    caplog.set_level(logging.DEBUG)

    async def check() -> list[tuple[str, str]]:
        key_file = tmp_path / "keys" / "id_fixture_leakcheck"
        key_file.parent.mkdir()
        key_file.write_text(KEY_TEXT)
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire, locks={"screen_lock_after": 30}))
        serve_app.hold_credential(_serve.USER, "mine")
        serve_app.map_target(_serve.USER, "normal", "mine")
        dumps: list[str] = []
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            clock = Clock()
            app.idle_clock, app.screen_lock_poll = clock, 0.02
            dialog = await _open_dialog(app, pilot, wait_until, target="normal", target_type="ssh")
            dialog.query_one("#create-button", Button).press()
            await _prompt(app, pilot, wait_until)
            app.screen.query_one("#vault-passphrase", Input).value = CHAIN.passphrase
            await pilot.pause(0.1)
            dumps.append(_dump(app))        # typed, masked, not yet sent
            app.screen.query_one("#vault-continue", Button).press()
            await _gate_up(app, pilot, wait_until)
            dumps.append(_dump(app))
            await pilot.press("escape")
            await wait_until(pilot, lambda: app.workspace() is app.screen, "the workspace")
            outcome = await asyncio.wait_for(
                save_login(app, "fixture-target", "oper", key=KEY_TEXT, reference="mykey"), 20)
            assert outcome.status == "saved"
            outcome = await asyncio.wait_for(
                save_login(app, "normal", "oper", password=TARGET_PASSWORD, reference="mypw"), 20)
            assert outcome.status == "saved"
            dumps.append(_dump(app))
            lock = await _locked(app, pilot, wait_until, clock, 31)
            dumps.append(_dump(app))
            lock.query_one("#lock-password", Input).value = _serve.PASSWORD
            await pilot.pause(0.1)
            dumps.append(_dump(app))        # typed, masked, not yet sent
            await lock.query_one("#lock-password", Input).action_submit()
            await wait_until(pilot, lambda: not isinstance(app.screen, LockScreen), "resumed")
            await pilot.pause(0.2)
            dumps.append(_dump(app))
        home = Path(os.environ["HOME"])
        places = {"log": "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text or ''}" for r in caplog.records).encode(),
                  "stdout/stderr": b"".join(s.encode() for s in capsys.readouterr())}
        for i, dump in enumerate(dumps):
            places[f"screen dump {i}"] = dump.encode()
        places.update({f"file {name}": data for name, data in _files(home).items()})
        found = []
        for what, secret in _secrets(key_file).items():
            if what == "login password":
                continue    # needed to log in; held by the form during the login only (the screen dumps below cover it)
            for form, needle in _forms(secret).items():
                for place, data in places.items():
                    if needle in data:
                        found.append((what, f"{form} in {place}"))
        # the login password is the lock's password too: it must not be left on a screen or in a file
        for place, data in places.items():
            if place.startswith(("screen dump", "file")) and _serve.PASSWORD.encode() in data:
                found.append(("login password", place))
        return found

    leaked = asyncio.run(check())
    assert leaked == []
