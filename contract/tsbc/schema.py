"""Mini JSON Schema 2020-12 validator: the frozen keyword subset the contract is written in.

Stdlib only, so a stranger validates the contract with nothing installed. The subset is a
published constraint (README "Validator subset"): every schema under `schemas/` and every schema
object inside `openapi/tsb.openapi.json` must stay inside it. A schema that uses any other keyword
raises SchemaError. It is never skipped, because an unimplemented keyword that passes silently
would make a schema look stricter than the check that grades it.

References resolve by file location, not by `$id`: a `$ref` of `other.schema.json#/$defs/x` is
read relative to the referencing document's path inside the contract tree.

`pattern` follows ECMA-262 as JSON Schema requires, not Python's `re`: `$` matches only at the very
end of the string (Python's also matches before a final newline, which would let "id\n" pass an
anchored id pattern), and character classes are ASCII.
"""

from __future__ import annotations

import json
import math
import posixpath
import re
from pathlib import Path
from typing import Any

# Keywords that constrain an instance.
ASSERTIONS = frozenset({
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "minimum", "maximum", "minLength", "maxLength", "pattern",
    "items", "minItems", "maxItems", "oneOf", "anyOf", "$ref",
})
# Keywords that carry no constraint. Keys starting with "x-" are annotations too.
ANNOTATIONS = frozenset({"$schema", "$comment", "$defs", "title", "description", "examples"})
TYPES = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})
MAX_REF_DEPTH = 64


class SchemaError(Exception):
    """The schema itself is outside the subset, malformed, or references something missing."""


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number (RFC 8259)")


def loads(text: str | bytes) -> Any:
    """json.loads, minus what Python's json accepts and JSON does not: NaN, Infinity, -Infinity.
    Every document tsbc reads — fixtures, schemas, collected artefacts, a provider's responses —
    goes through this, so a non-finite number is a parse error, never a value a check can pass."""
    return json.loads(text, parse_constant=_no_constant)


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def type_matches(value: Any, wanted: str) -> bool:
    actual = json_type(value)
    if actual == wanted:
        return True
    if wanted == "number" and actual == "integer":
        return True
    # JSON has one number type: 2.0 is an integer instance.
    return wanted == "integer" and actual == "number" and math.isfinite(value) and value.is_integer()


def json_equal(a: Any, b: Any) -> bool:
    """Equality with JSON semantics: true is not 1, and 1 equals 1.0."""
    ta, tb = json_type(a), json_type(b)
    numeric = {"integer", "number"}
    if ta in numeric and tb in numeric:
        return a == b
    if ta != tb:
        return False
    if ta == "array":
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if ta == "object":
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return a == b


_PATTERNS: dict[str, re.Pattern] = {}


def compile_pattern(pattern: str) -> re.Pattern:
    """A JSON Schema `pattern` as Python: an unescaped `$` outside a class becomes `\\Z`."""
    compiled = _PATTERNS.get(pattern)
    if compiled is None:
        out, i, in_class = [], 0, False
        while i < len(pattern):
            char = pattern[i]
            if char == "\\":
                out.append(pattern[i:i + 2])
                i += 2
                continue
            if in_class:
                in_class = char != "]"
            elif char == "[":
                in_class = True
            elif char == "$":
                char = r"\Z"
            out.append(char)
            i += 1
        compiled = _PATTERNS[pattern] = re.compile("".join(out), re.ASCII)
    return compiled


