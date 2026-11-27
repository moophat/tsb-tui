"""C3 control ordering rules: each one must fail the sequence that breaks it.

The same rules grade the fixtures offline and a harness's captures live, so a rule that cannot
fail would let a worker that reports connection loss for one session only, or a core that opens
channels before the connection is up, pass both.
"""

import json
import re
import unittest

from tsbc import _ipc
from tsbc.schema import Registry

from . import CONTRACT

CONNECT = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo"})
CONNECTED = ("worker", {"event": "connected", "conn_id": "c"})


def shell(sid):
    return ("core", {"cmd": "open_shell", "session_id": sid, "term": "xterm", "cols": 80, "rows": 24})


def opened(sid, kind="shell"):
    return ("worker", {"event": "channel_opened", "session_id": sid, "session_type": kind, "socket": f"{sid}.sock"})


def rules(messages):
    return [(rule, text) for rule, text in _ipc.conn_order(messages)]


class ConnectionOrder(unittest.TestCase):
    def test_a_well_formed_sequence(self):
        self.assertEqual(rules([CONNECT, CONNECTED, shell("a"), opened("a")]), [])

    def test_connect_comes_first(self):
        self.assertIn("first command", rules([shell("a")])[0][1])

    def test_hostkey_after_connected_is_misplaced(self):
        hostkey = ("worker", {"event": "hostkey", "conn_id": "c", "fingerprint": "x", "first_use": True})
        self.assertTrue(rules([CONNECT, CONNECTED, hostkey]))

    def test_channel_before_connected(self):
        self.assertTrue(rules([CONNECT, shell("a"), CONNECTED, opened("a")]))

    def test_channel_opened_of_the_wrong_type(self):
        self.assertIn("session_type", rules([CONNECT, CONNECTED, shell("a"), opened("a", "sftp")])[0][1])

    def test_resize_of_a_session_that_is_not_open(self):
        resize = ("core", {"cmd": "resize", "session_id": "zz", "cols": 80, "rows": 24})
        self.assertTrue(rules([CONNECT, CONNECTED, resize]))

    def test_unanswered_open(self):
        self.assertIn("never answered", rules([CONNECT, CONNECTED, shell("a")])[0][1])

    def test_an_open_given_up_at_its_bound_gets_no_channel_opened_afterwards(self):
        # Past open_timeout the worker answers error naming the session (no code) and never sends
        # channel_opened for it afterwards (ipc-control open_shell; C3/open-timeout-expired): the error
        # answers the open, and a later channel_opened answers nothing.
        bounded = ("core", {**shell("a")[1], "open_timeout": 20000})
        given_up = ("worker", {"event": "error", "session_id": "a", "message": "not opened within open_timeout"})
        self.assertEqual(rules([CONNECT, CONNECTED, bounded, given_up]), [])
        late = rules([CONNECT, CONNECTED, bounded, given_up, opened("a")])
        self.assertEqual(late, [(_ipc.ORDER, "message 5 (channel_opened): no open command asked for session a")])

    def test_connection_lost_must_list_every_open_session(self):
        lost_one = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["a"], "cause": "transport",
                               "reason": "x"})
        problems = rules([CONNECT, CONNECTED, shell("a"), opened("a"), shell("b"), opened("b"), lost_one])
        self.assertEqual([r for r, _ in problems], [_ipc.LOST_SET])

    def test_a_closed_session_is_not_listed_as_lost(self):
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        lost = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["b"], "cause": "transport",
                           "reason": "x"})
        self.assertEqual(rules([CONNECT, CONNECTED, shell("a"), opened("a"), shell("b"), opened("b"), closed, lost]), [])

    def test_who_closed_it_must_be_true(self):
        # by core and cause disconnect say the core asked. Claimed without the ask, they would turn
        # an end from outside TSB into the core's own and hide the target_closed or connection_lost.
        up = [CONNECT, CONNECTED, shell("a"), opened("a")]
        by_core = ("worker", {"event": "channel_closed", "session_id": "a", "by": "core", "reason": "x"})
        disconnect_answer = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["a"],
                                        "cause": "disconnect", "reason": "x"})
        self.assertIn("the core sent no close_channel", " ".join(t for _, t in rules(up + [by_core])))
        self.assertIn("the core sent no disconnect", " ".join(t for _, t in rules(up + [disconnect_answer])))

    def test_unknown_cmd_leaves_the_connection_as_it_was(self):
        # A later core probes a v0.1 worker with a command it does not know; the answer must not
        # read as the connection being refused, or the probe would kill what it was testing.
        probe = ("core", {"cmd": "x-later"})
        unknown = ("worker", {"event": "error", "code": "unknown_cmd", "message": "unknown cmd 'x-later'"})
        self.assertEqual(rules([CONNECT, CONNECTED, probe, unknown, shell("a"), opened("a")]), [])
        self.assertEqual(rules([CONNECT, probe, unknown, CONNECTED, shell("a"), opened("a")]), [])
        refused = ("worker", {"event": "error", "code": "hostkey_changed", "message": "refused"})
        self.assertTrue(rules([CONNECT, refused, CONNECTED]))

    def test_close_and_disconnect_await_their_answers(self):
        # close_channel is answered by channel_closed, disconnect by connection_lost; a capture that
        # ends without them, or goes on after disconnect, hides a worker that never answered.
        up = [CONNECT, CONNECTED, shell("a"), opened("a")]
        close = ("core", {"cmd": "close_channel", "session_id": "a"})
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "core", "reason": "closed by the core"})
        disconnect = ("core", {"cmd": "disconnect"})
        lost = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [], "cause": "disconnect",
                           "reason": "disconnect"})
        lost_a = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["a"], "cause": "transport",
                             "reason": "x"})
        resize = ("core", {"cmd": "resize", "session_id": "a", "cols": 80, "rows": 24})
        self.assertEqual(rules(up + [close, closed, disconnect, lost]), [])
        self.assertEqual(rules(up + [close, lost_a]), [])  # a loss answers a pending close too
        cases = {
            "was never answered by channel_closed": up + [close],
            "disconnect was never answered by connection_lost": up + [disconnect],
            "a command after disconnect": up + [disconnect, shell("b"), lost_a],
            "is already closing": up + [close, resize, closed],
        }
        for needle, messages in cases.items():
            with self.subTest(needle):
                found = [text for _, text in rules(messages)]
                self.assertTrue(any(needle in t for t in found), found)

    def test_nothing_after_the_connection_ends(self):
        failed = ("worker", {"event": "auth_failed", "conn_id": "c", "reason": "no"})
        self.assertTrue(rules([CONNECT, failed, shell("a")]))


def hello(sid, lcs=0, version="0.2"):
    return ("core", {"cmd": "hello", "session_id": sid, "proto_version": version, "last_committed_seq": lcs})


def hello_ack(sid, fbs=1, fbo=0, current=0, ended=False, version="0.2"):
    return ("worker", {"event": "hello_ack", "session_id": sid, "proto_version": version, "first_buffered_seq": fbs,
                       "first_buffered_offset": fbo, "current_seq": current, "ended": ended})


def replay(sid, from_seq):
    return ("core", {"cmd": "replay", "session_id": sid, "from_seq": from_seq})


def replay_done(sid, through):
    return ("worker", {"event": "replay_done", "session_id": sid, "through_seq": through})


def cmd(name, sid=None, **kw):
    return ("core", {"cmd": name, **({"session_id": sid} if sid else {}), **kw})


def texts(messages, runs=None):
    return " | ".join(text for _, text in _ipc.conn_order(messages, runs))


LINK = ("worker", {"event": "link_state", "conn_id": "c", "state": "answering", "last_ack_at": "2026-09-29T01:00:00Z"})


