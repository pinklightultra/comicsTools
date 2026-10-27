"""Unit tests for pencil_cleaner module in comicsTools."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

# Add pencil_cleaner to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner._pageio import load_gray
from pencil_cleaner.deskew import estimate_skew_angle, flatten_lighting


class TestPencilCleaner(unittest.TestCase):
    def test_load_gray(self):
        with tempfile.TemporaryDirectory() as td:
            img_path = Path(td) / "test.png"
            img = Image.new("RGB", (64, 64), color=(128, 128, 128))
            img.save(img_path)

            arr = load_gray(img_path)
            self.assertEqual(arr.shape, (64, 64))
            self.assertEqual(arr.dtype, np.float32)
            self.assertAlmostEqual(arr[0, 0], 128.0, delta=1.0)

    def test_flatten_lighting(self):
        # Create a synthetic image with a gradient
        h, w = 100, 100
        grad = np.tile(np.linspace(100, 255, w, dtype=np.float32), (h, 1))
        flattened = flatten_lighting(grad)
        self.assertEqual(flattened.shape, (h, w))
        self.assertEqual(flattened.dtype, np.float32)

    def test_estimate_skew_angle_blank_image(self):
        # Blank canvas should have 0 skew
        blank = np.full((100, 100), 255, dtype=np.float32)
        angle = estimate_skew_angle(blank)
        self.assertAlmostEqual(angle, 0.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
