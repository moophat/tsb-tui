"""P4 — searchable New session and add target (L1-open-direct,
L1-new-target, L1-open-refusals, the ask for a target with a session).

Every case drives the app with keys through Pilot against the contract's fixture server (a test double, never a
broker or sshd), asserts the exact ruled words in the painted screen, and keeps the two-size SVG baselines under
tests/snapshots/redesign-p4/ separate from those assertions. The search is the P3 literal name/host-text rule:
nothing is resolved, nothing is asked of the broker beyond `GET /targets` pages, and no target id is asked of the
operator; Enter uses the row entity's internal id.

AI-Model: strata/strata
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest
from input_server import InputRecordingApp
from stub_server import SlowChunkedApp, StubServer
from textual.widgets import Input, RadioButton, Select
from tsb_contract_client import Client
from tsbc import _serve
from vault_support import VaultServe
from workspace_support import fixture_login, login, open_here, open_new_session

from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.new_session import NewSessionScreen
from tsb_tui.screens.target_form import TargetFormScreen


def _creates(serve_app) -> list[dict]:
    return [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/sessions")]


def _target_writes(serve_app) -> list[tuple[str, str]]:
    return [(method, path) for method, path, _b in serve_app.requests
            if method in ("POST", "PUT", "DELETE") and path.startswith("/targets")]


def _get_by_id(serve_app) -> list[str]:
    return [path for method, path, _b in serve_app.requests
            if method == "GET" and path.startswith("/targets/") and path != "/targets"]


def _screen_lines(app) -> list[str]:
    return [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]


def _joined(app) -> str:
    return "\n".join(_screen_lines(app))


def _position(app) -> str:
    """The painted Targets position line, read from the widget so a repaint is observed without a render-strip race."""
    return str(app.screen.query_one("#new-session-position").render())


async def _login_and_open(app, pilot, wait_until, *, user=None):
    await fixture_login(app, pilot, user)
    await pilot.press("ctrl+backslash", "F", "tab")
    await pilot.pause()
    await pilot.press("n")
    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                     "New session to open")


async def _p4_snapshot(app, pilot, name):
    # Settle the frame before export: a focused control's highlight and any pending refresh finish their layout
    # in the first pause, so the frozen frame is the resting one, not a mid-refresh snapshot. This keeps the
    # credential baselines (a focused RadioSet) deterministic run to run without changing what a person sees.
    await pilot.pause()
    svg = re.sub(r"terminal-\d+", "terminal-p4", app.export_screenshot(title="P4", simplify=True))
    path = Path(__file__).parent / "snapshots" / "redesign-p4" / f"{name}.svg"
    if os.environ.get("UPDATE_P4_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
    assert path.read_text() == svg


def test_search_matches_literal_name_and_host_text(serve, wait_until):
    """L1-filter-position: typing filters the P3 inventory against literal name and host field text, including IP
    text; nothing is resolved and nothing new is asked of the broker."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            before = len([p for m, p, _ in served.requests if p.startswith("/targets")])
            lines = _screen_lines(app)
            assert not any("telnet scenario" in line for line in lines), lines
            await pilot.press(*"telnet")
            await pilot.pause()
            lines = _screen_lines(app)
            assert any("telnet scenario" in line for line in lines), lines
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press(*"192.0.2.30")
            await pilot.pause()
            lines = _screen_lines(app)
            assert any("telnet scenario" in line for line in lines), (lines, "host text must match")
            asked = [p for m, p, _ in served.requests if p.startswith("/targets")]
            assert len(asked) == before, f"the search asked the broker: {asked[before:]}"
    asyncio.run(check())


def test_match_beyond_the_first_page_shows_with_the_position(serve, wait_until):
    """L1-filter-position: every published page is loaded, so a match on a late page shows; the position
    counts within the filtered results and follows P3's title form."""

    async def check():
        served, url = serve(InputRecordingApp())
        for i in range(600):
            tid = f"bulk-{i:04d}"
            served.targets[tid] = {"id": tid, "type": "ssh", "name": tid, "group": "lab", "tags": [],
                                   "discovered_by": "manual", "last_seen": "2026-10-17T08:30:00Z",
                                   "host": "192.0.2.50", "port": 22}
        served.targets["late-match"] = {"id": "late-match", "type": "ssh", "name": "late match",
                                        "group": "core-net", "tags": ["rtr"], "discovered_by": "manual",
                                        "last_seen": "2026-10-17T08:30:00Z", "host": "198.51.100.7", "port": 22}
        app = TsbTuiApp(url)
        async with app.run_test(size=(160, 45)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) == 609
                             and not app.workspace().targets.next_cursor, "every page", timeout_s=60.0)
            await pilot.press(*"late match")
            await pilot.pause()
            lines = _screen_lines(app)
            assert any("late match" in line for line in lines), lines
            title = next((line for line in lines if "Targets ·" in line), "")
            assert "1/1 of 609 · 'late match'" in title, title
    asyncio.run(check())