class ReattachOrder(unittest.TestCase):
    """The handshake runs on two sockets, and the order is what keeps a keystroke off a stale
    screen (immortality §6.3) and every lost byte declared (§4.3): each rule fails the sequence that
    breaks it."""

    UP = (CONNECT, CONNECTED, shell("a"), opened("a"))

    def new_session(self, sid="a"):
        return [hello(sid), hello_ack(sid), replay(sid, 1), replay_done(sid, 0), cmd("live", sid)]

    def test_a_new_session_and_a_redial(self):
        redial = [hello("a", 10), hello_ack("a", fbs=8, fbo=900, current=12), replay("a", 11), replay_done("a", 12),
                  cmd("live", "a")]
        self.assertEqual(texts([*self.UP] + self.new_session() + redial), "")

    def test_each_step_waits_for_the_one_before(self):
        cases = {
            "before its channel_opened": [CONNECT, CONNECTED, shell("a"), hello("a"), opened("a")],
            "never opened": [CONNECT, CONNECTED, hello("zz"), hello_ack("zz"), replay("zz", 1), replay_done("zz", 0),
                             cmd("live", "zz")],
            "before its hello_ack": [*self.UP] + [hello("a"), replay("a", 1)],
            "a replay starts at the next seq": [*self.UP] + [hello("a", 7), hello_ack("a", 8, 1, 7), replay("a", 7)],
            "read the channel mid-handshake": [*self.UP] + [hello("a"), hello_ack("a"), replay("a", 1),
                                                         replay_done("a", 3)],
            "before replay_done": [*self.UP] + [hello("a"), hello_ack("a"), replay("a", 1), cmd("live", "a")],
            "while its handshake is under way": [*self.UP] + [hello("a"), hello("a")],
            "the hello offered": [*self.UP] + [hello("a"), hello_ack("a", version="0.3")],
            "past current_seq": [*self.UP] + [hello("a"), hello_ack("a", fbs=5, current=2)],
            "the hello for session a was never answered": [*self.UP] + [hello("a")],
            "hello before the connection is up": [CONNECT, hello("a")],
            "(hello_ack): answers no hello for session a": [*self.UP] + [hello_ack("a")],
            "replay_done for session a answers no replay": [*self.UP] + [hello("a"), hello_ack("a"), replay_done("a", 0)],
            "the replay for session a was never answered by replay_done": [*self.UP] + [hello("a"), hello_ack("a"),
                                                                                       replay("a", 1)],
            "release for session a before its ended replay is complete": [*self.UP] + [hello("a"), cmd("release", "a")],
        }
        for needle, messages in cases.items():
            with self.subTest(needle):
                self.assertIn(needle, texts(messages))

    def test_a_worker_reports_only_what_it_holds(self):
        # A link_state before connected reports a link that does not exist yet, and an input_refused
        # for a session the connection never held points the core's defect count at the wrong session.
        refused = ("worker", {"event": "input_refused", "session_id": "zz", "seq": 1, "reason": "ended"})
        self.assertIn("link_state before connected", texts([CONNECT, LINK]))
        self.assertIn("input_refused for session zz, which this connection does not hold", texts([*self.UP, refused]))
        self.assertEqual(texts([*self.UP, LINK, ("worker", dict(refused[1], session_id="a"))]), "")

    def test_an_ended_session_is_released_not_made_live(self):
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        self.assertEqual(texts([*self.UP] + self.new_session() + [closed, cmd("release", "a")]), "")
        self.assertIn("which has not ended", texts([*self.UP] + self.new_session() + [cmd("release", "a")]))
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        ended = [LINK, hello("a", 4), hello_ack("a", fbs=3, fbo=40, current=6, ended=True), replay("a", 5),
                 replay_done("a", 6)]
        base = [*self.UP] + self.new_session()
        runs = [run1] * len(base) + [run2] * (len(ended) + 1)
        self.assertEqual(texts(base + ended + [cmd("release", "a")], runs), "")
        self.assertIn("release takes live's place", texts(base + ended + [cmd("live", "a")], runs))

    def test_a_retained_session_cannot_come_back_to_life(self):
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        messages = [*self.UP] + self.new_session() + [closed, hello("a", 2), hello_ack("a", 3, 20, 2)]
        self.assertIn("the hello_ack says it has not", texts(messages))

    def test_a_handshake_error_answers_the_hello(self):
        for code in ("no_data_peer", "unsupported_proto", "no_such_session"):
            with self.subTest(code=code):
                err = ("worker", {"event": "error", "code": code, "session_id": "a", "message": code})
                self.assertEqual(texts([*self.UP] + [hello("a"), err]), "")
                self.assertIn("answers no hello", texts([*self.UP] + [err]))

    def test_unknown_cmd_naming_the_session_answers_its_hello(self):
        # A peer that does not know hello answers unknown_cmd; the version rule makes its session
        # unreachable / protocol. That is an answer, not a hello left hanging.
        unknown = ("worker", {"event": "error", "code": "unknown_cmd", "session_id": "a", "message": "unknown cmd 'hello'"})
        self.assertEqual(texts([*self.UP] + [hello("a"), unknown]), "")
        self.assertIn("the hello for session a was never answered",
                      texts([*self.UP] + [hello("a"), ("worker", dict(unknown[1], session_id="b"))]))

    def test_heartbeats_are_answered_in_order(self):
        ping = lambda n: cmd("ping", n=n)
        pong = lambda n: ("worker", {"event": "pong", "n": n})
        self.assertEqual(texts([*self.UP] + [ping(1), pong(1), ping(2)]), "")  # the last may be in flight
        self.assertIn("pong 9 answers no ping", texts([*self.UP] + [ping(1), pong(9)]))
        self.assertIn("ping 1 was never answered, and a later one was", texts([*self.UP] + [ping(1), ping(2), pong(2)]))
        self.assertIn("ping 1 was never answered", texts([*self.UP] + [ping(1), ping(2)]))

    def test_a_hung_worker_may_end_with_heartbeat_missed_pings_unanswered(self):
        # A hung worker misses heartbeat_missed pings (3) before its supervisor kills it
        # (session model §11, T11), so a capture of one ends with up to that many unanswered
        # trailing pings; the value rides the connection's connect. The last is also the one in
        # flight at the capture's end, so exactly heartbeat_missed is allowed.
        xping = lambda n: cmd("ping", n=n)
        conn = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo",
                         "timers": {"heartbeat_missed": 3}})
        up = [conn, CONNECTED, shell("a"), opened("a")]
        self.assertEqual(texts(up + [xping(n) for n in (1, 2, 3)]), "")

    def test_a_hung_worker_with_one_ping_too_many_still_fails(self):
        # More than heartbeat_missed unanswered pings means the core did not kill the worker it
        # should have: the supervisor's countdown ran out one ping ago.
        xping = lambda n: cmd("ping", n=n)
        conn = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo",
                         "timers": {"heartbeat_missed": 3}})
        up = [conn, CONNECTED, shell("a"), opened("a")]
        self.assertIn("ping 1 was never answered", texts(up + [xping(n) for n in (1, 2, 3, 4)]))

    def test_a_later_timers_raises_the_allowance_for_pings_after_it(self):
        # A core-wide timers change (connection-scoped) raises heartbeat_missed for the pings sent
        # under the new value: 1 then timers to 3, then three trailing pings pass; a fourth fails.
        xping = lambda n: cmd("ping", n=n)
        conn = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo",
                         "timers": {"heartbeat_missed": 1}})
        up = [conn, CONNECTED, shell("a"), opened("a")]
        raised = cmd("timers", timers={"heartbeat_missed": 3})
        self.assertEqual(texts(up + [raised] + [xping(n) for n in (1, 2, 3)]), "")
        self.assertIn("ping 1 was never answered", texts(up + [raised] + [xping(n) for n in (1, 2, 3, 4)]))

    def test_a_later_timers_does_not_excuse_pings_sent_before_it(self):
        # Two unanswered pings were already in flight under heartbeat_missed 1; a timers raising it
        # to 3 afterwards must not excuse them — the oldest of the trailing pings was sent when
        # only one was allowed.
        xping = lambda n: cmd("ping", n=n)
        conn = ("core", {"cmd": "connect", "target_id": "t", "protocol": "echo",
                         "timers": {"heartbeat_missed": 1}})
        up = [conn, CONNECTED, shell("a"), opened("a")]
        raised = cmd("timers", timers={"heartbeat_missed": 3})
        self.assertIn("ping 1 was never answered", texts(up + [xping(1), xping(2), raised]))

    def test_a_linger_close_lists_no_session(self):
        linger = lambda ids: ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ids,
                                         "cause": "linger", "reason": "connection_linger"})
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        self.assertEqual(texts([*self.UP] + [closed, linger([])]), "")  # the retained session is not listed
        self.assertEqual(texts([CONNECT, CONNECTED, linger([])]), "")  # a connection that never carried one
        # A linger close that lists a session breaks its shape (ipc-control connection_lost).
        self.assertTrue(_ipc.message_errors(Registry.from_tree(CONTRACT), "conn-control", "worker", linger(["a"])[1]))
        self.assertIn("a linger close while sessions ['a'] are open", texts([*self.UP] + [linger([])]))

    def test_a_run_boundary_drops_what_was_in_flight(self):
        # A kill -9 cuts the first run mid-exchange. The restarted core reattaches: no connect, the
        # worker's first event is link_state, and nothing the dead core had in flight is "never
        # answered" — while every rule still holds within each run.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        first = [*self.UP] + [cmd("ping", n=1), hello("a")]
        second = [cmd("timers", timers={}), LINK, hello("a", 3), hello_ack("a", 4, 30, 3), replay("a", 4),
                  replay_done("a", 3), cmd("live", "a"),
                  ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["a"], "cause": "transport",
                              "reason": "x"})]
        runs = [run1] * len(first) + [run2] * len(second)
        self.assertEqual(texts(first + second, runs), "")
        self.assertIn("while its handshake is under way", texts(first + second))  # no boundary: the same lines fail
        no_link = [hello("a", 3), hello_ack("a", 4, 30, 3)]
        self.assertIn("is link_state", texts(first + no_link, [run1] * len(first) + [run2] * 2))
        self.assertIn("runs follow in the order", texts(first + no_link, [run2] * len(first) + [run1] * 2))
        # A session the dead core had open may have ended while it was away: a loss need not list it.
        lost = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [], "cause": "transport",
                           "reason": "x"})
        self.assertEqual(texts([*self.UP] + [LINK, lost], [run1] * 4 + [run2] * 2), "")

    def test_a_redial_reports_link_state_while_the_core_is_disconnecting(self):
        # Link-liveness step 10: the worker MUST send link_state as the first event to every new
        # control peer of a connected connection, and it may send it before reading the core's
        # disconnect. Refusing that link_state fails a compliant worker (issue #72).
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        first = [CONNECT, CONNECTED]
        second = [cmd("timers", timers={}), cmd("disconnect"), LINK,
                  ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                              "cause": "disconnect", "reason": "disconnect"})]
        runs = [run1] * len(first) + [run2] * len(second)
        self.assertEqual(texts(first + second, runs), "")

    def test_a_later_link_state_while_the_core_is_disconnecting_is_allowed(self):
        # A link state that reaches the core mid-disconnect (the peer answering "missing") is a
        # truthful report, not an error: refusing it would fail a worker that reports what it sees.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        missing = ("worker", {"event": "link_state", "conn_id": "c", "state": "missing",
                              "last_ack_at": "2026-09-29T01:00:00Z"})
        first = [CONNECT, CONNECTED]
        second = [cmd("timers", timers={}), cmd("disconnect"), LINK, missing,
                  ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                              "cause": "disconnect", "reason": "disconnect"})]
        runs = [run1] * len(first) + [run2] * len(second)
        self.assertEqual(texts(first + second, runs), "")

    def test_link_state_before_connected_is_still_a_problem(self):
        # Before the link exists there is nothing to report: a worker that announces a link it has
        # not built must not pass, so the rule keeps its edge for states before connected.
        self.assertIn("link_state before connected: there is no link to report yet", texts([CONNECT, LINK]))

    def test_link_state_after_the_connection_ended_is_a_problem(self):
        # Nothing comes after connection_lost; a link_state there reports a link that is gone. The
        # post-end states are reported by the rule that stops every message after the connection ends.
        disconnect = cmd("disconnect")
        lost = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": ["a"],
                           "cause": "disconnect", "reason": "disconnect"})
        self.assertIn("after the connection ended", texts([*self.UP, disconnect, lost, LINK]))

    def test_a_reattached_hello_before_the_first_ping_fails(self):
        # Core-restart step 22 (proto 0.4): toward a worker whose version it speaks, the restarted core's
        # first ping on the control socket goes before its first hello there. A 0.4 hello in a reattached
        # run with no core ping before it in that run is a REATTACH problem.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        seq = base + [LINK, hello("a", 5, version="0.4"), hello_ack("a", 6, 1, 5, version="0.4")]
        runs = [run1] * len(base) + [run2] * 3
        self.assertIn("hello before the restarted core's first ping", texts(seq, runs))

    def test_a_ping_before_the_reattached_hello_is_clean(self):
        # The restarted core pings first, then hello: the rule is satisfied, the capture is clean.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        ping, pong = cmd("ping", n=1), ("worker", {"event": "pong", "n": 1})
        seq = base + [LINK, ping, pong, hello("a", 5, version="0.4"), hello_ack("a", 6, 1, 5, version="0.4")]
        runs = [run1] * len(base) + [run2] * 5
        self.assertEqual(texts(seq, runs), "")

    def test_a_capture_of_all_03_hellos_is_not_flagged(self):
        # A 0.4 core toward 0.3 workers offers "0.3"; a capture with no 0.4 hello is not checked (those
        # windows are graded by the core's own test), so a reattached 0.3 hello first passes.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        seq = base + [LINK, hello("a", 5, version="0.3"), hello_ack("a", 6, 1, 5, version="0.3")]
        runs = [run1] * len(base) + [run2] * 3
        self.assertNotIn("hello before the restarted core's first ping", texts(seq, runs))

    def test_a_reattached_hello_at_05_before_the_first_ping_fails(self):
        # Core-restart step 22 is compared numerically: a reattached run whose hello offers 0.5 must be
        # preceded by that run's first ping, as a 0.4 hello must be.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        seq = base + [LINK, hello("a", 5, version="0.5"), hello_ack("a", 6, 1, 5, version="0.5")]
        runs = [run1] * len(base) + [run2] * 3
        self.assertIn("hello before the restarted core's first ping", texts(seq, runs))

    def test_a_ping_before_the_reattached_hello_at_05_is_clean(self):
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        ping, pong = cmd("ping", n=1), ("worker", {"event": "pong", "n": 1})
        seq = base + [LINK, ping, pong, hello("a", 5, version="0.5"), hello_ack("a", 6, 1, 5, version="0.5")]
        runs = [run1] * len(base) + [run2] * 5
        self.assertEqual(texts(seq, runs), "")

    def test_a_hello_in_the_first_run_is_not_flagged(self):
        # A fresh core starts with connect (no run boundary), so its first hello need not be preceded by a
        # ping even when it offers 0.4: the ping-first rule is only for a reattached run.
        seq = [CONNECT, CONNECTED, hello("a", 0, version="0.4"), hello_ack("a", 1, 0, 0, version="0.4")]
        self.assertNotIn("hello before the restarted core's first ping", texts(seq))

    def test_a_03_run_hello_first_and_a_04_run_ping_first_pass(self):
        # The ping-first rule is per run: a hello before the run's first ping is a problem only when that
        # run's core speaks 0.4. A 0.3 restart may hello before its ping; a 0.4 restart must ping first.
        r1, r2, r3 = "0" * 19 + "1", "0" * 19 + "2", "0" * 19 + "3"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        run2 = [LINK, hello("a", 4, version="0.3"), hello_ack("a", 5, 3, 4, version="0.3"), replay("a", 5),
                replay_done("a", 4), cmd("live", "a")]
        run3 = [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 8, version="0.4"),
                hello_ack("a", 9, 7, 8, version="0.4"), replay("a", 9), replay_done("a", 8), cmd("live", "a")]
        runs = [r1] * len(base) + [r2] * len(run2) + [r3] * len(run3)
        self.assertEqual(texts(base + run2 + run3, runs), "")


