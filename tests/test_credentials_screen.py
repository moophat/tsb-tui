"""Own credentials: list, delete, map, and saving a login from the inventory browser (phase 12 § C; DEFAULT 42, 59; O30).

Why it matters (Rule 9): the credentials screen is where a user sees what their vault holds and removes it. (1) It lists
references and kinds always (the index) and labels only while this login holds an unlock, as the core serves them. (2) A
reference, a label or a device name is free text a person wrote: markup, a control character or an escape in it must reach
the screen as characters, never as styling or as a command to the terminal (DEFAULT 59). (3) Delete says what it does to
the reference's mappings (O30: the core clears them, audited) and asks once; keeping it sends nothing; deleting needs an
unlock, so a locked login is asked the passphrase and the delete repeated. (4) Map sends one batch call for every picked
target, whether the targets were marked in the inventory browser or typed here. (5) From the inventory browser a target
is picked (or several marked) and the save form opens with them. (6) A mark survives a query that hides its row, and
Map acts on every marked target; the selected row is used only when nothing is marked. Server: `tsbc serve`
(`VaultServe`); data are synthetic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_vault_unlock import _answer, _prompt
from textual.widgets import Button, DataTable, Input, Select
from tsbc import _serve
from vault_support import CHAIN, RECORDING_LINE, VaultServe, flat
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.credentials import CredentialsScreen, MapScreen, SaveLoginScreen
from tsb_tui.screens.fleet_view import TAB_TARGETS

HOSTILE_LABEL = "[bold red]lab[/bold red] \x1b[31mred\x1b[0m [link=https://x.invalid]click[/link]"


async def _open(app, pilot, wait_until) -> CredentialsScreen:
    workspace = await fixture_login(app, pilot)
    await workspace.run_action_id("credentials")
    await wait_until(pilot, lambda: isinstance(app.screen, CredentialsScreen) and bool(app.screen.query("#cred-save"))
                     and app.screen.loaded, "the credentials screen")
    return app.screen


def _rows(screen: CredentialsScreen) -> list[str]:
    table = screen.query_one("#credentials-table", DataTable)
    return ["|".join(str(cell) for cell in table.get_row_at(i)) for i in range(table.row_count)]


def test_the_list_shows_references_and_kinds_and_labels_only_while_unlocked(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "core-sw", kind="password", label="core switches")
        serve_app.hold_credential(_serve.USER, "lab-key", kind="key")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot, wait_until)
            # The list renders in the locked state after it opens; read it once the references and kinds are laid
            # out, so the assertion reads the rendered locked list, not a half-built table. The label is not in this
            # predicate: the assertion below reports a label shown while locked with its own message.
            await wait_until(pilot, lambda: all(t in screen_text(app) for t in ("core-sw", "lab-key", "password", "key")),
                             "the locked list rendered (references, kinds)", 30.0)
            text = screen_text(app)
            assert "core-sw" in text and "lab-key" in text and "password" in text and "key" in text
            assert "core switches" not in text, "a label is shown only while this login holds an unlock"
            serve_app.unlocked.update(serve_app.logins)
            screen.query_one("#cred-refresh", Button).press()
            # The list re-renders after the unlock; read the label once it is on screen.
            await wait_until(pilot, lambda: "core switches" in screen_text(app), "the label once unlocked", 30.0)

    asyncio.run(check())


def test_free_text_renders_as_literal_characters(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "hostile", label=HOSTILE_LABEL)
        serve_app.register_device(_serve.USER, "A" * 42 + "A", "[red]laptop[/red]\x1b]0;owned\x07")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            await fixture_login(app, pilot)
            serve_app.unlocked.update(serve_app.logins)
            await app.workspace().run_action_id("credentials")
            await wait_until(pilot, lambda: isinstance(app.screen, CredentialsScreen) and app.screen.loaded, "list")
            await wait_until(pilot, lambda: "[bold red]lab[/bold red]" in screen_text(app), "the label as characters")
            text = screen_text(app)
            assert "[link=https://x.invalid]click[/link]" in text
            assert "\x1b" not in text and "\x07" not in text, "a control character never reaches the terminal"
            assert "␛" in text, "it is shown as its picture"
            await pilot.press("escape")
            await app.workspace().run_action_id("devices")
            await wait_until(pilot, lambda: "[red]laptop[/red]" in screen_text(app), "the device name as characters")
            assert "\x1b" not in screen_text(app) and "\x07" not in screen_text(app)

    asyncio.run(check())


def test_delete_says_what_it_does_to_mappings_and_asks_once(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe(salt=CHAIN.salt, accept=CHAIN.wire))
        serve_app.hold_credential(_serve.USER, "mine")
        serve_app.map_target(_serve.USER, "normal", "mine")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot, wait_until)
            screen.query_one("#cred-delete", Button).press()
            await wait_until(pilot, lambda: "cannot be undone" in flat(screen_text(app)), "the confirmation")
            text = flat(screen_text(app))
            assert "mine" in text and "clears every target mapping that names it" in text and "cannot be undone" in text
            app.screen.query_one("#delete-keep", Button).press()
            await wait_until(pilot, lambda: app.screen is screen, "back on the list")
            assert not serve_app.calls("DELETE", "/vault/credentials/mine") and "mine" in serve_app.creds[_serve.USER]
            # delete, the login holds no unlock: the passphrase is asked (with the recording line), the delete repeated
            screen.query_one("#cred-delete", Button).press()
            await wait_until(pilot, lambda: bool(app.screen.query("#delete-confirm")), "the confirmation")
            app.screen.query_one("#delete-confirm", Button).press()
            await _prompt(app, pilot, wait_until)
            assert RECORDING_LINE in screen_text(app)
            await _answer(app, pilot, CHAIN.passphrase)
            await wait_until(pilot, lambda: "mine" not in serve_app.creds.get(_serve.USER, {}), "the credential gone")
            assert serve_app.profiles.get(_serve.USER, {}) == {}, "the core cleared the mapping"
            await wait_until(pilot, lambda: app.screen is screen and _rows(screen) == [], "the refreshed list")

    asyncio.run(check())


def test_map_sends_one_batch_call_for_the_typed_targets(serve, wait_until) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "mine")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot, wait_until)
            screen.query_one("#credentials-table", DataTable).move_cursor(row=0)
            screen.query_one("#cred-map", Button).press()
            await wait_until(pilot, lambda: isinstance(app.screen, MapScreen) and bool(app.screen.query("#map-send")),
                             "the map screen")
            app.screen.query_one("#map-targets", Input).value = "normal fixture-target"
            app.screen.query_one("#map-send", Button).press()
            await wait_until(pilot, lambda: bool(serve_app.calls("POST", "/profiles/batch")), "the mapping")
            assert serve_app.calls("POST", "/profiles/batch") == [
                {"mappings": [{"target_id": "normal", "credential_ref": "mine"},
                              {"target_id": "fixture-target", "credential_ref": "mine"}]}]

    asyncio.run(check())


async def _targets_tab(app, pilot, wait_until):
    workspace = await fixture_login(app, pilot)
    workspace.open_fleet_view(TAB_TARGETS)
    view = workspace.fleet_view
    await wait_until(pilot, lambda: len(view.layout.rows) >= len(_serve.TARGETS)
                     and view.layout.rows[0][0] == "targets", "the targets")
    return workspace, view


async def _select(pilot, view, target_id: str) -> None:
    for _ in range(len(view.layout.rows) + 1):
        if view.selected == ("targets", target_id):
            return
        await pilot.press("down")
    raise AssertionError(f"{target_id} could not be selected")


def test_from_the_inventory_browser_a_picked_target_opens_the_save_form_with_it(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        _serve_app, url = serve(VaultServe())
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _workspace, view = await _targets_tab(app, pilot, wait_until)
            await _select(pilot, view, "normal")
            await pilot.press("l")
            await wait_until(pilot, lambda: isinstance(app.screen, SaveLoginScreen) and bool(app.screen.query("#save-send")),
                             "the save form")
            assert app.screen.query_one("#save-targets", Input).value == "normal"

    asyncio.run(check())


async def _map_from_marks(app, pilot, wait_until) -> str:
    """Press `p` on the Targets tab and read the targets the map form was opened with."""
    await pilot.press("p")
    await wait_until(pilot, lambda: isinstance(app.screen, MapScreen) and bool(app.screen.query("#map-targets")),
                     "the map screen")
    return app.screen.query_one("#map-targets", Input).value


def test_a_query_that_excludes_a_marked_target_leaves_both_marks_and_map_acts_on_both(
        serve, wait_until, screen_text) -> None:
    """#494 Qodo 4: a mark is the operator's, not the page's. `aa-shell` is marked, then `two-client`; the query
    `tag:scenario` answers without `aa-shell`, so the title still counts two marks and Map names both — the listed
    mark first, in the order listed, then the mark the query hid — never the row the selection happens to be on."""

    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "mine")
        serve_app.targets["aa-shell"] = {"id": "aa-shell", "type": "ssh", "name": "aa shell", "group": "lab",
                                         "tags": ["shell"], "discovered_by": "manual", "last_seen": _serve.SEEN,
                                         "host": "192.0.2.50", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace, view = await _targets_tab(app, pilot, wait_until)
            await _select(pilot, view, "aa-shell")
            await pilot.press("space")
            await _select(pilot, view, "two-client")
            await pilot.press("space")
            workspace._run_query("tag:scenario")  # `aa-shell` is not tagged `scenario`: the query hides its row
            await wait_until(pilot, lambda: [row[1] for row in view.layout.rows] ==
                             sorted(rec["id"] for rec in _serve.TARGETS.values() if "scenario" in rec["tags"]),
                             "the filtered rows")
            assert "2 marked" in screen_text(app), "the count kept the mark the query hid"
            assert await _map_from_marks(app, pilot, wait_until) == "two-client aa-shell"

    asyncio.run(check())


def test_marks_outside_the_loaded_rows_are_acted_on_and_the_selected_row_is_never_a_stand_in(
        serve, wait_until) -> None:
    """#494 Qodo 4: `type:telnet` leaves no mark in the rows shown, so the old code acted on the selected row
    (`telnet-scenario`) alone. The marks are the action: both of them, sorted by id, and the selected row not at all."""

    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "mine")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            workspace, view = await _targets_tab(app, pilot, wait_until)
            await _select(pilot, view, "fixture-target")
            await pilot.press("space")
            await _select(pilot, view, "normal")
            await pilot.press("space")
            workspace._run_query("type:telnet")  # no mark is in the rows that answer
            await wait_until(pilot, lambda: [row[1] for row in view.layout.rows] == ["telnet-scenario"],
                             "the filtered rows")
            assert view.selected == ("targets", "telnet-scenario"), "the selection moved to the only row shown"
            value = await _map_from_marks(app, pilot, wait_until)
            assert value == "fixture-target normal", "every marked target, never the selected row"

    asyncio.run(check())


def test_with_no_marks_the_selected_row_is_the_target_the_form_opens_with(serve, wait_until) -> None:
    """The unchanged half of the ruling: nothing marked, `p` concerns the selected row."""

    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "mine")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _workspace, view = await _targets_tab(app, pilot, wait_until)
            await _select(pilot, view, "normal")
            assert await _map_from_marks(app, pilot, wait_until) == "normal"

    asyncio.run(check())


def test_marking_targets_in_the_inventory_browser_maps_them_in_one_call(serve, wait_until, screen_text) -> None:
    async def check() -> None:
        serve_app, url = serve(VaultServe())
        serve_app.hold_credential(_serve.USER, "mine")
        app = TsbTuiApp(url)
        async with app.run_test(size=(140, 40)) as pilot:
            _workspace, view = await _targets_tab(app, pilot, wait_until)
            await _select(pilot, view, "fixture-target")
            await pilot.press("space")
            await _select(pilot, view, "normal")      # the list is in id order: `normal` is below `fixture-target`
            await pilot.press("space")
            assert "2 marked" in screen_text(app)
            await pilot.press("p")
            await wait_until(pilot, lambda: isinstance(app.screen, MapScreen) and bool(app.screen.query("#map-send")),
                             "the map screen")
            screen = app.screen
            assert set(screen.query_one("#map-targets", Input).value.split()) == {"normal", "fixture-target"}
            await wait_until(pilot, lambda: screen.loaded, "the credential list")
            screen.query_one("#map-reference", Select).value = "mine"
            screen.query_one("#map-send", Button).press()
            await wait_until(pilot, lambda: bool(serve_app.calls("POST", "/profiles/batch")), "the mapping")
            call, = serve_app.calls("POST", "/profiles/batch")
            assert {m["target_id"] for m in call["mappings"]} == {"normal", "fixture-target"}
            assert {m["credential_ref"] for m in call["mappings"]} == {"mine"}

    asyncio.run(check())


def test_read_key_file_without_o_noctty_or_o_nonblock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """P3: read_key_file uses getattr for O_NOCTTY and O_NONBLOCK; FIFO/non-regular refusal stays."""
    import os

    from tsb_tui.screens.credentials import read_key_file

    monkeypatch.delattr(os, "O_NOCTTY", raising=False)
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)

    key_file = tmp_path / "id_rsa"
    key_file.write_text("TEST-PRIVATE-KEY")
    text, err = read_key_file(str(key_file))
    assert text == "TEST-PRIVATE-KEY"
    assert err is None

    # FIFO / non-regular refusal stays
    non_reg, err = read_key_file(str(tmp_path))
    assert non_reg is None
    assert err == "That path is not a regular file."