def test_enter_on_a_target_creates_at_once_when_there_is_one_way(serve, wait_until):
    """L1-open-direct: an ssh target with a stored credential and no live connection has one way to open; Enter on
    its row creates at once, `connection: new`, no login, the creator's size."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            app.cfg.set_runtime("size.auto_request", True)
            await pilot.press(*"normal")
            await pilot.pause()
            lines = _screen_lines(app)
            assert any("▸" in line and "normal" in line for line in lines), lines
            before = len(_creates(served))
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the create")
            body = _creates(served)[-1]
            assert body["target_id"] == "normal" and body["connection"] == "new", body
            assert "login" not in body and "connection_id" not in body, body
    asyncio.run(check())


def test_enter_on_a_telnet_target_creates_new_at_once(serve, wait_until):
    """DEFAULT 22: a telnet connection carries one session, so there is one way and Enter creates `new` at once."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"telnet scenario")
            await pilot.pause()
            before = len(_creates(served))
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the create")
            body = _creates(served)[-1]
            assert body["target_id"] == "telnet-scenario" and body["connection"] == "new", body
            assert "connection_id" not in body, body
    asyncio.run(check())


def test_a_live_connection_shows_the_choice_and_nothing_is_chosen_for_the_operator(serve, wait_until):
    """L1-open-direct + §15.3: a live connection of this login to the target asks the operator; Enter sends
    nothing by itself, and no option is preselected."""

    async def check():
        served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        client.create_session("normal", "new", "xterm-256color", 80, 24)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"normal")
            await pilot.pause()
            before = len(_creates(served))
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert len(_creates(served)) == before, "Enter sent a create without asking"
            lines = _screen_lines(app)
            joined = "\n".join(lines)
            assert "another session" in joined and "new connection" in joined, lines
            # Nothing is chosen for the operator: no radio pressed for reuse, no picker value, nothing sent.
            assert app.screen.query_one("#connection-picker").value is Select.NULL, lines
    asyncio.run(check())


def test_enter_on_a_target_you_have_a_session_on_asks_going_first(serve, wait_until):
    """Enter on a target you have a session on asks, with 'go to session 1' listed first and
    highlighted; choosing it brings that session forward and creates nothing."""

    async def check():
        served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        sid = client.create_session("normal", "new", "xterm-256color", 80, 24)["session"]["id"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await pilot.press("ctrl+backslash", "F", "tab")
            await pilot.pause()
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                             "New session to open")
            await pilot.press(*"normal")
            await pilot.pause()
            before = len(_creates(served))
            await pilot.press("enter")
            await pilot.pause(0.3)
            lines = _screen_lines(app)
            joined = "\n".join(lines)
            go = joined.find("go to session 1")
            another = joined.find("another session")
            new = joined.find("new connection")
            assert go >= 0 and another >= 0 and new >= 0, lines
            assert go < another < new, lines
            assert "▸ go to session 1" in joined, f"'go to session 1' is not highlighted: {lines}"
            assert len(_creates(served)) == before, "the ask sent a create by itself"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert len(_creates(served)) == before, "going to the session created something"
            assert app.screen.__class__.__name__ == "TerminalScreen", app.screen
            assert app.workspace().front is not None and app.workspace().front.session_id == sid
    asyncio.run(check())


