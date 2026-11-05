# Drive send gate review fixes

**Model:** codex
**Branch:** feat/drive-send-gate
**Inspected head:** d40821a

## Outcome and scope

Repair the seven requested review findings with tests written before implementation, retain red-run evidence, run the send-gate and affected drive tests and logged `make lint`, and leave all changes uncommitted. Only the operator's allowed code paths and this report may change. Do not modify the brake definition or decision/send ordering.

## Task breakdown

- [x] Inspect AGENTS.md, the relevant design and phase instructions, current gate, counters, send path, configuration refusal, and existing tests.
- [x] Resolve the pruning rule's interaction with the young-brake exception.
- [x] Write failing regression tests and fix received-change classification.
- [x] Strengthen timeout/brake and history/silence coverage.
- [x] Test and restore send history for answers reporting nothing sent.
- [x] Test and implement look pruning and the per-session limit.
- [x] Test and correct profile refusal diagnostics.
- [x] Verify brake semantics and lock/send ordering remain intact; run required focused tests and lint; review allowed-path diff.

## Initial design blocker — resolved by the operator

Finding 5 asks to drop looks at each mint when their recorded counts are behind the current ones, describing these as looks that can no longer pass. These conditions are not equivalent under the design:

- `.review/plan.md`, “Brakes” (lines 598–607), explicitly permits a brake to name a look younger than `drive.brake_look_max_ms` despite output and size changes since that look.
- Finding 7 explicitly requires preserving that brake rule.
- `Commands._check_gate` currently implements this exception by removing output and size from the young brake's change causes. Existing tests exercise it.

Concrete case: mint L1; receive output; mint L2 before the brake age bound expires; send a brake naming L1. L1's recorded counts are behind, but L1 can still pass the specified brake rule.

Options presented at the initial stop:

1. Prune only looks that cannot pass **any** permitted send. Retain output/size-lagging looks while they remain usable by a young brake, subject to the 64-look limit. This preserves the plan's brake behavior and interprets “can no longer pass” literally.
2. Prune every count-lagging look at the next mint, including young-brake-usable looks. In the concrete case above L1 becomes `no_look`; this narrows the brake rule across a subsequent mint and needs an explicit exception to finding 7.

Recommendation was option 1. The operator subsequently ruled option 1; the implementation follows that ruling.

## Initial inspection checkpoint

Initial `git status --short` was empty. At the initial stop this was inspection only; no code or tests changed, no commands were started in the background, and no commit, push or tag was made. The explicit instruction to stop on an unspecified design decision takes precedence over continuing independent fixes.

At that initial checkpoint no tests or lint had run and the report was the only change. Implementation and verification resumed after the ruling, as recorded below.

## Resumption and first test run

Operator ruled option 1: preserve looks usable by young brakes; prune expired output/size-lagging looks and all attachment/send/keyboard/stale-invalidated looks; cap at 64 per session.

Tests were written before production changes. Command: `python -m pytest tests/test_drive_send_gate_fixes.py tests/test_drive_send_gate.py -q -p no:cacheprovider` with the requested PATH/PYTHONPATH and NO_COLOR unset. Exit code: 1 (16 failed, 49 passed). Raw first-run output follows.

