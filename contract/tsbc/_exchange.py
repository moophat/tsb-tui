"""C1 and C2 fixtures (kinds `rest` and `ws`): checked against the OpenAPI document offline,
executed against a running core live.

Offline, every step must name an operation the OpenAPI document declares, expect a status that
operation declares, send a body its request schema accepts (unless the step says the body is
invalid on purpose, and then it must not validate), and carry examples its response schema
accepts. Live, each response is validated against the same schema, so a fixture and the document
cannot drift apart without self-check or a run noticing.

Nothing a live run prints contains a credential, a ticket or a byte of terminal output.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import _http, _ws, selfcheck
from . import _wsframe as wf
from ._run import INPUTS, InputMissing, Run
from .fixtures import Fixture, contract_version
from .schema import (
    Registry,
    SchemaError,
    check_schema,
    json_equal,
    loads,
    pointer_get,
    validate,
)

OPENAPI = "openapi/tsb.openapi.json"
WS_FRAMES = "schemas/ws-frames.schema.json"
JSON_MEDIA, EVENT_STREAM = "application/json", "text/event-stream"
# The one response whose body is not JSON: the operator alert stream, server-sent events.
EVENT_STREAMS = frozenset({("GET", "/alerts", "200")})
# The event type whose data is the stream's per-event schema; a receiver ignores any other type.
ALERT_EVENT = "alert"
# A live step reads an event stream as it arrives, and stops at the stream's end, after this many
# events, or at the run's timeout: an alert stream never ends by itself.
MAX_STREAM_EVENTS = 64
# The C2 frames no live run can produce: each needs a timer value (notice, quiet_prompt and its
# answer), a worker that predates the core (input_refused unreachable), a mocked fault (data_path),
# a restart or a worker-side condition the runner cannot provoke (stale, stale_prompt, stale_answer,
# prefix), a request/reply the runner cannot force (request_refused, size), a client set it does not
# track (clients), or a timing knob (handoff). A provider's own test grades them, so a valid instance
# in a reference fixture covers them; every other frame is covered only by a live exchange, as at v0.1.
# The device login's frames, but its first (device_login_attach, which the live ticket fixtures send), come from
# the device: a run has no device to answer, so a provider's own test grades them (Release B).
REFERENCE_FRAMES = frozenset({"notice", "quiet_prompt", "quiet_prompt_answer", "input_refused", "data_path",
                              "stale", "stale_prompt", "stale_answer", "clients", "request_refused",
                              "prefix", "size", "handoff", "device_login_hostkey_answer", "device_login_answer",
                              "device_login_cancel", "device_login_hostkey", "device_login_prompt",
                              "device_login_result"})

# The era rule (F5, plan.md § Runner 1): a fixture reads server frames as its own era knew them, so
# a frame type introduced at a later tag than the fixture's mark epoch is skipped by the live runner
# exactly as an unknown `t` is. This keeps v0.2.1's `token-event-on-detach` (attached → token, with
# prefix and clients now between them) green at e1/e2 without editing it (R2). The map holds the
# server frame types each epoch's schema knew; an epoch not listed knows the full current set.
_ERA_SERVER_FRAMES: dict[str, frozenset[str]] = {
    "e0": frozenset({"attached", "notice", "quiet_prompt", "token", "input_refused", "data_path"}),
    "e1": frozenset({"attached", "notice", "quiet_prompt", "token", "input_refused", "data_path"}),
}


def era_server_frames(registry: Registry, mark: dict) -> set[str]:
    """The server frame types a fixture marked `mark` reads as its own era knew them (F5).

    The fixture's era is its mark's earliest epoch (required-green, else published). A frame type
    the schema defines now but that epoch's schema did not is omitted, so the runner skips it like
    an unknown `t`. An epoch with no entry knows every type the schema defines."""
    epoch = mark.get("required-green") or mark.get("published")
    known = _ERA_SERVER_FRAMES.get(epoch)
    if known is None:
        return frame_types(registry, "server")
    return frame_types(registry, "server") & set(known)


METHODS = ("get", "post", "delete", "put")
PLACEHOLDER = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")
# Offline, every placeholder becomes this: valid as an id, a ticket, a user name and a TERM.
SAMPLE = "sample00000000000000000000"
SECRET_FIELDS = frozenset({"ticket", "password", "current_password", "new_password", "key", "passphrase"})
_NO_BODY = object()
MAX_WAIT = 120  # seconds a wait step may sleep: expires_in is at most 60
_SESSION_PATH = re.compile(r"/sessions/([A-Za-z0-9][A-Za-z0-9._-]{0,127})\Z")
MAX_OUTPUT = 1 << 20  # bytes of streamed terminal output a socket keeps; a needle is a few dozen


def _keep(output: bytearray, data: bytes) -> None:
    """Append streamed bytes, keeping only the last MAX_OUTPUT: enough for any output_contains
    needle, and bounded however much a provider streams while the runner waits."""
    output += data
    if len(output) > MAX_OUTPUT:
        del output[:len(output) - MAX_OUTPUT]
# A heuristic, not a proof: an error message must not name a path on the broker host (Contract §4).
# It catches the runtime tree, an absolute path under a filesystem root, and a home-relative path;
# an API path such as /sessions/x is not one of those roots.
HOST_PATH = re.compile(
    r"/tmp/tsb|~/|(?:^|[^A-Za-z0-9_.])/(?:tmp|var|home|root|etc|opt|usr|srv|run|proc|sys|dev|mnt|media|"
    r"data|lib|lib64|bin|sbin|boot|snap)(?:/|$|[^A-Za-z0-9_-])")


def host_detail(message: str) -> bool:
    return bool(HOST_PATH.search(message))


def _error_message(body: Any) -> str | None:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
        return message if isinstance(message, str) else None
    return None


class Failed(Exception):
    """A live fixture failed. The message is safe to print."""


@dataclass
class StreamedResponse(_http.Response):
    """A 200 event stream, read as it arrived: (type, data) for each event read."""
    events: list[tuple[str, str]] = field(default_factory=list)


# ---- the OpenAPI document ---------------------------------------------------------------------

@dataclass(frozen=True)
class Operation:
    method: str
    template: str
    op: dict


def operations(registry: Registry) -> list[Operation]:
    paths = registry.docs[OPENAPI].get("paths", {})
    return [Operation(m.upper(), t, item[m]) for t, item in paths.items() for m in METHODS if m in item]


def _matches_template(template: str, path: str) -> bool:
    want = template.strip("/").split("/")
    have = path.split("?", 1)[0].strip("/").split("/")
    return len(want) == len(have) and all(
        (w.startswith("{") and w.endswith("}") and h != "") or w == h for w, h in zip(want, have))


def find_operation(registry: Registry, method: str, path: str) -> Operation | None:
    found = [o for o in operations(registry) if o.method == method and _matches_template(o.template, path)]
    found.sort(key=lambda o: o.template.count("{"))  # a literal segment beats a template
    return found[0] if found else None


def _deref(registry: Registry, node: Any) -> Any:
    for _ in range(8):
        if not (isinstance(node, dict) and set(node) == {"$ref"}):
            return node
        node, _doc = registry.resolve(node["$ref"], OPENAPI)
    raise SchemaError(f"{OPENAPI}: $ref chain too deep")


def response(registry: Registry, op: Operation, status: int) -> dict | None:
    resp = op.op.get("responses", {}).get(str(status))
    return _deref(registry, resp) if resp is not None else None


def json_schema_of(registry: Registry, holder: dict | None) -> Any | None:
    if not holder:
        return None
    media = holder.get("content", {}).get("application/json")
    return media.get("schema") if media else None


def request_schema(registry: Registry, op: Operation) -> Any | None:
    return json_schema_of(registry, op.op.get("requestBody"))


def event_schema_of(registry: Registry, holder: dict | None) -> Any | None:
    """The per-event schema of an event-stream response: its x-itemSchema."""
    media = (holder or {}).get("content", {}).get(EVENT_STREAM)
    return media.get("x-itemSchema") if media else None


def accept_of(registry: Registry, op: Operation | None) -> str:
    """The Accept a request to `op` sends: the media type of its success response."""
    if op is not None:
        for status, resp in op.op.get("responses", {}).items():
            if status.startswith("2") and EVENT_STREAM in _deref(registry, resp).get("content", {}):
                return EVENT_STREAM
    return JSON_MEDIA


def websocket_operation(registry: Registry) -> Operation | None:
    for o in operations(registry):
        if "x-websocket" in o.op:
            return o
    return None


def close_codes(registry: Registry) -> set[int]:
    return {int(code) for code in registry.docs[WS_FRAMES].get("x-close-codes", {})}


def frame_types(registry: Registry, side: str) -> set[str]:
    """The `t` values the ws-frames schema defines for `client` or `server`."""
    frames = registry.docs[WS_FRAMES]["$defs"]
    types = set()
    for branch in frames[side]["oneOf"]:
        target, _ = registry.resolve(branch["$ref"], WS_FRAMES)
        types.add(target["properties"]["t"]["const"])
    return types


def cookie_name(registry: Registry) -> str:
    return registry.docs[OPENAPI]["components"]["securitySchemes"]["session"]["name"]


# ---- the document's structure (self-check) ----------------------------------------------------

_REF = {"type": "object", "required": ["$ref"], "additionalProperties": False,
        "properties": {"$ref": {"type": "string"}}}
_SECURITY = {"type": "array", "items": {"type": "object", "additionalProperties": {"type": "array"}}}
OPENAPI_SHAPE = {
    "type": "object",
    "required": ["openapi", "info", "paths", "components"],
    "additionalProperties": False,
    "properties": {
        "openapi": {"const": "3.1.0"},
        "info": {"type": "object", "required": ["title", "version"], "additionalProperties": False,
                 "properties": {"title": {"type": "string"}, "version": {"type": "string"},
                                "description": {"type": "string"}}},
        "servers": {"type": "array", "items": {"type": "object", "required": ["url"]}},
        "security": _SECURITY,
        "components": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "securitySchemes": {"type": "object"},
                "parameters": {"type": "object", "additionalProperties": {"$ref": "#/$defs/parameter"}},
                "responses": {"type": "object", "additionalProperties": {"$ref": "#/$defs/response"}},
                "schemas": {"type": "object"},
            },
        },
        "paths": {"type": "object", "additionalProperties": {"$ref": "#/$defs/path_item"}},
    },
    "$defs": {
        "parameter": {"anyOf": [_REF, {
            "type": "object", "required": ["name", "in", "schema"], "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "in": {"enum": ["path", "query", "header", "cookie"]},
                           "required": {"type": "boolean"}, "description": {"type": "string"},
                           "schema": {"type": "object"}}}]},
        "media": {"type": "object", "additionalProperties": False,
                  "properties": {JSON_MEDIA: {"$ref": "#/$defs/media_type"}, EVENT_STREAM: {"$ref": "#/$defs/event_media"}},
                  "oneOf": [{"required": [JSON_MEDIA], "properties": {EVENT_STREAM: False}},
                            {"required": [EVENT_STREAM], "properties": {JSON_MEDIA: False}}]},
        "media_type": {"type": "object", "required": ["schema"], "additionalProperties": False,
                       "properties": {"schema": {"type": "object"}}},
        # An event stream's schema describes one event's data (OAS 3.1 has no per-item schema);
        # x-itemSchema names it explicitly, as OAS 3.2's itemSchema will.
        "event_media": {"type": "object", "required": ["schema", "x-itemSchema"], "additionalProperties": False,
                        "properties": {"schema": {"type": "object"}, "x-itemSchema": {"type": "object"}}},
        "response": {"anyOf": [_REF, {
            "type": "object", "required": ["description"], "additionalProperties": False,
            "properties": {"description": {"type": "string"}, "headers": {"type": "object"},
                           "content": {"$ref": "#/$defs/media"}}}]},
        "operation": {
            "type": "object", "required": ["operationId", "responses"], "additionalProperties": False,
            "properties": {
                "operationId": {"type": "string", "pattern": "^[a-z][A-Za-z0-9]*$"},
                "summary": {"type": "string"}, "description": {"type": "string"},
                "security": _SECURITY,
                "parameters": {"type": "array", "items": {"$ref": "#/$defs/parameter"}},
                "requestBody": {"type": "object", "required": ["required", "content"], "additionalProperties": False,
                                "properties": {"required": {"type": "boolean"}, "description": {"type": "string"},
                                               "content": {"$ref": "#/$defs/media"}}},
                "responses": {"type": "object", "additionalProperties": {"$ref": "#/$defs/response"}},
                "x-websocket": {"type": "object", "required": ["client", "server"], "additionalProperties": False,
                                "properties": {"client": _REF, "server": _REF}},
            },
        },
        "path_item": {"type": "object", "additionalProperties": False,
                      "properties": {"parameters": {"type": "array", "items": {"$ref": "#/$defs/parameter"}},
                                     "get": {"$ref": "#/$defs/operation"},
                                     "post": {"$ref": "#/$defs/operation"},
                                     "delete": {"$ref": "#/$defs/operation"},
                                     "put": {"$ref": "#/$defs/operation"}}},
    },
}


def _schema_objects(doc: dict):
    """(where, schema) for every schema object inside the OpenAPI document."""
    comps = doc.get("components", {})
    for name, schema in comps.get("schemas", {}).items():
        yield f"components/schemas/{name}", schema
    for name, param in comps.get("parameters", {}).items():
        if "schema" in param:
            yield f"components/parameters/{name}/schema", param["schema"]

    def responses(where: str, resps: dict):
        for status, resp in resps.items():
            for kind in (JSON_MEDIA, EVENT_STREAM):
                media = resp.get("content", {}).get(kind)
                if media:
                    yield f"{where}/{status}", media["schema"]
                    if "x-itemSchema" in media:
                        yield f"{where}/{status}/x-itemSchema", media["x-itemSchema"]
            for hname, header in resp.get("headers", {}).items():
                if "schema" in header:
                    yield f"{where}/{status}/headers/{hname}", header["schema"]

    yield from responses("components/responses", comps.get("responses", {}))
    for template, item in doc.get("paths", {}).items():
        for i, param in enumerate(item.get("parameters", [])):
            if "schema" in param:
                yield f"paths{template}/parameters/{i}", param["schema"]
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            where = f"paths{template}/{method}"
            media = op.get("requestBody", {}).get("content", {}).get("application/json")
            if media:
                yield f"{where}/requestBody", media["schema"]
            yield from responses(f"{where}/responses", op.get("responses", {}))
            for side, schema in op.get("x-websocket", {}).items():
                yield f"{where}/x-websocket/{side}", schema


def openapi_schema_problems(registry: Registry) -> list[str]:
    """Every schema object inside the OpenAPI document stays inside the validator subset."""
    problems = []
    try:
        objects = list(_schema_objects(registry.docs[OPENAPI]))
    except (AttributeError, TypeError, KeyError):
        return [f"{OPENAPI}: the document is malformed; its structure check names where"]
    for where, schema in objects:
        try:
            check_schema(schema, f"{OPENAPI}#/{where}")
        except SchemaError as exc:
            problems.append(f"{OPENAPI}: {exc}")
    return problems


def check_openapi(ctx: selfcheck.Context) -> None:
    reg, report = ctx.registry, ctx.report
    doc = reg.docs[OPENAPI]
    shape = Registry()
    shape.add("tsbc/openapi-shape.json", OPENAPI_SHAPE)
    shape_errors = validate(doc, OPENAPI_SHAPE, shape, "tsbc/openapi-shape.json")
    for err in shape_errors[:10]:
        report.fail(OPENAPI, err)
    if shape_errors:
        return
    version = contract_version(ctx.root)
    if doc["info"].get("version") != version:
        report.fail(OPENAPI, f"info.version {doc['info'].get('version')!r} is not CONTRACT_VERSION {version!r}")
    ops = operations(reg)
    ids = [o.op["operationId"] for o in ops]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        report.fail(OPENAPI, f"operationId {dup!r} is not unique")
    for template, item in doc["paths"].items():
        declared = {_deref(reg, p)["name"] for p in item.get("parameters", [])}
        for method in METHODS:
            if method not in item:
                continue
            names = declared | {_deref(reg, p)["name"] for p in item[method].get("parameters", [])}
            for needed in re.findall(r"\{([^}]+)\}", template):
                if needed not in names:
                    report.fail(OPENAPI, f"{method.upper()} {template}: path parameter {needed!r} is not declared")
            for status in item[method]["responses"]:
                if not re.fullmatch(r"[1-5][0-9][0-9]", status):
                    report.fail(OPENAPI, f"{method.upper()} {template}: response key {status!r} is not a status")
    ws = [o for o in ops if "x-websocket" in o.op]
    if len(ws) != 1 or ws[0].method != "GET" or "101" not in ws[0].op["responses"]:
        report.fail(OPENAPI, "exactly one GET operation answering 101 must carry x-websocket")
    for o in ops:
        if EVENT_STREAM in o.op.get("requestBody", {}).get("content", {}):
            report.fail(OPENAPI, f"{o.method} {o.template}: a request body is application/json")
        for status, resp in o.op["responses"].items():
            if EVENT_STREAM in _deref(reg, resp).get("content", {}) and (o.method, o.template, status) not in EVENT_STREAMS:
                report.fail(OPENAPI, f"{o.method} {o.template} {status}: text/event-stream is admitted only for "
                                     "GET /alerts' 200 response; every other body is application/json")
    report.lines.append(f"openapi: {len(doc['paths'])} paths, {len(ops)} operations, structure and schema "
                        "subset checked")


# ---- placeholders ----------------------------------------------------------------------------

def placeholders(node: Any) -> set[str]:
    if isinstance(node, str):
        return set(PLACEHOLDER.findall(node))
    if isinstance(node, dict):
        return set().union(*(placeholders(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(placeholders(v) for v in node)) if node else set()
    return set()


def substitute(node: Any, value) -> Any:
    """Replace ${name}. A string that is exactly one placeholder takes the value's own type."""
    if isinstance(node, str):
        whole = PLACEHOLDER.fullmatch(node)
        if whole:
            return value(whole.group(1))
        return PLACEHOLDER.sub(lambda m: str(value(m.group(1))), node)
    if isinstance(node, dict):
        return {k: substitute(v, value) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, value) for v in node]
    return node


