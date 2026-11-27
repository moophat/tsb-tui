"""C3 control sockets: which schema each sender's messages answer to, and the ordering rules a
sequence of them obeys. One implementation, used both for the sequence fixtures (self-check) and
for the control captures a harness collects (--artifacts).
"""

from __future__ import annotations

from typing import Any

from . import selfcheck
from .fixtures import Fixture
from .schema import Registry

CONN = "schemas/ipc-control.schema.json"
SUP = "schemas/supervisor.schema.json"
VT = "schemas/vt-ipc.schema.json"
# The C3 proto_version this tag speaks, and the one the core offers a peer whose version it does not
# know (immortality §6.4; ipc-control top: "proto_version is 0.8 at this tag"). A peer this tag speaks is
# spoken to in its own version; a later peer is offered this one.
C3_VERSION = "0.8"
SENDERS = {
    "conn-control": {"core": f"{CONN}#/$defs/command", "worker": f"{CONN}#/$defs/event"},
    "supervisor-control": {"core": f"{SUP}#/$defs/request", "supervisor": f"{SUP}#/$defs/reply"},
    "vt-control": {"core": f"{VT}#/$defs/core_message", "vt": f"{VT}#/$defs/vt_message"},
}
ORDER, LOST_SET = "order", "lost-set"
REATTACH, HEARTBEAT, LINGER = "reattach", "heartbeat", "linger"
OPENS = {"open_shell": lambda m: "shell", "open_sftp": lambda m: "sftp", "open_subsystem": lambda m: m.get("name")}
# The six fields a channel_closed by: worker carries (the end it decided, and the ring's final
# numbers). out_bytes, last_seq and last_out_ts are unsigned 64-bit integers; last_seq and
# last_out_ts are 0 when the session had no output.
END_FIELDS = ("end_state", "end_reason", "ended_at", "out_bytes", "last_seq", "last_out_ts")


def _version_at_least(version: str | None, floor: str) -> bool:
    try:
        return tuple(map(int, str(version).split("."))) >= tuple(map(int, floor.split(".")))
    except (ValueError, TypeError):
        return False


def _below_04(version: str) -> bool:
    """Whether a proto_version is below 0.4, comparing its numeric parts."""
    parts = str(version).split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False
    return (major, minor) < (0, 4)


def kind(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("cmd") or msg.get("event") or "?")
    return "?"


def message_errors(registry: Registry, socket: str, sender: str, msg: Any) -> list[str]:
    ref = SENDERS[socket].get(sender)
    if ref is None:
        return [f"{sender} does not send on a {socket} socket"]
    return registry.validate(msg, ref)


def defined_kinds(registry: Registry, ref: str) -> set[str]:
    """Every cmd/event name the oneOf at `ref` defines."""
    schema, doc = registry.split(ref)
    names = set()
    for branch in schema["oneOf"]:
        target, _ = registry.resolve(branch["$ref"], doc)
        # A definition that names itself carries its own cmd or event (its oneOf, if any, is a
        # conditional); one that does not is a set of alternatives that each do (connect's two).
        named = any(key in target.get("properties", {}) for key in ("cmd", "event"))
        for alt in [target] if named else target.get("oneOf", [target]):
            props = alt.get("properties", {})
            for key in ("cmd", "event"):
                if key in props:
                    names.add(props[key]["const"])
    return names


HANDSHAKE_ERRORS = frozenset({"unsupported_proto", "no_such_session", "no_data_peer"})
SUP_REQUESTS = frozenset({"create_worker", "list_workers", "ping", "kill_worker"})


def _heartbeat_missed(msg: dict, current: int) -> int:
    """The trailing unanswered pings a capture may end with, from a connect's or a timers
    message's own `timers.heartbeat_missed` (session model §11, T11: a hung worker misses this
    many before its supervisor kills it). No value means the capture is v0.1, or the message
    carries none: keep what we have, one by default."""
    timers = msg.get("timers")
    value = timers.get("heartbeat_missed") if isinstance(timers, dict) else None
    return value if isinstance(value, int) and value >= 1 else current


def _run_order(runs: list, i: int, run, where: str, problems: list, wrap) -> None:
    if run is not None and runs[i] is not None and runs[i] < run:
        problems.append(wrap(f"{where}: run {runs[i]} after run {run}: runs follow in the order the core started"))


DEVICE_LOGIN_EVENTS = frozenset({"hostkey_ask", "auth_prompt"})
DEVICE_LOGIN_COMMANDS = frozenset({"hostkey_answer", "auth_answer"})
# What the core may send on a prompt-mode connection before its terminal answer (C3 rule 2).
BEFORE_TERMINAL = DEVICE_LOGIN_COMMANDS | {"disconnect", "ping"}
PROMPT_METHODS = ("banner", "password", "keyboard-interactive")
HOSTKEY_KINDS = ("first_use",)


class PromptLogin:
    """The device login's order on one prompt-mode connection (ipc-control top, rules 2, 3, 6, 7 and 8): what
    the worker may send and when, what the worker owes the core's answers, and the attempt numbers. It
    returns problems and never decides the connection's state, which conn_order keeps."""

    def __init__(self) -> None:
        self.asked = False            # a hostkey_ask was seen
        self.ask_open = False         # ... and the core has not answered it
        self.prompted = False         # an auth_prompt (a banner too) was seen
        self.prompt: tuple[int, int] | None = None   # (prompt_id, count of strings) outstanding
        self.attempt: int | None = None
        self.answered = 0             # answers relayed in the current attempt
        self.owed_bad = False         # a bad command was sent: the terminal answer owed is error bad_request
        self.owed_disconnect = False  # an unknown value was met: the core owes disconnect
        self.terminal = False

    def worker(self, k: str, msg: dict, where: str) -> list[str]:
        """The worker's event before the terminal answer."""
        out: list[str] = []
        if k == "hostkey_ask":
            if self.asked:
                out.append(f"{where}: a second hostkey_ask: at most one per connect (rule 2)")
            if self.prompted:
                out.append(f"{where}: hostkey_ask after an auth_prompt: it comes before any auth_prompt (rule 2)")
            if self.prompt is not None or self.ask_open:
                out.append(f"{where}: hostkey_ask with a question outstanding (rule 2)")
            if msg.get("kind") not in HOSTKEY_KINDS:
                self.owed_disconnect = True
                return out
            self.asked = self.ask_open = True
        elif k == "auth_prompt":
            method = msg.get("method")
            if method not in PROMPT_METHODS:
                self.owed_disconnect = True
                return out
            if self.ask_open:
                out.append(f"{where}: auth_prompt while the hostkey_ask is unanswered (rule 2)")
            self.prompted = True
            if method == "banner":
                return out
            out += self._attempt(msg.get("attempt"), where)
            prompt_id = msg.get("prompt_id")
            if prompt_id is not None:
                if self.prompt is not None:
                    out.append(f"{where}: a second prompt with a prompt_id is outstanding: one at a time (rule 2)")
                count = 1 if method == "password" else len(msg.get("prompts") or [])
                self.prompt = (prompt_id, count)
        return out

    def _attempt(self, attempt, where: str) -> list[str]:
        """Rule 8: starts at 1; rises by one, and only after an answer was relayed in the attempt it ends."""
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            return []
        if self.attempt is None:
            problem = [] if attempt == 1 else [f"{where}: the first attempt is 1, this is {attempt} (rule 8)"]
        elif attempt == self.attempt or attempt == self.attempt + 1 and self.answered > 0:
            problem = []
        else:
            problem = [(f"{where}: attempt {attempt} after {self.attempt}: it rises by one, and only when the device "
                       f"refused an answer, never on a partial success or a round with no answer (rule 8)")]
        if self.attempt is None or attempt > self.attempt:
            self.answered = 0
        self.attempt = attempt
        return problem

    def core(self, k: str, msg: dict) -> None:
        """The core's hostkey_answer or auth_answer before the terminal answer: right, or one the worker
        answers error bad_request (rule 6)."""
        if k == "hostkey_answer":
            if self.ask_open and isinstance(msg.get("accept"), bool):
                self.ask_open = False
            else:
                self.owed_bad = True
            return
        answers = msg.get("answers")
        if (self.prompt is not None and msg.get("prompt_id") == self.prompt[0] and isinstance(answers, list)
                and len(answers) == self.prompt[1]):
            self.prompt = None
            self.answered += 1
        else:
            self.owed_bad = True


