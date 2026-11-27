"""The mini JSON Schema validator: the subset it promises, and loud failure outside it.

The contract's schemas are only as strict as this validator. A keyword it does not implement
must fail the schema, or a stranger reading `"format": "uuid"` would believe it is enforced.
"""

import unittest
from typing import ClassVar

from tsbc.schema import Registry, SchemaError, check_schema, json_equal, validate

from . import CONTRACT


def errors(instance, schema, extra_docs=None):
    reg = Registry()
    reg.add("schemas/t.json", schema)
    for name, doc in (extra_docs or {}).items():
        reg.add(name, doc)
    check_schema(schema)
    return validate(instance, schema, reg, "schemas/t.json")


class OutsideTheSubset(unittest.TestCase):
    def test_unknown_keyword_fails_loudly(self):
        for keyword, value in (("format", "uuid"), ("allOf", [{}]), ("not", {}),
                               ("patternProperties", {}), ("if", {}), ("minProperties", 1)):
            with self.subTest(keyword=keyword), self.assertRaises(SchemaError) as ctx:
                check_schema({"type": "object", keyword: value})
            self.assertIn(keyword, str(ctx.exception))

    def test_unknown_keyword_deep_inside_is_found(self):
        with self.assertRaises(SchemaError) as ctx:
            check_schema({"$defs": {"a": {"properties": {"b": {"items": {"uniqueItems": True}}}}}})
        self.assertIn("#/$defs/a/properties/b/items", str(ctx.exception))

    def test_annotations_and_extensions_are_allowed(self):
        check_schema({"title": "t", "description": "d", "$comment": "c", "examples": [1],
                      "x-binary-layout": {"anything": True}, "type": "integer"})

    def test_array_form_of_items_is_rejected(self):
        with self.assertRaises(SchemaError):
            check_schema({"items": [{"type": "string"}]})

    def test_bad_keyword_values_are_rejected(self):
        for schema in ({"type": "text"}, {"required": "a"}, {"enum": []}, {"pattern": "("},
                       {"minLength": -1}, {"oneOf": []}):
            with self.subTest(schema=schema), self.assertRaises(SchemaError):
                check_schema(schema)

    def test_unresolvable_ref_is_a_schema_error(self):
        with self.assertRaises(SchemaError):
            errors(1, {"$ref": "missing.schema.json#/$defs/x"})


