"""`tsbc verify --self-check`: the contract tree graded offline, with no provider and no network.

Checks, in order: CONTRACT_VERSION; every schema inside the validator subset and every $ref
resolving; the OpenAPI document's structure; every fixture valid against fixture.schema.json,
marked, and consistent with the schemas it names (per kind); the credential-hygiene scan over
everything that ships as data; and coverage (every operation, frame and message has a fixture).
"""

from __future__ import annotations

import base64
import copy
import json
import math
import posixpath
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import profiles
from .fixtures import GRADED_BY_INDEX, Fixture, contract_version, load_fixtures
from .schema import (
    Registry,
    SchemaError,
    check_schema,
    compile_pattern,
    iter_refs,
    loads,
)

FIXTURE_SCHEMA = "schemas/fixture.schema.json"
GRADED_BY_SCHEMA = "schemas/graded-by.schema.json"
VERSION_RE = re.compile(r"^(?:contract-v[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-dev)?|v[0-9]+\.[0-9]+\.[0-9]+)$")

# ---- credential hygiene -------------------------------------------------------------------
# The tarball goes to strangers, so no fixture may carry anything that could authenticate
# anywhere. Every credential-shaped value in a fixture is one of these fixed constants.
SYNTHETIC_CONSTANTS = frozenset({
    "FIXTURE-NOT-A-KEY",
    "FIXTURE-NOT-A-PASSWORD",
    "FIXTURE-WRONG-PASSWORD",
    "FIXTURE-NOT-A-TICKET-0000000000",
    "FIXTURE-TICKET-000000000000000000",
    "SHA256:FIXTURE000000000000000000000000000000000000",
    "SHA256:FIXTURE111111111111111111111111111111111111",
    # From the vault release: padded base64 of fixed ASCII text, each the length its field holds, so a shape
    # fixture's part1, part2 (16 bytes) and the three 32-byte keys fit their patterns and decode to a label.
    "RklYVFVSRS1QQVJUMS0wMA==",                       # "FIXTURE-PART1-00"
    "RklYVFVSRS1QQVJUMi0wMA==",                       # "FIXTURE-PART2-00"
    "RklYVFVSRS1NQVNURVItSEFTSC0wMDAwMDAwMDAwMDA=",   # "FIXTURE-MASTER-HASH-000000000000"
    "RklYVFVSRS1TVE9SQUdFLUtFWS0wMDAwMDAwMDAwMDA=",   # "FIXTURE-STORAGE-KEY-000000000000"
    "RklYVFVSRS1FTkNSWVBUSU9OLUtFWS0wMDAwMDAwMDA=",   # "FIXTURE-ENCRYPTION-KEY-000000000"
})
PEM_HEADER = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")
_OPENSSH_MAGIC = b"openssh-key-v1"
# The magic raw, and as the first 19 base64 characters of a key body (derived, not written out).
OPENSSH_MARKERS = (_OPENSSH_MAGIC.decode(), base64.b64encode(_OPENSSH_MAGIC).decode()[:19])
BASE64_RUN = re.compile(r"[A-Za-z0-9+/_=-]{40,}")
ENTROPY_LIMIT = 4.5  # bits per character
# A Go test (with any subtest path) or a pytest test or class name.
TEST_NAME = re.compile(r"Test[A-Za-z0-9_]*(?:/[A-Za-z0-9_-]+)*|test_[a-z0-9_]+")
HEX_STRING = re.compile(r"^(?:[0-9a-f]{2}){10,}$")
SCANNED_DIRS = ("fixtures", "schemas", "openapi", "protocols")
SCANNED_FILES = ("README.md", "CONTRACT_VERSION")
# Keys whose value is a credential wherever they appear. In a fixture the value must be a
# synthetic constant or a ${placeholder} the runner fills at run time; in collected artefacts it
# must be a synthetic constant (the harness replaces real ones before it collects).
CREDENTIAL_KEYS = frozenset({"password", "current_password", "new_password", "key", "passphrase", "ticket", "private_key", "secret",
                             # from the vault release (E4 DEFAULT 44): the halves, the hash and the two keys
                             "part1", "part2", "master_hash", "storage_key", "encryption_key",
                             # from Release B (the device login): `answers`, read as "each string of the list" — C2
                             # device_login_answer and C3 auth_answer; _leaves already walks into the list
                             "answers"})
