"""Unit tests for the I-Regexp (RFC 9485) parser and NFA matcher in prompt_pattern.py."""
from __future__ import annotations

import time

import pytest

from tsb_tui.drive.prompt_pattern import ParseError, compile_pattern

# ---------------------------------------------------------------------------
# RFC 9485 §4 examples and basic sanity
# ---------------------------------------------------------------------------

def test_rfc9485_literal_match():
    p = compile_pattern("hello")
    assert p.matches("hello")
    assert not p.matches("hell")
    assert not p.matches("hello!")


def test_rfc9485_dot_matches_any_except_newline():
    p = compile_pattern("a.c")
    assert p.matches("abc")
    assert p.matches("a c")
    assert not p.matches("a\nc")
    assert not p.matches("ac")


def test_rfc9485_star_quantifier():
    p = compile_pattern("a*")
    assert p.matches("")
    assert p.matches("a")
    assert p.matches("aaaa")
    assert not p.matches("b")


def test_rfc9485_plus_quantifier():
    p = compile_pattern("a+")
    assert not p.matches("")
    assert p.matches("a")
    assert p.matches("aaa")


def test_rfc9485_question_quantifier():
    p = compile_pattern("ab?c")
    assert p.matches("ac")
    assert p.matches("abc")
    assert not p.matches("abbc")


def test_rfc9485_alternation():
    p = compile_pattern("cat|dog")
    assert p.matches("cat")
    assert p.matches("dog")
    assert not p.matches("bird")


def test_rfc9485_group():
    p = compile_pattern("(ab)+")
    assert p.matches("ab")
    assert p.matches("abab")
    assert not p.matches("")
    assert not p.matches("a")


def test_rfc9485_char_class():
    p = compile_pattern("[abc]")
    assert p.matches("a")
    assert p.matches("b")
    assert not p.matches("d")


def test_rfc9485_negated_char_class():
    p = compile_pattern("[^abc]")
    assert p.matches("d")
    assert not p.matches("a")


def test_rfc9485_char_range():
    p = compile_pattern("[a-z]+")
    assert p.matches("hello")
    assert not p.matches("Hello")


# ---------------------------------------------------------------------------
# Whole-row match (XSD anchoring — must match the full string)
# ---------------------------------------------------------------------------

def test_whole_row_match_requires_full_string():
    p = compile_pattern(r"rtr-core-01[#>]")
    assert p.matches("rtr-core-01#")
    assert not p.matches("rtr-core-01# ")   # trailing space — but caller trims first
    assert not p.matches("xrtr-core-01#")   # leading prefix


def test_whole_row_match_allows_optional_config_mode():
    # A pattern for rtr-core-01 then optional (config...) then # or > at row's end
    p = compile_pattern(r"rtr-core-01(\(config[^)]*\))?[#>]")
    assert p.matches("rtr-core-01#")
    assert p.matches("rtr-core-01>")
    assert p.matches("rtr-core-01(config)#")
    assert p.matches("rtr-core-01(config-if)#")
    assert not p.matches("rtr-core-01")
    assert not p.matches("--More--")


# ---------------------------------------------------------------------------
# Unicode category escape \p{..}
# ---------------------------------------------------------------------------

def test_unicode_category_Lu_matches_uppercase():
    p = compile_pattern(r"\p{Lu}+")
    assert p.matches("ABC")
    assert not p.matches("abc")
    assert not p.matches("Abc")


def test_unicode_category_L_matches_any_letter():
    p = compile_pattern(r"\p{L}+")
    assert p.matches("abc")
    assert p.matches("ABC")
    assert p.matches("café")
    assert not p.matches("123")


def test_unicode_category_negated_P():
    p = compile_pattern(r"\P{Nd}+")
    assert p.matches("hello")
    assert not p.matches("123")


# ---------------------------------------------------------------------------
# Refused constructs (ParseError)
# ---------------------------------------------------------------------------

