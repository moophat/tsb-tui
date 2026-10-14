"""Every consumer MUST of the protocols the TUI drives has a named test, and the ones that had none have one now.

Why it matters (Rule 9): no contract fixture grades a consumer ("owed" in every protocol), so these lines are the TUI's
own to prove, and a line nobody tests is a promise nothing keeps. `CONSUMER_MUSTS` is the table of the plan's
§ Consumer MUSTs, one row per line, each naming the tests that prove it (file and test); a test that checks the table
fails when a named test is renamed or deleted, so the table cannot rot into a list of names that no longer exist. The
lines the earlier phases had no test for are proved here: a ticket only in the `attach` frame and never in a URL (V10),
the cookie in memory only (V10), an unknown error code handled by its HTTP status (V4), no way out to the broker's
host (V11) and no word of isolation for a second connection (V13). Servers: the contract's in-process fixture server
(DT7), taught to record what it was asked.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path

from input_server import InputRecordingApp, attached
from stub_server import create_owner_session
from tsb_tui.app import TsbTuiApp
from tsb_tui.screens.login import LoginScreen
from tsb_tui.screens.new_session import NewSessionScreen
from tsbc import _serve
from workspace_support import fixture_login, open_new_session

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent / "tsb_tui"

# line of the plan's § Consumer MUSTs -> (test file, test name) for each test that proves it.
CONSUMER_MUSTS: dict[str, list[tuple[str, str]]] = {
    "session-attach CR1: the recording text before an attach or any input": [
        ("test_attach_confirm.py", "test_the_host_key_tick_and_the_recording_text_come_before_the_first_attach"),
        ("test_drive_commands.py", "test_attach_prints_the_recording_text_before_it_attaches_then_answers"),
    ],
    "session-attach CR2: the ticket only in `attach`, a new one for every attach": [
        ("test_consumer_musts.py", "test_the_ticket_travels_only_in_the_first_frame_and_never_in_a_url"),
        ("test_ended_and_reattach.py", "test_a_reattach_mints_a_new_ticket_and_the_grid_resets_at_the_next_prefix"),
    ],
    "session-attach CR3: the dump at the `prefix` geometry; no `prefix`, output follows `attached`": [
        ("test_attach_prefix.py", "test_the_grid_ends_at_the_latest_size_whatever_the_order"),
        ("test_attach_prefix.py", "test_against_a_core_with_no_prefix_output_follows_attached"),
        ("test_model_attachment.py", "test_prefix_carries_the_dumps_geometry"),
        ("test_size_and_faithful.py", "test_a_session_joined_mid_stream_shows_the_grid_of_a_raw_replay_and_the_window_never_moves_it"),
    ],
    "session-attach CR4: the viewport is information, not a request": [
        ("test_size_rules.py", "test_attaching_sends_no_resize_and_the_attach_frame_names_the_canvas"),
    ],
    "session-attach CR5: panning is local": [
        ("test_size_rules.py", "test_the_canvas_pans_over_a_grid_wider_than_the_window"),
    ],
    "session-open step 22: a first-use host key acknowledged before the attach": [
        ("test_attach_confirm.py", "test_the_host_key_tick_and_the_recording_text_come_before_the_first_attach"),
    ],
    "session-detach rule 9: every ping answered (the pump keeps reading under paint load)": [
        ("test_canvas.py", "test_under_a_steady_stream_the_pump_keeps_reading_and_answers_the_servers_ping"),
        ("test_bench_smoke.py", "test_paint_runs_tiny_against_a_server_of_its_own_and_every_keystroke_comes_back"),
    ],
    "session-detach rule 10: 4011 is a detach, never an end": [
        ("test_model_closes.py", "test_4011_reattaches_with_backoff"),
        ("test_ended_and_reattach.py", "test_4011_and_a_dropped_socket_show_the_reattaching_row_and_esc_stops_it"),
    ],
    "session-detach rule 11: `clients` replaces the whole set": [
        ("test_model_attachment.py", "test_clients_frame_replaces_the_whole_set"),
    ],
    "session-detach rule 12: a new ticket to come back": [
        ("test_ended_and_reattach.py", "test_a_reattach_mints_a_new_ticket_and_the_grid_resets_at_the_next_prefix"),
        ("test_model_closes.py", "test_stub_4011_is_a_detach_and_the_pump_attaches_again_with_a_new_ticket"),
    ],
    "typing-ownership CR1: the holder comes from `token`": [
        ("test_model_attachment.py", "test_token_handoff_sets_the_new_holder_and_view_only"),
        ("test_model_attachment.py", "test_the_flags_follow_every_token_frame_whatever_its_reason"),
    ],
    "typing-ownership CR2: no request on getting the token": [
        ("test_size_rules.py", "test_getting_the_keyboard_sends_no_size_request"),
        ("test_size_and_faithful.py", "test_a_session_joined_mid_stream_shows_the_grid_of_a_raw_replay_and_the_window_never_moves_it"),
    ],
    "typing-ownership CR3: suppression is a guardrail, never an owner's typing": [
        ("test_input_path.py", "test_a_viewer_with_the_guard_on_sends_no_typing_and_no_paste_and_counts_them"),
        ("test_input_path.py", "test_the_owner_typing_while_another_client_holds_the_keyboard_takes_it_and_ctrl_c_follows"),
        ("test_derived_input.py", "test_an_owner_who_is_only_watching_answers_nothing_and_takes_nothing"),
    ],
    "size-control: no request for the surface's own rendering": [
        ("test_size_rules.py", "test_a_window_smaller_than_the_session_shows_the_sessions_own_layout_and_sends_nothing"),
        ("test_size_rules.py", "test_with_auto_requests_off_a_window_change_asks_for_nothing"),
    ],
    "size-control: an explicit request is labelled as one": [
        ("test_size_dialog.py", "test_the_typist_asks_for_the_window_minus_the_chrome_and_the_answer_shows_with_its_reason"),
    ],
    "size-control: automatic requests are debounced": [
        ("test_size_rules.py", "test_a_burst_of_window_changes_sends_one_debounced_request"),
    ],
    "size-control: none when the token moves": [
        ("test_size_rules.py", "test_getting_the_keyboard_sends_no_size_request"),
        ("test_size_and_faithful.py", "test_a_session_joined_mid_stream_shows_the_grid_of_a_raw_replay_and_the_window_never_moves_it"),
    ],
    "size-control: no second request after a clamp": [
        ("test_size_rules.py", "test_no_second_request_after_a_clamped_answer_until_the_window_changes"),
    ],
    "size-control: the real size and the reason are shown": [
        ("test_size_dialog.py", "test_a_clamped_answer_shows_its_reason_and_waits_for_the_window_to_change"),
    ],
    "size-control: an unknown reason reads as the columns and rows": [
        ("test_model_attachment.py", "test_an_unknown_size_reason_still_reads_cols_rows"),
        ("test_size_dialog.py", "test_every_known_reason_has_words_and_an_unknown_one_reads_by_its_sizes"),
    ],
    "stale-screen 18: the choices' texts exactly as sent": [
        ("test_stale_dialog.py", "test_the_holder_gets_the_dialog_with_the_texts_as_sent"),
    ],
    "stale-screen 19: `fresh_session` is the surface's own `POST /sessions`": [
        ("test_stale_dialog.py", "test_fresh_session_opens_new_session_on_the_same_target_and_never_ends_the_old_one"),
    ],
    "quiet-prompt 9: the answer carries its `prompt_id`, and the surface keeps no timer of its own": [
        ("test_quiet_prompt.py", "test_keep_it_answers_no_with_the_prompt_id_and_hides_the_box"),
        ("test_quiet_prompt.py", "test_the_box_appears_only_on_a_quiet_prompt"),
    ],
    "session-countdown 9: a countdown only from the core": [
        ("test_countdown_and_states.py", "test_the_countdown_is_the_cores_ends_at_against_the_wall_clock"),
    ],
    "session-countdown 10: an unknown `ends_by` shows `ends_at`": [
        ("test_countdown_and_states.py", "test_an_unknown_ends_by_shows_ends_at"),
        ("test_model_attachment.py", "test_an_unknown_ends_by_still_shows_ends_at"),
    ],
    "session-countdown 11: withdrawn on `ending` false": [
        ("test_countdown_and_states.py", "test_a_notice_with_ending_false_withdraws_the_countdown"),
    ],
    "session-list 7: an unknown state is not attachable, an unknown type is opaque": [
        ("test_model_fleet.py", "test_an_unknown_state_is_not_attachable"),
        ("test_model_fleet.py", "test_an_unknown_session_type_is_opaque_not_an_error"),
    ],
    "client-remove 8: no automatic re-attach after 4014": [
        ("test_model_closes.py", "test_4014_never_reattaches"),
        ("test_ended_and_reattach.py", "test_4014_says_removed_and_never_reattaches"),
    ],
    "client-remove 9: confirmation before a client is removed": [
        ("test_whos_here.py", "test_remove_asks_first_naming_the_client_then_the_removed_client_is_closed_with_4014_and_stays_out"),
    ],
    "operator-alerts: unknown events are ignored": [
        ("test_model_alerts.py", "test_an_unknown_event_type_is_ignored"),
        ("test_alerts_popup.py", "test_an_unknown_event_type_is_ignored_and_an_unknown_kind_is_tsbs_own_alert"),
    ],
    "operator-alerts: an unknown kind is TSB's own alert": [
        ("test_model_alerts.py", "test_an_unknown_kind_is_tsb_s_own_alert_never_host_state"),
    ],
    "conventions V1: a known type with an unknown version is an unknown type": [
        ("test_model_attachment.py", "test_a_known_type_with_an_unknown_version_changes_nothing"),
    ],
    "conventions V2: unknown fields, frame types and events are ignored": [
        ("test_model_attachment.py", "test_an_unknown_frame_type_changes_nothing"),
        ("test_model_attachment.py", "test_an_unknown_field_on_a_known_frame_is_ignored"),
    ],
    "conventions V3: an unknown 4xxx is 4010; no re-attach after 4014": [
        ("test_model_closes.py", "test_the_close_table_row"),
        ("test_model_closes.py", "test_a_5001_close_reads_as_4010"),
        ("test_ended_and_reattach.py", "test_an_ended_session_keeps_its_last_screen_dimmed_with_its_dialog_and_never_reattaches"),
    ],
    "conventions V4: an unknown error code is handled by its HTTP status": [
        ("test_consumer_musts.py", "test_an_error_code_the_tui_does_not_know_is_handled_by_its_http_status"),
    ],
    "conventions V5: the registry's consumer column, unknown values included": [
        ("test_model_attachment.py", "test_an_unknown_token_reason_still_reads_the_holder"),
        ("test_countdown_and_states.py", "test_an_unknown_refusal_reason_reads_as_input_was_refused"),
    ],
    "conventions V10: the cookie in memory only, no ticket or token in a URL": [
        ("test_consumer_musts.py", "test_the_cookie_is_kept_in_memory_and_written_nowhere"),
        ("test_consumer_musts.py", "test_the_ticket_travels_only_in_the_first_frame_and_never_in_a_url"),
    ],
    "conventions V11: no way out to the broker's host": [
        ("test_consumer_musts.py", "test_the_tui_has_no_way_to_run_anything_on_a_host"),
    ],
    "conventions V13: no second connection described as isolation": [
        ("test_consumer_musts.py", "test_no_screen_describes_a_second_connection_as_isolation"),
        ("test_ended_and_reattach.py", "test_an_ended_session_keeps_its_last_screen_dimmed_with_its_dialog_and_never_reattaches"),
    ],
    "conventions V14: the choice is put to the operator and the request repeated with it": [
        ("test_connection_choice.py", "test_connection_choice_required_offers_candidates_and_retries_with_the_chosen_one"),
        ("test_stale_dialog.py", "test_each_choice_sends_its_own_answer_by_click"),
    ],
}


def _test_names(path: Path) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")}


def test_every_consumer_must_line_names_tests_that_exist() -> None:
    """Rule 12. The table is the report's evidence for the plan's § Consumer MUSTs; a name that no longer exists in
    its file (renamed, deleted, or typed wrongly) leaves a line with no test behind it and says nothing, so each is
    looked up in the file's syntax tree and the table must keep at least one test for every line."""
    missing = []
    names: dict[str, set[str]] = {}
    for line, tests in CONSUMER_MUSTS.items():
        assert tests, f"{line!r} names no test"
        for file, name in tests:
            known = names.setdefault(file, _test_names(TESTS / file)) if (TESTS / file).is_file() else set()
            if name not in known:
                missing.append((line, file, name))
    assert not missing, f"tests the table names that do not exist: {missing}"


