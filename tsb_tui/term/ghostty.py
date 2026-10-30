"""The ctypes binding to libghostty-vt, and the ABI manifest check that refuses to load on drift.

Why this exists (DT1): the TUI consumes the same engine the core's VT worker uses, so the attach
prefix it rebuilds is the one the core checked (immortality §5.2: "each surface checks it with its
own renderer, the TUI first"). The library is loaded from an **explicit path the caller gives**
(DT2); this module never searches for it.

Every declaration below cites the vendored header it comes from, at `vt/third_party/libghostty-vt`
(the pin the VT plan's DV4/DV5 fix). At load, `_check_manifest` compares each declared struct's
size and field offsets, and every enum constant used, against the library's own ABI manifest,
`ghostty_type_json()` (`include/ghostty/vt/types.h:410`). A mismatch raises `AbiError` naming the
type and refuses to load, so a pin bump that moves a field fails at start-up instead of corrupting
memory (plan R2).

Only what `VTBackend` and the encoders call is declared here. Header line numbers are from the pin
above; a bump moves them, which is exactly the drift the manifest check catches.
"""

from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from pathlib import Path

# ---- Result codes (types.h:88 GhosttyResult) -------------------------------------------------
GHOSTTY_SUCCESS = 0
GHOSTTY_INVALID_VALUE = -2
GHOSTTY_OUT_OF_SPACE = -3
GHOSTTY_NO_VALUE = -4

# ---- Build info (build_info.h:51 GhosttyBuildInfo) -------------------------------------------
BUILD_INFO_SIMD = 1
BUILD_INFO_KITTY_GRAPHICS = 2
BUILD_INFO_OPTIMIZE = 4
BUILD_INFO_VERSION_STRING = 5
BUILD_INFO_VERSION_MAJOR = 6
BUILD_INFO_VERSION_MINOR = 7
BUILD_INFO_VERSION_PATCH = 8
BUILD_INFO_VERSION_PRE = 9
BUILD_INFO_VERSION_BUILD = 10

# ---- Terminal options (terminal.h:1850 GhosttyTerminalOption) --------------------------------
OPT_USERDATA = 0
OPT_WRITE_PTY = 1
OPT_XTVERSION = 4
OPT_COLOR_SCHEME = 7
OPT_DEVICE_ATTRIBUTES = 8
OPT_KITTY_IMAGE_STORAGE_LIMIT = 15
OPT_SCROLLBACK_MAX_BYTES = 27
OPT_UNKNOWN_MAX_BYTES = 36
OPT_RENDER_HOLD = 41

# ---- Terminal data (terminal.h:~2455 GhosttyTerminalData) ------------------------------------
DATA_CURSOR_X = 3
DATA_CURSOR_Y = 4
DATA_ACTIVE_SCREEN = 6
DATA_CURSOR_VISIBLE = 7
DATA_KITTY_KEYBOARD_FLAGS = 8
DATA_SCROLLBAR = 9
DATA_TOTAL_ROWS = 14
DATA_SCROLLBACK_ROWS = 15
DATA_MODE = 37

# ---- Terminal screen (terminal.h:2466 GhosttyTerminalScreen) ---------------------------------
SCREEN_PRIMARY = 0
SCREEN_ALTERNATE = 1

# ---- Render state data (render.h:309 GhosttyRenderStateData) ---------------------------------
RS_DATA_COLS = 1
RS_DATA_ROWS = 2
RS_DATA_DIRTY = 3
RS_DATA_ROW_ITERATOR = 4
RS_DATA_CURSOR = 18

# ---- Render state row data (render.h:427 GhosttyRenderStateRowData) --------------------------
RS_ROW_DATA_DIRTY = 1
RS_ROW_DATA_CELLS = 3
RS_ROW_DATA_CELLS_RAW = 5

# ---- Render state row cells data (render.h:941 GhosttyRenderStateRowCellsData) ---------------
RS_CELL_DATA_RAW = 1
RS_CELL_DATA_STYLE = 2

# ---- Render state dirty (render.h:233 GhosttyRenderStateDirty) -------------------------------
RS_DIRTY_FALSE = 0
RS_DIRTY_PARTIAL = 1
RS_DIRTY_FULL = 2

# ---- Cell content / wide / style (screen.h:80, :102, style.h:49) -----------------------------
CELL_CONTENT_CODEPOINT = 0
CELL_CONTENT_CODEPOINT_GRAPHEME = 1
CELL_CONTENT_BG_COLOR_PALETTE = 2
CELL_CONTENT_BG_COLOR_RGB = 3

CELL_WIDE_NARROW = 0
CELL_WIDE_WIDE = 1
CELL_WIDE_SPACER_TAIL = 2
CELL_WIDE_SPACER_HEAD = 3

STYLE_COLOR_NONE = 0
STYLE_COLOR_PALETTE = 1
STYLE_COLOR_RGB = 2

# ---- Packed GhosttyCell bit layout (types.h manifest "GhosttyCell") --------------------------
# The manifest is authoritative; these mirror it and _check_manifest verifies them from
# ghostty_type_json() so a build that changes the packed layout refuses to load.
CELL_CONTENT_TAG_LSB = 0
CELL_CONTENT_TAG_WIDTH = 2
CELL_CODEPOINT_LSB = 2
CELL_CODEPOINT_WIDTH = 21
CELL_STYLE_ID_LSB = 26
CELL_STYLE_ID_WIDTH = 16
CELL_WIDE_LSB = 42
CELL_WIDE_WIDTH = 2

# ---- Scroll viewport tag (terminal.h:296 GhosttyTerminalScrollViewportTag) --------------------
SCROLL_VIEWPORT_TOP = 0
SCROLL_VIEWPORT_BOTTOM = 1
SCROLL_VIEWPORT_DELTA = 2
SCROLL_VIEWPORT_ROW = 3

