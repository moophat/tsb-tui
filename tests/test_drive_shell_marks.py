"""Live shell marks tighten line sends and report command results as data."""
from pathlib import Path

import pytest
from test_drive_prompt_pattern import scenario as prompt_scenario
from test_drive_send_gate import barrier, look, output, refused, scenario, send


@prompt_scenario(pattern='never-matches')
async def test_prompt_mismatch_precedes_shell_mark_refusal(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07\x1b]133;C\x07')
    refused(await send(rig, 'bad\r', look=await look(rig)), 'prompt_mismatch')
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


@prompt_scenario(target_read_error=(503, 'inventory_unavailable', 'inventory down'))
async def test_unusable_prompt_precedes_shell_mark_refusal(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07\x1b]133;C\x07')
    refused(await send(rig, 'bad\r', look=await look(rig)), 'prompt_unusable')
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == []


async def marks(rig, server, sid, clock, data):
    await output(rig, server, sid, data)
    clock[0] += 1


@scenario
async def test_prompt_then_command_mark_refuses_line_sends(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07$ \x1b]133;B\x07')
    assert (await send(rig, 'echo ok\r', look=await look(rig)))['sent']
    await marks(rig, server, sid, clock, b'\x1b]133;C\x07running')
    before = list(server.received(slot.pump.state.client_id))
    for fields in ({'op': 'type', 'text': 'bad\r'}, {'op': 'keys', 'keys': ['enter']},
                   {'op': 'keys', 'keys': ['ctrl+j']}):
        refused(await rig.cmd(**fields, look=await look(rig), timeout_ms=1), 'not_at_prompt')
    await barrier(rig, server)
    assert server.received(slot.pump.state.client_id) == before
    assert (await send(rig, 'partial', look=await look(rig)))['sent']
    await marks(rig, server, sid, clock, b'\x1b]133;D;0\x07\x1b]133;A\x07$ ')
    assert (await send(rig, '\r', look=await look(rig)))['sent']


@scenario
async def test_split_prompt_and_command_marks_are_kept_across_runs(rig, server, sid, slot, clock, patch):
    for chunk in (b'\x1b]13', b'3;A\x1b', b'\\$ ', b'\x1b]133;', b'C\x07'):
        await marks(rig, server, sid, clock, chunk)
    refused(await send(rig, 'bad\r', look=await look(rig)), 'not_at_prompt')


@scenario
async def test_no_prompt_start_means_no_extra_gate(rig, server, sid, slot, clock, patch):
    for data in (b'plain prompt$ ', b'\x1b]133;C\x07', b'\x1b]133;D;3\x07'):
        await marks(rig, server, sid, clock, data)
        answer = await send(rig, 'echo ok\r', look=await look(rig))
        assert answer['ok'] and answer['sent']
        assert 'exit_code' not in answer


@scenario
async def test_exit_code_is_reported_only_for_the_settled_send_that_received_it(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;D;99\x07')
    original = slot.pump.send
    responses = iter((b'\x1b]133;D;1\x07\x1b]133;D;7\x1b\\', b'no marks',
                      b'\x1b]133;D\x07'))
    async def respond(data):
        await original(data)
        await marks(rig, server, sid, clock, next(responses))
    patch.setattr(slot.pump, 'send', respond)
    first = await send(rig, look=await look(rig))
    assert first['settled'] and first['exit_code'] == 7, first
    for _ in range(2):
        answer = await send(rig, look=await look(rig))
        assert answer['settled'] and 'exit_code' not in answer, answer


@scenario
async def test_unsettled_answer_omits_exit_code(rig, server, sid, slot, clock, patch):
    original = slot.pump.send
    async def respond(data):
        await original(data)
        await output(rig, server, sid, b'\x1b]133;D;4\x07')
    patch.setattr(slot.pump, 'send', respond)
    answer = await send(rig, look=await look(rig))
    assert answer['sent'] and not answer['settled'] and 'exit_code' not in answer


@scenario
async def test_attach_resets_shell_state_and_dump_does_not_enable_it(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07\x1b]133;C\x07')
    refused(await send(rig, '\r', look=await look(rig)), 'not_at_prompt')
    await rig.cmd('detach')
    await rig.attach(sid)
    assert (await send(rig, 'ok\r', look=await look(rig)))['sent']


@scenario
async def test_shell_gate_is_last_and_does_not_apply_to_full_screen(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07\x1b]133;C\x07')
    old = await look(rig)
    await marks(rig, server, sid, clock, b'changed')
    refused(await send(rig, '\r', look=old), 'screen_changed')
    await marks(rig, server, sid, clock, b'\x1b[?1049h')
    assert (await send(rig, 'ok\r', look=await look(rig), full_screen=True))['sent']


def test_guide_lists_shell_mark_refusal_in_error_table():
    guide = (Path(__file__).resolve().parents[1] / 'tsb_tui/drive/GUIDE.md').read_text()
    assert '| `not_at_prompt` |' in guide.split('## 11. Errors')[1].split('## 12.')[0]


def test_prompt_end_cannot_clear_a_command_since_prompt_start():
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;A\x07')
    assert state.at_prompt
    for mark in (b'C', b'B', b'D;0', b'B'):
        state.feed(b'\x1b]133;' + mark + b'\x07')
        assert not state.at_prompt
    state.feed(b'\x1b]133;A\x07')
    assert state.at_prompt


def test_oversized_exit_status_counts_as_a_codeless_d_mark():
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;A\x07')
    assert state.at_prompt
    state.feed(b'\x1b]133;D;' + b'9' * 5000 + b'\x07')
    assert state.last_mark == 'D'
    assert state.exit_serial == 1
    assert state.exit_code is None
    assert not state.at_prompt
    state.feed(b'\x1b]133;D;7\x07')
    assert state.exit_serial == 2
    assert state.exit_code == 7


@pytest.mark.parametrize('digits, code', [
    (b'2147483647', 2147483647),
    (b'2147483648', None),
    (b'00000000000', None),
])
def test_exit_status_is_capped(digits, code):
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;D;' + digits + b'\x07')
    assert state.last_mark == 'D'
    assert state.exit_serial == 1
    assert state.exit_code == code


def test_plain_output_returns_at_once_without_touching_state():
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;A\x07\x1b]133;C\x07')
    before = dict(vars(state))
    state.feed(b'x' * (1024 * 1024))
    assert vars(state) == before


