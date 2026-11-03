"""The vault operations a TUI user starts, as functions that ask for what they need (phase 12 § B, § C; plan D2, D15).

Untrusted mode only (DEFAULT 64): nothing about the vault is written on this host, the TUI never registers a device and
never sends a challenge. The passphrase is typed into a prompt, turned into bytes (OpaqueString) and into the two halves
by argon2id off the UI thread, sent in an untrusted unlock, and dropped; the core holds the unlock until the vault locks.

* `unlock(app)`: read the salt; an uninitialised vault is offered init (`init_vault`); otherwise ask the passphrase and send
  the unlock. A wrong passphrase puts the prompt back; cancelling ends it as `cancelled`.
* `with_unlock(app, func, *args)`: run a vault call that needs this login's unlock; answered `vault_locked`, unlock and
  repeat it once.
* `save_login(...)`: THE function login-at-connect calls. Save a username with a password or a private key for a target:
  the salt is read, init is offered if the vault is not initialised, the store runs (asking the passphrase if the login
  holds no unlock) and then one Layer 2a batch call maps every picked target to the reference.

Every function returns what happened and never raises for an answer the core gave: `SaveOutcome` / `UnlockResult` carry the
core's own message. A 401 means the login ended: the app is sent to its login screen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tsb_contract_client import ApiError

from . import vault_crypto
from ._transport import HttpError
from .screens.vault_prompts import InitScreen, PassphraseScreen

VAULT_LOCKED = "vault_locked"
VAULT_NOT_INITIALIZED = "vault_not_initialized"
VAULT_EXISTS = "vault_exists"
UNLOCK_FAILED = "unlock_failed"
RATE_LIMITED = "rate_limited"

WRONG_PASSPHRASE = "Incorrect passphrase."      # credential-protection Appendix B
REFUSED_PASSPHRASE = "That passphrase cannot be used: it has characters the vault does not accept."
DERIVING = "Deriving the key (this takes a few seconds)…"
UNLOCKING = "Unlocking…"
USERNAME_REQUIRED = "A username is required."

Failure = ApiError | HttpError | OSError


@dataclass(frozen=True)
class UnlockResult:
    """`status`: `unlocked`, `cancelled` or `error` (then `message` is the core's own words or the transport's)."""

    status: str
    message: str = ""


@dataclass(frozen=True)
class SaveOutcome:
    """What `save_login` did. `status`: `saved`, `cancelled` or `error`. `stored` and `mapped` say how far it got: an
    `error` with `stored` true is a credential in the vault that no target maps to yet (map or delete it from the
    credentials list). `message` is words for the user, the core's own where the core refused."""

    status: str
    reference: str
    stored: bool = False
    mapped: bool = False
    message: str = ""
    error: Exception | None = None


@dataclass(frozen=True)
class Done:
    """The result of `with_unlock`: `status` `ok` (then `value`), `cancelled` or `error` (then `error`, `message`)."""

    status: str
    value: Any = None
    error: Exception | None = None
    message: str = ""


def wrong_passphrase_message(exc: ApiError) -> str:
    """Appendix B's message for an `unlock_failed`: "Incorrect passphrase. N attempts remaining." with the count the core
    served in `detail["attempts_remaining"]` (a count of one reads "1 attempt remaining."); the plain message when the
    count is absent or is not a count."""
    count = exc.detail.get("attempts_remaining")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        word = "attempt" if count == 1 else "attempts"
        return f"{WRONG_PASSPHRASE} {count} {word} remaining."
    return WRONG_PASSPHRASE


def failure_message(exc: Exception) -> str:
    return exc.message if isinstance(exc, ApiError) else str(exc)


class _LoginChanged(Exception):
    """The save belongs to a TSB login that is no longer current."""


async def call(app: Any, func: Callable[..., Any], *args: Any,
               check_current: Callable[[], None] | None = None) -> Any:
    """A blocking client call off the UI thread, awaited. Raises what `App.call_api` would have handed to `on_error`."""
    if check_current is not None:
        check_current()
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    def done(result: Any) -> None:
        if not future.done():
            future.set_result(result)

    def failed(exc: Failure) -> None:
        if not future.done():
            future.set_exception(exc)

    app.call_api(func, *args, on_done=done, on_error=failed, on_stale_error=failed)
    return await future


def _ended_login(app: Any, exc: Exception) -> None:
    """A 401 is the login gone, wherever it surfaced: the app returns to its login screen."""
    if isinstance(exc, ApiError) and exc.status == 401:
        app.notify_api_error(exc)