TELNET = ("core", {"cmd": "connect", "target_id": "t", "protocol": "telnet", "host": "192.0.2.10", "port": 23,
                   "term": "xterm", "cols": 80, "rows": 24})


class TelnetOrder(unittest.TestCase):
    """A telnet connection carries one session, ever: the rules that keep it so must fail the capture
    that breaks each one, and pass the compliant reuse refusal beside a live session."""

    UP = (TELNET, CONNECTED, shell("a"), opened("a"))

    def refused(self, sid):
        return ("worker", {"event": "error", "code": "not_supported", "session_id": sid,
                           "message": f"one session: {sid} refused"})

    def test_a_refused_reuse_beside_a_live_session_passes(self):
        # The worker answers a later open_shell with not_supported naming its own session, and nothing
        # follows: the connection and its session go on, so the capture is clean.
        self.assertEqual(texts([*self.UP, shell("b"), self.refused("b")]), "")

    def test_a_hostkey_on_a_telnet_connection_fails(self):
        hostkey = ("worker", {"event": "hostkey", "conn_id": "c", "fingerprint": "x", "first_use": True})
        self.assertIn("no host key (RFC 854)", texts([TELNET, hostkey, CONNECTED]))

    def test_a_second_session_is_never_opened(self):
        # channel_opened for a second session on the connection's socket: the rule names the one session.
        self.assertIn("second session on a telnet connection",
                      texts([*self.UP, shell("b"), opened("b")]))

    def test_single_session_is_owed_after_the_one_session_ends(self):
        closed = ("worker", {"event": "channel_closed", "session_id": "a", "by": "core", "reason": "closed"})
        single = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                             "cause": "single_session", "reason": "the one session ended"})
        close = cmd("close_channel", "a")
        self.assertEqual(texts([*self.UP, close, closed, single]), "")
        # The worker emits nothing between the end and its report: a link_state there breaks the rule.
        self.assertIn("single_session report is owed next", texts([*self.UP, close, closed, LINK, single]))
        # A connection_lost of another cause there is not the report.
        transport = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                                "cause": "transport", "reason": "x"})
        self.assertIn("next event is connection_lost cause single_session", texts([*self.UP, close, closed, transport]))
        # A capture that ends with the end reported and no connection_lost fails: the close is missing.
        self.assertIn("single_session report was never sent", texts([*self.UP, close, closed]))

    def test_a_failed_first_open_owes_single_session(self):
        # The first open_shell is answered by an error, so no channel_opened ever named the connection's
        # one session. The close with single_session is still owed: the capture must report the missing
        # connection_lost, or a connection that fails to open its one session closes ungraded.
        failed = ("worker", {"event": "error", "code": "not_supported", "session_id": "a",
                             "message": "the first open failed"})
        self.assertIn("single_session report was never sent",
                      texts([TELNET, CONNECTED, shell("a"), failed]))

    def test_a_failed_first_open_then_single_session_passes(self):
        # The same, with the report present: the connection's one command failed, and it closed with
        # cause single_session. Nothing else is owed, so the capture is clean.
        failed = ("worker", {"event": "error", "code": "not_supported", "session_id": "a",
                             "message": "the first open failed"})
        single = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                             "cause": "single_session", "reason": "the one session ended"})
        self.assertEqual(texts([TELNET, CONNECTED, shell("a"), failed, single]), "")

    def test_an_error_on_an_open_telnet_session_does_not_owe_single_session(self):
        # The first open_shell already succeeded (channel_opened), so the connection's one session is
        # open. An error naming that session later is a failed command on the open session, not the
        # refused first open: it owes no single_session, so the capture — ending after more output and
        # a link_state — is clean.
        failed = ("worker", {"event": "error", "code": "internal", "session_id": "a",
                             "message": "a command on the open session failed"})
        self.assertEqual(texts([TELNET, CONNECTED, shell("a"), opened("a"), failed, LINK]), "")

    def test_single_session_follows_the_release_of_the_one_session(self):
        # The hello path: no channel_closed comes; the core's release for the session is the trigger.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        ping, pong = cmd("ping", n=1), ("worker", {"event": "pong", "n": 1})
        single = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                             "cause": "single_session", "reason": "the one session ended"})
        ended = [LINK, ping, pong, hello("a", 3, version="0.5"), hello_ack("a", 4, 30, 3, ended=True, version="0.5"),
                 replay("a", 4), replay_done("a", 3)]
        base = [*self.UP]
        runs1 = [run1] * len(base)
        runs2 = [run2] * (len(ended) + 2)
        self.assertEqual(texts(base + ended + [cmd("release", "a"), single], runs1 + runs2), "")
        # hello_ack alone is never the trigger: ending the capture after the handshake, with no release,
        # is the worker's legal wait, and release followed by anything but single_session fails.
        self.assertEqual(texts(base + ended, runs1 + [run2] * len(ended)), "")
        self.assertIn("single_session report is owed next",
                      texts(base + ended + [cmd("release", "a"), LINK], runs1 + [run2] * (len(ended) + 2)))

    def test_a_telnet_connection_that_never_opened_a_session_closes_by_linger(self):
        linger = ("worker", {"event": "connection_lost", "conn_id": "c", "session_ids": [],
                             "cause": "linger", "reason": "connection_linger"})
        self.assertEqual(texts([TELNET, CONNECTED, linger]), "")


