"""Change causes, unsent input, and bounded retention of still-usable looks."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from test_drive_send_gate import barrier, look, output, refused, scenario, send

from tsb_tui import config


def fixture_frame(file, kind, **fields):
    path = Path(__file__).resolve().parents[1] / 'contract/fixtures/C2' / file
    instances = json.loads(path.read_text())['case']['instances']
    return next(item['doc'] for item in instances if item['valid'] and item['doc']['t'] == kind
                and all(item['doc'].get(key) == value for key, value in fields.items()))


async def queued_frame(rig, server, sid, slot, patch, frame):
    await barrier(rig, server)
    old = await look(rig)
    held = []
    original = slot.pump._deliver_frame
    with patch.context() as held_patch:
        held_patch.setattr(slot.pump, '_deliver_frame', held.append)
        with server.lock:
            server._broadcast(server.sessions[sid], lambda cid, ws: ws.send_text(json.dumps(frame)))
        for _ in range(500):
            if held:
                break
            await asyncio.sleep(.002)
        assert held, 'frame was not received'
        answer = await send(rig, look=old)
    for item in held:
        original(item)
    return answer


@scenario
async def test_queued_token_names_only_keyboard(rig, server, sid, slot, clock, patch):
    frame = fixture_frame('ws-frames-shape-v0.2.json', 'token')
    answer = await queued_frame(rig, server, sid, slot, patch, frame)
    refused(answer, 'screen_changed')
    assert answer['changed'] == ['keyboard']


@scenario
async def test_queued_size_names_only_size(rig, server, sid, slot, clock, patch):
    frame = fixture_frame('size-coalesced.json', 'size')
    answer = await queued_frame(rig, server, sid, slot, patch, frame)
    refused(answer, 'screen_changed')
    assert answer['changed'] == ['size']


@scenario
async def test_queued_stale_names_only_stale(rig, server, sid, slot, clock, patch):
    frame = fixture_frame('stale-flow.json', 'stale')
    answer = await queued_frame(rig, server, sid, slot, patch, frame)
    refused(answer, 'screen_changed')
    assert answer['changed'] == ['stale']


@scenario
async def test_clients_notices_and_prompts_do_not_change_a_look(rig, server, sid, slot, clock, patch):
    frames = [fixture_frame('ws-frames-shape-v0.2.json', kind) for kind in ('notice', 'quiet_prompt')]
    frames.append(fixture_frame('stale-flow.json', 'stale_prompt'))
    frames.append({'t': 'clients', 'v': 1, 'session_id': sid, 'clients': slot.pump.state.clients})
    for frame in frames:
        clock[0] += 1
        answer = await queued_frame(rig, server, sid, slot, patch, frame)
        assert answer['ok'], answer
        # Pending stale choices are a separate refusal, not a change-counter cause.
        slot.pump.state.stale_prompt = None
        await barrier(rig, server)


async def unsent_history(rig, server, sid, slot, clock, patch, *, close):
    await send(rig, look=await look(rig))
    await output(rig, server, sid)
    clock[0] += 1
    old = await look(rig)
    commands = rig.driver.commands
    history = commands.history(sid)
    before = asdict(history)
    original = rig.driver.screen().send_input
    async def not_sent(*args, **kwargs):
        assert history.count == before['count'] + 1, 'record before awaiting send_input'
        assert not history.output_since_send
        if close:
            slot.closed = True
            return await original(*args, **kwargs)
        return False
    with patch.context() as refused_patch:
        refused_patch.setattr(rig.driver.screen(), 'send_input', not_sent)
        answer = await send(rig, look=old)
    slot.closed = False
    assert answer['error'] == 'not_attached' and answer['sent'] is False, answer
    assert commands.history(sid) is history
    assert asdict(history) == before
    assert (await send(rig, look=old))['ok']


@scenario
async def test_unsent_input_restores_the_previous_send_history(rig, server, sid, slot, clock, patch):
    await unsent_history(rig, server, sid, slot, clock, patch, close=False)


@scenario
async def test_a_slot_closed_at_send_restores_the_previous_send_history(rig, server, sid, slot, clock, patch):
    await unsent_history(rig, server, sid, slot, clock, patch, close=True)


async def assert_dropped(rig, old):
    assert old not in rig.driver.commands._looks
    answer = await send(rig, '\x03', look=old)
    refused(answer, 'no_look')
    assert answer['why'] == 'that look is too old; nothing was sent'


@scenario
async def test_mint_drops_looks_from_an_ended_attachment(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await rig.cmd('detach')
    await rig.attach(sid)
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_drops_looks_from_a_previous_attachment_generation(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    state = slot.pump.state
    state.apply_attached({'client_id': state.client_id, 'session': state.session})
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_drops_looks_spent_by_a_send(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await send(rig, look=old)
    await output(rig, server, sid)
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_drops_looks_after_keyboard_counts_move(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    state = slot.pump.state
    holder = state.holder
    state.apply_token({'holder': None})
    state.apply_token({'holder': holder})
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_drops_looks_after_stale_counts_move(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    frame = fixture_frame('stale-flow.json', 'stale', state='chosen')
    slot.pump.state.apply_stale(frame)
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_keeps_output_lagging_looks_until_the_brake_age_bound(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    await output(rig, server, sid)
    await look(rig)
    assert old in rig.driver.commands._looks
    clock[0] += rig.app.cfg['drive.brake_look_max_ms'] / 1000 + .001
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_keeps_size_lagging_looks_until_the_brake_age_bound(rig, server, sid, slot, clock, patch):
    old = await look(rig)
    slot.pump.state.apply_size(fixture_frame('size-coalesced.json', 'size'))
    await look(rig)
    assert old in rig.driver.commands._looks
    clock[0] += rig.app.cfg['drive.brake_look_max_ms'] / 1000 + .001
    await look(rig)
    await assert_dropped(rig, old)


@scenario
async def test_mint_retains_at_most_64_looks_per_session_oldest_first(rig, server, sid, slot, clock, patch):
    from input_server import make_session
    other = make_session(rig.url)
    await rig.attach(other)
    other_look = await look(rig)
    await rig.cmd('switch', session=sid)
    ids = [await look(rig) for _ in range(66)]
    kept = rig.driver.commands._looks
    assert [ident for ident, seen in kept.items() if seen.session == sid] == ids[-64:]
    assert other_look in kept
    assert len(set(ids)) == 66, 'eviction must never reuse a look id'
    await assert_dropped(rig, ids[0])
    assert (await send(rig, look=ids[-1]))['ok']


@pytest.mark.parametrize('key,value', [('size.auto_request', 'true'), ('drive.send_gate', 'false')])
def test_profile_table_refusal_names_its_key_and_only_the_gate_mentions_the_gate(tmp_path, key, value):
    path = tmp_path / 'profiles.toml'
    path.write_text(f'[profiles.phone]\n{key} = {value}\n')
    with pytest.raises(config.ConfigError) as caught:
        config.load(config_file=path, environ={})
    message = str(caught.value)
    assert f'profiles.phone.{key}' in message
    assert ('drive.send_gate' in message) == (key == 'drive.send_gate')
    if key == 'drive.send_gate':
        assert 'config file only' in message
