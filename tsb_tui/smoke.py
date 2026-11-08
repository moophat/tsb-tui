"""The non-interactive smoke path the deploy hook runs after every deploy, alongside
`harness/rigs/smoke.sh`: load the terminal engine, login, create a session, attach, one round trip,
close — the same binary
the operator runs, driven without a human. User from `TSB_SUPERADMIN_USER`; password read from the
file named by `TSB_SUPERADMIN_PASSWORD_FILE`, or from stdin when that names `-` (mirroring the
core's own `tsb seed-superadmin --password-file -`) — never argv, never a plaintext env value.
Recording and host-key warnings are acknowledged automatically here — there is no operator to ask.
The round trip sends a unique probe and succeeds only once that exact string comes back — the
target's own echo of typed input — never on unrelated output arriving on its own; a trailing
Ctrl-U erases the probe line afterward so nothing it spells out ever executes. One status line per
step; exit 0 on a full pass, 1 otherwise, with no step failing silently, and no step's own
transport failure (a refused connection, a timeout, a TLS failure — `HttpError`) or protocol
failure (a broken WebSocket handshake or frame — `HandshakeError`/`ProtocolError`) escaping
unhandled."""

from __future__ import annotations

import os
import secrets
import stat
import sys
import time
from pathlib import Path

from tsb_contract_client import ApiError, Attachment, AttachRefused, Client

from . import config
from ._transport import ConnectionClosed, HandshakeError, HttpError, ProtocolError
from .app import EngineError, load_engine

TERM = "xterm-256color"
COLS, ROWS = 80, 24
RECV_TIMEOUT_S = 10.0


def _password_from_file(path_str: str) -> str:
    """`-` reads the password from stdin instead of a file — the deploy hook pipes the root-only
    password file into a smoke run as the unprivileged operator this way, so the operator's own
    installed code never runs as root. Otherwise mirrors harness/rigs/pylib/env.py's
    `password_from_file` (a different unit, so not imported: tui/ imports only the published
    contract client — the read/strip/warn rules must still agree with what the core's own seed
    reads, or a smoke run logs in with the wrong string)."""
    if path_str == "-":
        value = sys.stdin.read().rstrip("\r\n")
        if not value:
            print("ERROR: TSB_SUPERADMIN_PASSWORD_FILE=- read an empty password from stdin",
                  file=sys.stderr)
            raise SystemExit(1)
        return value
    path = Path(path_str)
    if not path.is_file():
        print(f"ERROR: TSB_SUPERADMIN_PASSWORD_FILE={path_str} does not name a file", file=sys.stderr)
        raise SystemExit(1)
    mode = path.stat().st_mode
    if sys.platform == "win32":
        print("NOTICE: password file mode check skipped on Windows; stdin is the safer door", file=sys.stderr)
    elif mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"WARNING: TSB_SUPERADMIN_PASSWORD_FILE={path_str} is group- or other-readable; "
              "it should be owner-only", file=sys.stderr)
    value = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        print(f"ERROR: TSB_SUPERADMIN_PASSWORD_FILE={path_str} names an empty file", file=sys.stderr)
        raise SystemExit(1)
    return value


class _ProbeFailed(Exception):
    """The round trip below did not prove the target is alive and echoing. Its message is the
    smoke path's `FAIL output` line — never a bare traceback."""


def _probe_round_trip(att: Attachment, recv_timeout: float) -> None:
    """Send a unique probe (a random suffix, no trailing newline) and succeed only once that
    exact string comes back in the output — the target's own echo of typed input, which network
    CLIs and shells both do. A banner or a prompt arriving before or independently of the probe
    must not satisfy it: this checks for the probe's own bytes, not merely *some* output. Once
    seen, Ctrl-U (0x15, erase line) clears the probe from the target's input buffer so nothing it
    spells out ever executes."""
    probe = f"tsb-smoke-{secrets.token_hex(8)}".encode("ascii")
    try:
        att.send(probe)
        buf = b""
        deadline = time.monotonic() + recv_timeout
        while probe not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _ProbeFailed(f"no echo of the probe within {recv_timeout:g}s")
            try:
                chunk = att.recv(remaining)
            except TimeoutError:
                raise _ProbeFailed(f"no echo of the probe within {recv_timeout:g}s") from None
            if chunk is None:
                raise _ProbeFailed(f"session closed before the probe echoed back (close {att.close_code})")
            buf += chunk
        att.send(b"\x15")  # Ctrl-U: erase the line so the probe is never executed
    except (ConnectionClosed, ProtocolError, OSError) as exc:
        raise _ProbeFailed(str(exc)) from exc


