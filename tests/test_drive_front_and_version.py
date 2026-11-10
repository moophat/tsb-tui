"""Front refusals must tell agents whether a session exists before they retry."""
import asyncio
from types import SimpleNamespace

import pytest
from drive_support import Rig
from tsb_contract_client import HandshakeError
from tsbc import _serve
from tsbc._http import Response

from tsb_tui import __main__, config
from tsb_tui.screens.chrome_dialogs import about_lines, keys_lines


@pytest.mark.parametrize('stage', ['create', 'ticket', 'upgrade'])
@pytest.mark.parametrize('status', [502, 503, 504])
@pytest.mark.parametrize('json_error', [False, True])
def test_new_front_answer(serve, tmp_path, monkeypatch, stage, status, json_error):
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
            await rig.login()
            client = rig.app.client
            request = client._http.request

            def failing_request(method, path, *args, **kwargs):
                if (stage == 'create' and method == 'POST' and path == '/sessions'
                        or stage == 'ticket' and path == '/auth/ws-ticket'):
                    body = (b'{"error":{"code":"unavailable","message":"core says wait"}}'
                            if json_error else b'<html>Bad gateway</html>')
                    return Response(status, [], body)
                return request(method, path, *args, **kwargs)

            monkeypatch.setattr(client._http, 'request', failing_request)
            if stage == 'upgrade':
                from tsb_contract_client.attachment import AsyncAttachment

                async def refused(*args, **kwargs):
                    raise HandshakeError(f'upgrade refused with HTTP {status}', status)

                monkeypatch.setattr(AsyncAttachment, 'open', refused)
            answer = await rig.cmd('new', target=_serve.TARGET)
            assert answer['ok'] is False, answer
            assert answer['error'] == ('unavailable' if stage == 'create' and json_error
                                       else None if stage == 'create' else 'attach_failed'), answer
            if json_error and stage != 'upgrade':
                assert answer['why'] == ('core says wait' if stage == 'create'
                                         else f'HTTP {status} unavailable: core says wait'), answer
                return
            assert 'front answered' in answer['why'] and 'TSB did not' in answer['why'], answer
            assert 'restarting' in answer['why'], answer
            if stage == 'create':
                assert 'not known' in answer['why'], answer
                assert 'sessions' in answer['why'] and 'new' in answer['why'], answer
                assert not server.sessions
                assert 'session' not in answer
            else:
                assert answer['session'] in server.sessions, answer
                assert 'session was created' in answer['why'], answer
                assert 'attached again or ended' in answer['why'], answer

    asyncio.run(check())


def test_new_create_answered_by_front_leaves_creation_unknown(serve, tmp_path, monkeypatch):
    """A 504 can arrive after the core finished the create: drive must not say the session is absent."""
    async def check():
        server, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
            await rig.login()
            client = rig.app.client
            request = client._http.request

            def front_after_create(method, path, *args, **kwargs):
                if method == 'POST' and path == '/sessions':
                    created = request(method, path, *args, **kwargs)
                    assert created.status == 201, created
                    return Response(504, [], b'<html>Gateway timeout</html>')
                return request(method, path, *args, **kwargs)

            monkeypatch.setattr(client._http, 'request', front_after_create)
            answer = await rig.cmd('new', target=_serve.TARGET)
            assert answer['ok'] is False, answer
            assert answer['error'] is None, answer
            assert 'front answered' in answer['why'] and 'TSB did not' in answer['why'], answer
            assert 'restarting' in answer['why'], answer
            assert 'not known' in answer['why'], answer
            assert 'sessions' in answer['why'] and 'new' in answer['why'], answer
            assert server.sessions, answer
            assert 'session' not in answer, answer

    asyncio.run(check())


def test_version_without_broker_matches_about(capsys):
    app = SimpleNamespace(engine=SimpleNamespace(build_info=SimpleNamespace(version_string='test')),
                          cfg=config.load())
    expected = about_lines(app)[0]
    with pytest.raises(SystemExit) as exc:
        __main__.main(['--version'])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == expected + '\n'
    assert captured.err == ''


def test_ready_version_matches_about(serve, tmp_path):
    async def check():
        _, url = serve()
        async with Rig(url, tmp_path=tmp_path, overrides=("size.auto_request=true",)) as rig:
            assert rig.driver.ready((120, 40))['version'] == about_lines(rig.app)[0]
    asyncio.run(check())


def test_all_keys_marks_target_edit_permission():
    lines = keys_lines(config.load())
    for key, action in [('n', 'create'), ('e', 'edit'), ('d', 'delete')]:
        assert any(line.split() and line.split()[0] == key and 'Targets tab' in line and action in line
                   and line.endswith('(needs target.edit)') for line in lines)
