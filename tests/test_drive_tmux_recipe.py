"""GUIDE.md §9 tmux recipe: the gate accepts each step of attach-read, read, detach.

Why it matters (Rule 9): the recipe is the documented path for reading a tmux session. If the gate refused a step
(the prefix, the detach key, the first type at the shell prompt), an agent following the guide would stall on a
production device. Servers: the contract's in-process fixture server.
"""

from __future__ import annotations

import asyncio

from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


def test_tmux_read_only_recipe_passes_the_gate(serve, tmp_path) -> None:
    """Each drive command in the GUIDE.md §9 tmux recipe is accepted by the gate: type at the shell prompt (no
    full_screen), wait for the tmux screen, read the reply (alt: true), ctrl+b alone, d with full_screen."""

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, _serve.USER, 80, 24)
        async with Rig(url, tmp_path=tmp_path) as rig:
            await rig.login()
            await rig.attach(sid)
            _emit_output(serve_app, sid, b"user@host:~$ ")
            assert (await rig.cmd("wait", until={"screen": "\\$"}, timeout_ms=5000))["ok"]

            look1 = await rig.look()
            step1 = await rig.cmd("type", text="tmux attach -r -t mysession\r", look=look1)
            assert step1["ok"], step1
            assert (await rig.cmd("wait", until={"reply": "tmux attach"}, timeout_ms=5000))["ok"]

            _emit_output(serve_app, sid, b"\x1b[?1049h\x1b[H\x1b[Jtmux[mysession] 0:window0")
            step2 = await rig.cmd("wait", until={"screen": "tmux", "settled_ms": True}, timeout_ms=5000)
            assert step2["ok"], step2

            reply = await rig.cmd("read", what="reply")
            assert reply["alt"] is True, "tmux took the alt screen"

            look2 = step2["look"]
            step4 = await rig.cmd("keys", keys=["ctrl+b"], look=look2)
            assert step4["ok"], step4

            look3 = await rig.look()
            step5 = await rig.cmd("keys", keys=["d"], look=look3, full_screen=True)
            assert step5["ok"], step5

    asyncio.run(check())