# ---- offline check of rest / ws fixtures ------------------------------------------------------

def _match_holds(data: Any, m: dict, value) -> bool:
    try:
        actual, found = pointer_get(data, m["pointer"]), True
    except KeyError:
        actual, found = None, False
    if "absent" in m:
        return not found
    if not found:
        return False
    if "present" in m:
        return True
    if "eq" in m:
        expected = substitute(m["eq"], value)
        if m.get("ignore") and isinstance(actual, dict) and isinstance(expected, dict):
            actual = {k: v for k, v in actual.items() if k not in m["ignore"]}
            expected = {k: v for k, v in expected.items() if k not in m["ignore"]}
        return json_equal(actual, expected)
    if "not_contains" in m:
        serialized = json.dumps(actual, ensure_ascii=False)
        return all(canary not in serialized for canary in substitute(m["not_contains"], value))
    if "ne" in m:
        return not json_equal(actual, substitute(m["ne"], value))
    if "some" in m:
        want = substitute(m["some"], value)
        return isinstance(actual, list) and any(_element_matches(e, want) for e in actual)
    if "none" in m:
        want = substitute(m["none"], value)
        return isinstance(actual, list) and not any(_element_matches(e, want) for e in actual)
    return False


def _element_matches(element: Any, want: dict) -> bool:
    for pointer, expected in want.items():
        try:
            if not json_equal(pointer_get(element, pointer), expected):
                return False
        except KeyError:
            return False
    return True


