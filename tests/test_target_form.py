"""Target writes use the published client and fixture server; refusals preserve the editor's work."""
from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, Input, RadioButton, Static
from tsbc._serve import ServeApp
from workspace_support import fixture_login

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.fleet_view import TAB_TARGETS
from tsb_tui.screens.target_form import (
    CREDENTIAL_NOTE,
    ConfirmTargetDeleteScreen,
    TargetFormScreen,
)

# The condition, not the bound, is what each test proves; 60 s bounds a wait on a loaded CI runner.
TARGET_FORM_WAIT_S = 60.0


async def wait_for_target_workers(app, pilot, step):
    deadline = asyncio.get_event_loop().time() + TARGET_FORM_WAIT_S
    while True:
        await pilot.pause()
        if app.workers:
            remaining = max(0.1, deadline - asyncio.get_event_loop().time())
            try:
                await asyncio.wait_for(app.workers.wait_for_complete(), remaining)
            except TimeoutError as exc:
                raise AssertionError(f"{step}: target workers did not complete within {TARGET_FORM_WAIT_S} s") from exc
            await pilot.pause()
        else:
            break


async def open_targets(app, pilot, wait_until):
    screen = await fixture_login(app, pilot)
    screen.open_fleet_view(TAB_TARGETS)
    await wait_until(pilot, lambda: screen.targets.fetched, "targets", timeout_s=TARGET_FORM_WAIT_S)
    return screen, screen.fleet_view


def test_create_edit_and_delete_with_confirmation(serve, wait_until, screen_text):
    """A person's keys write the target, preserve its immutable id and confirm before deleting it."""
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, view = await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "new form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            assert CREDENTIAL_NOTE in str(form.query_one("#target-credential-note", Static).content)
            assert not form.query("#target-credential-note Input, #target-credential-note Button")
            form.query_one("#target-id", Input).value = "form-target"
            form.query_one("#target-host", Input).value = "router.example"
            form.query_one("#target-tags", Input).value = " prod , core "
            form.query_one("#target-user", Input).value = "operator"
            form.query_one("#target-save", Button).press()
            await wait_for_target_workers(app, pilot, "created row")
            await wait_until(pilot, lambda: app.screen is screen and view.selected_session == "form-target", "created row selected", timeout_s=TARGET_FORM_WAIT_S)
            assert app.screen is screen and view.selected_session == "form-target"
            assert server.target_definitions["form-target"] == {
                "id": "form-target", "type": "ssh", "host": "router.example", "tags": ["prod", "core"], "user": "operator"}
            await pilot.press("e")
            await wait_for_target_workers(app, pilot, "edit form")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "edit form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            assert form.query_one("#target-id", Input).disabled
            assert form.query_one("#target-user", Input).value == "operator"
            form.query_one("#target-name", Input).value = "Renamed"
            form.query_one("#target-telnet", RadioButton).value = True
            await pilot.pause()
            assert not form.query_one("#target-account").display
            form.query_one("#target-save", Button).press()
            await wait_for_target_workers(app, pilot, "edited target")
            await wait_until(pilot, lambda: app.screen is screen and server.targets["form-target"].get("name") == "Renamed", "edited target", timeout_s=TARGET_FORM_WAIT_S)
            assert "user" not in server.target_definitions["form-target"]
            assert ("GET", "/targets/form-target/definition") in [(m, p) for m, p, _ in server.requests]
            await wait_until(pilot, lambda: view.selected_session == "form-target", "edited row", timeout_s=TARGET_FORM_WAIT_S)
            await pilot.press("d")
            await wait_for_target_workers(app, pilot, "delete confirmation")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmTargetDeleteScreen), "delete confirmation", timeout_s=TARGET_FORM_WAIT_S)
            assert "form-target" in screen_text(app) and "Open sessions stay open" in screen_text(app)
            await pilot.press("escape")
            assert "form-target" in server.targets
            await pilot.press("d")
            await wait_for_target_workers(app, pilot, "delete confirmation again")
            await wait_until(pilot, lambda: isinstance(app.screen, ConfirmTargetDeleteScreen), "delete confirmation again", timeout_s=TARGET_FORM_WAIT_S)
            app.screen.query_one("#target-delete-confirm", Button).press()
            await wait_for_target_workers(app, pilot, "deleted target")
            await wait_until(pilot, lambda: "form-target" not in server.targets and screen.targets.row("form-target") is None, "deleted target", timeout_s=TARGET_FORM_WAIT_S)
    asyncio.run(check())


