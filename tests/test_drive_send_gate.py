"""The sender sees a whole screen before input; a refusal must leave the wire empty."""
from __future__ import annotations

import asyncio
import functools
import inspect
import io
import threading
import time

from drive_support import Rig
from input_server import InputRecordingApp, make_session
from stub_server import resize_requests
from tsbc import _serve

from tsb_tui.model import actions as A
from tsb_tui.screens.terminal import SOURCE_KEY, SOURCE_PASTE


def scenario(test):
    @functools.wraps(test)
    def run(serve, tmp_path, monkeypatch):
        async def check():
            server, url = serve(InputRecordingApp())
            sid = make_session(url)
            async with Rig(url, tmp_path=tmp_path) as rig:
                await rig.login()
                await rig.attach(sid)
                slot = rig.driver.screen().front
                assert any(c['client_id'] == slot.pump.state.client_id for c in slot.pump.state.clients)
                clock = [time.monotonic()]
                rig._gate_clock = clock
                rig.app.idle_clock = lambda: clock[0]
                slot.pump.state._clock = lambda: clock[0]
                await test(rig, server, sid, slot, clock, monkeypatch)
        asyncio.run(check())
    # pytest must see fixtures, not the coroutine's implementation parameters.
    run.__signature__ = inspect.signature(lambda serve, tmp_path, monkeypatch: None)
    return run


async def look(rig):
    answer = await rig.cmd('read', what='screen')
    assert answer['ok'] and answer.get('look'), answer
    assert {'lines', 'cursor', 'cols', 'rows', 'alt', 'cursor_hidden', 'quiet_ms', 'modes'} <= answer.keys()
    return answer['look']


async def send(rig, text='x', **kw):
    return await rig.cmd('type', text=text, timeout_ms=1, **kw)


async def output(rig, server, sid, data=b'echo'):
    # The preceding send's echo must be fully applied before the next target output.
    await barrier(rig, server)
    state = rig.driver.screen().front.pump.state
    before = state.output_bytes_applied
    rig._gate_clock[0] += .001
    with server.lock:
        server._emit_output(server.sessions[sid], data)
    await barrier(rig, server)
    assert state.output_bytes_applied > before, 'output was not applied'


async def barrier(rig, server):
    """A later response proves all earlier output was applied on the same ordered socket."""
    pump = rig.driver.screen().front.pump
    state = pump.state
    before = len(resize_requests(server))
    size, refusals = state.last_size, state.refusal_count
    await pump.resize(state.size_cols, state.size_rows, timeout=0)
    for _ in range(500):
        if (len(resize_requests(server)) > before
                and (state.last_size is not size or state.refusal_count > refusals)):
            await pump._drain()
            return
        await asyncio.sleep(.002)
    raise AssertionError('marker response was not applied')


def refused(answer, code):
    assert answer.get('error') == code and answer.get('sent') is False, answer
    assert 'screen' in answer and 'look' in answer, answer


@scenario
async def test_type_without_a_look_is_refused_and_nothing_reaches_the_stub(rig, server, sid, slot, clock, patch):
    refused(await send(rig), 'no_look')
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


@scenario
async def test_type_after_an_unchanged_look_sends_exactly_its_bytes_once(rig, server, sid, slot, clock, patch):
    answer = await send(rig, 'show clock\r', look=await look(rig))
    assert answer['ok'] and answer['sent'], answer
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == [b'show clock\r']