def check_exchange(fx: Fixture, ctx: selfcheck.Context) -> None:
    reg, fail = ctx.registry, ctx.report.fail
    if OPENAPI not in reg.docs or WS_FRAMES not in reg.docs:
        fail(fx.rel, f"needs {OPENAPI} and {WS_FRAMES}")
        return
    case = fx.case
    inputs = set(case["inputs"])
    defined = inputs | {"nonce"}
    opened: set[str] = set()
    ws_op = websocket_operation(reg)
    codes = close_codes(reg)
    server_types = frame_types(reg, "server")

    def undefined(where: str, node: Any, extra: set[str] = frozenset()) -> None:
        for name in sorted(placeholders(node) - defined - set(extra)):
            fail(where, f"placeholder ${{{name}}} is neither an input nor saved by an earlier step")

    def check_request(where: str, req: dict) -> Operation | None:
        who = req.get("as", "main")
        needs = {"main": {"user", "password"}, "peer": {"peer_user", "peer_password"}}.get(who, set())
        for missing in sorted(needs - inputs):
            fail(where, f"requests made as {who!r} need input {missing!r} declared")
        op = find_operation(reg, req["method"], substitute(req["path"], lambda _: SAMPLE))
        if op is None:
            fail(where, f"{req['method']} {req['path']} is not an operation in {OPENAPI}")
            return None
        if op is ws_op:
            fail(where, "the WebSocket operation is opened with ws_open, not an http step")
        schema = request_schema(reg, op)
        if "body" in req:
            if schema is None:
                fail(where, f"{op.method} {op.template} takes no request body")
            else:
                body = substitute(req["body"], lambda name: "imp-" + "a" * 26 if name == "import_id" else SAMPLE)
                errs = validate(body, schema, reg, OPENAPI)
                if req.get("invalid_body") and not errs:
                    fail(where, "marked invalid_body, but the body is valid: the case would not test rejection")
                if not req.get("invalid_body") and errs:
                    fail(where, f"request body invalid: {errs[0]}")
        elif schema is not None and op.op["requestBody"].get("required") and not req.get("invalid_body"):
            fail(where, f"{op.method} {op.template} requires a body")
        return op

    for i, step in enumerate(case["steps"], 1):
        where = f"{fx.rel} step {i}"
        saves = set(step.get("save", {}))
        if "ws_expect" in step and "frame" in step:
            saves |= set(step["frame"].get("save", {}))
        undefined(where, step, saves)
        if "http" in step:
            op = check_request(where, step["http"])
            expect = step["expect"]
            if op is not None:
                resp = response(reg, op, expect["status"])
                if resp is None:
                    fail(where, f"{op.method} {op.template} declares no {expect['status']} response")
                else:
                    schema = json_schema_of(reg, resp)
                    if "example" in expect:
                        if schema is None:
                            fail(where, f"{expect['status']} has no JSON body, but the step carries an example")
                        else:
                            for err in validate(expect["example"], schema, reg, OPENAPI)[:3]:
                                fail(where, f"example: {err}")
                            message = _error_message(expect["example"])
                            if message is not None and host_detail(message):
                                fail(where, "example error message names a host path (Contract §4)")
                            for m in expect.get("match", []):
                                if not placeholders(m) and not _match_holds(expect["example"], m, lambda _: SAMPLE):
                                    fail(where, f"example does not satisfy its own match on {m['pointer']}")
                    if expect.get("cookie") and op.op.get("operationId") != "login":
                        fail(where, "cookie expectations belong to POST /auth/login")
                    content = _deref(reg, resp).get("content", {})
                    if "binary_body" in expect and "application/octet-stream" not in content:
                        fail(where, f"{op.method} {op.template} {expect['status']} has no binary body, "
                                    "but the step expects one")
                    for name in expect.get("headers", {}):
                        if name not in _deref(reg, resp).get("headers", {}):
                            fail(where, f"{op.method} {op.template} {expect['status']} declares no "
                                        f"{name!r} header")
        elif "ws_open" in step:
            if fx.kind != "ws":
                fail(where, "a rest fixture cannot open a WebSocket")
            if ws_op is None or step["path"] != ws_op.template:
                fail(where, f"ws_open path {step['path']} is not the WebSocket operation")
            opened.add(step["ws_open"])
        elif "wait" in step:
            wait = substitute(step["wait"], lambda _: 1)
            if not isinstance(wait, (int, float)) or isinstance(wait, bool):
                fail(where, "wait is a number of seconds, or a ${placeholder} saved from one")
        else:
            sid = step.get("ws_send") or step.get("ws_expect") or step.get("ws_close")
            if fx.kind != "ws":
                fail(where, "a rest fixture cannot use WebSocket steps")
            if sid not in opened:
                fail(where, f"socket {sid!r} is used before ws_open")
            if "ws_send" in step and "frame" in step:
                frame = substitute(step["frame"], lambda _: SAMPLE)
                errs = reg.validate(frame, f"{WS_FRAMES}#/$defs/client")
                if step.get("unknown"):
                    if frame.get("t") in frame_types(reg, "client") | server_types:
                        fail(where, f"marked unknown, but t={frame.get('t')!r} is a known frame type")
                elif step.get("invalid"):
                    if not errs:
                        fail(where, "marked invalid, but the frame is valid")
                elif errs:
                    fail(where, f"frame invalid: {errs[0]}")
            elif "ws_expect" in step and "frame" in step:
                example = step["frame"].get("example")
                if example is not None:
                    for err in reg.validate(example, f"{WS_FRAMES}#/$defs/server")[:3]:
                        fail(where, f"example: {err}")
            elif "ws_expect" in step and "close" in step:
                if step["close"] >= 4000 and step["close"] not in codes:
                    fail(where, f"close code {step['close']} is not in {WS_FRAMES} x-close-codes")
            elif "ws_expect" in step and "prefix" in step:
                prefix = step["prefix"]
                example = prefix.get("example")
                if example is not None:
                    errs = reg.validate(example, f"{WS_FRAMES}#/$defs/server")
                    if errs or example.get("t") != "prefix":
                        fail(where, f"prefix example is not a valid prefix frame: {errs[0] if errs else 't'}")
                if not any(k in prefix for k in ("binary_contains", "binary_lacks", "match")):
                    fail(where, "a prefix step says nothing about the frame or the dump")
        defined |= saves
    for j, req in enumerate(case.get("cleanup", []), 1):
        where = f"{fx.rel} cleanup {j}"
        undefined(where, req)
        check_request(where, req)


