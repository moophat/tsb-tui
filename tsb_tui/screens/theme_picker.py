"""The Theme picker (design B5): two lists, previewed as the highlight moves, in two layouts.

The session stays visible and undimmed beside the panel (the screen's background is transparent);
moving a highlight repaints the chrome or the terminal at once (`app.apply_look`), `⏎` keeps the
previewed look for this run (OPEN-T6 a), Esc puts back what was in use. The pick in use leads each
list, the rest follow alphabetically, user files marked `yours` (OPEN-T6 c); a user file that failed
to load is listed after its list, file and field (TT10), where the highlight cannot reach it. While a
preview is on, the status row says `· previewing …` (OPEN-T6 d1). Save, the filter, the toggle and
the footer come in phase 11.

Laptop: the panel fills the window's height in its rightmost 54 columns, both lists shown. Phone
(`is_compact`, TT20): the panel takes the lower half beside the rail and shows one list at a time;
its first line is the tab line `Chrome │ Terminal colours` with `Tab switches` at its right, and
`Tab` switches the tab with the focus (the mock, tools/mock-picker.py:141-142, 197-204). A window
that changes size re-lays the picker out with its highlights kept. The compact status row gives
`⏎ Use` and `Esc back` (mock :259-260; `s Save` comes with phase 10).

Keys: `↑↓` moves the highlight, `Tab` switches between the two lists (TT20 — the buttons take no
focus), `⏎` is Use, `Esc` puts it back; a click on `[ Use ]` / `[ Cancel ]` does the same. The action
table's keys for the picker's buttons are the printed forms the status row shows (scope `theme`).
"""

from __future__ import annotations

from typing import ClassVar

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.style import Style
from textual.widget import Widget
from textual.widgets import Button, Static

from .. import theme
from ..config import ConfigError
from ..model.actions import BY_ID
from ..theme_files import ChromeTheme, scheme_catalogue, theme_catalogue
from ..widgets.status_row import Hint, KeysTarget, is_compact

PANEL_WIDTH = 54  # the mock's panel width (tools/mock-picker.py:140-158)
INNER = PANEL_WIDTH - 4  # the border and the padding take two cells each side
NAME_COL = 4
SWATCH_BLOCK = 9  # the mock's scheme swatch block: a space, six dots, `a`, a space
GAP = 1  # cells between the swatch block and the note; the theme swatch's own trailing space counts too
MIN_NAME = 2  # the minimum name width before `…` (the orchestrator's default, 2026-10-16): one glyph plus the ellipsis; the swatch block keeps all of SWATCH_BLOCK
TABS_LEFT = "Chrome │ Terminal colours"  # the mock's phone tab line (tools/mock-picker.py:199-201)
TABS_RIGHT = "Tab switches"  # mock :202
TAB_SEP = TABS_LEFT.index("│")

THEME_HEAD = "rail · sidebar · status row · dialogs"


def display_order(items: list, current_key: str, key, id_key) -> list:
    """OPEN-T6 c (ruled): the pick in use first, then the rest alphabetically. `key` is the theme's
    id or the scheme's display name; `id_key` names the item for the comparison with `current_key`
    (a scheme is named by its slug, which is not its display name)."""
    used = [i for i in items if id_key(i) == current_key]
    rest = sorted((i for i in items if id_key(i) != current_key), key=key)
    return used + rest


def row_note(item) -> str:
    """The mock's note column (tools/mock-picker.py:198-237): a theme's variant, and `default` on
    terracotta; a scheme's variant and its `source.license`, or `TSB` for tsb-default; `yours` on a
    user file."""
    if isinstance(item, ChromeTheme):
        note = item.variant or ""
        if item.id == "terracotta":
            note = f"{note} · default" if note else "default"
    else:
        licence = "TSB" if item.slug == "tsb-default" else (item.source or {}).get("license", "")
        note = f"{item.variant} · {licence}" if licence else item.variant
    if item.yours:
        note = f"{note} · yours" if note else "yours"
    return note


