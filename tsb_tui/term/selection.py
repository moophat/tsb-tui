"""The scroll view's selection: whole rows of the engine's row space, and the text they send to the clipboard.

A selection is two row numbers (where it was started and where its loose end is) and the text the rows held when they
were selected. Copying reads the rows again and compares: text that moved since (the history trimmed, a row rewritten) is
never sent.
"""

from __future__ import annotations

from .backend import RowText, VTBackend


class Selection:
    """Rows `lo`..`hi` of the engine's row space, with the text each held when it was read."""

    def __init__(self, anchor: int, cursor: int) -> None:
        self.anchor = anchor
        self.cursor = cursor
        self.rows: dict[int, RowText] = {}

    @property
    def lo(self) -> int:
        return min(self.anchor, self.cursor)

    @property
    def hi(self) -> int:
        return max(self.anchor, self.cursor)

    def fit(self, backend: VTBackend) -> bool:
        """Keep the text of exactly the rows now selected: drop the rows left out, read the rows added. False when a row
        the selection covers cannot be read (the engine no longer holds it)."""
        lo, hi = self.lo, self.hi
        self.rows = {y: text for y, text in self.rows.items() if lo <= y <= hi}
        y = lo
        while y <= hi:
            if y in self.rows:
                y += 1
                continue
            end = y
            while end + 1 <= hi and end + 1 not in self.rows:
                end += 1
            for at, text in zip(range(y, end + 1), backend.row_texts(y, end), strict=True):
                if text is None:
                    return False
                self.rows[at] = text
            y = end + 1
        return True

    def held(self) -> list[RowText]:
        return [self.rows[y] for y in range(self.lo, self.hi + 1)]

    def read_for_copy(self, backend: VTBackend, join_wrapped: bool, limit: int) -> tuple[str, str]:
        """Read the selected rows again, oldest first, one at a time, and build their clipboard text: ("ok", the text),
        ("moved", "") when a row read is not the text it was selected with or the backend changed (bytes fed, a resize, a
        reset) at any point of the read, or ("over", "") as soon as the text so far is over `limit` bytes. It stops there,
        so it never reads more rows than `limit` bytes hold; the rows after the one it stops at are not read, and not
        compared. Each row is read under the lock on its own, so the feed can rewrite a row already read: the text built
        would then be a screen that never existed, which is why a change anywhere across the read is "moved"."""
        started = backend.generation
        lines = _Lines(join_wrapped)
        for y in range(self.lo, self.hi + 1):
            (row,) = backend.row_texts(y, y)
            if row is None or row != self.rows.get(y) or backend.changed_since(started):
                return "moved", ""
            lines.add(row, more=y < self.hi)
            if lines.size > limit:
                return "over", ""
        return ("moved", "") if backend.changed_since(started) else ("ok", lines.text())


def _utf8_size(text: str) -> int:
    """The bytes `text` takes in UTF-8: the one place the copy sizes text, so a row is sized once, as it is added."""
    return len(text.encode("utf-8"))


class _Lines:
    """Rows added one by one into the clipboard text, with the bytes it has so far. A row the engine marked as soft-wrapped
    into the next row, when that row is selected too and `join_wrapped` is on, runs on into it with no newline, its last
    cell kept (a space at the wrap is the text's). Every other line ends with a newline, with its trailing blanks
    trimmed; the last has none.

    A line still running on is kept as the fragments of its rows with a running byte count and the count of blanks
    ending it, so the work for a row does not grow with the rows before it: the line is joined once, when it ends, and
    a row is sized once."""

    def __init__(self, join_wrapped: bool) -> None:
        self._join = join_wrapped
        self._lines: list[str] = []
        self._finished = 0  # bytes of the finished lines and the newlines between them
        self._fragments: list[str] = []  # the rows of the line still running on into its next row
        self._bytes = 0  # their bytes, with the blanks that end them
        self._blanks = 0  # the blanks ending the fragments (one byte each), which the line's end trims

    def add(self, row: RowText, *, more: bool) -> None:
        """Add `row`; `more` says the next row is selected too."""
        text = row.text
        self._fragments.append(text)
        self._bytes += _utf8_size(text)
        trimmed = len(text.rstrip(" "))
        self._blanks = self._blanks + len(text) if trimmed == 0 else len(text) - trimmed
        if self._join and row.wrapped and more:
            return
        line = "".join(self._fragments).rstrip(" ")
        self._finished += self._bytes - self._blanks + (1 if self._lines else 0)
        self._lines.append(line)
        self._fragments, self._bytes, self._blanks = [], 0, 0

    @property
    def size(self) -> int:
        """The bytes of the text so far: never more than the whole text has, so over a limit now is over it at the end."""
        if not self._fragments:
            return self._finished
        return self._finished + (1 if self._lines else 0) + self._bytes - self._blanks

    def text(self) -> str:
        return "\n".join(self._lines)