def pointer_get(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer lookup. Raises KeyError when the pointer does not resolve."""
    if pointer in ("", "/"):
        return doc if pointer == "" else doc[""]
    if not pointer.startswith("/"):
        raise KeyError(f"JSON pointer must start with '/': {pointer!r}")
    node = doc
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                raise KeyError(pointer)
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                raise KeyError(pointer)
            node = node[int(token)]
        else:
            raise KeyError(pointer)
    return node


def _nonneg_int(value: Any) -> bool:
    return json_type(value) == "integer" and value >= 0


def check_schema(schema: Any, where: str = "#") -> None:
    """Raise SchemaError if `schema` uses anything outside the subset, at any depth."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"{where}: a schema must be an object or a boolean")
    for key in schema:
        if key not in ASSERTIONS and key not in ANNOTATIONS and not key.startswith("x-"):
            raise SchemaError(f"{where}: keyword '{key}' is outside the tsbc validator subset")

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not types or any(t not in TYPES for t in types):
            raise SchemaError(f"{where}/type: must be one of {sorted(TYPES)} or a non-empty list of them")
    if "required" in schema:
        req = schema["required"]
        if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
            raise SchemaError(f"{where}/required: must be a list of strings")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError(f"{where}/enum: must be a non-empty list")
    for key in ("minimum", "maximum"):
        if key in schema and json_type(schema[key]) not in ("integer", "number"):
            raise SchemaError(f"{where}/{key}: must be a number")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and not _nonneg_int(schema[key]):
            raise SchemaError(f"{where}/{key}: must be a non-negative integer")
    if "pattern" in schema:
        try:
            compile_pattern(schema["pattern"])
        except (re.error, TypeError) as exc:
            raise SchemaError(f"{where}/pattern: does not compile: {exc}") from None
    if "$ref" in schema and not isinstance(schema["$ref"], str):
        raise SchemaError(f"{where}/$ref: must be a string")

    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise SchemaError(f"{where}/properties: must be an object")
        for name, sub in schema["properties"].items():
            check_schema(sub, f"{where}/properties/{name}")
    if "$defs" in schema:
        if not isinstance(schema["$defs"], dict):
            raise SchemaError(f"{where}/$defs: must be an object")
        for name, sub in schema["$defs"].items():
            check_schema(sub, f"{where}/$defs/{name}")
    if "additionalProperties" in schema:
        check_schema(schema["additionalProperties"], f"{where}/additionalProperties")
    if "items" in schema:
        if isinstance(schema["items"], list):
            raise SchemaError(f"{where}/items: the array form is not 2020-12 and not in the subset")
        check_schema(schema["items"], f"{where}/items")
    for key in ("oneOf", "anyOf"):
        if key in schema:
            branches = schema[key]
            if not isinstance(branches, list) or not branches:
                raise SchemaError(f"{where}/{key}: must be a non-empty list")
            for i, sub in enumerate(branches):
                check_schema(sub, f"{where}/{key}/{i}")


def iter_refs(node: Any):
    """Yield every `$ref` string anywhere inside a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


class Registry:
    """JSON documents of one contract tree, keyed by posix path relative to the tree root."""

    def __init__(self) -> None:
        self.docs: dict[str, Any] = {}

    def add(self, name: str, doc: Any) -> None:
        self.docs[name] = doc

    @classmethod
    def from_tree(cls, root: Path, subdirs: tuple[str, ...] = ("schemas", "openapi")) -> Registry:
        reg = cls()
        for sub in subdirs:
            base = root / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.json")):
                name = path.relative_to(root).as_posix()
                try:
                    reg.add(name, loads(path.read_text(encoding="utf-8")))
                except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, a non-finite number
                    raise SchemaError(f"{name}: not valid JSON: {exc}") from None
        return reg

    def resolve(self, ref: str, base: str) -> tuple[Any, str]:
        """(schema, document name) for `ref` as written inside document `base`."""
        path, _, fragment = ref.partition("#")
        name = base if not path else posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
        if name not in self.docs:
            raise SchemaError(f"{base}: $ref '{ref}' names a document that is not in the tree ({name})")
        try:
            return pointer_get(self.docs[name], fragment), name
        except KeyError:
            raise SchemaError(f"{base}: $ref '{ref}' does not resolve inside {name}") from None

    def split(self, ref: str) -> tuple[Any, str]:
        """Resolve a tree-rooted reference such as `schemas/x.schema.json#/$defs/y`."""
        path, _, fragment = ref.partition("#")
        return self.resolve(f"{posixpath.basename(path)}#{fragment}", path)

    def validate(self, instance: Any, ref: str) -> list[str]:
        """Validate against a tree-rooted reference. Returns error strings, empty when valid."""
        schema, doc = self.split(ref)
        return validate(instance, schema, self, doc)


def validate(instance: Any, schema: Any, registry: Registry, doc: str, path: str = "$",
             depth: int = 0) -> list[str]:
    """Errors for `instance` against `schema`, which lives in document `doc` of `registry`."""
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here"]
    errors: list[str] = []

    if "$ref" in schema:
        if depth >= MAX_REF_DEPTH:
            raise SchemaError(f"{doc}: $ref chain deeper than {MAX_REF_DEPTH} (a cycle?)")
        target, target_doc = registry.resolve(schema["$ref"], doc)
        errors += validate(instance, target, registry, target_doc, path, depth + 1)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(type_matches(instance, t) for t in types):
            errors.append(f"{path}: expected {' or '.join(types)}, got {json_type(instance)}")
            return errors
    if "const" in schema and not json_equal(instance, schema["const"]):
        errors.append(f"{path}: must equal {json.dumps(schema['const'])}")
    if "enum" in schema and not any(json_equal(instance, e) for e in schema["enum"]):
        errors.append(f"{path}: must be one of {json.dumps(schema['enum'])}")

    kind = json_type(instance)
    if kind in ("integer", "number"):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below the minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above the maximum {schema['maximum']}")
    elif kind == "string":
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not compile_pattern(schema["pattern"]).search(instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']}")
    elif kind == "array":
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors += validate(item, schema["items"], registry, doc, f"{path}[{i}]", depth)
    elif kind == "object":
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property '{name}'")
        props = schema.get("properties", {})
        for name, value in instance.items():
            if name in props:
                errors += validate(value, props[name], registry, doc, f"{path}.{name}", depth)
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    errors.append(f"{path}: property '{name}' is not allowed")
                else:
                    errors += validate(value, extra, registry, doc, f"{path}.{name}", depth)

    if "anyOf" in schema:
        results = [validate(instance, s, registry, doc, path, depth) for s in schema["anyOf"]]
        if all(results):
            errors.append(f"{path}: matches none of anyOf ({_branch_summary(results)})")
    if "oneOf" in schema:
        results = [validate(instance, s, registry, doc, path, depth) for s in schema["oneOf"]]
        passing = sum(1 for r in results if not r)
        if passing == 0:
            errors.append(f"{path}: matches none of oneOf ({_branch_summary(results)})")
        elif passing > 1:
            errors.append(f"{path}: matches {passing} branches of oneOf, exactly one is allowed")
    return errors


def _branch_summary(results: list[list[str]]) -> str:
    return "; ".join(f"branch {i}: {r[0]}" for i, r in enumerate(results) if r)
