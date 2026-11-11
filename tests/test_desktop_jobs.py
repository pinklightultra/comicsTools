"""Tests for the desktop job wrappers. No display and no PySide6."""

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.desktop_jobs import (  # noqa: E402
    run_clean,
    run_deskew,
    run_job,
    run_panels,
    run_prep,
    suggest_output,
)


def _pencils(path: Path) -> None:
    im = Image.new("L", (240, 160), 255)
    draw = ImageDraw.Draw(im)
    draw.line((20, 40, 220, 40), fill=170, width=3)
    im.save(path)


def _inked(path: Path) -> None:
    im = Image.new("L", (240, 160), 255)
    draw = ImageDraw.Draw(im)
    draw.line((20, 30, 220, 30), fill=190, width=2)
    draw.line((20, 80, 220, 80), fill=12, width=8)
    im.save(path)


def _page(path: Path) -> None:
    im = Image.new("L", (300, 200), 255)
    draw = ImageDraw.Draw(im)
    draw.rectangle((10, 10, 130, 190), outline=0, width=4)
    draw.rectangle((160, 10, 280, 190), outline=0, width=4)
    im.save(path)


class TestDesktopJobs(unittest.TestCase):
    def test_suggest_output_uses_the_task(self):
        self.assertEqual(suggest_output("prep", "scans/page.png").name, "page-ghost.png")
        self.assertEqual(suggest_output("prep", "scans/page.pdf").name, "page-ghost.pdf")
        self.assertEqual(suggest_output("panels", "scans/page.png").name, "page-panels")

    def test_missing_file_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            run_prep("no-such-scan.png", "ghost.png")

    def test_prep_and_clean_write_previews(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pencils = root / "pencils.png"
            inked = root / "inked.png"
            _pencils(pencils)
            _inked(inked)
            prepared = run_job("prep", input_path=pencils, output_path=root / "ghost.png", opacity="light")
            self.assertTrue(prepared.preview.is_file())
            cleaned = run_clean(inked, root / "clean.png", method="morphological")
            self.assertTrue(cleaned.preview.is_file())
            self.assertIn("Wrote", cleaned.summary)

    def test_deskew_and_panels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            page = root / "page.png"
            _page(page)
            straight = run_deskew(page, root / "straight.png", deskew=True, flatten=True)
            self.assertTrue(straight.preview.is_file())
            panels = run_panels(page, root / "panels")
            self.assertIn("Cut 2 panel", panels.summary)
            self.assertTrue(panels.preview.is_file())

    def test_panels_rejects_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "page.pdf"
            pdf.write_bytes(b"%PDF-1.1\n")
            with self.assertRaises(ValueError):
                run_panels(pdf, root / "out")


if __name__ == "__main__":
    unittest.main()