def conn_order(messages: list[tuple[str, dict]], runs: list | None = None, version: str | None = None) -> list[tuple[str, str]]:
    """Ordering rules for one connection's control socket.

    connect first; hostkey only between connect and connected; channels opened only once
    connected, each answered by channel_opened of the type the command asked for, or by error
    naming the session; resize and close_channel only on open sessions that are not closing, each
    close_channel answered by channel_closed (or by the connection's loss), and a channel_closed by
    core only ever such an answer; disconnect answered by connection_lost, no command after it, and a
    connection_lost of cause disconnect only ever its answer; nothing after connection_lost (rule
    "order"). Every
    connection_lost lists exactly the sessions open at that moment, closing ones included (rule
    "lost-set", lang-arch §6.4 step 3). Returns (rule, problem) pairs.

    From v0.2 (ipc-control.schema.json), per session: hello → hello_ack (or error unsupported_proto,
    no_such_session, no_data_peer, or unknown_cmd naming the session) → replay from last_committed_seq + 1 → replay_done through
    hello_ack's current_seq → live, or release for an ended session; release only for a session
    that has ended, and for a handshake crossed by the worker's own end — a channel_closed by:
    worker sent before the worker read its hello, which release accepts with or without the
    hello_ack, and one late hello_ack with ended: true after such a release is answered to; ping
    answered by pong with the same n; link_state once connected, and while the
    core is disconnecting (a worker may send it before reading the disconnect); no linger
    close while a session is open (that it lists none is its shape's rule). `runs` gives each
    message's core run (None without one). At a run boundary the previous run's in-flight state goes — a command in flight at the kill is not "never
    answered"; terminal barriers can still arrive on the restarted core's control connection (§3.1, §9 B): every session
    still sealing joins a carried-seal set and every session still closing joins a carried-close set (the orchestrator, 2026-11-15 21:0x, amended 21:5x), so a later run's
    `terminal_busy` hello refusal for it is accepted while in either set; `output_sealed` is accepted for a carried seal and `channel_closed` by: core
    for a carried close; an `error` `no_such_session` answers a carried seal; a `hello_ack` settles a carried drain only with proof
    (`output_seal` with `sealed: true` for a carried seal, `ended: true` for a carried close); toward a hello offering a
    version below 0.8, which gets `unsupported_proto` while the seal is carried, the legacy `hello_ack` with `ended: true`
    settles a carried seal; release settles both sets. A session whose below-0.8 hello was refused during a carried seal
    gets no further hello in that run (terminal-output-accounting §3.1): a running legacy core sets no retry timer, so
    its legacy handshake, replay and release come at its next start, which is the next run boundary; the restriction
    clears there. `output_sealed` and an `error` `terminal_busy`
    reach only a peer that stated 0.8 in a parsed hello (refused hellos included) or by sending seal_output on this
    connection; either statement applies to every session on the connection, and a replacement peer starts with
    no statement. Before either statement both events are flagged. A later hello offering less does not undo
    that statement; `terminal_busy` and a `hello_ack`'s `output_seal` are also judged by the hello they answer, so
    toward a hello offering below 0.8 both are flagged (terminal-output-accounting §3.1).
    The connection is reattached, not reconnected, so no connect is expected, and the
    worker's first event to the new core is link_state. The sessions an earlier run left open are
    carried unconfirmed until a hello_ack says what they are; a connection_lost may list them.

    A telnet connection (its connect names protocol telnet) carries one session: no host key, no
    channel_opened for a second session, and after the one session's end reaches this peer — its
    channel_closed, the error answering the first open_shell, or the core's release for it — the
    connection's next event is connection_lost cause single_session, with nothing emitted between.
    An error answering a later open_shell requires nothing after it.
    """
    runs = runs if runs is not None else [None] * len(messages)
    problems: list[tuple[str, str]] = []
    state = "start"
    pending: dict[str, str] = {}
    open_: set[str] = set()
    closing: set[str] = set()
    carried: set[str] = set()   # open when an earlier run ended; unconfirmed in this one
    ended: set[str] = set()     # ended sessions the worker retains until release
    crossed_released: set[str] = set()  # crossed handshakes the core released: one late ended ack is owed
    ever_closed: set[str] = set()  # every session the capture has ever shown closed
    current_run: set[str] = set() # sessions opened or helloed in the current run (S5 rule)
    hs: dict[str, dict] = {}    # session id -> the handshake so far: hello, ack, replay, done
    pings: list = []            # outstanding ping n, oldest first
    missed = 1                  # the heartbeat_missed limit currently in force
    telnet = False              # the connection is telnet (its connect names protocol telnet)
    telnet_first = None         # the session the connection's first open_shell opened
    await_single = False        # the one session's end has reached this peer: single_session is owed next
    # A hung worker misses heartbeat_missed pings before its supervisor kills it (session model
    # §11, T11), so a capture of one legitimately ends with up to that many unanswered pings. The
    # limit rides the connection's own connect, or a later timers; it is recorded with each ping as
    # it is sent, so a timers that raises it never excuses a ping sent under a smaller one. A v0.1
    # capture keeps today's rule of one (the last ping may still be in flight).
    run = runs[0] if runs else None
    link_first = False          # the worker's next event must be link_state (a new core peer)
    reattached = False          # this run reattached a connection an earlier core opened
    first_core = next((kind(m) for s, m in messages if s == "core"), None)
    if run is not None and first_core not in (None, "connect"):
        state, link_first, reattached = "connected", True, True  # the capture begins with a reattach
    # Core-restart step 22 (proto 0.4 and later, compared numerically): toward a worker whose version the core
    # speaks, the restarted core's first ping on a control socket goes before its first hello there. It is a
    # per-run rule: the check fires only when that run's own hello offers 0.4 or later, so a 0.3 restart may
    # hello first.
    pinged = False              # the core has pinged in this run; reset at each run boundary
    negotiated = version        # the connection's proto_version: the handshake's, once it answers
    seals: dict[str, dict] = {}
    sealing: dict[str, int] = {}
    carried_seals: set[str] = set()  # sessions whose seal was still draining when an earlier run ended (2026-11-15 21:0x)
    carried_closes: set[str] = set()  # sessions whose core close was still draining when an earlier run ended (2026-11-15 21:0x)
    refused_legacy: set[str] = set()  # sessions whose below-0.8 hello was refused during a carried seal in this run (§3.1)
    login: PromptLogin | None = None   # the device login's order, on a prompt-mode connection
    stale = False               # a device-login command came after the terminal answer: the worker ignores it
    peer_08 = False             # this control peer stated 0.8 by parsed hello (refused included) or seal_output
    for i, (sender, msg) in enumerate(messages, 1):
        k, where = kind(msg), f"message {i} ({kind(msg)})"
        if runs[i - 1] != run:
            _run_order(runs, i - 1, run, where, problems, lambda p: (ORDER, p))
            run = runs[i - 1]
            if state not in ("lost", "refused", "start"):
                # Per-run request tracking clears below, but §3.1 and §9 B permit terminal
                # barriers on the restarted control connection. Carry the worker's state: sessions
                # still sealing or closing join the carried-seal and carried-close sets (2026-11-15 21:0x).
                carried_seals |= set(sealing)
                carried_closes |= closing
            pending.clear()
            closing.clear()
            hs.clear()
            sealing.clear()
            crossed_released.clear()
            current_run.clear()
            refused_legacy.clear()  # §3.1: the no-further-hello rule is per run; the next start may hello again
            pings.clear()
            pinged = False
            negotiated = version
            peer_08 = False  # each replacement control peer starts with no stated version (§3.1)
            await_single = False  # the report is owed to the peer that saw the end, not to a new one
            if state not in ("lost", "refused", "start"):
                state, link_first, reattached = "connected", True, True
                carried |= open_
                open_ = set()
        if login is not None and sender == "core" and k in DEVICE_LOGIN_COMMANDS and state in ("refused", "connected"):
            stale = True  # after the terminal answer the worker ignores it, logs it and sends nothing (rule 6)
            continue
        if stale and sender == "worker" and k == "error" and msg.get("code") == "bad_request":
            problems.append((ORDER, (f"{where}: the worker answered a device-login command sent after its terminal "
                                    "answer: it ignores them and sends nothing (rule 6)")))
            stale = False
            continue
        if state in ("lost", "refused"):
            problems.append((ORDER, f"{where}: after the connection ended"))
            continue
        if sender == "worker":
            stale = False
        sid = msg.get("session_id") if isinstance(msg, dict) else None
        known = open_ | carried
        # The core's own terminal vocabulary: once the core has stated 0.8 on this connection it speaks 0.8
        # there, whatever a later hello offers; before that the connection's version decides. The worker's
        # output_sealed and terminal_busy follow the peer's statement (below), and terminal_busy and a hello_ack's
        # output_seal also the version of the hello they answer (§3.1).
        timers = msg.get("timers")
        core_terminal = (k in ("seal_output", "release_accounted")
                         or isinstance(timers, dict) and "output_drain_timeout" in timers)
        if (core_terminal and not peer_08 and negotiated is not None
                and not _version_at_least(negotiated, "0.8")):
            problems.append((ORDER, f"{where}: terminal vocabulary sent to pre-0.8 peer"))
        if sender == "core" and state == "disconnecting":
            problems.append((ORDER, f"{where}: a command after disconnect"))
            continue
        if sender == "core" and login is not None and state == "connecting" and k != "connect":
            if k not in BEFORE_TERMINAL:
                problems.append((ORDER, (f"{where}: before the terminal answer the core sends only hostkey_answer, "
                                        "auth_answer, disconnect and ping on a prompt-mode connection (rule 2)")))
                continue
            if k in DEVICE_LOGIN_COMMANDS:
                if login.owed_disconnect:
                    problems.append((ORDER, (f"{where}: the core met a value it does not know and must answer "
                                            "disconnect, not carry on (rule 7)")))
                login.core(k, msg)
                continue
        if sender == "core":
            if k == "connect":
                if state != "start":
                    problems.append((ORDER, f"{where}: connect on a connection that already started"))
                state = "connecting"
                missed = _heartbeat_missed(msg, missed)
                telnet = msg.get("protocol") == "telnet"
                auth = msg.get("auth")
                login = PromptLogin() if isinstance(auth, dict) and auth.get("kind") == "prompt" else None
                stale = False
            elif state == "start":
                problems.append((ORDER, f"{where}: the first command on a connection must be connect"))
            elif k in OPENS:
                if state != "connected":
                    problems.append((ORDER, f"{where}: a channel opened before connected"))
                if sid in known or sid in pending:
                    problems.append((ORDER, f"{where}: session {sid} is already open"))
                # The connection's one session is named by its first open command, not only by the
                # channel_opened that answers it: a first open the target refuses is answered by an
                # error (never channel_opened), and the connection still owes single_session once its
                # end reaches this peer (the error below sets await_single for it).
                if telnet and telnet_first is None:
                    telnet_first = sid
                pending[sid] = OPENS[k](msg)
                current_run.add(sid)
            elif k in ("resize", "close_channel"):
                if sid not in known:
                    problems.append((ORDER, f"{where}: session {sid} is not open"))
                elif sid in closing:
                    problems.append((ORDER, f"{where}: session {sid} is already closing"))
                elif k == "close_channel":
                    h = hs.get(sid)
                    # A 0.8 close abandons only the paused handshake Flow B creates: hello_ack seen,
                    # no replay yet. A handshake still waiting for its hello_ack, or mid-replay, stays
                    # in place so a crossing hello_ack or replay_done still matches it.
                    if _version_at_least(negotiated, "0.8") and h is not None and "ack" in h and "replay" not in h:
                        hs.pop(sid, None)
                    closing.add(sid)
            elif k == "disconnect":
                # Before a prompt-mode connect's terminal answer it ends the attempt (rule 3).
                if state != "connected" and not (login is not None and state == "connecting"):
                    problems.append((ORDER, f"{where}: disconnect before connected"))
                state = "disconnecting"
            elif k == "hello":
                if sid in refused_legacy:
                    problems.append((REATTACH, (f"{where}: hello for session {sid} after its below-0.8 hello was "
                                                "refused during a carried seal: a running legacy core sends no further "
                                                "hello for that session until its next start (§3.1)")))
                if reattached and not pinged and not _below_04(msg.get("proto_version")):
                    problems.append((REATTACH, f"{where}: hello before the restarted core's first ping"))
                if state != "connected":
                    problems.append((REATTACH, f"{where}: hello before the connection is up"))
                if sid in pending:
                    problems.append((REATTACH, f"{where}: hello for session {sid} before its channel_opened"))
                elif sid not in known | ended and not reattached:
                    problems.append((REATTACH, f"{where}: hello for session {sid}, which this connection never opened"))
                if sid in hs:
                    problems.append((REATTACH, f"{where}: hello for session {sid} while its handshake is under way"))
                elif sid not in known | ended:
                    carried.add(sid)  # a session a restarted core rediscovered from the manifest
                current_run.add(sid)
                hs[sid] = {"hello": msg}
                negotiated = msg.get("proto_version")
                if _version_at_least(msg.get("proto_version"), "0.8"):
                    # The peer stated 0.8 in a hello the worker parsed; refused hellos included, so a 0.8 core's
                    # barrier survives its own terminal_busy refusal (§3.1).
                    peer_08 = True
            elif k == "replay":
                h = hs.get(sid)
                if not h or "ack" not in h or "replay" in h:
                    problems.append((REATTACH, f"{where}: replay for session {sid} before its hello_ack"))
                else:
                    h["replay"] = msg
                    want = h["hello"].get("last_committed_seq", 0) + 1
                    if msg.get("from_seq") != want:
                        problems.append((REATTACH, (f"{where}: replay from {msg.get('from_seq')}, but the hello's "
                                                f"last_committed_seq was {want - 1}: a replay starts at the next seq")))
            elif k == "live":
                h = hs.get(sid)
                if not h or "done" not in h:
                    problems.append((REATTACH, (f"{where}: live for session {sid} before replay_done — input authority "
                                            "before the replay is complete (immortality §6.3)")))
                elif h["ack"].get("ended"):
                    problems.append((REATTACH, f"{where}: live for session {sid}, which has ended: release takes live's place"))
                else:
                    del hs[sid]
            elif k == "seal_output":
                peer_08 = True  # sending seal_output states 0.8 even without a hello (§3.1)
                if sid not in ended:
                    problems.append((REATTACH, f"{where}: sealing a session not known ended"))
                else:
                    hs.pop(sid, None)  # seal explicitly abandons the old paused peer
                    sealing[sid] = sealing.get(sid, 0) + 1
            elif k == "release_accounted":
                h = hs.get(sid)
                if sid in ended:
                    ack = h.get("ack", {}) if h else {}
                    if sid not in seals or not h or "done" not in h:
                        problems.append((REATTACH, f"{where}: accounted release before sealed ended replay"))
                    elif ack.get("output_seal", {}).get("sealed") is not True:
                        # §3.3, §9A step 4-5: disposal needs the seal summary on the replay's hello_ack;
                        # without it the snapshot check never ran and there is no final proof to release on.
                        problems.append((REATTACH, (f"{where}: accounted release for session {sid} whose replay "
                                                    "hello_ack carried no sealed summary")))
                    elif seals[sid].get("final_last_seq") != ack.get("current_seq"):
                        problems.append((REATTACH, (f"{where}: accounted release with seal final_last_seq "
                                                    f"{seals[sid].get('final_last_seq')}, but the replay snapshot "
                                                    f"said current_seq {ack.get('current_seq')}")))
                    else:
                        hs.pop(sid, None)
                        ended.discard(sid)
                        seals.pop(sid, None)
                        # Release clears both carried drain sets (2026-11-15 21:0x).
                        carried_seals.discard(sid)
                        carried_closes.discard(sid)
                        if telnet and sid == telnet_first:
                            await_single = True  # after release of the one session, single_session is owed next
                elif sid in known or sid in sealing:
                    problems.append((REATTACH, f"{where}: accounted release for unsealed/live session"))
                # An absent duplicate succeeds silently; durable audit assertions are provider-graded.
            elif k == "release":
                h = hs.get(sid)
                if h is not None:
                    # Accept release once the ended replay is complete, and for a handshake crossed by
                    # the worker's own end: the worker may send its end before it reads the hello, so
                    # release is accepted with or without the hello_ack that answers that hello.
                    if ("done" in h and h["ack"].get("ended")) or h.get("crossed"):
                        del hs[sid]
                        ended.discard(sid)
                        carried_seals.discard(sid)
                        carried_closes.discard(sid)
                        if h.get("crossed"):
                            crossed_released.add(sid)  # the hello it crossed is owed exactly one late answer
                        if telnet and sid == telnet_first:
                            await_single = True  # after release of the one session, single_session is owed next
                    else:
                        problems.append((REATTACH, f"{where}: release for session {sid} before its ended replay is complete"))
                elif sid in ended:
                    ended.discard(sid)
                    carried_seals.discard(sid)
                    carried_closes.discard(sid)
                    if telnet and sid == telnet_first:
                        await_single = True
                else:
                    problems.append((REATTACH, f"{where}: release for session {sid}, which has not ended"))
            elif k == "ping":
                pinged = True  # core-restart step 22: this run's first core ping, before its hello
                # Record the limit in force when this ping was sent: a later timers must not excuse
                # pings that were already in flight under a smaller heartbeat_missed.
                pings.append((msg.get("n"), missed))
            elif k == "timers":
                missed = _heartbeat_missed(msg, missed)
            continue
        if link_first and state == "connected":
            if k != "link_state":
                problems.append((REATTACH, (f"{where}: the first event to a new core of a connected connection is "
                                        "link_state, so the core has link_timeout's countdown at once")))
            link_first = False
        if await_single and state not in ("lost", "refused"):
            # The worker sends its single_session report before it reads another command, and emits
            # nothing between the one session's end and that report: its link monitor is stopped.
            if k != "connection_lost":
                problems.append((ORDER, (f"{where}: the connection's one session has ended; its single_session report "
                                          f"is owed next, and the worker emits nothing between")))
            elif msg.get("cause") != "single_session":
                problems.append((ORDER, (f"{where}: after the one session's end the connection's next event is "
                                          "connection_lost cause single_session")))
            if k == "connection_lost":
                await_single = False
        if k in DEVICE_LOGIN_EVENTS:
            if login is None:
                problems.append((ORDER, (f"{where}: a device-login event on a connection not in prompt mode: a worker "
                                        "fault the core logs and ignores (rule 7)")))
            elif state == "disconnecting":
                pass  # it crossed the core's disconnect (rule 3)
            elif state != "connecting":
                problems.append((ORDER, f"{where}: no {k} after the terminal answer (rule 2)"))
            else:
                problems += [(ORDER, p) for p in login.worker(k, msg, where)]
            continue
        if login is not None and state == "disconnecting" and k in ("connected", "auth_failed", "error"):
            continue  # a terminal answer that crossed the core's disconnect: the core drops it (rule 3)
        if k == "hostkey":
            if login is not None:
                problems.append((ORDER, (f"{where}: never a hostkey event on a prompt-mode connect: the worker asks "
                                        "with hostkey_ask (rule 2)")))
            elif telnet:
                problems.append((ORDER, f"{where}: a host key on a telnet connection: the base protocol has no host key (RFC 854)"))
            elif state != "connecting":
                problems.append((ORDER, f"{where}: a host key is reported between connect and connected, not here"))
        elif k == "connected":
            if state != "connecting":
                problems.append((ORDER, f"{where}: connected without a pending connect"))
            elif login is not None:
                if login.ask_open or login.prompt is not None:
                    problems.append((ORDER, (f"{where}: connected with a question outstanding: the device cannot "
                                            "accept what was never answered (rule 2)")))
                if login.owed_bad:
                    problems.append((ORDER, (f"{where}: a bad device-login command was sent: the terminal answer owed "
                                            "is error bad_request (rule 6)")))
                login.terminal = True
            state = "connected"
        elif k == "auth_failed":
            if state != "connecting":
                problems.append((ORDER, f"{where}: auth_failed without a pending connect"))
            elif login is not None:
                if login.owed_bad:
                    problems.append((ORDER, (f"{where}: a bad device-login command was sent: the terminal answer owed "
                                            "is error bad_request (rule 6)")))
                login.terminal = True
            if login is None and "code" in msg:
                problems.append((ORDER, (f"{where}: auth_failed code is sent only on the answer to a prompt-mode "
                                        "connect")))
            state = "refused"
        elif k == "channel_opened":
            if sid not in pending:
                problems.append((ORDER, f"{where}: no open command asked for session {sid}"))
            else:
                wanted = pending.pop(sid)
                if msg.get("session_type") != wanted:
                    problems.append((ORDER, f"{where}: session_type {msg.get('session_type')!r}, the command opened {wanted!r}"))
                if telnet and telnet_first is None:
                    telnet_first = sid  # the connection's one session
                elif telnet and sid != telnet_first:
                    problems.append((ORDER, (f"{where}: channel_opened for a second session on a telnet connection, "
                                             f"which carries one session ({telnet_first})")))
                open_.add(sid)
        elif k == "channel_closed":
            by = msg.get("by")
            present_end = [f for f in END_FIELDS if f in msg]
            # (2) Presence rule: by worker carries all six end fields; by core/target carry none
            if by == "worker":
                if len(present_end) != len(END_FIELDS):
                    problems.append((ORDER, f"{where}: by worker without its end"))
                elif negotiated is not None and _below_04(negotiated):
                    # by: worker exists only from proto_version 0.4; a connection below it cannot carry it,
                    # so the crossing-hello release exception does not apply to it.
                    problems.append((ORDER, (f"{where}: by worker on a connection at proto_version {negotiated}, "
                                             "which is below 0.4")))
                else:
                    # S5 rule: by: worker only for sessions this control peer opened or helloed in this run
                    if sid not in current_run:
                        problems.append((ORDER, f"{where}: by worker for session {sid}, which was not opened or helloed in this run"))
                    # A by: worker for a session whose hello the worker already answered: the end
                    # waits for live, not for a close the worker decides after its own ack.
                    if sid in hs and "ack" in hs[sid]:
                        problems.append((REATTACH, f"{where}: by worker during a handshake the worker already answered: the end waits for live"))
                    elif sid in hs:
                        # The event crossed a hello the worker had not read yet: its handshake is crossed,
                        # so release is accepted for it, and the ack the worker sends when it reads the
                        # hello can arrive after the release.
                        hs[sid]["crossed"] = True
            elif by in ("core", "target") and present_end:
                problems.append((ORDER, f"{where}: by {by} with an end"))
            # Session must be open, unless it's rediscovered in a reattached run
            if sid not in open_ | carried:
                # (4) In a reattached run, by: target can name a rediscovered session the capture never showed closed
                if reattached and by == "target" and sid not in ever_closed:
                    carried.add(sid)
                else:
                    problems.append((ORDER, f"{where}: session {sid} was not open"))
            # (1) by: worker needs no pending close_channel
            if by == "core" and sid not in closing and sid not in carried_closes:
                problems.append((ORDER, f"{where}: by core, but the core sent no close_channel for session {sid}"))
            if sid in open_ | carried:
                ended.add(sid)  # retained until the core releases it
            open_.discard(sid)
            carried.discard(sid)
            ever_closed.add(sid)  # the capture has now shown this session closed
            # (3) by: worker while close_channel is pending clears it (the two crossed)
            closing.discard(sid)
            carried_closes.discard(sid)  # channel closed clears carried close (2026-11-15 21:0x)
            if telnet and sid == telnet_first:
                await_single = True  # the connection's one session ended: single_session is owed next
        elif k == "error" and login is not None and state == "connecting":
            # Before the terminal answer every error is the terminal answer (rule 6).
            if login.owed_bad and msg.get("code") != "bad_request":
                problems.append((ORDER, (f"{where}: a bad device-login command was sent: the terminal answer owed "
                                        "is error bad_request (rule 6)")))
            login.terminal = True
            state = "refused"
        elif k == "error":
            code = msg.get("code")
            if code == "hostkey_refused" and login is None:
                problems.append((ORDER, f"{where}: hostkey_refused answers only a prompt-mode connect"))
            if sid in sealing and not (sid in hs and "ack" not in hs[sid]):
                # The error answers the pending seal_output directly (§3 line 70 lists only bad_request,
                # for a live/racing target, and no_such_session, for an absent session, as its refusals;
                # any other code answering the seal is flagged). A refusal ends the seal attempt, so it is
                # not left pending at the capture's end. A terminal_busy that answers a fresh hello sent
                # during sealing has a pending hello, so it is routed to the handshake branch below.
                if code == "no_such_session":
                    sealing.pop(sid, None)
                    carried_seals.discard(sid)
                    ended.discard(sid)
                    seals.pop(sid, None)
                    hs.pop(sid, None)
                elif code == "bad_request":
                    sealing.pop(sid, None)
                else:
                    problems.append((ORDER, (f"{where}: {code} answers seal_output for session {sid}, which §3 "
                                             "line 70 refuses only with bad_request and no_such_session")))
            elif sid in carried_seals and code == "no_such_session" and not (sid in hs and "ack" not in hs[sid]):
                # A refusal can answer a carried seal (Ruling 2, the orchestrator, 2026-11-15 21:5x): an error
                # no_such_session for a session in the carried-seal set, in a later run, is accepted as the answer
                # to that carried seal_output (the session is gone), removes it from the set and is not reported
                # as "answers no hello".
                carried_seals.discard(sid)
                ended.discard(sid)
                seals.pop(sid, None)
                hs.pop(sid, None)
            elif code == "unknown_cmd":
                # The answer to a command this worker does not know; the connection carries on. Naming a
                # session whose hello is pending, it answers that hello: the peer does not speak the
                # handshake, so the session is unreachable / protocol.
                if sid in hs and "ack" not in hs[sid]:
                    del hs[sid]
            elif code in HANDSHAKE_ERRORS or code == "terminal_busy":
                h = hs.get(sid)
                if code == "terminal_busy":
                    offered = h["hello"].get("proto_version") if h is not None and "ack" not in h else None
                    if offered is not None and not _version_at_least(offered, "0.8"):
                        # §3.1: toward a hello offering below 0.8 the worker answers unsupported_proto, even when
                        # the peer stated 0.8 in an earlier hello.
                        problems.append((ORDER, (f"{where}: terminal vocabulary sent to pre-0.8 peer: terminal_busy "
                                                 f"answers a hello offering {offered!r}, which gets unsupported_proto")))
                    elif not peer_08:
                        problems.append((ORDER, (f"{where}: terminal_busy sent to a peer that has not stated 0.8 in "
                                                 "a parsed hello or by seal_output on this connection")))
                if h is None or "ack" in h:
                    problems.append((REATTACH, f"{where}: {code} answers no hello for session {sid}"))
                elif (code == "terminal_busy" and sid not in sealing and sid not in closing
                      and sid not in carried_seals and sid not in carried_closes):
                    # §3.1: a hello is refused terminal_busy only while the session is sealing or its
                    # already-requested core end is in flight; for any other session the refusal is
                    # an order fault, not a handshake answer the checker accepts. A session whose
                    # seal or close was still draining when an earlier run ended is in flight for
                    # this peer too, so its terminal_busy is accepted while in either set (2026-11-15 21:0x).
                    problems.append((ORDER, (f"{where}: terminal_busy for session {sid}: a hello is "
                                             "refused terminal_busy only while the session is sealing "
                                             "or its already-requested core end is in flight")))
                else:
                    del hs[sid]
                    if (code == "unsupported_proto" and sid in carried_seals
                            and not _version_at_least(h["hello"].get("proto_version"), "0.8")):
                        # §3.1: the refused hello is this run's last for that session; a running legacy core sets no
                        # retry timer, so its legacy handshake comes at its next start, the next run boundary.
                        refused_legacy.add(sid)
                    if code == "no_such_session":
                        open_.discard(sid)
                        carried.discard(sid)
                        ended.discard(sid)
                        carried_seals.discard(sid)
                        carried_closes.discard(sid)
            elif sid is not None:
                # An error naming a session answers that session's open_shell if one is still pending.
                # It owes the connection's single_session only when it answers the connection's first
                # open_shell (a first open the worker refused): an error on an already-open session
                # ends nothing, so the connection is not closing.
                first_open_failed = pending.pop(sid, None) is not None and telnet and sid == telnet_first
                if first_open_failed:
                    await_single = True
            elif state == "connecting":
                state = "refused"  # the connection itself was refused (a changed host key, say)
        elif k == "output_sealed":
            if not peer_08:
                problems.append((ORDER, (f"{where}: output_sealed sent to a peer that has not stated 0.8 in a parsed "
                                         "hello or by seal_output on this connection")))
            seal = msg.get("output_seal", {})
            if (sid not in sealing and sid not in carried_seals) or seal.get("sealed") is not True:
                problems.append((REATTACH, f"{where}: no pending seal or no successful sealed result"))
            elif sid in seals and seals[sid] != seal:
                problems.append((REATTACH, f"{where}: terminal seal changed"))
            else:
                seals[sid] = seal
                carried_seals.discard(sid)
            if sid in sealing:
                sealing[sid] -= 1
                if sealing[sid] <= 0:
                    del sealing[sid]
        elif k == "hello_ack":
            if sid in sealing:
                # §3.1: a hello for a session that is sealing is refused terminal_busy, and its attempt gets
                # no ack. An ack here answers no handshake this checker accepts, so the replay after it is
                # reported as answering no hello_ack too.
                problems.append((ORDER, (f"{where}: hello_ack for session {sid} while it is sealing: the attempt "
                                         "is refused terminal_busy and answered with no ack")))
                continue
            seal = msg.get("output_seal", {})
            answered = hs.get(sid)
            offered = answered["hello"].get("proto_version") if answered else negotiated
            if "output_seal" in msg and offered is not None and not _version_at_least(offered, "0.8"):
                # The summary is 0.8 vocabulary, judged by the hello this ack answers: the legacy hello_ack has none
                # (terminal-output-accounting §3.1).
                problems.append((ORDER, (f"{where}: terminal vocabulary sent to pre-0.8 peer: a hello_ack answering "
                                         f"a hello offering {offered!r} carries output_seal")))
            if sid in carried_seals:
                if not _version_at_least(offered, "0.8"):
                    # terminal-output-accounting §3.1: toward a hello offering a version
                    # below 0.8 the worker answers unsupported_proto while the seal is carried, and the
                    # ordinary legacy hello_ack, which has no output_seal, only once it has sealed. That ack
                    # is the proof such a peer gets; a sealed session is ended, so it says ended: true.
                    if not msg.get("ended"):
                        problems.append((ORDER, (f"{where}: hello_ack for session {sid} offered below 0.8 while its "
                                                 "seal is carried says ended: false; the worker answers "
                                                 "unsupported_proto until it has sealed, and a sealed session "
                                                 "has ended")))
                        continue
                # Ruling 1 (the orchestrator, 2026-11-15 21:5x): for a session in the carried-seal set,
                # a hello_ack is accepted only when it carries output_seal with sealed: true (the seal
                # finished while no core was listening); it then leaves the set. A hello_ack without
                # that proof is a fault (the worker should have answered terminal_busy).
                elif not (isinstance(seal, dict) and seal.get("sealed") is True):
                    problems.append((ORDER, (f"{where}: hello_ack for session {sid} while its seal is draining: "
                                             "a hello_ack without a sealed summary is a fault; the worker "
                                             "should have answered terminal_busy")))
                    continue
                carried_seals.discard(sid)
            if sid in carried_closes:
                # Ruling 1 (the orchestrator, 2026-11-15 21:5x): for a session in the carried-close set,
                # a hello_ack is accepted only when it says ended: true; it then leaves the set. A hello_ack
                # without that proof is a fault (the worker should have answered terminal_busy).
                if not msg.get("ended"):
                    problems.append((ORDER, (f"{where}: hello_ack for session {sid} while its core end is in flight: "
                                             "a hello_ack without ended: true is a fault; the worker "
                                             "should have answered terminal_busy")))
                    continue
                carried_closes.discard(sid)
            if seal.get("sealed") is True:
                if not msg.get("ended"):
                    problems.append((REATTACH, f"{where}: a sealed session cannot be live"))
                if sid in seals and seals[sid] != seal:
                    problems.append((REATTACH, f"{where}: hello changed the terminal seal"))
                elif seal.get("final_last_seq") != msg.get("current_seq"):
                    problems.append((REATTACH, f"{where}: seal disagrees with replay snapshot"))
                else:
                    seals[sid] = seal
            h = hs.get(sid)
            if h is None and sid in crossed_released:
                # The worker read the hello its own end crossed, after the core released the session:
                # exactly one answer with ended: true is accepted. A second, or one saying the session
                # has not ended, is a problem.
                if msg.get("ended"):
                    crossed_released.discard(sid)
                else:
                    problems.append((REATTACH, f"{where}: ended: false answers a hello for session {sid} the core already released"))
            elif h is None or "ack" in h:
                problems.append((REATTACH, f"{where}: answers no hello for session {sid}"))
            else:
                h["ack"] = msg
                negotiated = msg.get("proto_version")
                if msg.get("proto_version") != h["hello"].get("proto_version"):
                    problems.append((REATTACH, (f"{where}: speaks {msg.get('proto_version')!r}, but the hello offered "
                                            f"{h['hello'].get('proto_version')!r}: a worker that cannot speak it answers "
                                            "unsupported_proto")))
                current = msg.get("current_seq", 0)
                if msg.get("first_buffered_seq", 1) > current + 1:
                    problems.append((REATTACH, (f"{where}: first_buffered_seq {msg.get('first_buffered_seq')} is past "
                                            f"current_seq {current} + 1")))
                if msg.get("ended"):
                    ended.add(sid)
                    open_.discard(sid)
                    carried.discard(sid)
                elif sid in ended:
                    problems.append((REATTACH, f"{where}: session {sid} has ended, but the hello_ack says it has not"))
                else:
                    open_.add(sid)
                    carried.discard(sid)
        elif k == "replay_done":
            h = hs.get(sid)
            if not h or "replay" not in h or "done" in h:
                problems.append((REATTACH, f"{where}: replay_done for session {sid} answers no replay"))
            else:
                h["done"] = msg
                current = h["ack"].get("current_seq")
                if msg.get("through_seq") != current:
                    problems.append((REATTACH, (f"{where}: through_seq {msg.get('through_seq')}, but the hello_ack said "
                                            f"current_seq {current}: the worker read the channel mid-handshake")))
        elif k == "pong":
            n = msg.get("n")
            answered = next((i for i, p in enumerate(pings) if p[0] == n), None)
            if answered is not None:
                skipped, pings = pings[:answered], pings[answered + 1:]
                problems += [(HEARTBEAT, f"{where}: ping {m} was never answered, and a later one was")
                             for m, _ in skipped]
            else:
                problems.append((HEARTBEAT, f"{where}: pong {n} answers no ping"))
        elif k == "link_state":
            # A worker may send link_state (link-liveness step 10) before it reads the core's
            # disconnect, so a link_state while disconnecting is not an error. States after the
            # connection ended never reach here: the guard above reports them.
            if state not in ("connected", "disconnecting"):
                problems.append((ORDER, f"{where}: link_state before connected: there is no link to report yet"))
        elif k == "input_refused":
            if sid not in open_ | carried | ended | set(hs):
                problems.append((ORDER, f"{where}: input_refused for session {sid}, which this connection does not hold"))
        elif k == "connection_lost":
            if msg.get("cause") == "disconnect" and state != "disconnecting":
                problems.append((ORDER, f"{where}: cause disconnect, but the core sent no disconnect"))
            listed = set(msg.get("session_ids", []))
            if msg.get("cause") == "linger" and open_:
                problems.append((LINGER, (f"{where}: a linger close while sessions {sorted(open_)} are open: "
                                         "connection_linger arms only once no session is left")))
            elif not (open_ <= listed <= open_ | carried):
                problems.append((LOST_SET, f"{where}: lists {sorted(listed)}, but the open sessions are {sorted(open_)}"))
            state = "lost"
            open_.clear()
            carried.clear()
            ended.clear()  # the close releases what the worker retained
            pending.clear()
            closing.clear()
            hs.clear()
            sealing.clear()
            crossed_released.clear()
            pings.clear()
    if login is not None and login.owed_disconnect and state == "connecting":
        problems.append((ORDER, "the core met a value it does not know and never disconnected (rule 7)"))
    problems += [(ORDER, f"the open for session {sid} was never answered") for sid in pending]
    problems += [(ORDER, f"close_channel for session {sid} was never answered by channel_closed")
                 for sid in sorted(closing)]
    if await_single and state not in ("lost", "refused"):
        problems.append((ORDER, "the connection's single_session report was never sent"))
    if state == "disconnecting":
        problems.append((ORDER, "disconnect was never answered by connection_lost"))
    for sid, h in sorted(hs.items()):
        if "ack" not in h:
            problems.append((REATTACH, f"the hello for session {sid} was never answered"))
        elif "replay" in h and "done" not in h:
            problems.append((REATTACH, f"the replay for session {sid} was never answered by replay_done"))
    # A seal_output ends the paused handshake and owes its answer, as a hello or replay does: a capture
    # that ends with the seal pending is an ORDER problem (§3, §9A).
    problems += [(ORDER, f"the seal_output for session {sid} was never answered") for sid in sorted(sealing)]
    # Counting back from the newest unanswered ping, the j-th newest is excused only if the
    # heartbeat_missed in force when it was sent is at least j; the first one that is not ends the
    # excused group, and every older unanswered ping fails.
    allowed = 0
    for k in range(1, len(pings) + 1):
        if pings[-k][1] >= k:
            allowed = k
        else:
            break
    problems += [(HEARTBEAT, f"ping {n} was never answered") for n, _ in pings[:len(pings) - allowed]]
    return problems


