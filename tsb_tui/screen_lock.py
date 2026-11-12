"""The screen lock and the activity signal of one TUI instance (phase 12 § F, § G; cred §5.5; screen-lock; vault-lock).

Each surface instance is a client: this one holds its own lock. The time is `screen_lock_after`, the core's resolved value
from the login answer and every `GET /auth/me` the TUI reads (`apply`), never a local value. After that long with no input
from its user the lock screen covers the whole stack (`lock`); the sessions stay attached. While it is up:

* a screen pushed by something running in the background waits (`hold`) and appears after the unlock, so a dialog can never
  cover the lock screen with session content; so does anything that would take screens off the stack (`after_unlock`), since
  the lock refuses a pop;
* a toast or notification is not drawn (`TsbTuiApp.toast`/`notify` ask `locked`), so none can carry a session's text over it.

The activity signal is `POST /auth/activity`, sent when the user types or clicks, at most once per `activity_interval` the same
settings carry: the core counts at most one per interval anyway, and a surface throttles to it. Nothing typed while the screen
is locked is a signal (ruling 2026-10-31 16:25); the first input after a correct password re-check is sent as usual.

The clock is `app.idle_clock` (a test sets its own) and the check runs every `app.screen_lock_poll` seconds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from textual import events
from textual.timer import Timer
from tsb_contract_client import ApiError

from ._transport import HttpError

log = logging.getLogger(__name__)

# What counts as input from the user: a key, a paste, a click or a wheel turn. A pointer that only moves does not.
INPUT_EVENTS = (events.Key, events.Paste, events.MouseDown, events.MouseUp, events.MouseScrollDown,
                events.MouseScrollUp)


class ScreenLock:
    def __init__(self, app: Any) -> None:
        self._app = app
        self.settings: dict | None = None
        self._last_input = 0.0
        self._last_signal: float | None = None
        self._timer: Timer | None = None
        self._screen: Any = None
        self._held: list[tuple[Any, Callable[..., Any] | None]] = []
        self._after: list[Callable[[], None]] = []

    @property
    def locked(self) -> bool:
        return self._screen is not None

    # -- the settings --

    def apply(self, locks: dict | None) -> None:
        """The core's resolved lock settings (a login answer's or `/auth/me`'s `locks`); absent: none are in force."""
        if not isinstance(locks, dict):
            return
        first = self.settings is None
        self.settings = dict(locks)
        if first:
            self._last_input = self._app.idle_clock()      # logging in is the user's last input
            self._last_signal = None
            self._timer = self._app.set_interval(self._app.screen_lock_poll, self._tick)

    def clear(self) -> None:
        """The login ended (logout, or a 401): no lock, no settings, nothing held."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.settings = None
        self._screen = None
        self._held.clear()
        self._after.clear()
        self._last_signal = None

    # -- input and the signal --

    def note_input(self) -> None:
        """Input from the user: the idle time starts again and, at most once per interval, the core is told. While the
        screen is locked it is neither: what is typed on the lock screen is not the user's activity (ruling 2026-10-31 16:25),
        so a locked, unattended screen cannot keep the vault open by itself. The first input after the core accepted the
        password (`unlock`) signals as usual."""
        settings = self.settings
        if settings is None or self.locked:
            return
        now = self._app.idle_clock()
        self._last_input = now
        interval = settings.get("activity_interval")
        if isinstance(interval, int) and (self._last_signal is None or now - self._last_signal >= interval):
            self._last_signal = now
            self._app.call_api(self._app.client.activity, on_done=lambda _r: None, on_error=self._signal_failed)

    def _signal_failed(self, exc: ApiError | HttpError | OSError) -> None:
        if isinstance(exc, ApiError) and exc.status == 401:
            self._app.notify_api_error(exc)      # the login ended; any other failure of a guardrail is not shown

    # -- locking --

    def _tick(self) -> None:
        if self._app.under_drive:
            drive = getattr(self._app, "drive_screen_lock", None)
            if drive is not None:
                drive.check()
            return
        settings = self.settings
        if settings is None or self.locked:
            return
        after = settings.get("screen_lock_after")
        if isinstance(after, int) and self._app.idle_clock() - self._last_input >= after:
            self.lock()

    def lock(self, note: str = "") -> None:
        """Cover every screen. Idempotent."""
        if self._app.under_drive:
            drive = getattr(self._app, "drive_screen_lock", None)
            if drive is not None:
                drive.engage(note or "screen locked now")
            return
        if self.locked:
            return
        from .screens.lock import LockScreen

        self._screen = LockScreen(note)
        self._app.clear_notifications()
        self._app.push_screen(self._screen)

    def show(self, message: str) -> None:
        if self._screen is not None:
            self._screen.show(message)

    def unlock(self, screen: Any) -> None:
        """The core accepted the login password `screen` sent: take that lock screen away and show what was held. An answer
        for any other screen than the one up now (a lock of a login that has ended) lifts nothing."""
        if screen is None or screen is not self._screen:
            return
        self._screen = None
        self._last_input = self._app.idle_clock()
        self._app.pop_screen()
        held, self._held = self._held, []
        for held_screen, callback in held:
            self._app.push_screen(held_screen, callback)
        after, self._after = self._after, []
        for callback in after:
            self._app.call_later(callback)

    def hold(self, screen: Any, callback: Callable[..., Any] | None) -> None:
        self._held.append((screen, callback))

    def after_unlock(self, callback: Callable[[], None]) -> None:
        """Run `callback` once the lock is lifted (on the UI thread, after the held screens are up). It re-checks what it
        needs: the world may have moved on while the screen was locked."""
        self._after.append(callback)

    # -- lock now --

    def lock_now(self) -> None:
        """`POST /auth/lock` purges every vault unlock the user holds; this client locks its own screen as well."""
        self.lock()
        client = self._app.client

        def failed(exc: ApiError | HttpError | OSError) -> None:
            self._lock_now_failed(client, exc)

        self._app.call_api(client.lock_now, on_done=lambda _r: None, on_error=failed, on_stale_error=failed)

    def _lock_now_failed(self, client: Any, exc: ApiError | HttpError | OSError) -> None:
        """A failure of the request of the login that is now: a 401 ends it, any other says the vault lock was not
        confirmed (this screen stays locked either way). A failure of a request whose login has been replaced is no news
        to the newer login: it is settled here, logged, and shown nowhere."""
        if client is not self._app.client:
            log.info("lock now: the request of a replaced login failed and is dropped: %s", exc)
            return
        if isinstance(exc, ApiError) and exc.status == 401:
            self._app.notify_api_error(exc)
            return
        log.info("lock now: the vault lock was not confirmed: %s", exc)
        self.show("The vault lock was not confirmed.")
