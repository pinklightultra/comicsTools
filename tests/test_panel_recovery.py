"""Reading order and missed-panel recovery in the panel detector.

The cases come from the 15 hand-drawn pencil pages checked on 11-11: a row whose
right panel starts a little higher read out of order, a panel with no box at all,
and a box whose top was cut off above the art."""

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.panels import _recover_missed_panels, reading_order  # noqa: E402


def _framed_page():
    """1200 x 900 page, three framed panels in a row, each with a scribble inside."""
    image = Image.new("L", (1200, 900), 245)
    draw = ImageDraw.Draw(image)
    frames = [(40, 60, 380, 840), (420, 60, 780, 840), (820, 60, 1160, 840)]
    for x0, y0, x1, y1 in frames:
        draw.rectangle((x0, y0, x1, y1), outline=60, width=5)
        for k in range(6):
            draw.line((x0 + 30, y0 + 80 + k * 110, x1 - 30, y0 + 140 + k * 110), fill=80, width=4)
    return np.asarray(image), frames


def _near(a, b, tol=20):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


class TestReadingOrder(unittest.TestCase):
    def test_a_row_reads_left_to_right_even_when_one_panel_starts_higher(self):
        left, middle, right = (0, 300, 300, 600), (310, 330, 600, 600), (610, 280, 900, 600)
        top = (0, 0, 900, 290)
        self.assertEqual(reading_order([right, middle, top, left]), [top, left, middle, right])

    def test_panels_stacked_inside_a_row_read_top_to_bottom(self):
        tall = (0, 0, 400, 800)
        upper, lower = (410, 0, 800, 395), (412, 405, 800, 800)
        self.assertEqual(reading_order([lower, upper, tall]), [tall, upper, lower])


class TestRecovery(unittest.TestCase):
    def test_a_panel_with_no_box_is_found(self):
        gray, frames = _framed_page()
        found = _recover_missed_panels(gray, frames[:2])
        self.assertEqual(len(found), 3)
        self.assertTrue(any(_near(box, frames[2]) for box in found), found)

    def test_a_box_cut_off_above_its_art_is_stretched(self):
        gray, frames = _framed_page()
        x0, y0, x1, y1 = frames[1]
        short = (x0, y0 + 140, x1, y1)  # the detector stopped below the top of the panel
        found = _recover_missed_panels(gray, [frames[0], short, frames[2]])
        self.assertEqual(len(found), 3, found)
        self.assertLess(found[1][1], y0 + 40, "the cut-off box should reach the top of its panel")

    def test_nothing_changes_when_every_panel_has_a_box(self):
        gray, frames = _framed_page()
        self.assertEqual(_recover_missed_panels(gray, frames), frames)


if __name__ == "__main__":
    unittest.main()