def supervisor_order(messages: list[tuple[str, dict]], runs: list | None = None) -> list[str]:
    """Every request gets exactly one reply: create_worker a worker_created or an error naming its
    conn_id; list_workers workers; ping a pong with its n; kill_worker a worker_killed or a
    kill_failed naming its worker_id; a request this tag does not define an error unknown_cmd, and a
    malformed one bad_request — the two that may lack a conn_id. So supervisor_heartbeat's "request
    unanswered" rule never fires on a supervisor that is merely refusing. The last ping of a capture
    may still be in flight; every other unanswered request is a problem. At a run boundary the
    previous run's pending requests go: in flight at the kill, they are not "never answered".

    Placement (session model §4): a worker keeps the placement_key it was spawned with for its
    life, so a worker_created that puts a connection into a worker known to carry another key, or
    a workers reply giving a known worker another key, is a problem. n_max is not checked here: a
    connection leaves its worker on its own control socket, which a supervisor capture never shows.
    """
    runs = runs if runs is not None else [None] * len(messages)
    problems: list[str] = []
    pending: list[tuple[str, Any, dict]] = []  # (kind, key, request) awaiting an answer, in order
    keys: dict[Any, Any] = {}                  # worker_id -> the placement_key it was spawned with
    run = runs[0] if runs else None

    def take(kind_: str, key: Any) -> tuple[str, Any, dict] | None:
        found = next((p for p in pending if p[0] == kind_ and p[1] == key), None)
        if found is not None:
            pending.remove(found)
        return found

    for i, (sender, msg) in enumerate(messages, 1):
        k, where = kind(msg), f"message {i} ({kind(msg)})"
        if runs[i - 1] != run:
            _run_order(runs, i - 1, run, where, problems, lambda p: p)
            run = runs[i - 1]
            pending.clear()
        if sender == "core":
            key = {"create_worker": "conn_id", "kill_worker": "worker_id", "ping": "n"}.get(k)
            pending.append((k, msg.get(key) if key and isinstance(msg, dict) else None, msg))
            continue
        reason = msg.get("reason") if isinstance(msg, dict) else None
        if k == "worker_created" or (k == "error" and reason not in ("bad_request", "unknown_cmd")):
            asked = take("create_worker", msg.get("conn_id"))
            if asked is None:
                problems.append(f"{where}: answers no create_worker for {msg.get('conn_id')}")
                continue
            if k == "worker_created":
                worker, key = msg.get("worker_id"), asked[2].get("placement_key")
                held = keys.setdefault(worker, key)
                if held != key:
                    problems.append(f"{where}: puts {asked[1]} (placement_key {key!r}) into worker {worker}, "
                                    f"which carries placement_key {held!r}: a worker never holds two keys")
        elif k == "error":
            conn = msg.get("conn_id")
            asked = next((p for p in pending if conn is not None and isinstance(p[2], dict)
                          and p[2].get("conn_id") == conn), None)
            if asked is None:  # the oldest line with no reply of its own: a kind this tag lacks, or a request missing its key
                asked = next((p for p in pending if p[1] is None and p[0] != "list_workers"), None)
            if asked is None:
                problems.append(f"{where}: {reason} answers no request")
                continue
            pending.remove(asked)
            if reason == "unknown_cmd" and asked[0] in SUP_REQUESTS:
                problems.append(f"{where}: unknown_cmd answers {asked[0]}, a command this tag defines")
        elif k == "workers":
            if take("list_workers", None) is None:
                problems.append(f"{where}: answers no list_workers")
            for item in msg.get("workers", []) if isinstance(msg.get("workers"), list) else []:
                if not isinstance(item, dict):
                    continue
                held = keys.setdefault(item.get("worker_id"), item.get("placement_key"))
                if held != item.get("placement_key"):
                    problems.append(f"{where}: lists worker {item.get('worker_id')} with placement_key "
                                    f"{item.get('placement_key')!r}, but it carries {held!r}: a worker's key "
                                    "is fixed for its life")
        elif k == "pong":
            earlier = [p for p in pending if p[0] == "ping"]
            asked = take("ping", msg.get("n"))
            if asked is None:
                problems.append(f"{where}: pong {msg.get('n')} answers no ping")
            else:
                for p in earlier[:earlier.index(asked)]:
                    pending.remove(p)
                    problems.append(f"{where}: ping {p[1]} was never answered, and a later one was")
        elif k in ("worker_killed", "kill_failed"):
            if take("kill_worker", msg.get("worker_id")) is None:
                problems.append(f"{where}: answers no kill_worker for {msg.get('worker_id')}")
    last_ping = next((p for p in reversed(pending) if p[0] == "ping"), None)
    for p in pending:
        if p is last_ping:
            continue  # the heartbeat in flight when the capture was taken
        if p[0] == "create_worker":
            problems.append(f"create_worker for {p[1]} was never answered")
        elif p[0] == "list_workers":
            problems.append("list_workers was never answered")
        else:
            problems.append(f"{p[0]} {p[1] if p[1] is not None else ''} was never answered".replace("  ", " "))
    return problems