PLACEHOLDER_VALUE = re.compile(r"^\$\{[a-z][a-z0-9_]*\}$")
# The published C7 vector files (E4 DEFAULT 44): a known-answer vector copied from a standard (kat-*) or a step of
# the key chain (chain-*), under fixtures/C7/ and nowhere else. They hold credential-named keys with the real hex
# values a test needs — fixtures, not secrets — so the credential-key rule skips exactly these paths. Every other
# check (the fixture format, the schema, the hygiene scan) still runs on them; hex never reads as high-entropy
# base64, and the scan decodes it and looks inside.
VECTOR_FILE = re.compile(r"^fixtures/C7/(?:kat|chain)-[a-z0-9][a-z0-9.-]*\.json$")


def is_vector_file(rel: str) -> bool:
    """Whether a fixture path (relative to the contract root) is a published C7 vector file."""
    return VECTOR_FILE.fullmatch(rel) is not None


def _leaves(node: Any, where: str):
    """(path, value) for every scalar inside node, however deep in lists and objects."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _leaves(item, f"{where}[{i}]")
    else:
        yield where, node



def _integrity_identity(event: dict) -> str | None:
    """Only an exact boundary-derived integrity identity is non-credential `detail.key`."""
    if event.get("action") != "recording.output_incomplete":
        return None
    sid, detail = event.get("session_id"), event.get("detail")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", sid):
        return None
    if not isinstance(detail, dict) or type(detail.get("version")) is not int or detail["version"] != 1:
        return None
    kind = detail.get("kind")
    if not isinstance(kind, str):
        return None
    fields = {"extent": ("from_seq", "through_seq", "from_byte", "through_byte"),
              "unconfirmed": ("baseline_seq", "baseline_bytes")}
    if kind in fields:
        values = [detail.get(f) for f in fields[kind]]
        if any(type(v) is not int or not 0 <= v <= 0xFFFFFFFFFFFFFFFF for v in values):
            return None
        return f"ri1:{sid}:{'e' if kind == 'extent' else 'u'}:" + ":".join(map(str, values))
    if kind in ("continuation", "continuation_unconfirmed"):
        generation = detail.get("generation")
        if type(generation) is not int or not 1 <= generation <= 0xFFFFFFFFFFFFFFFF:
            return None
        return f"ri1:{sid}:{'c' if kind == 'continuation' else 'q'}:{generation}"
    if kind in ("sealed", "not_recorded"):
        return f"ri1:{sid}:{'s' if kind == 'sealed' else 'n'}"
    if kind == "unrecorded":
        return f"ri1:{sid}:u:null:null"
    return None

def credential_problems(node: Any, *, placeholders: bool, where: str = "$") -> list[str]:
    """Every value under a credential-named key that is not synthetic — the key's own value, or
    anything nested in it, so a credential cannot hide in a list or an object. Never quotes it."""
    problems = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}"
            if key == "detail" and isinstance(value, dict):
                identity = _integrity_identity(node)
                if identity is not None and value.get("key") == identity:
                    value = {k: v for k, v in value.items() if k != "key"}
            if key in CREDENTIAL_KEYS:
                allowed = "a documented synthetic constant" + (" or a ${placeholder}" if placeholders else "")
                for path, leaf in _leaves(value, here):
                    synthetic = isinstance(leaf, str) and (
                        leaf in SYNTHETIC_CONSTANTS or (placeholders and PLACEHOLDER_VALUE.match(leaf)))
                    if not synthetic:
                        problems.append(f"{path}: a credential-named value must be {allowed}")
            else:
                problems += credential_problems(value, placeholders=placeholders, where=here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            problems += credential_problems(item, placeholders=placeholders, where=f"{where}[{i}]")
    return problems


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def scan_text(text: str) -> list[str]:
    """Credential-shaped findings in one string."""
    hits = []
    if PEM_HEADER.search(text):
        hits.append("PEM header")
    hits += [f"OpenSSH private-key marker {m!r}" for m in OPENSSH_MARKERS if m in text]
    for match in BASE64_RUN.finditer(text):
        run = match.group()
        if run in SYNTHETIC_CONSTANTS or any(run in c for c in SYNTHETIC_CONSTANTS):
            continue
        entropy = shannon_entropy(run)
        if entropy > ENTROPY_LIMIT:
            hits.append(f"{len(run)}-character base64-like run with {entropy:.2f} bits/char")
    return hits


def _strip_test_names(test_id: str) -> str:
    """The parts of a `test_id` that are not a test name, joined back by `::`: what stays to be
    scanned. Only the test name itself is exempt, never the string it sits beside."""
    return "::".join(p for p in test_id.split("::") if not TEST_NAME.fullmatch(p))


# A `"test_id"` key with a plain string value: no backslash inside, so an escaped value is left as
# it is in the raw text and is scanned there.
TEST_ID_VALUE = re.compile(r'("test_id"\s*:\s*")([^"\\]*)(")')


def _strip_raw_test_names(text: str) -> str:
    """The raw index text with each plain `"test_id"` value replaced by its non-test-name parts."""
    return TEST_ID_VALUE.sub(lambda m: m.group(1) + _strip_test_names(m.group(2)) + m.group(3), text)


# Only the complete shape in common.schema.json $defs/device_id is a non-secret device id.
# Escaped values remain in the raw text and are scanned there.
DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]")
DEVICE_ID_VALUE = re.compile(rf'("device_id"\s*:\s*")({DEVICE_ID_PATTERN.pattern})(")')


def _strip_raw_device_ids(text: str) -> str:
    """Empty only whole, device-id-shaped values under `"device_id"` in raw JSON or JSONL.
    Other values and the same string under any other key remain scanned."""
    return DEVICE_ID_VALUE.sub(lambda m: m.group(1) + m.group(3), text)


def _doc_without_device_ids(node: Any) -> Any:
    """A copy with only whole, device-id-shaped strings under `"device_id"` emptied."""
    if isinstance(node, dict):
        return {key: ("" if key == "device_id" and isinstance(value, str)
                      and DEVICE_ID_PATTERN.fullmatch(value)
                      else _doc_without_device_ids(value)) for key, value in node.items()}
    if isinstance(node, list):
        return [_doc_without_device_ids(item) for item in node]
    return node


def _index_without_test_names(index):
    """A deep copy of the parsed index with every entry's `test_id` replaced by its non-test-name
    parts, so the parsed strings scanned are the citation minus the test name."""
    stripped = copy.deepcopy(index)
    if not isinstance(stripped, dict):
        return stripped
    for entries in stripped.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("test_id"), str):
                entry["test_id"] = _strip_test_names(entry["test_id"])
    return stripped


def _json_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _json_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _json_strings(item)


def scan_tree(root: Path) -> tuple[int, list[str]]:
    """(files scanned, findings) over the data that ships in the tarball."""
    paths = [p for d in SCANNED_DIRS if (root / d).is_dir() for p in sorted((root / d).rglob("*"))
             if p.is_file()]
    paths += [root / f for f in SCANNED_FILES if (root / f).is_file()]
    return len(paths), scan_files(root, paths)


def scan_files(root: Path, paths: list[Path]) -> list[str]:
    """Hygiene findings in the given files, named relative to root. A test name in a graded-by index
    entry's `test_id` is not a credential; the same string in any other field or file is still scanned.
    Only a `"device_id"` value matching its schema's whole shape is exempt (device-trust.md)."""
    findings = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        texts = [raw.decode("latin-1")]  # one char per byte, so binaries are scanned too
        if path.suffix in (".json", ".jsonl"):
            texts[0] = _strip_raw_device_ids(texts[0])
        if path.suffix == ".json":
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                doc = None
            if doc is not None and rel == f"fixtures/{GRADED_BY_INDEX}":
                doc = _index_without_test_names(doc)
                texts[0] = _strip_raw_test_names(raw.decode("utf-8"))
            strings = list(_json_strings(_doc_without_device_ids(doc))) if doc is not None else []
            texts += strings  # catches a secret hidden behind JSON \u escapes
            texts += [bytes.fromhex(s).decode("latin-1") for s in strings if HEX_STRING.match(s)]
        for hit in sorted({h for t in texts for h in scan_text(t)}):
            findings.append(f"{rel}: {hit}")
    return findings