class TheSubset(unittest.TestCase):
    def test_types_follow_json_not_python(self):
        self.assertTrue(errors(True, {"type": "integer"}))       # a bool is not an integer
        self.assertFalse(errors(2.0, {"type": "integer"}))       # 2.0 is
        self.assertFalse(errors(3, {"type": "number"}))
        self.assertFalse(errors(None, {"type": ["string", "null"]}))
        self.assertFalse(json_equal(True, 1))
        self.assertTrue(json_equal(1, 1.0))

    def test_object_keywords(self):
        schema = {"type": "object", "required": ["a"], "additionalProperties": False,
                  "properties": {"a": {"type": "string", "minLength": 2, "maxLength": 3, "pattern": "^x"}}}
        self.assertFalse(errors({"a": "xy"}, schema))
        self.assertIn("missing required property 'a'", errors({}, schema)[0])
        self.assertIn("not allowed", errors({"a": "xy", "b": 1}, schema)[0])
        self.assertTrue(errors({"a": "x"}, schema))
        self.assertTrue(errors({"a": "xyzw"}, schema))
        self.assertTrue(errors({"a": "yy"}, schema))

    def test_additional_properties_as_schema(self):
        schema = {"type": "object", "additionalProperties": {"type": "integer"}}
        self.assertFalse(errors({"any": 1}, schema))
        self.assertTrue(errors({"any": "1"}, schema))

    def test_numbers_enum_const_arrays(self):
        self.assertTrue(errors(0, {"minimum": 1}))
        self.assertTrue(errors(65536, {"maximum": 65535}))
        self.assertTrue(errors("c", {"enum": ["a", "b"]}))
        self.assertTrue(errors(2, {"const": 1}))
        self.assertTrue(errors([], {"type": "array", "minItems": 1}))
        self.assertTrue(errors([1, 2], {"type": "array", "maxItems": 1}))
        self.assertTrue(errors(["a", 1], {"type": "array", "items": {"type": "string"}}))

    def test_one_of_means_exactly_one(self):
        schema = {"oneOf": [{"type": "integer"}, {"minimum": 0}]}
        self.assertFalse(errors(-1, schema))            # integer only
        self.assertIn("2 branches", errors(5, schema)[0])  # both match
        self.assertTrue(errors("x", {"oneOf": [{"type": "integer"}]}))

    def test_any_of(self):
        self.assertFalse(errors(5, {"anyOf": [{"type": "integer"}, {"minimum": 0}]}))
        self.assertTrue(errors("x", {"anyOf": [{"type": "integer"}]}))

    def test_refs_resolve_by_file_location(self):
        other = {"$defs": {"id": {"type": "string", "pattern": "^[a-z]+$"}}}
        schema = {"$ref": "other.schema.json#/$defs/id"}
        docs = {"schemas/other.schema.json": other}
        self.assertFalse(errors("abc", schema, docs))
        self.assertTrue(errors("ABC", schema, docs))

    def test_local_ref_and_boolean_schemas(self):
        schema = {"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}, "b": False}}
        self.assertFalse(errors({"a": 1}, schema))
        self.assertTrue(errors({"a": "1"}, schema))
        self.assertTrue(errors({"b": 1}, schema))


class PatternsAreEcma(unittest.TestCase):
    """JSON Schema patterns are ECMA-262. Python's `$` also matches before a final newline, so
    without the translation "fixture-session-a\\n" would pass the id pattern and become a file
    name, a ticket with a newline would pass as a ticket, and so on."""

    REG = Registry.from_tree(CONTRACT)
    CASES: ClassVar[dict[str, str]] = {
        "schemas/common.schema.json#/$defs/id": "fixture-session-a",
        "schemas/common.schema.json#/$defs/ticket": "FIXTURE-TICKET-000000000000000000",
        "schemas/common.schema.json#/$defs/timestamp": "2026-09-23T12:00:00Z",
        "schemas/supervisor.schema.json#/$defs/conn_dir": "/tmp/tsb/conn-fixture-conn-1",
        "schemas/ipc-control.schema.json#/$defs/socket": "shell.sock",
    }

    def test_value_passes_and_the_same_value_with_a_trailing_newline_fails(self):
        for ref, value in self.CASES.items():
            with self.subTest(ref=ref):
                self.assertEqual(self.REG.validate(value, ref), [])
                self.assertTrue(self.REG.validate(value + "\n", ref), f"{value!r}+newline passed {ref}")

    def test_dollar_inside_a_class_or_escaped_stays_literal(self):
        self.assertFalse(errors("a$", {"pattern": "^a[$]$"}))
        self.assertFalse(errors("a$", {"pattern": "^a\\$$"}))
        self.assertTrue(errors("a$\n", {"pattern": "^a\\$$"}))

    def test_classes_are_ascii(self):
        self.assertTrue(errors("٣", {"pattern": "^\\d$"}))  # ARABIC-INDIC DIGIT THREE


class SessionTypeVocabulary(unittest.TestCase):
    """session_type is the word the audit log, the manifests, C3's channel_opened and the session record
    use to say what a session is, so a shell and a file transfer are never indistinguishable (Storage §6).
    It must be session model §2's list exactly. A value missing here makes a line a conforming core writes
    — an exec session's audit event — fail every v0.1 reader that validates strictly; a value added here
    names a session the corpus does not define. And there must be one list, or the copies drift."""

    SESSION_MODEL_2: ClassVar[set[str]] = {"shell", "exec", "sftp", "netconf", "forward"}  # session model §2, vault 3920e0f

    def test_the_vocabulary_is_session_model_section_2s(self):
        reg = Registry.from_tree(CONTRACT)
        enum = reg.docs["schemas/common.schema.json"]["$defs"]["session_type"]["enum"]
        self.assertEqual(set(enum), self.SESSION_MODEL_2)
        self.assertEqual(len(enum), len(self.SESSION_MODEL_2))
        event = {"ts": "2026-09-26T12:00:00Z", "user": "u", "action": "session.connect", "target": "t1",
                 "session_id": "s1", "session_type": "exec"}
        self.assertEqual(reg.validate(event, "schemas/audit-event.schema.json"), [])

    def test_every_schema_uses_the_one_list(self):
        reg = Registry.from_tree(CONTRACT)
        found = []

        def walk(node, where):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "session_type" and isinstance(value, dict) and ("$ref" in value or "enum" in value):
                        found.append((where, value))
                    walk(value, f"{where}/{key}")
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{where}/{i}")

        for name, doc in reg.docs.items():
            walk(doc, name)
        self.assertTrue(found)
        for where, value in found:
            with self.subTest(where):
                if where.startswith("schemas/common.schema.json/$defs"):
                    continue  # the list itself
                self.assertEqual(set(value), {"$ref"}, "a restated list drifts from session model §2")
                self.assertTrue(value["$ref"].endswith("common.schema.json#/$defs/session_type"))


class TimestampPattern(unittest.TestCase):
    """Every session and audit timestamp is this one definition: a digit count is not a date."""

    REF = "schemas/common.schema.json#/$defs/timestamp"

    def test_real_instants_pass(self):
        reg = Registry.from_tree(CONTRACT)
        for ok in ("2026-09-23T12:00:00Z", "2026-12-31T23:59:60Z", "2028-02-29T00:00:00.123456789Z",
                   "2026-04-30T00:00:00Z", "2026-01-31T00:00:00Z"):
            with self.subTest(ok):
                self.assertEqual(reg.validate(ok, self.REF), [])

    def test_impossible_fields_fail(self):
        reg = Registry.from_tree(CONTRACT)
        for bad in ("2026-99-99T25:99:99Z", "2026-13-01T00:00:00Z", "2026-00-10T00:00:00Z",
                    "2026-09-00T00:00:00Z", "2026-04-31T00:00:00Z", "2026-02-30T00:00:00Z",
                    "2026-09-23T24:00:00Z", "2026-09-23T12:60:00Z", "2026-09-23T12:00:61Z"):
            with self.subTest(bad):
                self.assertTrue(reg.validate(bad, self.REF))

    def test_the_parser_refuses_non_finite_numbers(self):
        from tsbc.schema import loads
        for text in ("NaN", '{"a": Infinity}', "[-Infinity]"):
            with self.subTest(text), self.assertRaises(ValueError):
                loads(text)
        self.assertEqual(loads('{"a": 1.5}'), {"a": 1.5})


if __name__ == "__main__":
    unittest.main()
