"""The targets model (design F14, E3 phase 11): the `TargetRecord`s the Targets tab shows, the query that fetched
them, and the one function every target string passes before a widget sees it. No Textual, no I/O.

Rules this module owns:

  * **The query is the broker's.** `type:`, `tag:` (repeatable), `group:` and `source:` become the filters of
    `GET /targets` and nothing is matched on the client; a word with no `field:` is refused with the field list,
    because `GET /targets` has no text search and a partial client-side match over the rows fetched so far would
    answer a different question than the broker's.
  * **A stale reply never overwrites a newer one**, as `model/fleet.py` does for `GET /sessions`: each request
    carries a generation, and a reply is applied only when it answers the newest request — for the query now
    wanted, not the last one that happened to land.
  * **A page at a time.** The tab asks for `PAGE_LIMIT` targets and the next page on demand; while `next_cursor`
    is present the count reads `N+`.
  * **Target strings are text** (M11, R17): `as_text` replaces every C0 and C1 control character, ESC included,
    before any widget sees a string. Rich keeps ESC in `Text.plain`, so turning markup off is not enough.
  * **An unknown target type is shown as it is**, never an error (conventions V5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# One page of `GET /targets`. The contract bounds `limit` to 1..500; the TUI asks for the biggest page.
PAGE_LIMIT = 500
MAX_LIMIT = 500

FIELDS = ("type", "tag", "group", "source")
FIELD_LIST = ", ".join(f"{name}:" for name in FIELDS)

# The Unicode control pictures (U+2400 block) stand for C0 and DEL. The block has no picture for a C1
# control, so those show the replacement character: a C1 byte is as unreadable as an escape.
_PICTURE_BASE = 0x2400
_DEL_PICTURE = "␡"
_REPLACEMENT = "�"


def _replacement(char: str) -> str | None:
    code = ord(char)
    if code < 0x20:
        return chr(_PICTURE_BASE + code)
    if code == 0x7F:
        return _DEL_PICTURE
    if 0x80 <= code <= 0x9F:
        return _REPLACEMENT
    return None


def as_text(value: Any) -> str:
    """`value` as a string no terminal can take for a command: each C0 and C1 control character, ESC included, is
    replaced by its control picture (`␛` for ESC; a C1 control by U+FFFD). Printable text, markup brackets and
    non-ASCII letters are left as they are: a widget renders the result with markup off."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if text.isprintable():
        return text
    return "".join(_replacement(char) or char for char in text)


class QueryError(ValueError):
    """A query the TUI refuses before it asks the broker; the message is shown in the query line."""


@dataclass(frozen=True)
class Query:
    """The filters of `GET /targets`, each an exact match on the index."""

    type: str | None = None
    tags: tuple[str, ...] = ()
    group: str | None = None
    discovered_by: str | None = None

    def filters(self) -> dict[str, Any]:
        """The keyword arguments of `Client.list_targets` for this query (without `limit` and `cursor`)."""
        return {"type": self.type, "tags": self.tags, "group": self.group, "discovered_by": self.discovered_by}


def parse_query(text: str) -> Query:
    """`type:telnet tag:a tag:b group:g source:s` as a `Query`. Only `tag` may be repeated. A word with no `field:`
    prefix, an unknown field, an empty value or a repeated single-valued field is a `QueryError`."""
    values: dict[str, str] = {}
    tags: list[str] = []
    for word in text.split():
        name, colon, value = word.partition(":")
        if not colon:
            raise QueryError(f"'{word}': write field:value, with one of {FIELD_LIST}")
        if name not in FIELDS:
            raise QueryError(f"'{name}:' is not a field: use one of {FIELD_LIST}")
        if not value:
            raise QueryError(f"'{name}:' needs a value")
        if name == "tag":
            tags.append(value)
        elif name in values:
            raise QueryError(f"'{name}:' is given twice: only tag: can be repeated")
        else:
            values[name] = value
    return Query(type=values.get("type"), tags=tuple(tags), group=values.get("group"),
                 discovered_by=values.get("source"))


def natural_key(text: str) -> tuple:
    """The natural order of a label (ruled 2026-11-20): case-insensitive, and a run of digits compares as the
    number it spells, so `rtr-2` comes before `rtr-10`. A numeric run is a run of decimal characters — the ones
    `int()` parses; a character like `²` counts as digits is text, never a number."""
    runs: list[tuple[int, object]] = []
    run = ""
    digits = False
    for char in text:
        if char.isdecimal() == digits:
            run += char
        else:
            runs.append((1 if digits else 0, int(run) if digits else run.lower()))
            run, digits = char, char.isdecimal()
    runs.append((1 if digits else 0, int(run) if digits else run.lower()))
    return tuple(runs)


@dataclass(frozen=True)
class TargetRow:
    """One `TargetRecord` for the tab. `id` is the record's own, for the API; every other string went through
    `as_text`, and `label` is the id as text, for the screen. `has_name` says the record carried a `name` (the
    row grammar's label falls back name, then host, then the type word and the id)."""

    id: str
    label: str
    type: str
    name: str
    has_name: bool
    group: str
    tags: tuple[str, ...]
    discovered_by: str
    host: str
    port: str
    last_seen: str


