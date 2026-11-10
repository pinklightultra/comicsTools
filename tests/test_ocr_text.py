"""OCR is optional. A missing Tesseract install returns no text and a message."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.ocr_text import recognize  # noqa: E402


class TestOcrFallback(unittest.TestCase):
    def test_missing_tesseract_returns_no_text(self):
        image = Image.new("L", (80, 30), 255)
        with patch("pencil_cleaner.ocr_text.shutil.which", return_value=None):
            result = recognize(image)
        self.assertEqual(result.text, "")
        self.assertFalse(result.available)
        self.assertIn("tesseract", result.message.lower())
        self.assertIn("not", result.message.lower())

    def test_missing_python_package_returns_no_text(self):
        image = Image.new("L", (80, 30), 255)
        with patch("pencil_cleaner.ocr_text.shutil.which", return_value="/usr/bin/tesseract"):
            with patch.dict(sys.modules, {"pytesseract": None}):
                result = recognize(image)
        self.assertEqual(result.text, "")
        self.assertFalse(result.available)
        self.assertIn("pytesseract", result.message)


if __name__ == "__main__":
    unittest.main()