def test_no_match_add_row_opens_the_form_prefilled_and_continues(serve, wait_until):
    """L1-new-target: the no-match last row is exactly `+ Add '<query>' as a target`; Enter opens the
    existing form with the typed text in the address field when it reads as a host name, nothing else inferred;
    after a successful save New session continues with the record the save returned."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"router.example")
            await pilot.pause()
            lines = _screen_lines(app)
            add = next((line for line in lines if "+ Add 'router.example' as a target" in line), None)
            assert add is not None, lines
            assert "▸ + Add 'router.example' as a target" in "\n".join(lines), lines
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            form = app.screen
            assert form.query_one("#target-host").value == "router.example"
            assert form.query_one("#target-name").value == "" and form.query_one("#target-port").value == ""
            assert form.query_one("#target-user").value == ""
            form.query_one("#target-id").value = "new-gear"
            form.query_one("#target-host").value = "192.0.2.77"
            form.query_one("#target-save").press()
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "the continuation")
            await wait_until(pilot, lambda: app.screen.selected_target_id == "new-gear",
                             "the saved record selected")
            assert len(_creates(served)) == 0, "the continuation created a session by itself"
            assert ("POST", "/targets") in [(m, p) for m, p, _ in served.requests]
    asyncio.run(check())


def test_no_match_without_target_rights_says_the_ruled_words(serve, wait_until):
    """L1-new-target amended: a login without target rights gets 'No target matches ... ask an admin to add it'
    with Esc clears; no Add row, and no target-create request is made."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until, user=_serve.VIEWER)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"zzz")
            await pilot.pause()
            lines = _screen_lines(app)
            flat = " ".join(" ".join(lines).replace("│", " ").split())
            assert "No target matches 'zzz' · ask an admin to add it · Esc clears" in flat, lines
            assert "+ Add" not in flat, lines
            await pilot.press("a")
            await pilot.pause(0.2)
            assert isinstance(app.screen, NewSessionScreen), app.screen
            assert _target_writes(served) == [], served.requests
            await pilot.press("escape")
            await pilot.pause()
            assert "No target matches" not in "\n".join(_screen_lines(app))
    asyncio.run(check())