# ---- report -------------------------------------------------------------------------------

@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def fail(self, where: str, message: str) -> None:
        self.failures.append(f"{where}: {message}")

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class Context:
    """What per-kind checks receive."""
    root: Path
    registry: Registry
    report: Report
    fixtures: list[Fixture]


KindCheck = Callable[[Fixture, Context], None]
CoverageCheck = Callable[[Context], None]
KIND_CHECKS: dict[str, KindCheck] = {}
TREE_CHECKS: list[CoverageCheck] = []      # always run, after the schemas load
COVERAGE_CHECKS: list[CoverageCheck] = []  # run when the tree has fixtures
OPENAPI_CHECKS: list[Callable[[Context], None]] = []


def check_instance(fx: Fixture, ctx: Context) -> None:
    """Each document validates, or fails to validate, exactly as the fixture says it should."""
    ref = fx.case["schema"]
    if ref.split("#", 1)[0] not in ctx.registry.docs:
        ctx.report.fail(fx.rel, f"schema {ref} is not in the tree")
        return
    for i, inst in enumerate(fx.case["instances"], 1):
        try:
            errors = ctx.registry.validate(inst["doc"], ref)
        except SchemaError as exc:
            ctx.report.fail(fx.rel, str(exc))
            return
        if inst["valid"] and errors:
            ctx.report.fail(fx.rel, f"instance {i} should be valid: {errors[0]}")
        elif not inst["valid"] and not errors:
            ctx.report.fail(fx.rel, f"instance {i} is marked invalid, but it validates: the case tests nothing")


