"""The inventory reader — the rules of the target-file layout, applied to a tree state.

`schemas/inventory-target.schema.json`'s `x-layout` states the layout normatively: the `current`
link, one file per target with the stem rule, the atomic write, staging directories, a missing
`current`, identity and refusals, and rule 10's YAML data model. This is that reader as code: it
takes one tree state and returns what a reader loads, what it refuses, and what it reports in
`alerts` — the refusals a reader states, each of which the core writes as a log line, since no alert
kind exists at this release. The four
inventory transform fixtures (`C5/inventory-*`) are records of this rule; self-check derives each
fixture's output from its input and compares, so the code and the published layout cannot drift.

A file is given in one of two forms: `doc`, the JSON form it validates against, or `yaml`, the tree
a YAML 1.1 reader sees — every scalar as {text: the scalar's text, yaml11: the type a YAML 1.1
reader reads it as}. Rule 10 is the mapping between them: at a key the schema types as a string,
the JSON form is the scalar's text, whatever YAML 1.1 read.

Stdlib only (README "Stdlib only"): no YAML parser is imported. The `yaml` form is data, written by
the fixture; what this code enforces is the contract's mapping rule, not a YAML implementation.
"""

from __future__ import annotations

import re

from .fixtures import Fixture

TARGET_SCHEMA = "schemas/inventory-target.schema.json"
INVENTORY_TRANSFORMS = ("inventory-layout", "inventory-yaml-mapping",
                        "inventory-write-then-observe", "inventory-bulk-swap")

# common.schema.json#/$defs/id: ids become file names under the inventory, so no slash, no leading dot.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# A staging directory is never read (x-layout rule 5): any name starting with `staging.`, whatever
# follows the dot — "A staging directory has a unique name, staging.<random>/, and is never read."
_STAGING = re.compile(r"^staging\.")
# Keys the target schema types as a string: rule 10 maps their YAML scalars to the scalar's text.
_STRING_KEYS = {"id", "name", "prompt_pattern", "group", "discovered_by", "last_seen", "host", "user", "tags"}


def _yaml11_int(text: str) -> int:
    """A `yaml11: int` scalar by its YAML 1.1 notation, never decimal int(text): an optional sign,
    then 0x/0X hexadecimal, 0o or a leading-zero octal, 0b binary, or decimal, with `_` separators
    removed. The discovery writer is Python (YAML 1.1), so `017` is 15 and `1_000` is 1000."""
    s = text.replace("_", "")
    sign = -1 if s[:1] == "-" else 1
    if s[:1] in ("+", "-"):
        s = s[1:]
    low = s.lower()
    if low.startswith(("0x", "0b", "0o")):
        return sign * int(s, 0)  # 0x/0X hexadecimal, 0b/0B binary, 0o/0O octal: the prefix is the base
    if len(s) > 1 and s[0] == "0":
        return sign * int(s, 8)  # a leading-zero octal: 017 is 15
    return sign * int(s, 10)


def _scalar(node, key):
    if not isinstance(node, dict) or "text" not in node:
        return node
    if key in _STRING_KEYS:
        return node["text"]
    kind = node.get("yaml11", "string")
    text = node["text"]
    if kind == "int":
        return _yaml11_int(text)
    if kind == "float":
        return float(text)
    if kind == "bool":
        return text.lower() in {"true", "yes", "on", "y"}
    if kind == "null":
        return None
    return text  # string, and a timestamp: a durable record holds a date-time as its text


def _yaml_form(node, key=None):
    if isinstance(node, dict) and set(node) == {"text", "yaml11"}:
        return _scalar(node, key)
    if isinstance(node, dict):
        return {k: _yaml_form(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_yaml_form(v, "tags") for v in node]
    return node


def inventory_read(state: dict, validate) -> dict:
    """One read of one tree state: the index a reader builds and the refusals it reports in `alerts`.

    `validate` is the target schema's validator, {doc, schema} -> errors. A reported refusal names
    ids and stems and sources, never a path; the core writes each as a log line: no alert kind exists
    at this release (rule 9). The reader never takes `inventory/.lock` (rule 7), so the lock is not an
    input; `root_files` are names in the inventory root, which rule 2 ignores.
    """
    if state.get("current") is None:
        # Rule 6: a missing current keeps the index as it is and reports the refusal. It never empties it.
        return {"index": [dict(e) for e in state.get("index_before", [])],
                "alerts": [{"kind": "missing_current"}], "docs": []}
    alerts, loaded = [], []
    for f in sorted(state.get("files", []), key=lambda f: (f["source"], f["name"])):
        source, name = f["source"], f["name"]
        if _STAGING.match(source) or not _ID.match(source) or not name.endswith(".yaml"):
            # A staging directory, or a source that is not id-shaped (rule 1: <source> has common
            # id's shape), is never read; a name a reader ignores (rules 2, 3, 5).
            continue
        stem = name[:-len(".yaml")]
        if not _ID.match(stem):
            continue  # rule 2: the stem must match id's pattern; every other name is ignored
        doc = f["doc"] if "doc" in f else _yaml_form(f["yaml"])
        if validate(doc, TARGET_SCHEMA):
            # Rule 9: a file that does not validate is skipped and the rest loaded.
            alerts.append({"kind": "invalid_file", "stem": stem, "source": source})
            continue
        if doc.get("id") != stem:
            # Rule 2: a file's id equals its stem.
            alerts.append({"kind": "stem_mismatch", "stem": stem, "source": source})
            continue
        if "discovered_by" in doc and doc["discovered_by"] != source:
            # Rule 9: discovered_by, when a file has it, equals the source directory that holds it.
            alerts.append({"kind": "discovered_by_mismatch", "stem": stem, "source": source})
            continue
        loaded.append((source, doc))
    sources = {}
    for source, doc in loaded:
        sources.setdefault(doc["id"], []).append(source)
    dupes = sorted(i for i, srcs in sources.items() if len(srcs) > 1)
    for i in dupes:
        # Rule 9: two sources holding the same id — neither loads; the reported refusal names the id
        # and both sources.
        alerts.append({"kind": "duplicate_id", "id": i, "sources": sorted(sources[i])})
    kept = [(source, doc) for source, doc in loaded if doc["id"] not in dupes]
    index = [{"id": doc["id"], "source": source, "tags": doc.get("tags", [])} for source, doc in kept]
    index.sort(key=lambda e: (e["id"], e["source"]))
    docs = [{"stem": doc["id"], "source": source, "doc": doc} for source, doc in kept]
    return {"index": index, "alerts": alerts, "docs": docs}


def check_inventory_transform(fx: Fixture, ctx, case: dict) -> None:
    reads = case["input"].get("reads")
    results = case["output"].get("results")
    if not isinstance(reads, list) or not isinstance(results, list) or len(reads) != len(results):
        ctx.report.fail(fx.rel, "input `reads` and output `results` are lists of the same length")
        return
    derived = [inventory_read(state, ctx.registry.validate) for state in reads]
    for i, (got, want) in enumerate(zip(derived, results, strict=True)):
        if got != want:
            ctx.report.fail(fx.rel, f"read {i}: the reader rule gives {got}, the fixture says {want}")
