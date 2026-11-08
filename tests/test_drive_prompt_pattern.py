"""The send gate checks the cursor row against the target's prompt pattern at a line send."""
from __future__ import annotations

import asyncio
import functools
import inspect
import os
import time
from pathlib import Path

import pytest
from drive_support import Rig
from input_server import InputRecordingApp, make_session
from stub_server import SlowChunkedApp, StubServer
from test_drive_send_gate import barrier, output
from tsb_contract_client import Client


class PromptApp(InputRecordingApp):
    def __init__(self, *, prompt_pattern=None, target_read_error=None):
        super().__init__()
        self.prompt_pattern = prompt_pattern
        self.target_read_error = target_read_error
        self.target_reads = 0

    def _get_target(self, target_id):
        self.target_reads += 1
        if self.target_read_error:
            status, code, message = self.target_read_error
            return status, [], {"error": {"code": code, "message": message}}
        status, headers, record = super()._get_target(target_id)
        if status == 200 and self.prompt_pattern is not None:
            record = dict(record, prompt_pattern=self.prompt_pattern)
        return status, headers, record


def scenario(*, pattern: str | None = None, target_read_error=None):
    return lambda test: _scenario(test, pattern=pattern, target_read_error=target_read_error)


def _scenario(test, *, pattern, target_read_error):
    """Decorator: build a standard rig with optional prompt_pattern on the target."""
    @functools.wraps(test)
    def run(serve, tmp_path, monkeypatch):
        async def check():
            server, url = serve(PromptApp(prompt_pattern=pattern,
                                                  target_read_error=target_read_error))
            sid = make_session(url)
            async with Rig(url, tmp_path=tmp_path) as rig:
                await rig.login()
                await rig.attach(sid)
                slot = rig.driver.screen().front
                clock = [time.monotonic()]
                rig._gate_clock = clock
                rig.app.idle_clock = lambda: clock[0]
                slot.pump.state._clock = lambda: clock[0]
                await test(rig, server, sid, slot, clock, monkeypatch)
        asyncio.run(check())
    run.__signature__ = inspect.signature(lambda serve, tmp_path, monkeypatch: None)
    return run


async def look(rig):
    answer = await rig.cmd("read", what="screen")
    assert answer["ok"] and answer.get("look"), answer
    return answer["look"]


async def send_line(rig, text="show version\r", **kw):
    return await rig.cmd("type", text=text, timeout_ms=1, **kw)


async def send_key(rig, key="enter", **kw):
    return await rig.cmd("keys", keys=[key], timeout_ms=1, **kw)


def refused(answer, code):
    assert answer.get("error") == code and answer.get("sent") is False, answer
    assert "screen" in answer and "look" in answer, answer


def sent(answer):
    assert answer.get("ok") and answer.get("sent"), answer



@scenario(pattern=r"rtr-core-01[#>]")
async def test_a_target_pattern_refuses_a_line_send_whose_cursor_row_does_not_match(
        rig, server, sid, slot, clock, patch):
    lk = await look(rig)
    answer = await send_line(rig, "show version\r", look=lk)
    refused(answer, "prompt_mismatch")
    # answer must name the pattern
    assert "rtr-core-01" in answer.get("why", ""), answer
    # nothing was sent to the stub
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []





@scenario(pattern=r"rtr-core-01[#>]")
async def test_the_pattern_checks_only_line_sends_not_marked_full_screen(
        rig, server, sid, slot, clock, patch):
    # A brake goes through (line-ending but brake => not checked)
    lk = await look(rig)
    answer = await rig.cmd("type", text="\x03", timeout_ms=1, look=lk)
    sent(answer)

    await barrier(rig, server)
    clock[0] += 1
    # A non-line-ending key (space) goes through
    lk = await look(rig)
    answer = await send_key(rig, "space", look=lk)
    sent(answer)

    await barrier(rig, server)
    clock[0] += 1
    # A line send with full_screen: true is not checked (full_screen skips the prompt check)
    lk = await look(rig)
    answer = await rig.cmd("type", text="show version\r", timeout_ms=1,
                           look=lk, full_screen=True)
    # full_screen may be refused with not_full_screen (no program took the screen in the stub),
    # but not with prompt_mismatch; the pattern check must not fire first
    assert answer.get("error") != "prompt_mismatch", answer