def coverage(ctx: selfcheck.Context) -> None:
    """Every operation, and every frame type in both directions, is exercised by some fixture."""
    reg = ctx.registry
    if OPENAPI not in reg.docs or WS_FRAMES not in reg.docs:
        return
    used_ops, sent, received = set(), set(), set()
    for fx in ctx.fixtures:
        if fx.kind == "instance" and fx.grade == "reference" and fx.case.get("schema", "").startswith(WS_FRAMES):
            shown = {i["doc"].get("t") for i in fx.case.get("instances", []) if i["valid"] and isinstance(i["doc"], dict)}
            sent |= shown & REFERENCE_FRAMES
            received |= shown & REFERENCE_FRAMES
            continue
        if fx.kind not in ("rest", "ws"):
            continue
        for step in fx.case.get("steps", []):
            if "http" in step:
                op = find_operation(reg, step["http"]["method"], substitute(step["http"]["path"], lambda _: SAMPLE))
                if op:
                    used_ops.add((op.method, op.template))
            elif "ws_open" in step:
                ws = websocket_operation(reg)
                if ws:
                    used_ops.add((ws.method, ws.template))
            elif "ws_send" in step and "frame" in step and not step.get("unknown"):
                sent.add(step["frame"].get("t"))
            elif "ws_expect" in step and "frame" in step:
                for m in step["frame"].get("match", []):
                    if m["pointer"] == "/t" and "eq" in m:
                        received.add(m["eq"])
    for op in operations(reg):
        if (op.method, op.template) not in used_ops:
            ctx.report.fail("coverage", f"{op.method} {op.template} has no fixture")
    for t in sorted(frame_types(reg, "client") - sent):
        ctx.report.fail("coverage", f"client frame {t!r} is sent by no fixture")
    for t in sorted(frame_types(reg, "server") - received):
        ctx.report.fail("coverage", f"server frame {t!r} is expected by no fixture")