KIND_CHECKS["instance"] = check_instance

# Which schema documents each fixture kind exercises, beyond an instance fixture's own `schema`.
KIND_ROOTS = {
    "rest": ("openapi/tsb.openapi.json",),
    "ws": ("openapi/tsb.openapi.json", "schemas/ws-frames.schema.json"),
    "conn-control": ("schemas/ipc-control.schema.json",),
    "supervisor-control": ("schemas/supervisor.schema.json",),
    "c3-frames": ("schemas/ipc-data-frame.schema.json",),
    "c8-recording": ("schemas/ttyrec.schema.json",),
    "c4-vt-frames": ("schemas/vt-ipc.schema.json", "schemas/ipc-data-frame.schema.json"),
    "c3-to-c8": ("schemas/ipc-data-frame.schema.json", "schemas/ttyrec.schema.json"),
    "c5-rediscovery": ("schemas/worker-manifest.schema.json", "schemas/supervisor-manifest.schema.json"),
    "c5-rediscovery-v0.2": ("schemas/worker-manifest.schema.json", "schemas/supervisor-manifest.schema.json",
                            "schemas/spool-entry.schema.json"),
    "c8-compare": ("schemas/ttyrec.schema.json",),
    "inventory-layout": ("schemas/inventory-target.schema.json",),
    "inventory-yaml-mapping": ("schemas/inventory-target.schema.json",),
    "inventory-write-then-observe": ("schemas/inventory-target.schema.json",),
    "inventory-bulk-swap": ("schemas/inventory-target.schema.json",),
}
# Not wire contracts: the format of fixture files, of a harness's artefacts directory, and of the
# graded_by side index (the schema is a schema of its own file, not of anything that crosses C1-C8).
RUNNER_SCHEMAS = frozenset({FIXTURE_SCHEMA, "schemas/artifacts-collected.schema.json",
                            "schemas/graded-by.schema.json"})
AUDIT_SCHEMA = "schemas/audit-event.schema.json"


def _roots(fx: Fixture) -> tuple[str, ...]:
    case = fx.case
    if fx.kind == "instance":
        return (case.get("schema", "").split("#", 1)[0],)
    key = {"sequence": "socket", "bytes": "format", "transform": "transform"}.get(fx.kind)
    return KIND_ROOTS.get(case.get(key) if key else fx.kind, ())


def schema_coverage(ctx: Context) -> None:
    """Every wire schema is reached, through $refs, from some fixture."""
    reached, todo = set(), [r for fx in ctx.fixtures for r in _roots(fx)]
    while todo:
        name = todo.pop()
        if name in reached or name not in ctx.registry.docs:
            continue
        reached.add(name)
        for ref in iter_refs(ctx.registry.docs[name]):
            path = ref.split("#", 1)[0]
            if path:
                todo.append(posixpath.normpath(posixpath.join(posixpath.dirname(name), path)))
    for name in sorted(ctx.registry.docs):
        if name.startswith("schemas/") and name not in RUNNER_SCHEMAS and name not in reached:
            ctx.report.fail("coverage", f"{name} is exercised by no fixture")