# -- the lines that had no test --


class _Recording(InputRecordingApp):
    """The fixture server, remembering the path of each WebSocket upgrade and the tickets it minted."""

    def __init__(self) -> None:
        super().__init__()
        self.upgrade_paths: list[str] = []

    def websocket(self, path, ws) -> None:
        self.upgrade_paths.append(path)
        super().websocket(path, ws)


def test_the_ticket_travels_only_in_the_first_frame_and_never_in_a_url(serve, wait_until) -> None:
    """V10, session-attach CR2: "MUST NOT put a ticket or token in a URL". A ticket in a URL lands in proxy and access
    logs for anyone to replay for its lifetime; the contract puts it in the first WebSocket frame and nowhere else, and
    a new ticket for each attach. Read from the server's side: every upgrade is to `/ws/attach` with no query, the
    ticket is in the `attach` frame, no request line anywhere names a ticket, and a reattach brings a different one."""

    async def check() -> None:
        serve_app, url = serve(_Recording())
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            screen = await attached(pilot, app, session_id, wait_until)
            first = screen.pump.state.client_id
            with serve_app.lock:
                serve_app._drop_client(serve_app.sessions[session_id], next(iter(serve_app.clients[session_id])),
                                       code=4011, reason="")
            await wait_until(pilot, lambda: screen.pump.state.client_id not in (None, first)
                             and screen.pump.state.dump_seen, "the reattach")

            attach_frames = [f for f in serve_app.frames if f.get("t") == "attach"]
            tickets = [f["ticket"] for f in attach_frames]
            assert len(tickets) == 2 and len(set(tickets)) == 2, "each attach needs a ticket of its own"
            assert serve_app.upgrade_paths == ["/ws/attach", "/ws/attach"], serve_app.upgrade_paths
            for method, path, body in serve_app.requests:
                assert not any(ticket in path for ticket in tickets), f"a ticket is in a URL: {method} {path}"
            mints = [body for method, path, body in serve_app.requests if (method, path) == ("POST", "/auth/ws-ticket")]
            assert mints == [{"session_id": session_id}] * 2, mints

    asyncio.run(check())