class RevokingApp(InputRecordingApp):
    """`tsbc serve` that can drop `target.edit` from every principal's list mid-test."""

    def __init__(self, *args, revoke: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.revoke = revoke

    def _permissions(self, user):
        perms = super()._permissions(user)
        return [p for p in perms if not (self.revoke and p == "target.edit")]


def test_permission_revoked_after_the_form_opens_shows_the_refusal_and_creates_nothing(serve, wait_until):
    """L1-new-target-permissions: a revoked permission gets the authoritative 403 in the form; no session is
    created and nothing is retried by itself."""

    async def check():
        served, url = serve(RevokingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"router.example")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            served.revoke = True
            app.screen.query_one("#target-id").value = "router.example"
            app.screen.query_one("#target-save").press()
            await wait_until(pilot, lambda: bool(app.screen.query_one("#target-error").content), "the 403")
            assert str(app.screen.query_one("#target-error").content).startswith("target.edit is required")
            await pilot.pause(0.3)
            assert len(_creates(served)) == 0 and len(_target_writes(served)) == 1, served.requests
    asyncio.run(check())


def test_cancel_returns_exactly_one_level_with_the_query_intact(serve, wait_until):
    """L1-focus/escape: Cancel backs one level and the query stays."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"router.example")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, NewSessionScreen), app.screen
            lines = _screen_lines(app)
            assert any("+ Add 'router.example' as a target" in line for line in lines), lines
    asyncio.run(check())


class SnapshotApp(VaultServe):
    """`tsbc serve` with a settable create refusal, so the error state lands on demand."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.create_refusal: tuple[int, str, str] | None = None

    def _create(self, user, body, cookie=""):
        if self.create_refusal is not None:
            status, code, message = self.create_refusal
            return status, [], {"error": {"code": code, "message": message}}
        return super()._create(user, body, cookie)


class SaveThenDownApp(SnapshotApp):
    """POST /targets saves as the fixture server does; once `list_down` is set, every `GET /targets` list answer
    fails with `inventory_unavailable`, so a saved row never lands and the add-target watch times out."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.list_down = False

    def http(self, method, path, headers, body):
        if method == "GET" and path.split("?", 1)[0] == "/targets" and self.list_down:
            return 503, [], {"error": {"code": "inventory_unavailable", "message": "the inventory is down"}}
        return super().http(method, path, headers, body)


def _inside(app) -> bool:
    """Every control the screen holds fits inside the viewport."""
    screen = app.screen
    for widget in screen.query("Input, Button, Static, Select, Checkbox"):
        region = widget.region
        if region.y < 0 or region.bottom > screen.size.height or region.right > screen.size.width:
            return False
    return True


async def _prefill(app, pilot, wait_until, target: str, typ: str) -> None:
    app.workspace()._new_session_dialog(target, typ)
    await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                     "the prefilled dialog")


@pytest.mark.parametrize("size", [(80, 24), (160, 45)])
@pytest.mark.parametrize("case", ["matches", "no-match", "form", "error", "connection", "credential"])
def test_two_size_baselines_for_the_ruled_states(serve, wait_until, size, case):
    """L1-focus/geometry: snapshots at 80x24 and 160x45 for matches, no-match, the form, an error, the connection
    choice and the credential choice; every control stays inside the viewport."""

    async def check():
        served, url = serve(SnapshotApp())
        if case == "connection":
            client = Client(url)
            client.login(_serve.USER, _serve.PASSWORD)
            client.create_session("normal", "new", "xterm-256color", 80, 24)
        app = TsbTuiApp(url)
        async with app.run_test(size=size) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            if case == "matches":
                await pilot.press(*"normal")
                await pilot.pause()
            elif case == "no-match":
                await pilot.press(*"zzz")
                await pilot.pause()
            elif case == "form":
                await pilot.press(*"router.example")
                await pilot.pause()
                await pilot.press("enter")
                await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            elif case == "error":
                served.create_refusal = (502, "target_auth_failed", "the target refused the login")
                await _prefill(app, pilot, wait_until, "normal", "ssh")
                app.screen.query_one("#create-button").press()
                await wait_until(pilot, lambda: "The target refused the login" in "\n".join(_screen_lines(app)),
                                 "the refusal line")
            elif case == "connection":
                await pilot.press(*"normal")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.3)
            elif case == "credential":
                # The same two layers the credential-choice suite serves: a personal credential and a granted org
                # one of the same reference, both resolving for `normal`.
                served.hold_credential(_serve.USER, "netops")
                served.map_target(_serve.USER, "normal", "netops")
                served.org_creds["netops"] = {"reference": "netops", "unlock_class": "broker_unlocked",
                                              "kind": "password", "login_name": "svc", "secret": "FIXTURE",
                                              "stored_by": _serve.PEER}
                served.grants["g-1"] = {"grant_id": "g-1", "user": _serve.USER, "selectors": ["*"],
                                         "credential_ref": "netops", "granted_by": _serve.PEER, "granted_at": "x"}
                await _prefill(app, pilot, wait_until, "normal", "ssh")
                app.screen.query_one("#create-button").press()
                await wait_until(pilot, lambda: "Choose the one to connect with" in " ".join(" ".join(_screen_lines(app)).replace("│", " ").split()),
                                 "the credential choice")
            assert _inside(app), f"a control clips at the {case} state"
            await _p4_snapshot(app, pilot, f"{case}-{size[0]}x{size[1]}")
    asyncio.run(check())


def test_retired_strings_and_the_direct_get_by_id_are_gone(serve, wait_until):
    """L1-open-refusals (retirement half): a text that matches no row is guidance, not 'no such target'; no id is
    required and the screen asks the broker for no `GET /targets/{id}`."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"no-such-device")
            await pilot.pause()
            app.screen.query_one("#create-button").press()
            await pilot.pause(0.3)
            joined = "\n".join(_screen_lines(app))
            assert "no such target" not in joined and "a target id is required" not in joined, joined
            assert _get_by_id(served) == [], [p for m, p, _ in served.requests if p.startswith("/targets/")]
            assert len(_creates(served)) == 0
    asyncio.run(check())


def test_a_prefilled_target_not_in_the_loaded_list_is_created_not_the_first_row(serve, wait_until):
    """A target the workspace handed to New session that is not on any loaded page keeps its own identity:
    Create sends that target, never whatever row happens to sit at the highlight."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            app.workspace()._new_session_dialog("ghost-target", "ssh")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                             "the prefilled dialog")
            before = len(_creates(served))
            app.screen.query_one("#create-button").press()
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the create")
            body = _creates(served)[-1]
            assert body["target_id"] == "ghost-target", body
    asyncio.run(check())


def test_the_connection_choice_picker_is_shown_not_inside_a_hidden_box(serve, wait_until):
    """The picker for a broker-required connection choice lives inside `#reuse-box`, hidden at rest; offering the
    choice shows the box so the operator can see and reach the picker by keyboard."""

    async def check():
        class _ListStub(SlowChunkedApp):
            def http(self, method, path, headers, body):
                if method == "GET" and path.split("?", 1)[0] == "/targets":
                    record = {"id": "stub-target", "type": "ssh", "name": "stub target", "group": "stub",
                              "tags": [], "discovered_by": "manual", "last_seen": "2026-09-23T12:00:00Z",
                              "host": "192.0.2.20", "port": 22}
                    return 200, [], {"targets": [record]}
                return super().http(method, path, headers, body)

        stub = _ListStub(connection_choice_candidates=["stub-conn-a", "stub-conn-b"], recording_on_create=False)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target="stub-target")
            async with app.run_test(size=(120, 40)) as pilot:
                await login(app, pilot)
                await wait_until(pilot, lambda: any(row.id == "stub-target" for row in app.workspace().targets.rows),
                                 "the inventory")
                await open_new_session(app, pilot, wait_until)
                app.screen.query_one("#new-session-filter", Input).value = "stub target"
                await pilot.pause()
                await pilot.click("#conn-reuse")
                app.screen.query_one("#create-button").press()
                picker = app.screen.query_one("#connection-picker", Select)
                await wait_until(pilot, lambda: not picker.disabled, "the picker populated from GET /sessions")
                assert app.screen.query_one("#reuse-box").display is True, "the box holding the picker stays hidden"
    asyncio.run(check())


def test_enter_on_a_focused_control_is_not_the_open_press(serve, wait_until):
    """The screen-wide Enter belongs to a focused control, not always to the highlighted row: Enter on Cancel
    cancels, it never starts a create."""

    async def check():
        served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"normal")
            await pilot.pause()
            app.screen.query_one("#cancel-button").focus()
            before = len(_creates(served))
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert len(_creates(served)) == before, "Enter on Cancel started a create"
            assert app.screen.query_one("#new-session-filter", Input).value == "", (
                "Enter did not reach Cancel: the query was not cleared")
    asyncio.run(check())


def test_a_second_enter_during_a_create_in_flight_sends_one_create(serve, wait_until):
    """A create in flight is not pressed again: with the create held on the broker, a second Enter during the round
    trip sends one create, not two sessions."""

    async def check():
        import threading

        class _ListStub(SlowChunkedApp):
            def http(self, method, path, headers, body):
                if method == "GET" and path.split("?", 1)[0] == "/targets":
                    record = {"id": "stub-target", "type": "ssh", "name": "stub target", "group": "stub",
                              "tags": [], "discovered_by": "manual", "last_seen": "2026-09-23T12:00:00Z",
                              "host": "192.0.2.20", "port": 22}
                    return 200, [], {"targets": [record]}
                return super().http(method, path, headers, body)

        gate = threading.Event()
        stub = _ListStub(recording_on_create=False, create_gate=gate)
        with StubServer(stub) as url:
            app = TsbTuiApp(url, target="stub-target")
            async with app.run_test(size=(120, 40)) as pilot:
                await login(app, pilot)
                await wait_until(pilot, lambda: any(row.id == "stub-target" for row in app.workspace().targets.rows),
                                 "the inventory")
                await open_new_session(app, pilot, wait_until)
                app.screen.query_one("#new-session-filter", Input).value = "stub target"
                await pilot.pause()
                await pilot.press("enter")
                await wait_until(pilot, lambda: len(stub.creates) >= 1, "the first create")
                await pilot.press("enter")
                await pilot.pause(0.3)
                assert len(stub.creates) == 1, stub.creates
                gate.set()
    asyncio.run(check())


def test_a_transport_failure_shows_the_operator_something(serve, wait_until):
    """A create that fails on the transport (not an API refusal code) is told to the operator: a message on the
    screen and the app's own transport handling, never a silent re-enable of Create."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            told = []
            app.notify = lambda *a, **k: told.append(a)
            app.screen._on_create_error(OSError("connection refused"))
            await pilot.pause()
            joined = "\n".join(_screen_lines(app))
            assert "connection refused" in joined, joined
            assert told, "the transport failure was never told to the operator"
    asyncio.run(check())


def test_choosing_new_connection_after_the_reuse_radio_sends_new(serve, wait_until):
    """An ask option carries its own mode: choosing `new connection` after the reuse radio was selected sends
    `connection: new`, not the radio state left on screen."""

    async def check():
        served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        sid = client.create_session("normal", "new", "xterm-256color", 80, 24)["session"]["id"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await pilot.press("ctrl+backslash", "F", "tab")
            await pilot.pause()
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                             "New session to open")
            await pilot.press(*"normal")
            await pilot.pause()
            # Set the reuse radio's state first, so a create that reads the radio would send reuse; the filter keeps
            # the keys, so `⏎` raises the ask.
            app.screen.query_one("#conn-reuse", RadioButton).value = True
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.press("down")
            await pilot.press("down")
            before = len(_creates(served))
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the create")
            body = _creates(served)[-1]
            assert body["connection"] == "new" and "connection_id" not in body, body
    asyncio.run(check())


def test_moving_the_highlight_updates_the_protocol_controls(serve, wait_until):
    """The login fields follow the highlighted row: moving from a telnet row to an ssh row restores them, moving
    onto a telnet row hides them."""

    async def check():
        _served, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"scenario")
            await pilot.pause()
            for _ in range(4):
                await pilot.press("down")
            await pilot.pause()
            assert app.screen.query_one("#login-fields").display is False, "a telnet row shows no login fields"
            await pilot.press("up")
            await pilot.pause()
            assert app.screen.query_one("#login-fields").display is True, (
                "moving onto an ssh row left the telnet row's controls on screen")
    asyncio.run(check())