```text
FFFFFFFFFFFFFFFF.................................................        [100%]
=================================== FAILURES ===================================
____________________ test_queued_token_names_only_keyboard _____________________

rig = <drive_support.Rig object at 0x79d4a6827f20>
server = <input_server.InputRecordingApp object at 0x79d4a68275c0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871034.566066254]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a697ae10>

    @scenario
    async def test_queued_token_names_only_keyboard(rig, server, sid, slot, clock, patch):
        frame = fixture_frame('ws-frames-shape-v0.2.json', 'token')
        answer = await queued_frame(rig, server, sid, slot, patch, frame)
        refused(answer, 'screen_changed')
>       assert answer['changed'] == ['keyboard']
E       AssertionError: assert ['output', 'keyboard'] == ['keyboard']
E         
E         At index 0 diff: 'output' != 'keyboard'
E         Left contains one more item: 'keyboard'
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:46: AssertionError
_______________________ test_queued_size_names_only_size _______________________

rig = <drive_support.Rig object at 0x79d4a54f3830>
server = <input_server.InputRecordingApp object at 0x79d4a55f9d90>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871035.559802459]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a691e4e0>

    @scenario
    async def test_queued_size_names_only_size(rig, server, sid, slot, clock, patch):
        frame = fixture_frame('size-coalesced.json', 'size')
        answer = await queued_frame(rig, server, sid, slot, patch, frame)
        refused(answer, 'screen_changed')
>       assert answer['changed'] == ['size']
E       AssertionError: assert ['output'] == ['size']
E         
E         At index 0 diff: 'output' != 'size'
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:54: AssertionError
______________________ test_queued_stale_names_only_stale ______________________

rig = <drive_support.Rig object at 0x79d4a4123920>
server = <input_server.InputRecordingApp object at 0x79d4a41235c0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871036.563147243]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a428f1a0>

    @scenario
    async def test_queued_stale_names_only_stale(rig, server, sid, slot, clock, patch):
        frame = fixture_frame('stale-flow.json', 'stale')
        answer = await queued_frame(rig, server, sid, slot, patch, frame)
        refused(answer, 'screen_changed')
>       assert answer['changed'] == ['stale']
E       AssertionError: assert ['output', 'stale'] == ['stale']
E         
E         At index 0 diff: 'output' != 'stale'
E         Left contains one more item: 'stale'
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:62: AssertionError
____________ test_clients_notices_and_prompts_do_not_change_a_look _____________

rig = <drive_support.Rig object at 0x79d4a68bbb60>
server = <input_server.InputRecordingApp object at 0x79d4a684fb90>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871038.569281394]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a7d5e060>

    @scenario
    async def test_clients_notices_and_prompts_do_not_change_a_look(rig, server, sid, slot, clock, patch):
        frames = [fixture_frame('ws-frames-shape-v0.2.json', kind) for kind in ('notice', 'quiet_prompt')]
        frames.append(fixture_frame('stale-flow.json', 'stale_prompt'))
        frames.append({'t': 'clients', 'v': 1, 'session_id': sid, 'clients': slot.pump.state.clients})
        for frame in frames:
            clock[0] += 1
            answer = await queued_frame(rig, server, sid, slot, patch, frame)
>           assert answer['ok'], answer
E           AssertionError: {'id': 4, 'ok': False, 'error': 'screen_changed', 'why': 'the screen changed after your look; nothing was sent', ...}
E           assert False

tests/test_drive_send_gate_fixes.py:73: AssertionError
_____________ test_unsent_input_restores_the_previous_send_history _____________

rig = <drive_support.Rig object at 0x79d4a684f470>
server = <input_server.InputRecordingApp object at 0x79d4a684df40>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871039.5656499008]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a684ef60>

    @scenario
    async def test_unsent_input_restores_the_previous_send_history(rig, server, sid, slot, clock, patch):
>       await unsent_history(rig, server, sid, slot, clock, patch, close=False)

tests/test_drive_send_gate_fixes.py:107: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4a684f470>
server = <input_server.InputRecordingApp object at 0x79d4a684df40>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871039.5656499008]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a684ef60>
close = False

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
>       assert asdict(history) == before
E       AssertionError: assert {'count': 2, ...38.5656499008} == {'count': 1, ...38.5656499008}
E         
E         Omitting 1 identical items, use -vv to show
E         Differing items:
E         {'last_send_at': 1871039.5656499008} != {'last_send_at': 1871038.564649901}
E         {'output_since_send': False} != {'output_since_send': True}
E         {'count': 2} != {'count': 1}
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:101: AssertionError
________ test_a_slot_closed_at_send_restores_the_previous_send_history _________

rig = <drive_support.Rig object at 0x79d4976e9fa0>
server = <input_server.InputRecordingApp object at 0x79d4976ebec0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871040.763750036]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d497558560>

    @scenario
    async def test_a_slot_closed_at_send_restores_the_previous_send_history(rig, server, sid, slot, clock, patch):
>       await unsent_history(rig, server, sid, slot, clock, patch, close=True)

tests/test_drive_send_gate_fixes.py:112: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4976e9fa0>
server = <input_server.InputRecordingApp object at 0x79d4976ebec0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871040.763750036]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d497558560>, close = True

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
>       assert asdict(history) == before
E       AssertionError: assert {'count': 2, ...039.763750036} == {'count': 1, ...039.763750036}
E         
E         Omitting 1 identical items, use -vv to show
E         Differing items:
E         {'last_send_at': 1871040.763750036} != {'last_send_at': 1871039.762750036}
E         {'output_since_send': False} != {'output_since_send': True}
E         {'count': 2} != {'count': 1}
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:101: AssertionError
________________ test_mint_drops_looks_from_an_ended_attachment ________________

rig = <drive_support.Rig object at 0x79d497f0d1c0>
server = <input_server.InputRecordingApp object at 0x79d497f0c560>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...on lives on', device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871041.010507667]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d497f27080>

    @scenario
    async def test_mint_drops_looks_from_an_ended_attachment(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        await rig.cmd('detach')
        await rig.attach(sid)
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:128: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d497f0d1c0>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d49738f7a0>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871041.010507667, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d49738f7a0>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871041.010507667, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4a546cd40>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4a546cd40> = <tsb_tui.drive.runner.Driver object at 0x79d4a552a510>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4a552a510> = <drive_support.Rig object at 0x79d497f0d1c0>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
_________ test_mint_drops_looks_from_a_previous_attachment_generation __________

rig = <drive_support.Rig object at 0x79d4a43cb1a0>
server = <input_server.InputRecordingApp object at 0x79d4a546e7e0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871042.26970242]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a4121040>

    @scenario
    async def test_mint_drops_looks_from_a_previous_attachment_generation(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        state = slot.pump.state
        state.apply_attached({'client_id': state.client_id, 'session': state.session})
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:137: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4a43cb1a0>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d49738e360>, attachment=1,...tems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871042.26970242, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d49738e360>, attachment=1,...tems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871042.26970242, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4a42a60f0>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4a42a60f0> = <tsb_tui.drive.runner.Driver object at 0x79d496eda6f0>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d496eda6f0> = <drive_support.Rig object at 0x79d4a43cb1a0>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
____________________ test_mint_drops_looks_spent_by_a_send _____________________

rig = <drive_support.Rig object at 0x79d4a68695e0>
server = <input_server.InputRecordingApp object at 0x79d4a686bce0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871043.336267133]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d49738eff0>

    @scenario
    async def test_mint_drops_looks_spent_by_a_send(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        await send(rig, look=old)
        await output(rig, server, sid)
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:146: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4a68695e0>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4972c2180>, attachment=1,...ms=0, stale_items=0, size=0, stale=0, sends=1, kind='unsettled', at=1871043.336267133, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4972c2180>, attachment=1,...ms=0, stale_items=0, size=0, stale=0, sends=1, kind='unsettled', at=1871043.336267133, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4974f4950>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4974f4950> = <tsb_tui.drive.runner.Driver object at 0x79d4974f5a00>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4974f5a00> = <drive_support.Rig object at 0x79d4a68695e0>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
_______________ test_mint_drops_looks_after_keyboard_counts_move _______________

rig = <drive_support.Rig object at 0x79d4a42b1f40>
server = <input_server.InputRecordingApp object at 0x79d4a42b2870>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871044.586948656]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4a42b0fb0>

    @scenario
    async def test_mint_drops_looks_after_keyboard_counts_move(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        state = slot.pump.state
        holder = state.holder
        state.apply_token({'holder': None})
        state.apply_token({'holder': holder})
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:157: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4a42b1f40>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a68ca450>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871044.586948656, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a68ca450>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871044.586948656, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4a54ae570>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4a54ae570> = <tsb_tui.drive.runner.Driver object at 0x79d4a42b0e00>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4a42b0e00> = <drive_support.Rig object at 0x79d4a42b1f40>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
________________ test_mint_drops_looks_after_stale_counts_move _________________

rig = <drive_support.Rig object at 0x79d497f0d070>
server = <input_server.InputRecordingApp object at 0x79d497f0eb70>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871048.478679068]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d497f0f080>

    @scenario
    async def test_mint_drops_looks_after_stale_counts_move(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        frame = fixture_frame('stale-flow.json', 'stale')
        frame['state'] = 'chosen'
        slot.pump.state.apply_stale(frame)
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:167: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d497f0d070>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a428f1d0>, attachment=1,...ems=0, stale_items=0, size=0, stale=1, sends=0, kind='ordinary', at=1871048.478679068, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a428f1d0>, attachment=1,...ems=0, stale_items=0, size=0, stale=1, sends=0, kind='ordinary', at=1871048.478679068, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d49755a8d0>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d49755a8d0> = <tsb_tui.drive.runner.Driver object at 0x79d4a546fe60>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4a546fe60> = <drive_support.Rig object at 0x79d497f0d070>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
________ test_mint_keeps_output_lagging_looks_until_the_brake_age_bound ________

rig = <drive_support.Rig object at 0x79d4a42a7fe0>
server = <input_server.InputRecordingApp object at 0x79d49714c8c0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871059.594777179]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d49738c740>

    @scenario
    async def test_mint_keeps_output_lagging_looks_until_the_brake_age_bound(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        await output(rig, server, sid)
        await look(rig)
        assert old in rig.driver.commands._looks
        clock[0] += rig.app.cfg['drive.brake_look_max_ms'] / 1000 + .001
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:178: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d4a42a7fe0>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a4267860>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871059.594777179, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4a4267860>, attachment=1,...ems=0, stale_items=0, size=0, stale=0, sends=0, kind='ordinary', at=1871059.594777179, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4972c33e0>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4972c33e0> = <tsb_tui.drive.runner.Driver object at 0x79d4972c1f10>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4972c1f10> = <drive_support.Rig object at 0x79d4a42a7fe0>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
_________ test_mint_keeps_size_lagging_looks_until_the_brake_age_bound _________

rig = <drive_support.Rig object at 0x79d496419790>
server = <input_server.InputRecordingApp object at 0x79d4972417f0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871060.782433752]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d4972c2330>

    @scenario
    async def test_mint_keeps_size_lagging_looks_until_the_brake_age_bound(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        slot.pump.state.apply_size(fixture_frame('size-coalesced.json', 'size'))
        await look(rig)
        assert old in rig.driver.commands._looks
        clock[0] += rig.app.cfg['drive.brake_look_max_ms'] / 1000 + .001
        await look(rig)
>       await assert_dropped(rig, old)

tests/test_drive_send_gate_fixes.py:189: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

rig = <drive_support.Rig object at 0x79d496419790>, old = 'L1'

    async def assert_dropped(rig, old):
>       assert old not in rig.driver.commands._looks
E       AssertionError: assert 'L1' not in {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4974f74d0>, attachment=1,...ems=0, stale_items=0, size=1, stale=0, sends=0, kind='ordinary', at=1871060.782433752, alt=False, cursor_hidden=False)}
E        +  where {'L1': Look(session='sess-2', state=<tsb_tui.model.attachment.AttachmentState object at 0x79d4974f74d0>, attachment=1,...ems=0, stale_items=0, size=1, stale=0, sends=0, kind='ordinary', at=1871060.782433752, alt=False, cursor_hidden=False)} = <tsb_tui.drive.commands.Commands object at 0x79d4a5560da0>._looks
E        +    where <tsb_tui.drive.commands.Commands object at 0x79d4a5560da0> = <tsb_tui.drive.runner.Driver object at 0x79d4a5561070>.commands
E        +      where <tsb_tui.drive.runner.Driver object at 0x79d4a5561070> = <drive_support.Rig object at 0x79d496419790>.driver

tests/test_drive_send_gate_fixes.py:116: AssertionError
_________ test_mint_retains_at_most_64_looks_per_session_oldest_first __________

rig = <drive_support.Rig object at 0x79d496ed87d0>
server = <input_server.InputRecordingApp object at 0x79d496eda780>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871052.228030559]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x79d497f0fda0>

    @scenario
    async def test_mint_retains_at_most_64_looks_per_session_oldest_first(rig, server, sid, slot, clock, patch):
        from input_server import make_session
        other = make_session(rig.url)
        await rig.attach(other)
        other_look = await look(rig)
        await rig.cmd('switch', session=sid)
        ids = [await look(rig) for _ in range(66)]
        kept = rig.driver.commands._looks
>       assert [ident for ident, seen in kept.items() if seen.session == sid] == ids[-64:]
E       AssertionError: assert ['L2', 'L3', ...6', 'L7', ...] == ['L4', 'L5', ...8', 'L9', ...]
E         
E         At index 0 diff: 'L2' != 'L4'
E         Left contains 2 more items, first extra item: 'L66'
E         Use -v to get more diff

tests/test_drive_send_gate_fixes.py:201: AssertionError
_ test_profile_table_refusal_names_its_key_and_only_the_gate_mentions_the_gate[size.auto_request-true] _

tmp_path = PosixPath('/tmp/pytest-of-root/pytest-837/test_profile_table_refusal_nam0')
key = 'size.auto_request', value = 'true'

    @pytest.mark.parametrize('key,value', [('size.auto_request', 'true'), ('drive.send_gate', 'false')])
    def test_profile_table_refusal_names_its_key_and_only_the_gate_mentions_the_gate(tmp_path, key, value):
        path = tmp_path / 'profiles.toml'
        path.write_text(f'[profiles.phone]\n{key} = {value}\n')
        with pytest.raises(config.ConfigError) as caught:
            config.load(config_file=path, environ={})
        message = str(caught.value)
>       assert f'profiles.phone.{key}' in message
E       AssertionError: assert 'profiles.phone.size.auto_request' in '/tmp/pytest-of-root/pytest-837/test_profile_table_refusal_nam0/profiles.toml: profiles: a [profiles.*] table is not a knob and cannot be set here; drive.send_gate is config file only'

tests/test_drive_send_gate_fixes.py:215: AssertionError
_ test_profile_table_refusal_names_its_key_and_only_the_gate_mentions_the_gate[drive.send_gate-false] _

tmp_path = PosixPath('/tmp/pytest-of-root/pytest-837/test_profile_table_refusal_nam1')
key = 'drive.send_gate', value = 'false'

    @pytest.mark.parametrize('key,value', [('size.auto_request', 'true'), ('drive.send_gate', 'false')])
    def test_profile_table_refusal_names_its_key_and_only_the_gate_mentions_the_gate(tmp_path, key, value):
        path = tmp_path / 'profiles.toml'
        path.write_text(f'[profiles.phone]\n{key} = {value}\n')
        with pytest.raises(config.ConfigError) as caught:
            config.load(config_file=path, environ={})
        message = str(caught.value)
>       assert f'profiles.phone.{key}' in message
E       AssertionError: assert 'profiles.phone.drive.send_gate' in '/tmp/pytest-of-root/pytest-837/test_profile_table_refusal_nam1/profiles.toml: profiles: a [profiles.*] table is not a knob and cannot be set here; drive.send_gate is config file only'

tests/test_drive_send_gate_fixes.py:215: AssertionError
16 failed, 49 passed in 79.19s (0:01:19)

```

