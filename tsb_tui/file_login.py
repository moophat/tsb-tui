"""The one-shot holder for a password that came from a file (`--user` with `--password-file`).

Textual's crash report prints the locals of every frame (`App._fatal_error`, `show_locals=True`), so the value must
sit in no named local and no widget. `FileLogin` holds it, shows `<password: withheld>` wherever it is printed, and
gives it up once through `take()`."""

from __future__ import annotations

import sys
from typing import Any

from tsb_contract_client import ApiError

from ._transport import HttpError


class FileLogin:
    """The user and, until `take()`, the password read from the file. `repr` never shows the value."""

    def __init__(self, user: str, password: str) -> None:
        self.user = user
        self._value: str | None = password

    @property
    def pending(self) -> bool:
        """Whether the password has not been taken yet: only then is the Login screen in file mode."""
        return self._value is not None

    def take(self) -> str:
        """The password, once: the holder forgets it as it hands it over."""
        value, self._value = self._value, None
        if value is None:
            raise RuntimeError("the password from the file was already used")
        return value

    def __repr__(self) -> str:
        return "<password: withheld>"


class FileLoginCrashed(Exception):
    """An unexpected failure of the login worker, named by its type only: the original is dropped (`from None`)
    because the frames it carries (the contract client's `login(user, password)`) hold the password as a local."""


def login_from_file(client: Any, holder: FileLogin) -> dict:
    """The login worker (runs off the UI thread): `client.login(user, password)` with the password passed inline, never
    bound to a name here. `ApiError`, `HttpError` and `OSError` are the login's ordinary refusals and go on to the
    caller's `on_error`; anything else is re-raised as `FileLoginCrashed`, so a crash report never walks into
    frames that hold the value."""
    try:
        return client.login(holder.user, holder.take())
    except (ApiError, HttpError, OSError):
        raise
    except Exception:  # noqa: BLE001 — any other failure is re-raised by type only (the password is in its frames)
        raise FileLoginCrashed(type(sys.exc_info()[1]).__name__) from None