@scenario(pattern=None)
async def test_no_pattern_or_a_404_target_record_means_the_look_alone(
        rig, server, sid, slot, clock, patch):
    # With no pattern the send goes through on a valid look (the look alone decides)
    lk = await look(rig)
    answer = await send_line(rig, "show version\r", look=lk)
    # The stub echoes nothing so this may be unsettled/no_look after the gate passes,
    # but it must NOT be refused with prompt_mismatch or prompt_unusable
    assert answer.get("error") not in ("prompt_mismatch", "prompt_unusable"), answer
    assert answer.get("sent") is True, answer





@scenario(target_read_error=(503, "inventory_unavailable",
                              "the inventory index is currently unavailable"))
async def test_an_unusable_pattern_or_an_unreadable_record_fails_closed_and_is_fetched_again(
        rig, server, sid, slot, clock, patch):
    # The 503 on the target read means prompt_unusable must refuse line sends
    lk = await look(rig)
    answer = await send_line(rig, "show version\r", look=lk)
    refused(answer, "prompt_unusable")
    # why must say something meaningful
    assert answer.get("why"), answer

    assert server.target_reads == 2
    # On the NEXT line send drive retries the fetch; stub still 503s
    lk = answer["look"]   # refusal's look, named by id
    # but "last" never names a refusal's look, so we use the id
    answer2 = await send_line(rig, "show version\r", look=lk)
    refused(answer2, "prompt_unusable")
    assert server.target_reads == 3





@scenario(pattern=r".*")
async def test_the_pattern_never_lets_through_a_send_the_look_refuses(
        rig, server, sid, slot, clock, patch):
    # Sending with no look is refused with no_look, never prompt_mismatch
    answer = await send_line(rig, "show version\r")
    refused(answer, "no_look")
    # pattern is .* which matches everything — but the look check comes first
    assert answer.get("error") == "no_look", answer


@scenario(pattern=r"rtr-core-01(\(config[^)]*\))?[#>]")
async def test_matching_trimmed_prompt_sends_and_typed_suffix_refuses(rig, server, sid, slot, clock, patch):
    await output(rig, server, sid, b"\r\x1b[2Krtr-core-01(config-if)#   ")
    sent(await send_line(rig, look=await look(rig)))
    await output(rig, server, sid, b"\r\x1b[2Krtr-core-01#reload")
    clock[0] += 1
    refused(await send_key(rig, look=await look(rig)), "prompt_mismatch")


@scenario(target_read_error=(404, "target_not_found", "no such target"))
async def test_missing_target_stays_unchecked_until_reattach(rig, server, sid, slot, clock, patch):
    assert server.target_reads == 1
    server.target_read_error = None
    server.prompt_pattern = "never-matches"
    sent(await send_line(rig, look=await look(rig)))
    assert server.target_reads == 1
    await rig.cmd("detach")
    await rig.attach(sid)
    assert server.target_reads == 2
    clock[0] += 6
    refused(await send_key(rig, look=await look(rig)), "prompt_mismatch")


@scenario(pattern="never-matches")
async def test_target_edits_are_seen_only_on_next_attach(rig, server, sid, slot, clock, patch):
    server.prompt_pattern = ".*"
    refused(await send_line(rig, look=await look(rig)), "prompt_mismatch")
    assert server.target_reads == 1
    await rig.cmd("detach")
    await rig.attach(sid)
    assert server.target_reads == 2
    sent(await send_line(rig, look=await look(rig)))


@pytest.mark.parametrize("pattern", ["[", r"\s", "a{4097}", 12, None])
def test_bad_pattern_fails_closed_without_refetch(serve, tmp_path, monkeypatch, pattern):
    async def check():
        app = PromptApp()
        server, url = serve(app)
        sid = make_session(url)
        original = app._get_target

        def record(target):
            status, headers, body = original(target)
            return status, headers, dict(body, prompt_pattern=pattern)

        monkeypatch.setattr(app, "_get_target", record)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            for _ in range(2):
                answer = await send_line(rig, look=await look(rig))
                refused(answer, "prompt_unusable")
                assert "parsed or built" in answer["why"]
            assert server.target_reads == 1
    asyncio.run(check())