@scenario
async def test_one_byte_after_the_look_refuses_and_returns_a_screen_and_a_look_that_works(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await output(rig, server, sid, b'z')
    answer = await send(rig, look=old)
    refused(answer, 'screen_changed')
    assert answer['changed'] == ['output']
    assert 'z' in '\n'.join(answer['screen']['lines'])
    assert (await send(rig, look=answer['look']))['ok']


@scenario
async def test_a_frame_queued_but_not_applied_counts_as_a_change(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    pump = slot.pump
    original = pump._deliver_frame
    held = []
    patch.setattr(pump, '_deliver_frame', lambda frame: held.append(frame))
    with server.lock:
        server._send_token(server.sessions[sid], 'handoff')
    for _ in range(500):
        if held:
            break
        await asyncio.sleep(.002)
    assert held
    refused(await send(rig, look=old), 'screen_changed')
    for frame in held:
        original(frame)


@scenario
async def test_bytes_received_but_not_yet_on_the_grid_count_as_a_change(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    started, release = threading.Event(), threading.Event()
    original = slot.pump._sink
    def sink(role, data):
        started.set()
        assert release.wait(10)
        return original(role, data)
    patch.setattr(slot.pump, '_sink', sink)
    try:
        with server.lock:
            server._emit_output(server.sessions[sid], b'unfed')
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(.002)
        assert started.is_set()
        fresh = await look(rig)
        refused(await send(rig, look=fresh), 'screen_changed')
        assert old != fresh
    finally:
        release.set()


@scenario
async def test_a_read_right_after_a_send_mints_no_look_until_output_and_quiet(rig, server, sid, slot, clock, patch):
    assert (await send(rig, look=await look(rig)))['ok']
    answer = await rig.cmd('read', what='screen')
    assert answer['look'] is None or answer.get('look_kind') == 'unsettled', answer
    await output(rig, server, sid)
    clock[0] += 1
    assert (await rig.cmd('read', what='screen'))['look_kind'] == 'ordinary'


@scenario
async def test_a_refusal_right_after_a_send_carries_no_usable_look(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    answer = await send(rig, 'B', look='last')
    assert answer['error'] in ('no_look', 'unsettled'), answer
    assert answer['look'] is None or answer.get('look_kind') == 'unsettled'


@scenario
async def test_an_echo_during_the_send_lock_wait_counts_as_output_since_the_send(rig, server, sid, slot, clock, patch):
    original = slot.pump.send
    async def held(data):
        await output(rig, server, sid, b'early')
        clock[0] += 1
        await original(data)
    patch.setattr(slot.pump, 'send', held)
    answer = await send(rig, look=await look(rig))
    assert answer['ok'] and answer['look_kind'] == 'ordinary', answer


@scenario
async def test_a_brake_goes_into_a_flood_the_senders_own_send_started(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await output(rig, server, sid)
    answer = await rig.cmd('read', what='screen')
    assert answer['look_kind'] == 'unsettled'
    await output(rig, server, sid, b'more')
    assert (await send(rig, '\x03', look=answer['look']))['ok']


@scenario
async def test_only_a_brake_may_name_an_unsettled_look(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await output(rig, server, sid)
    answer = await rig.cmd('read', what='screen')
    refused(await send(rig, look=answer['look']), 'unsettled')


@scenario
async def test_send_history_survives_detach_attach_reattach_and_relogin(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await barrier(rig, server)
    history = rig.driver.commands.send_history[sid]
    for relogin in (False, True):
        await rig.cmd('detach')
        if relogin:
            await rig.cmd('logout')
            await rig.login()
        await rig.attach(sid)
        assert rig.driver.commands.send_history[sid] is history
    pump = rig.driver.screen().front.pump
    prior = pump.state.client_id
    pump._att._ws._writer.transport.abort()
    for _ in range(1000):
        if not pump.state.closed and pump.state.client_id != prior and pump.state.dump_seen:
            break
        await asyncio.sleep(.005)
    assert pump.state.client_id != prior and pump.state.dump_seen
    assert rig.driver.commands.send_history[sid] is history
    # A new attached frame also changes the attachment identity.
    state = rig.driver.screen().front.pump.state
    old = await rig.cmd('read', what='screen')
    state.apply_attached({'client_id': state.client_id, 'session': state.session})
    assert rig.driver.commands.send_history[sid] is history
    if old['look']:
        refused(await send(rig, look=old['look']), 'no_look')


@scenario
async def test_the_attach_dump_is_not_output_since_a_send(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await barrier(rig, server)
    history = rig.driver.commands.send_history[sid]
    # Start a silent send history after the preceding echo, then attach's dump must not clear it.
    history.output_since_send = False
    await rig.cmd('detach')
    await rig.attach(sid)
    clock[0] += 1
    answer = await rig.cmd('read', what='screen')
    assert answer['look'] is None, answer


@scenario
async def test_a_wait_answers_when_its_predicates_hold_with_the_look_state_of_that_moment(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    answer = await rig.cmd('wait', until={'screen': '.*'})
    assert answer['ok'] and 'screen' in answer
    assert answer['look'] is None or answer['look_kind'] == 'unsettled', answer


@scenario
async def test_a_held_waits_screen_block_and_look_are_what_its_predicates_held_on(rig, server, sid, slot, clock, patch):
    original = rig.driver._evaluate_one
    def evaluated(pending):
        original(pending)
        if pending.future.done() and pending.future.result().ok:
            slot.canvas.backend.feed(b'\r\nLATER')
    patch.setattr(rig.driver, '_evaluate_one', evaluated)
    answer = await rig.cmd('wait', until={'screen': '.*'})
    assert answer['look'] and 'LATER' not in '\n'.join(answer['screen']['lines'])


@scenario
async def test_mid_frame_only_a_brake_may_name_a_look(rig, server, sid, slot, clock, patch):
    await output(rig, server, sid, b'\x1b[?2026h')
    answer = await rig.cmd('read', what='screen')
    assert answer['look'] and answer['look_kind'] == 'unsettled'
    refused(await send(rig, look=answer['look']), 'unsettled')
    assert (await send(rig, '\x03', look=answer['look']))['ok']


@scenario
async def test_read_reply_history_and_a_holder_wait_mint_no_look(rig, server, sid, slot, clock, patch):
    for what in ('reply', 'history'):
        assert 'look' not in await rig.cmd('read', what=what)
    assert 'look' not in await rig.cmd('wait', until={'holder': 'me'})
    assert 'look' not in await rig.cmd('wait', until={'settled_ms': 0})


@scenario
async def test_last_never_names_a_refusal_look(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await output(rig, server, sid)
    answer = await send(rig, look=old)
    refused(answer, 'screen_changed')
    refused(await send(rig, look='last'), 'screen_changed')
    assert (await send(rig, look=answer['look']))['ok']


@scenario
async def test_a_send_uses_up_its_look(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    assert (await send(rig, look=old))['ok']
    refused(await send(rig, look=old), 'no_look')


@scenario
async def test_a_look_from_another_session_or_attachment_is_refused(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    other = make_session(rig.url)
    await rig.attach(other)
    refused(await send(rig, look=old), 'no_look')
    await rig.cmd('switch', session=sid)
    await rig.cmd('detach')
    await rig.attach(sid)
    answer = await send(rig, look=old)
    refused(answer, 'no_look')
    assert 'reattach' in answer['why']


@scenario
async def test_a_keyboard_move_and_back_size_change_or_stale_prompt_refuses(rig, server, sid, slot, clock, patch):
    state = slot.pump.state
    for cause in ('keyboard', 'size', 'stale'):
        old = await look(rig)
        if cause == 'keyboard':
            holder = state.holder
            state.apply_token({'holder': None, 'reason': 'owner'})
            state.apply_token({'holder': holder, 'reason': 'owner'})
        elif cause == 'size':
            state.apply_size({'cols': 79, 'rows': 24, 'reason': 'request'})
        else:
            state.apply_stale({'state': 'restored'})
        answer = await send(rig, look=old)
        refused(answer, 'screen_changed')
        assert cause in answer['changed']


@scenario
async def test_a_pending_stale_choice_refuses_every_send(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    slot.pump.state.stale_state = "pending"
    for text in ('x', '\x03'):
        refused(await send(rig, text, look=old), 'stale')


@scenario
async def test_full_screen_statement_both_ways_for_type_and_text_keys(rig, server, sid, slot, clock, patch):
    for mode in (b'\x1b[?1049h', b'\x1b[?1049l\x1b[?25l'):
        await output(rig, server, sid, mode)
        clock[0] += 1
        for fields in ({'op': 'type', 'text': 'x'}, {'op': 'keys', 'keys': ['slash']},
                       {'op': 'keys', 'keys': ['shift+a']}):
            old = await look(rig)
            op = fields['op']
            refused(await rig.cmd(op, **{k:v for k,v in fields.items() if k != 'op'}, look=old), 'full_screen')
        assert (await send(rig, look=await look(rig), full_screen=True))['ok']
        clock[0] += 1
    await output(rig, server, sid, b'\x1b[?1049l\x1b[?25h')
    clock[0] += 1
    refused(await send(rig, look=await look(rig), full_screen=True), 'not_full_screen')


@scenario
async def test_type_refuses_lf_and_crlf_and_a_bare_enter_is_cr(rig, server, sid, slot, clock, patch):
    for text in ('x\n', 'x\r\n', '\n'):
        refused(await send(rig, text, look=await look(rig)), 'multi_line')
    assert (await send(rig, '\r', look=await look(rig)))['ok']
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == [b'\r']


@scenario
async def test_enter_is_a_line_end_in_any_keyboard_mode(rig, server, sid, slot, clock, patch):
    for mode in (b'\x1b[>0u', b'\x1b[>3u'):
        await output(rig, server, sid, mode)
        for key in ('enter', 'ctrl+m', 'ctrl+j'):
            refused(await rig.cmd('keys', keys=[key, 'x'], look=await look(rig)), 'multi_line')


@scenario
async def test_a_key_whose_legacy_payload_ends_in_cr_or_lf_is_a_line_end(rig, server, sid, slot, clock, patch):
    for key in ('ctrl+M', 'alt+enter'):
        refused(await rig.cmd('keys', keys=[key, 'x'], look=await look(rig)), 'multi_line')


@scenario
async def test_ctrl_j_is_a_line_end_but_not_a_bare_enter(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await barrier(rig, server)
    history = rig.driver.commands.send_history[sid]
    history.output_since_send = False
    clock[0] += 31
    old = await look(rig)
    refused(await rig.cmd('keys', keys=['ctrl+j'], look=old), 'silent')
    refused(await rig.cmd('keys', keys=['ctrl+j', 'enter'], look=old), 'multi_line')


@scenario
async def test_multi_line_refused_for_type_and_for_keys(rig, server, sid, slot, clock, patch):
    refused(await send(rig, 'a\rb\r', look=await look(rig)), 'multi_line')
    refused(await rig.cmd('keys', keys=['a', 'enter', 'b', 'enter'], look=await look(rig)), 'multi_line')


@scenario
async def test_a_tab_and_a_line_end_never_go_in_one_send(rig, server, sid, slot, clock, patch):
    refused(await send(rig, 'foo\t\r', look=await look(rig)), 'multi_line')
    refused(await rig.cmd('keys', keys=['tab', 'enter'], look=await look(rig)), 'multi_line')


@scenario
async def test_type_refuses_control_bytes_but_a_lone_stop_byte_is_a_brake(rig, server, sid, slot, clock, patch):
    for text in ('a\x04', '\x1b', 'a\x03', '\x00'):
        refused(await send(rig, text, look=await look(rig)), 'control_bytes')
    assert (await send(rig, '\x1e', look=await look(rig)))['ok']


@scenario
async def test_a_control_key_goes_alone_in_its_keys_command(rig, server, sid, slot, clock, patch):
    for keys in (['l', 's', 'ctrl+d'], ['up', 'ctrl+c'], ['down', 'enter'], ['f1', 'a'], ['escape', 'x']):
        refused(await rig.cmd('keys', keys=keys, look=await look(rig)), 'control_bytes')


@scenario
async def test_a_brake_is_recognised_by_its_bytes_under_every_alias_and_as_type(rig, server, sid, slot, clock, patch):
    await output(rig, server, sid, b'\x1b[?1049h')
    for key in ('ctrl+c', 'ctrl+^', 'ctrl+6', 'ctrl+shift+6', 'ctrl+circumflex_accent'):
        answer = await rig.cmd('keys', keys=[key], look=await look(rig), timeout_ms=1)
        assert answer['ok'], answer
        await output(rig, server, sid)
        clock[0] += 1
    await output(rig, server, sid, b'\x1b[>3u')
    for text in ('\x03', '\x1e'):
        assert (await send(rig, text, look=await look(rig)))['ok']
        await output(rig, server, sid)
        clock[0] += 1


@scenario
async def test_a_brake_passes_output_but_not_a_keyboard_move_or_alt_screen_change(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await output(rig, server, sid)
    assert (await send(rig, '\x03', look=old))['ok']
    await output(rig, server, sid)
    clock[0] += 1
    old = await look(rig)
    await output(rig, server, sid, b'\x1b[?1049h')
    refused(await send(rig, '\x03', look=old), 'screen_changed')
    old = await look(rig)
    holder = slot.pump.state.holder
    slot.pump.state.apply_token({'holder': None})
    slot.pump.state.apply_token({'holder': holder})
    refused(await send(rig, '\x03', look=old), 'screen_changed')


@scenario
async def test_a_brake_on_a_look_older_than_the_brake_bound_gets_the_strict_check(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    clock[0] += 11
    await output(rig, server, sid)
    refused(await send(rig, '\x03', look=old), 'screen_changed')


@scenario
async def test_after_silence_a_look_counts_again_for_a_brake_or_a_bare_enter_only(rig, server, sid, slot, clock, patch):
    for text in ('\r', '\x03'):
        # Suppress the echo at the processor boundary to represent a truly silent target.
        original = server._emit_output
        patch.setattr(server, '_emit_output', lambda sess, data: None)
        await send(rig, look=await look(rig))
        await barrier(rig, server)
        clock[0] += 31
        answer = await rig.cmd('read', what='screen')
        assert answer['look_kind'] == 'after_silence', answer
        refused(await send(rig, 'command\r', look=answer['look']), 'silent')
        assert (await send(rig, text, look=answer['look']))['ok']
        patch.setattr(server, '_emit_output', original)
        await output(rig, server, sid)
        clock[0] += 1


@scenario
async def test_silence_counts_only_after_the_silence_time_with_nothing_received(rig, server, sid, slot, clock, patch):
    patch.setattr(server, '_emit_output', lambda sess, data: None)
    await send(rig, look=await look(rig))
    await barrier(rig, server)
    clock[0] += 29
    assert (await rig.cmd('read', what='screen'))['look'] is None
    clock[0] += 2
    assert (await rig.cmd('read', what='screen'))['look_kind'] == 'after_silence'


@scenario
async def test_output_after_a_send_ends_the_silence_rule(rig, server, sid, slot, clock, patch):
    await send(rig, look=await look(rig))
    await barrier(rig, server)
    history = rig.driver.commands.send_history[sid]
    history.output_since_send = False
    clock[0] += 31
    assert (await rig.cmd('read', what='screen'))['look_kind'] == 'after_silence'
    await output(rig, server, sid)
    clock[0] += 1
    assert (await rig.cmd('read', what='screen'))['look_kind'] == 'ordinary'


@scenario
async def test_owner_take_over_follows_the_ruling(rig, server, sid, slot, clock, patch):
    async with Rig(rig.url, tmp_path=rig._tmp) as peer:
        await peer.login(_serve.PEER)
        await peer.attach(sid)
        peer_id = peer.driver.screen().front.pump.state.client_id
        assert (await rig.cmd('hand', to=peer_id))['ok']
        refused(await send(rig), 'no_look')
        assert (await rig.cmd('take'))['ok']
        assert slot.pump.state.holds_keyboard
        assert server.received(slot.pump.state.client_id) == []
        answer = await rig.cmd('take')
        assert answer['error'] == 'not_available'
        assert answer['why'] == A.blocked('take_back', A.Facts(slot.pump.state))
        assert (await rig.cmd('hand', to=peer_id))['ok']
        assert (await send(rig, look=await look(rig)))['ok']
        await rig.cmd('wait', until={'holder': 'me'})


@scenario
async def test_the_scroll_view_and_the_guard_answer_before_the_gate(rig, server, sid, slot, clock, patch):
    await rig.app_call(rig.driver.screen().do_scroll)
    answer = await send(rig)
    assert answer['error'] == 'not_available' and 'screen' not in answer
    await rig.app_call(rig.driver.screen().close_scroll_view)
    state = slot.pump.state
    state.owner = 'another'
    state.holder = None
    answer = await send(rig)
    assert answer['error'] == 'not_typist' and 'screen' not in answer
    state.owner = state.user
    refused(await send(rig), 'no_look')


@scenario
async def test_under_drive_send_input_refuses_typed_sources_not_from_the_gate(rig, server, sid, slot, clock, patch):
    screen = rig.driver.screen()
    for source in (SOURCE_KEY, SOURCE_PASTE):
        assert not await rig.app_call(screen.send_input, source, b'bad', slot)
    await rig.cmd('press', keys=['ctrl+backslash', 'ctrl+backslash'])
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


@scenario
async def test_press_drops_keys_bound_for_the_session_and_counts_them(rig, server, sid, slot, clock, patch):
    answer = await rig.cmd('press', keys=['a', 'enter'])
    assert answer['dropped'] == 2, answer
    answer = await rig.cmd('press', keys=['ctrl+backslash', 'left_square_bracket', 'escape'])
    assert answer['dropped'] == 0, answer
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


@scenario
async def test_refusals_never_send_a_key_of_their_own(rig, server, sid, slot, clock, patch):
    for text in ('x', 'x\nx', '\x04'):
        assert not (await send(rig, text))['ok']
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


@scenario
async def test_a_decision_is_never_reopened_by_an_await_before_pump_send(rig, server, sid, slot, clock, patch):
    commands = rig.driver.commands
    ran = []
    original = commands._record_send
    def decision(*args):
        original(*args)
        asyncio.get_running_loop().call_soon(ran.append, True)
    patch.setattr(commands, '_record_send', decision)
    original_send = slot.pump.send
    async def checked(data):
        assert not ran, 'the loop ran between the decision and pump.send'
        await original_send(data)
    patch.setattr(slot.pump, 'send', checked)
    assert (await send(rig, look=await look(rig)))['ok']


@scenario
async def test_type_waits_for_output_and_quiet_and_returns_the_settled_screen(rig, server, sid, slot, clock, patch):
    # Use the real clock for the settle timer.
    slot.pump.state._clock = time.monotonic
    rig.app.idle_clock = time.monotonic
    answer = await rig.cmd('type', text='settle\r', look=await look(rig), timeout_ms=3000)
    assert answer['ok'] and answer['sent'] and answer['settled'] and answer['look_kind'] == 'ordinary', answer
    assert 'settle' in '\n'.join(answer['screen']['lines'])


@scenario
async def test_type_timeout_returns_sent_screen_and_a_brake_can_follow(rig, server, sid, slot, clock, patch):
    answer = await send(rig, look=await look(rig))
    assert answer['sent'] and answer['settled'] is False and 'screen' in answer
    await output(rig, server, sid)
    answer = await rig.cmd('read', what='screen')
    assert (await send(rig, '\x03', look=answer['look']))['ok']


def test_the_gate_switch_loads_from_a_config_file_only(tmp_path, monkeypatch):
    import pytest

    from tsb_tui import config
    path = tmp_path / 'gate.toml'
    path.write_text('[drive]\nsend_gate = false\n')
    assert config.load(config_file=path, environ={})['drive.send_gate'] is False
    with pytest.raises(config.ConfigError, match='config file only'):
        config.load(environ={'TSB_TUI_DRIVE_SEND_GATE': 'false'})
    with pytest.raises(config.ConfigError, match='config file only'):
        config.load(environ={}, overrides=['drive.send_gate=false'])
    doc = {'profiles': {'drive': {'drive': {'send_gate': False}}}}
    with pytest.raises(config.ConfigError, match='config file only'):
        config._profile_overlay(doc, 'drive')
    with pytest.raises(config.ConfigError, match='config file only'):
        config._profile_overlay(doc, 'default')


def test_config_load_refuses_a_quiet_window_below_800_ms_and_a_silence_time_below_5_s(tmp_path):
    import pytest

    from tsb_tui import config
    for key, value in (('settled_ms', 799), ('wait_timeout_ms', 4999)):
        path = tmp_path / 'floor.toml'
        path.write_text(f'[drive]\n{key} = {value}\n')
        for args in ({'config_file': path}, {'overrides': [f'drive.{key}={value}']},
                     {'environ': {f'TSB_TUI_DRIVE_{key.upper()}': str(value)}}):
            with pytest.raises(config.ConfigError, match=f'drive.{key}'):
                config.load(**args)


def test_ready_reports_the_effective_quiet_window_and_silence_time(serve, monkeypatch, capsys):
    from test_drive_commands import _main
    _server, url = serve()
    rc, lines, err = _main(monkeypatch, capsys, ['--broker', url, '--set', 'drive.settled_ms=900',
                                               '--set', 'drive.wait_timeout_ms=6000'], '{"op":"quit"}\n')
    assert rc == 0, err
    assert lines[0]['send_gate'] is True
    assert lines[0]['settled_ms'] == 900 and lines[0]['wait_timeout_ms'] == 6000


def test_gate_off_is_reported_in_ready_and_every_send_answer(serve, tmp_path, monkeypatch, capsys):
    from test_drive_commands import _main
    _server, url = serve(InputRecordingApp())
    path = tmp_path / 'gate.toml'
    path.write_text('[drive]\nsend_gate = false\n')
    record = tmp_path / 'record.txt'
    rc, lines, err = _main(monkeypatch, capsys, ['--broker', url, '--config', str(path), '--record', str(record)],
                           '{"op":"type","text":"x"}\n{"op":"quit"}\n')
    assert rc == 0, err
    assert lines[0]['send_gate'] is False and lines[1]['send_gate'] is False
    assert '"send_gate":false' in record.read_text()
    async def check():
        async with Rig(url, tmp_path=tmp_path, record=io.StringIO()) as rig:
            await rig.login()
            await rig.attach(make_session(url))
            rig.app.cfg.set_runtime('drive.send_gate', False)
            answer = await send(rig)
            assert answer['ok'] and answer['send_gate'] is False
    asyncio.run(check())


@scenario
async def test_a_young_brake_refuses_a_keyboard_frame_queued_behind_unfed_output(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    started, release = threading.Event(), threading.Event()
    original = slot.pump._sink
    def sink(role, data):
        started.set()
        assert release.wait(10)
        return original(role, data)
    patch.setattr(slot.pump, '_sink', sink)
    try:
        with server.lock:
            server._emit_output(server.sessions[sid], b'held')
            server._send_token(server.sessions[sid], 'handoff')
        for _ in range(500):
            if started.is_set() and slot.pump.state.received_keyboard_items > slot.pump.state.applied_keyboard_items:
                break
            await asyncio.sleep(.002)
        assert started.is_set()
        refused(await send(rig, '\x03', look=old), 'screen_changed')
    finally:
        release.set()