## Finding 1 checkpoint

Red: queued token reported output+keyboard, size reported output, stale reported output+stale; notice refused as output. Added received/applied byte totals and size-frame totals. Gate now compares each cause separately; stale_prompt no longer increments stale change counters. Pending focused green run.

## Findings 2 and 3 checkpoints

Strengthened timeout test applies output during pump.send and asserts the timeout answer itself has an unsettled look; its brake names that same answer without another read. Reconnect test advances the clock, requires the old look, removes the conditional refusal assertion, then tests a silent target across detach/attach/relogin and after_silence after the bound. Both passed in the first run on unchanged production code; these are coverage repairs, not reproduced production failures. No red is claimed for these two tests.

Counting focused verification:
```text
....                                                                     [100%]
4 passed, 12 deselected in 5.14s

```

## Finding 4 checkpoint

Both False-return and slot-closed-at-send tests failed first: count advanced, last_send_at changed and output_since_send was cleared. Snapshot history inside the decision lock, record in the same original position, and restore all four fields on send_input False without replacing the history object. Tests also assert recording precedes send_input and retry the original look after refusal.

History focused verification:
```text
..                                                                       [100%]
2 passed, 14 deselected in 2.82s

```

## Finding 5 checkpoint

Eight retention tests failed first: ended attachment, changed attachment generation, spent send, keyboard movement, stale movement, aged output lag, aged size lag, and oldest-first per-session eviction. Implemented pruning at each actual mint, preserving lagging young-brake looks per the ruling. A monotonic serial prevents ID reuse and recognizes previously minted but removed IDs without retaining tombstones. Missing removed IDs report no_look with "that look is too old; nothing was sent". Cap is independent per session and includes refusal looks. Pending focused green run.