def audit_coverage(ctx: Context) -> None:
    """Every audit action has at least one valid example line."""
    doc = ctx.registry.docs.get(AUDIT_SCHEMA)
    if doc is None:
        return
    actions = set()
    for family in doc["$defs"].values():
        action = family.get("properties", {}).get("action", {})
        actions |= set(action.get("enum", [])) | ({action["const"]} if "const" in action else set())
    shown = {inst["doc"].get("action") for fx in ctx.fixtures
             if fx.kind == "instance" and fx.case.get("schema") == AUDIT_SCHEMA
             for inst in fx.case["instances"] if inst["valid"] and isinstance(inst["doc"], dict)}
    for action in sorted(actions - shown):
        ctx.report.fail("coverage", f"audit action {action!r} has no valid example in a fixture")


def grade_rules(ctx: Context) -> None:
    """How each fixture is graded is itself checked: a required-green fixture that only a provider
    can grade names that provider's test, and a `with:` fixture leans on one that a run grades."""
    names = Counter(fx.name for fx in ctx.fixtures)
    for name in sorted(n for n, c in names.items() if c > 1):
        ctx.report.fail("fixtures", f"fixture name {name!r} is used more than once (with: grades name fixtures)")
    by_name = {fx.name: fx for fx in ctx.fixtures}
    for fx in ctx.fixtures:
        required = fx.mark.get("required-green")
        if fx.grade == "reference":
            if required and not fx.doc.get("graded_by"):
                ctx.report.fail(fx.rel, "a required-green reference fixture must name the provider test that "
                                        "grades it (graded_by)")
        elif "graded_by" in fx.doc:
            ctx.report.fail(fx.rel, "graded_by belongs to reference fixtures")
        if fx.grade.startswith("with:"):
            other = by_name.get(fx.grade.split(":", 1)[1])
            if other is None:
                ctx.report.fail(fx.rel, f"{fx.grade} names no fixture")
            elif not (other.grade == "live" or other.grade.startswith("artifacts:")):
                ctx.report.fail(fx.rel, f"{fx.grade}: that fixture is not graded live or from artefacts")
            elif required and (not other.mark.get("required-green") or
                               profiles.epoch_index(other.mark["required-green"]) > profiles.epoch_index(required)):
                ctx.report.fail(fx.rel, f"{fx.grade}: that fixture is not required green by {required}")


COVERAGE_CHECKS.extend([schema_coverage, audit_coverage, grade_rules])


# ---- the graded_by side index -----------------------------------------------------------------

def load_index(root: Path, registry: Registry, report: Report | None = None) -> dict[str, list[dict]]:
    """The graded_by side index, {fixture label: its checked entries}, validated against its
    schema. A tree with no index reads as an empty one, so a run still grades: the index is what
    makes a reference fixture's citation checkable, never what makes the runner start."""
    path = root / "fixtures" / GRADED_BY_INDEX
    if not path.is_file():
        return {}
    rel = f"fixtures/{GRADED_BY_INDEX}"
    try:
        doc = loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if report is not None:
            report.fail(rel, f"not readable JSON: {exc}")
        return {}
    if GRADED_BY_SCHEMA not in registry.docs:
        if report is not None:
            report.fail(rel, f"{GRADED_BY_SCHEMA} is not in the tree, so the index cannot be checked")
        return {}
    errors = registry.validate(doc, GRADED_BY_SCHEMA)
    if errors and report is not None:
        for err in errors[:5]:
            report.fail(rel, err)
        return {}
    if not isinstance(doc, dict):  # unreachable on a valid document; a malformed one fails above
        return {}
    return {name: entries for name, entries in doc.items() if isinstance(entries, list)}


