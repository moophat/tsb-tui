"""GUIDE.md §9 tmux recipe: the gate accepts each step of attach-read, read, detach.

Why it matters (Rule 9): the recipe is the documented path for reading a tmux session. If the gate refused a step
(the prefix, the detach key, the first type at the shell prompt), an agent following the guide would stall on a
production device. Servers: the contract's in-process fixture server.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from drive_support import Rig
from input_server import InputRecordingApp, make_session
from tsbc import _serve


def _emit_output(serve_app, sid: str, data: bytes) -> None:
    with serve_app.lock:
        serve_app._emit_output(serve_app.sessions[sid], data)


def _guide_commands() -> list[dict]:
    """The fenced block under the tmux heading: each `> ` line is one JSON command."""
    text = (Path(__file__).parent.parent / "tsb_tui" / "drive" / "GUIDE.md").read_text()
    heading = "### tmux: attach read-only, read, detach"
    section = text[text.index(heading) + len(heading):]
    section = section[: section.index("\n## ")]  # stop at the next top-level heading (## 10. Events)
    return [json.loads(line[2:]) for line in section.splitlines() if line.startswith("> ")]


def _sent_matches_guide(guide: list[dict], sent: list[dict]) -> None:
    """The guide's commands and the test's sends cannot drift. Compare op order, the wait's `until`, and each send's
    `keys`, `text` and `full_screen`. `id` and `look` are placeholders in the guide, so never compared."""
    assert [c["op"] for c in guide] == ["type", "wait", "read", "keys", "keys"]
    assert [c["op"] for c in sent] == [c["op"] for c in guide]
    for g, s in zip(guide, sent):
        if "until" in g:
            assert g["until"] == s.get("until"), (g, s)
        for field in ("keys", "text", "full_screen"):
            assert g.get(field, False) == s.get(field, False), (g, s, field)


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

            sent = []  # the 5 commands the guide lists, in the order the test runs them; scaffolding stays out
            look1 = await rig.look()
            step1 = await rig.cmd("type", text="tmux attach -r -t mysession\r", look=look1)
            assert step1["ok"], step1
            sent.append({"op": "type", "text": "tmux attach -r -t mysession\r"})
            assert (await rig.cmd("wait", until={"reply": "tmux attach"}, timeout_ms=5000))["ok"]

            _emit_output(serve_app, sid, b"\x1b[?1049h\x1b[H\x1b[Jtmux [mysession] 0:window0")
            step2 = await rig.cmd("wait", until={"screen": "\\[mysession\\]", "settled_ms": True}, timeout_ms=5000)
            assert step2["ok"], step2
            sent.append({"op": "wait", "until": {"screen": "\\[mysession\\]", "settled_ms": True}})

            reply = await rig.cmd("read", what="reply")
            assert reply["alt"] is True, "tmux took the alt screen"
            sent.append({"op": "read"})

            look2 = step2["look"]
            step4 = await rig.cmd("keys", keys=["ctrl+b"], look=look2)
            assert step4["ok"], step4
            sent.append({"op": "keys", "keys": ["ctrl+b"]})

            look3 = await rig.look()
            step5 = await rig.cmd("keys", keys=["d"], look=look3, full_screen=True)
            assert step5["ok"], step5
            sent.append({"op": "keys", "keys": ["d"], "full_screen": True})

            _sent_matches_guide(_guide_commands(), sent)

    asyncio.run(check())
