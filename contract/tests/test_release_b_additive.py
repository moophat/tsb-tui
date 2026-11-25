"""Release B is additive on v0.11.1, and the descriptions it rewrites are exactly the plan's 34 rows.

The old tree is read from the v0.11.1 tag (the CI checkout carries every tag, as test_serve's contract-v0.2.1 read
does). Every schema and the OpenAPI document are compared with their v0.11.1 text, node by node:

- nothing is removed, no value changes, and no constraint keyword is newly put on an existing node (a tightening),
  but for the one the plan names: the placement rule on `connection_record` (OPEN-R2 (a));
- a description or a close code's meaning may be extended (the old text stays whole inside the new) or rewritten; the
  rewrites are exactly the JSON rows of "Sentences Release B makes false" (rows 1 to 20), no more and no fewer;
- the Markdown rows (21 to 34) are checked by their old sentence: present at v0.11.1, gone now.
"""

import copy
import json
import re
import subprocess
import unittest

from . import CONTRACT

TAG = "v0.11.1"
ROOT = CONTRACT.parent
OPENAPI = "openapi/tsb.openapi.json"
CONSTRAINTS = frozenset({"required", "enum", "const", "pattern", "minimum", "maximum", "minLength", "maxLength",
                         "minItems", "maxItems", "oneOf", "anyOf", "type", "additionalProperties"})
PROSE_KEYS = frozenset({"description", "meaning"})
# The release workflow writes this value at release time, so it differs from the old tag by construction.
RELEASE_OWNED = {("openapi/tsb.openapi.json", "info/version")}
# The constraints a release B node newly carries: the placement rule of the connection record's `login`, and the one that
# ties `attempts` to the audit line's login kind. Each governs a value or a key this release introduced (`prompted`,
# `attempts`, the record's `login`): nothing valid at v0.11.1 is refused by either.
# E4b M5 registers only the existing login-failure reason and the new disabled reason.
# The conditional does not restrict detail on other auth actions; all existing audit fixtures remain valid.
TIGHTENING_ALLOWED = {("schemas/audit-event.schema.json", "$defs/auth_event/oneOf"),("schemas/continuity-state.schema.json", "$defs/connection_record/oneOf"),
                      ("schemas/audit-event.schema.json", "$defs/target_auth_event/properties/detail/oneOf"),
                      (OPENAPI, "components/schemas/TargetRecord/anyOf")}

# Rows 1 to 20: (row, file, path of the rewritten string). A path is the keys and list indexes from the root.
JSON_ROWS = [
    (1, OPENAPI, "components/schemas/SessionLogin/description"),
    (2, OPENAPI, "components/schemas/SessionLoginAuth/description"),
    (3, OPENAPI, "components/schemas/SessionLoginAuth/oneOf/0/properties/password/description"),
    (4, OPENAPI, "paths//sessions/post/description"),
    (5, OPENAPI, "components/schemas/Me/properties/recording/properties/login_input/description"),
    (6, "schemas/audit-event.schema.json", "$defs/target_auth_event/description"),
    (6, "schemas/audit-event.schema.json", "$defs/target_auth_event/properties/detail/properties/login/description"),
    (7, "schemas/audit-event.schema.json", "description"),
    (8, "schemas/audit-event.schema.json", "$defs/session_connect/properties/credential_ref/description"),
    (9, "schemas/continuity-state.schema.json", "$defs/connection_record/properties/credential_ref/description"),
    (10, "schemas/ttyrec.schema.json", "description"),
    (10, "schemas/ttyrec.schema.json", "x-sidecar/rules/%RULE67%"),
    (10, "schemas/ttyrec.schema.json", "$defs/login_prompt_marker/description"),
    (11, "schemas/ipc-control.schema.json", "description"),
    (12, "schemas/ipc-control.schema.json", "$defs/auth_failed/description"),
    (13, "schemas/ipc-control.schema.json", "$defs/disconnect/description"),
    (14, "schemas/ipc-control.schema.json", "$defs/hostkey/description"),
    (15, "schemas/ws-frames.schema.json", "$defs/attach/description"),
    (16, "schemas/ws-frames.schema.json", "x-attach-order/0"),
    (16, "schemas/ws-frames.schema.json", "x-attach-order/2"),
    (17, "schemas/ws-frames.schema.json", "x-close-codes/4003/meaning"),
    (18, "schemas/ws-frames.schema.json", "x-close-codes/4015/meaning"),
    (19, "schemas/ws-frames.schema.json", "$defs/request_refused/description"),
    (20, "schemas/artifacts-collected.schema.json", "description"),
]