@scenario(target_read_error=(503, "inventory_unavailable", "inventory down"))
async def test_failed_read_recovers_at_next_checked_send(rig, server, sid, slot, clock, patch):
    refused(await send_line(rig, look=await look(rig)), "prompt_unusable")
    server.target_read_error = None
    server.prompt_pattern = ".*"
    sent(await send_line(rig, look=await look(rig)))
    assert server.target_reads == 3


@scenario(target_read_error=(503, "inventory_unavailable", "inventory down"))
async def test_unchecked_send_does_not_retry_failed_read(rig, server, sid, slot, clock, patch):
    sent(await send_key(rig, key="space", look=await look(rig)))
    assert server.target_reads == 1


@scenario(pattern="never-matches")
async def test_actual_full_screen_line_send_bypasses_pattern(rig, server, sid, slot, clock, patch):
    await output(rig, server, sid, b"\x1b[?1049h")
    sent(await send_line(rig, look=await look(rig), full_screen=True))


@scenario(pattern="never-matches")
async def test_bare_enter_after_silence_is_checked(rig, server, sid, slot, clock, patch):
    sent(await send_line(rig, text="x", look=await look(rig)))
    await barrier(rig, server)
    history = rig.driver.commands.history(sid)
    history.output_since_send = False
    clock[0] += 31
    answer = await rig.cmd("read", what="screen")
    assert answer["look_kind"] == "after_silence", answer
    refused(await send_key(rig, look=answer["look"]), "prompt_mismatch")


@scenario(pattern=".*")
async def test_transport_failure_retries_and_rechecks_output_after_await(rig, server, sid, slot, clock, patch):
    def unavailable(target):
        raise OSError("transport unavailable")
    patch.setattr(rig.app.client, "get_target", unavailable)
    await rig.cmd("detach")
    await rig.attach(sid)
    answer = await send_line(rig, look=await look(rig))
    refused(answer, "prompt_unusable")
    assert "transport unavailable" in answer["why"]
    old = await look(rig)
    original = rig.driver.commands._read_prompt

    async def recovering(current):
        await output(rig, server, sid, b"new output")
        patch.setattr(rig.app.client, "get_target", lambda target: {"id": target, "prompt_pattern": ".*"})
        await original(current)

    patch.setattr(rig.driver.commands, "_read_prompt", recovering)
    refused(await send_line(rig, look=old), "screen_changed")


def test_stub_server_serves_target_prompt_pattern():
    with StubServer(SlowChunkedApp(prompt_pattern="device[#>]")) as url:
        record = Client(url).get_target("device")
        assert record["prompt_pattern"] == "device[#>]"


@scenario(pattern="never-matches")
async def test_command_prompt_cannot_override_target_pattern(rig, server, sid, slot, clock, patch):
    refused(await send_line(rig, look=await look(rig), prompt=".*"), "prompt_mismatch")


@scenario(target_read_error=(503, "inventory_unavailable", "inventory down"))
async def test_no_look_error_precedes_unusable_pattern(rig, server, sid, slot, clock, patch):
    refused(await send_line(rig), "no_look")
    assert server.target_reads == 2


@scenario(pattern=".*")
async def test_non_object_target_record_is_retried(rig, server, sid, slot, clock, patch):
    responses = iter([None, [], {"id": slot.target, "prompt_pattern": "never-matches"}])
    patch.setattr(rig.app.client, "get_target", lambda target: next(responses))
    await rig.cmd("detach")
    await rig.attach(sid)
    refused(await send_line(rig, look=await look(rig)), "prompt_unusable")
    refused(await send_line(rig, look=await look(rig)), "prompt_mismatch")


