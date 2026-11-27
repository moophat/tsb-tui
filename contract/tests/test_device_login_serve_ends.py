"""The fixture server's device login at its ends: no answer is kept, the records go away, and a device login ends with
the TSB login that created it. The cap is in test_device_login_serve_cap_and_answers.
"""

import asyncio
import contextlib
import json

from tsbc import _aws
from tsbc import _wsframe as _wf
from tsbc._serve import PEER, TARGET, USER

from .test_device_login_serve_cap_and_answers import CANARY, DEV, SYNTH, Pane, Served


def answer(answers, prompt_id=1):
    return {"t": "device_login_answer", "v": 1, "prompt_id": prompt_id, "answers": answers}


class NoAnswerIsKept(Served):
    """No answer is kept: the frames the server read hold the synthetic constant where an answer was, on every path."""

    def setUp(self):
        self.start()

    def answers_held(self) -> list:
        return [f["answers"] for f in self.core.device_login_frames if "answers" in f]

    def assert_none_kept(self) -> None:
        self.assertNotIn(CANARY, repr(self.core.__dict__))      # nowhere in the app's state, not only in the frames

    async def test_a_good_answer_is_kept_as_the_constant_only(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        await pane.send(answer([CANARY, CANARY + "-2"]))
        self.assertEqual((await pane.next())["outcome"], "connected")
        self.assertEqual(self.answers_held(), [[SYNTH, SYNTH]])
        sent = [f for f in self.core.device_login_frames if f["t"] == "device_login_answer"]
        self.assertEqual(sent, [answer([SYNTH, SYNTH])])        # the rest of the frame is kept as it came
        self.assert_none_kept()
        await pane.close()

    async def test_an_answer_the_server_refuses_is_kept_as_the_constant_only(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send(answer([CANARY, CANARY]))                # no host-key answer yet: not_pending
        self.assertEqual((await pane.next())["reason"], "not_pending")
        await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": True})
        await pane.next()
        await pane.send(answer([CANARY]))                        # one string for two prompts: invalid
        self.assertEqual((await pane.next())["reason"], "invalid")
        await pane.send(answer([CANARY, CANARY], prompt_id=7))   # another prompt_id: not_pending
        self.assertEqual((await pane.next())["reason"], "not_pending")
        await pane.send(answer([CANARY + "x" * 2000, CANARY]))   # past 1024 bytes: invalid
        self.assertEqual((await pane.next())["reason"], "invalid")
        self.assertEqual(self.answers_held(), [[SYNTH, SYNTH], [SYNTH], [SYNTH, SYNTH], [SYNTH, SYNTH]])
        self.assert_none_kept()
        await pane.close()

    async def test_a_malformed_answers_value_keeps_no_value_either(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        for malformed in (CANARY, {"x": CANARY}, [1, CANARY, None], [[CANARY]]):
            await pane.send(answer(malformed))
            self.assertEqual((await pane.next())["reason"], "invalid")
        await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": True, "answers": [CANARY]})
        self.assertEqual((await pane.next())["reason"], "not_pending")
        self.assert_none_kept()
        await pane.close()

    async def test_a_cancel_after_an_answer_keeps_none(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send(answer([CANARY, CANARY]))
        await pane.next()
        await pane.send({"t": "device_login_cancel", "v": 1})
        self.assertEqual((await pane.next())["outcome"], "cancelled")
        await pane.close()
        self.assertEqual(self.answers_held(), [[SYNTH, SYNTH]])
        self.assert_none_kept()

    async def test_a_socket_lost_after_an_answer_keeps_none(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send(answer([CANARY, CANARY]))
        await pane.next()
        await pane.close()
        self.assert_none_kept()


class RecordsGoAway(Served):
    """Records go away: one end function drops the record (and the ticket) on every end."""

    def setUp(self):
        self.start()

    def assert_gone(self) -> None:
        self.assertEqual((self.core.device_logins, self.core.dl_tickets), ({}, {}))

    async def test_a_record_lives_while_the_login_is_in_progress(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        self.assertEqual(len(self.core.device_logins), 1)
        await pane.close()

    async def test_a_cancel_drops_it(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send({"t": "device_login_cancel", "v": 1})
        self.assertEqual((await pane.next())["outcome"], "cancelled")
        self.assert_gone()
        await pane.close()

    async def test_a_refused_key_drops_it(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": False})
        self.assertEqual((await pane.next())["outcome"], "hostkey_refused")
        self.assert_gone()
        await pane.close()

    async def test_a_lost_socket_drops_it(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.close()
        self.assert_gone()

    async def test_a_bad_frame_that_fails_the_socket_drops_it(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.next()
        await pane.send([1, 2])                                  # not a JSON object: the socket is failed 1007
        self.assertEqual((await pane.next())[:2], ("close", 1007))
        self.assert_gone()

    async def test_connected_drops_it(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        await pane.send(answer([SYNTH, SYNTH]))
        self.assertEqual((await pane.next())["outcome"], "connected")
        self.assert_gone()
        await pane.close()

    def test_an_unattached_expiry_drops_it_with_no_later_create(self):
        pending = self.create(self.client)
        self.assertEqual(len(self.core.device_logins), 1)
        self.expire(pending)
        self.client.me()                                         # any request: the server's sweep runs
        self.assert_gone()

    async def test_an_expired_ticket_is_refused_and_leaves_no_record(self):
        pending = self.create(self.client)
        self.expire(pending)
        pane = await Pane.open(self.client, pending)
        self.assertEqual((await pane.next())[:2], ("close", 4001))
        self.assert_gone()


    async def test_a_ticket_presented_for_another_device_login_burns_and_ends_its_own(self):
        pending = self.create(self.client)
        pane = await Pane.open(self.client, {**pending, "device_login_id": "some-other-login"})
        self.assertEqual((await pane.next())[:2], ("close", 4003))
        self.assert_gone()

    async def test_a_device_login_ticket_presented_as_an_attach_burns_and_ends_its_login(self):
        pending = self.create(self.client)
        ws = await _aws.AsyncWebSocket.connect(self.client._attach_url(), ssl_context=None, timeout=5)
        pane = Pane(ws)
        await pane.send({"t": "attach", "v": 1, "ticket": pending["ticket"], "session_id": "sess-1"})
        self.assertEqual((await pane.next())[:2], ("close", 4003))
        self.assert_gone()


class TiedToTheTsbLogin(Served):
    """Tied to the TSB login: V10 and login steps 9 to 14."""

    def setUp(self):
        self.start()

    async def test_a_logout_ends_the_pending_logins_under_that_login_only(self):
        mine, other = self.client, self.login(USER)
        pane = await Pane.open(mine, self.create(mine))
        await pane.next()
        theirs = await Pane.open(other, self.create(other))
        await theirs.next()
        mine.logout()
        self.assertEqual(await pane.next(), {"t": "device_login_result", "v": 1, "outcome": "cancelled"})
        self.assertEqual((await pane.next())[:2], ("close", 4016))
        self.assertEqual(len(self.core.device_logins), 1)        # the other login's device login stands
        await theirs.send({"t": "device_login_hostkey_answer", "v": 1, "accept": True})
        self.assertEqual((await theirs.next())["t"], "device_login_prompt")
        await theirs.close()

    async def test_an_answer_after_the_logout_opens_no_session(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        self.client.logout()
        with contextlib.suppress(_aws.ConnectionClosed, OSError):    # the server may have closed the socket already
            await pane.send(answer([SYNTH, SYNTH]))
        await pane.close()
        self.assertEqual((self.core.sessions, self.core.connections, self.core.device_logins), ({}, {}, {}))

    async def test_an_unredeemed_ticket_dies_with_its_login(self):
        pending = self.create(self.client)
        self.client.logout()
        self.assertEqual((self.core.device_logins, self.core.dl_tickets), ({}, {}))
        pane = await Pane.open(self.login(USER), pending)
        self.assertEqual((await pane.next())[:2], ("close", 4001))
        self.assertEqual(self.core.sessions, {})

    async def test_a_revocation_closes_a_pending_login_4015_and_tells_the_pane_no_result(self):
        tied, untied = self.tied_login(), self.login(USER)
        pane = await Pane.open(tied, self.create(tied))
        await pane.next()
        unredeemed = self.create(tied)
        untied.revoke_device(DEV)
        self.assertEqual(await pane.next(), ("close", 4015, "device_revoked"))      # no `device_login_result` first
        self.assertEqual((self.core.device_logins, self.core.dl_tickets), ({}, {}))
        late = await Pane.open(untied, unredeemed)
        self.assertEqual((await late.next())[:2], ("close", 4001))
        self.assertEqual(self.core.sessions, {})

    async def test_the_session_a_device_login_opened_is_a_client_of_its_login(self):
        tied, untied = self.tied_login(), self.login(USER)
        pane = await Pane.open(tied, self.create(tied))
        await pane.through_hostkey()
        await pane.send(answer([SYNTH, SYNTH]))
        self.assertEqual((await pane.next())["outcome"], "connected")
        attached = await pane.next()
        self.assertEqual(attached["t"], "attached")
        cookie = self.core.client_logins[attached["client_id"]]
        self.assertEqual(self.core.tied[cookie], DEV)            # tracked under the login the create came through
        untied.revoke_device(DEV)
        while True:
            got = await pane.next()
            if isinstance(got, tuple):
                break
        self.assertEqual(got, ("close", 4015, "device_revoked"))
        self.assertNotIn(attached["client_id"], self.core.client_logins)
        (sess,) = self.core.sessions.values()                    # the terminal session lives on, detached
        self.assertEqual((sess["owner"], sess["target_id"]), (USER, TARGET))
        self.assertNotEqual(sess["state"], "killed")

    async def test_another_principals_device_login_is_not_touched_by_a_revocation(self):
        tied = self.tied_login()
        peer = self.login(PEER)
        pane = await Pane.open(peer, self.create(peer))
        await pane.next()
        tied.revoke_device(DEV)
        await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": True})
        self.assertEqual((await pane.next())["t"], "device_login_prompt")
        await pane.close()



class DialogueEdges(Served):
    """The served dialogue's edges: a cancel that is already read, a frame at a version the server does not speak, a
    send on a socket the login's end closed."""

    def setUp(self):
        self.start()

    async def test_a_cancel_read_before_the_terminal_answer_is_sent_ends_the_login_cancelled(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        with self.core.lock:                  # the answer and the cancel are both in the socket before the server acts
            await pane.send(answer([SYNTH, SYNTH]))
            await pane.send({"t": "device_login_cancel", "v": 1})
        self.assertEqual(await pane.next(), {"t": "device_login_result", "v": 1, "outcome": "cancelled"})
        self.assertEqual((await pane.next())[:2], ("close", 4016))
        self.assertEqual((self.core.sessions, self.core.connections, self.core.device_logins), ({}, {}, {}))
        await pane.close()

    async def test_a_close_read_before_the_terminal_answer_is_sent_opens_no_session(self):
        for how in ("close frame", "eof"):
            with self.subTest(how):
                pane = await Pane.open(self.client, self.create(self.client))
                await pane.through_hostkey()
                with self.core.lock:              # the answer and the close are both in before the server acts
                    await pane.send(answer([SYNTH, SYNTH]))
                    if how == "eof":
                        pane.ws._drop()
                        await asyncio.sleep(0.1)      # the loop closes the transport before the server may act
                    else:
                        await pane.ws._send_close(1000)
                for _ in range(100):
                    with self.core.lock:
                        if not self.core.device_logins:
                            break
                    await asyncio.sleep(0.02)
                self.assertEqual((self.core.sessions, self.core.connections, self.core.device_logins), ({}, {}, {}))

    async def test_a_host_key_answer_at_another_version_is_ignored(self):
        for version in (2, 0, "1", True, None):
            with self.subTest(v=version):
                pane = await Pane.open(self.client, self.create(self.client))
                await pane.next()
                await pane.send({"t": "device_login_hostkey_answer", "v": version, "accept": True})
                await pane.send({"t": "device_login_hostkey_answer", "accept": True})
                await pane.send({"t": "device_login_hostkey_answer", "v": 1, "accept": False})
                self.assertEqual((await pane.next())["outcome"], "hostkey_refused")     # the first two were no answers
                await pane.close()

    async def test_a_cancel_at_another_version_is_ignored_at_the_prompt(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        await pane.send({"t": "device_login_cancel", "v": 2})
        await pane.send({"t": "device_login_cancel"})
        await pane.send(answer([SYNTH, SYNTH]))
        self.assertEqual((await pane.next())["outcome"], "connected")
        self.assertEqual((await pane.next())["t"], "attached")
        await pane.close()

    async def test_an_answer_at_another_version_is_ignored_at_the_prompt(self):
        pane = await Pane.open(self.client, self.create(self.client))
        await pane.through_hostkey()
        await pane.send({**answer([SYNTH, SYNTH]), "v": 2})
        await pane.send({"t": "device_login_answer", "prompt_id": 1, "answers": [SYNTH, SYNTH]})
        await pane.send({"t": "device_login_cancel", "v": 1})
        self.assertEqual((await pane.next())["outcome"], "cancelled")        # the two answers before it were no answers
        self.assertEqual(self.core.sessions, {})
        await pane.close()

    def test_a_send_on_a_socket_the_logins_end_closed_ends_the_dialogue_quietly(self):
        class Closed:
            """A socket the login's end has closed after the dialogue read an answer: the next send raises."""

            def __init__(self):
                self.closed, self.sent = False, []
                self.reads = iter([json.dumps({"t": "device_login_hostkey_answer", "v": 1, "accept": True})])

            def send_text(self, text):
                if self.sent:
                    self.closed = True
                    raise TimeoutError("socket already closed")
                self.sent.append(text)

            def recv(self):
                return _wf.Message("text", next(self.reads))

        dl = {"id": "dl-x", "ended": False, "ws": None, "ticket": "t", "target_id": TARGET, "term": "xterm",
              "cols": 80, "rows": 24, "login": "c"}
        ws = Closed()
        self.assertIsNone(self.core._dl_dialogue(ws, dl, USER))
        self.assertEqual(len(ws.sent), 1)                                    # the host key ask; nothing after it