def _put_back(screen: Any, message: str) -> bool:
    """Put the prompt back for another try. False when it is gone: a login that ended popped every screen, or the prompt was
    closed. The flow then ends as cancelled and sends nothing, and never calls a removed screen."""
    if screen.gone:
        return False
    screen.refuse(message)
    return True


# -- init and unlock --

async def init_vault(app: Any, salt: dict | None = None, *,
                     check_current: Callable[[], None] | None = None) -> UnlockResult:
    """Offer init: the passphrase twice with cred §7.4's warning. Mint the salt if there is none, derive, send init. The
    login then holds an unlock (DEFAULT 35)."""
    try:
        if salt is None or not salt.get("user_salt"):
            salt = await call(app, app.client.vault_mint_salt, check_current=check_current)
    except ApiError as exc:
        if exc.code == VAULT_EXISTS:
            return await unlock(app, check_current=check_current)        # someone else sealed it first: it is an unlock now
        _ended_login(app, exc)
        return UnlockResult("error", failure_message(exc))
    except (HttpError, OSError) as exc:
        return UnlockResult("error", failure_message(exc))
    screen = InitScreen()
    app.push_screen(screen)
    try:
        while True:
            typed = await screen.submitted()
            if typed is None or screen.gone:
                return UnlockResult("cancelled")
            screen.busy(DERIVING)
            try:
                halves = await asyncio.to_thread(vault_crypto.halves_b64, typed, salt["user_salt"])
            except vault_crypto.PassphraseRefused:
                if not _put_back(screen, REFUSED_PASSPHRASE):
                    return UnlockResult("cancelled")
                continue
            finally:
                typed = ""
            if screen.gone:
                return UnlockResult("cancelled")
            screen.busy(UNLOCKING)
            try:
                await call(app, app.client.vault_init, *halves, check_current=check_current)
            except ApiError as exc:
                halves = ("", "")
                if exc.code == VAULT_EXISTS:
                    screen.close()
                    return await unlock(app, check_current=check_current)
                _ended_login(app, exc)
                return UnlockResult("error", failure_message(exc))
            except (HttpError, OSError) as exc:
                return UnlockResult("error", failure_message(exc))
            return UnlockResult("unlocked")
    finally:
        screen.close()


async def unlock(app: Any, salt: dict | None = None, *, why: str = "",
                 check_current: Callable[[], None] | None = None) -> UnlockResult:
    """Unlock this login's vault in untrusted mode. `salt` is the answer of a salt read the caller already made."""
    try:
        if salt is None:
            salt = await call(app, app.client.vault_salt, check_current=check_current)
    except ApiError as exc:
        _ended_login(app, exc)
        return UnlockResult("error", failure_message(exc))
    except (HttpError, OSError) as exc:
        return UnlockResult("error", failure_message(exc))
    if not salt.get("initialized"):
        return await init_vault(app, salt, check_current=check_current)
    screen = PassphraseScreen(why)
    app.push_screen(screen)
    try:
        while True:
            typed = await screen.submitted()
            if typed is None or screen.gone:
                return UnlockResult("cancelled")
            screen.busy(DERIVING)
            try:
                halves = await asyncio.to_thread(vault_crypto.halves_b64, typed, salt["user_salt"])
            except vault_crypto.PassphraseRefused:
                if not _put_back(screen, REFUSED_PASSPHRASE):
                    return UnlockResult("cancelled")
                continue
            finally:
                typed = ""
            if screen.gone:
                return UnlockResult("cancelled")
            screen.busy(UNLOCKING)
            try:
                await call(app, app.client.vault_unlock_untrusted, *halves, check_current=check_current)
            except ApiError as exc:
                halves = ("", "")
                if exc.code == UNLOCK_FAILED:
                    if not _put_back(screen, wrong_passphrase_message(exc)):
                        return UnlockResult("cancelled")
                    continue
                if exc.code == RATE_LIMITED:
                    if not _put_back(screen, exc.message):      # the core's own words: no time is invented
                        return UnlockResult("cancelled")
                    continue
                if exc.code == VAULT_NOT_INITIALIZED:
                    screen.close()
                    return await init_vault(app, None, check_current=check_current)
                _ended_login(app, exc)
                return UnlockResult("error", failure_message(exc))
            except (HttpError, OSError) as exc:
                return UnlockResult("error", failure_message(exc))
            return UnlockResult("unlocked")
    finally:
        screen.close()