selfcheck.KIND_CHECKS["rest"] = check_exchange
selfcheck.KIND_CHECKS["ws"] = check_exchange
selfcheck.OPENAPI_CHECKS.append(check_openapi)
selfcheck.COVERAGE_CHECKS.append(coverage)


# ---- live execution --------------------------------------------------------------------------

def _safe(pointer: str, value: Any) -> str:
    if pointer.rsplit("/", 1)[-1] in SECRET_FIELDS:
        return "<redacted>"
    text = json.dumps(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _error_code(resp: _http.Response) -> str:
    try:
        err = resp.json()["error"]
        return f" ({err['code']}: {str(err['message'])[:80]})"
    except (ValueError, KeyError, TypeError):
        return ""


class Live:
    """One live execution of one rest/ws fixture."""

    def __init__(self, fx: Fixture, run: Run) -> None:
        self.fx, self.run, self.reg = fx, run, run.registry
        self.vars: dict[str, Any] = {"nonce": secrets.token_hex(4)}
        self.fresh: _http.HttpClient | None = None
        self.fresh_created: set[str] = set()  # sessions this fixture's own fresh login created
        self.sockets: dict[str, tuple[_ws.WebSocket, bytearray]] = {}

    def value(self, name: str) -> Any:
        if name in self.vars:
            return self.vars[name]
        if name in INPUTS:
            return self.run.input(name)  # raises InputMissing
        raise Failed(f"${{{name}}} has no value (a step that should have saved it did not run)")

    def sub(self, node: Any) -> Any:
        return substitute(node, self.value)

    def _new_client(self) -> _http.HttpClient:
        return _http.HttpClient(self.run.url, ca_file=self.run.ca_file, timeout=self.run.timeout)

    def client(self, who: str) -> _http.HttpClient:
        if who == "fresh":
            self.fresh = self.fresh or self._new_client()
            return self.fresh
        if who == "none":
            return self._new_client()
        clients = self.run.state.setdefault("clients", {})
        cached = clients.get(who)
        if isinstance(cached, Failed):
            raise cached
        if cached is not None:
            return cached
        user, password = (("user", "password") if who == "main" else ("peer_user", "peer_password"))
        body = {"username": self.run.input(user), "password": self.run.input(password)}
        client = self._new_client()
        try:
            resp = client.request("POST", "/auth/login", body=body)
        except _http.HttpError as exc:
            raise Failed(f"cannot reach the core to log in as {who}: {exc}") from None
        if resp.status != 200:
            clients[who] = Failed(f"login as {who} ({self.run.input(user)}) failed: HTTP {resp.status}"
                                  f"{_error_code(resp)}")
            raise clients[who]
        clients[who] = client
        return client

    def check_matches(self, data: Any, matches: list[dict], where: str) -> None:
        for m in matches:
            if _match_holds(data, m, self.value):
                continue
            pointer = m["pointer"]
            if "not_contains" in m:
                raise Failed(f"{where}: response contains a forbidden value")
            try:
                actual = _safe(pointer, pointer_get(data, pointer))
            except KeyError:
                actual = "missing"
            if "absent" in m:
                raise Failed(f"{where}: {pointer} is present, expected absent")
            if "present" in m or actual == "missing":
                raise Failed(f"{where}: {pointer} is missing")
            if "eq" in m:
                raise Failed(f"{where}: {pointer} is {actual}, expected {_safe(pointer, self.sub(m['eq']))}")
            if "ne" in m:
                raise Failed(f"{where}: {pointer} is {actual}, which it must not be")
            if "none" in m:
                raise Failed(f"{where}: an element of {pointer} matches {json.dumps(self.sub(m['none']))}, "
                             "which none may")
            raise Failed(f"{where}: no element of {pointer} matches {json.dumps(m['some'])}")

    def save(self, data: Any, saves: dict[str, str], where: str) -> None:
        for name, pointer in saves.items():
            try:
                self.vars[name] = pointer_get(data, pointer)
            except KeyError:
                raise Failed(f"{where}: nothing at {pointer} to save as {name}") from None

    # -- http --

    def http(self, step: dict, where: str) -> None:
        deadline = time.monotonic() + self.run.timeout
        while True:
            try:
                self._http_once(step, where)
                return
            except Failed:
                if not step.get("poll") or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    def request(self, req: dict, where: str) -> tuple[Operation | None, _http.Response]:
        method, path = req["method"], self.sub(req["path"])
        who = req.get("as", "main")
        client = self.client(who)
        body = self.sub(req["body"]) if "body" in req else _NO_BODY
        kept = dict(client.jar.cookies) if req.get("keep_cookies") else None
        op = find_operation(self.reg, method, path)
        if op is not None and (op.method, op.template, "200") in EVENT_STREAMS and body is _NO_BODY:
            return op, self._stream(client, path, where)
        accept = accept_of(self.reg, op)
        try:
            if body is _NO_BODY:
                resp = client.request(method, path, use_cookies=who != "none", accept=accept)
            else:
                resp = client.request(method, path, body=body, use_cookies=who != "none", accept=accept)
        except _http.HttpError as exc:
            raise Failed(f"{where}: {exc}") from None
        if kept is not None:
            client.jar.cookies = kept
        self._track(who, method, path, resp)
        return op, resp

    def _stream(self, client: _http.HttpClient, path: str, where: str) -> _http.Response:
        """GET an event stream. A refusal is read whole, like any JSON response; a 200 is read as it
        arrives, event by event, until the stream ends, MAX_STREAM_EVENTS events have come, or the
        run's timeout passes — reading an alert stream to its end would never return."""
        deadline = time.monotonic() + self.run.timeout
        try:
            stream = client.stream(path, accept=EVENT_STREAM, timeout=self.run.timeout)
        except _http.HttpError as exc:
            raise Failed(f"{where}: {exc}") from None
        try:
            if stream.status != 200:
                return _http.Response(stream.status, stream.headers, stream.read())
            resp = StreamedResponse(stream.status, stream.headers, b"")
            while len(resp.events) < MAX_STREAM_EVENTS:
                try:
                    event = _http.next_event(stream, deadline)
                except _http.StreamTimeout:
                    break
                except _http.HttpError as exc:
                    raise Failed(f"{where}: {exc}") from None
                if event is None:
                    break
                resp.events.append(event)
            return resp
        except _http.HttpError as exc:
            raise Failed(f"{where}: {exc}") from None
        finally:
            stream.close()

    def _track(self, who: str, method: str, path: str, resp: _http.Response) -> None:
        """Remember every session this run creates, read from the raw response so a create that
        fails its fixture is remembered too, and forget one once it is deleted (see sweep)."""
        created = self.run.state.setdefault("created_sessions", {})
        if method == "POST" and path == "/sessions" and resp.status == 201 and who != "none":
            try:
                sid = resp.json()["session"]["id"]
            except (ValueError, KeyError, TypeError):
                return
            if not (isinstance(sid, str) and _SESSION_PATH.match(f"/sessions/{sid}")):
                return
            if who == "fresh":  # that login lives only as long as this fixture: sweep it here
                self.fresh_created.add(sid)
            else:
                created[sid] = who
        elif method == "DELETE" and resp.status in (204, 404):
            match = _SESSION_PATH.match(path)
            if match:
                created.pop(match.group(1), None)
                self.fresh_created.discard(match.group(1))

    def _sweep_fresh(self) -> None:
        """Delete what this fixture's fresh login created and left open, before the fixture's own
        cleanup can log that login out — the run's sweep holds no client that could."""
        for sid in sorted(self.fresh_created):
            try:
                resp = self.fresh.request("DELETE", f"/sessions/{sid}")
            except _http.HttpError as exc:
                self.run.warnings.append(f"{self.fx.label}: cleanup DELETE /sessions/{sid} failed: {exc}")
                continue
            if resp.status not in (204, 404):
                self.run.warnings.append(f"{self.fx.label}: cleanup DELETE /sessions/{sid} returned HTTP {resp.status}")
        self.fresh_created.clear()

    def _http_once(self, step: dict, where: str) -> None:
        req, expect = step["http"], step["expect"]
        op, resp = self.request(req, where)
        label = f"{where} ({req['method']} {op.template if op else req['path']})"
        if resp.status != expect["status"]:
            raise Failed(f"{label}: HTTP {resp.status}{_error_code(resp)}, expected {expect['status']}")
        if isinstance(resp, StreamedResponse):
            data = self._events(resp, event_schema_of(self.reg, response(self.reg, op, resp.status)), label)
            self.save(data, step.get("save", {}), label)
            self.check_matches(data, expect.get("match", []), label)
            return
        schema = json_schema_of(self.reg, response(self.reg, op, resp.status)) if op else None
        if "binary_body" in expect:
            self._check_binary_body(resp, expect["binary_body"], label)
            self._check_headers(resp, expect.get("headers", {}), label)
            return
        data = None
        if resp.body.strip():
            if schema is None:
                raise Failed(f"{label}: HTTP {resp.status} must have no body, got {len(resp.body)} bytes")
            try:
                data = resp.json()
            except (ValueError, UnicodeDecodeError):
                raise Failed(f"{label}: body is not JSON") from None
        elif schema is not None:
            raise Failed(f"{label}: HTTP {resp.status} has an empty body, expected JSON")
        if schema is not None:
            errs = validate(data, schema, self.reg, OPENAPI)
            if errs:
                raise Failed(f"{label}: response breaks the schema: {'; '.join(errs[:3])}")
        message = _error_message(data)
        if message is not None and host_detail(message):
            raise Failed(f"{label}: the error message names a path on the broker host (Contract §4)")
        if "cookie" in expect:
            self.check_cookie(resp, expect["cookie"], label)
        self._check_headers(resp, expect.get("headers", {}), label)
        self.save(data, step.get("save", {}), label)
        self.check_matches(data, expect.get("match", []), label)

    def _check_binary_body(self, resp: _http.Response, want: dict, label: str) -> None:
        """A response whose body is application/octet-stream: length and content, never JSON.
        `contains`/`lacks` are the UTF-8 encodings of their strings, as `bytes` steps send them."""
        body = resp.body
        if want.get("empty") and body:
            raise Failed(f"{label}: body must be empty, got {len(body)} bytes")
        low = want.get("min_bytes")
        if low is not None and len(body) < low:
            raise Failed(f"{label}: body is {len(body)} bytes, expected at least {low}")
        for needle in want.get("contains", []):
            if str(self.sub(needle)).encode("utf-8") not in body:
                raise Failed(f"{label}: body does not contain the expected bytes")
        for needle in want.get("lacks", []):
            if str(self.sub(needle)).encode("utf-8") in body:
                raise Failed(f"{label}: body contains bytes it must not")

    def _check_headers(self, resp: _http.Response, want: dict, label: str) -> None:
        for name, value in want.items():
            actual = resp.header(name)
            if value is None:
                # null expectation: the response MUST NOT carry this header
                if actual is not None:
                    raise Failed(f"{label}: response carries {name}, expected none")
            else:
                if actual is None:
                    raise Failed(f"{label}: response has no {name} header")
                if actual != str(self.sub(value)):
                    raise Failed(f"{label}: {name} header is {actual!r}, expected {value!r}")

    def _events(self, resp: StreamedResponse, schema: Any, label: str) -> list:
        """The data of each alert event read, each held to the per-event schema; an event of
        another type is ignored, as a receiver must. Pointers in match and save index this list."""
        data = []
        for i, (kind, text) in enumerate(resp.events):
            if kind != ALERT_EVENT:
                continue
            try:
                doc = loads(text)
            except ValueError:
                raise Failed(f"{label}: event {i} ({kind}) carries data that is not JSON") from None
            errs = validate(doc, schema, self.reg, OPENAPI) if schema is not None else []
            if errs:
                raise Failed(f"{label}: event {i} ({kind}) breaks its schema: {'; '.join(errs[:3])}")
            data.append(doc)
        return data

    def check_cookie(self, resp: _http.Response, want: dict, label: str) -> None:
        name = cookie_name(self.reg)
        live = [c for c in resp.set_cookies if c.name == name and c.value and not _http._expired(c)]
        if not want["set"]:
            if live:
                raise Failed(f"{label}: set the {name} cookie, expected none")
            return
        if not live:
            raise Failed(f"{label}: no {name} cookie set")
        cookie = live[-1]
        checks = {"HttpOnly": cookie.flag("httponly"), "Secure": cookie.flag("secure"),
                  "SameSite=Strict": (cookie.attr("samesite") or "").lower() == "strict"}
        missing = [a for a in want.get("attributes", []) if not checks[a]]
        if missing:
            raise Failed(f"{label}: {name} cookie lacks {', '.join(missing)}")

    # -- websocket --

    def ws_open(self, step: dict, where: str) -> None:
        parts = urllib.parse.urlsplit(self.run.url)
        tls = parts.scheme == "https"
        url = urllib.parse.urlunsplit(("wss" if tls else "ws", parts.netloc,
                                       parts.path.rstrip("/") + step["path"], "", ""))
        try:
            sock = _ws.WebSocket.connect(url, ssl_context=_http.ssl_context(self.run.ca_file) if tls else None,
                                         timeout=self.run.timeout)
        except (wf.HandshakeError, OSError) as exc:
            raise Failed(f"{where}: WebSocket upgrade failed: {exc}") from None
        self.sockets[step["ws_open"]] = (sock, bytearray())

    def _socket(self, sid: str) -> tuple[_ws.WebSocket, bytearray]:
        return self.sockets[sid]

    def _recv(self, sock: _ws.WebSocket, deadline: float, where: str, waiting_for: str) -> wf.Message:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failed(f"{where}: {waiting_for} did not arrive within {self.run.timeout:g}s")
        try:
            return sock.recv(remaining)
        except TimeoutError:
            raise Failed(f"{where}: {waiting_for} did not arrive within {self.run.timeout:g}s") from None
        except (_ws.ConnectionClosed, wf.ProtocolError) as exc:
            raise Failed(f"{where}: WebSocket failed while waiting for {waiting_for}: {exc}") from None

    def ws_step(self, step: dict, where: str) -> None:
        if "ws_close" in step:
            self._socket(step["ws_close"])[0].close()
            return
        if "ws_send" in step:
            sock, _ = self._socket(step["ws_send"])
            try:
                if "frame" in step:
                    sock.send_text(json.dumps(self.sub(step["frame"])))
                else:
                    sock.send_binary(str(self.sub(step["bytes"])).encode("utf-8"))
            except _ws.ConnectionClosed:
                raise Failed(f"{where}: the socket was closed (code {sock.close_code}) before the send") from None
            return
        sock, output = self._socket(step["ws_expect"])
        deadline = time.monotonic() + self.run.timeout
        # The era rule (F5): read server frames as this fixture's own mark epoch knew them, so a
        # frame type introduced at a later tag is skipped like an unknown `t`.
        server_types = era_server_frames(self.reg, self.fx.mark)
        if "close" in step:
            while True:
                message = self._recv(sock, deadline, where, f"close {step['close']}")
                if message.kind == "binary":
                    _keep(output, message.data)
                elif message.kind == "close":
                    if message.close_code != step["close"]:
                        raise Failed(f"{where}: closed with {message.close_code} "
                                     f"({message.close_reason!r}), expected {step['close']}")
                    return
        if "output_contains" in step:
            needle = str(self.sub(step["output_contains"])).encode("utf-8")
            while needle not in output:
                message = self._recv(sock, deadline, where, f"the expected output ({len(output)} bytes so far)")
                if message.kind == "binary":
                    _keep(output, message.data)
                elif message.kind == "close":
                    raise Failed(f"{where}: closed with {message.close_code} before the expected output "
                                 f"({len(output)} bytes received)")
            return
        if "prefix" in step:
            self._prefix_step(sock, output, step, where, deadline, server_types)
            return
        spec = step["frame"]
        while True:
            message = self._recv(sock, deadline, where, "a server frame")
            if message.kind == "binary":
                _keep(output, message.data)
                continue
            if message.kind == "close":
                raise Failed(f"{where}: closed with {message.close_code} ({message.close_reason!r}) "
                             "while waiting for a server frame")
            try:
                frame = loads(message.data)
            except ValueError:
                raise Failed(f"{where}: a text frame is not JSON") from None
            if not isinstance(frame, dict) or frame.get("t") not in server_types:
                continue  # receivers ignore frame types they do not know
            errs = self.reg.validate(frame, f"{WS_FRAMES}#/$defs/server")
            if errs:
                raise Failed(f"{where}: server frame breaks the schema: {'; '.join(errs[:3])}")
            self.save(frame, spec.get("save", {}), where)
            self.check_matches(frame, spec.get("match", []), where)
            return

    def _prefix_step(self, sock: _ws.WebSocket, output: bytearray, step: dict, where: str,
                     deadline: float, server_types: set[str]) -> None:
        """The attach prefix: the text `prefix` frame (validated and matched), then exactly one
        binary message — the dump — checked for the strings it must contain or lack."""
        prefix = step["prefix"]
        while True:
            message = self._recv(sock, deadline, where, "the prefix frame")
            if message.kind == "binary":
                _keep(output, message.data)
                continue
            if message.kind == "close":
                raise Failed(f"{where}: closed with {message.close_code} ({message.close_reason!r}) "
                             "while waiting for the prefix frame")
            try:
                frame = loads(message.data)
            except ValueError:
                raise Failed(f"{where}: the prefix frame is not JSON") from None
            if not isinstance(frame, dict) or frame.get("t") not in server_types:
                continue
            errs = self.reg.validate(frame, f"{WS_FRAMES}#/$defs/server")
            if errs:
                raise Failed(f"{where}: the prefix frame breaks the schema: {'; '.join(errs[:3])}")
            if frame.get("t") != "prefix":
                continue  # a frame between attached and the prefix is read past, like any other
            self.save(frame, prefix.get("save", {}), where)
            self.check_matches(frame, prefix.get("match", []), where)
            break
        # Exactly one binary message follows the prefix frame: the dump. The next binary message is
        # it, whatever text frames (none, by the order) come first.
        while True:
            message = self._recv(sock, deadline, where, "the prefix dump")
            if message.kind == "binary":
                break
            if message.kind == "close":
                raise Failed(f"{where}: closed with {message.close_code} before the prefix dump")
        dump = message.data
        if not dump:
            raise Failed(f"{where}: the prefix dump is empty; it is at least one byte")
        for needle in prefix.get("binary_contains", []):
            if str(self.sub(needle)).encode("utf-8") not in dump:
                raise Failed(f"{where}: the prefix dump does not contain the expected bytes")
        for needle in prefix.get("binary_lacks", []):
            if str(self.sub(needle)).encode("utf-8") in dump:
                raise Failed(f"{where}: the prefix dump contains bytes it must not")

    def wait(self, step: dict, where: str) -> None:
        seconds = self.sub(step["wait"])
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= MAX_WAIT:
            raise Failed(f"{where}: cannot wait {seconds!r} seconds (a number from 0 to {MAX_WAIT})")
        time.sleep(seconds + step.get("extra", 0))

    # -- the fixture --

    def execute(self) -> None:
        case = self.fx.case
        try:
            for i, step in enumerate(case["steps"], 1):
                where = f"step {i}"
                if "http" in step:
                    self.http(step, where)
                elif "ws_open" in step:
                    self.ws_open(step, where)
                elif "wait" in step:
                    self.wait(step, where)
                else:
                    self.ws_step(step, where)
        finally:
            for sock, _ in self.sockets.values():
                sock.close(timeout=0.5)
            self._sweep_fresh()
            self.cleanup(case.get("cleanup", []))

    def cleanup(self, requests: list[dict]) -> None:
        for req in requests:
            if placeholders(req) - set(self.vars) - set(INPUTS):
                continue  # the step that would have created the object never ran
            if req.get("as") == "fresh" and self.fresh is None:
                continue  # this fixture's own login never happened
            try:
                _op, resp = self.request(req, "cleanup")
            except (Failed, InputMissing, ValueError) as exc:  # cleanup never masks the fixture's own result
                self.run.warnings.append(f"{self.fx.label}: cleanup {req['method']} {req['path']} failed: {exc}")
                continue
            if resp.status >= 400 and resp.status != 404:
                self.run.warnings.append(f"{self.fx.label}: cleanup {req['method']} {req['path']} "
                                         f"returned HTTP {resp.status}")


def execute(fx: Fixture, run: Run) -> None:
    Live(fx, run).execute()


def sweep(run: Run) -> int:
    """After a live run: DELETE every session it created that is still there, best effort, as
    the principal that created it — so repeated runs against a standing core do not pile up
    connections until the supervisors refuse capacity. A failure is a warning, never a FAIL: the
    grades are already decided. Returns how many sessions were deleted."""
    created = run.state.get("created_sessions", {})
    clients = run.state.get("clients", {})
    deleted = 0
    for sid, who in sorted(created.items()):
        client = clients.get(who)
        if not isinstance(client, _http.HttpClient):
            run.warnings.append(f"cleanup: session {sid} was left open (no {who} login to delete it with)")
            continue
        try:
            resp = client.request("DELETE", f"/sessions/{sid}")
        except _http.HttpError as exc:
            run.warnings.append(f"cleanup: DELETE /sessions/{sid} failed: {exc}")
            continue
        if resp.status == 204:
            deleted += 1
        elif resp.status != 404:
            run.warnings.append(f"cleanup: DELETE /sessions/{sid} returned HTTP {resp.status}")
    created.clear()
    return deleted