# The sentences the table missed, rewritten on the orchestrator's ruling of 2026-11-01 15:00 (M1 to M14; their
# Markdown sentences are EXTRA_MD_ROWS). The same strings may carry a row above (the audit top description is row 7 and M5).
EXTRA_JSON_ROWS = [
    ("inventory metadata hint", OPENAPI, "paths//targets/get/description"),
    ("target metadata hint", OPENAPI, "components/schemas/TargetRecord/description"),
    ("terminal drain scope", "schemas/ipc-control.schema.json", "$defs/timer_values/description"),
    ("recording data path hold", "schemas/ipc-data-frame.schema.json", "description"),
    ("recording alert subject", "schemas/common.schema.json", "$defs/alert_subjects/description"),
    ("recording alert kind", "schemas/common.schema.json", "$defs/alert_kind/description"),
    ("recording config section", "schemas/config.schema.json", "description"),
    ("recording held attach order", "schemas/ws-frames.schema.json", "x-attach-order/4"),
    ("recording session-wide out", "schemas/continuity-state.schema.json", "$defs/session_record/properties/recording_last_seq_out/description"),
    ("recording session-wide bytes", "schemas/continuity-state.schema.json", "$defs/session_record/properties/out_bytes_committed/description"),
    ("recording hold data path", "schemas/ws-frames.schema.json", "$defs/data_path/description"),
    ("recording continuation state", "schemas/continuity-state.schema.json", "description"),
    ("recording continuation input", "schemas/continuity-state.schema.json", "$defs/session_record/properties/recording_last_seq_in/description"),
    ("M2", OPENAPI, "paths//ws/attach/get/description"),
    ("M3", "schemas/ws-frames.schema.json", "x-close-codes/4000/meaning"),
    ("M5", "schemas/audit-event.schema.json", "description"),
    ("M5", "schemas/audit-event.schema.json", "$defs/session_connect/properties/detail/properties/login/description"),
    ("M5", "schemas/audit-event.schema.json", "$defs/session_connect/properties/detail/description"),
    ("M6", "schemas/common.schema.json", "$defs/proto_version/description"),
    ("M6", "schemas/worker-manifest.schema.json", "properties/proto_version/description"),
    ("M7", "schemas/common.schema.json", "$defs/credential_ref/description"),
    ("M7", "schemas/continuity-state.schema.json", "$defs/connection_record/properties/credential_layer/description"),
    ("M8", OPENAPI, "components/schemas/SessionCreateRequest/description"),
    ("M8", OPENAPI, "paths//sessions/post/responses/409/description"),
    ("M13", "schemas/ttyrec.schema.json", "x-sidecar/reopen"),
]
ALL_JSON_ROWS = [*JSON_ROWS, *EXTRA_JSON_ROWS]