# ---- Row data (screen.h:264 GhosttyRowData) -----------------------------------------------------
ROW_DATA_WRAP = 1  # the row is soft-wrapped into the next row

# ---- Point tag (point.h:49 GhosttyPointTag) --------------------------------------------------
POINT_ACTIVE = 0
POINT_VIEWPORT = 1
POINT_SCREEN = 2
POINT_HISTORY = 3

# ---- Key encoder options (key/encoder.h:93 GhosttyKeyEncoderOption) --------------------------
KEY_ENCODER_OPT_CURSOR_KEY_APPLICATION = 0
KEY_ENCODER_OPT_KEYPAD_KEY_APPLICATION = 1
KEY_ENCODER_OPT_ALT_ESC_PREFIX = 3
KEY_ENCODER_OPT_KITTY_FLAGS = 5
KEY_ENCODER_OPT_BACKARROW_KEY_MODE = 7

# ---- Kitty keyboard flags (key/encoder.h:34 GhosttyKittyKeyFlags) ----------------------------
KITTY_KEY_DISAMBIGUATE = 1 << 0
KITTY_KEY_REPORT_EVENTS = 1 << 1
KITTY_KEY_REPORT_ALTERNATES = 1 << 2
KITTY_KEY_REPORT_ALL = 1 << 3
KITTY_KEY_REPORT_ASSOCIATED = 1 << 4

# ---- Key actions (key/event.h:29 GhosttyKeyAction) -------------------------------------------
KEY_ACTION_PRESS = 1

# ---- Mods (key/event.h:56 GhosttyMods) -------------------------------------------------------
MODS_SHIFT = 1 << 0
MODS_CTRL = 1 << 1
MODS_ALT = 1 << 2

# ---- Focus (focus.h:38 GhosttyFocusEvent) ----------------------------------------------------
FOCUS_GAINED = 0
FOCUS_LOST = 1

# ---- Mouse (mouse/encoder.h, mouse/event.h) --------------------------------------------------
MOUSE_TRACKING_NONE = 0
MOUSE_TRACKING_X10 = 1
MOUSE_TRACKING_NORMAL = 2
MOUSE_TRACKING_BUTTON = 3
MOUSE_TRACKING_ANY = 4

MOUSE_FORMAT_X10 = 0
MOUSE_FORMAT_SGR = 2

MOUSE_ACTION_PRESS = 0
MOUSE_ACTION_RELEASE = 1
MOUSE_ACTION_MOTION = 2

MOUSE_BUTTON_UNKNOWN = 0
MOUSE_BUTTON_LEFT = 1
MOUSE_BUTTON_RIGHT = 2
MOUSE_BUTTON_MIDDLE = 3

MOUSE_ENCODER_OPT_EVENT = 0
MOUSE_ENCODER_OPT_FORMAT = 1
MOUSE_ENCODER_OPT_SIZE = 2


class AbiError(RuntimeError):
    """A declared struct or enum does not match the loaded library's ABI manifest."""


class LibraryError(RuntimeError):
    """The shared object at the given path could not be loaded."""


# ---- ctypes structures -----------------------------------------------------------------------
# Each mirrors a vendored header at the pin. `_check_manifest` verifies size and offsets from
# ghostty_type_json() after load, so a wrong field here fails loudly rather than corrupting memory.


class GhosttyString(ctypes.Structure):
    """types.h:268. A borrowed pointer + length."""

    _fields_ = [("ptr", ctypes.c_void_p), ("len", ctypes.c_size_t)]


class GhosttyBuffer(ctypes.Structure):
    """types.h:282. A caller-provided byte buffer."""

    _fields_ = [
        ("ptr", ctypes.POINTER(ctypes.c_ubyte)),
        ("cap", ctypes.c_size_t),
        ("len", ctypes.c_size_t),
    ]


class GhosttyCodepoints(ctypes.Structure):
    """types.h:317."""

    _fields_ = [("ptr", ctypes.POINTER(ctypes.c_uint32)), ("len", ctypes.c_size_t)]


class GhosttyColorRgb(ctypes.Structure):
    """color.h / types.h manifest. 3 bytes, align 1."""

    _fields_ = [
        ("r", ctypes.c_uint8),
        ("g", ctypes.c_uint8),
        ("b", ctypes.c_uint8),
    ]


class GhosttyStyleColor(ctypes.Structure):
    """style.h:77. A tagged union; value is 8 bytes at offset 8."""

    _fields_ = [("tag", ctypes.c_int), ("value", ctypes.c_uint64)]