def test_the_cookie_is_kept_in_memory_and_written_nowhere(serve, wait_until, tmp_path) -> None:
    """V10, decision 5.34: "MUST hold the cookie in memory only". A login cookie in a file (a config, a cache, a session
    record, a log) is a credential on disk for whoever reads the directory. After a login, an attach and a reattach
    nothing under the home directory or the test's own directory holds the cookie's value, and nor does the
    process's environment; the client holds it in memory."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(100, 30)) as pilot:
            session_id = create_owner_session(app, _serve.TARGET, 80, 24)
            await attached(pilot, app, session_id, wait_until)
            cookies = list(serve_app.logins)
            assert cookies, "the server never saw a login"
            written: list[str] = []
            for root in (Path(os.environ["HOME"]), tmp_path):
                for path in root.rglob("*"):
                    if path.is_file() and not path.is_symlink() and path.stat().st_size < 5_000_000:
                        data = path.read_bytes()
                        if any(cookie.encode() in data for cookie in cookies):
                            written.append(str(path))
            assert not written, f"the login cookie is on disk: {written}"
            assert not any(cookie in value for cookie in cookies for value in os.environ.values())

    asyncio.run(check())


class _UnknownCodes(InputRecordingApp):
    """The fixture server answering one request with a status and an error code that no registry lists."""

    def __init__(self) -> None:
        super().__init__()
        self.answer: tuple[str, str, int, str] | None = None  # method, path, status, code

    def http(self, method, path, headers, body):
        if self.answer is not None and (method, path) == self.answer[:2]:
            return self._err(self.answer[2], self.answer[3], "a sentence the TUI must show as it is")
        return super().http(method, path, headers, body)


def test_an_error_code_the_tui_does_not_know_is_handled_by_its_http_status(serve, wait_until) -> None:
    """V4: "Consumers MUST handle an unknown code by its HTTP status". A core newer than the TUI answers with a code
    this build has never seen, and the TUI must do what the status means and nothing a guessed code would: a 401 sends
    the person back to the login (their login lapsed, whatever the code is called), and a 409 on creating a session
    shows the core's own sentence in the dialog, keeps the dialog open and does not read as the choice of a connection
    (that is a code the TUI does know, and an unknown one must never be mistaken for it)."""

    async def check() -> None:
        serve_app, url = serve(_UnknownCodes())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot)
            await open_new_session(app, pilot, wait_until)
            dialog = app.screen
            assert isinstance(dialog, NewSessionScreen)
            dialog.query_one("#target-id").value = _serve.TARGET
            serve_app.answer = ("POST", "/sessions", 409, "a_code_from_the_future")
            await pilot.click("#create-button")
            await wait_until(pilot, lambda: "a sentence the TUI must show as it is" in str(
                dialog.query_one("#new-session-error").render()), "the core's sentence in the dialog")
            assert app.screen is dialog, "the dialog closed on a refusal"
            assert dialog.query_one("#connection-picker").disabled, "an unknown 409 was read as a connection choice"
            assert not any(s["state"] != "killed" and s["owner"] == _serve.USER for s in serve_app.sessions.values()), (
                "a refused create made a session")
            dialog.action_cancel()
            await wait_until(pilot, lambda: app.screen is screen, "the dialog to close")

            serve_app.answer = ("GET", "/sessions", 401, "a_lapsed_login_by_another_name")
            screen._refresh_fleet()
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen), "the login screen after a 401")

    asyncio.run(check())


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_the_tui_has_no_way_to_run_anything_on_a_host() -> None:
    """V11, Contract §4: "a surface MUST NOT bridge a consumer to the broker host". A shell, a pty, a spawned process or a
    raw socket in the TUI is a bridge from whoever drives it (a person, or an agent through `tsb-tui drive`) to a machine
    that is not the target. The TUI reaches the broker only through the contract client (the import-lint holds the
    imports); here no module of the shipped package may import a way to start a process or open a terminal, and none
    may call `os.system`, `os.popen`, `os.exec*` or `os.spawn*`."""
    banned = {"subprocess", "pty", "pexpect", "ptyprocess", "multiprocessing", "ctypes.util"}
    offenders: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if path.parts[-3:-1] == ("term",) and path.name == "ghostty.py":
            continue  # the engine's own binding: ctypes over the vendored library, no process
        bad = _imports(path) & banned
        calls = {f"{node.func.value.id}.{node.func.attr}" for node in ast.walk(ast.parse(path.read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                 and re.match(r"(system|popen|exec|spawn|fork)", node.func.attr)}
        if bad or calls:
            offenders.append(f"{path.relative_to(SOURCE)}: {sorted(bad | calls)}")
    assert not offenders, f"the TUI can start a process: {offenders}"


def test_no_screen_describes_a_second_connection_as_isolation(serve, wait_until) -> None:
    """V13, Contract §15.3: "Consumers MUST NOT describe a second connection as isolation". A second connection to a
    target shares the target with the first: another login on the device, not a wall between two sessions. The dialog that
    offers a new connection or the reuse of one says what each is (fresh authentication, a connection already open) and
    none of the words that would promise separation."""

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot)
            await open_new_session(app, pilot, wait_until)
            text = " ".join(str(widget.render()) for widget in app.screen.query("RadioButton, Static, Label, Button"))
            assert "New connection" in text and "Reuse a connection" in text, text
            for word in ("isolat", "separate", "private", "sandbox", "walled"):
                assert word not in text.lower(), f"the New session dialog says {word!r}: {text!r}"

    asyncio.run(check())