def test_the_ask_number_counts_closed_slots_like_the_rail(serve, wait_until):
    """`go to session N` uses this window's number over all slots, closed included, as the rail and the row facts
    count it — not a closed-filtered index."""

    async def check():
        _served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        first = client.create_session("normal", "new", "xterm-256color", 80, 24)["session"]["id"]
        second = client.create_session("normal", "new", "xterm-256color", 80, 24)["session"]["id"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, first, second)
            app.workspace().slots[0].closed = True  # the earlier session ended on the rail
            await pilot.press("ctrl+backslash", "F", "tab")
            await pilot.pause()
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                             "New session to open")
            await pilot.press(*"normal")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.3)
            joined = "\n".join(_screen_lines(app))
            assert "go to session 2" in joined, joined
    asyncio.run(check())


def test_a_live_telnet_connection_open_one_way_not_an_ask(serve, wait_until):
    """DEFAULT 22: a telnet connection carries one session, so a live telnet connection never gets the reuse ask;
    Enter opens one way with `connection: new`."""

    async def check():
        served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        client.create_session("telnet-scenario", "new", "xterm-256color", 80, 24)
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"telnet scenario")
            await pilot.pause()
            before = len(_creates(served))
            await pilot.press("enter")
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the one-way create")
            body = _creates(served)[-1]
            assert body["target_id"] == "telnet-scenario" and body["connection"] == "new", body
            joined = "\n".join(_screen_lines(app))
            assert "another session" not in joined, joined
    asyncio.run(check())


