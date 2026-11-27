"""Marks and profiles (development-plan.md §2).

The distinction is load-bearing: a tag that publishes a capability the core serves only epochs
later must not make the core fail its own pinned suite, and a required fixture must never slip
into "skipped" because the profile misread it.
"""

import unittest

from tsbc import profiles as p


class Marks(unittest.TestCase):
    def test_well_formed_marks(self):
        for mark in ({"required-green": "e0"}, {"published": "e0"}, {"published": "e2", "required-green": "e5"}):
            self.assertEqual(p.check_mark(mark), [], mark)

    def test_malformed_marks(self):
        for mark in ({}, None, {"required": "e0"}, {"published": "e9"},
                     {"published": "e5", "required-green": "e2"}):
            self.assertTrue(p.check_mark(mark), mark)


class Profiles(unittest.TestCase):
    def test_required_from_its_epoch_on(self):
        mark = {"required-green": "e1"}
        self.assertEqual(p.status(mark, "e0"), p.FUTURE)
        self.assertEqual(p.status(mark, "e1"), p.REQUIRED)
        self.assertEqual(p.status(mark, "e6"), p.REQUIRED)

    def test_published_before_required(self):
        # Pagination's case: published at e2 for the web lane, required green only at e5.
        mark = {"published": "e2", "required-green": "e5"}
        self.assertEqual(p.status(mark, "e0"), p.FUTURE)
        self.assertEqual(p.status(mark, "e2"), p.PUBLISHED)
        self.assertEqual(p.status(mark, "e4"), p.PUBLISHED)
        self.assertEqual(p.status(mark, "e5"), p.REQUIRED)

    def test_e3b_follows_e4b_and_precedes_e5(self):
        mark = {"published": "e3b", "required-green": "e3b"}
        self.assertEqual(p.status(mark, "e4b"), p.FUTURE)
        self.assertEqual(p.status(mark, "e3b"), p.REQUIRED)
        self.assertEqual(p.status(mark, "e5"), p.REQUIRED)
        self.assertEqual(p.status({"required-green": "e5"}, "e3b"), p.FUTURE)

    def test_later_profiles_are_supersets(self):
        marks = [{"required-green": e} for e in p.EPOCHS] + [{"published": "e0"}]
        for i in range(len(p.EPOCHS) - 1):
            now = {id(m) for m in marks if p.status(m, p.EPOCHS[i]) == p.REQUIRED}
            later = {id(m) for m in marks if p.status(m, p.EPOCHS[i + 1]) == p.REQUIRED}
            self.assertLessEqual(now, later)


if __name__ == "__main__":
    unittest.main()
