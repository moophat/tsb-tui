"""The served account API keeps synthetic state and ends the affected synthetic logins."""
import http.client
import json
import unittest
import urllib.parse
from unittest import mock

from tsbc._serve import MAX_BODY, PASSWORD, PEER, USER, ServeApp, StubServer


class ServeAccounts(unittest.TestCase):
    def setUp(self):
        self.app = ServeApp()
        self.admin = self.login(USER, PASSWORD)
        self.peer = self.login(PEER, PASSWORD)

    def login(self, name, password):
        status, headers, _ = self.app.http("POST", "/auth/login", {}, {"username": name, "password": password})
        self.assertEqual(status, 200)
        return {"cookie": headers[0][1].split(";")[0]}

    def request(self, path, body=None, headers=None, method="POST"):
        return self.app.http(method, path, headers or self.admin, body)

    def test_operations_and_state_refusals(self):
        password = "synthetic-new-password"
        status, _, created = self.request("/accounts", {"username": "new-user", "password": password})
        self.assertEqual(status, 201)
        self.assertEqual(created["permissions"], [])
        self.assertNotIn("password", str(created))
        self.assertEqual(self.request("/accounts", {"username": "new-user", "password": password})[0], 409)
        login = self.login("new-user", password)
        self.assertEqual(self.request("/accounts/new-user/enable", {"password": password})[0], 409)
        self.assertEqual(self.request("/accounts/new-user/disable")[0], 204)
        self.assertEqual(self.request("/auth/me", headers=login, method="GET")[0], 401)
        self.assertEqual(self.request("/accounts/new-user/disable")[0], 204)
        self.assertEqual(self.request("/accounts/new-user/password", {"password": password})[2]["error"]["code"], "account_disabled")
        self.assertEqual(self.request("/accounts/new-user/enable", {"password": password})[0], 204)
        login = self.login("new-user", password)
        self.assertEqual(self.request("/accounts/new-user/password", {"password": password + "2"})[0], 204)
        self.assertEqual(self.request("/auth/me", headers=login, method="GET")[0], 401)
        self.login("new-user", password + "2")
        listing = self.request("/accounts", method="GET")[2]["accounts"]
        self.assertEqual([x["username"] for x in listing], sorted(x["username"] for x in listing))
        self.assertTrue(all(set(x) == {"username", "created_at", "disabled"} for x in listing))

    def test_own_change_keeps_only_calling_login_and_redacts_capture(self):
        other = self.login(PEER, PASSWORD)
        new = "fixture-peer-new-password"
        self.assertEqual(self.request("/auth/password", {"current_password": "wrong", "new_password": new}, self.peer)[0], 403)
        self.assertEqual(self.request("/auth/password", {"current_password": PASSWORD, "new_password": new}, self.peer)[0], 204)
        self.assertEqual(self.request("/auth/me", headers=self.peer, method="GET")[0], 200)
        self.assertEqual(self.request("/auth/me", headers=other, method="GET")[0], 401)
        self.login(PEER, new)
        self.assertEqual(self.request("/auth/screen-unlock", {"password": new}, self.peer)[0], 204)
        self.assertEqual(self.request("/auth/screen-unlock", {"password": PASSWORD}, self.peer)[0], 403)
        self.assertNotIn(new, str(self.app.requests))
        self.assertNotIn(new, str(self.app.users))

    def test_refusals_do_not_mutate_accounts(self):
        original = repr(self.app.users)
        self.assertEqual(self.request("/accounts", headers=self.peer, method="GET")[0], 403)
        self.assertEqual(self.request("/accounts/unknown/disable")[0], 404)
        self.assertEqual(self.request(f"/accounts/{USER}/disable")[0], 409)
        for name, password in (("shared", PASSWORD), ("bad/name", PASSWORD), ("new", "short"), ("new", "x" * 1025), ("new", "x" * 15 + "\ud800")):
            self.assertEqual(self.request("/accounts", {"username": name, "password": password})[0], 422)
        foreign = dict(self.admin, origin="https://foreign.example", host="localhost")
        self.assertEqual(self.request("/accounts", {}, foreign)[0], 403)
        self.assertEqual(repr(self.app.users), original)

    def test_account_end_purges_login_resources_and_preserves_terminal_sessions(self):
        cookie = self.peer["cookie"].split("=", 1)[1]
        self.app.unlocked.add(cookie)
        self.app.tied[cookie] = "synthetic-device"
        self.app.challenges["challenge"] = (cookie, "vault.unlock", "synthetic-device", 0)
        self.app.ticket_logins["ticket"] = cookie
        self.app.tickets["ticket"] = (PEER, "session", 0)
        self.app.device_logins["dl"] = {"login": cookie}
        self.app.client_logins["client"] = cookie
        self.app.clients["session"] = {"client": mock.Mock()}
        self.app.sessions["session"] = {"id": "session", "state": "live"}
        with mock.patch.object(self.app, "_dl_end") as device_end, mock.patch.object(self.app, "_drop_client") as client_end:
            # Call under the app lock as the account HTTP dispatch does.
            with self.app.lock:
                self.app._account_end_logins(PEER)
            device_end.assert_called_once_with({"login": cookie}, "cancelled", tell=False, close=4017, reason="account_login_ended")
            client_end.assert_called_once_with(self.app.sessions["session"], "client", code=4017, reason="account_login_ended")
        self.assertNotIn(cookie, self.app.logins)
        self.assertNotIn(cookie, self.app.unlocked)
        self.assertNotIn(cookie, self.app.tied)
        self.assertEqual(self.app.challenges, {})
        self.assertEqual(self.app.tickets, {})
        self.assertEqual(self.app.ticket_logins, {})
        self.assertEqual(self.app.sessions["session"]["state"], "live")

    def test_created_login_has_no_session_or_target_capabilities(self):
        self.assertEqual(self.request('/accounts', {'username': 'ungranted', 'password': PASSWORD})[0], 201)
        login = self.login('ungranted', PASSWORD)
        self.assertEqual(self.request('/auth/me', headers=login, method='GET')[2]['permissions'], [])
        self.assertEqual(self.request('/targets', headers=login, method='GET')[2], {'targets': []})
        # Refuse before resource lookup or body validation, for both existing and absent resources.
        for method, path, body in (
            ('POST', '/sessions', {}), ('GET', '/sessions', None),
            ('GET', '/sessions/missing', None), ('DELETE', '/sessions/missing', None),
            ('GET', '/sessions/missing/output', None),
            ('DELETE', '/sessions/missing/clients/missing', None),
            ('POST', '/auth/ws-ticket', {'session_id': 'missing'}),
            ('POST', '/targets', {}),
            ('PUT', '/targets/missing', {}), ('DELETE', '/targets/missing', None),
            ('GET', '/targets/missing/definition', None), ('GET', '/alerts', None),
            ('GET', '/accounts', None), ('POST', '/vault/org-credentials', {}),
            ('POST', '/vault/grants', {}), ('DELETE', '/vault/users/fixture-peer', None),
        ):
            with self.subTest(method=method, path=path):
                response = self.request(path, body, login, method)
                self.assertEqual(response[0], 403)
                self.assertEqual(response[2]['error']['code'], 'forbidden')
        self.assertEqual(self.request('/targets/fixture-target', headers=login, method='GET')[0], 404)
        self.assertFalse(self.app._may_control('ungranted'))
        self.assertIsNone(self.app._may_attach('ungranted', 'missing'))
        # Operations requiring only a login remain available.
        self.assertEqual(self.request('/auth/password',
                                    {'current_password': PASSWORD, 'new_password': PASSWORD}, login)[0], 204)

    def test_disabled_and_wrong_password_logins_run_the_same_hash_function(self):
        wrong = 'synthetic-wrong-password'
        responses = []
        for disabled in (False, True):
            self.app.users[PEER]['disabled'] = disabled
            with mock.patch.object(self.app, '_account_hash', wraps=self.app._account_hash) as hash_password:
                responses.append(self.app.http('POST', '/auth/login', {},
                                               {'username': PEER, 'password': wrong}))
                hash_password.assert_called_once_with(wrong)
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[0][0], 401)
        self.assertEqual(responses[0][1], [])
        self.assertEqual(responses[0][2]['error']['code'], 'invalid_credentials')
        with mock.patch.object(self.app, '_account_hash', wraps=self.app._account_hash) as hash_password:
            response = self.app.http('POST', '/auth/login', {}, {'username': PEER, 'password': PASSWORD})
            hash_password.assert_called_once_with(PASSWORD)
        self.assertEqual(response, responses[0])

    def test_account_refusal_order(self):
        foreign = {'origin': 'https://foreign.example', 'host': 'localhost'}
        response = self.app.http('POST', '/accounts', foreign, {})
        self.assertEqual(response[0], 401)
        response = self.app.http('POST', '/accounts', dict(foreign, **self.peer), {})
        self.assertEqual(response[0], 403)
        self.assertEqual(response[2]['error']['message'], 'same origin is required')
        response = self.request('/accounts/shared/password', {}, self.peer)
        self.assertEqual(response[0], 403)
        self.assertEqual(response[2]['error']['message'], 'account.admin is required')
        before = repr(self.app.users)
        for path, body in (
            ('/accounts', {'username': USER, 'password': ''}),
            ('/accounts/missing/password', {'password': ''}),
            ('/accounts/missing/enable', {'password': 'short'}),
            (f'/accounts/{PEER}/enable', {'password': ''}),
            ('/auth/password', {'current_password': 'wrong', 'new_password': ''}),
        ):
            with self.subTest(path=path, body_keys=list(body)):
                self.assertEqual(self.request(path, body)[0], 422)
        self.assertEqual(self.request(f'/accounts/{PEER}/disable')[0], 204)
        disabled = repr(self.app.users)
        self.assertEqual(self.request(f'/accounts/{PEER}/password', {'password': ''})[0], 422)
        self.assertEqual(repr(self.app.users), disabled)
        self.assertNotEqual(before, disabled)
        self.assertEqual(self.request('/accounts/missing/password', {'password': PASSWORD})[0], 404)
        self.assertEqual(self.request(f'/accounts/{PEER}/password', {'password': PASSWORD})[0], 409)

    def test_screen_unlock_non_string_password_stays_invalid_after_password_change(self):
        new = 'synthetic-new-password'
        self.assertEqual(self.request('/auth/password',
                                    {'current_password': PASSWORD, 'new_password': new}, self.peer)[0], 204)
        for password in (None, 1, True, [], {}):
            with self.subTest(kind=type(password).__name__):
                response = self.request('/auth/screen-unlock', {'password': password}, self.peer)
                self.assertEqual(response[0], 422)
                self.assertEqual(response[2]['error']['code'], 'invalid_request')


    def test_http_parse_failures_follow_account_refusal_order(self):
        failures = (
            (b'{"password":"never-echo-this",', None),
            (b'\xff', None),
            (b'never-echo-this', 'invalid'),
            (b'never-echo-this', '-1'),
            (b'never-echo-this', str(MAX_BODY + 1)),
        )
        paths = ('/accounts', f'/accounts/{PEER}/disable',
                 f'/accounts/{PEER}/enable', f'/accounts/{PEER}/password', '/auth/password')
        before = repr(self.app.users)
        with StubServer(self.app) as url:
            host = urllib.parse.urlsplit(url).netloc
            for path in paths:
                cases = [({}, 401, 'unauthenticated', 'log in first'),
                         (dict(self.admin, Origin='https://foreign.example'), 403,
                          'forbidden', 'same origin is required'),
                         (dict(self.peer, Origin=f'http://{host}'),
                          422 if path == '/auth/password' else 403,
                          'invalid_request' if path == '/auth/password' else 'forbidden',
                          'the request body cannot be parsed' if path == '/auth/password'
                          else 'account.admin is required'),
                         (dict(self.admin, Origin=f'http://{host}'), 422,
                          'invalid_request', 'the request body cannot be parsed')]
                for raw, length in failures:
                    for headers, status, code, message in cases:
                        with self.subTest(path=path, raw=raw, length=length, status=status):
                            conn = http.client.HTTPConnection(host, timeout=5)
                            try:
                                sent = dict(headers)
                                if length is not None:
                                    sent['Content-Length'] = length
                                conn.request('POST', path, body=raw, headers=sent)
                                response = conn.getresponse()
                                self.assertEqual(response.status, status)
                                self.assertEqual(json.loads(response.read()),
                                                 {'error': {'code': code, 'message': message}})
                                if length is not None:
                                    self.assertEqual(conn.sock.recv(1), b'')
                                else:
                                    # The consumed malformed body leaves the same connection usable.
                                    sock = conn.sock
                                    conn.request('GET', '/healthz')
                                    healthy = conn.getresponse()
                                    self.assertEqual(healthy.status, 200)
                                    healthy.read()
                                    self.assertIs(conn.sock, sock)
                            finally:
                                conn.close()
            # Non-account routes retain the parser's original messages and precedence.
            for raw, length in failures:
                with self.subTest(non_account=True, length=length):
                    conn = http.client.HTTPConnection(host, timeout=5)
                    try:
                        headers = {} if length is None else {'Content-Length': length}
                        conn.request('POST', '/auth/login', body=raw, headers=headers)
                        response = conn.getresponse()
                        self.assertEqual(response.status, 422)
                        message = ('the request body is not JSON' if length is None else
                                   f'Content-Length must be a number of bytes, at most {MAX_BODY}')
                        self.assertEqual(json.loads(response.read()),
                                         {'error': {'code': 'invalid_request', 'message': message}})
                    finally:
                        conn.close()
        self.assertEqual(repr(self.app.users), before)
        self.assertNotIn('never-echo-this', repr(self.app.requests))