class GhosttyStyle(ctypes.Structure):
    """style.h:94. The sized struct a cell's style_id resolves to."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("fg_color", GhosttyStyleColor),
        ("bg_color", GhosttyStyleColor),
        ("underline_color", GhosttyStyleColor),
        ("bold", ctypes.c_bool),
        ("italic", ctypes.c_bool),
        ("faint", ctypes.c_bool),
        ("blink", ctypes.c_bool),
        ("inverse", ctypes.c_bool),
        ("invisible", ctypes.c_bool),
        ("strikethrough", ctypes.c_bool),
        ("overline", ctypes.c_bool),
        ("underline", ctypes.c_int),
    ]


class GhosttyDeviceAttributesPrimary(ctypes.Structure):
    """terminal.h / types.h manifest. 144 bytes."""

    _fields_ = [
        ("conformance_level", ctypes.c_uint16),
        ("features", ctypes.c_uint16 * 64),
        ("num_features", ctypes.c_size_t),
    ]


class GhosttyDeviceAttributesSecondary(ctypes.Structure):
    """terminal.h / types.h manifest. 6 bytes."""

    _fields_ = [
        ("device_type", ctypes.c_uint16),
        ("firmware_version", ctypes.c_uint16),
        ("rom_cartridge", ctypes.c_uint16),
    ]


class GhosttyDeviceAttributesTertiary(ctypes.Structure):
    """terminal.h / types.h manifest. 4 bytes."""

    _fields_ = [("unit_id", ctypes.c_uint32)]


class GhosttyDeviceAttributes(ctypes.Structure):
    """terminal.h:1582, filled by the DEVICE_ATTRIBUTES effect. 160 bytes."""

    _fields_ = [
        ("primary", GhosttyDeviceAttributesPrimary),
        ("secondary", GhosttyDeviceAttributesSecondary),
        ("tertiary", GhosttyDeviceAttributesTertiary),
    ]


class GhosttyPointCoordinate(ctypes.Structure):
    """point.h:32."""

    _fields_ = [("x", ctypes.c_uint16), ("y", ctypes.c_uint32)]


class GhosttyPointValue(ctypes.Union):
    """point.h:61."""

    _fields_ = [("coordinate", GhosttyPointCoordinate), ("_padding", ctypes.c_uint64 * 2)]


class GhosttyPoint(ctypes.Structure):
    """point.h:70. Passed **by value** to ghostty_terminal_grid_ref (terminal.h:3257)."""

    _fields_ = [("tag", ctypes.c_int), ("value", GhosttyPointValue)]


class GhosttyGridRef(ctypes.Structure):
    """grid_ref.h:126. A sized struct."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("node", ctypes.c_void_p),
        ("x", ctypes.c_uint16),
        ("y", ctypes.c_uint16),
    ]


class GhosttyTerminalScrollViewportValue(ctypes.Union):
    """terminal.h:326. `delta` is signed (up is negative); `row` is an offset from the top of the scrollable area."""

    _fields_ = [("delta", ctypes.c_ssize_t), ("row", ctypes.c_size_t), ("_padding", ctypes.c_uint64 * 2)]


class GhosttyTerminalScrollViewport(ctypes.Structure):
    """terminal.h:343. Passed **by value** to ghostty_terminal_scroll_viewport (terminal.h:3121)."""

    _fields_ = [("tag", ctypes.c_int), ("value", GhosttyTerminalScrollViewportValue)]


class GhosttyTerminalScrollbar(ctypes.Structure):
    """terminal.h:392. What DATA_SCROLLBAR fills: the scrollable area's rows, the viewport's offset into it, and the
    viewport's own length in rows."""

    _fields_ = [("total", ctypes.c_uint64), ("offset", ctypes.c_uint64), ("len", ctypes.c_uint64)]


class GhosttyTerminalModeConfig(ctypes.Structure):
    """terminal.h:1823 / types.h manifest. 4 bytes."""

    _fields_ = [("mode", ctypes.c_uint16), ("value", ctypes.c_bool)]


class GhosttyRenderStateOverscan(ctypes.Structure):
    """render.h:275."""

    _fields_ = [("above", ctypes.c_uint16), ("below", ctypes.c_uint16)]


class GhosttyRenderStateRowId(ctypes.Structure):
    """render.h:299."""

    _fields_ = [("bits", ctypes.c_uint64 * 2)]


class GhosttyRenderStateCursor(ctypes.Structure):
    """render.h:522. A sized struct."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("viewport_has_value", ctypes.c_bool),
        ("viewport_x", ctypes.c_uint16),
        ("viewport_y", ctypes.c_uint16),
        ("wide_tail", ctypes.c_bool),
        ("visible", ctypes.c_bool),
        ("blinking", ctypes.c_bool),
        ("password_input", ctypes.c_bool),
        ("visual_style", ctypes.c_int),
    ]


class GhosttyCellsView(ctypes.Structure):
    """screen.h:65. The bulk read of a row's raw cells."""

    _fields_ = [("ptr", ctypes.POINTER(ctypes.c_uint64)), ("len", ctypes.c_size_t)]


class GhosttyFormatterScreenExtra(ctypes.Structure):
    """formatter.h:52. 16 bytes."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("cursor", ctypes.c_bool),
        ("style", ctypes.c_bool),
        ("hyperlink", ctypes.c_bool),
        ("protection", ctypes.c_bool),
        ("kitty_keyboard", ctypes.c_bool),
        ("charsets", ctypes.c_bool),
    ]


class GhosttyFormatterTerminalExtra(ctypes.Structure):
    """formatter.h:74. 32 bytes."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("palette", ctypes.c_bool),
        ("modes", ctypes.c_bool),
        ("scrolling_region", ctypes.c_bool),
        ("tabstops", ctypes.c_bool),
        ("pwd", ctypes.c_bool),
        ("keyboard", ctypes.c_bool),
        ("screen", GhosttyFormatterScreenExtra),
    ]


class GhosttyFormatterTerminalOptions(ctypes.Structure):
    """formatter.h:110. 56 bytes, passed **by value** to formatter_terminal_new (formatter.h:135)."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("emit", ctypes.c_int),
        ("unwrap", ctypes.c_bool),
        ("trim", ctypes.c_bool),
        ("extra", GhosttyFormatterTerminalExtra),
        ("selection", ctypes.c_void_p),
    ]


class GhosttyMouseEncoderSize(ctypes.Structure):
    """mouse/encoder.h:74. 40 bytes."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("screen_width", ctypes.c_uint32),
        ("screen_height", ctypes.c_uint32),
        ("cell_width", ctypes.c_uint32),
        ("cell_height", ctypes.c_uint32),
        ("padding_top", ctypes.c_uint32),
        ("padding_bottom", ctypes.c_uint32),
        ("padding_right", ctypes.c_uint32),
        ("padding_left", ctypes.c_uint32),
    ]


class GhosttyMousePosition(ctypes.Structure):
    """mouse/event.h:75. Two floats."""

    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


