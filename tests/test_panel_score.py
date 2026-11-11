"""The answer-key scorer sorts every miss into the hand fix it needs.

The BLUE175 benchmark itself needs the comic's scans and its checked key, which
are not in this repository. Point COMICSTOOLS_BLUE175 at a folder holding the
reference project (``blue175_ref``) and ``blue175_truth.json`` to run it; it fails
if detection finds fewer panels than the 11-11 baseline."""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.panel_score import score_page, score_pages  # noqa: E402

TRUTH = [(0, 0, 100, 100), (110, 0, 210, 100), (0, 110, 100, 210), (110, 110, 210, 210)]


class TestScore(unittest.TestCase):
    def test_exact_boxes_need_no_fixes(self):
        s = score_page(TRUTH, TRUTH)
        self.assertEqual((s.found, s.fixes), (4, 0))

    def test_a_box_over_two_panels_is_one_merge(self):
        s = score_page([(0, 0, 210, 100), TRUTH[2], TRUTH[3]], TRUTH)
        self.assertEqual((s.found, s.merged, s.fixes), (2, 1, 1))

    def test_a_panel_cut_in_two_is_one_split(self):
        s = score_page([TRUTH[0], TRUTH[1], TRUTH[2], (110, 110, 160, 210), (160, 110, 210, 210)], TRUTH)
        self.assertEqual((s.found, s.split, s.fixes), (3, 1, 1))

    def test_missing_extra_and_loose(self):
        s = score_page([TRUTH[0], (110, 0, 180, 100), (400, 400, 500, 500)], TRUTH[:3])
        self.assertEqual((s.found, s.loose, s.missed, s.extra), (1, 1, 1, 1))

    def test_totals_add_up(self):
        r = score_pages({"a": (TRUTH, TRUTH), "b": ([(0, 0, 210, 100)], TRUTH[:2])})
        self.assertEqual(r["total"]["truth"], 6)
        self.assertEqual(r["total"]["found"], 4)
        self.assertEqual(r["total"]["merged"], 1)


FIXTURE = os.environ.get("COMICSTOOLS_BLUE175")


@unittest.skipUnless(FIXTURE, "COMICSTOOLS_BLUE175 is not set")
class TestBlue175(unittest.TestCase):
    # 11-11 baseline: pencil 128 of 152 found with 13 hand fixes; finished pages 91 of 152 with 52
    def test_detection_is_no_worse_than_the_baseline(self):
        from pencil_cleaner.benchmark import run

        root = Path(FIXTURE)
        result = run(root / "blue175_ref", root / "blue175_truth.json")
        self.assertGreaterEqual(result["pencil"]["total"]["found"], 128)
        self.assertLessEqual(result["pencil"]["total"]["fixes"], 13)
        self.assertGreaterEqual(result["take"]["total"]["found"], 91)
        self.assertLessEqual(result["take"]["total"]["fixes"], 52)


if __name__ == "__main__":
    unittest.main()