def _truncate(text: str, width: int) -> str:
    """`text` shown in `width` cells, with `…` when it does not fit; `width` 0 shows nothing."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:width - 1] + "…"


def style(hexfg: str | None = None, hexbg: str | None = None, *, bold: bool = False) -> Style:
    """A Content style from the config's or a file's hex colours."""
    return Style(foreground=Color.parse(hexfg) if hexfg else None,
                 background=Color.parse(hexbg) if hexbg else None, bold=bold)


def swatch_text(item) -> str:
    """The swatch as text, each block ending with a space so the note never runs into it (the mock's
    `swatch_scheme` puts one at x+7): a theme ` ●◆▲a `, a scheme six dots in ANSI 1-6 and a bold `a`
    in its foreground, on its background."""
    return " ●◆▲a " if isinstance(item, ChromeTheme) else " ●●●●●●a "


def swatch_styles(item, swatch_col: int, defaults: dict[str, str]) -> list[tuple[int, Style]]:
    """The style of each swatch cell, keyed by its column, starting at `swatch_col` (the block's
    leading space). A theme's swatch paints its **resolved** roles: a role the file lacks is the packaged
    default, exactly as TT12 resolves it for the rest of the chrome — a partial theme never raises."""
    if isinstance(item, ChromeTheme):
        roles = {**defaults, **item.roles}
        surface = roles["surface"]
        cells = [style(hexbg=surface),
                 style(roles["accent"], surface),
                 style(roles["steel"], surface),
                 style(roles["warn"], surface),
                 style(roles["text"], surface, bold=True),
                 style(hexbg=surface)]
    else:
        background = item.background
        cells = [style(hexbg=background)] + [
            style(item.ansi[i], background) for i in range(1, 7)] + [
            style(item.foreground, background, bold=True),
            style(hexbg=background)]
    return [(swatch_col + i, cell) for i, cell in enumerate(cells)]


class PickerButton(Button):
    """The panel's button: a click target that never takes focus, so `Tab` moves between the two
    lists only (TT20)."""

    can_focus = False


class ThemeList(Widget):
    """One picker list: a row of Statics per item, `↑↓` moves the highlight, and the highlighted row
    scrolls into view. The rows' content is built by the picker; the items are fixed when the list is
    composed, so a file that failed to load is not in it and the highlight cannot reach it."""

    can_focus = True

    def __init__(self, picker, kind: str, *, list_id: str) -> None:
        super().__init__(id=list_id)
        self._picker = picker
        self._kind = kind

    def compose(self) -> ComposeResult:
        for _ in self._picker.items(self._kind):
            yield Static(markup=False)
        # TT10, ruling 3: a catalogue's broken-file lines sit inside this list's scroll region, after its
        # last row, so they scroll with it. They are not items, so `highlightable_rows()` excludes them and
        # the highlight never lands on one.
        cls = "broken-theme" if self._kind == "themes" else "broken-scheme"
        for _ in self._picker.broken(self._kind):
            yield Static(classes=cls, markup=False)

    def highlightable_rows(self) -> int:
        """The rows the highlight can reach: the items, not the broken-file lines after them."""
        return len(self._picker.items(self._kind))

    def on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.stop()
            self.move_highlight(-1)
        elif event.key == "down":
            event.stop()
            self.move_highlight(1)

    def move_highlight(self, delta: int) -> None:
        items = self._picker.items(self._kind)
        index = max(0, min(self._picker.highlight(self._kind) + delta, len(items) - 1))
        if index != self._picker.highlight(self._kind):
            self._picker.set_highlight(self._kind, index)


class ThemePicker(ModalScreen[None]):
    DEFAULT_CSS = """
    ThemePicker {
        background: transparent;
        align: right top;
    }
    #theme-panel {
        width: 54;
        border: round $frame;
        background: $surface;
        padding: 0 1;
        layout: vertical;
        border-title-color: $foreground;
        border-title-style: bold;
    }
    #theme-chrome-head, #theme-schemes-head, #theme-rule { height: 1; }
    #theme-tabs { height: 1; display: none; }
    #theme-themes { overflow-y: auto; }
    #theme-schemes { height: 1fr; overflow-y: auto; }
    #theme-buttons { height: 1; }
    #theme-buttons Button { height: 1; min-width: 0; }
    #theme_use { text-style: bold; }
    #theme_cancel { color: $text-muted; }
    """
    AUTO_FOCUS = "#theme-themes"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "use", "Use", show=False),
        Binding("escape", "cancel", "put back", show=False),
        Binding("tab", "next_tab", "switch list", show=False),
    ]

    def __init__(self, terminal) -> None:
        super().__init__()
        self._terminal = terminal
        cfg = terminal.app.cfg
        self._saved = cfg.look()  # what Esc puts back, sources included
        # The packaged default roles (TT12): a partial theme's swatch fills its missing roles from here,
        # as every other chrome reader does.
        self._default_roles = cfg.default_roles
        themes, bad_themes = theme_catalogue(cfg.user_dir)
        schemes, bad_schemes = scheme_catalogue(cfg.user_dir)
        self._themes = display_order(themes, cfg["theme.name"], lambda t: t.id, lambda t: t.id)
        self._schemes = display_order(schemes, cfg["theme.term.scheme"], lambda s: s.name,
                                      lambda s: s.slug)
        self._bad = {"themes": bad_themes, "schemes": bad_schemes}
        self._hl = {"themes": 0, "schemes": 0}  # each highlight sits on the row in use
        self._current = (cfg["theme.name"], cfg["theme.term.scheme"])  # the rows the ✓ marks
        self._compact = False  # set by `_layout` at mount and on every resize
        self._tab = "themes"  # the phone tab line's active list; follows the focus there
        # The ruled name column (display defect fix, 2026-10-16): as wide as the longest name in the
        # lists. A longer name is truncated with `…` only when the panel is too narrow for the row:
        # markers + name + swatch block + gap + the longest note.
        self._fit_columns(INNER)

    def _fit_columns(self, inner: int) -> None:
        """The name, swatch and note columns for a panel whose content is `inner` cells wide. The markers,
        the name's reserved width and the swatch block are never given up: the note gives cells first
        (truncated with `…` in `_row_content`), then the name, so no computed width is negative."""
        items = self._themes + self._schemes
        longest_name = max(len(i.id if isinstance(i, ChromeTheme) else i.name) for i in items)
        self._inner = inner
        room = max(inner - NAME_COL - SWATCH_BLOCK - GAP, 0)  # the cells left for the name and the note
        self._name_w = min(longest_name, room) if room >= MIN_NAME else MIN_NAME
        self._swatch_col = NAME_COL + self._name_w
        self._note_col = self._swatch_col + SWATCH_BLOCK + GAP

    # -- layout --

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-panel"):
            yield Static(id="theme-tabs", markup=False)
            yield Static(id="theme-chrome-head", markup=False)
            yield ThemeList(self, "themes", list_id="theme-themes")
            yield Static(id="theme-rule", markup=False)
            yield Static(id="theme-schemes-head", markup=False)
            yield ThemeList(self, "schemes", list_id="theme-schemes")
            with Horizontal(id="theme-buttons"):
                yield PickerButton(Content(f"[ {BY_ID['theme_use'].label} ]"), id="theme_use",
                                   variant="primary", compact=True)
                yield PickerButton(Content(f"[ {BY_ID['theme_cancel'].label} ]"), id="theme_cancel",
                                   compact=True)

    def on_mount(self) -> None:
        self._terminal.dialogs.append(self)
        self._layout()
        # The panel's children mount after this screen's `on_mount`, so the rows are filled once the
        # layout refresh has run; the status row needs no widgets, only the lists.
        self.call_after_refresh(self.fill_rows)
        self._terminal.refresh_status()

    def on_resize(self, event: events.Resize) -> None:
        """The window changed size: re-lay the picker out by the new width, keeping its highlights
        (`self._hl` is untouched) and its tab, then rebuild the rows for the new inner width."""
        self._layout()
        self.fill_rows()

    def _layout(self) -> None:
        """Laptop: the panel is the window's height less the status row's line. Phone (`is_compact`,
        TT20): the lower half of the area beside the rail, never covering it (mock :141-142), one list
        shown — the active tab's — with the tab line as the panel's first line."""
        cfg = self.app.cfg
        show = bool(cfg["status.show"])
        status_line = 1 if show and cfg["status.position"] == "bottom" else 0
        height = max(self.app.size.height - status_line, 1)
        compact = is_compact(cfg["view.chrome"], self.app.size.width)
        self._compact = compact
        panel = self.query_one("#theme-panel")
        if compact:
            self.styles.align = ("right", "bottom")
            width = self.app.size.width - int(cfg["rail.width"])
            panel.styles.width = width
            panel.styles.height = max(height // 2, 1)
            # The lower half of the area *above* the status row: anchored at the bottom with a margin
            # so its last line never sits on the row. (A per-side margin assignment does not take in
            # Textual 8.2.8; the whole Spacing tuple must be set.)
            panel.styles.margin = (0, 0, status_line, 0)
        else:
            # Laptop: the panel docks at the right edge, as `align: right top` in the CSS above and the
            # design B5 say. The app's own CSS (`Screen { align: center middle }`, app.py:155) outranks
            # this screen's CSS, so the docking is set here as the compact branch sets its own.
            self.styles.align = ("right", "top")
            width = PANEL_WIDTH
            panel.styles.width = width
            panel.styles.height = height
            panel.styles.margin = (1 if show and cfg["status.position"] == "top" else 0, 0, 0, 0)
        # The mock's top border: `Theme` at the left, `Esc puts it back` at the right
        # (tools/mock-picker.py:154-156). Textual draws one title Content across the border, inside
        # its own `─` padding, so the content is the border width less six cells.
        fill = width - 8 - len("Theme") - len("Esc puts it back")
        panel.border_title = (Content("Theme").stylize("b").pad_left(1).pad_right(fill)
                              + Content("Esc puts it back").stylize(
                                  style(theme.role_colours(cfg)["text_muted"])).pad_right(1))
        self._show_tab(self._tab)
        self._fit_columns(width - 4)

    def _show_tab(self, kind: str) -> None:
        """On the phone, show only `kind`'s list, its broken-file lines and the tab line; on the
        laptop, show everything as phase 8 composed it. The active list fills the panel's height."""
        self._tab = kind
        self.query_one("#theme-tabs").styles.display = "block" if self._compact else "none"
        for head in ("theme-chrome-head", "theme-schemes-head", "theme-rule"):
            self.query_one(f"#{head}").styles.display = "none" if self._compact else "block"
        for k in ("themes", "schemes"):
            show = not self._compact or k == kind
            w = self.query_one(f"#theme-{k}", ThemeList)
            w.styles.display = "block" if show else "none"
            if self._compact:
                # One list shows at a time, so it takes the panel's height and scrolls.
                w.styles.height = "1fr"
                w.styles.max_height = self.app.size.height
            elif k == "themes":
                # The chrome list shows at most 6 rows on the laptop layout and scrolls beyond that.
                w.styles.height = "auto"
                w.styles.max_height = 6
            else:
                # The schemes list keeps its current sizing.
                w.styles.height = "1fr"
        for cls, k in ((".broken-theme", "themes"), (".broken-scheme", "schemes")):
            for static in self.query(cls):
                static.styles.display = "block" if not self._compact or k == kind else "none"

    def action_next_tab(self) -> None:
        """`Tab` switches between the two lists (TT20); on the phone it switches the tab and the
        focus moves with it, so the newly shown list takes the keys."""
        kind = "schemes" if self._tab == "themes" else "themes"
        self._show_tab(kind)
        self.query_one(f"#theme-{kind}", ThemeList).focus()
        self.fill_rows()  # the tab line marks the active one, so it is redrawn with it

    # -- what it shows --

    def items(self, kind: str) -> list:
        return self._themes if kind == "themes" else self._schemes

    def broken(self, kind: str) -> list:
        """The catalogue's broken-file lines for `kind`, listed inside that list after its last row."""
        return self._bad[kind]

    def highlight(self, kind: str) -> int:
        return self._hl[kind]

    def set_highlight(self, kind: str, index: int) -> None:
        previous = self._hl[kind]
        self._hl[kind] = index
        if self._compact:
            # On the phone a highlight move is a tab change: the moved list is shown and takes the
            # focus, so its rows are on screen and `↑↓` keeps working on it.
            self._show_tab(kind)
            self.query_one(f"#theme-{kind}", ThemeList).focus()
        row = list(self.query_one(f"#theme-{kind}", ThemeList).children)[index]
        row.scroll_visible()
        if not self.preview():
            # A file that failed to load: the ▸ stays on the row that is previewed, so `_hl` keeps pointing
            # at the previous index. The next ↑/↓ moves from the visible row and the status note names what
            # is in fact previewed, not the broken row the highlight just tried to land on.
            self._hl[kind] = previous

    def preview(self) -> bool:
        """Apply the highlighted pair (B5). A file that changed on disk since the list was read is a
        toast naming it, and the preview stays as it was; returns whether the look applied cleanly."""
        theme = self._themes[self._hl["themes"]]
        scheme = self._schemes[self._hl["schemes"]]
        try:
            self.app.apply_look(theme.id, scheme.slug)
        except ConfigError as exc:
            self.app.toast(str(exc), title="not available", severity="warning")
            return False
        self.fill_rows()
        self._terminal.refresh_status()
        return True

    def fill_rows(self) -> None:
        """(Re)build every row, head, rule and broken-file line from the catalogues and the look in
        force: the markers' colours follow the previewed chrome, so this runs after each move."""
        roles = theme.role_colours(self.app.cfg)
        self._fill_tabs()
        self._fill_head("theme-chrome-head", "Chrome", THEME_HEAD)
        self._fill_head("theme-schemes-head", "Terminal colours", "")
        rule = Content("┄" * self._inner).stylize(style(roles["border_on_surface"]))
        self.query_one("#theme-rule", Static).update(rule)
        for kind, marks in (("themes", self._current[0]), ("schemes", self._current[1])):
            items = self.items(kind)
            rows = list(self.query_one(f"#theme-{kind}", ThemeList).children)
            for i, item in enumerate(items):
                key = item.id if kind == "themes" else item.slug
                rows[i].update(self._row_content(item, highlighted=i == self._hl[kind],
                                                 current=key == marks))
            for static, (_path, message) in zip(self.query(f".broken-{'theme' if kind == 'themes' else 'scheme'}"),
                                                self._bad[kind]):
                line = Content(message).stylize(style(roles["text_muted"]))
                static.update(line)

    def _fill_head(self, id_: str, title: str, right: str) -> None:
        # `Content.stylize` is immutable (it returns a new Content), so every style is chained here.
        head = Content(f"{title}{' ' * max(INNER - len(title) - len(right), 0)}{right}")
        muted = theme.role_colours(self.app.cfg)["text_muted"]
        head = head.stylize(style(muted, bold=True), 0, len(title))
        if right:
            head = head.stylize(style(muted), INNER - len(right), INNER)
        self.query_one(f"#{id_}", Static).update(head)

    def _row_content(self, item, *, highlighted: bool, current: bool) -> Content:
        """One row as the mock draws it (tools/mock-picker.py:183-195): `▸` in accent-hot on the
        highlighted row, whose background is accent-soft; `✓` in ok on the one in use; the name (a
        scheme's `name`, a theme's id) in the ruled column, truncated with `…` only when the panel is
        too narrow; the swatch block, which ends with a space so the note never runs into it; the
        note."""
        roles = theme.role_colours(self.app.cfg)
        name = _truncate(item.id if isinstance(item, ChromeTheme) else item.name, self._name_w)
        note = _truncate(row_note(item), max(self._inner - self._note_col, 0))
        swatch = swatch_text(item)
        plain = (("▸" if highlighted else " ") + " " + ("✓" if current else " ") + " "
                 + name.ljust(self._name_w) + swatch
                 + " " * (SWATCH_BLOCK - len(swatch) + GAP) + note)
        row = Content(plain.ljust(self._inner))
        if highlighted:
            row = row.stylize(style(hexbg=roles["accent_soft"]))
        row = row.stylize(style(roles["accent_hot"], roles["accent_soft"] if highlighted else None,
                                bold=True), 0, 1)
        if current:
            row = row.stylize(style(roles["ok"]), 2, 3)
        row = row.stylize(style(roles["text"], bold=highlighted), NAME_COL, NAME_COL + self._name_w)
        for start, cell in swatch_styles(item, self._swatch_col, self._default_roles):
            row = row.stylize(cell, start, start + 1)
        if self._note_col < self._inner:
            row = row.stylize(style(roles["text_muted"]), self._note_col, self._inner)
        return row

    def _fill_tabs(self) -> None:
        """The phone tab line (mock :199-204): `Chrome │ Terminal colours`, the active tab in
        `accent_hot` bold underlined and the other in `text_muted`, with `Tab switches` at the right.
        The laptop shows the heads instead, so this line is hidden there."""
        roles = theme.role_colours(self.app.cfg)
        inner = self._inner
        pad = max(inner - len(TABS_LEFT) - len(TABS_RIGHT), 0)
        line = Content(TABS_LEFT + " " * pad + TABS_RIGHT)
        line = line.stylize(style(roles["text_muted"]), 0, len(TABS_LEFT))
        line = line.stylize(style(roles["border_on_surface"]), TAB_SEP, TAB_SEP + 1)
        active = "Chrome" if self._tab == "themes" else "Terminal colours"
        start = 0 if self._tab == "themes" else TAB_SEP + 2
        line = line.stylize(style(roles["accent_hot"], bold=True) + Style(underline=True),
                            start, start + len(active))
        line = line.stylize(style(roles["text_muted"]), inner - len(TABS_RIGHT), inner)
        self.query_one("#theme-tabs", Static).update(line)

    # -- the keys and the buttons --

    def status_target(self) -> KeysTarget:
        """OPEN-T6 d1 (ruled): the note names what is previewed. Only the chrome differs: `previewing
        <theme id>`; only the scheme: `previewing <scheme name>`; both: `previewing <theme id> · <scheme
        name>`. A note naming the scheme while a chrome theme is being previewed sends the operator to
        the wrong list."""
        cfg = self.app.cfg
        previewed = []
        if cfg["theme.name"] != self._current[0]:
            previewed.append(cfg["theme.name"])
        if cfg["theme.term.scheme"] != self._current[1]:
            previewed.append(self._schemes[self._hl["schemes"]].name)
        note = f"previewing {' · '.join(previewed)}" if previewed else ""
        hints = ((Hint("⏎", "Use"), Hint("Esc", "back")) if self._compact else
                 (Hint("↑↓", "choose"), Hint("Tab", "chrome│colours"),
                  Hint("⏎", "Use"), Hint("Esc", "put back")))  # compact hints, mock :259-260
        return KeysTarget("theme", hints, note)

    def sync(self) -> None:
        """Redraw the marks: the terminal calls it on model changes, as for Who's here."""
        self.fill_rows()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "theme_use":
            self.action_use()
        elif event.button.id == "theme_cancel":
            self.action_cancel()

    def action_use(self) -> None:
        """OPEN-T6 a (ruled): `⏎` applies the highlighted pair and closes the picker only when it
        previews cleanly. A file that vanished or turned invalid since the preview is a toast naming the
        file and the field, the picker stays open, and the look in use stays as it was."""
        theme_id = self._themes[self._hl["themes"]].id
        scheme_slug = self._schemes[self._hl["schemes"]].slug
        try:
            self.app.apply_look(theme_id, scheme_slug)
        except ConfigError as exc:
            self.app.toast(str(exc), title="not available", severity="warning")
            return
        self._current = (theme_id, scheme_slug)
        self.fill_rows()
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Esc, or a click on Cancel: put back what was in use, sources included."""
        self.app.restore_look(self._saved)
        self.dismiss(None)

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()