VT_REQUESTS = {"prefix": "prefix_ready", "snapshot": "snapshot_ready", "restore": "restored"}
VT_ANSWERS = {v: k for k, v in VT_REQUESTS.items()}
VT_SESSION_CMDS = frozenset({"resize", "prefix", "snapshot", "restore", "close"})


def vt_order(messages: list[tuple[str, dict]], runs: list | None = None) -> list[tuple[str, str]]:
    """Ordering rules for one core ↔ VT worker socket (`vt-control`), as vt-ipc states them.

    `hello` is the first message on the socket (hello's description): no message either way comes
    before the core's hello, a hello_ack answers one, and a second hello is a returning core (D19).
    A request is answered by its reply carrying the same `request_id` (prefix → prefix_ready,
    snapshot → snapshot_ready, restore → restored; request_id's description), and each ping by a
    pong (ping's description). Per session, a grid's messages come only after its session's `open`
    (open's description: the grid exists from then), and nothing for a session comes after its
    `close` (close's description: the grid is dropped). A `close` or `restore` names a session too,
    so those are checked the same way. An `error` carrying a request_id answers that request; one
    without it (unknown_cmd, bad_request, unsupported_proto) answers no request and is not chased.
    Returns (rule, problem) pairs. `runs` gives each message's core run; at a run boundary the
    previous run's in-flight requests go (a new core does not get the old core's answer) and the
    handshake starts over (the returning core's first message is its hello), while the grids stay —
    D19 keeps every grid when the core's socket closes.
    """
    runs = runs if runs is not None else [None] * len(messages)
    problems: list[tuple[str, str]] = []
    hellod = False
    open_: set[str] = set()
    closed: set[str] = set()
    pending: dict[str, str] = {}   # request_id -> the command awaiting its reply
    pings = 0                      # outstanding pings, oldest first (pong carries no id)
    run = runs[0] if runs else None

    for i, (sender, msg) in enumerate(messages, 1):
        k, where = kind(msg), f"message {i} ({kind(msg)})"
        if runs[i - 1] != run:
            _run_order(runs, i - 1, run, where, problems, lambda p: (ORDER, p))
            run = runs[i - 1]
            pending.clear()
            pings = 0
            hellod = False  # a returning core opens its socket with its own hello (D19)
        if sender == "core":
            if k != "hello" and not hellod:
                problems.append((ORDER, f"{where}: the core's first message on a vt-control socket must be hello"))
                continue
            if k == "hello":
                hellod = True
            elif k == "open":
                sid = msg.get("session_id")
                if sid is not None:
                    closed.discard(sid)  # a fresh open makes the grid again
                    open_.add(sid)
            elif k in VT_SESSION_CMDS:
                sid = msg.get("session_id")
                if sid in closed:
                    problems.append((ORDER, f"{where}: session {sid} after its close"))
                elif sid not in open_:
                    problems.append((ORDER, f"{where}: session {sid} is not open"))
                if k in VT_REQUESTS:
                    rid = msg.get("request_id")
                    if rid in pending:
                        problems.append((ORDER, f"{where}: request_id {rid!r} is already in flight"))
                    else:
                        pending[rid] = k
                elif k == "close":
                    open_.discard(sid)
                    closed.add(sid)
            elif k == "ping":
                pings += 1
            continue
        # sender == "vt"
        if not hellod:
            problems.append((ORDER, f"{where}: a VT message before the core's hello"))
            continue
        if k == "hello_ack":
            continue
        if k in VT_ANSWERS:
            rid = msg.get("request_id")
            asked = pending.pop(rid, None)
            if asked is None:
                problems.append((ORDER, f"{where}: answers no in-flight request with request_id {rid!r}"))
            elif asked != VT_ANSWERS[k]:
                problems.append((ORDER, f"{where}: {k} answers a {asked}, not a {VT_ANSWERS[k]}"))
        elif k == "pong":
            if pings <= 0:
                problems.append((ORDER, f"{where}: pong answers no ping"))
            else:
                pings -= 1
        elif k == "error":
            rid = msg.get("request_id")
            if rid is not None and pending.pop(rid, None) is None:
                problems.append((ORDER, f"{where}: answers no request with request_id {rid!r}"))
            # an error without a request_id (unknown_cmd, bad_request, unsupported_proto) chases nothing
    # A request left unanswered at the capture's end is not itself out of order: the capture may be
    # cut, and a VT worker that cannot answer (down or hung, F20) is a real state — vt-snapshot-
    # unavailable's heartbeat and the core's vt_unavailable alert grade that, not this socket.
    return problems


