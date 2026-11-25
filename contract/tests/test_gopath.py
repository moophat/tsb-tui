"""`tsbc._gopath.match`: Go's `path.Match`, which the core's RBAC filter uses for `host:` patterns and so a grant's
selectors use too (Storage §9, reused verbatim; ruling 2026-10-29 23:36).

`GO_ANSWERS` was produced by running Go 1.22.2's `path.Match` over each (pattern, name) pair: the table in Go's own
`path/match_test.go` as remembered, plus the `host:` patterns of the selector tests in `test_vault_candidates.py`. `True` and `False` are
the match; `"bad"` is `ErrBadPattern`, which the core's filter reads as no match (`ok, _ := path.Match(...)`). Python
is not `fnmatch`: a `*` or `?` never crosses `/`, a class is `[^...]` and takes `\\` escapes, a `-` or `]` at the
wrong place is a bad pattern.
"""

import unittest

from tsbc._gopath import BadPattern, match

GO_ANSWERS = (
    ('abc', 'abc', True),
    ('*', 'abc', True),
    ('*c', 'abc', True),
    ('a*', 'a', True),
    ('a*', 'abc', True),
    ('a*', 'ab/c', False),
    ('a*/b', 'abc/b', True),
    ('a*/b', 'a/c/b', False),
    ('a*b*c*d*e*/f', 'axbxcxdxe/f', True),
    ('a*b*c*d*e*/f', 'axbxcxdxexxx/f', True),
    ('a*b*c*d*e*/f', 'axbxcxdxe/xxx/f', False),
    ('a*b*c*d*e*/f', 'axbxcxdxexxx/fff', False),
    ('a*b?c*x', 'abxbbxdbxebxczzx', True),
    ('a*b?c*x', 'abxbbxdbxebxczzy', False),
    ('ab[c]', 'abc', True),
    ('ab[b-d]', 'abc', True),
    ('ab[e-g]', 'abc', False),
    ('ab[^c]', 'abc', False),
    ('ab[^b-d]', 'abc', False),
    ('ab[^e-g]', 'abc', True),
    ('a\\*b', 'a*b', True),
    ('a\\*b', 'ab', False),
    ('a?b', 'a☺b', True),
    ('a[^a]b', 'a☺b', True),
    ('a???b', 'a☺b', False),
    ('a[^a][^a][^a]b', 'a☺b', False),
    ('[a-ζ]*', 'α', True),
    ('*[a-ζ]', 'A', False),
    ('a?b', 'a/b', False),
    ('a*b', 'a/b', False),
    ('[\\]a]', ']', True),
    ('[\\-]', '-', True),
    ('[x\\-]', 'x', True),
    ('[x\\-]', '-', True),
    ('[x\\-]', 'z', False),
    ('[\\-x]', 'x', True),
    ('[\\-x]', '-', True),
    ('[\\-x]', 'a', False),
    ('[]a]', ']', 'bad'),
    ('[-]', '-', 'bad'),
    ('[x-]', 'x', 'bad'),
    ('[x-]', 'z', 'bad'),
    ('[-x]', 'x', 'bad'),
    ('[-x]', 'a', 'bad'),
    ('\\', 'a', 'bad'),
    ('[a-b-c]', 'a', 'bad'),
    ('[', 'a', 'bad'),
    ('[^', 'a', 'bad'),
    ('[^bc', 'a', 'bad'),
    ('a[', 'a', 'bad'),
    ('a[', 'ab', 'bad'),
    ('a[', 'x', 'bad'),
    ('a/b[', 'x', 'bad'),
    ('*x', 'xxx', True),
    ('staging-*', 'staging-web', True),
    ('staging-*', 'staging-', True),
    ('staging-*', 'web-staging', False),
    ('staging-*', 'staging-a/b', False),
    ('192.0.2.5*', '192.0.2.50', True),
    ('192.0.2.5?', '192.0.2.50', True),
    ('192.0.2.5?', '192.0.2.5', False),
    ('192.0.2.5?', '192.0.2.500', False),
    ('192.0.2.[1-5]0', '192.0.2.30', True),
    ('192.0.2.[1-5]0', '192.0.2.60', False),
    ('192.0.2.[^1-5]0', '192.0.2.60', True),
    ('192.0.2.[^1-5]0', '192.0.2.30', False),
    ('lab/*', 'lab/web-1', True),
    ('lab*', 'lab/web-1', False),
    ('lab/*', 'lab/a/b', False),
    ('*/*', 'lab/web-1', True),
    ('*', 'lab/web-1', False),
    ('?', '/', False),
    ('a?b', 'a/b', False),
    ('[/]', '/', True),
    ('[^a]', '/', True),
    ('lab/?eb-1', 'lab/web-1', True),
    ('lab\\/web-1', 'lab/web-1', True),
    ('[a-', 'a', 'bad'),
    ('[a', 'a', 'bad'),
    ('[]', 'a', 'bad'),
    ('[^]', 'a', 'bad'),
    ('[a-]', 'a', 'bad'),
    ('a\\', 'a', 'bad'),
    ('[\\', 'a', 'bad'),
    ('*[', 'a', 'bad'),
    ('*[a', 'a', 'bad'),
    ('**', 'abc', True),
    ('a**b', 'ab', True),
    ('a**b', 'a/b', False),
    ('*a*', 'bab', True),
    ('*a*', 'b/ab', False),
    ('', '', True),
    ('', 'a', False),
    ('a', '', False),
)


class MatchesGoPathMatch(unittest.TestCase):
    def test_every_answer_go_gave(self):
        for pattern, name, expected in GO_ANSWERS:
            with self.subTest(pattern=pattern, name=name):
                if expected == "bad":
                    with self.assertRaises(BadPattern):
                        match(pattern, name)
                else:
                    self.assertIs(match(pattern, name), expected)

    def test_the_table_holds_each_kind_of_answer(self):
        answers = {a for _, _, a in GO_ANSWERS}
        self.assertEqual(answers, {True, False, "bad"})
        self.assertGreaterEqual(len(GO_ANSWERS), 90)


if __name__ == "__main__":
    unittest.main()