def index_rules(ctx: Context) -> None:
    """The index is the record a run checks a reference fixture's citation against, so it is held
    to the fixture it speaks for: an entry for a fixture that is not a reference one, an entry
    naming a unit the fixture's own `graded_by` does not, or a required-green reference fixture
    with no entry at all is a citation a run cannot grade.

    A published-only fixture is not asked for an entry: at e1 nothing grades C3 `reattach-non-pty`
    (no v0.2 worker opens a non-PTY session), so requiring one would name a test that cannot exist.
    An entry that is owed is the index recording that no test exists yet, and the grading rule
    never grades on it; what self-check holds it to is the epoch the claim is already due.
    """
    index = load_index(ctx.root, ctx.registry, ctx.report)
    by_name = {f"{fx.seam}/{fx.name}": fx for fx in ctx.fixtures}
    for label, entries in sorted(index.items()):
        fx = by_name.get(label)
        where = f"fixtures/{GRADED_BY_INDEX}: {label}"
        if fx is None:
            ctx.report.fail(where, "names no fixture")
            continue
        if fx.grade != "reference":
            ctx.report.fail(where, f"is not a reference fixture (graded {fx.grade}); graded_by belongs "
                                   "to reference fixtures")
            continue
        units = {g["unit"] for g in fx.doc.get("graded_by", [])}
        for i, entry in enumerate(entries):
            entry_where = f"{where}[{i}]"
            if not isinstance(entry, dict):
                continue
            if entry.get("unit") not in units:
                ctx.report.fail(entry_where, f"names unit {entry.get('unit')!r}, which the fixture's own "
                                             f"graded_by does not ({', '.join(sorted(units)) or 'none'})")
            file = entry.get("file")
            if isinstance(file, str) and (".." in file.split("/") or file.startswith("/")):
                ctx.report.fail(entry_where, "a file must be repo-relative: it may not begin with '/' or "
                                             "hold a '..' segment")
    for label, fx in sorted(by_name.items()):
        required = (profiles.check_mark(fx.mark) == []
                    and profiles.status(fx.mark, "e6") == profiles.REQUIRED)
        if fx.grade != "reference" or not required:
            continue
        if label not in index:
            ctx.report.fail(f"fixtures/{GRADED_BY_INDEX}", f"a required-green reference fixture has no "
                                                          f"entry ({label})")


def owed_rules(ctx: Context) -> None:
    """An owed entry is never graded, so it may not sit on a fixture a closed epoch already
    requires green: at e0 and e1 the claim is already due, and refusing owed there is what keeps
    an unfinished plan from being graded at the epoch that said it was done."""
    index = load_index(ctx.root, ctx.registry)
    by_name = {f"{fx.seam}/{fx.name}": fx for fx in ctx.fixtures}
    for label, entries in sorted(index.items()):
        fx = by_name.get(label)
        if fx is None or fx.grade != "reference":
            continue
        required = fx.mark.get("required-green")
        if required not in ("e0", "e1"):
            continue
        for i, entry in enumerate(entries):
            if isinstance(entry, dict) and "owed" in entry:
                ctx.report.fail(f"fixtures/{GRADED_BY_INDEX}: {label}[{i}]",
                                f"is owed, but the fixture is required green at {required}: a closed "
                                "epoch's requirement cannot be owed")


COVERAGE_CHECKS.extend([index_rules, owed_rules])


def unshared_syntax(pattern: str) -> str | None:
    """The first construct in `pattern` outside the syntax Python re, ECMA-262 and Go RE2 share:
    lookahead, lookbehind, a backreference, or Python's named group or named backreference. Escaped tokens and those inside a class are not it."""
    i, in_class = 0, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            nxt = pattern[i + 1:i + 2]
            if not in_class:
                if len(nxt) == 1 and nxt in "123456789":
                    return pattern[i:i + 2]
                if pattern.startswith("k<", i + 1):
                    return pattern[i:i + 3]
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        else:
            for token in ("(?<=", "(?<!", "(?=", "(?!", "(?P<", "(?P="):
                if pattern.startswith(token, i):
                    return token
        i += 1
    return None