def test_refused_multi_char_esc_i():
    with pytest.raises(ParseError):
        compile_pattern(r"\i+")


def test_refused_multi_char_esc_c():
    with pytest.raises(ParseError):
        compile_pattern(r"\c+")


def test_refused_multi_char_esc_w():
    with pytest.raises(ParseError):
        compile_pattern(r"\w+")


def test_refused_multi_char_esc_W():
    with pytest.raises(ParseError):
        compile_pattern(r"\W+")


def test_refused_large_bounded_quantifier():
    # Refuse construction beyond the bounded state budget.
    with pytest.raises(ParseError):
        compile_pattern("a{4097}")


# ---------------------------------------------------------------------------
# (a|a)*b on a long row finishes quickly (linear NFA, not exponential)
# ---------------------------------------------------------------------------

def test_ambiguous_star_on_long_row_finishes_quickly():
    # (a|a)*b — a classic exponential backtracking case; must finish quickly with NFA
    p = compile_pattern("(a|a)*b")
    long_no_match = "a" * 1000
    t0 = time.monotonic()
    result = p.matches(long_no_match)
    elapsed = time.monotonic() - t0
    assert not result
    assert elapsed < 1.0, f"NFA took {elapsed:.3f}s on long non-matching string (should be <1s)"


def test_ambiguous_star_on_matching_row():
    p = compile_pattern("(a|a)*b")
    assert p.matches("aaaab")
    assert not p.matches("aaaa")


@pytest.mark.parametrize("pattern, good, bad", [
    ("a{0}", "", "a"), ("a{0,0}", "", "a"),
    ("a{0,2}", "a", "aaa"), ("a{2,}", "aa", "a"),
    ("(ab){2,3}", "ababab", "ab"), ("(a|bc){02}", "abc", "a"),
    ("a{000,001}", "", "aa"), ("(a?){2}", "", "aaa"),
    ("a|", "", "b"), ("()", "", "a"),
    (r"[\n-\r]", "\n", "a"), (r"[\--0]", ".", "a"),
    (r"[a\-z]", "-", "b"), (r"[\p{L}-]", "-", "1"),
    ("[-a]", "-", "b"), ("[a-]", "-", "b"),
    ("[-]", "-", "a"), (r"[\^]", "^", "a"),
    (r"\n\r\t", "\n\r\t", "nrt"),
    (r"\p{Cn}", "\u0378", "a"), (r"[\P{L}]", "1", "a"),
    ("^$", "^$", ""), (".", "😀", "\r"),
])
def test_grammar_and_repetition_boundaries(pattern, good, bad):
    compiled = compile_pattern(pattern)
    assert compiled.matches(good)
    assert not compiled.matches(bad)


@pytest.mark.parametrize("pattern", [
    r"\s", r"\S", r"\d", r"\D", r"[\w]", r"\a", r"\$",
    r"\p{XX}", r"\p{Cs}", r"\p{ Lu}", r"\p{IsBasicLatin}",
    "[]", "[^]", "[a[b]", "[a--b]", "[a-b-c]", "[z-a]",
    r"[a-\p{L}]", r"[\p{L}-a]", "a{٢}", "a{2,1}",
    "a{,2}", "a**", "a{2}?", "(?=a)", "a{2", ")", "[a", "\\",
    "a" * 257, "\ud800", "a\x00", "(a{100}){100}",
])
def test_invalid_or_oversized_patterns_are_refused(pattern):
    with pytest.raises(ParseError):
        compile_pattern(pattern)


def test_zero_repetition_of_group_does_not_consume():
    assert compile_pattern("(ab){0}c").matches("c")


def test_ambiguous_star_scales_to_a_long_terminal_row():
    compiled = compile_pattern("(a|a)*b")
    start = time.monotonic()
    assert not compiled.matches("a" * 100_000)
    assert compiled.matches("a" * 100_000 + "b")
    assert time.monotonic() - start < 3