async def with_unlock(app: Any, func: Callable[..., Any], *args: Any, salt: dict | None = None,
                      check_current: Callable[[], None] | None = None) -> Done:
    """Run a vault call that needs this login's unlock. Answered `vault_locked`: unlock, then repeat it once."""
    for attempt in (1, 2):
        try:
            return Done("ok", await call(app, func, *args, check_current=check_current))
        except ApiError as exc:
            if exc.code == VAULT_LOCKED and attempt == 1:
                result = await unlock(app, salt, check_current=check_current)
                if result.status == "cancelled":
                    return Done("cancelled")
                if result.status != "unlocked":
                    return Done("error", message=result.message)
                continue
            _ended_login(app, exc)
            return Done("error", error=exc, message=exc.message)
        except (HttpError, OSError) as exc:
            return Done("error", error=exc, message=str(exc))
    return Done("error", message="The vault is still locked.")


# -- saving a login for a target --

def _targets(target: str | Sequence[str]) -> list[str]:
    names = [target] if isinstance(target, str) else list(target)
    return [name.strip() for name in names if name and name.strip()]


def check_login(target: str | Sequence[str], username: str, secret: str | None, reference: str) -> str | None:
    """Why this save cannot be sent, or None. A login is a username with a secret for at least one target under a
    reference; the form and `save_login` ask the same question, so neither sends what the other would refuse."""
    if not (username or "").strip():
        return USERNAME_REQUIRED
    if not secret:
        return "A password or a private key is required."
    if not (reference or "").strip():
        return "A reference is required."
    if not _targets(target):
        return "Name at least one target."
    return None


async def save_login(app: Any, target: str | Sequence[str], username: str, *, password: str | None = None,
                     key: str | None = None, reference: str, label: str | None = None) -> SaveOutcome:
    """Save a username with a password or a private key for one target or several, in the personal vault, then map them.

    The order is the plan's (phase 12 § C, D15): read the salt; offer init if the vault is not initialised; store, asking
    the passphrase first when this login holds no unlock; then one Layer 2a batch call `{target → reference}`. Returns
    `saved`, `cancelled` or `error`; a store the core refuses (a reference already in use, an encrypted key) is `error`
    with the core's message and nothing mapped. A failed mapping leaves the stored credential: `error` with `stored`
    true. The secret is a local of this call, never kept; callers drop theirs once it returns."""
    client, generation = app.client, app._login_gen
    was_stored = False

    def check_current() -> None:
        if app.client is not client or app._login_gen != generation:
            raise _LoginChanged

    try:
        targets = _targets(target)
        secret, kind = (key, "key") if key else (password, "password")
        if (problem := check_login(targets, username, secret, reference)) is not None:
            return SaveOutcome("error", reference, message=problem)
        try:
            salt = await call(app, client.vault_salt, check_current=check_current)
        except ApiError as exc:
            _ended_login(app, exc)
            return SaveOutcome("error", reference, message=exc.message, error=exc)
        except (HttpError, OSError) as exc:
            return SaveOutcome("error", reference, message=str(exc), error=exc)
        if not salt.get("initialized"):
            opened = await init_vault(app, salt, check_current=check_current)
            if opened.status == "cancelled":
                return SaveOutcome("cancelled", reference, message="Not saved: the vault was not set up.")
            if opened.status != "unlocked":
                return SaveOutcome("error", reference, message=opened.message)
        stored = await with_unlock(app, client.store_credential, reference, kind, username.strip(), secret,
                                   label or None, salt=salt, check_current=check_current)
        secret = None
        if stored.status == "cancelled":
            return SaveOutcome("cancelled", reference, message="Not saved: the vault was not unlocked.")
        if stored.status != "ok":
            return SaveOutcome("error", reference, message=stored.message, error=stored.error)
        was_stored = True
        try:
            await call(app, client.map_profiles, {name: reference for name in targets}, check_current=check_current)
        except ApiError as exc:
            _ended_login(app, exc)
            return SaveOutcome("error", reference, stored=True, error=exc, message=(
                f"The login was stored as {reference} but not mapped to {', '.join(targets)}: {exc.message}. "
                f"Map or delete it from the credentials list."))
        except (HttpError, OSError) as exc:
            return SaveOutcome("error", reference, stored=True, error=exc, message=(
                f"The login was stored as {reference} but not mapped to {', '.join(targets)}: {exc}. "
                f"Map or delete it from the credentials list."))
        return SaveOutcome("saved", reference, stored=True, mapped=True,
                           message=f"Saved {reference} for {', '.join(targets)}.")
    except _LoginChanged:
        return SaveOutcome("cancelled", reference, stored=was_stored, message="Not saved: your TSB login changed.")
