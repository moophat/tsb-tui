"""A `tsb-tui drive` process, in-process: the real app on Textual's test runner, the real `Driver.serve` loop, and
the real transcript writer, with commands fed from a queue and every line it writes read back.

Each rig runs in its own task. Textual finds "the" app through a context variable, so two apps (two drivers on one
session, the owner's brake) must not share one task's context: a task copies the context it starts in, and the rig's
`run_test` sets its own app inside it.

`cmd` sends one command and returns its answer; `send` returns a future for it, so a test can have a `wait` pending
and do something else meanwhile. The loop takes one command at a time, as drive does, so a command sent behind a `wait`
is taken when the wait has answered.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any, Self

from tsb_tui import config
from tsb_tui.app import TsbTuiApp
from tsb_tui.drive.runner import Driver, Transcript
from tsbc import _serve


class _Sink:
    """What `Transcript` writes to: each line is parsed, and goes to the answer it is, or to the events."""

    def __init__(self, rig: Rig) -> None:
        self._rig = rig

    def write(self, text: str) -> int:
        for line in text.splitlines():
            self._rig._line(line)
        return len(text)

    def flush(self) -> None:
        pass


class Rig:
    def __init__(self, url: str, *, tmp_path: Path, profile: str = "drive", overrides: tuple[str, ...] = (),
                 size: tuple[int, int] = (120, 40), record: io.StringIO | None = None) -> None:
        self.url = url
        self._tmp = tmp_path
        self._profile = profile
        self._overrides = overrides
        self._size = size
        self._record = record
        self.lines: list[dict[str, Any]] = []  # everything drive wrote, in order
        self.on_line: Any = None  # a test's hook, called with each line as it is written
        self.app: TsbTuiApp
        self.pilot: Any
        self.driver: Driver
        self.rc: int | None = None
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._answers: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next = 0
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None

    # -- the process --

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._main())
        waiter = asyncio.ensure_future(self._ready.wait())
        await asyncio.wait({waiter, self._task}, return_when=asyncio.FIRST_COMPLETED)
        if self._task.done():
            self._task.result()
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        if exc[0] is not None:
            # The test failed: do not wait for a loop that may be parked on a command, and never hide the failure.
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            return
        if not self._task.done():
            await self._queue.put(None)
            await asyncio.wait_for(self._task, 10)
        self._task.result()

    async def _main(self) -> None:
        cfg = config.load(profile=self._profile, overrides=list(self._overrides))
        app = TsbTuiApp(self.url, cfg=cfg)
        async with app.run_test(size=self._size) as pilot:
            self.app, self.pilot = app, pilot
            out = Transcript(_Sink(self), self._record)  # type: ignore[arg-type]
            self.driver = Driver(app, pilot, emit=out.outbound)
            self._ready.set()
            try:
                self.rc = await self.driver.serve(self._input(), out, script=False)
            finally:
                self.driver.close()

    async def _input(self):
        while (line := await self._queue.get()) is not None:
            yield line

    # -- lines in and out --

    def _line(self, line: str) -> None:
        obj = json.loads(line)
        self.lines.append(obj)
        if self.on_line is not None:
            self.on_line(obj)
        if "id" in obj and obj["id"] in self._answers:
            future = self._answers.pop(obj["id"])
            if not future.done():
                future.set_result(obj)

    def send(self, op: str, **fields: Any) -> asyncio.Future[dict[str, Any]]:
        self._next += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._answers[self._next] = future
        self._queue.put_nowait(json.dumps({"id": self._next, "op": op, **fields}))
        return future

    async def cmd(self, op: str, timeout: float = 20.0, **fields: Any) -> dict[str, Any]:
        return await asyncio.wait_for(self.send(op, **fields), timeout)

    def raw(self, line: str) -> None:
        self._queue.put_nowait(line)

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        return [line for line in self.lines if "ev" in line and (name is None or line["ev"] == name)]

    # -- common steps --

    def password_file(self, password: str = _serve.PASSWORD) -> str:
        path = self._tmp / f"password-{self._next}-{len(self.lines)}"
        path.write_text(password + "\n")
        os.chmod(path, 0o600)
        return str(path)

    async def login(self, user: str = _serve.USER) -> dict[str, Any]:
        answer = await self.cmd("login", user=user, password_file=self.password_file())
        assert answer["ok"], answer
        return answer

    async def attach(self, session: str) -> dict[str, Any]:
        answer = await self.cmd("attach", session=session)
        assert answer["ok"], answer
        return answer