class TelnetConnectRequired(unittest.TestCase):
    """The telnet branch is new at this release and its peers are at 0.5 only, so a telnet connect
    carries what a v0.2+ connect must (a v0.2 worker refuses one that lacks any of them, decision
    5.36): timers, buffer_bytes and input_frame_max. The ssh and echo branches stay as they are, so
    a v0.1 connect stays valid (immortality §6.4: additive only)."""

    REF = "schemas/ipc-control.schema.json#/$defs/command"

    @classmethod
    def setUpClass(cls):
        cls.reg = Registry.from_tree(CONTRACT)

    def full(self):
        return {"cmd": "connect", "target_id": "t", "protocol": "telnet", "host": "192.0.2.10", "port": 23,
                "term": "xterm-256color", "cols": 80, "rows": 24,
                "timers": {"heartbeat_interval": 10000, "heartbeat_missed": 3, "keepalive_interval": 4000,
                           "keepalive_count_max": 2, "keepalive_probe_interval": 1500, "connection_linger": 0,
                           "orphan_timeout": 60000, "idle": 900000, "unattended": 1800000},
                "buffer_bytes": 65536, "input_frame_max": 4096}

    def test_a_telnet_connect_requires_timers_buffer_bytes_and_input_frame_max(self):
        self.assertEqual(self.reg.validate(self.full(), self.REF), [])
        for key in ("timers", "buffer_bytes", "input_frame_max"):
            with self.subTest(key=key):
                self.assertTrue(self.reg.validate({k: v for k, v in self.full().items() if k != key}, self.REF))

    def test_an_ssh_connect_stays_valid_without_them(self):
        ssh = {"cmd": "connect", "target_id": "t", "protocol": "ssh", "host": "192.0.2.10", "port": 22,
               "user": "u", "auth": {"kind": "key", "key": "FIXTURE-NOT-A-KEY"}}
        self.assertEqual(self.reg.validate(ssh, self.REF), [])