Retention focused verification:
```text
........                                                                 [100%]
8 passed, 8 deselected in 9.32s

```

## Finding 6 checkpoint

Both configuration cases failed first: neither refusal named the actual nested key, and an unrelated size key misleadingly mentioned the gate. The refusal now names the first rejected flattened profiles key and uses the send-gate explanation only for drive.send_gate.

## Finding 7 checkpoint

No change to the whole-encoded-payload brake predicate (`data in (b"\x03", b"\x1e")`), its young-brake exception, or the synchronous gate/record/send ordering. Existing alias, kitty-mode and scheduled-callback ordering tests are regression guards; all passed before production edits. These unchanged invariants do not have a genuine red reproduction. Pending final focused verification.

Profile focused verification:
```text
..                                                                       [100%]
2 passed, 14 deselected in 0.55s

```

## Combined focused verification

Command: `python -m pytest tests/test_drive_send_gate.py tests/test_drive_send_gate_fixes.py tests/test_drive_commands.py tests/test_drive_waits.py tests/test_drive_runner.py tests/test_model_attachment.py tests/test_config.py -q -p no:cacheprovider`

Exit code: 4

```text
ERROR: file or directory not found: tests/test_drive_runner.py


no tests ran in 0.00s

```

## Corrected focused verification

Command: `python -m pytest tests/test_drive*.py tests/test_model_attachment.py tests/test_config.py -q -p no:cacheprovider`

