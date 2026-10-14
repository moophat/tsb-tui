"""The engine layer (design §3.3 `term/`): libghostty-vt behind `VTBackend`, plus the encoders.

`backend.py` owns the grid; `ghostty.py` is the ctypes binding and its ABI manifest check; `keys.py`
turns a key, paste, mouse or focus event into the bytes the session's modes require. Nothing in
this package imports Textual or the network — it is stdlib `ctypes` and `tsb_tui` only (phase 1
acceptance).

Phase 1 is additive: nothing imports this package yet. Phase 3's canvas and phase 4's input path do.
"""

from __future__ import annotations

from .backend import (
    BackendError,
    Cell,
    Cursor,
    Modes,
    Row,
    VTBackend,
)
from .ghostty import AbiError, BuildInfo, GhosttyLib, LibraryError, load
from .keys import (
    UnknownKey,
    encode_focus,
    encode_key,
    encode_mouse,
    encode_paste,
)

__all__ = [
    "AbiError",
    "BackendError",
    "BuildInfo",
    "Cell",
    "Cursor",
    "GhosttyLib",
    "LibraryError",
    "Modes",
    "Row",
    "UnknownKey",
    "VTBackend",
    "encode_focus",
    "encode_key",
    "encode_mouse",
    "encode_paste",
    "load",
]
