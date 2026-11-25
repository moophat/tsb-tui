"""Go's `path.Match`, ported, for a grant's `host:pattern` selector (Storage §9, reused verbatim).

The core's RBAC filter reads `host:pattern` with Go's `path.Match` over a target's id and then its host
(`core/internal/rbac/rbac.go`, `matches`), and so does a grant's selector, so the fixture server must match as Go
does and not as `fnmatch` does: a `*` or `?` never crosses a `/`; a class is `[...]` or `[^...]` of characters and
`lo-hi` ranges, with `\\` escaping one character; a pattern malformed in any way is `BadPattern`, which the filter reads
as no match. `tests/test_gopath.py` holds Go 1.22.2's own answers.

Names are text, so a "character" is a code point, as a rune is in Go.
"""

from __future__ import annotations


class BadPattern(ValueError):
    """Go's `path.ErrBadPattern`."""


def _scan_chunk(pattern: str) -> tuple[bool, str, str]:
    """Go's `scanChunk`: leading stars, then the chunk up to the next star outside a class."""
    star = False
    while pattern[:1] == "*":
        pattern = pattern[1:]
        star = True
    inrange = False
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            if i + 1 < len(pattern):
                i += 1
        elif c == "[":
            inrange = True
        elif c == "]":
            inrange = False
        elif c == "*" and not inrange:
            break
        i += 1
    return star, pattern[:i], pattern[i:]


def _get_esc(chunk: str) -> tuple[str, str]:
    """Go's `getEsc`: one class character, possibly escaped, and the rest of the chunk."""
    if not chunk or chunk[0] in "-]":
        raise BadPattern
    if chunk[0] == "\\":
        chunk = chunk[1:]
        if not chunk:
            raise BadPattern
    rest = chunk[1:]
    if not rest:
        raise BadPattern
    return chunk[0], rest


def _match_chunk(chunk: str, s: str) -> tuple[str, bool]:
    """Go's `matchChunk`: does `chunk` match a prefix of `s`; the rest of `s` when it does. After a mismatch it
    keeps reading `chunk`, so a malformed pattern is found whether or not the name matched."""
    failed = False
    while chunk:
        if not failed and not s:
            failed = True
        head = chunk[0]
        if head == "[":
            r = ""
            if not failed:
                r, s = s[0], s[1:]
            chunk = chunk[1:]
            negated = chunk[:1] == "^"
            if negated:
                chunk = chunk[1:]
            matched = False
            nrange = 0
            while True:
                if chunk[:1] == "]" and nrange > 0:
                    chunk = chunk[1:]
                    break
                lo, chunk = _get_esc(chunk)
                hi = lo
                if chunk[0] == "-":
                    hi, chunk = _get_esc(chunk[1:])
                if not failed and lo <= r <= hi:
                    matched = True
                nrange += 1
            if matched == negated:
                failed = True
        elif head == "?":
            if not failed:
                if s[0] == "/":
                    failed = True
                s = s[1:]
            chunk = chunk[1:]
        else:
            if head == "\\":
                chunk = chunk[1:]
                if not chunk:
                    raise BadPattern
            if not failed:
                if chunk[0] != s[0]:
                    failed = True
                s = s[1:]
            chunk = chunk[1:]
    return ("", False) if failed else (s, True)


def match(pattern: str, name: str) -> bool:
    """Go's `path.Match(pattern, name)`; `BadPattern` where Go returns `ErrBadPattern`."""
    while pattern:
        star, chunk, pattern = _scan_chunk(pattern)
        if star and chunk == "":
            return "/" not in name          # a trailing star matches the rest of the name unless it has a /
        t, ok = _match_chunk(chunk, name)
        if ok and (not t or pattern):
            name = t
            continue
        advanced = False
        if star:
            i = 0
            while i < len(name) and name[i] != "/":      # the star skips i+1 characters, never a /
                t, ok = _match_chunk(chunk, name[i + 1:])
                if ok:
                    if not pattern and t:
                        i += 1
                        continue
                    name, advanced = t, True
                    break
                i += 1
        if advanced:
            continue
        while pattern:                       # a mismatch: the rest of the pattern must still be well formed
            _, chunk, pattern = _scan_chunk(pattern)
            _match_chunk(chunk, "")
        return False
    return not name