class SingleSessionList(unittest.TestCase):
    """connection_lost cause single_session lists at most the connection's one session (H2(b)):
    empty once the session's end has reached the core, or exactly that session at the bound."""

    REF = "schemas/ipc-control.schema.json#/$defs/event"

    @classmethod
    def setUpClass(cls):
        cls.reg = Registry.from_tree(CONTRACT)

    @staticmethod
    def lost(ids):
        return {"event": "connection_lost", "conn_id": "c", "session_ids": ids,
                "cause": "single_session", "reason": "x"}

    def test_single_session_lists_at_most_one_session(self):
        self.assertEqual(self.reg.validate(self.lost([]), self.REF), [])
        self.assertEqual(self.reg.validate(self.lost(["a"]), self.REF), [])
        self.assertTrue(self.reg.validate(self.lost(["a", "b"]), self.REF))

    def test_the_fixtures_bound_listing_instance_stays_valid(self):
        # The fixture that encodes H2(b) — the one session listed at the bound — stays valid.
        from tsbc.fixtures import load_fixtures

        fixtures, _ = load_fixtures(CONTRACT)
        [fx] = [f for f in fixtures if f.name == "control-message-shape-v0.5"]
        listed = [ins for ins in fx.case["instances"]
                  if ins["valid"] and ins["doc"].get("cause") == "single_session" and ins["doc"].get("session_ids")]
        self.assertTrue(listed)
        for ins in listed:
            self.assertEqual(self.reg.validate(ins["doc"], self.REF), [])


def create_worker(conn, key="alice", n_max=2):
    return ("core", {"cmd": "create_worker", "conn_id": conn, "placement_key": key, "n_max": n_max})


def worker_created(conn, worker):
    return ("supervisor", {"event": "worker_created", "conn_id": conn, "worker_id": worker, "conn_dir": f"/x/conn-{conn}"})


class SupervisorOrder(unittest.TestCase):
    def test_each_create_is_answered_once_by_conn_id(self):
        create, created = create_worker("c1", n_max=1), worker_created("c1", "w")
        self.assertEqual(_ipc.supervisor_order([create, created]), [])
        self.assertTrue(_ipc.supervisor_order([create]))
        self.assertTrue(_ipc.supervisor_order([created]))
        self.assertTrue(_ipc.supervisor_order([create, created, created]))

    def test_a_worker_keeps_its_placement_key_for_life(self):
        # Under per_user placement the key is the owning user's login (session model §4). A worker
        # that took one user's connection and then another's — even after the first one left it,
        # which a supervisor capture cannot see — lets one user's crash or compromised worker reach
        # the other user's sessions (Contract §4: users are tenants).
        apart = [create_worker("c1"), worker_created("c1", "w1"), create_worker("c2"), worker_created("c2", "w1"),
                 create_worker("c3", "bob"), worker_created("c3", "w2")]
        self.assertEqual(_ipc.supervisor_order(apart), [])
        mixed = [create_worker("c1"), worker_created("c1", "w1"), create_worker("c2", "bob"), worker_created("c2", "w1")]
        self.assertIn("a worker never holds two keys", " ".join(_ipc.supervisor_order(mixed)))
        # A restarted core learns the boundary from list_workers, so the reply must tell the truth.
        relabelled = ("supervisor", {"event": "workers", "workers": [
            {"worker_id": "w1", "pid": 7, "placement_key": "bob", "connections": ["c1"]}]})
        listed = [create_worker("c1"), worker_created("c1", "w1"), ("core", {"cmd": "list_workers"}), relabelled]
        self.assertIn("fixed for its life", " ".join(_ipc.supervisor_order(listed)))
        known = [("core", {"cmd": "list_workers"}), relabelled, create_worker("c2", "alice"), worker_created("c2", "w1")]
        self.assertIn("a worker never holds two keys", " ".join(_ipc.supervisor_order(known)))


class SupervisorOneAnswer(unittest.TestCase):
    """Every request line gets exactly one answer, or supervisor_heartbeat's "request unanswered"
    rule would raise supervisor_unresponsive against a supervisor that was merely refusing."""

    def test_the_v02_requests_are_each_answered(self):
        kill = ("core", {"cmd": "kill_worker", "worker_id": "w1", "reap_timeout": 5000})
        killed = ("supervisor", {"event": "worker_killed", "worker_id": "w1"})
        failed = ("supervisor", {"event": "kill_failed", "worker_id": "w1", "reason": "unkillable"})
        ping, pong = ("core", {"cmd": "ping", "n": 1}), ("supervisor", {"event": "pong", "n": 1})
        for exchange in ([kill, killed], [kill, failed], [ping, pong], [ping]):
            with self.subTest(exchange=[m[1].get("cmd") or m[1].get("event") for m in exchange]):
                self.assertEqual(_ipc.supervisor_order(exchange), [])
        self.assertIn("kill_worker w1 was never answered", " ".join(_ipc.supervisor_order([kill, ping])))
        self.assertIn("answers no kill_worker", " ".join(_ipc.supervisor_order([killed])))
        self.assertIn("pong 2 answers no ping", " ".join(_ipc.supervisor_order([ping, ("supervisor", {"event": "pong",
                                                                                                        "n": 2})])))

    def test_a_refusal_is_an_answer(self):
        probe = ("core", {"cmd": "x-later"})
        unknown = ("supervisor", {"event": "error", "reason": "unknown_cmd", "message": "unknown cmd 'x-later'"})
        bad = ("supervisor", {"event": "error", "reason": "bad_request", "conn_id": "c1", "message": "duplicate"})
        self.assertEqual(_ipc.supervisor_order([probe, unknown]), [])
        self.assertEqual(_ipc.supervisor_order([create_worker("c1"), worker_created("c1", "w1"), create_worker("c1"),
                                                bad]), [])
        self.assertIn("never answered", " ".join(_ipc.supervisor_order([probe])))
        # unknown_cmd for a command this tag defines would tell a later core the supervisor is older
        # than it is — a create_worker whose conn_id it could not read is still a create_worker.
        keyless_create = ("core", {"cmd": "create_worker", "placement_key": "u"})
        self.assertIn("unknown_cmd answers create_worker, a command this tag defines",
                      " ".join(_ipc.supervisor_order([keyless_create, unknown])))
        self.assertEqual(_ipc.supervisor_order([keyless_create, ("supervisor", {"event": "error", "reason": "bad_request",
                                                                                 "message": "no conn_id"})]), [])
        # A refusal with no conn_id never takes a create_worker's answer: the create that names
        # its connection is still owed its own, or the capture would hide a supervisor that dropped it.
        keyless = ("supervisor", {"event": "error", "reason": "bad_request", "message": "unreadable"})
        stolen = " ".join(_ipc.supervisor_order([create_worker("c1"), keyless]))
        self.assertIn("bad_request answers no request", stolen)
        self.assertIn("create_worker for c1 was never answered", stolen)

    def test_a_pong_is_not_held_behind_a_kill(self):
        # Replies need not follow request order: a kill waiting out its reap_timeout must not make
        # the heartbeat look unanswered.
        kill = ("core", {"cmd": "kill_worker", "worker_id": "w1", "reap_timeout": 60000})
        ping, pong = ("core", {"cmd": "ping", "n": 3}), ("supervisor", {"event": "pong", "n": 3})
        killed = ("supervisor", {"event": "worker_killed", "worker_id": "w1"})
        self.assertEqual(_ipc.supervisor_order([kill, ping, pong, killed]), [])

    def test_a_refusal_never_takes_a_reply_owed_elsewhere(self):
        # A probe refused unknown_cmd while a kill waits out its reap_timeout: the refusal answers
        # the probe, and the kill, a list_workers and a ping each still get their own reply. Matched
        # to the older request, a correct supervisor would read as one that mixed up its answers.
        kill = ("core", {"cmd": "kill_worker", "worker_id": "w1", "reap_timeout": 60000})
        probe = ("core", {"cmd": "x-later"})
        unknown = ("supervisor", {"event": "error", "reason": "unknown_cmd", "message": "unknown cmd 'x-later'"})
        killed = ("supervisor", {"event": "worker_killed", "worker_id": "w1"})
        listing = ("core", {"cmd": "list_workers"})
        workers = ("supervisor", {"event": "workers", "workers": []})
        ping, pong = ("core", {"cmd": "ping", "n": 5}), ("supervisor", {"event": "pong", "n": 5})
        self.assertEqual(_ipc.supervisor_order([kill, listing, ping, probe, unknown, pong, workers, killed]), [])
        self.assertIn("unknown_cmd answers no request", " ".join(_ipc.supervisor_order([kill, listing, ping, unknown])))
    def test_a_dropped_ping_or_list_workers_is_silence(self):
        # Out of order is allowed; unanswered is not. A supervisor that answers a later ping and
        # never the earlier one, or never answers list_workers, is the silence supervisor_heartbeat
        # exists to catch.
        ping, pong = (lambda n: ("core", {"cmd": "ping", "n": n})), (lambda n: ("supervisor", {"event": "pong", "n": n}))
        self.assertIn("ping 1 was never answered, and a later one was",
                      " ".join(_ipc.supervisor_order([ping(1), ping(2), pong(2)])))
        self.assertIn("list_workers was never answered", " ".join(_ipc.supervisor_order([("core", {"cmd": "list_workers"})])))

    def test_a_restart_drops_the_requests_in_flight(self):
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        messages = [create_worker("c1"), ("core", {"cmd": "ping", "n": 4}), ("core", {"cmd": "ping", "n": 1}),
                    ("supervisor", {"event": "pong", "n": 1})]
        self.assertEqual(_ipc.supervisor_order(messages, [run1, run1, run2, run2]), [])
        self.assertTrue(_ipc.supervisor_order(messages))