def test_duplicate_keeps_every_field_and_core_message(serve, wait_until):
    """A 409 must not discard the target a person is still editing."""
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 40)) as pilot:
            await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            form.fill({"id": "normal", "type": "ssh", "host": "example", "name": "Keep me", "group": "g",
                       "tags": ["a", "b"], "port": 2222, "user": "account"})
            before = form.definition()
            form.query_one("#target-save", Button).press()
            await wait_until(pilot, lambda: bool(form.query_one("#target-error", Static).content), "duplicate refusal", timeout_s=TARGET_FORM_WAIT_S)
            assert app.screen is form and form.definition() == before
            assert str(form.query_one("#target-error", Static).content) == "a source already holds that target id"
    asyncio.run(check())


@pytest.mark.parametrize("permission", [True, False])
def test_keys_follow_auth_me_permission(serve, wait_until, permission):
    """The login response grants nothing: the post-login Me permissions control keys and click hints."""
    class Policy(ServeApp):
        @staticmethod
        def _permissions(user):
            return ["target.edit"] if permission else []

    async def check():
        server, url = serve(Policy())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, _ = await open_targets(app, pilot, wait_until)
            hints = screen._fleet_view_keys_target().hints
            assert {"n", "e", "d"}.issubset({hint.key for hint in hints}) == permission
            assert any(m == "GET" and p == "/auth/me" for m, p, _ in server.requests)
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, TargetFormScreen) == permission
    asyncio.run(check())


def test_other_source_disables_edit_and_delete_with_reason(serve, wait_until):
    """A manual/discovery row cannot launch either write, and says why in the status line."""
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, view = await open_targets(app, pilot, wait_until)
            view.selected = ("targets", "normal")
            for key in ("e", "d"):
                await pilot.press(key)
                assert app.screen is screen
                assert "not edited through TSB" in screen._targets_query_error
            assert not any(p.endswith("/definition") or m in ("PUT", "DELETE") for m, p, _ in server.requests)
    asyncio.run(check())


@pytest.mark.parametrize("field,value", [("port", "0"), ("port", "65536"), ("port", "abc"), ("tags", "a,,b"), ("tags", " ")])
def test_invalid_port_or_empty_tag_is_refused_without_write(serve, wait_until, field, value):
    """Local form validation refuses invalid ports and empty comma-separated tags before a POST."""
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 40)) as pilot:
            await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            form.fill({"id": "validate", "host": "example", "type": "ssh"})
            form.query_one(f"#target-{field}", Input).value = value
            form.query_one("#target-save", Button).press()
            await wait_until(pilot, lambda: bool(form.query_one("#target-error", Static).content), "validation", timeout_s=TARGET_FORM_WAIT_S)
            assert not any(m == "POST" and p == "/targets" for m, p, _ in server.requests)
    asyncio.run(check())


def test_hot_policy_refusal_keeps_the_form(serve, wait_until):
    """Keys are a guardrail: a revoked permission still displays the core's 403 message."""
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 40)) as pilot:
            await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            form.fill({"id": "revoked", "host": "example", "type": "ssh"})
            server._permissions = lambda user: []
            form.query_one("#target-save", Button).press()
            await wait_until(pilot, lambda: bool(form.query_one("#target-error", Static).content), "403", timeout_s=TARGET_FORM_WAIT_S)
            assert str(form.query_one("#target-error", Static).content) == "a session or target permission is required"
            assert form.query_one("#target-id", Input).value == "revoked"
    asyncio.run(check())


