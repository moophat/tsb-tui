"""The Theme picker (design B5): two lists, previewed as the highlight moves, in two layouts.

The session stays visible and undimmed beside the panel (the screen's background is transparent);
moving a highlight repaints the chrome or the terminal at once (`app.apply_look`), `⏎` keeps the
previewed look for this run (OPEN-T6 a), Esc puts back what was in use. The pick in use leads each
list, the rest follow alphabetically, user files marked `yours` (OPEN-T6 c); a user file that failed
to load is listed after its list, file and field (TT10), where the highlight cannot reach it. While a
preview is on, the status row says `· previewing …` (OPEN-T6 d1). `s` saves the previewed look to
the per-login state file and closes the picker (phase 10). `v` cycles the scheme list's variant
filter dark→light→all (phase 11, OPEN-T6 b); `/` filters by name; both apply when the picker opens,
and the scheme in use is always listed first, whatever the filters say (review round 1 ruling, from
OPEN-T6 c); the laptop footer shows the highlighted scheme's metadata and contrast ratios.

Laptop: the panel fills the window's height in its rightmost 54 columns, both lists shown. Phone
(`is_compact`, TT20): the panel takes the lower half beside the rail and shows one list at a time;
its first line is the tab line `Chrome │ Terminal colours` with `Tab switches` at its right, and
`Tab` switches the tab with the focus (the mock, tools/mock-picker.py:141-142, 197-204). A window
that changes size re-lays the picker out with its highlights kept. The compact status row gives
`⏎ Use`, `s Save` and `Esc back` (mock :259-260).

Keys: `↑↓` moves the highlight, `Tab` switches between the two lists (TT20 — the buttons take no
focus), `⏎` is Use, `s` is Save as my default, `Esc` puts it back; `v` toggles variant filter;
`/` starts filter editing; a click on `[ Use ]` / `[ Save as my default ]` / `[ Cancel ]` does the
same. The action table's keys for the picker's buttons are the printed forms the status row shows
(scope `theme`).
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
        # Rows are composed for the full catalogue, not the filtered list at mount: `v` and `/`
        # change the item count after mount, and `fill_rows` blanks the rows past the filtered list.
        for _ in self._picker.full(self._kind):
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
        # When editing the filter, let the picker handle the keys instead
        if self._picker._editing_filter:
            return
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
    #theme-footer { height: 2; display: none; }
    #theme-buttons { height: 1; }
    #theme-buttons Button { height: 1; min-width: 0; }
    #theme_use { text-style: bold; }
    #theme_cancel { color: $text-muted; }
    """
    AUTO_FOCUS = "#theme-themes"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "use", "Use", show=False),
        Binding("s", "save", "Save", show=False),
        Binding("escape", "cancel", "put back", show=False),
        Binding("tab", "next_tab", "switch list", show=False),
        Binding("v", "toggle_variant", "toggle variant", show=False),
        Binding("slash", "start_filter", "filter", show=False),
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
        self._bad = {"themes": bad_themes, "schemes": bad_schemes}
        self._hl = {"themes": 0, "schemes": 0}  # each highlight sits on the row in use
        self._current = (cfg["theme.name"], cfg["theme.term.scheme"])  # the rows the ✓ marks
        self._compact = False  # set by `_layout` at mount and on every resize
        self._tab = "themes"  # the phone tab line's active list; follows the focus there
        # Phase 11: variant filter and name filter
        # OPEN-T6 b (ruled): starts on the variant of the chrome theme in use (the resolved variant in cfg.chrome).
        # Use cfg.chrome.variant (resolved, includes computed variant from bg), not raw catalogue entry.
        self._variant_filter = cfg.chrome.variant if cfg.chrome.variant else "dark"
        self._filter_text = ""  # the `/` filter text
        self._editing_filter = False  # whether the filter is being edited
        self._all_schemes = schemes  # all schemes before filtering
        # Both filters apply when the picker opens (review round 1, fix 2 as ruled): the list shown
        # matches the toggle, with the scheme in use always listed first.
        self._schemes = self._filtered_schemes(cfg)
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
            yield Static(id="theme-footer", markup=False)
            with Horizontal(id="theme-buttons"):
                yield PickerButton(Content(f"[ {BY_ID['theme_use'].label} ]"), id="theme_use",
                                   variant="primary", compact=True)
                yield PickerButton(Content("[ Save as my default ]"), id="theme_save",
                                   compact=True)
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

    def on_key(self, event: events.Key) -> None:
        """Handle filter editing keys (phase 11, sidebar.py:663-677 pattern)."""
        if not self._editing_filter:
            return
        key = event.key
        if key == "escape":
            # Esc while editing: clear and stop editing; the picker stays open
            event.stop()
            event.prevent_default()
            self._filter_text = ""
            self._editing_filter = False
            self._apply_filters()
        elif key == "enter":
            # ⏎: stop editing and keep the text
            event.stop()
            event.prevent_default()
            self._editing_filter = False
            self.fill_rows()
        elif key == "backspace":
            event.stop()
            event.prevent_default()
            if self._filter_text:
                self._filter_text = self._filter_text[:-1]
                self._apply_filters()
        elif event.is_printable and event.character:
            event.stop()
            event.prevent_default()
            self._filter_text += event.character
            self._apply_filters()

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

    def action_toggle_variant(self) -> None:
        """`v`: cycle the scheme list's variant filter dark→light→all→dark (phase 11 step 1)."""
        if self._variant_filter == "dark":
            self._variant_filter = "light"
        elif self._variant_filter == "light":
            self._variant_filter = "all"
        else:
            self._variant_filter = "dark"
        self._apply_filters()

    def action_start_filter(self) -> None:
        """`/`: start editing the scheme filter (phase 11 step 2)."""
        self._editing_filter = True
        self.fill_rows()

    def _filtered_schemes(self, cfg) -> list:
        """The scheme list with the variant and name filters applied (phase 11 steps 1-2, applied at
        open too). The scheme in use is always listed, first and marked ✓, whatever the filters say
        (review round 1 ruling, from OPEN-T6 c "each list shows the pick in use first"); every other
        scheme is filtered as the phase file says. The scheme in use is the ✓ row (`self._current[1]`),
        not the previewed cfg value, which moves with the highlight. A scheme in use not in the
        catalogue (an unknown name) cannot be listed."""
        current_slug = self._current[1]
        schemes = self._all_schemes
        if self._variant_filter != "all":
            schemes = [s for s in schemes if s.variant == self._variant_filter]
        if self._filter_text:
            text = self._filter_text.lower()
            schemes = [s for s in schemes if text in s.name.lower() or text in s.slug.lower()]
        if not any(s.slug == current_slug for s in schemes):
            current = next((s for s in self._all_schemes if s.slug == current_slug), None)
            if current is not None:
                schemes = [current] + schemes
        return display_order(schemes, current_slug, lambda s: s.name, lambda s: s.slug)

    def _apply_filters(self) -> None:
        """Apply variant filter and name filter to the scheme list (phase 11 steps 1-3). The highlight
        moves onto the first match if it was on a now-filtered row. With the scheme in use always
        listed, the list is never empty, so a filter that matches nothing else leaves the scheme in
        use as the only row and the highlight lands on it (B5: moving the highlight applies at once)."""
        cfg = self.app.cfg
        old_hl_slug = self._schemes[self._hl["schemes"]].slug if self._schemes and self._hl["schemes"] < len(self._schemes) else None
        self._schemes = self._filtered_schemes(cfg)
        if self._schemes:
            # Try to keep the same scheme highlighted; if it's gone, move to 0
            new_index = 0
            if old_hl_slug:
                try:
                    new_index = next(i for i, s in enumerate(self._schemes) if s.slug == old_hl_slug)
                except StopIteration:
                    pass
            self.set_highlight("schemes", new_index)
        # Rebuild rows (including the header with filter display)
        self.fill_rows()

    # -- what it shows --

    def items(self, kind: str) -> list:
        return self._themes if kind == "themes" else self._schemes

    def full(self, kind: str) -> list:
        """The rows composed for this list: the full catalogue count, so a filter that widens the
        list (`all`, or clearing `/`) has a row to paint on."""
        return self._themes if kind == "themes" else self._all_schemes

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
        """(Re)build every row, head, rule, footer and broken-file line from the catalogues and the look in
        force: the markers' colours follow the previewed chrome, so this runs after each move."""
        roles = theme.role_colours(self.app.cfg)
        self._fill_tabs()
        self._fill_head("theme-chrome-head", "Chrome", THEME_HEAD)
        self._fill_schemes_head()
        rule = Content("┄" * self._inner).stylize(style(roles["border_on_surface"]))
        self.query_one("#theme-rule", Static).update(rule)
        for kind, marks in (("themes", self._current[0]), ("schemes", self._current[1])):
            items = self.items(kind)
            rows = list(self.query_one(f"#theme-{kind}", ThemeList).children)
            for i, item in enumerate(items):
                key = item.id if kind == "themes" else item.slug
                rows[i].update(self._row_content(item, highlighted=i == self._hl[kind],
                                                 current=key == marks))
            # Rows past the filtered list (the catalogue is composed full, so a filter that widens
            # the list has a row to paint on): blank and hide them so they take no space.
            for row in rows[len(items):len(self.full(kind))]:
                row.update(Content(""))
                row.styles.display = "none"
            for i in range(len(items)):
                rows[i].styles.display = "block"
            for static, (_path, message) in zip(self.query(f".broken-{'theme' if kind == 'themes' else 'scheme'}"),
                                                self._bad[kind]):
                line = Content(message).stylize(style(roles["text_muted"]))
                static.update(line)
        self._fill_footer()

    def _fill_head(self, id_: str, title: str, right: str) -> None:
        # `Content.stylize` is immutable (it returns a new Content), so every style is chained here.
        head = Content(f"{title}{' ' * max(INNER - len(title) - len(right), 0)}{right}")
        muted = theme.role_colours(self.app.cfg)["text_muted"]
        head = head.stylize(style(muted, bold=True), 0, len(title))
        if right:
            head = head.stylize(style(muted), INNER - len(right), INNER)
        self.query_one(f"#{id_}", Static).update(head)

    def _fill_schemes_head(self) -> None:
        """Terminal colours header with filter and toggle. The right part reads
        `/ filter   dark│light│all`, with the active word in accent_hot bold underlined, others in
        text_muted. While editing, the filter is drawn as `/ text▏`. When the line is too long for
        the panel, the filter keeps as many of its last characters as fit (`/ …` + tail), the title
        shortens from the right, the toggle stays whole, and at least one cell separates the title
        from the filter."""
        roles = theme.role_colours(self.app.cfg)
        title = "Terminal colours"
        # Toggle and gap always stay whole; shorten the filter's tail, then the title from the right.
        toggle_parts = ["dark", "│", "light", "│", "all"]
        toggle_gap = "   "
        toggle = toggle_gap + "".join(toggle_parts)
        cursor = "▏" if self._editing_filter else ""
        text = self._filter_text

        # Build the filter display
        if self._editing_filter:
            raw = f"/ {text}▏"
        elif text:
            raw = f"/ {text}"
        else:
            raw = "/ filter"

        if len(title) + 1 + len(raw) + len(toggle) <= self._inner:
            display_title = title
            filter_display = raw
        else:
            if text:
                room = self._inner - len(toggle) - len(title) - 1 - len("/ …") - len(cursor)
                keep = min(max(room, 1), len(text))
                filter_display = "/ …" + text[-keep:] + cursor
            else:
                filter_display = raw
            if len(title) + 1 + len(filter_display) + len(toggle) > self._inner:
                t = self._inner - len(toggle) - 1 - len(filter_display)
                display_title = title[:t - 1] + "…" if t >= 2 else "…"
            else:
                display_title = title
        pad = max(self._inner - len(display_title) - len(filter_display) - len(toggle), 1)

        # Build the full line
        line = Content(display_title + " " * pad + filter_display + toggle)

        # Style the title
        line = line.stylize(style(roles["text_muted"], bold=True), 0, len(display_title))

        # Style the filter part
        filter_start = len(display_title) + pad
        filter_end = filter_start + len(filter_display)
        filter_color = roles["text_muted"] if not self._filter_text and not self._editing_filter else roles["text"]
        line = line.stylize(style(filter_color), filter_start, filter_end)

        # Style the toggle parts (active in accent_hot bold underlined, others in text_muted).
        # The styling starts after the three spaces that precede the words.
        toggle_start = filter_end + len(toggle_gap)
        pos = toggle_start
        for part in toggle_parts:
            if part == "│":
                line = line.stylize(style(roles["text_muted"]), pos, pos + 1)
                pos += 1
            else:
                if part == self._variant_filter:
                    line = line.stylize(style(roles["accent_hot"], bold=True) + Style(underline=True), 
                                      pos, pos + len(part))
                else:
                    line = line.stylize(style(roles["text_muted"]), pos, pos + len(part))
                pos += len(part)
        self.query_one("#theme-schemes-head", Static).update(line)

    def _fill_footer(self) -> None:
        """The footer (laptop only, phase 11 step 4): two lines under the scheme list, showing the
        highlighted scheme's name, author, license, and contrast ratios. OPEN-T6 d2 (ruled): shows
        the "▲ low on a phone" line when the lowest ANSI colour ratio is under 4.5:1."""
        footer_widget = self.query_one("#theme-footer", Static)
        # Show footer on laptop layout only
        if self._compact or not self._schemes:
            footer_widget.styles.display = "none"
            return
        footer_widget.styles.display = "block"
        scheme = self._schemes[self._hl["schemes"]]
        roles = theme.role_colours(self.app.cfg)
        # First line: name · author · license
        parts = [scheme.name]
        if scheme.slug == "tsb-default":
            parts.append("TSB")
        elif scheme.source:
            author = scheme.source.get("author", "")
            license_text = scheme.source.get("license", "")
            if author:
                parts.append(author)
            if license_text:
                parts.append(license_text)
        line1 = " · ".join(parts)
        # Second line: contrast ratios
        text_contrast = theme.contrast_ratio(scheme.foreground, scheme.background)
        # Lowest of ANSI 1-7, 9-15 on background
        ansi_indices = list(range(1, 8)) + list(range(9, 16))
        lowest_contrast = min(theme.contrast_ratio(scheme.ansi[i], scheme.background) for i in ansi_indices)
        line2 = f"text {text_contrast:.2f}:1 on its bg · lowest colour {lowest_contrast:.2f}:1"
        # OPEN-T6 d2 (ruled): the warning keys on the scheme's LOWEST ANSI colour ratio under the
        # ruled 4.5:1, not the text ratio; the mock's line (tools/mock-picker.py:242-243) is drawn
        # as a third footer line, so the footer grows to three lines only when it shows.
        lines = [line1, line2]
        if lowest_contrast < 4.5:
            lines.append("▲ low on a phone: minimum_contrast can lift it")
        footer_widget.styles.height = len(lines)
        content = Content("\n".join(lines)).stylize(style(roles["text_muted"]))
        footer_widget.update(content)

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
        if cfg["theme.term.scheme"] != self._current[1] and self._schemes and self._hl["schemes"] < len(self._schemes):
            previewed.append(self._schemes[self._hl["schemes"]].name)
        note = f"previewing {' · '.join(previewed)}" if previewed else ""
        hints = ((Hint("⏎", "Use"), Hint("s", "Save"), Hint("Esc", "back")) if self._compact else
                 (Hint("↑↓", "choose"), Hint("Tab", "chrome│colours"),
                  Hint("⏎", "Use"), Hint("s", "Save"), Hint("Esc", "put back")))  # compact hints, mock :259-260
        return KeysTarget("theme", hints, note)

    def sync(self) -> None:
        """Redraw the marks: the terminal calls it on model changes, as for Who's here."""
        self.fill_rows()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "theme_use":
            self.action_use()
        elif event.button.id == "theme_save":
            self.action_save()
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

    def action_save(self) -> None:
        """Save the previewed look to the per-login state file (phase 10, TT16–TT18). If the state file
        was unreadable at login or is now, a toast and nothing is written. Otherwise write the previewed
        names with source `saved for <login>`, toast the login, and close the picker (OPEN-T6 a). When
        the config file, TSB_TUI_* or --set set either name before the picker opened, the toast names
        that layer (TT18). An OSError is toasted naming the path and the error, and the look stays.

        On persistence failure, restore_look rolls back the applied look, the ✓ stays on the pick in
        use before Save, the status row still says '· previewing', and the picker stays open."""
        from .. import saved_theme

        app = self.app
        if app._saved_theme_unreadable:
            self.app.toast(f"cannot save: {app._saved_theme_path} could not be read; fix or remove it",
                           title="cannot save", severity="warning")
            return
        theme_id = self._themes[self._hl["themes"]].id
        scheme_slug = self._schemes[self._hl["schemes"]].slug

        # Capture the look before applying, so we can roll back on failure.
        before = app.cfg.look()
        old_current = self._current

        # Apply the previewed names first, so they are in force with the right source before writing.
        try:
            app.apply_look(theme_id, scheme_slug, source=f"saved for {app.username}")
        except ConfigError as exc:
            app.toast(str(exc), title="not available", severity="warning")
            return
        self._current = (theme_id, scheme_slug)
        self.fill_rows()
        # Write to the state file.
        path = saved_theme.state_path()
        try:
            saved_theme.save(path, app.username, theme_id, scheme_slug)
        except saved_theme.SavedThemeError:
            # Rollback on persistence failure: save() propagates read errors if the file became unreadable since login.
            app._saved_theme_unreadable = True
            app.cfg.restore_look(before)
            self._current = old_current
            app._push_look()
            self.fill_rows()
            app.toast(f"cannot save: {path} could not be read; fix or remove it",
                       title="cannot save", severity="warning")
            return
        except OSError as exc:
            # Rollback on persistence failure.
            app.cfg.restore_look(before)
            self._current = old_current
            app._push_look()
            self.fill_rows()
            app.toast(f"{path}: {exc}", title="cannot save", severity="warning")
            return
        # Saved. Toast the login, and mention a higher layer if one wins at the next start (TT18): the sources
        # as they stood when the picker opened (`self._saved`), not after apply_look set `saved for <login>`.
        higher = []
        for src in (self._saved["name"][1], self._saved["scheme_name"][1]):
            if src.startswith("config file"):
                higher.append("config file")
            elif src.startswith("TSB_TUI_"):
                higher.append("environment")
            elif src == "--set":
                higher.append("--set")
        note = f" ({', '.join(sorted(set(higher)))} wins at next start)" if higher else ""
        app.toast(f"Saved for {app.username}{note}", title="saved theme")
        self.dismiss(None)

    def on_unmount(self) -> None:
        if self in self._terminal.dialogs:
            self._terminal.dialogs.remove(self)
        self._terminal.refresh_status()