class VtOrder(unittest.TestCase):
    """C4 core ↔ VT ordering (vt-ipc): hello first, a request answered by its reply with the same
    request_id, a session's messages after its open, and nothing for a session after its close."""

    @staticmethod
    def _hello():
        return ("core", {"cmd": "hello", "proto_version": "0.3"})

    @staticmethod
    def _ack(grids=()):
        return ("vt", {"event": "hello_ack", "proto_version": "0.3",
                       "engine": {"name": "libghostty-vt", "version": "1"}, "grids": list(grids)})

    @staticmethod
    def _open(sid="s1"):
        return ("core", {"cmd": "open", "session_id": sid, "cols": 80, "rows": 24,
                         "scrollback_bytes": 1024})

    @staticmethod
    def _prefix(sid="s1", rid="r1"):
        return ("core", {"cmd": "prefix", "session_id": sid, "request_id": rid})

    @staticmethod
    def _prefix_ready(sid="s1", rid="r1"):
        return ("vt", {"event": "prefix_ready", "request_id": rid, "session_id": sid,
                       "through_seq": 1, "cols": 80, "rows": 24})

    def order(self, messages):
        return [(rule, text) for rule, text in _ipc.vt_order(messages)]

    def test_a_well_formed_sequence(self):
        self.assertEqual(self.order([self._hello(), self._ack(), self._open(),
                                     self._prefix(), self._prefix_ready()]), [])

    def test_hello_comes_first(self):
        # Rule 1: hello is the first message on the socket, either way.
        before_open = self.order([self._open()])
        self.assertIn("first message", before_open[0][1])
        vt_first = self.order([self._ack()])
        self.assertIn("before the core's hello", vt_first[0][1])

    def test_a_returning_core_opens_with_its_own_hello(self):
        # D19: the grids outlive the core, the handshake does not. A second run whose first message
        # is not hello breaks the rule though an earlier run said hello.
        first = [self._hello(), self._ack(), self._open()]
        runs = ["run-1"] * 3 + ["run-2"]
        bad = _ipc.vt_order(first + [self._prefix()], runs)
        self.assertTrue(any("must be hello" in text for _, text in bad))
        good = _ipc.vt_order(first + [self._hello(), self._ack(["s1"]), self._prefix(), self._prefix_ready()],
                             runs + ["run-2"] * 3)
        self.assertEqual(good, [])

    def test_a_request_is_answered_with_the_same_request_id(self):
        # Rule 2: prefix_ready must carry the request_id the prefix named, and answer a real request.
        wrong_id = self.order([self._hello(), self._ack(), self._open(),
                               self._prefix(rid="r1"), self._prefix_ready(rid="r2")])
        self.assertTrue(any("in-flight request" in text for _, text in wrong_id))
        orphan = self.order([self._hello(), self._ack(), self._open(), self._prefix_ready()])
        self.assertTrue(any("answers no" in text for _, text in orphan))

    def test_a_ping_is_answered_by_a_pong(self):
        # ping and pong carry no id, so the pairing is by order.
        self.assertEqual(self.order([self._hello(), self._ack(),
                                     ("core", {"cmd": "ping"}), ("vt", {"event": "pong"})]), [])
        stray = self.order([self._hello(), self._ack(), ("vt", {"event": "pong"})])
        self.assertIn("answers no ping", stray[0][1])

    def test_a_sessions_messages_come_after_its_open(self):
        # Rule 3: resize before the grid exists is refused (unknown_session in life; an order error here).
        resize = ("core", {"cmd": "resize", "session_id": "zz", "cols": 80, "rows": 24})
        problems = self.order([self._hello(), self._ack(), resize])
        self.assertIn("not open", problems[0][1])

    def test_nothing_for_a_session_after_its_close(self):
        # Rule 4: close drops the grid, so a later message for that session is refused.
        close = ("core", {"cmd": "close", "session_id": "s1"})
        after = self.order([self._hello(), self._ack(), self._open(), close, self._prefix()])
        self.assertIn("after its close", after[0][1])
        # A fresh open makes the grid again, so a session may be reopened after its close.
        reopen = self.order([self._hello(), self._ack(), self._open(), close, self._open(),
                             self._prefix(), self._prefix_ready()])
        self.assertEqual(reopen, [])

    def test_grids_outlive_the_core_so_a_second_hello_is_fine(self):
        # D19: the worker keeps every grid when the socket closes; a returning core's second hello
        # is the same run, not a reset.
        second = self.order([self._hello(), self._ack([("s1", 1, 80, 24)]),
                             self._hello(), self._ack([("s1", 1, 80, 24)])])
        self.assertEqual(second, [])