# ---- Effect callback types (terminal.h) ------------------------------------------------------
# WRITE_PTY: terminal.h:1798. DEVICE_ATTRIBUTES: terminal.h:1582.
WritePtyFn = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t
)
DeviceAttributesFn = ctypes.CFUNCTYPE(
    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GhosttyDeviceAttributes)
)
RenderHoldFn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)


# ---- The struct/enum declarations the manifest check verifies --------------------------------
# (manifest name, ctypes type) pairs. Size and every field offset must match ghostty_type_json().
_DECLARED_STRUCTS: tuple[tuple[str, type[ctypes.Structure]], ...] = (
    ("GhosttyString", GhosttyString),
    ("GhosttyBuffer", GhosttyBuffer),
    ("GhosttyCodepoints", GhosttyCodepoints),
    ("GhosttyColorRgb", GhosttyColorRgb),
    ("GhosttyStyleColor", GhosttyStyleColor),
    ("GhosttyStyle", GhosttyStyle),
    ("GhosttyDeviceAttributesPrimary", GhosttyDeviceAttributesPrimary),
    ("GhosttyDeviceAttributesSecondary", GhosttyDeviceAttributesSecondary),
    ("GhosttyDeviceAttributesTertiary", GhosttyDeviceAttributesTertiary),
    ("GhosttyDeviceAttributes", GhosttyDeviceAttributes),
    ("GhosttyPointCoordinate", GhosttyPointCoordinate),
    ("GhosttyPointValue", GhosttyPointValue),
    ("GhosttyPoint", GhosttyPoint),
    ("GhosttyGridRef", GhosttyGridRef),
    ("GhosttyTerminalScrollViewportValue", GhosttyTerminalScrollViewportValue),
    ("GhosttyTerminalScrollViewport", GhosttyTerminalScrollViewport),
    ("GhosttyTerminalScrollbar", GhosttyTerminalScrollbar),
    ("GhosttyTerminalModeConfig", GhosttyTerminalModeConfig),
    ("GhosttyRenderStateOverscan", GhosttyRenderStateOverscan),
    ("GhosttyRenderStateRowId", GhosttyRenderStateRowId),
    ("GhosttyRenderStateCursor", GhosttyRenderStateCursor),
    ("GhosttyCellsView", GhosttyCellsView),
    ("GhosttyFormatterScreenExtra", GhosttyFormatterScreenExtra),
    ("GhosttyFormatterTerminalExtra", GhosttyFormatterTerminalExtra),
    ("GhosttyFormatterTerminalOptions", GhosttyFormatterTerminalOptions),
    ("GhosttyMouseEncoderSize", GhosttyMouseEncoderSize),
    ("GhosttyMousePosition", GhosttyMousePosition),
)

