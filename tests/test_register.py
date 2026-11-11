"""Lining up takes of the same page: a pencil scan and an ink scan that was shifted,
rotated, scaled and bent a little, as a second scan of the same sheet is."""

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.register import (  # noqa: E402
    RegistrationError,
    match_score,
    measure_error,
    page_signature,
    register,
    warp_take,
)
from pencil_cleaner.studio_project import Project  # noqa: E402


def _page(seed: int, size=(900, 1200)) -> np.ndarray:
    """A page of framed panels with random strokes: enough line texture to match on."""
    rng = np.random.default_rng(seed)
    w, h = size
    image = Image.new("L", size, 245)
    draw = ImageDraw.Draw(image)
    for row in range(3):
        for col in range(2):
            x0, y0 = 40 + col * 420, 40 + row * 380
            draw.rectangle((x0, y0, x0 + 400, y0 + 360), outline=40, width=4)
            for _ in range(18):
                pts = [(int(x0 + rng.uniform(15, 385)), int(y0 + rng.uniform(15, 345))) for _ in range(3)]
                draw.line(pts, fill=int(rng.uniform(30, 120)), width=int(rng.uniform(2, 5)))
            for _ in range(6):
                cx, cy, r = x0 + rng.uniform(40, 360), y0 + rng.uniform(40, 320), rng.uniform(8, 30)
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=50, width=3)
    return np.asarray(image, dtype=np.uint8)


def _rescan(gray: np.ndarray, bend: float = 3.0) -> np.ndarray:
    """The same sheet scanned again: rotated, scaled, shifted, darker lines, a slow bend."""
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 1.7, 1.06)
    M[:, 2] += (25, -18)
    moved = cv2.warpAffine(gray, M, (w + 60, h + 60), borderValue=245)
    ys, xs = np.mgrid[0 : moved.shape[0], 0 : moved.shape[1]].astype(np.float32)
    xs += bend * np.sin(ys / moved.shape[0] * np.pi)  # paper curl: middle drifts sideways
    bent = cv2.remap(moved, xs, ys, cv2.INTER_LINEAR, borderValue=245)
    return np.clip(bent.astype(np.float32) * 0.9, 0, 255).astype(np.uint8)  # ink is darker


class TestRegister(unittest.TestCase):
    def test_a_rescan_lines_up_under_a_pixel(self):
        pencil = _page(1)
        take = _rescan(pencil)
        alignment = register(pencil, take)
        warped = warp_take(take, alignment, pencil.shape[1], pencil.shape[0])
        error, tiles = measure_error(pencil, warped)
        self.assertGreater(tiles, 8)
        self.assertLess(error, 1.0, f"median misalignment {error:.2f} px")

    def test_the_touch_up_fixes_a_bend_the_page_fit_cannot(self):
        pencil = _page(2)
        take = _rescan(pencil, bend=6.0)
        fit_only = register(pencil, take, refine=False)
        touched = register(pencil, take)
        before, _ = measure_error(pencil, warp_take(take, fit_only, pencil.shape[1], pencil.shape[0]))
        after, _ = measure_error(pencil, warp_take(take, touched, pencil.shape[1], pencil.shape[0]))
        self.assertLess(after, before)

    def test_different_pages_do_not_match(self):
        a, b = page_signature(_page(3)), page_signature(_page(4))
        same = page_signature(_rescan(_page(3)))
        self.assertGreater(match_score(same, a), 5 * max(1, match_score(b, a)))

    def test_blank_paper_is_refused(self):
        with self.assertRaises(RegistrationError):
            register(_page(5), np.full((1200, 900), 245, np.uint8))


class TestProjectTakes(unittest.TestCase):
    def test_takes_pair_with_their_pencils_in_any_order_and_crops_line_up(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pencils = [_page(10), _page(11)]
            for i, gray in enumerate(pencils):
                Image.fromarray(gray).save(root / f"pencil{i}.png")
            # the ink scans arrive in the other order
            Image.fromarray(_rescan(pencils[1])).save(root / "ink_a.png")
            Image.fromarray(_rescan(pencils[0])).save(root / "ink_b.png")
            project = Project()
            project.import_files([root / "pencil0.png", root / "pencil1.png"])
            results = project.import_takes([root / "ink_a.png", root / "ink_b.png"])
            names = {s.id: s.name for s in project.sources}
            paired = {names[t]: names[p] for t, p, _ in results}
            self.assertEqual(paired, {"ink_a": "pencil1", "ink_b": "pencil0"})
            for _t, _p, note in results:
                self.assertIn("lined up", note)
            panel = next(p for p in project.panels if names[p.source_id] == "pencil0")
            pencil_crop = np.asarray(project._finished_crop(panel), np.float32)
            panel.stage = "ink"
            ink_crop = np.asarray(project._finished_crop(panel), np.float32)
            self.assertEqual(pencil_crop.shape, ink_crop.shape)
            corr = np.corrcoef(pencil_crop.ravel(), ink_crop.ravel())[0, 1]
            self.assertGreater(corr, 0.8, "the ink crop should show the same art as the pencil crop")
            saved = project.save(root / "proj")
            again = Project.load(saved)
            ink = next(s for s in again.sources if s.name == "ink_b")
            self.assertTrue(ink.alignment)
            self.assertEqual(again.view_gray(ink.id).shape, again.view_gray(ink.linked_id).shape)


if __name__ == "__main__":
    unittest.main()
