"""Search over the rows the engine holds, for the scroll view.

A query is a literal string, not a pattern. Its case is smart: insensitive unless the query has a capital letter. It is
found in the physical rows the formatter prints (`VTBackend.grid_lines`), so a row's index is its row in the engine's
row space (the one the scrollbar and the viewport's ROW scroll use), and a match that a soft wrap breaks across two rows
is not found. One match is one occurrence, so a row can hold several; matches are ordered oldest first, by row and then
by column.

Formatting every row the engine holds is the cost of a search (up to the scrollback budget), so it is done once per query
and the result is kept while the engine's `generation` stays the same; a caller whose result is stale searches again, and
finds its place in the new rows by `reanchor`.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from .backend import VTBackend


@dataclass(frozen=True)
class Match:
    """One occurrence: its row in the engine's row space, and its characters [start, end) of that row's text."""

    row: int
    start: int
    end: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.row, self.start)


@dataclass(frozen=True)
class SearchResult:
    """What one query found, the engine `generation` whose rows it was found in, and those rows' text."""

    query: str
    generation: int
    matches: tuple[Match, ...]
    rows: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def at_or_before_row(self, row: int) -> int | None:
        """The index of the newest match on `row` or an older row, or None when every match is newer."""
        index = bisect_right(self.matches, row, key=lambda m: m.row) - 1
        return index if index >= 0 else None

    def before(self, key: tuple[int, int]) -> int | None:
        """The index of the newest match older than the one at `key` (row, start), or None."""
        index = bisect_left(self.matches, key, key=lambda m: m.key) - 1
        return index if index >= 0 else None

    def after(self, key: tuple[int, int]) -> int | None:
        """The index of the oldest match newer than the one at `key` (row, start), or None."""
        index = bisect_right(self.matches, key, key=lambda m: m.key)
        return index if index < len(self.matches) else None


def reanchor(rows: Sequence[str], row: int, text: str) -> int | None:
    """Where the row that had index `row` and this `text` is now, after the engine may have dropped its oldest rows.
    Dropping rows moves every survivor to a lower index and never to a higher one, so it is looked for at `row`, then
    below it, nearest first; None when no row at or below `row` has this text any more."""
    for index in range(min(row, len(rows) - 1), -1, -1):
        if rows[index] == text:
            return index
    return None


def pattern_for(query: str) -> re.Pattern[str]:
    """The query as a literal, case-insensitive unless it has a capital letter (the pattern's flag keeps every index
    in the row's own characters, which lower-casing the text would not for every script)."""
    return re.compile(re.escape(query), 0 if any(ch.isupper() for ch in query) else re.IGNORECASE)


def find_matches(rows: Sequence[str], query: str) -> list[Match]:
    """Every occurrence of `query` in `rows`, oldest first. An empty query matches nothing."""
    if not query:
        return []
    pattern = pattern_for(query)
    return [Match(row, m.start(), m.end()) for row, text in enumerate(rows) for m in pattern.finditer(text)]


def search(backend: VTBackend, query: str) -> SearchResult:
    """Format the rows the engine holds once and find `query` in them. The rows and the generation they belong to are
    read under one hold of the engine's lock, so no feed lands between them."""
    with backend.lock():
        generation = backend.generation
        rows = backend.grid_lines()
    return SearchResult(query, generation, tuple(find_matches(rows, query)), tuple(rows))