def test_plain_text_inside_an_open_mark_is_scanned():
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;D;')
    state.feed(b'42')
    state.feed(b'\x07')
    assert state.last_mark == 'D'
    assert state.exit_serial == 1
    assert state.exit_code == 42


def test_escape_at_a_chunk_end_opens_the_mark_in_the_next_chunk():
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b')
    state.feed(b']133;A\x07')
    assert state.last_mark == 'A'
    assert state.prompt_seen
    assert state.at_prompt


@scenario
async def test_dump_marks_and_partial_marks_do_not_survive_reattach(rig, server, sid, slot, clock, patch):
    await marks(rig, server, sid, clock, b'\x1b]133;A\x07\x1b]133;C\x07\x1b]133;')
    state = slot.pump.state
    state.apply_attached({'client_id': state.client_id, 'session': state.session})
    dump = b'\x1b]133;A\x07\x1b]133;C\x07\x1b]133;D;42\x07'
    state.received_items += 1
    state.received_output_bytes += len(dump)
    slot.pump._expect_dump = True
    await slot.pump._feed_item(dump)
    await marks(rig, server, sid, clock, b'A\x07\x1b]133;C\x07')
    assert state.shell_marks.last_mark == 'C'
    assert state.shell_marks.exit_code is None
    assert (await send(rig, 'ok\r', look=await look(rig)))['sent']


@scenario
async def test_final_codeless_mark_omits_an_earlier_exit_code_from_same_send(rig, server, sid, slot, clock, patch):
    original = slot.pump.send
    async def respond(data):
        await original(data)
        await marks(rig, server, sid, clock, b'\x1b]133;D;7\x07\x1b]133;D\x07')
    patch.setattr(slot.pump, 'send', respond)
    answer = await send(rig, look=await look(rig))
    assert answer['settled'] and 'exit_code' not in answer, answer


@pytest.mark.parametrize('body,mark,code', [
    (b'A;cl=m', 'A', None),
    (b'A;aid=12', 'A', None),
    (b'D;0;aid=12', 'D', 0),
    (b'D;aid=12', 'D', None),
    (b'D;12x;aid=12', 'D', None),
    (b'AB', 'C', None),
])
@pytest.mark.parametrize('terminator', [b'\x07', b'\x1b\\'])
@pytest.mark.parametrize('split', [False, True])
def test_shell_mark_options_and_invalid_letters(body, mark, code, terminator, split):
    from tsb_tui.drive.shell_marks import ShellMarks

    state = ShellMarks()
    state.feed(b'\x1b]133;A\x07\x1b]133;C\x07')
    sequence = b'\x1b]133;' + body + terminator
    if split:
        boundary = sequence.rfind(b';') + 1
        state.feed(sequence[:boundary])
        assert state.last_mark == 'C'
        state.feed(sequence[boundary:])
    else:
        state.feed(sequence)
    assert state.last_mark == mark
    assert state.at_prompt == (mark == 'A')
    assert state.exit_serial == (1 if mark == 'D' else 0)
    assert state.exit_code == code


@scenario
async def test_type_reports_exit_codes_with_options_and_omits_nonnumeric_fields(rig, server, sid, slot, clock, patch):
    original = slot.pump.send
    response = [b'']
    split = [False]
    async def respond(data):
        await original(data)
        sequence = response[0]
        if split[0]:
            boundary = sequence.rfind(b';') + 1
            await output(rig, server, sid, sequence[:boundary])
            await marks(rig, server, sid, clock, sequence[boundary:])
        else:
            await marks(rig, server, sid, clock, sequence)
    patch.setattr(slot.pump, 'send', respond)
    for split[0] in (False, True):
        for response[0], code in ((b'\x1b]133;D;0;aid=12\x07', 0),
                                  (b'\x1b]133;D;aid=12\x1b\\', None)):
            answer = await send(rig, look=await look(rig))
            assert answer['settled'], answer
            if code is None:
                assert 'exit_code' not in answer, answer
            else:
                assert answer['exit_code'] == code, answer