# Enum constants used, grouped by the manifest enum they belong to. The value declared above must
# equal the manifest's.
_DECLARED_ENUMS: dict[str, dict[str, int]] = {
    "GhosttyTerminalOption": {
        "USERDATA": OPT_USERDATA,
        "WRITE_PTY": OPT_WRITE_PTY,
        "XTVERSION": OPT_XTVERSION,
        "COLOR_SCHEME": OPT_COLOR_SCHEME,
        "DEVICE_ATTRIBUTES": OPT_DEVICE_ATTRIBUTES,
        "KITTY_IMAGE_STORAGE_LIMIT": OPT_KITTY_IMAGE_STORAGE_LIMIT,
        "SCROLLBACK_MAX_BYTES": OPT_SCROLLBACK_MAX_BYTES,
        "UNKNOWN_MAX_BYTES": OPT_UNKNOWN_MAX_BYTES,
        "RENDER_HOLD": OPT_RENDER_HOLD,
    },
    "GhosttyTerminalData": {
        "CURSOR_X": DATA_CURSOR_X,
        "CURSOR_Y": DATA_CURSOR_Y,
        "ACTIVE_SCREEN": DATA_ACTIVE_SCREEN,
        "CURSOR_VISIBLE": DATA_CURSOR_VISIBLE,
        "KITTY_KEYBOARD_FLAGS": DATA_KITTY_KEYBOARD_FLAGS,
        "SCROLLBAR": DATA_SCROLLBAR,
        "TOTAL_ROWS": DATA_TOTAL_ROWS,
        "SCROLLBACK_ROWS": DATA_SCROLLBACK_ROWS,
        "MODE": DATA_MODE,
    },
    "GhosttyTerminalScreen": {"PRIMARY": SCREEN_PRIMARY, "ALTERNATE": SCREEN_ALTERNATE},
    "GhosttyTerminalScrollViewportTag": {
        "TOP": SCROLL_VIEWPORT_TOP,
        "BOTTOM": SCROLL_VIEWPORT_BOTTOM,
        "DELTA": SCROLL_VIEWPORT_DELTA,
        "ROW": SCROLL_VIEWPORT_ROW,
    },
    "GhosttyRenderStateData": {
        "COLS": RS_DATA_COLS,
        "ROWS": RS_DATA_ROWS,
        "DIRTY": RS_DATA_DIRTY,
        "ROW_ITERATOR": RS_DATA_ROW_ITERATOR,
        "CURSOR": RS_DATA_CURSOR,
    },
    "GhosttyRenderStateRowData": {
        "DIRTY": RS_ROW_DATA_DIRTY,
        "CELLS": RS_ROW_DATA_CELLS,
        "CELLS_RAW": RS_ROW_DATA_CELLS_RAW,
    },
    "GhosttyRenderStateRowCellsData": {
        "RAW": RS_CELL_DATA_RAW,
        "STYLE": RS_CELL_DATA_STYLE,
    },
    "GhosttyRenderStateDirty": {
        "FALSE": RS_DIRTY_FALSE,
        "PARTIAL": RS_DIRTY_PARTIAL,
        "FULL": RS_DIRTY_FULL,
    },
    "GhosttyCellContentTag": {
        "CODEPOINT": CELL_CONTENT_CODEPOINT,
        "CODEPOINT_GRAPHEME": CELL_CONTENT_CODEPOINT_GRAPHEME,
        "BG_COLOR_PALETTE": CELL_CONTENT_BG_COLOR_PALETTE,
        "BG_COLOR_RGB": CELL_CONTENT_BG_COLOR_RGB,
    },
    "GhosttyCellWide": {
        "NARROW": CELL_WIDE_NARROW,
        "WIDE": CELL_WIDE_WIDE,
        "SPACER_TAIL": CELL_WIDE_SPACER_TAIL,
        "SPACER_HEAD": CELL_WIDE_SPACER_HEAD,
    },
    "GhosttyStyleColorTag": {
        "NONE": STYLE_COLOR_NONE,
        "PALETTE": STYLE_COLOR_PALETTE,
        "RGB": STYLE_COLOR_RGB,
    },
    "GhosttyPointTag": {
        "ACTIVE": POINT_ACTIVE,
        "VIEWPORT": POINT_VIEWPORT,
        "SCREEN": POINT_SCREEN,
        "HISTORY": POINT_HISTORY,
    },
    "GhosttyKeyEncoderOption": {
        "CURSOR_KEY_APPLICATION": KEY_ENCODER_OPT_CURSOR_KEY_APPLICATION,
        "KEYPAD_KEY_APPLICATION": KEY_ENCODER_OPT_KEYPAD_KEY_APPLICATION,
        "ALT_ESC_PREFIX": KEY_ENCODER_OPT_ALT_ESC_PREFIX,
        "KITTY_FLAGS": KEY_ENCODER_OPT_KITTY_FLAGS,
        "BACKARROW_KEY_MODE": KEY_ENCODER_OPT_BACKARROW_KEY_MODE,
    },
    "GhosttyKeyAction": {"PRESS": KEY_ACTION_PRESS},
    "GhosttyFocusEvent": {"GAINED": FOCUS_GAINED, "LOST": FOCUS_LOST},
    "GhosttyMouseTrackingMode": {
        "NONE": MOUSE_TRACKING_NONE,
        "X10": MOUSE_TRACKING_X10,
        "NORMAL": MOUSE_TRACKING_NORMAL,
        "BUTTON": MOUSE_TRACKING_BUTTON,
        "ANY": MOUSE_TRACKING_ANY,
    },
    "GhosttyMouseFormat": {"X10": MOUSE_FORMAT_X10, "SGR": MOUSE_FORMAT_SGR},
    "GhosttyMouseAction": {
        "PRESS": MOUSE_ACTION_PRESS,
        "RELEASE": MOUSE_ACTION_RELEASE,
        "MOTION": MOUSE_ACTION_MOTION,
    },
    "GhosttyMouseButton": {
        "UNKNOWN": MOUSE_BUTTON_UNKNOWN,
        "LEFT": MOUSE_BUTTON_LEFT,
        "RIGHT": MOUSE_BUTTON_RIGHT,
        "MIDDLE": MOUSE_BUTTON_MIDDLE,
    },
    "GhosttyMouseEncoderOption": {
        "EVENT": MOUSE_ENCODER_OPT_EVENT,
        "FORMAT": MOUSE_ENCODER_OPT_FORMAT,
        "SIZE": MOUSE_ENCODER_OPT_SIZE,
    },
}

# The packed GhosttyCell bit fields we decode by hand. The manifest is authoritative; these are
# checked against it. Each entry: manifest bit name -> (declared lsb, declared width).
_CELL_BITS = {
    "content_tag": (CELL_CONTENT_TAG_LSB, CELL_CONTENT_TAG_WIDTH),
    "style_id": (CELL_STYLE_ID_LSB, CELL_STYLE_ID_WIDTH),
    "wide": (CELL_WIDE_LSB, CELL_WIDE_WIDTH),
}


@dataclass(frozen=True)
class BuildInfo:
    """The engine's compile-time build info (build_info.h)."""

    version_string: str
    version_major: int
    version_minor: int
    version_patch: int
    version_pre: str
    version_build: str
    simd: bool
    kitty_graphics: bool
    optimize: int


def _manifest_type(manifest: dict, name: str) -> dict:
    types = manifest.get("types", {})
    if name not in types:
        raise AbiError(f"the library's ABI manifest has no type {name!r}")
    return types[name]


def _check_struct(manifest: dict, name: str, cls: type[ctypes.Structure]) -> None:
    declared = _manifest_type(manifest, name)
    want_size = declared.get("size")
    got_size = ctypes.sizeof(cls)
    if want_size != got_size:
        raise AbiError(
            f"{name}: manifest size {want_size}, declared {got_size} — the ctypes declaration has "
            f"drifted from the library; refusing to load"
        )
    fields = declared.get("fields", {})
    declared_names = {name for name, _ in cls._fields_}
    for fname, fdesc in fields.items():
        if fname not in declared_names:
            # The manifest may hold fields this binding does not read; offsets of the ones we do
            # are what matter, and a leading field we omit would show as a size/offset mismatch.
            continue
        offset = getattr(cls, fname).offset
        if offset != fdesc.get("offset"):
            raise AbiError(
                f"{name}.{fname}: manifest offset {fdesc.get('offset')}, declared {offset} — "
                f"refusing to load"
            )


def _check_enum(manifest: dict, name: str, constants: dict[str, int]) -> None:
    declared = _manifest_type(manifest, name)
    values = declared.get("values")
    if values is None:
        raise AbiError(f"{name} is not an enum in the library's ABI manifest")
    for const, want in constants.items():
        if const not in values:
            raise AbiError(f"{name}.{const} is not in the library's ABI manifest — refusing to load")
        if values[const] != want:
            raise AbiError(
                f"{name}.{const}: manifest {values[const]}, declared {want} — refusing to load"
            )