def _pattern_problems(node: Any, name: str, pointer: str = "#") -> list[str]:
    problems = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key == "pattern" and isinstance(value, str):
                found = unshared_syntax(value)
                if found:
                    problems.append(f"{name}{here}: pattern uses {found}, outside the syntax Python re, "
                                    "ECMA-262 and Go RE2 share (no lookaround, no backreference)")
            else:
                problems += _pattern_problems(value, name, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            problems += _pattern_problems(item, name, f"{pointer}/{i}")
    return problems


def compile_published(pattern: str):
    """A pattern published in an annotation (x-gap-line), read as a schema pattern is."""
    import re
    found = unshared_syntax(pattern) if isinstance(pattern, str) else None
    if found:
        raise ValueError(f"uses {found}, outside the syntax Python re, ECMA-262 and Go RE2 share")
    try:
        return compile_pattern(pattern)
    except re.error as exc:
        raise ValueError(str(exc)) from None


OCTET_STREAM = "application/octet-stream"
# The one operation whose 200 body is octet-stream rather than JSON (C1 decision 3).
OCTET_STREAM_RESPONSES = frozenset({("GET", "/sessions/{id}/output", "200")})


def admit_octet_stream() -> None:
    """Widen the OpenAPI shape so a response may carry application/octet-stream, then register the
    policy that admits it on exactly the pages 200. Safe to call on every run (the policy is registered once), after `_exchange` is imported: the
    shape lives there, and the pages endpoint is the one place a binary body crosses C1."""
    from . import _exchange

    media = _exchange.OPENAPI_SHAPE["$defs"]["media"]
    media["properties"][OCTET_STREAM] = {"$ref": "#/$defs/media_type"}
    media["oneOf"] = [
        {"required": [_exchange.JSON_MEDIA],
         "properties": {_exchange.EVENT_STREAM: False, OCTET_STREAM: False}},
        {"required": [_exchange.EVENT_STREAM],
         "properties": {_exchange.JSON_MEDIA: False, OCTET_STREAM: False}},
        {"required": [OCTET_STREAM],
         "properties": {_exchange.JSON_MEDIA: False, _exchange.EVENT_STREAM: False}},
    ]
    if _octet_stream_policy not in _exchange.selfcheck.OPENAPI_CHECKS:  # run() calls this every time
        _exchange.selfcheck.OPENAPI_CHECKS.append(_octet_stream_policy)


def _octet_stream_policy(ctx: Context) -> None:
    """application/octet-stream is admitted only as the 200 body of GET /sessions/{id}/output."""
    from ._exchange import OPENAPI, _deref

    reg, report = ctx.registry, ctx.report
    doc = reg.docs[OPENAPI]
    for template, item in doc["paths"].items():
        for method in ("get", "post", "delete", "put"):
            op = item.get(method)
            if not op:
                continue
            if OCTET_STREAM in op.get("requestBody", {}).get("content", {}):
                report.fail(OPENAPI, f"{method.upper()} {template}: application/octet-stream is never "
                                     "a request body")
            for status, resp in op["responses"].items():
                body = _deref(reg, resp) if isinstance(resp, dict) and "$ref" in resp else resp
                if OCTET_STREAM in body.get("content", {}) and \
                        (method.upper(), template, status) not in OCTET_STREAM_RESPONSES:
                    report.fail(OPENAPI, f"{method.upper()} {template} {status}: application/octet-stream is "
                                         "admitted only for GET /sessions/{id}/output's 200 response")


def schema_problems(registry: Registry) -> tuple[list[str], int]:
    """(problems, $refs checked): every schema inside the validator subset — the documents under
    schemas/ and every schema object in the OpenAPI document — and every $ref resolving. Run by
    self-check and before every live or --artifacts run, so no run grades against a schema the
    validator cannot fully enforce."""
    from ._exchange import OPENAPI, openapi_schema_problems

    problems, refs = [], 0
    for name, doc in sorted(registry.docs.items()):
        if name.startswith("schemas/"):
            try:
                check_schema(doc, name)
            except SchemaError as exc:
                problems.append(f"{name}: {exc}")
            problems += _pattern_problems(doc, name)
        for ref in iter_refs(doc):
            refs += 1
            try:
                registry.resolve(ref, name)
            except SchemaError as exc:
                problems.append(f"{name}: {exc}")
    if OPENAPI in registry.docs:
        problems += openapi_schema_problems(registry)
        problems += _pattern_problems(registry.docs[OPENAPI], OPENAPI)
    return problems, refs


def _check_fixture(fx: Fixture, ctx: Context) -> None:
    if not is_vector_file(fx.rel):  # a published vector file is exempt by path (DEFAULT 44), nothing else is
        for problem in credential_problems(fx.doc, placeholders=True):  # even in a malformed fixture
            ctx.report.fail(fx.rel, problem)
    errors = ctx.registry.validate(fx.doc, FIXTURE_SCHEMA)
    if errors:
        for err in errors[:5]:
            ctx.report.fail(fx.rel, err)
        return
    if fx.name != fx.path.stem:
        ctx.report.fail(fx.rel, f"name {fx.name!r} must equal the file name")
    if fx.seam != fx.path.parent.name:
        ctx.report.fail(fx.rel, f"seam {fx.seam} does not match its directory {fx.path.parent.name}")
    for problem in profiles.check_mark(fx.doc["mark"]):
        ctx.report.fail(fx.rel, problem)
    check = KIND_CHECKS.get(fx.kind)
    if check is None:
        ctx.report.fail(fx.rel, f"no self-check exists for kind {fx.kind!r}")
        return
    check(fx, ctx)


def _stray_binaries(root: Path, fixtures: list[Fixture], report: Report) -> None:
    referenced = set()
    for fx in fixtures:
        for value in _json_strings(fx.case):
            if value.endswith(".bin"):
                referenced.add((fx.path.parent / value).resolve())
    base = root / "fixtures"
    for path in sorted(base.rglob("*.bin")) if base.is_dir() else []:
        if path.resolve() not in referenced:
            report.fail(path.relative_to(root).as_posix(), "no fixture references this byte file")


def run(root: Path, profile: str | None = None) -> Report:
    # Each module registers its checks on import: C1/C2, the byte formats, C3 control, artefacts.
    from . import _binfmt, _exchange, _ipc, artifacts  # noqa: F401
    admit_octet_stream()

    report = Report()
    try:
        version = contract_version(root)
    except OSError as exc:
        report.fail("CONTRACT_VERSION", str(exc))
        return report
    if not VERSION_RE.match(version):
        report.fail("CONTRACT_VERSION", f"{version!r} is not contract-vX.Y, contract-vX.Y.Z or their -dev "
                                        f"forms, nor a plain vX.Y.Z release tag")
    report.lines.append(f"contract: {version} (tree: {root})")

    try:
        registry = Registry.from_tree(root)
    except SchemaError as exc:
        report.fail("schemas", str(exc))
        return report
    schema_docs = [n for n in registry.docs if n.startswith("schemas/")]
    problems, refs = schema_problems(registry)
    report.failures += problems
    report.lines.append(f"schemas: {len(schema_docs)} documents inside the validator subset, {refs} $refs checked")

    fixtures, load_errors = load_fixtures(root)
    ctx = Context(root, registry, report, fixtures)
    for check in TREE_CHECKS:
        check(ctx)
    if "openapi/tsb.openapi.json" in registry.docs:
        for check in OPENAPI_CHECKS:
            check(ctx)
    else:
        report.lines.append("openapi: no openapi/tsb.openapi.json in this tree")

    for err in load_errors:
        report.fail("fixtures", err)
    if fixtures and FIXTURE_SCHEMA not in registry.docs:
        report.fail(FIXTURE_SCHEMA, "missing, so no fixture can be checked")
    else:
        for fx in fixtures:
            _check_fixture(fx, ctx)
    _stray_binaries(root, fixtures, report)
    by_seam = Counter(fx.seam for fx in fixtures)
    per_seam = ", ".join(f"{s} {n}" for s, n in sorted(by_seam.items())) or "none"
    report.lines.append(f"fixtures: {len(fixtures)} fixtures ({per_seam})")

    if fixtures:
        for check in COVERAGE_CHECKS:
            check(ctx)

    scanned, findings = scan_tree(root)
    for finding in findings:
        report.fail("hygiene", finding)
    report.lines.append(f"hygiene: {scanned} files scanned for PEM headers, OpenSSH key markers and "
                        f"high-entropy base64 ({len(findings)} findings)")

    if profile is not None:
        counts = Counter()
        for fx in fixtures:
            if not profiles.check_mark(fx.mark):
                counts[profiles.status(fx.mark, profile)] += 1
        report.lines.append(f"profile {profile}: {counts[profiles.REQUIRED]} required-green, "
                            f"{counts[profiles.PUBLISHED]} published only, {counts[profiles.FUTURE]} future")
    return report
