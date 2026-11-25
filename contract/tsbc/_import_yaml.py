"""Read the simple, safe YAML data forms supported by the fixture server.

This is deliberately not an object constructor: tags, anchors and aliases are refused.
Unsupported YAML syntax is rejected rather than interpreted as a different value.
"""

from __future__ import annotations

import json
import re

_TEXT_KEYS = frozenset({"id", "type", "name", "group", "vendor", "prompt_pattern", "host", "user", "password", "tags"})


def _strip_comment(text: str) -> str:
    """Drop a trailing `#` comment that starts outside any quote."""
    i, n = 0, len(text)
    in_double = in_single = False
    while i < n:
        c = text[i]
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if in_single:
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if c == '"':
            in_double = True
        elif c == "'":
            in_single = True
        elif c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].strip()
        i += 1
    return text.strip()


def _split_flow(inside: str) -> list[str]:
    """Split a flow sequence's body at top-level commas, keeping quoted commas as one item."""
    items, cur, stack = [], [], []
    i, n = 0, len(inside)
    in_double = in_single = False
    matching = {"]": "[", "}": "{"}
    while i < n:
        c = inside[i]
        if in_double:
            cur.append(c)
            if c == "\\":
                if i + 1 < n:
                    cur.append(inside[i + 1])
                    i += 2
                    continue
            elif c == '"':
                in_double = False
            i += 1
            continue
        if in_single:
            cur.append(c)
            if c == "'":
                if i + 1 < n and inside[i + 1] == "'":
                    cur.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if c == '"':
            in_double = True
        elif c == "'":
            in_single = True
        elif c in "[{":
            stack.append(c)
        elif c in "]}":
            if not stack or stack[-1] != matching[c]:
                raise ValueError("mismatched flow delimiter")
            stack.pop()
        elif c == "," and not stack:
            items.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if stack or in_double or in_single:
        raise ValueError("unclosed flow delimiter")
    tail = "".join(cur).strip()
    if tail:
        items.append(tail)
    return items


def _scalar(text: str, *, as_text: bool = False):
    text = _strip_comment(text)
    if not text:
        return None
    if text.startswith('"'):
        return json.loads(text)
    if text.startswith("'"):
        if not text.endswith("'"):
            raise ValueError("unclosed scalar")
        return text[1:-1].replace("''", "'")
    if text.startswith("["):
        if not text.endswith("]"):
            raise ValueError("unclosed sequence")
        items = _split_flow(text[1:-1])
        return [] if not items else [_scalar(item, as_text=as_text) for item in items]
    if text.startswith(("{", "!", "&", "*", "|", ">")):
        raise ValueError("unsupported YAML syntax")
    if as_text:
        return text
    if text in ("null", "Null", "NULL", "~"):
        return None
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text):
        return int(text)
    return text


def load(data: bytes, max_depth: int = 32):
    """Parse a single block mapping or sequence without constructing custom objects."""
    text = data.decode("utf-8-sig")
    lines = []
    document_marker = False
    for raw in text.splitlines():
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise ValueError("tab indentation")
        line = raw.lstrip(" ")
        if not line or line.startswith("#"):
            continue
        if line == "---":
            if lines or document_marker:
                raise ValueError("multiple YAML documents")
            document_marker = True
            continue
        if line == "..." or line.startswith("%"):
            raise ValueError("unsupported document")
        lines.append((len(raw) - len(line), line))
    if not lines or lines[0][0] != 0:
        raise ValueError("empty or indented document")

    def block(index, indent, depth):
        if depth > max_depth:
            raise ValueError("nesting limit")
        sequence = lines[index][1].startswith("- ")
        result = [] if sequence else {}
        while index < len(lines) and lines[index][0] == indent:
            item = lines[index][1]
            if sequence:
                if not item.startswith("- "):
                    raise ValueError("mixed collection")
                rest = item[2:].strip()
                if ":" in rest and not rest.startswith(("'", '"')):
                    key, _, value = rest.partition(":")
                    if not key or not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
                        raise ValueError("invalid key")
                    obj = {key: _scalar(value, as_text=key in _TEXT_KEYS)}
                    index += 1
                    if index < len(lines) and lines[index][0] > indent:
                        other, index = block(index, lines[index][0], depth + 1)
                        if not isinstance(other, dict) or set(obj) & set(other):
                            raise ValueError("duplicate or invalid mapping")
                        obj.update(other)
                    result.append(obj)
                else:
                    result.append(_scalar(rest))
                    index += 1
            else:
                if item.startswith("- ") or ":" not in item:
                    raise ValueError("invalid mapping")
                key, _, value = item.partition(":")
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or key in result:
                    raise ValueError("duplicate or invalid key")
                index += 1
                if not value.strip() and index < len(lines) and lines[index][0] > indent:
                    result[key], index = block(index, lines[index][0], depth + 1)
                else:
                    result[key] = _scalar(value, as_text=key in _TEXT_KEYS)
        return result, index

    parsed, consumed = block(0, 0, 1)
    if consumed != len(lines):
        raise ValueError("invalid indentation")
    return parsed
