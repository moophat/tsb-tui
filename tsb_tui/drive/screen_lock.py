"""Headless idle lock: re-read a login's regular password file before revealing content."""
from __future__ import annotations

import asyncio
import os
import stat
from typing import Any

from tsb_contract_client import ApiError

from .._transport import HttpError
from .commands import DriveError, read_password_file


class DriveScreenLock:
    def __init__(self, driver: Any) -> None:
        self.d = driver
        self.generation = driver.app._login_gen
        self.last_command = driver.app.idle_clock()
        self.locked = False
        self.path: str | None = None
        self.failure = ""
        self.why = ""
        self.void_serial = 0
        self.void_last: dict[str, str] = {}

    def reset(self, path: str | None = None) -> None:
        self.generation = self.d.app._login_gen
        self.last_command = self.d.app.idle_clock()
        self.locked = False
        self.path = None
        self.failure = ""
        self.why = ""
        if path is not None and path != "-":
            parts = os.path.abspath(path).split("/")
            descriptor = (path in ("/dev/stdin", "/dev/stdout", "/dev/stderr")
                          or path.startswith("/dev/fd/")
                          or (len(parts) > 3 and parts[1] == "proc" and parts[3] == "fd"))
            if descriptor:
                return
            try:
                if stat.S_ISREG(os.stat(path).st_mode):
                    self.path = path
            except OSError:
                pass

    def check(self) -> bool:
        if self.generation != self.d.app._login_gen:
            self.reset()
        settings = self.d.app.screen_lock.settings
        after = settings.get("screen_lock_after") if settings else None
        idle = self.d.app.idle_clock() - self.last_command
        if self.d.screen() is not None and isinstance(after, int) and idle >= after and not self.locked:
            self.engage(f"sender idle for {idle:g} seconds; screen-lock time is {after} seconds")
        return self.locked

    def engage(self, why: str = "screen locked now") -> None:
        if self.locked:
            return
        self.locked = True
        self.why = why
        commands = self.d.commands
        self.void_serial = commands._look_serial
        self.void_last = dict(commands._last_look)
        commands._looks.clear()
        commands._last_look.clear()

    def command(self) -> None:
        self.check()
        self.last_command = self.d.app.idle_clock()

    def fields(self) -> dict[str, Any]:
        return {"locked": True, "why": self.failure or self.why}

    async def resume(self) -> bool:
        if not self.check():
            return True
        if self.failure:
            return False
        if self.path is None:
            self.failure = "screen locked: log in again with a regular password file; stdin and pipes cannot be re-read"
            return False
        client, generation = self.d.app.client, self.d.app._login_gen
        path = self.path

        def read_password() -> str:
            # Check again before opening: a replaced pipe must never block the command loop.
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise OSError("password source is no longer a regular file")
            return read_password_file(path, regular_only=True)

        try:
            password = await asyncio.to_thread(read_password)
        except (DriveError, OSError):
            if generation != self.d.app._login_gen:
                return False
            self.failure = "the password file could not be read; log in again with a regular password file"
            return False

        retry_why = ("the password re-check could not complete for now; "
                     "drive will try again at the sender's next refused command")
        try:
            if generation != self.d.app._login_gen or client is not self.d.app.client:
                return False
            await asyncio.to_thread(client.screen_unlock, password)
        except ApiError as exc:
            if generation != self.d.app._login_gen:
                return False
            if exc.status == 401:
                self.d.app.notify_api_error(exc)
                return False
            if exc.status == 429 or 500 <= exc.status < 600:
                self.why = retry_why
                return False
            self.failure = "the password re-check failed; log in again with a regular password file"
            return False
        except (HttpError, OSError):
            if generation != self.d.app._login_gen:
                return False
            self.why = retry_why
            return False
        finally:
            del password
        if generation != self.d.app._login_gen or client is not self.d.app.client:
            return False
        self.locked = False
        self.last_command = self.d.app.idle_clock()
        return True