def test_a_session_opened_in_this_window_offers_another_session_before_the_poll(serve, wait_until):
    """§15.3: a session opened in this window is a live connection of this login, so its target's ask offers
    `another session` from the workspace's live records, not only after the fleet poll lists it."""

    async def check():
        _served, url = serve(InputRecordingApp())
        client = Client(url)
        client.login(_serve.USER, _serve.PASSWORD)
        sid = client.create_session("normal", "new", "xterm-256color", 80, 24)["session"]["id"]
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await open_here(app, pilot, wait_until, sid)
            await pilot.press("ctrl+backslash", "F", "tab")
            await pilot.pause()
            await pilot.press("n")
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen) and app.screen.is_mounted,
                             "New session to open")
            await pilot.press(*"normal")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.3)
            joined = "\n".join(_screen_lines(app))
            assert "another session" in joined, joined
    asyncio.run(check())


def test_r_after_a_connect_refusal_repeats_the_create(serve, wait_until):
    """A refusal that names `r` moves the keyboard off the filter to the Create button, so the person's own `r`
    reaches the refusal and repeats the create on the target it was attempted against. Nothing retries by itself."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            served.create_refusal = (502, "target_connect_failed", "the target did not accept the connection")
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            app.screen.query_one("#create-button").press()
            await wait_until(pilot, lambda: "Could not connect to normal" in "\n".join(_screen_lines(app)),
                             "the refusal line")
            # The keyboard left the filter: focus is on Create, so `r` reaches the refusal, not typing.
            assert app.screen.focused.id == "create-button", app.screen.focused
            served.create_refusal = None  # the person's own repeat lands this time
            before = len(_creates(served))
            await pilot.press("r")
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the repeated create")
            assert _creates(served)[-1]["target_id"] == "normal", _creates(served)[-1]
    asyncio.run(check())


def test_r_after_a_not_found_refusal_refetches_the_list(serve, wait_until):
    """A refusal that names `r` moves the keyboard to Create; the person's own `r` refetches the list for
    `target_not_found`, never a retry of the create."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            served.create_refusal = (404, "target_not_found", "no such target")
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            app.screen.query_one("#create-button").press()
            await wait_until(pilot, lambda: "is no longer in your list" in "\n".join(_screen_lines(app)),
                             "the refusal line")
            assert app.screen.focused.id == "create-button", app.screen.focused
            before = len([p for m, p, _ in served.requests if p.split("?")[0] == "/targets"])
            await pilot.press("r")
            await wait_until(pilot, lambda: len([p for m, p, _ in served.requests
                                                 if p.split("?")[0] == "/targets"]) > before, "the refetch")
    asyncio.run(check())