def _check_cell_bits(manifest: dict) -> None:
    declared = _manifest_type(manifest, "GhosttyCell")
    bits = declared.get("bits")
    if bits is None:
        raise AbiError("GhosttyCell is not packed in the library's ABI manifest")
    for fname, (lsb, width) in _CELL_BITS.items():
        if fname not in bits:
            raise AbiError(f"GhosttyCell.{fname} is not in the library's ABI manifest")
        got = bits[fname]
        if got.get("lsb") != lsb or got.get("width") != width:
            raise AbiError(
                f"GhosttyCell.{fname}: manifest lsb/width {got.get('lsb')}/{got.get('width')}, "
                f"declared {lsb}/{width} — refusing to load"
            )


def _check_manifest(manifest: dict) -> None:
    """Refuse to load when a declared struct, enum or packed field drifts (plan R2)."""
    schema = manifest.get("schema")
    if schema != 1:
        raise AbiError(f"unknown ABI manifest schema {schema!r}; this binding speaks schema 1")
    for name, cls in _DECLARED_STRUCTS:
        _check_struct(manifest, name, cls)
    for name, constants in _DECLARED_ENUMS.items():
        _check_enum(manifest, name, constants)
    _check_cell_bits(manifest)


class GhosttyLib:
    """The loaded library: the functions the backend and encoders call, and the build info.

    Constructed only through :func:`load`, which takes the explicit `.so` path (DT2).
    """

    def __init__(self, lib: ctypes.CDLL, manifest: dict) -> None:
        self._lib = lib
        self.manifest = manifest
        self._declare_functions()
        self.build_info = self._read_build_info()

    # -- function prototypes (each cites the header that declares it) --

    def _declare_functions(self) -> None:
        L = self._lib
        # build_info.h:142
        L.ghostty_build_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        L.ghostty_build_info.restype = ctypes.c_int
        # types.h:410
        L.ghostty_type_json.argtypes = []
        L.ghostty_type_json.restype = ctypes.c_char_p
        # allocator.h:263
        L.ghostty_free.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        L.ghostty_free.restype = None
        # terminal.h:2866
        L.ghostty_terminal_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint16,
            ctypes.c_uint16,
        ]
        L.ghostty_terminal_new.restype = ctypes.c_int
        # terminal.h:2881
        L.ghostty_terminal_free.argtypes = [ctypes.c_void_p]
        L.ghostty_terminal_free.restype = None
        # terminal.h:2897
        L.ghostty_terminal_reset.argtypes = [ctypes.c_void_p]
        L.ghostty_terminal_reset.restype = None
        # terminal.h:2924
        L.ghostty_terminal_resize.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        L.ghostty_terminal_resize.restype = ctypes.c_int
        # terminal.h:2952
        L.ghostty_terminal_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_terminal_set.restype = ctypes.c_int
        # terminal.h:2977
        L.ghostty_terminal_vt_write.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        L.ghostty_terminal_vt_write.restype = None
        # terminal.h:3194
        L.ghostty_terminal_get.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_terminal_get.restype = ctypes.c_int
        # terminal.h:3121
        L.ghostty_terminal_scroll_viewport.argtypes = [ctypes.c_void_p, GhosttyTerminalScrollViewport]
        L.ghostty_terminal_scroll_viewport.restype = None
        # terminal.h:3257
        L.ghostty_terminal_grid_ref.argtypes = [
            ctypes.c_void_p,
            GhosttyPoint,
            ctypes.POINTER(GhosttyGridRef),
        ]
        L.ghostty_terminal_grid_ref.restype = ctypes.c_int
        # terminal.h:3288
        L.ghostty_terminal_grid_ref_track.argtypes = [
            ctypes.c_void_p,
            GhosttyPoint,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_terminal_grid_ref_track.restype = ctypes.c_int
        # grid_ref_tracked.h:37
        L.ghostty_tracked_grid_ref_free.argtypes = [ctypes.c_void_p]
        L.ghostty_tracked_grid_ref_free.restype = None
        # grid_ref_tracked.h:50
        L.ghostty_tracked_grid_ref_has_value.argtypes = [ctypes.c_void_p]
        L.ghostty_tracked_grid_ref_has_value.restype = ctypes.c_bool
        # grid_ref_tracked.h:79
        L.ghostty_tracked_grid_ref_point.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(GhosttyPointCoordinate),
        ]
        L.ghostty_tracked_grid_ref_point.restype = ctypes.c_int
        # grid_ref.h:124
        L.ghostty_grid_ref_cell.argtypes = [ctypes.POINTER(GhosttyGridRef), ctypes.POINTER(ctypes.c_uint64)]
        L.ghostty_grid_ref_cell.restype = ctypes.c_int
        # grid_ref.h:137
        L.ghostty_grid_ref_row.argtypes = [ctypes.POINTER(GhosttyGridRef), ctypes.POINTER(ctypes.c_uint64)]
        L.ghostty_grid_ref_row.restype = ctypes.c_int
        # grid_ref.h:162
        L.ghostty_grid_ref_graphemes.argtypes = [
            ctypes.POINTER(GhosttyGridRef),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_grid_ref_graphemes.restype = ctypes.c_int
        # screen.h:383. `row` is the packed GhosttyRow value.
        L.ghostty_row_get.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_row_get.restype = ctypes.c_int
        # render.h:601
        L.ghostty_render_state_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_render_state_new.restype = ctypes.c_int
        # render.h:614
        L.ghostty_render_state_free.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_free.restype = None
        # render.h:636
        L.ghostty_render_state_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        L.ghostty_render_state_update.restype = ctypes.c_int
        # render.h:702
        L.ghostty_render_state_clean.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_clean.restype = ctypes.c_int
        # render.h:719
        L.ghostty_render_state_get.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_render_state_get.restype = ctypes.c_int
        # render.h:784
        L.ghostty_render_state_row_iterator_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_render_state_row_iterator_new.restype = ctypes.c_int
        # render.h:795
        L.ghostty_render_state_row_iterator_free.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_row_iterator_free.restype = None
        # render.h:813
        L.ghostty_render_state_row_iterator_next.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_row_iterator_next.restype = ctypes.c_bool
        # render.h:838
        L.ghostty_render_state_row_iterator_next_dirty.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint16),
        ]
        L.ghostty_render_state_row_iterator_next_dirty.restype = ctypes.c_bool
        # render.h:859
        L.ghostty_render_state_row_get.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_render_state_row_get.restype = ctypes.c_int
        # render.h:932
        L.ghostty_render_state_row_cells_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_render_state_row_cells_new.restype = ctypes.c_int
        # render.h:1020
        L.ghostty_render_state_row_cells_next.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_row_cells_next.restype = ctypes.c_bool
        # render.h:1036
        L.ghostty_render_state_row_cells_select.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        L.ghostty_render_state_row_cells_select.restype = ctypes.c_int
        # render.h:1056
        L.ghostty_render_state_row_cells_get.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        L.ghostty_render_state_row_cells_get.restype = ctypes.c_int
        # render.h:1096
        L.ghostty_render_state_row_cells_free.argtypes = [ctypes.c_void_p]
        L.ghostty_render_state_row_cells_free.restype = None
        # formatter.h:135
        L.ghostty_formatter_terminal_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            GhosttyFormatterTerminalOptions,
        ]
        L.ghostty_formatter_terminal_new.restype = ctypes.c_int
        # formatter.h:211
        L.ghostty_formatter_format_alloc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_formatter_format_alloc.restype = ctypes.c_int
        # formatter.h:226
        L.ghostty_formatter_free.argtypes = [ctypes.c_void_p]
        L.ghostty_formatter_free.restype = None
        # key/encoder.h:132
        L.ghostty_key_encoder_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_key_encoder_new.restype = ctypes.c_int
        # key/encoder.h:144
        L.ghostty_key_encoder_free.argtypes = [ctypes.c_void_p]
        L.ghostty_key_encoder_free.restype = None
        # key/encoder.h:167
        L.ghostty_key_encoder_setopt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.ghostty_key_encoder_setopt.restype = None
        # key/encoder.h:186
        L.ghostty_key_encoder_setopt_from_terminal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        L.ghostty_key_encoder_setopt_from_terminal.restype = None
        # key/encoder.h:253
        L.ghostty_key_encoder_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_key_encoder_encode.restype = ctypes.c_int
        # key/event.h:315
        L.ghostty_key_event_new.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        L.ghostty_key_event_new.restype = ctypes.c_int
        # key/event.h:327
        L.ghostty_key_event_free.argtypes = [ctypes.c_void_p]
        L.ghostty_key_event_free.restype = None
        # key/event.h:337
        L.ghostty_key_event_set_action.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.ghostty_key_event_set_action.restype = None
        # key/event.h:357
        L.ghostty_key_event_set_key.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.ghostty_key_event_set_key.restype = None
        # key/event.h:377
        L.ghostty_key_event_set_mods.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        L.ghostty_key_event_set_mods.restype = None
        # key/event.h:397
        L.ghostty_key_event_set_consumed_mods.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        L.ghostty_key_event_set_consumed_mods.restype = None
        # key/event.h:447
        L.ghostty_key_event_set_utf8.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        L.ghostty_key_event_set_utf8.restype = None
        # key/event.h:470
        L.ghostty_key_event_set_unshifted_codepoint.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        L.ghostty_key_event_set_unshifted_codepoint.restype = None
        # paste.h:241
        L.ghostty_paste_encode.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_bool,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_paste_encode.restype = ctypes.c_int
        # focus.h:64
        L.ghostty_focus_encode.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_focus_encode.restype = ctypes.c_int
        # mouse/encoder.h:137
        L.ghostty_mouse_encoder_new.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        L.ghostty_mouse_encoder_new.restype = ctypes.c_int
        # mouse/encoder.h:147
        L.ghostty_mouse_encoder_free.argtypes = [ctypes.c_void_p]
        L.ghostty_mouse_encoder_free.restype = None
        # mouse/encoder.h:160
        L.ghostty_mouse_encoder_setopt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        L.ghostty_mouse_encoder_setopt.restype = None
        # mouse/encoder.h:175
        L.ghostty_mouse_encoder_setopt_from_terminal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        L.ghostty_mouse_encoder_setopt_from_terminal.restype = None
        # mouse/encoder.h:208
        L.ghostty_mouse_encoder_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        L.ghostty_mouse_encoder_encode.restype = ctypes.c_int
        # mouse/event.h:82
        L.ghostty_mouse_event_new.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        L.ghostty_mouse_event_new.restype = ctypes.c_int
        # mouse/event.h:92
        L.ghostty_mouse_event_free.argtypes = [ctypes.c_void_p]
        L.ghostty_mouse_event_free.restype = None
        # mouse/event.h:102
        L.ghostty_mouse_event_set_action.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.ghostty_mouse_event_set_action.restype = None
        # mouse/event.h:127
        L.ghostty_mouse_event_set_button.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.ghostty_mouse_event_set_button.restype = None
        # mouse/event.h:139
        L.ghostty_mouse_event_clear_button.argtypes = [ctypes.c_void_p]
        L.ghostty_mouse_event_clear_button.restype = None
        # mouse/event.h:161
        L.ghostty_mouse_event_set_mods.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        L.ghostty_mouse_event_set_mods.restype = None
        # mouse/event.h:182
        L.ghostty_mouse_event_set_position.argtypes = [ctypes.c_void_p, GhosttyMousePosition]
        L.ghostty_mouse_event_set_position.restype = None

    # -- build info --

    def _read_build_info(self) -> BuildInfo:
        def string(data: int) -> str:
            s = GhosttyString()
            if self._lib.ghostty_build_info(data, ctypes.byref(s)) != GHOSTTY_SUCCESS:
                return ""
            if not s.ptr or s.len == 0:
                return ""
            return ctypes.string_at(s.ptr, s.len).decode("utf-8", "replace")

        def size(data: int) -> int:
            v = ctypes.c_size_t()
            self._lib.ghostty_build_info(data, ctypes.byref(v))
            return int(v.value)

        def boolean(data: int) -> bool:
            v = ctypes.c_bool()
            self._lib.ghostty_build_info(data, ctypes.byref(v))
            return bool(v.value)

        optimize = ctypes.c_int()
        self._lib.ghostty_build_info(BUILD_INFO_OPTIMIZE, ctypes.byref(optimize))
        return BuildInfo(
            version_string=string(BUILD_INFO_VERSION_STRING),
            version_major=size(BUILD_INFO_VERSION_MAJOR),
            version_minor=size(BUILD_INFO_VERSION_MINOR),
            version_patch=size(BUILD_INFO_VERSION_PATCH),
            version_pre=string(BUILD_INFO_VERSION_PRE),
            version_build=string(BUILD_INFO_VERSION_BUILD),
            simd=boolean(BUILD_INFO_SIMD),
            kitty_graphics=boolean(BUILD_INFO_KITTY_GRAPHICS),
            optimize=int(optimize.value),
        )

    @property
    def lib(self) -> ctypes.CDLL:
        """The underlying CDLL, for functions the backend calls directly."""
        return self._lib