class PublishedC3(unittest.TestCase):
    def test_every_lang_arch_4_1_message_is_in_the_schema(self):
        # C3 is authored from lang-arch §4.1, not from a summary of it: its whole message list.
        section_4_1 = {"connect", "open_shell", "open_sftp", "open_subsystem", "resize", "disconnect",
                       "connected", "channel_opened", "connection_lost", "auth_failed", "error"}
        reg = Registry.from_tree(CONTRACT)
        defined = (_ipc.defined_kinds(reg, f"{_ipc.CONN}#/$defs/command")
                   | _ipc.defined_kinds(reg, f"{_ipc.CONN}#/$defs/event"))
        self.assertLessEqual(section_4_1, defined)

    def test_create_worker_carries_the_placement_boundary(self):
        # Without a key a supervisor may hand Bob's connection to Alice's worker and nothing on this
        # socket could say otherwise; a key added at a later tag would be ignored by a v0.1
        # supervisor (receivers ignore unknown fields), which would go on mixing users.
        reg = Registry.from_tree(CONTRACT)
        defs = reg.docs[_ipc.SUP]["$defs"]
        self.assertIn("placement_key", defs["create_worker"]["required"])
        self.assertIn("placement_key", defs["workers"]["properties"]["workers"]["items"]["required"])
        rule = defs["create_worker"]["description"]
        for words in ("the same placement_key", "fixed for its life", "fewer than n_max", "session model §4",
                      "Contract §4"):
            self.assertIn(words, rule)
        request = f"{_ipc.SUP}#/$defs/request"
        keyed = {"cmd": "create_worker", "conn_id": "c1", "placement_key": "ops@fixture", "n_max": 1}
        self.assertEqual(reg.validate(keyed, request), [])  # a TSB login name, '@' and all
        self.assertTrue(reg.validate({k: v for k, v in keyed.items() if k != "placement_key"}, request))

    def test_no_claim_token_and_no_snapshot_state_anywhere(self):
        # A local reattach authenticates by SO_PEERCRED alone (immortality §6.1, LOCKED; decision
        # 5.30): the claim-token machinery is satellite-scoped and post-v1, so a claim* property in
        # any schema, or in any instance a fixture calls valid, would ship that scope early. From
        # contract-v0.3 the grid snapshot is real, but only in its one home: the grid_snapshot_record
        # def of continuity-state, and the instances that name it. A snapshot* property anywhere else
        # would put the snapshot beside the session record, where recording_committed_offset is the
        # one counter (C6 grid_snapshot_record; plan.md §3 C6). Only an instance a fixture shows
        # refused may carry either name outside its home — that is how a fixture proves the refusal.
        banned = re.compile(r"^(claim)", re.IGNORECASE)
        snapshot = re.compile(r"^snapshot", re.IGNORECASE)

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        def schema_properties(node, where, in_snapshot):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        yield from ((where, name, in_snapshot) for name in value)
                    yield from schema_properties(value, where, in_snapshot or key == "grid_snapshot_record")
            elif isinstance(node, list):
                for item in node:
                    yield from schema_properties(item, where, in_snapshot)

        reg = Registry.from_tree(CONTRACT)
        hits = []
        # C4's seam has its own snapshot words (snapshot_seq, snapshot_ready, snapshot_incompatible)
        # that name the VT's opaque snapshot, not C6's grid counter; its schema is their home.
        # config.yaml's snapshot: section is the E2 knob home (grid-snapshot rule 12): its keys are
        # the cadence knobs the loader reads, not a counter beside the session record.
        c4_home = "schemas/vt-ipc.schema.json"
        config_home = "schemas/config.schema.json"
        for name, doc in reg.docs.items():
            for where, key, in_snapshot in schema_properties(doc, name, name in (c4_home, config_home)):
                if banned.match(key) or (snapshot.match(key) and not in_snapshot):
                    hits.append(f"{where}: {key}")
        for path in sorted((CONTRACT / "fixtures").rglob("*.json")):
            fx = json.loads(path.read_text(encoding="utf-8"))
            case = fx.get("case", {})
            home = "grid_snapshot_record" in case.get("schema", "") or "config.schema.json" in case.get("schema", "")
            shown = [(home, i["doc"]) for i in case.get("instances", []) if i.get("valid")]
            if fx.get("kind") == "sequence":
                # A vt-control sequence's messages are vt-ipc's, C4's own home for snapshot words.
                in_c4 = case.get("socket") == "vt-control"
                shown += [(home or in_c4, m["msg"]) for m in case.get("messages", [])]
            shown += [(False, case.get("input")), (False, case.get("output")), (False, case.get("steps"))]
            for in_home, doc in shown:
                hits += [f"{path.name}: {k}" for k in keys(doc)
                         if banned.match(k) or (snapshot.match(k) and not in_home)]
        self.assertEqual(hits, [])

    def test_v01_messages_stay_valid_at_v02(self):
        # Additive only (immortality §6.4): every message a v0.1 sequence fixture shows is valid
        # against this tag's schemas, so a v0.2 core reads a v0.1 worker, and a v0.1 core a v0.2
        # worker's v0.1 messages. The v0.1 fixtures are the record of what v0.1 sent.
        reg = Registry.from_tree(CONTRACT)
        seen = 0
        for path in sorted((CONTRACT / "fixtures" / "C3").glob("*.json")):
            fx = json.loads(path.read_text(encoding="utf-8"))
            if fx["kind"] != "sequence" or fx["mark"].get("required-green", fx["mark"].get("published")) != "e0":
                continue
            for m in fx["case"]["messages"]:
                if not m.get("unknown"):
                    seen += 1
                    self.assertEqual(_ipc.message_errors(reg, fx["case"]["socket"], m["from"], m["msg"]), [],
                                     f"{path.name}: {m['msg']}")
        self.assertGreater(seen, 40)