def test_a_refresh_before_the_marked_target_moves_the_mark_to_the_entity(serve, wait_until):
    """The mark follows the entity through a refresh that inserts a row above it: after the repaint the marked row
    is the one Create acts on, not the row left at the stale index."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            n = len(app.workspace().targets.rows)
            served.targets["aaa-before"] = {"id": "aaa-before", "type": "ssh", "name": "aaa before",
                                             "group": "lab", "tags": [], "discovered_by": "manual",
                                             "last_seen": "2026-10-17T08:30:00Z", "host": "198.51.100.20", "port": 22}
            app.workspace()._fetch_targets()  # a workspace refresh, not a key press in New session
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) == n + 1, "the new row to land")
            # The mark (painted at `_sel`) must be the entity Enter acts on: after a repaint through the row-change
            # notification, the row at the painted index is the selected target.
            rows = app.screen._matched_rows()
            assert rows[app.screen._sel].id == app.screen._sel_id, (rows[app.screen._sel].id, app.screen._sel_id)
            marked = [ln for ln in str(app.screen.query_one("#target-rows").render()).splitlines()
                      if ln.startswith("▸")]
            assert len(marked) == 1, (marked, "no single marked row")
            assert "normal" in marked[0], (marked[0], "the mark is not on the selected target")
            assert app.screen.selected_target_id == "normal", app.screen.selected_target_id
    asyncio.run(check())


def test_r_after_a_connect_refusal_with_a_typed_login_refocuses_the_login(serve, wait_until):
    """A connect refusal after a create that carried a typed login is not repeated silently with the login cleared:
    `r` re-shows the refusal and focuses the login fields, sending nothing."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            served.create_refusal = (502, "target_connect_failed", "the target did not accept the connection")
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            app.screen.query_one("#login-user").value = "svc"
            app.screen.query_one("#login-password").value = "FIXTURE"
            app.screen.query_one("#create-button").press()
            await wait_until(pilot, lambda: "Could not connect to normal" in "\n".join(_screen_lines(app)),
                             "the refusal line")
            before = len(_creates(served))
            await pilot.press("r")
            await pilot.pause(0.3)
            assert len(_creates(served)) == before, (len(_creates(served)), before, "r sent a create without the login")
            assert app.screen.focused.id == "login-user", app.screen.focused
    asyncio.run(check())


def test_the_add_target_watch_bounds_and_shows_the_inventory_state(serve, wait_until):
    """The add-target continuation watches for the saved row; when the list does not refresh within 5 seconds it
    stops watching and shows the Targets tab's own inventory-down state, keeping the saved id so a later list selects
    it. No endless timer."""

    async def check():
        served, url = serve(SaveThenDownApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"router.example")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            app.screen.query_one("#target-id").value = "new-gear"
            served.list_down = True  # the post-save list refresh fails; the saved row never lands
            app.screen.query_one("#target-save").press()
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "the continuation")
            await wait_until(pilot, lambda: "Could not load targets · r retry" in "\n".join(_screen_lines(app)),
                             "the bounded watch to end", timeout_s=8.0)
            assert app.screen._pending_select == "new-gear", "the saved id was dropped"
            assert app.screen._pending_timer is None, "the watch never stopped"
            # The message names `r` as the recovery key, so the keyboard moves off the filter to Create.
            await pilot.pause()
            assert app.screen.focused.id == "create-button", app.screen.focused
    asyncio.run(check())


def test_the_credential_choice_takes_focus_and_a_key_picks_a_candidate(serve, wait_until):
    """Keys-only traversal: an offered credential choice takes focus, so a key acts on its candidates, not on the
    target row behind it; the create repeats with the picked credential."""

    async def check():
        served, url = serve(SnapshotApp())
        served.hold_credential(_serve.USER, "netops")
        served.map_target(_serve.USER, "normal", "netops")
        served.org_creds["netops"] = {"reference": "netops", "unlock_class": "broker_unlocked", "kind": "none",
                                      "login_name": "svc", "secret": "FIXTURE", "stored_by": _serve.PEER}
        served.grants["g-1"] = {"grant_id": "g-1", "user": _serve.USER, "selectors": ["*"],
                                 "credential_ref": "netops", "granted_by": _serve.PEER, "granted_at": "x"}
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            app.screen.query_one("#create-button").press()
            await wait_until(pilot, lambda: app.screen.query_one("#credential-choice").display,
                             "the credential choice")
            # Keys-only: the offered choice takes focus, so a key acts on the candidates, not the row behind.
            assert app.screen.focused.id == "credential-choice", app.screen.focused
            before = len(_creates(served))
            await pilot.press("down")  # highlight the first candidate (RadioSet next_button)
            await pilot.press("enter")  # toggle it: RadioSet.Changed fires the pick, repeating the create
            await wait_until(pilot, lambda: len(_creates(served)) > before, "the repeated create")
            second = _creates(served)[-1]
            assert second.get("credential_ref") == "netops", second
    asyncio.run(check())