def order_problems(socket: str, messages: list[tuple[str, dict]], runs: list | None = None) -> list[tuple[str, str]]:
    if socket == "conn-control":
        return conn_order(messages, runs)
    if socket == "vt-control":
        return vt_order(messages, runs)
    return [(ORDER, p) for p in supervisor_order(messages, runs)]


# ---- self-check ---------------------------------------------------------------------------

def check_sequence(fx: Fixture, ctx: selfcheck.Context) -> None:
    case, reg = fx.case, ctx.registry
    socket = case["socket"]
    known = set()
    for ref in SENDERS[socket].values():
        known |= defined_kinds(reg, ref)
    for i, item in enumerate(case["messages"], 1):
        errors = message_errors(reg, socket, item["from"], item["msg"])
        if item.get("unknown"):
            if kind(item["msg"]) in known:
                ctx.report.fail(fx.rel, f"message {i} is marked unknown, but {kind(item['msg'])!r} is defined")
        elif item.get("invalid"):
            if not errors:
                ctx.report.fail(fx.rel, f"message {i} is marked invalid, but it validates: the case tests nothing")
        elif errors:
            ctx.report.fail(fx.rel, f"message {i}: {errors[0]}")
    pairs = [(m["from"], m["msg"]) for m in case["messages"]]
    for _rule, problem in order_problems(socket, pairs, [m.get("run") for m in case["messages"]]):
        ctx.report.fail(fx.rel, problem)


def coverage(ctx: selfcheck.Context) -> None:
    """Every command, event, request and reply is shown by a sequence fixture, both connect
    protocols included."""
    reg = ctx.registry
    if CONN not in reg.docs or SUP not in reg.docs:
        return
    seen: dict[str, set[str]] = {s: set() for s in SENDERS}
    protocols = set()
    for fx in ctx.fixtures:
        if fx.kind != "sequence":
            continue
        for item in fx.case.get("messages", []):
            if not item.get("unknown") and not item.get("invalid"):
                seen[fx.case["socket"]].add(kind(item["msg"]))
                if kind(item["msg"]) == "connect":
                    protocols.add(item["msg"].get("protocol"))
    for socket, senders in SENDERS.items():
        for ref in senders.values():
            for name in sorted(defined_kinds(reg, ref) - seen[socket]):
                ctx.report.fail("coverage", f"{socket} message {name!r} appears in no sequence fixture")
    for protocol in sorted({"ssh", "echo"} - protocols):
        ctx.report.fail("coverage", f"connect with protocol {protocol!r} appears in no sequence fixture")


selfcheck.KIND_CHECKS["sequence"] = check_sequence
selfcheck.COVERAGE_CHECKS.append(coverage)