def make_row(record: Mapping[str, Any]) -> TargetRow:
    """A row from a `TargetRecord`: a type it does not know is kept as it is (V5), a missing name is the id."""
    target_id = str(record.get("id", ""))
    label = as_text(target_id)
    name = as_text(record.get("name"))
    tags: Iterable[Any] = record.get("tags") or ()
    return TargetRow(
        id=target_id, label=label, type=as_text(record.get("type")), name=name or label, has_name=bool(name),
        group=as_text(record.get("group")), tags=tuple(as_text(tag) for tag in tags),
        discovered_by=as_text(record.get("discovered_by")), host=as_text(record.get("host")),
        port=as_text(record.get("port")), last_seen=as_text(record.get("last_seen")))


@dataclass
class TargetsModel:
    """The rows on show with the query that fetched them. `begin()` before each request, `apply()` with its reply."""

    query: Query = field(default_factory=Query)
    wanted: Query = field(default_factory=Query)  # the query now asked for, before its first page lands
    rows: tuple[TargetRow, ...] = ()
    records: dict[str, Mapping[str, Any]] = field(default_factory=dict)  # each row's raw `TargetRecord`
    next_cursor: str | None = None
    fetched: bool = False  # a page has been applied: the count means something
    unavailable: bool = False  # 503 inventory_unavailable: the tab says so
    _generation: int = 0
    _applied: int = 0

    def begin(self, query: Query | None = None) -> int:
        """A generation for the request about to be made; a query passed is the one now wanted. A refresh's
        `reset` leaves a gap so a page sent before it can never read as the newest request (rule 2)."""
        if query is not None:
            self.wanted = query
        self._generation += 1
        return self._generation

    def revert_to_shown(self) -> Query:
        """A refused query (`invalid_request`) answers the newest request and takes nothing away: the rows stay, so
        the query wanted goes back to the one they were fetched with. Paging and polls then ask for the shown query,
        never the refused one."""
        self.wanted = self.query
        return self.wanted

    def is_current(self, generation: int) -> bool:
        """Whether a reply answers the newest request: only that one may change what the tab shows. A page sent
        while an older request was the newest is stale even after that request landed (rule 2, 2026-11-21): a
        reply never applies below the newest applied one."""
        return generation == self._generation and generation >= self._applied

    def apply(self, generation: int, query: Query, page: Mapping[str, Any], *, append: bool = False) -> bool:
        """Show a page. False unless the reply answers the newest request; a next page of a query that is no
        longer the one on show is refused too (the rows must never mix two queries)."""
        if not self.is_current(generation):
            return False
        if append and query != self.query:
            return False
        fresh = tuple(make_row(record) for record in page.get("targets") or ())
        self._applied = generation
        self.rows = (*self.rows, *fresh) if append else fresh
        if append:
            self.records.update({row.id: record for row, record in zip(fresh, page.get("targets") or ())})
        else:
            self.records = {row.id: record for row, record in zip(fresh, page.get("targets") or ())}
        self.query = query
        self.next_cursor = page.get("next_cursor") or None
        self.fetched = True
        self.unavailable = False
        return True

    def mark_unavailable(self, generation: int) -> bool:
        """The inventory answered 503: the rows go, the tab says so. Subject to the same stale rule."""
        if not self.is_current(generation):
            return False
        self._applied = generation
        self.rows = ()
        self.records = {}
        self.next_cursor = None
        self.fetched = True
        self.unavailable = True
        return True

    def reset(self) -> None:
        """The view opened afresh: no rows, no query, and the generation moves past every request already sent,
        so no earlier reply can be applied after it. A page sent before the refresh is stale by rule 2 (2026-11-21)
        even if its generation is the newest number, so the applied floor sits above every request in flight."""
        self.query = Query()
        self.wanted = Query()
        self.rows = ()
        self.records = {}
        self.next_cursor = None
        self.fetched = False
        self.unavailable = False
        self._generation += 2
        self._applied = self._generation

    def count_label(self) -> str | None:
        """The tab's count: `412`, or `500…` while pages are still arriving; None before the first page."""
        if not self.fetched or self.unavailable:
            return None
        return f"{len(self.rows)}…" if self.next_cursor else str(len(self.rows))

    def row(self, target_id: str | None) -> TargetRow | None:
        return next((row for row in self.rows if row.id == target_id), None)

    def record(self, target_id: str | None) -> Mapping[str, Any] | None:
        """The raw `TargetRecord` of a shown row, for the details view; None when the row is not on show."""
        return self.records.get(target_id) if target_id else None

    def request_limit(self) -> int:
        """The `limit` of a refresh: it re-reads as many rows as the operator has paged through, up to the contract's
        bound, so a poll does not fold the list back to its first page."""
        return min(max(len(self.rows), PAGE_LIMIT), MAX_LIMIT)


__all__ = ["FIELDS", "FIELD_LIST", "MAX_LIMIT", "PAGE_LIMIT", "Query", "QueryError", "TargetRow", "TargetsModel",
           "as_text", "make_row", "natural_key", "parse_query"]