def load(path: str | Path) -> GhosttyLib:
    """Load libghostty-vt from the explicit path and verify the ABI manifest (DT2).

    Raises :class:`LibraryError` when the file cannot be loaded, and :class:`AbiError` when a
    declared struct, enum or packed field does not match the library's own manifest (plan R2).
    """
    so = Path(path)
    if not so.is_file():
        raise LibraryError(f"libghostty-vt not found at {so} (the `term.library` knob)")
    try:
        lib = ctypes.CDLL(str(so))
    except OSError as exc:  # unloadable: wrong architecture, missing glibc, not a shared object
        raise LibraryError(f"could not load libghostty-vt at {so}: {exc}") from exc
    lib.ghostty_type_json.argtypes = []
    lib.ghostty_type_json.restype = ctypes.c_char_p
    raw = lib.ghostty_type_json()
    if not raw:
        raise AbiError("the library returned no ABI manifest from ghostty_type_json()")
    manifest = json.loads(raw.decode("utf-8"))
    _check_manifest(manifest)
    return GhosttyLib(lib, manifest)


def declared_function_names() -> tuple[str, ...]:
    """Every ghostty_* function this binding declares, for the drift test to check headers."""
    return _FUNCTION_NAMES


# The explicit list of ghostty_* functions this binding declares, one per prototype above.
_FUNCTION_NAMES: tuple[str, ...] = (
    "ghostty_build_info",
    "ghostty_type_json",
    "ghostty_free",
    "ghostty_terminal_new",
    "ghostty_terminal_free",
    "ghostty_terminal_reset",
    "ghostty_terminal_resize",
    "ghostty_terminal_set",
    "ghostty_terminal_vt_write",
    "ghostty_terminal_get",
    "ghostty_terminal_scroll_viewport",
    "ghostty_terminal_grid_ref",
    "ghostty_terminal_grid_ref_track",
    "ghostty_tracked_grid_ref_free",
    "ghostty_tracked_grid_ref_has_value",
    "ghostty_tracked_grid_ref_point",
    "ghostty_grid_ref_cell",
    "ghostty_grid_ref_row",
    "ghostty_grid_ref_graphemes",
    "ghostty_row_get",
    "ghostty_render_state_new",
    "ghostty_render_state_free",
    "ghostty_render_state_update",
    "ghostty_render_state_clean",
    "ghostty_render_state_get",
    "ghostty_render_state_row_iterator_new",
    "ghostty_render_state_row_iterator_free",
    "ghostty_render_state_row_iterator_next",
    "ghostty_render_state_row_iterator_next_dirty",
    "ghostty_render_state_row_get",
    "ghostty_render_state_row_cells_new",
    "ghostty_render_state_row_cells_next",
    "ghostty_render_state_row_cells_select",
    "ghostty_render_state_row_cells_get",
    "ghostty_render_state_row_cells_free",
    "ghostty_formatter_terminal_new",
    "ghostty_formatter_format_alloc",
    "ghostty_formatter_free",
    "ghostty_key_encoder_new",
    "ghostty_key_encoder_free",
    "ghostty_key_encoder_setopt",
    "ghostty_key_encoder_setopt_from_terminal",
    "ghostty_key_encoder_encode",
    "ghostty_key_event_new",
    "ghostty_key_event_free",
    "ghostty_key_event_set_action",
    "ghostty_key_event_set_key",
    "ghostty_key_event_set_mods",
    "ghostty_key_event_set_consumed_mods",
    "ghostty_key_event_set_utf8",
    "ghostty_key_event_set_unshifted_codepoint",
    "ghostty_paste_encode",
    "ghostty_focus_encode",
    "ghostty_mouse_encoder_new",
    "ghostty_mouse_encoder_free",
    "ghostty_mouse_encoder_setopt",
    "ghostty_mouse_encoder_setopt_from_terminal",
    "ghostty_mouse_encoder_encode",
    "ghostty_mouse_event_new",
    "ghostty_mouse_event_free",
    "ghostty_mouse_event_set_action",
    "ghostty_mouse_event_set_button",
    "ghostty_mouse_event_clear_button",
    "ghostty_mouse_event_set_mods",
    "ghostty_mouse_event_set_position",
)
