"""The client ignores a known frame type at a version it does not speak (V1, V2).

A receiver ignores a `t` it does not know. V1's consumer rule: a known `t` with an unknown `v` is
ignored as an unknown `t`. `parse_server` recognises a frame by its `t` alone, so a known type at an
unsupported version must be ignored exactly the way an unknown type is.
"""

import json
import unittest

import tsb_contract_client as tcc


def frame(t: str, v: int) -> str:
    """A server frame of type `t` at version `v`, shaped enough to carry its payload."""
    body = {"t": t, "v": v}
    if t == "attached":
        body["client_id"] = "client-1"
        body["session"] = {"session_id": "sess-1", "state": "running"}
    return json.dumps(body, separators=(",", ":"))


class FrameVersion(unittest.TestCase):
    def test_a_known_type_at_an_unknown_version_is_ignored_as_unknown(self):
        # An unknown `t` is the reference: parse_server returns None for it.
        unknown = tcc.frames.parse_server(frame("x-later-frame", 1))
        self.assertIsNone(unknown)
        # A known type at a version the client does not speak is ignored the same way.
        for t in ("attached", "token"):
            self.assertIsNone(tcc.frames.parse_server(frame(t, 2)), t)
            self.assertEqual(tcc.frames.parse_server(frame(t, 2)), unknown, t)

    def test_a_known_type_at_the_client_version_is_parsed(self):
        self.assertEqual(tcc.frames.parse_server(frame("attached", 1)),
                         json.loads(frame("attached", 1)))
        self.assertEqual(tcc.frames.parse_server(frame("token", 1)),
                         json.loads(frame("token", 1)))

    def test_a_version_that_equals_one_but_is_not_the_integer_one_is_ignored(self):
        for raw in ("true", "1.0"):
            text = f'{{"t":"token","v":{raw}}}'
            self.assertIsNone(tcc.frames.parse_server(text), raw)
            self.assertIsNone(tcc.frames.parse_server(text.replace("token", "attached")), raw)
        self.assertIsNotNone(tcc.frames.parse_server('{"t":"token","v":1}'))


if __name__ == "__main__":
    unittest.main()
