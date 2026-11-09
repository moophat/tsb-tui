"""I-Regexp (RFC 9485) parser and NFA matcher for the send gate's prompt-pattern check.

The contract field `prompt_pattern` on a target record is an I-Regexp.  Before a
line-ending send drive checks whether the cursor row (trailing spaces trimmed) is
matched by the target's pattern.  A send that does not match is refused.

Matching semantics follow RFC 9485 §4 and XSD 1.1 §F.1.1: the pattern is implicitly
anchored to the full string — it must match the whole row, not a substring of it.

This module:
  * Parses I-Regexp source text according to the §3 ABNF (recursive descent).
  * Builds an NFA from the parsed expression via Thompson construction.
  * Runs the NFA with a subset simulation (linear in `len(row)`).
  * Evaluates `\\p{Cat}` and `\\P{Cat}` predicates with `unicodedata.category`.

Constructs that are part of RFC 9485's grammar but are too large to implement
faithfully here are refused at parse time (returned as ``ParseError``), not silently
approximated.  The refused list is documented in the build report.

Standard-library only: no ``re``, no third-party packages.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------

class ParseError(Exception):
    """The pattern text violates RFC 9485 §3 ABNF, or uses a construct that
    this implementation refuses to approximate.  ``args[0]`` is a plain-text
    explanation suitable for a ``prompt_unusable`` ``why`` field."""


# ---------------------------------------------------------------------------
# Character-class predicates
# ---------------------------------------------------------------------------

# Unicode general category names accepted by \p{..} (single-letter and two-letter).
# unicodedata.category returns two-character strings like "Lu", "Ll", "Nd" etc.
# A one-letter prefix like "L" means any "L*" category.
def _make_cat_pred(cat: str, negate: bool) -> Callable[[str], bool]:
    categories = {"L", "Ll", "Lm", "Lo", "Lt", "Lu", "M", "Mc", "Me", "Mn",
                  "N", "Nd", "Nl", "No", "P", "Pc", "Pd", "Pe", "Pf", "Pi", "Po", "Ps",
                  "Z", "Zl", "Zp", "Zs", "S", "Sc", "Sk", "Sm", "So", "C", "Cc", "Cf", "Cn", "Co"}
    if cat not in categories:
        raise ParseError(f"unrecognised Unicode category {cat!r}")
    if len(cat) == 1:
        def _pred(ch: str, _c: str = cat, _n: bool = negate) -> bool:
            return (unicodedata.category(ch).startswith(_c)) != _n
    elif len(cat) == 2:
        def _pred(ch: str, _c: str = cat, _n: bool = negate) -> bool:
            return (unicodedata.category(ch) == _c) != _n
    else:
        raise ParseError(f"\\p{{..}}: unrecognised Unicode category {cat!r}")
    return _pred


# The dot (.) in I-Regexp matches any character except CR and LF (XSD §F.1.1 / RFC 9485 §4).
def _pred_dot(ch: str) -> bool:
    return ch not in "\n\r"


# ---------------------------------------------------------------------------
# NFA nodes (Thompson construction)
# ---------------------------------------------------------------------------
# Each NFA is represented as a list of states.  Each state is a tuple:
#   ("char", pred, [next_states])          — consume one character satisfying pred
#   ("eps", [], [next_states])             — epsilon transition
#   ("accept", [], [])                     — accepting state
#
# We build sub-NFAs as (start, accept_list) where accept_list is the list of
# accept-state indices to be wired to the next fragment.

_NFA = list[tuple]  # type alias for readability


class _Builder:
    """Incrementally builds the NFA state list."""

    def __init__(self) -> None:
        self.states: _NFA = []

    def add(self, kind: str, pred: Callable[[str], bool] | None, nexts: list[int]) -> int:
        if len(self.states) >= 4096:
            raise ParseError("pattern exceeds the 4096-state NFA construction limit")
        idx = len(self.states)
        self.states.append((kind, pred, nexts))
        return idx

    def eps(self) -> int:
        return self.add("eps", None, [])

    def char(self, pred: Callable[[str], bool]) -> int:
        return self.add("char", pred, [])

    def accept(self) -> int:
        return self.add("accept", None, [])

    def link(self, from_idx: int, to_idx: int) -> None:
        self.states[from_idx][2].append(to_idx)

    def patch(self, dangling: list[int], target: int) -> None:
        for idx in dangling:
            self.states[idx][2].append(target)


# A Fragment is (start_state, dangling_out_list).
# After patching, dangling_out_list states transition to the next fragment.
class _Frag:
    __slots__ = ("out", "start")

    def __init__(self, start: int, out: list[int]) -> None:
        self.start = start
        self.out = out


def _frag_char(b: _Builder, pred: Callable[[str], bool]) -> _Frag:
    s = b.char(pred)
    return _Frag(s, [s])


def _frag_concat(b: _Builder, f1: _Frag, f2: _Frag) -> _Frag:
    b.patch(f1.out, f2.start)
    return _Frag(f1.start, f2.out)


def _frag_alternate(b: _Builder, f1: _Frag, f2: _Frag) -> _Frag:
    split = b.eps()
    b.link(split, f1.start)
    b.link(split, f2.start)
    return _Frag(split, f1.out + f2.out)


def _frag_star(b: _Builder, f: _Frag) -> _Frag:
    split = b.eps()
    b.link(split, f.start)
    b.patch(f.out, split)
    return _Frag(split, [split])


def _frag_plus(b: _Builder, f: _Frag) -> _Frag:
    split = b.eps()
    b.link(split, f.start)
    b.patch(f.out, split)
    return _Frag(f.start, [split])


def _frag_question(b: _Builder, f: _Frag) -> _Frag:
    split = b.eps()
    b.link(split, f.start)
    return _Frag(split, [split] + f.out)


def _frag_repeat(b: _Builder, f_factory: Callable[[], _Frag], n: int, m: int | None) -> _Frag:
    """Repeat f_factory() n..m times.  m=None means unbounded (n+)."""
    if n > 4096 or (m is not None and m > 4096):
        raise ParseError("quantifier exceeds the 4096-state NFA construction limit")
    start = b.eps()
    result = _Frag(start, [start])
    for _ in range(n):
        result = _frag_concat(b, result, f_factory())
    if m is None:
        return _frag_concat(b, result, _frag_star(b, f_factory()))
    for _ in range(m - n):
        result = _frag_concat(b, result, _frag_question(b, f_factory()))
    return result


# ---------------------------------------------------------------------------
# Parser (recursive descent over RFC 9485 §3 ABNF)
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, src: str, builder: _Builder) -> None:
        self._s = src
        self._pos = 0
        self._b = builder

    def _peek(self) -> str | None:
        return self._s[self._pos] if self._pos < len(self._s) else None

    def _consume(self) -> str:
        ch = self._s[self._pos]
        self._pos += 1
        return ch

    def _expect(self, ch: str) -> None:
        got = self._peek()
        if got != ch:
            raise ParseError(f"expected {ch!r} at position {self._pos}, got {got!r}")
        self._pos += 1

    # -- i-regexp = branch *( '|' branch ) --

    def parse_regexp(self) -> _Frag:
        f = self._parse_branch()
        while self._peek() == "|":
            self._pos += 1
            f2 = self._parse_branch()
            f = _frag_alternate(self._b, f, f2)
        return f

    # -- branch = *piece --

    def _parse_branch(self) -> _Frag:
        # Build an epsilon-start fragment so an empty branch is valid.
        eps_s = self._b.eps()
        result: _Frag | None = None
        while self._peek() not in ("|", ")", None):
            piece = self._parse_piece()
            if result is None:
                self._b.link(eps_s, piece.start)
                result = _Frag(eps_s, piece.out)
            else:
                result = _frag_concat(self._b, result, piece)
        if result is None:
            # Empty branch: eps with dangling exit
            return _Frag(eps_s, [eps_s])
        return result

    # -- piece = atom [ quantifier ] --

    def _parse_piece(self) -> _Frag:
        # We need to build the atom potentially multiple times for {n,m}.
        pos_before = self._pos
        atom = self._parse_atom_as_pred()
        atom_end = self._pos
        b = self._b
        fragment = atom if isinstance(atom, _Frag) else _frag_char(b, atom)
        q = self._peek()
        if q in ("*", "+", "?"):
            self._pos += 1
            return {"*": _frag_star, "+": _frag_plus, "?": _frag_question}[q](b, fragment)
        if q == "{":
            self._pos += 1
            n, m = self._parse_quantity()
            self._expect("}")
            atom_text = self._s[pos_before:atom_end]

            def factory() -> _Frag:
                sub = _Parser(atom_text, b)
                parsed = sub._parse_atom_as_pred()
                return parsed if isinstance(parsed, _Frag) else _frag_char(b, parsed)

            return _frag_repeat(b, factory, n, m)
        return fragment

    # -- quantifier helpers --

    def _parse_quantity(self) -> tuple[int, int | None]:
        """Parse QuantExact | QuantMin ',' | QuantMin ',' QuantMax."""
        n_str = ""
        while self._peek() and "0" <= self._peek() <= "9":
            n_str += self._consume()
        if not n_str:
            raise ParseError(f"expected digit in {{..}} at position {self._pos}")
        n = int(n_str)
        if self._peek() == "}":
            return n, n
        self._expect(",")
        m_str = ""
        while self._peek() and "0" <= self._peek() <= "9":
            m_str += self._consume()
        m = int(m_str) if m_str else None
        if m is not None and m < n:
            raise ParseError(f"{{n,m}}: m ({m}) < n ({n})")
        return n, m

    # -- atom --

    def _parse_atom_as_pred(self) -> Callable[[str], bool] | _Frag:
        """Parse one atom and return either a char-predicate or a _Frag (for groups)."""
        ch = self._peek()
        if ch is None:
            raise ParseError(f"unexpected end of pattern at position {self._pos}")
        if ch == "(":
            self._pos += 1
            f = self.parse_regexp()
            self._expect(")")
            return f
        if ch == ".":
            self._pos += 1
            return _pred_dot
        if ch == "\\":
            return self._parse_escape()
        if ch == "[":
            return self._parse_char_class_expr()
        # NormalChar: anything not special
        if ch in ("|", ")", "*", "+", "?", "{", "}"):
            raise ParseError(f"unexpected {ch!r} at position {self._pos}")
        if ch == "]":
            raise ParseError(f"unexpected {ch!r} at position {self._pos}")
        self._pos += 1
        return (lambda c, _ch=ch: c == _ch)

    # -- escape sequences --

    def _parse_escape(self) -> Callable[[str], bool]:
        self._expect("\\")
        ch = self._peek()
        if ch is None:
            raise ParseError("trailing backslash in pattern")
        if ch in "()*+-.?[]\\^{}|nrt":
            self._pos += 1
            literal = {"n": "\n", "r": "\r", "t": "\t"}.get(ch, ch)
            return lambda c: c == literal
        # catEsc / complEsc: \p{Cat} \P{Cat}
        if ch in ("p", "P"):
            negate = ch == "P"
            self._pos += 1
            self._expect("{")
            cat = ""
            while self._peek() and self._peek() != "}":
                cat += self._consume()
            self._expect("}")
            return _make_cat_pred(cat, negate)
        raise ParseError(f"unknown escape \\{ch} at position {self._pos}")

    # -- character class expression [..] --

    def _parse_char_class_expr(self) -> Callable[[str], bool]:
        self._expect("[")
        negate = False
        if self._peek() == "^":
            negate = True
            self._pos += 1
        if self._peek() == "]" and not negate:
            raise ParseError(f"empty character class [] at position {self._pos}")
        pred = self._parse_pos_char_group()
        self._expect("]")
        if negate:
            return lambda c, p=pred: not p(c)
        return pred

    def _parse_pos_char_group(self) -> Callable[[str], bool]:
        predicates: list[Callable[[str], bool]] = []
        if self._peek() == "-":
            self._pos += 1
            predicates.append(lambda c: c == "-")
        while self._peek() not in ("]", None):
            if self._peek() == "-" and self._s[self._pos + 1:self._pos + 2] == "]" and predicates:
                self._pos += 1
                predicates.append(lambda c: c == "-")
                break
            if self._s.startswith(("\\p{", "\\P{"), self._pos):
                predicates.append(self._parse_escape())
                continue
            first = self._class_char()
            if self._peek() == "-" and self._s[self._pos + 1:self._pos + 2] != "]":
                self._pos += 1
                last = self._class_char()
                if ord(first) > ord(last):
                    raise ParseError("character class range is reversed")
                predicates.append(lambda c, lo=first, hi=last: lo <= c <= hi)
            else:
                predicates.append(lambda c, literal=first: c == literal)
            if self._peek() == "-" and self._s[self._pos + 1:self._pos + 2] == "]":
                self._pos += 1
                predicates.append(lambda c: c == "-")
        if not predicates:
            raise ParseError("empty character class group")
        return lambda c: any(pred(c) for pred in predicates)

    def _class_char(self) -> str:
        ch = self._peek()
        if ch == "\\":
            self._pos += 1
            ch = self._peek()
            if ch is None or ch not in "()*+-.?[]\\^{}|nrt":
                raise ParseError("expected a character or single-character escape in class")
            self._pos += 1
            return {"n": "\n", "r": "\r", "t": "\t"}.get(ch, ch)
        if ch is None or ch in "-[]":
            raise ParseError("expected a character in class")
        self._pos += 1
        return ch


# ---------------------------------------------------------------------------
# NFA simulation (linear in the row for a bounded pattern)
# ---------------------------------------------------------------------------

def _epsilon_closure(states: _NFA, starts: frozenset[int]) -> frozenset[int]:
    """All states reachable from `starts` via epsilon transitions."""
    stack = list(starts)
    seen = set(starts)
    while stack:
        idx = stack.pop()
        kind, _, nexts = states[idx]
        if kind == "eps":
            for n in nexts:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
    return frozenset(seen)


def _step(states: _NFA, current: frozenset[int], ch: str) -> frozenset[int]:
    """Move from `current` consuming one character `ch`."""
    nexts: set[int] = set()
    for idx in current:
        kind, pred, out = states[idx]
        if kind == "char" and pred(ch):
            nexts.update(out)
    return _epsilon_closure(states, frozenset(nexts))


def _run_nfa(states: _NFA, start: int, accept: int, text: str) -> bool:
    """True iff the NFA accepts `text` (whole-string match)."""
    current = _epsilon_closure(states, frozenset({start}))
    for ch in text:
        if not current:
            return False
        current = _step(states, current, ch)
    return accept in current


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class CompiledPattern:
    """A compiled I-Regexp, ready to match against strings."""

    __slots__ = ("_accept", "_source", "_start", "_states")

    def __init__(self, states: _NFA, start: int, accept: int, source: str) -> None:
        self._states = states
        self._start = start
        self._accept = accept
        self._source = source

    def matches(self, row: str) -> bool:
        """True iff *the whole* ``row`` matches (XSD whole-string semantics).

        ``row`` should already have trailing spaces trimmed (the caller's responsibility,
        as the contract defines the trimming boundary).
        """
        return _run_nfa(self._states, self._start, self._accept, row)

    def __repr__(self) -> str:
        return f"CompiledPattern({self._source!r})"


def compile_pattern(src: str) -> CompiledPattern:
    """Parse `src` as an I-Regexp (RFC 9485) and return a ``CompiledPattern``.

    Raises ``ParseError`` if the pattern is syntactically invalid or uses a
    construct that this implementation refuses.
    """
    if not isinstance(src, str) or not 1 <= len(src) <= 256:
        raise ParseError("prompt pattern must contain 1 to 256 characters")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in src):
        raise ParseError("prompt pattern contains a control character or surrogate")
    b = _Builder()
    parser = _Parser(src, b)
    try:
        frag = parser.parse_regexp()
    except ParseError:
        raise
    except RecursionError as exc:
        raise ParseError("pattern nesting exceeds the parser construction limit") from exc
    if parser._pos != len(src):
        raise ParseError(f"unexpected character {src[parser._pos]!r} at position {parser._pos}")
    accept = b.accept()
    b.patch(frag.out, accept)
    return CompiledPattern(b.states, frag.start, accept, src)