Exit code: 1

```text
........................................................................ [ 19%]
..........F............................................................. [ 38%]
.........F.............................................................. [ 57%]
........................................................................ [ 76%]
........................................................................ [ 95%]
..................                                                       [100%]
=================================== FAILURES ===================================
_ test_one_byte_after_the_look_refuses_and_returns_a_screen_and_a_look_that_works _

rig = <drive_support.Rig object at 0x7db3a56b80b0>
server = <input_server.InputRecordingApp object at 0x7db38ff8c7a0>
sid = 'sess-2'
slot = SessionSlot(session_id='sess-2', canvas=Canvas(), record={'id': 'sess-2', 'target_id': 'fixture-target', 'connection_i...e, left=None, device_login=None, device_gate=False, device_size_pending=False, login_status='', login_status_until=0.0)
clock = [1871406.649450277]
patch = <_pytest.monkeypatch.MonkeyPatch object at 0x7db3a51963f0>

    @scenario
    async def test_one_byte_after_the_look_refuses_and_returns_a_screen_and_a_look_that_works(rig, server, sid, slot, clock, patch):
        old = await look(rig)
        await output(rig, server, sid, b'z')
        answer = await send(rig, look=old)
        refused(answer, 'screen_changed')
>       assert answer['changed'] == ['output']
E       AssertionError: assert ['output', 'size'] == ['output']
E         
E         Left contains one more item: 'size'
E         Use -v to get more diff

tests/test_drive_send_gate.py:107: AssertionError
_ test_the_owner_takes_the_keyboard_back_and_the_agents_next_input_is_stopped[true] _

serve = <function serve.<locals>.start at 0x7db3a7789f80>
tmp_path = PosixPath('/tmp/pytest-of-root/pytest-842/test_the_owner_takes_the_keybo1')
guard = 'true'

    @pytest.mark.parametrize("guard", ["false", "true"])
    def test_the_owner_takes_the_keyboard_back_and_the_agents_next_input_is_stopped(serve, tmp_path, guard) -> None:
        """The §2.9.6 script, both guard settings. With the guard off the agent's `type` reaches the core and is refused
        there with `input_refused` `not_typist` (the core is the control, the guard only a guardrail); with it on, drive
        answers `not_typist`, `sent: false`, and the server records nothing from the agent. Either way the owner's Ctrl-C
        reached the device and the owner holds the keyboard."""
    
        async def check() -> None:
            serve_app, url = serve(InputRecordingApp())
            sid = make_session(url, _serve.USER)
            async with Rig(url, tmp_path=tmp_path) as owner, Rig(
                    url, tmp_path=tmp_path, profile="harness", overrides=(f"input.guard={guard}",)) as agent:
                await owner.login(_serve.USER)
                await owner.attach(sid)
                await agent.login(_serve.PEER)
                assert (await agent.attach(sid))["you"] == "view_only"
                owner_client = owner.app.workspace().slot_for(sid).pump.state.client_id
                agent_client = agent.app.workspace().slot_for(sid).pump.state.client_id
    
                handed = await owner.cmd("hand", to=_serve.PEER)
                assert handed["to"] == {"user": _serve.PEER, "client_id": agent_client}
                assert (await agent.cmd("wait", until={"holder": "me"}, timeout_ms=5000))["ok"]
                token = next(e for e in agent.events("token") if e["holder"] == "me")
                assert token["reason"] == "handoff" and token["from"] == _serve.USER
    
                assert (await agent.cmd("type", text="ping 198.51.100.77 repeat 100000\r", look=await agent.look()))["ok"]
                assert (await agent.cmd("wait", until={"reply": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]
                assert (await owner.cmd("wait", until={"screen": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]
    
                # The owner's client watches now: a query the session asks must be answered by the holder alone.
                _emit_output(serve_app, sid, DSR)
                await _until(lambda: any(chunk.endswith(b"R") for chunk in serve_app.received(agent_client)),
                             "the holder to answer the query")
                await owner.cmd("wait", until={"settled_ms": 150})
                assert serve_app.received(owner_client) == [], "the owner's watching client sent input"
    
                agent_old = await agent.look()
                assert (await owner.cmd("keys", keys=["ctrl+c"], look=await owner.look()))["ok"]
                assert (await agent.cmd("wait", until={"holder": "not_me"}, timeout_ms=2000))["ok"]
                assert (await owner.cmd("wait", until={"holder": "me"}, timeout_ms=2000))["ok"]
                assert b"\x03" in b"".join(serve_app.received(owner_client)), "the Ctrl-C did not reach the core"
    
                before = list(serve_app.received(agent_client))
                typed = await agent.cmd("type", text="x", timeout_ms=1, look=await agent.look())
                if guard == "false":
                    assert typed["ok"] is True and typed["queued"] == 1
                    await _until(lambda: any(e.get("reason") == "not_typist" for e in agent.events("input_refused")),
                                 "the core's refusal event, possibly delivered during the send wait")
                    assert serve_app.received(agent_client) == [*before, b"x"], "the core never saw the agent's input"
                else:
                    assert typed["ok"] is False and typed["error"] == "not_typist" and typed["sent"] is False
                    assert typed["holder"] == _serve.USER
                    await agent.cmd("wait", until={"settled_ms": 150})
                    assert serve_app.received(agent_client) == before, "the guard let the agent's input through"
                    assert not agent.events("input_refused"), "the core was asked, so the guard was not what stopped it"
                assert not any(chunk == b"x" for chunk in serve_app.received(owner_client))
                assert (await owner.cmd("hand", to=agent_client))["ok"]
                assert (await agent.cmd("wait", until={"holder": "me"}))["ok"]
                old = await agent.cmd("type", text="x", look=agent_old)
                if guard == "true":
                    assert old["error"] == "screen_changed" and "keyboard" in old["changed"], old
                else:
                    assert old["error"] == "no_look", old  # the attempted send used up every earlier look
    
>       asyncio.run(check())

tests/test_drive_two_drivers.py:102: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/usr/lib/python3.12/asyncio/runners.py:194: in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
/usr/lib/python3.12/asyncio/runners.py:118: in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/usr/lib/python3.12/asyncio/base_events.py:687: in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER)
        async with Rig(url, tmp_path=tmp_path) as owner, Rig(
                url, tmp_path=tmp_path, profile="harness", overrides=(f"input.guard={guard}",)) as agent:
            await owner.login(_serve.USER)
            await owner.attach(sid)
            await agent.login(_serve.PEER)
            assert (await agent.attach(sid))["you"] == "view_only"
            owner_client = owner.app.workspace().slot_for(sid).pump.state.client_id
            agent_client = agent.app.workspace().slot_for(sid).pump.state.client_id
    
            handed = await owner.cmd("hand", to=_serve.PEER)
            assert handed["to"] == {"user": _serve.PEER, "client_id": agent_client}
            assert (await agent.cmd("wait", until={"holder": "me"}, timeout_ms=5000))["ok"]
            token = next(e for e in agent.events("token") if e["holder"] == "me")
            assert token["reason"] == "handoff" and token["from"] == _serve.USER
    
            assert (await agent.cmd("type", text="ping 198.51.100.77 repeat 100000\r", look=await agent.look()))["ok"]
            assert (await agent.cmd("wait", until={"reply": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]
            assert (await owner.cmd("wait", until={"screen": "ping 198.51.100.77"}, timeout_ms=5000))["ok"]
    
            # The owner's client watches now: a query the session asks must be answered by the holder alone.
            _emit_output(serve_app, sid, DSR)
            await _until(lambda: any(chunk.endswith(b"R") for chunk in serve_app.received(agent_client)),
                         "the holder to answer the query")
            await owner.cmd("wait", until={"settled_ms": 150})
            assert serve_app.received(owner_client) == [], "the owner's watching client sent input"
    
            agent_old = await agent.look()
            assert (await owner.cmd("keys", keys=["ctrl+c"], look=await owner.look()))["ok"]
            assert (await agent.cmd("wait", until={"holder": "not_me"}, timeout_ms=2000))["ok"]
            assert (await owner.cmd("wait", until={"holder": "me"}, timeout_ms=2000))["ok"]
            assert b"\x03" in b"".join(serve_app.received(owner_client)), "the Ctrl-C did not reach the core"
    
            before = list(serve_app.received(agent_client))
            typed = await agent.cmd("type", text="x", timeout_ms=1, look=await agent.look())
            if guard == "false":
                assert typed["ok"] is True and typed["queued"] == 1
                await _until(lambda: any(e.get("reason") == "not_typist" for e in agent.events("input_refused")),
                             "the core's refusal event, possibly delivered during the send wait")
                assert serve_app.received(agent_client) == [*before, b"x"], "the core never saw the agent's input"
            else:
                assert typed["ok"] is False and typed["error"] == "not_typist" and typed["sent"] is False
                assert typed["holder"] == _serve.USER
                await agent.cmd("wait", until={"settled_ms": 150})
                assert serve_app.received(agent_client) == before, "the guard let the agent's input through"
                assert not agent.events("input_refused"), "the core was asked, so the guard was not what stopped it"
            assert not any(chunk == b"x" for chunk in serve_app.received(owner_client))
            assert (await owner.cmd("hand", to=agent_client))["ok"]
            assert (await agent.cmd("wait", until={"holder": "me"}))["ok"]
            old = await agent.cmd("type", text="x", look=agent_old)
            if guard == "true":
>               assert old["error"] == "screen_changed" and "keyboard" in old["changed"], old
E               AssertionError: {'id': 13, 'ok': False, 'error': 'no_look', 'why': 'that look is too old; nothing was sent', ...}
E               assert ('no_look' == 'screen_changed'
E                 
E                 - screen_changed
E                 + no_look)

tests/test_drive_two_drivers.py:98: AssertionError
2 failed, 376 passed in 316.31s (0:05:16)

```

