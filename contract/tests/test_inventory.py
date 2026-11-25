"""The inventory reader: the rules of the target-file layout, applied to a tree state.

Why each rule matters, in one line: a writer's temporary name read as a target would put a half-written
file in the index; a hidden name that fails the whole load (today's behaviour) makes one stray file
drop every target; an id that is not its stem means two names for one target; two sources with one id
mean the index answers for a target no one wrote; a bad file that stops the load means one Tabby export
empties the fleet; a missing `current` that empties the index means a botched swap takes the fleet
down; rule 10 unapplied means a Python writer's file fails the Go core's schema for the spelling of a
scalar.
"""

import unittest
from typing import ClassVar

from tsbc import _inventory as iv
from tsbc.fixtures import load_fixtures
from tsbc.schema import Registry

from . import CONTRACT


def _ssh(id_):
    return {"id": id_, "type": "ssh", "options": {"host": "10.0.1.1"}}


class Reader(unittest.TestCase):
    REG: ClassVar[Registry] = None

    @classmethod
    def setUpClass(cls):
        cls.REG = Registry.from_tree(CONTRACT)

    def read(self, state):
        return iv.inventory_read(state, self.REG.validate)

    def test_a_writers_temp_name_is_not_a_target(self):
        # A deploy mid-write has .<id>.yaml.tmp beside the target. Read as a target, a half-written
        # file enters the index; rule 2 takes only names ending in .yaml.
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": ".core-rtr-01.yaml.tmp", "doc": _ssh("core-rtr-01")},
            {"source": "manual", "name": "core-rtr-02.yaml", "doc": _ssh("core-rtr-02")}]})
        self.assertEqual([e["id"] for e in out["index"]], ["core-rtr-02"])
        self.assertEqual(out["alerts"], [])

    def test_a_stray_name_loads_nothing_and_blocks_nothing(self):
        # Today a hidden .x.yaml fails the whole load. The reader ignores every name whose stem
        # fails id's pattern, and the rest of the source still loads.
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": ".hidden.yaml", "doc": _ssh("hidden")},
            {"source": "manual", "name": "core-rtr-01.yaml", "doc": _ssh("core-rtr-01")}]})
        self.assertEqual([e["id"] for e in out["index"]], ["core-rtr-01"])

    def test_a_file_whose_id_is_not_its_stem_is_refused(self):
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": "core-rtr-01.yaml", "doc": _ssh("core-rtr-99")}]})
        self.assertEqual(out["index"], [])
        self.assertEqual(out["alerts"], [{"kind": "stem_mismatch", "stem": "core-rtr-01", "source": "manual"}])

    def test_two_sources_with_one_id_load_neither(self):
        # The alert names the id and both sources, never a path (V11).
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": "lab-sw-01.yaml",
             "doc": {"id": "lab-sw-01", "type": "telnet", "options": {"host": "10.0.2.1"}}},
            {"source": "tailscale-lab", "name": "lab-sw-01.yaml",
             "doc": {"id": "lab-sw-01", "type": "telnet", "discovered_by": "tailscale-lab",
                     "options": {"host": "10.0.2.1"}}}]})
        self.assertEqual(out["index"], [])
        self.assertIn({"kind": "duplicate_id", "id": "lab-sw-01",
                       "sources": ["manual", "tailscale-lab"]}, out["alerts"])

    def test_a_bad_file_is_skipped_and_the_rest_loaded(self):
        # One Tabby export with a secret must not empty the fleet.
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": "core-sw-02.yaml",
             "doc": {"id": "core-sw-02", "type": "telnet", "options": {"port": 23}}},
            {"source": "manual", "name": "core-rtr-01.yaml", "doc": _ssh("core-rtr-01")}]})
        self.assertEqual([e["id"] for e in out["index"]], ["core-rtr-01"])
        self.assertIn({"kind": "invalid_file", "stem": "core-sw-02", "source": "manual"}, out["alerts"])

    def test_a_discovered_by_that_is_not_its_source_is_refused(self):
        out = self.read({"current": "v17", "files": [
            {"source": "tailscale-lab", "name": "core-rtr-04.yaml",
             "doc": {"id": "core-rtr-04", "type": "ssh", "discovered_by": "manual",
                     "options": {"host": "10.0.1.4"}}}]})
        self.assertEqual(out["index"], [])
        self.assertEqual(out["alerts"], [{"kind": "discovered_by_mismatch", "stem": "core-rtr-04",
                                          "source": "tailscale-lab"}])

    def test_a_missing_current_keeps_the_index(self):
        # A botched swap takes the link away; the fleet stays up and the operator is told.
        out = self.read({"current": None,
                         "index_before": [{"id": "core-rtr-01", "source": "manual", "tags": ["prod"]}],
                         "files": [{"source": "manual", "name": "core-rtr-01.yaml", "doc": _ssh("core-rtr-01")}]})
        self.assertEqual(out["index"], [{"id": "core-rtr-01", "source": "manual", "tags": ["prod"]}])
        self.assertEqual(out["alerts"], [{"kind": "missing_current"}])

    def test_a_source_that_is_not_id_shaped_is_never_read(self):
        # x-layout rule 1: <source> has common#/$defs/id's shape. A directory whose name does not (a
        # stray .git under current, say) is skipped exactly as a staging directory is: no alert, and
        # nothing it holds loads. The good source still loads.
        out = self.read({"current": "v17", "files": [
            {"source": "manual", "name": "core-rtr-01.yaml", "doc": _ssh("core-rtr-01")},
            {"source": ".git", "name": "core-rtr-02.yaml", "doc": _ssh("core-rtr-02")}]})
        self.assertEqual([e["id"] for e in out["index"]], ["core-rtr-01"])
        self.assertEqual(out["alerts"], [])

    def test_a_staging_directory_is_never_read(self):
        out = self.read({"current": "v18", "files": [
            {"source": "staging.a7f3", "name": "core-rtr-01.yaml",
             "doc": {"id": "core-rtr-01", "type": "ssh", "tags": ["staging"], "options": {"host": "10.0.1.1"}}},
            {"source": "manual", "name": "core-rtr-01.yaml",
             "doc": {"id": "core-rtr-01", "type": "ssh", "tags": ["prod", "site-b"], "options": {"host": "10.0.1.1"}}}]})
        self.assertEqual(out["index"], [{"id": "core-rtr-01", "source": "manual", "tags": ["prod", "site-b"]}])

    def test_a_staging_directory_is_any_name_starting_with_staging(self):
        # x-layout bulk-change: "A staging directory has a unique name, staging.<random>/, and is never
        # read." Any name starting with `staging.` is one, whatever follows the dot: the old test only
        # covered alphanumerics, so `staging.a7-f3` (a real unique-name shape) was read as a source.
        out = self.read({"current": "v18", "files": [
            {"source": "staging.a7-f3", "name": "core-rtr-01.yaml",
             "doc": {"id": "core-rtr-01", "type": "ssh", "options": {"host": "10.0.1.1"}}}]})
        self.assertEqual(out["index"], [])
        self.assertEqual(out["alerts"], [])

    def test_rule_10_makes_a_yaml_scalar_the_text_of_its_string_key(self):
        # The discovery writer is Python (YAML 1.1), the core is Go (YAML 1.2). An unquoted `on` is a
        # boolean to YAML 1.1 and a timestamp is not a string; rule 10 says the JSON form of a
        # string-typed key is the scalar's text, so both load as strings.
        out = self.read({"current": "v17", "files": [{
            "source": "manual", "name": "core-rtr-06.yaml",
            "yaml": {"id": {"text": "core-rtr-06", "yaml11": "string"},
                     "type": {"text": "ssh", "yaml11": "string"},
                     "name": {"text": "on", "yaml11": "bool"},
                     "last_seen": {"text": "2026-03-18T10:30:00Z", "yaml11": "timestamp"},
                     "options": {"host": {"text": "10.0.1.1", "yaml11": "string"}}}}]})
        self.assertEqual(out["index"], [{"id": "core-rtr-06", "source": "manual", "tags": []}])
        doc = out["docs"][0]["doc"]
        self.assertEqual(doc["name"], "on")
        self.assertEqual(doc["last_seen"], "2026-03-18T10:30:00Z")

    def test_rule_10_makes_a_tag_scalar_the_text_of_its_string_key(self):
        # A `tags` item is typed a string, so an unquoted `yes` (YAML 1.1 bool) and an unquoted `null`
        # (YAML 1.1 null) are that scalar's text under rule 10: both load as strings, never as
        # true/None, so the file validates and the index carries the text.
        out = self.read({"current": "v17", "files": [{
            "source": "manual", "name": "core-sw-01.yaml",
            "yaml": {"id": {"text": "core-sw-01", "yaml11": "string"},
                     "type": {"text": "ssh", "yaml11": "string"},
                     "tags": [{"text": "yes", "yaml11": "bool"}, {"text": "null", "yaml11": "null"}],
                     "options": {"host": {"text": "10.0.1.1", "yaml11": "string"}}}}]})
        self.assertEqual(out["index"], [{"id": "core-sw-01", "source": "manual", "tags": ["yes", "null"]}])
        self.assertEqual(out["docs"][0]["doc"]["tags"], ["yes", "null"])
        self.assertEqual(out["alerts"], [])

    def test_rule_10_reads_a_yaml11_int_by_its_notation(self):
        # A YAML 1.1 int scalar is read by its own notation, never by decimal int(text): 0x hexadecimal,
        # a leading-zero (or 0o) octal, 0b binary, an optional sign, and `_` digit separators removed.
        # The JSON form is the value, so the port is an integer the schema accepts and the file loads.
        def ssh_file(id_, port):
            return {"source": "manual", "name": f"{id_}.yaml",
                    "yaml": {"id": {"text": id_, "yaml11": "string"},
                             "type": {"text": "ssh", "yaml11": "string"},
                             "options": {"host": {"text": "10.0.1.7", "yaml11": "string"},
                                         "port": {"text": port, "yaml11": "int"}}}}
        out = self.read({"current": "v17", "files": [
            ssh_file("core-rtr-07", "0x17"), ssh_file("core-rtr-08", "017"), ssh_file("core-rtr-09", "1_000")]})
        self.assertEqual(out["alerts"], [])
        self.assertEqual({d["stem"]: d["doc"]["options"]["port"] for d in out["docs"]},
                         {"core-rtr-07": 23, "core-rtr-08": 15, "core-rtr-09": 1000})

    def test_the_transform_fixtures_are_the_readers_own_records(self):
        # Each C5 inventory transform fixture is the reader applied to its tree; a fixture whose output
        # the rule does not give is a layout that drifts from the code self-check proves.
        fixtures, _ = load_fixtures(CONTRACT)
        for fx in fixtures:
            if fx.kind == "transform" and fx.case["transform"] in iv.INVENTORY_TRANSFORMS:
                for state, want in zip(fx.case["input"]["reads"], fx.case["output"]["results"], strict=True):
                    self.assertEqual(iv.inventory_read(state, self.REG.validate), want, fx.label)