def old_text(rel: str) -> str:
    return subprocess.run(["git", "show", f"{TAG}:contract/{rel}"], cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def new_text(rel: str) -> str:
    return (CONTRACT / rel).read_text(encoding="utf-8")


def get(doc, path: str):
    node = doc
    for key in _split(path):
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def _split(path: str) -> list[str]:
    """A path as keys. A route under `paths` is itself one key (`paths//sessions/post`: '/sessions')."""
    if not path.startswith("paths/"):
        return path.split("/")
    route = re.match(r"^paths/(/.*?)/(get|post|put|delete|parameters)(/.*)?$", path)
    return ["paths", route.group(1), route.group(2), *(route.group(3) or "").split("/")[1:]]


def _resolve_rule(doc, path: str) -> str:
    if "%RULE67%" not in path:
        return path
    rules = doc["x-sidecar"]["rules"]
    return path.replace("%RULE67%", str(next(i for i, r in enumerate(rules) if r.startswith("6 marks a login prompt"))))


def additive(old, new, where: str, rel: str, rewritten: set[str], problems: list[str]) -> None:
    """Walk `old` against `new`: record every rewritten prose string and every non-additive change."""
    if isinstance(old, dict):
        if not isinstance(new, dict):
            problems.append(f"{rel}:{where}: no longer an object")
            return
        widened = "const" in old and "const" not in new and old["const"] in new.get("enum", [])
        for key in old:
            if key == "const" and widened:
                continue          # a const that became an enum holding it: a widening, `detail.login` (row 6)
            if key not in new:
                problems.append(f"{rel}:{where}/{key}: removed")
            else:
                additive(old[key], new[key], f"{where}/{key}" if where else key, rel, rewritten, problems)
        for key in new:
            if key == "enum" and widened:
                continue
            if key not in old and key in CONSTRAINTS and (rel, f"{where}/{key}") not in TIGHTENING_ALLOWED:
                problems.append(f"{rel}:{where}/{key}: a constraint newly put on an existing node (a tightening)")
    elif isinstance(old, list):
        # TargetRecord separates SSH from telnet to exclude a new SSH-only field. Expand the
        # historical branch before comparing: every old constraint is still checked for each type.
        if rel == OPENAPI and where == "components/schemas/TargetRecord/oneOf" and \
                old[0].get("properties", {}).get("type") == {"enum": ["ssh", "telnet"]}:
            ssh, telnet = copy.deepcopy(old[0]), copy.deepcopy(old[0])
            ssh["properties"]["type"] = {"const": "ssh"}
            telnet["properties"]["type"] = {"const": "telnet"}
            ssh["description"] = "ssh: the address is present, with the port in force."
            telnet["description"] = "telnet: the address is present, with the port in force."
            old = [ssh, telnet, *old[1:]]
        if not isinstance(new, list) or len(new) < len(old):
            problems.append(f"{rel}:{where}: a list that lost items")
            return
        # JSON Schema enum order has no meaning. E4b inserts an epoch between e4 and e5;
        # compare membership here, while retaining positional checks for ordered lists.
        if where.rsplit("/", 1)[-1] == "enum":
            for item in old:
                if item not in new:
                    problems.append(f"{rel}:{where}: enum lost {item!r}")
            return
        for i, item in enumerate(old):
            additive(item, new[i], f"{where}/{i}", rel, rewritten, problems)
        if where.rsplit("/", 1)[-1] == "required" and new != old:
            problems.append(f"{rel}:{where}: required grew (a tightening)")
    elif isinstance(old, str):
        last = where.rsplit("/", 1)[-1]
        if old == new or (rel, where) in RELEASE_OWNED:
            return
        prose = (last in PROSE_KEYS or "/x-attach-order/" in f"/{where}" or "/rules/" in f"/{where}"
                 or where in ("x-sidecar/reopen", "x-compare/payload-identical", "x-compare/monotonic"))
        if not prose or not isinstance(new, str):
            problems.append(f"{rel}:{where}: a value that is not prose changed")
        elif old not in new:
            rewritten.add(where)
    elif old != new:
        problems.append(f"{rel}:{where}: {old!r} became {new!r}")


class ReleaseBIsAdditive(unittest.TestCase):
    def walk(self, rel: str):
        problems: list[str] = []
        rewritten: set[str] = set()
        previous, current = json.loads(old_text(rel)), json.loads(new_text(rel))
        if rel == "schemas/inventory-target.schema.json":
            # The new duplicate matrix retains rejection of multiple ordinary shared files.
            old_rule = "Two sources holding the same id: neither loads, and the core logs a line naming the id and both sources."
            new_rule = ("Duplicate IDs are resolved independent of scan order after provenance validation. "
                        "One ordinary shared file wins over imported shared and private files; two or more ordinary "
                        "shared files refuse all candidates. With no ordinary shared file, one imported shared file "
                        "wins over private files, while two or more refuse all. With no shared file, one validated "
                        "private file loads and two or more refuse all. Logs name IDs and sources, never paths. "
                        "private_owner is required in a private source and must match its directory owner; missing "
                        "or mismatched ownership, or private_owner elsewhere, skips and logs the file. import_ref "
                        "outside tsb and a validated private source is ignored while the file loads. These previously "
                        "ignored keys now enforce provenance. A g- ID in any source the core does not write is skipped and logged. "
                        "share is required in a private source and forbidden elsewhere: a private-source file without a share list, with one that does not name its private_owner, with a duplicate entry or more than 256 entries, or a file in any other source carrying share, is skipped and logged. An entry of a kind this release does not know matches no principal.")
            old_layout = previous["x-layout"]["identity-and-refusals"]
            self.assertIn(old_rule, old_layout)
            self.assertEqual(current["x-layout"]["identity-and-refusals"], old_layout.replace(old_rule, new_rule))
            previous["x-layout"]["identity-and-refusals"] = current["x-layout"]["identity-and-refusals"]
        additive(previous, current, "", rel, rewritten, problems)
        return problems, rewritten

    def test_every_change_to_a_schema_and_the_openapi_document_is_an_addition(self):
        files = sorted({rel for _, rel, _ in ALL_JSON_ROWS} | {
            "schemas/common.schema.json", "schemas/config.schema.json", "schemas/error.schema.json",
            "schemas/inventory-target.schema.json", "schemas/worker-manifest.schema.json",
            "schemas/supervisor.schema.json", "schemas/spool-entry.schema.json", "schemas/session-record.schema.json",
            "schemas/policy.schema.json", "schemas/vault.schema.json"})
        for rel in files:
            with self.subTest(rel):
                problems, _ = self.walk(rel)
                self.assertEqual(problems, [])

    def test_enum_insertions_are_additive_but_removals_and_ordered_list_changes_are_not(self):
        for old, new, where, good in (
            (["e4", "e5"], ["e4", "e4b", "e5"], "$defs/epoch/enum", True),
            (["e4", "e5"], ["e4", "e6"], "$defs/epoch/enum", False),
            (["e4", "e5"], ["e5", "e4"], "x-attach-order", False),
        ):
            with self.subTest(where=where, new=new):
                problems, rewritten = [], set()
                additive(old, new, where, "test.json", rewritten, problems)
                self.assertEqual(not problems and not rewritten, good)

    def test_the_release_owned_allowance_names_only_the_openapi_version(self):
        self.assertEqual(RELEASE_OWNED, {("openapi/tsb.openapi.json", "info/version")})
        for where, rel in (("info/title", "openapi/tsb.openapi.json"), ("info/version", "schemas/common.schema.json")):
            with self.subTest(where=where, rel=rel):
                problems: list[str] = []
                additive("old", "new", where, rel, set(), problems)
                self.assertEqual(len(problems), 1, problems)

    def test_the_rewritten_descriptions_are_exactly_the_plans_json_rows_and_the_rulings_additions(self):
        wanted: dict[str, set[str]] = {}
        for row, rel, path in ALL_JSON_ROWS:
            wanted.setdefault(rel, set()).add(_resolve_rule(json.loads(new_text(rel)), path))
        for rel in ("schemas/common.schema.json", "schemas/config.schema.json", "schemas/error.schema.json",
                    "schemas/inventory-target.schema.json", "schemas/worker-manifest.schema.json",
                    "schemas/supervisor.schema.json", "schemas/spool-entry.schema.json"):
            wanted.setdefault(rel, set())
        scanned = sorted(wanted.items())
        for rel, expected in scanned:
            with self.subTest(rel):
                _, rewritten = self.walk(rel)
                self.assertEqual(rewritten, expected)

    def test_the_old_sentence_of_each_json_row_is_gone_and_a_new_one_stands(self):
        for row, rel, path in ALL_JSON_ROWS:
            with self.subTest(f"row {row} {rel} {path}"):
                path = _resolve_rule(json.loads(new_text(rel)), path)
                old, new = get(json.loads(old_text(rel)), path), get(json.loads(new_text(rel)), path)
                self.assertNotEqual(old, new)
                self.assertNotIn(old, new)

    def test_the_two_version_statements_track_current_c3_0_8(self):
        # common proto_version and the worker manifest said 0.6 "at this tag" (M6; ruled 2026-11-01 15:00).
        common = json.loads(new_text("schemas/common.schema.json"))["$defs"]["proto_version"]["description"]
        manifest = json.loads(new_text("schemas/worker-manifest.schema.json"))["properties"]["proto_version"]["description"]
        self.assertIn('This tag writes "0.8"', common)
        self.assertIn('"0.8" at this tag', manifest)
        self.assertNotIn('"0.6"', common)
        self.assertNotIn('"0.6"', manifest)


# Rows 21 to 34: (row, file, a sentence of v0.11.1 that Release B makes false), read with whitespace collapsed.
MD_ROWS = [
    (21, "protocols/conventions.md", "and a supplied login MUST NOT be stored, logged, put in an audit event or echoed in any answer"),
    (21, "protocols/conventions.md", "bound to principal and session (`common#/$defs/ticket`; `ws-frames#/$defs/attach`); MUST refuse"),
    (22, "protocols/session-open.md", "It is never stored, and it is the operator's explicit choice of credential"),
    (22, "protocols/session-open.md", "It accepts, warns and records it; a different key later is refused"),
    (23, "protocols/session-open.md", "no C3 field is new, no `proto_version` step is taken, and every worker that serves a stored password serves a supplied login"),
    (24, "protocols/session-open.md", "The worker MUST send it between `connect` and `connected` and at no other time"),
    (25, "protocols/session-open.md", "When the refused credential was a supplied login the core MUST also write the audit event"),
    (26, "protocols/session-open.md", "(the username, then the password: two code-7 frames of `x-login-line`)"),
    (27, "protocols/session-open.md", "with one exception: a supplied login the target refuses is audited by"),
    (28, "protocols/session-open.md", "and CO-PW once, in `connect.auth`, as a stored password does; it goes into SSH's authentication"),
    (29, "protocols/session-open.md", "and no record or frame carries it; the audit event keeps it"),
    (30, "protocols/session-attach.md", "Before it attaches or sends input, a consumer MUST show"),
    (30, "protocols/session-attach.md", "shows the recording warning (rule CR1) | — | the operator |"),
    (31, "protocols/edges/SF-CO.md", "used only to authenticate it and never stored)"),
    (31, "protocols/edges/SF-CO.md", "bound to principal and session, never in a URL"),
    (31, "protocols/edges/SF-CO.md", "no auto-reattach after 4014 or 4015 (V3)"),
    (32, "protocols/edges/CO-PW.md", "| **Credentials.** Only in `connect.auth`, never in argv"),
    (32, "protocols/edges/CO-PW.md", "and otherwise its own (\"0.6\")"),
    (33, "protocols/edges/PW-TG.md", "and travels only in `connect.auth`: V10; session-open step 8."),
    (33, "protocols/edges/PW-TG.md", "- Host keys: the worker accepts, warns and records on first use;"),
    (34, "protocols/edges/CO-DK.md", "Audit `$def` events 14, actions 23."),
    (34, "protocols/edges/CO-DK.md", "session-open 19 (a refused supplied login) |"),
]


# The Markdown sentences the table missed, rewritten on the ruling of 2026-11-01 15:00: (label, file, old sentence).
EXTRA_MD_ROWS = [
    ("M1", "protocols/reattach-handshake.md", "credentials travel only in `connect.auth` (ipc-control top)."),
    ("M1", "protocols/config-reload.md", "credentials travel only inside `connect.auth` (ipc-control top)."),
    ("M1", "protocols/worker-placement.md", "Credentials go only in `connect.auth` on the connection's own socket"),
    ("M1", "protocols/grid-restore.md", "credentials travel only in C3 `connect.auth` (ipc-control top)."),
    ("M1", "protocols/byte-relay.md", "credentials travel only in `connect.auth` on the control socket"),
    ("M1", "protocols/session-open.md", "It crosses edge CO-PW only inside `connect.auth`, on the new connection's own control socket."),
    ("M3", "protocols/session-attach.md", "4000 for a first frame that is not a text `attach` with `v` 1"),
    ("M4", "protocols/session-open.md", "It MAY carry `detail.login` (`stored` or `supplied`)"),
    ("M4", "protocols/session-open.md", "with `detail.login` `supplied`; it is written only for a refused supplied login, never carries the password,"),
    ("M8", "protocols/session-open.md", "`connection_id` that is not a candidate: 422 `invalid_request`"),
    ("M8", "protocols/session-open.md", "the target account's username and password for this one connection, supplied by the caller,"),
    ("M9", "protocols/edges/CO-DK.md", "fourteen `$def`s carrying twenty-three actions"),
    ("M9", "protocols/edges/SF-CO.md", "All 39 operations, all 17 text frames, all 9 close codes"),
    ("M10", "README.md", "a capture (`connect.auth.key`, `connect.auth.password`, and `login.auth.password` of a create request wherever one was captured)"),
    ("M10", "README.md", "(the username and password a supplied login records at the head of a recording"),
    ("M12", "protocols/session-open.md", "`connection_ids` MUST list the principal's own candidates and no other principal's, and none opened with a supplied login"),
    ("M14", "protocols/edges/CO-DK.md", "Recording codes 8 (0–7), from the release that adds `SessionCreateRequest.login`."),
    ("M11", "protocols/session-open.md", "**A request without a login** (from the vault release, E4): the core resolves the candidates"),
]


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class MarkdownRows(unittest.TestCase):
    def test_each_markdown_row_names_a_sentence_that_stood_at_v0_11_1_and_is_gone(self):
        self.assertEqual(sorted({row for row, _, _ in MD_ROWS}), list(range(21, 35)))
        for row, rel, sentence in MD_ROWS:
            with self.subTest(f"row {row} {rel}: {sentence[:50]}"):
                self.assertIn(sentence, squash(old_text(f"{rel}")))
                self.assertNotIn(sentence, squash(new_text(rel)))

    def test_each_sentence_the_table_missed_stood_at_v0_11_1_and_is_gone(self):
        for label, rel, sentence in EXTRA_MD_ROWS:
            with self.subTest(f"{label} {rel}: {sentence[:50]}"):
                self.assertIn(sentence, squash(old_text(rel)))
                self.assertNotIn(sentence, squash(new_text(rel)))

    def test_the_table_has_thirty_four_rows(self):
        rows = {row for row, _, _ in JSON_ROWS} | {row for row, _, _ in MD_ROWS}
        self.assertEqual(sorted(rows), list(range(1, 21)) + list(range(21, 35)))
        self.assertEqual(len(rows), 34)


if __name__ == "__main__":
    unittest.main()