## Broader-run findings reconciled

The first corrected combined run returned 2 failures and 376 passes (exit 1). Both were test expectations affected by the requested semantics: the one-byte test injected resize marker frames via its output helper and now legitimately reported size too; it now waits on the one emitted byte without injecting another frame after its look. The two-driver test expected screen_changed after a keyboard move and a subsequent mint; the ruled pruning policy requires no_look/too old there. Updated that exact assertion, retaining the wire-emptiness and ownership assertions.

## Documentation checkpoint

Updated the owning drive guide for unsent-history semantics, the ruled retention policy and 64-look cap, dropped-look refusal, and exact change-frame causes. No requirement/context sources changed.

## Affected-files recheck

Command: `python -m pytest tests/test_drive_send_gate.py tests/test_drive_send_gate_fixes.py tests/test_drive_two_drivers.py tests/test_model_attachment.py -q -p no:cacheprovider`

Exit code: 0

```text
........................................................................ [ 40%]
........................................................................ [ 80%]
..................................                                       [100%]
178 passed in 188.30s (0:03:08)

```

## make lint

Command: `make lint` with requested environment.

Exit code: 2

```text
I001 [*] Import block is un-sorted or un-formatted
  --> tests/test_drive_send_gate_fixes.py:2:1
   |
 1 |   """Change causes, unsent input, and bounded retention of still-usable looks."""
 2 | / from __future__ import annotations
 3 | |
 4 | | import asyncio
 5 | | import json
 6 | | from dataclasses import asdict
 7 | | from pathlib import Path
 8 | |
 9 | | import pytest
10 | |
11 | | from test_drive_send_gate import barrier, look, output, refused, scenario, send
12 | | from tsb_tui import config
   | |__________________________^
help: Organize imports
   |
9  | import pytest
10 + from test_drive_send_gate import barrier, look, output, refused, scenario, send
11 |
   - from test_drive_send_gate import barrier, look, output, refused, scenario, send
12 | from tsb_tui import config
   |

Found 1 error.
[*] 1 fixable with the `--fix` option.
make: *** [Makefile:22: lint] Error 1

```