class ChannelClosedByWorker(unittest.TestCase):
    """channel_closed by: worker rules: the worker names the end it decided, and the core records
    it at once. Each test has a capture that must pass and one that must fail on the old grader."""

    UP = (CONNECT, CONNECTED, shell("a"), opened("a"))

    def test_by_worker_with_all_three_end_fields_passes(self):
        # fails today: by: worker was not a known value, so it would be read as target
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        self.assertEqual(rules([*self.UP, by_worker]), [])

    def test_by_worker_without_end_state_fails(self):
        # fails today: the three fields were not checked
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z"})
        self.assertIn("by worker without its end", texts([*self.UP, by_worker]))

    def test_by_worker_without_end_reason_fails(self):
        # fails today: the three fields were not checked
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "ended_at": "2026-10-17T04:00:00.123456Z"})
        self.assertIn("by worker without its end", texts([*self.UP, by_worker]))

    def test_by_worker_without_ended_at_fails(self):
        # fails today: the three fields were not checked
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle"})
        self.assertIn("by worker without its end", texts([*self.UP, by_worker]))

    def test_by_core_with_end_fields_fails(self):
        # fails today: the three fields were not checked
        close = ("core", {"cmd": "close_channel", "session_id": "a"})
        by_core = ("worker", {"event": "channel_closed", "session_id": "a", "by": "core", "reason": "closed",
                              "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z"})
        self.assertIn("by core with an end", texts([*self.UP, close, by_core]))

    def test_by_target_with_end_fields_fails(self):
        # fails today: the three fields were not checked
        by_target = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z"})
        self.assertIn("by target with an end", texts([*self.UP, by_target]))

    def test_by_worker_with_pending_close_channel_clears_it(self):
        # fails today: by: worker still required a pending close_channel
        close = ("core", {"cmd": "close_channel", "session_id": "a"})
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        # The two crossed: the worker sent by: worker before close_channel reached it
        self.assertEqual(rules([*self.UP, close, by_worker]), [])

    def test_by_worker_for_rediscovered_session_in_reattached_run(self):
        # fails today: by: worker for an unseen session was "session was not open"
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        runs = [run1] * len(base) + [run2, run2, run2, run2, run2, run2]
        # In the reattached run, by: worker requires hello first (S5 rule); the event crossed a hello
        # the worker had not yet answered, so no hello_ack precedes it, and release closes the handshake.
        # The connection speaks 0.4, so its first core event after link_state is a ping, before the hello.
        self.assertEqual(texts(base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}),
                                      hello("a", 5, version="0.4"), by_worker, cmd("release", "a")], runs), "")

    def test_by_worker_before_hello_fails(self):
        # fails today: by: worker for an unseen session was only checked for "session was not open"
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        runs = [run1] * len(base) + [run2, run2]
        # S5 rule: by: worker before any hello or open_shell in the run is an ORDER problem
        self.assertIn("not opened or helloed in this run", texts(base + [LINK, by_worker], runs))

    def test_by_worker_on_a_sub_04_handshake_fails(self):
        # by: worker exists only from proto_version 0.4: a fully populated one after a 0.3 handshake fails,
        # and the crossing-hello release exception does not apply to it.
        base = [*self.UP, hello("a", 0, version="0.3"), hello_ack("a", 1, 0, 0, version="0.3")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        self.assertIn("by worker on a connection at proto_version 0.3, which is below 0.4", texts(base + [by_worker]))

    def test_by_target_for_rediscovered_session_in_reattached_run(self):
        # Also covers by: target in a reattached run
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_target = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        runs = [run1] * len(base) + [run2, run2]
        self.assertEqual(texts(base + [LINK, by_target], runs), "")

    def test_by_target_second_close_after_boundary_fails(self):
        # The reattached-run exemption is only for a session the capture never showed closed.
        # Two by: target closes for the same session across a run boundary: the first passes (the
        # session was open in run 1), the second names a session the capture already showed closed.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_target = ("worker", {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit"})
        first_close = base + [by_target]
        second_close = [LINK, by_target]
        runs = [run1] * len(first_close) + [run2] * len(second_close)
        self.assertEqual(texts(first_close), "")  # the first close is fine: the session was open
        self.assertIn("session a was not open", texts(first_close + second_close, runs))

    def test_by_worker_missing_a_new_field_fails(self):
        # A by: worker missing any of out_bytes, last_seq, last_out_ts has no end
        full = {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000}
        for field in ("out_bytes", "last_seq", "last_out_ts"):
            with self.subTest(field=field):
                msg = {k: v for k, v in full.items() if k != field}
                self.assertIn("by worker without its end", texts([*self.UP, ("worker", msg)]))

    def test_by_core_carrying_a_new_field_fails(self):
        # A by: core carrying any of the three ring fields is "by core with an end"
        close = ("core", {"cmd": "close_channel", "session_id": "a"})
        for field, val in (("out_bytes", 120), ("last_seq", 5), ("last_out_ts", 1760000000000000000)):
            with self.subTest(field=field):
                by_core = {"event": "channel_closed", "session_id": "a", "by": "core", "reason": "closed", field: val}
                self.assertIn("by core with an end", texts([*self.UP, close, ("worker", by_core)]))

    def test_by_target_carrying_a_new_field_fails(self):
        # A by: target carrying any of the three ring fields is "by target with an end"
        for field, val in (("out_bytes", 120), ("last_seq", 5), ("last_out_ts", 1760000000000000000)):
            with self.subTest(field=field):
                by_target = {"event": "channel_closed", "session_id": "a", "by": "target", "reason": "exit", field: val}
                self.assertIn("by target with an end", texts([*self.UP, ("worker", by_target)]))


class ReleaseAfterByWorker(unittest.TestCase):
    """release after channel_closed by: worker: the event crossed the hello, so the replay is never
    started and release is accepted."""

    UP = (CONNECT, CONNECTED, shell("a"), opened("a"))

    def test_release_after_by_worker_with_crossed_hello_passes(self):
        # fails today: release before ended replay complete was always a problem
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        runs = [run1] * len(base) + [run2, run2, run2, run2, run2, run2]
        # The event crossed the hello: hello comes first, then by_worker before any hello_ack; the connection
        # speaks 0.4, so its first core event after link_state is a ping, before the hello.
        self.assertEqual(texts(base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}),
                                      hello("a", 5, version="0.4"), by_worker, cmd("release", "a")], runs), "")

    def test_release_without_by_worker_and_no_ended_replay_still_fails(self):
        # The old rule still applies when there's no by: worker
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        runs = [run1] * len(base) + [run2, run2, run2]  # LINK, hello, release
        self.assertIn("before its ended replay is complete", texts(base + [LINK, hello("a", 5), cmd("release", "a")], runs))

    def test_by_worker_exception_cleared_at_run_boundary(self):
        # fails today: by_worker set kept across run boundaries
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        # Run 1: session has by: worker and release
        run1_msgs = [*base, by_worker, cmd("release", "a")]
        # Run 2: same session gets hello then release with no ended: true and no replay_done
        run2_msgs = [LINK, hello("a", 5), cmd("release", "a")]
        runs = [run1] * len(run1_msgs) + [run2] * len(run2_msgs)
        # The by: worker exception from run 1 should not apply in run 2
        self.assertIn("before its ended replay is complete", texts(run1_msgs + run2_msgs, runs))

    def test_by_worker_after_answered_hello_is_a_reattach_problem(self):
        # Once the worker answered the hello with hello_ack, a by: worker before live/release
        # is the worker deciding an end after its own ack — the end waits for live. The release then
        # has no crossed-hello exception (the handshake was answered), so it fails too.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        seq = base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 5, version="0.4"),
                      hello_ack("a", fbs=6, current=5, version="0.4"), by_worker, cmd("release", "a")]
        runs = [run1] * len(base) + [run2] * 7
        self.assertIn("by worker during a handshake the worker already answered: the end waits for live", texts(seq, runs))

    def test_crossed_hello_answered_with_ended_before_release_passes(self):
        # The worker's own timer end crossed a hello it had not read; it then read the hello and
        # answered it ended, and the core released the session after that answer.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        seq = base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 5, version="0.4"),
                      by_worker, hello_ack("a", fbs=6, current=5, ended=True, version="0.4"), cmd("release", "a")]
        runs = [run1] * len(base) + [run2] * 7
        self.assertEqual(texts(seq, runs), "")

    def test_release_before_the_late_ended_ack_passes(self):
        # The release deletes the crossed handshake; the hello_ack the worker sends when it reads the
        # hello it crossed is answered to, not rejected as answering no hello.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        seq = base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 5, version="0.4"),
                      by_worker, cmd("release", "a"), hello_ack("a", fbs=6, current=5, ended=True, version="0.4")]
        runs = [run1] * len(base) + [run2] * 7
        self.assertEqual(texts(seq, runs), "")

    def test_late_ack_after_a_crossed_release_saying_not_ended_fails(self):
        # The worker decided the end, then answered the hello it crossed with ended: false. The session
        # it released cannot be answered as still running.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        seq = base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 5, version="0.4"),
                      by_worker, cmd("release", "a"), hello_ack("a", fbs=6, current=5, version="0.4")]
        runs = [run1] * len(base) + [run2] * 7
        self.assertIn("the core already released", texts(seq, runs))

    def test_second_late_ack_after_a_crossed_release_fails(self):
        # One hello crossed by the worker's end is answered once: a second late ack answers no hello.
        run1, run2 = "0" * 19 + "1", "0" * 19 + "2"
        base = [*self.UP, hello("a"), hello_ack("a"), replay("a", 1), replay_done("a", 0), cmd("live", "a")]
        by_worker = ("worker", {"event": "channel_closed", "session_id": "a", "by": "worker", "reason": "idle timer",
                                "end_state": "killed", "end_reason": "idle", "ended_at": "2026-10-17T04:00:00.123456Z",
                                "out_bytes": 120, "last_seq": 5, "last_out_ts": 1760000000000000000})
        seq = base + [LINK, cmd("ping", n=1), ("worker", {"event": "pong", "n": 1}), hello("a", 5, version="0.4"),
                      by_worker, cmd("release", "a"), hello_ack("a", fbs=6, current=5, ended=True, version="0.4"),
                      hello_ack("a", fbs=6, current=5, ended=True, version="0.4")]
        runs = [run1] * len(base) + [run2] * 8
        self.assertIn("answers no hello for session a", texts(seq, runs))


if __name__ == "__main__":
    unittest.main()