def test_new_reads_target_pattern_for_the_opened_session(serve, tmp_path):
    async def check():
        server, url = serve(PromptApp(prompt_pattern="never-matches"))
        async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
            await rig.login()
            answer = await rig.cmd("new", target="normal")
            assert answer["ok"], answer
            assert server.target_reads >= 1
            refused(await send_line(rig, look=await look(rig)), "prompt_mismatch")
    asyncio.run(check())


@scenario(pattern=".*")
async def test_attach_does_not_front_a_slot_closed_during_target_read(rig, server, sid, slot, clock, patch):
    import threading

    entered, release = threading.Event(), threading.Event()

    def target_record(target):
        entered.set()
        assert release.wait(5)
        return {"id": target, "prompt_pattern": ".*"}

    patch.setattr(rig.app.client, "get_target", target_record)
    fronts = []
    original = rig.driver.screen().show_front
    patch.setattr(rig.driver.screen(), "show_front", lambda current: (fronts.append(current), original(current))[-1])
    pending = rig.send("attach", session=sid)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await rig.app_call(rig.driver.screen().action_detach)
    finally:
        release.set()
    answer = await pending
    assert answer.get("error") == "attach_failed", answer
    assert fronts == [slot]  # the initial existing-slot switch, never after the read
    assert rig.driver.screen().front is None
    assert sid not in rig.driver.commands._prompts


def test_attach_with_send_gate_off_never_reads_target(serve, tmp_path, monkeypatch):
    async def check():
        _server, url = serve(PromptApp())
        sid = make_session(url)
        path = Path(os.environ["XDG_CONFIG_HOME"]) / "tsb-tui" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[drive]\nsend_gate = false\n")
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            reads = []
            def unexpected(target):
                reads.append(target)
                raise OSError("target read must not run")
            monkeypatch.setattr(rig.app.client, "get_target", unexpected)
            await rig.attach(sid)
            assert reads == []
            assert rig.driver.commands._prompts == {}
    asyncio.run(check())


@scenario(pattern=".*")
async def test_target_record_id_must_match_and_is_retried(rig, server, sid, slot, clock, patch):
    # Exercise both malformed identities in one scenario: neither may disable the pattern.
    reads = []
    records = iter([{"prompt_pattern": ".*"}, {"prompt_pattern": ".*"},
                    {"id": "another-target", "prompt_pattern": ".*"},
                    {"id": slot.target, "prompt_pattern": "never-matches"}])
    def target_record(target):
        reads.append(target)
        return next(records)
    patch.setattr(rig.app.client, "get_target", target_record)
    await rig.cmd("detach")
    await rig.attach(sid)
    for _ in range(2):
        answer = await send_line(rig, look=await look(rig))
        refused(answer, "prompt_unusable")
        assert "id" in answer["why"] and "target" in answer["why"]
    refused(await send_line(rig, look=await look(rig)), "prompt_mismatch")
    assert reads == [slot.target] * 4


@scenario(pattern=".*")
async def test_prompt_cache_is_dropped_on_every_close_without_losing_history(rig, server, sid, slot, clock, patch):
    commands = rig.driver.commands
    history = commands.history(sid)
    history.count = 7
    for route in ("detach", "remove", "release", "end"):
        screen = rig.driver.screen()
        current = screen.slot_for(sid)
        assert sid in commands._prompts
        if route == "detach":
            await rig.cmd("detach")
        elif route == "remove":
            await asyncio.to_thread(rig.app.client.remove_client, sid, current.pump.state.client_id)
            await rig.driver.condition(lambda current=current: True if current.pump.state.closed else None, sid, "removal")
        elif route == "release":
            await rig.app_call(screen._release_slot, current)
        else:
            assert (await rig.cmd("end", session=sid, confirm=True))["ok"]
            assert (await rig.cmd("wait", session=sid, until={"ended": True}))["ok"]
        assert sid not in commands._prompts, route
        assert commands.history(sid) is history and history.count == 7
        if route != "end":
            if screen.slot_for(sid) is not None:
                await rig.app_call(screen._close_slot, current)
            await rig.attach(sid)
            assert commands._prompts[sid].state is screen.slot_for(sid).pump.state