## make lint recheck

Fixed only the new test import-group spacing after the first lint failure. Command: `make lint`.

Exit code: 0

```text
All checks passed!
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: <the lane clone>
configfile: pyproject.toml
plugins: platformdirs-4.12.2
collected 51 items

.github/scripts/tests/test_docs_branch_guard.py .............            [ 25%]
.github/scripts/tests/test_leak_check_prefixes.py ..                     [ 29%]
tools/test_import_lint.py ....................................           [100%]

============================== 51 passed in 3.70s ==============================
import-lint: clean (./ 167 files)
docs_branch_guard: no event, not pull_request — no-op
clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths
libghostty-vt.so: OK
include/ghostty/vt/allocator.h: OK
include/ghostty/vt/build_info.h: OK
include/ghostty/vt/color.h: OK
include/ghostty/vt/color_scheme.h: OK
include/ghostty/vt/device.h: OK
include/ghostty/vt/focus.h: OK
include/ghostty/vt/formatter.h: OK
include/ghostty/vt/grid_ref.h: OK
include/ghostty/vt/grid_ref_tracked.h: OK
include/ghostty/vt/io.h: OK
include/ghostty/vt/key/encoder.h: OK
include/ghostty/vt/key/event.h: OK
include/ghostty/vt/key.h: OK
include/ghostty/vt/kitty_graphics.h: OK
include/ghostty/vt/modes.h: OK
include/ghostty/vt/mouse/encoder.h: OK
include/ghostty/vt/mouse/event.h: OK
include/ghostty/vt/mouse.h: OK
include/ghostty/vt/osc.h: OK
include/ghostty/vt/paste.h: OK
include/ghostty/vt/point.h: OK
include/ghostty/vt/render.h: OK
include/ghostty/vt/screen.h: OK
include/ghostty/vt/search.h: OK
include/ghostty/vt/selection.h: OK
include/ghostty/vt/sgr.h: OK
include/ghostty/vt/size_report.h: OK
include/ghostty/vt/snapshot.h: OK
include/ghostty/vt/style.h: OK
include/ghostty/vt/sys.h: OK
include/ghostty/vt/terminal.h: OK
include/ghostty/vt/types.h: OK
include/ghostty/vt/unicode.h: OK
include/ghostty/vt/wasm.h: OK
include/ghostty/vt.h: OK
engine-check: shared object and all headers verified; object matches PIN

```