def _engine_step(cfg: config.Config) -> bool:
    """Load the terminal engine and print what it is. The first step, so a node whose engine is
    missing or unloadable fails the deploy check here, naming the knob and the path, before anything
    is created on the broker (DT2, DT11)."""
    try:
        lib = load_engine(cfg)
    except EngineError as exc:
        print(f"FAIL engine: {exc}", file=sys.stderr)
        return False
    info = lib.build_info
    print(f"engine: ok libghostty-vt {info.version_string} "
          f"(simd={str(info.simd).lower()}, kitty_graphics={str(info.kitty_graphics).lower()})")
    return True


def run(broker: str, target: str, ca_file: str | None, *, recv_timeout: float = RECV_TIMEOUT_S,
        cfg: config.Config | None = None) -> int:
    if cfg is None:
        try:
            # Nothing here sends a size request, so auto size means nothing to this path; turn it off
            # so the smoke never asks the target for anything.
            cfg = config.load(overrides=["size.auto_request=false"])
        except config.ConfigError as exc:
            print(f"FAIL config: {exc}", file=sys.stderr)
            return 1
    if not _engine_step(cfg):
        return 1
    user = os.environ.get("TSB_SUPERADMIN_USER")
    password_file = os.environ.get("TSB_SUPERADMIN_PASSWORD_FILE")
    if not user or not password_file:
        print("ERROR: TSB_SUPERADMIN_USER and TSB_SUPERADMIN_PASSWORD_FILE must be set", file=sys.stderr)
        return 1
    password = _password_from_file(password_file)

    try:
        # A cleartext broker URL is refused here (`_http.HttpClient`) unless the host is
        # loopback — otherwise the superadmin password below would cross the network in clear.
        # `broker.timeout_s`: the same knob as the app — a slow create must not read as a transport
        # timeout.
        client = Client(broker, ca_file=ca_file, timeout=cfg["broker.timeout_s"])
    except ValueError as exc:
        print(f"FAIL broker URL: {exc}", file=sys.stderr)
        return 1
    try:
        client.login(user, password)
    except (ApiError, HttpError, OSError) as exc:
        print(f"FAIL login: {exc}", file=sys.stderr)
        return 1
    print("login: ok")

    try:
        created = client.create_session(target, "new", TERM, COLS, ROWS)
    except (ApiError, HttpError, OSError) as exc:
        print(f"FAIL create session: {exc}", file=sys.stderr)
        return 1
    session_id = created["session"]["id"]
    print("create session: ok")

    if created["session"]["recording"]["active"]:
        print("recording warning: acknowledged (smoke mode)")
    if "hostkey" in created:
        print("host key: acknowledged (smoke mode)")

    ok = True
    try:
        try:
            att = client.attach(session_id)
        except (ApiError, AttachRefused, HandshakeError, HttpError, ProtocolError, OSError) as exc:
            # A broken frame can surface here rather than in the receive step below: the upgrade
            # response and a frame sent right behind it can land in the same read from the
            # socket, so the client's own attach loop is the one that first parses it.
            print(f"FAIL attach: {exc}", file=sys.stderr)
            return 1
        print("attach: ok")
        try:
            _probe_round_trip(att, recv_timeout)
        except _ProbeFailed as exc:
            print(f"FAIL output: {exc}", file=sys.stderr)
            ok = False
        else:
            print("output: ok")
        finally:
            att.close()
    finally:
        # Every path — including a FAIL above — closes the session: a leaked detached session on
        # the standing prefix sits for up to 7 days (session model §11, the `idle` timer). A
        # close that itself fails must say so and fail the run — printing "close: ok" regardless
        # would tell a broken deploy hook it passed.
        try:
            client.close(session_id)
        except ApiError as exc:
            print(f"close: FAILED ({exc.status} {exc.code})", file=sys.stderr)
            ok = False
        except (HttpError, OSError) as exc:
            print(f"close: FAILED ({exc})", file=sys.stderr)
            ok = False
        else:
            print("close: ok")
    return 0 if ok else 1