def test_target_edit_refusal_keeps_the_form(serve, wait_until):
    """With every other permission kept, a missing target.edit still refuses the write and keeps the form."""
    async def check():
        server, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 40)) as pilot:
            await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form", timeout_s=TARGET_FORM_WAIT_S)
            form = app.screen
            form.fill({"id": "revoked-edit", "host": "example", "type": "ssh"})
            server._permissions = lambda user: [p for p in ServeApp._permissions(user) if p != "target.edit"]
            form.query_one("#target-save", Button).press()
            await wait_until(pilot, lambda: bool(form.query_one("#target-error", Static).content), "403", timeout_s=TARGET_FORM_WAIT_S)
            assert str(form.query_one("#target-error", Static).content) == "target.edit is required"
            assert form.query_one("#target-id", Input).value == "revoked-edit"
    asyncio.run(check())


def test_saved_target_is_selected_after_it_leaves_the_query(serve, wait_until):
    """Saving still selects the changed row when its type no longer matches the opening query."""
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, view = await open_targets(app, pilot, wait_until)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form", timeout_s=TARGET_FORM_WAIT_S)
            app.screen.fill({"id": "query-target", "type": "ssh", "host": "example"})
            app.screen.query_one("#target-save", Button).press()
            await wait_for_target_workers(app, pilot, "new row")
            await wait_until(pilot, lambda: app.screen is screen and view.selected_session == "query-target", "new row selected", timeout_s=TARGET_FORM_WAIT_S)
            assert app.screen is screen and view.selected_session == "query-target"
            screen._run_query("type:ssh")
            await wait_until(pilot, lambda: screen.targets.query.type == "ssh", "filtered rows", timeout_s=TARGET_FORM_WAIT_S)
            await pilot.press("e")
            await wait_for_target_workers(app, pilot, "editor")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "editor", timeout_s=TARGET_FORM_WAIT_S)
            app.screen.query_one("#target-telnet", RadioButton).value = True
            app.screen.query_one("#target-save", Button).press()
            await wait_for_target_workers(app, pilot, "saved row outside old query")
            await wait_until(pilot, lambda: app.screen is screen and screen.targets.row("query-target") is not None
                             and screen.targets.row("query-target").type == "telnet", "saved row outside old query",
                             timeout_s=TARGET_FORM_WAIT_S)
            assert view.selected_session == "query-target"
    asyncio.run(check())


def test_saved_target_selection_survives_relayout_before_fleet_flush(serve, wait_until, monkeypatch):
    """A delayed fleet rebuild must not let a resize discard the saved selection."""
    async def check():
        _, url = serve()
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen, view = await open_targets(app, pilot, wait_until)
            screen._stop_targets_timer()
            original_reply = screen._targets_reply
            dirty = screen._mark_fleet_dirty
            interleaved = False

            def reply(*args):
                nonlocal interleaved
                if screen._target_select != "delayed-target":
                    return original_reply(*args)
                # Hold the after-refresh model rebuild until a resize has laid out
                # the view using its previous rows, as on a slow event loop.
                with monkeypatch.context() as patch:
                    patch.setattr(screen, "_mark_fleet_dirty", lambda: None)
                    original_reply(*args)
                    assert view.selected_session == "delayed-target"
                    assert screen._target_select is None
                    view._relayout()
                    interleaved = True
                dirty()

            monkeypatch.setattr(screen, "_targets_reply", reply)
            await pilot.press("a")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "form")
            app.screen.fill({"id": "delayed-target", "type": "ssh", "host": "example"})
            app.screen.query_one("#target-save", Button).press()
            await wait_for_target_workers(app, pilot, "saved row")
            await wait_until(pilot, lambda: interleaved and screen.targets.row("delayed-target") is not None,
                             "delayed reply")
            screen._sync_fleet_widgets()
            assert view.selected_session == "delayed-target"
    asyncio.run(check())