def test_rows_that_land_after_open_are_repainted_without_a_key(serve, wait_until):
    """When the workspace's target rows change after New session opens, the screen repaints its painted window
    through the workspace's row-change notification, with no key pressed in the screen."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            n = len(app.workspace().targets.rows)
            assert f"Targets · 1/{n}" in _position(app), _position(app)
            served.targets["late-landed"] = {"id": "late-landed", "type": "ssh", "name": "late landed",
                                             "group": "lab", "tags": [], "discovered_by": "manual",
                                             "last_seen": "2026-10-17T08:30:00Z", "host": "198.51.100.9", "port": 22}
            app.workspace()._fetch_targets()  # a workspace refresh, not a key press in New session
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) == n + 1, "the new row to land")
            assert f"Targets · 1/{n + 1}" in _position(app), (_position(app), "the painted rows stayed stale")
    asyncio.run(check())


def test_a_not_found_refetch_repaints_the_rows_as_they_land(serve, wait_until):
    """After a `target_not_found` refusal the person's `r` refetches the list; when the fresh rows land, New session
    repaints them with no further key press."""

    async def check():
        served, url = serve(SnapshotApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await fixture_login(app, pilot)
            await _prefill(app, pilot, wait_until, "normal", "ssh")
            n = len(app.workspace().targets.rows)
            served.create_refusal = (404, "target_not_found", "no such target")
            app.screen.query_one("#create-button").press()  # Create on the prefilled row -> the refusal
            await wait_until(pilot, lambda: "is no longer in your list" in _joined(app), "the refusal line")
            served.targets["refetched-row"] = {"id": "refetched-row", "type": "ssh", "name": "refetched row",
                                               "group": "lab", "tags": [], "discovered_by": "manual",
                                               "last_seen": "2026-10-17T08:30:00Z", "host": "198.51.100.11", "port": 22}
            await pilot.press("r")  # the person's recovery press: refetches the list
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) == n + 1, "the refetched rows to land")
            # The painted position's total grew from n to n+1 with no key pressed in New session.
            assert f"/{n + 1}/" in _position(app), (_position(app), "the refetch did not repaint the rows")
    asyncio.run(check())


def test_a_saved_target_that_lands_after_the_watch_ended_is_selected(serve, wait_until):
    """The add-target watch keeps the saved id after it ends; when a later list lands that holds that target, New
    session selects it through the workspace's row-change notification, with no key pressed in the screen."""

    async def check():
        served, url = serve(SaveThenDownApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 36)) as pilot:
            await _login_and_open(app, pilot, wait_until)
            await wait_until(pilot, lambda: len(app.workspace().targets.rows) >= 8, "the inventory")
            await pilot.press(*"router.example")
            await pilot.pause()
            await pilot.press("enter")
            await wait_until(pilot, lambda: isinstance(app.screen, TargetFormScreen), "the target form")
            app.screen.query_one("#target-id").value = "new-gear"
            served.list_down = True  # the post-save list refresh fails; the saved row never lands
            app.screen.query_one("#target-save").press()
            await wait_until(pilot, lambda: isinstance(app.screen, NewSessionScreen), "the continuation")
            await wait_until(pilot, lambda: "Could not load targets · r retry" in _joined(app),
                             "the bounded watch to end", timeout_s=8.0)
            assert app.screen._pending_select == "new-gear", "the saved id was dropped"
            served.list_down = False  # the inventory answers again; a later list now lands
            app.workspace()._fetch_targets()
            await wait_until(pilot, lambda: app.workspace().targets.row("new-gear") is not None,
                             "the saved row to land")
            assert app.screen._sel_id == "new-gear", (app.screen._sel_id, "the saved target was not selected")
            # The watch's inventory-down message goes when the saved row lands, before the repaint.
            assert "Could not load targets" not in _joined(app), (_joined(app), "the watch message stayed")
    asyncio.run(check())