## Guide and scope verification

Command: `python -m pytest tests/test_drive_commands.py -k guide_names_every_command -q -p no:cacheprovider`

Exit code: 0

```text
.                                                                        [100%]
1 passed, 43 deselected in 0.64s

```

`git diff --check` passed. Tracked and untracked changes were checked programmatically against the explicit allowed paths. HEAD remains d40821a. No commit, push, tag, background command, real broker, or full-suite run was made.

Changed files:

- `tests/test_drive_send_gate.py`
- `tests/test_drive_two_drivers.py`
- `tsb_tui/config.py`
- `tsb_tui/drive/GUIDE.md`
- `tsb_tui/drive/commands.py`
- `tsb_tui/model/attachment.py`
- `plans/reports/build-261106-drive-send-gate-p1-fixes.md`
- `tests/test_drive_send_gate_fixes.py`

## Final review and evidence

Reviewed the entire pending diff and the new test file against all seven requests and the operator's pruning ruling. No remaining implementation finding was identified within this scope. Gate checks and recording remain in the existing backend lock with no intervening await before send_input/pump.send. Brake recognition remains exactly the whole encoded payload 0x03 or 0x1E. Pending stale-choice guard behavior remains intact; prompt receipt is not a change-counter cause.

| Finding | First-run evidence | Final verification |
|---|---|---|
| 1 — cause counters | Four failing tests (token, size, stale, irrelevant frames) before production edits | Four focused tests passed; send-gate/model checks passed |
| 2 — timeout answer's own brake look | Strengthened test already passed on unchanged HEAD | Passed in the 178-test affected-file run |
| 3 — history, unconditional old look, silence | Strengthened test already passed on unchanged HEAD; later added immediate reconnect look-kind guards | Passed in the 178-test affected-file run |
| 4 — unsent history rollback | Both False-return and closed-at-send cases failed before production edits | Both focused cases and affected-file run passed |
| 5 — retention | All eight retention cases failed before production edits | Eight focused cases and affected-file run passed |
| 6 — profile diagnostics | Both key cases failed before production edits | Both focused cases and combined config checks passed |
| 7 — brake and ordering preserved | Existing regression guards passed before production edits | Alias/kitty-mode/brake and scheduled-callback ordering tests passed in affected-file run; source predicate and ordering inspected |

Verification totals: first broader selected run had 376 passes and 2 failures; both failures were reconciled with the requested semantics and the 178-test affected-file recheck passed, with no skips. The other selected drive/config tests passed in the earlier run and were not changed afterward. Final make lint exited 0, including 51 passing lint-support tests and all installed lint checks. The final guide-specific check also passed. All failed invocations and their outputs remain above.

Concern: a failing-on-HEAD test for each of findings 2, 3 and 7 cannot be honestly claimed: the strengthened coverage and the intentionally preserved invariants already pass on HEAD. No production behavior was reverted or altered to manufacture a red. The actual reproduced defects have their pre-fix red evidence above.

Full suite intentionally not run, as instructed; CI owns that verification. No live lab or staging proof was attempted.

Status: DONE_WITH_CONCERNS
