"""Engine tests for the desktop project: import, panels, layout, ghost PDF."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.mark_cleanup import MARK_D, MARK_INSET  # noqa: E402
from pencil_cleaner.studio_project import Project, clamp_box  # noqa: E402


def _two_panel_page(path: Path) -> None:
    image = Image.new("L", (600, 400), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 270, 380), outline=0, width=4)
    draw.line((40, 60, 240, 200), fill=40, width=6)
    draw.rectangle((320, 20, 580, 380), outline=0, width=4)
    draw.line((350, 80, 540, 300), fill=30, width=6)
    image.save(path)


def _mark_pixel(page: Image.Image) -> int:
    dpi = 300
    cx = int(round(MARK_INSET * dpi))
    cy = int(round(MARK_INSET * dpi))
    return page.getpixel((cx, cy))


class TestStudioProject(unittest.TestCase):
    def test_clamp_keeps_a_minimum_box_on_the_page(self):
        self.assertEqual(clamp_box(-20, -5, 4, 1000, 100, 80), (0, 0, 8, 80))

    def test_import_detects_panels_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _two_panel_page(scan)
            project = Project(name="Issue")
            project.columns, project.rows = 2, 2
            added = project.import_files([scan])
            self.assertEqual(len(added), 1)
            self.assertEqual(len(project.panels), 2)
            page = project.sources[0]
            self.assertGreater(page.width, 0)
            self.assertTrue(project.image_path(page.id).is_file())
            for panel in project.panels:
                self.assertLess(panel.x0, panel.x1)
                self.assertLess(panel.y0, panel.y1)
                self.assertGreaterEqual(panel.x0, 0)
                self.assertLessEqual(panel.x1, page.width)
            saved = project.save(root / "proj")
            loaded = Project.load(saved)
            self.assertEqual(loaded.name, "Issue")
            self.assertEqual(len(loaded.panels), 2)
            self.assertEqual(loaded.panels[0].box(), project.panels[0].box())
            self.assertTrue((saved / "project.json").is_file())
            self.assertEqual(json.loads((saved / "project.json").read_text())["version"], 1)

    def test_edit_split_merge_duplicate_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _two_panel_page(scan)
            project = Project()
            project.import_files([scan])
            first = project.reading_order()[0]
            project.set_rect(first.id, first.x0, first.y0, first.x0 + 80, first.y0 + 80)
            self.assertEqual(project.panel(first.id).width(), 80)
            left, right = project.split_panel(first.id)
            self.assertLess(left.x1, right.x0 + 1)
            self.assertEqual(left.x1, right.x0)
            merged = project.merge_panels(left.id, right.id)
            self.assertGreaterEqual(merged.width(), 80)
            copy = project.duplicate_panel(merged.id)
            self.assertEqual(copy.box(), merged.box())
            self.assertNotEqual(copy.id, merged.id)
            before = [p.id for p in project.reading_order()]
            project.move_by(before[0], 1)
            after = [p.id for p in project.reading_order()]
            self.assertEqual(after[0], before[1])
            self.assertEqual(after[1], before[0])
            project.move_to_page(after[0], 3)
            self.assertEqual(project.panel(after[0]).board_page, 3)
            project.delete_panel(copy.id)
            self.assertNotIn(copy.id, {p.id for p in project.panels})

    def test_pack_grid_and_drag_order(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _two_panel_page(scan)
            project = Project()
            project.columns, project.rows = 2, 2
            project.import_files([scan])
            # Four manual panels so the grid has to cross a page.
            source = project.sources[0].id
            while len(project.panels) < 5:
                project.add_panel(source, 30, 30, 80, 90)
            project.pack_grid()
            pages = {p.board_page for p in project.panels}
            self.assertEqual(pages, {0, 1})
            self.assertEqual(len(project.panels_on(0)), 4)
            ordered = project.reading_order()
            pairs = [(p.id, 0 if i < 3 else 1) for i, p in enumerate(ordered)]
            project.set_reading_order(pairs)
            self.assertEqual(len(project.panels_on(0)), 3)
            self.assertEqual(project.reading_number(ordered[0].id), 1)
            with self.assertRaises(ValueError):
                project.set_reading_order(pairs[:-1])

    def test_bleed_is_source_pixels_and_clamped(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _two_panel_page(scan)
            project = Project()
            project.bleed = 12
            project.import_files([scan])
            panel = project.panels[0]
            panel.x0, panel.y0 = 0, 0
            x0, y0, x1, y1 = project.crop_box(panel)
            self.assertEqual((x0, y0), (0, 0))
            self.assertGreaterEqual(x1, panel.x1)
            self.assertLessEqual(x1, project.sources[0].width)

    def test_ghost_pdf_has_marks_swatches_and_a_lighter_panel(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _two_panel_page(scan)
            project = Project()
            project.opacity = "light"
            project.import_files([scan])
            pages = project.render_ghost_pages()
            self.assertGreaterEqual(len(pages), 2)
            ghost = pages[0]
            test = pages[-1]
            self.assertEqual(_mark_pixel(ghost), 0)
            self.assertEqual(_mark_pixel(test), 0)
            # First swatch (10%) is lighter than the last (40%).
            width, height = test.size
            art_x = int(round(0.75 * 300))
            art_y = int(round(2.0 * 300))
            col = (int(round(7.75 * 300)) - art_x) // 7
            light = test.getpixel((art_x + col // 2, art_y))
            dark = test.getpixel((art_x + col * 6 + col // 2, art_y))
            self.assertGreater(light, dark)
            dest = root / "ghost.pdf"
            project.write_ghost_pdf(dest)
            self.assertTrue(dest.read_bytes().startswith(b"%PDF"))
            gray = np.asarray(Image.open(project.image_path(project.sources[0].id)).convert("L"))
            panel = project.panels[0]
            source_mean = float(gray[panel.y0 : panel.y1, panel.x0 : panel.x1].mean())
            self.assertLess(source_mean, 250)

    def test_merge_rejects_different_scans(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.png"
            b = root / "b.png"
            _two_panel_page(a)
            _two_panel_page(b)
            project = Project()
            project.import_files([a, b])
            ids = [p.id for p in project.panels]
            # Panels from the second import are a different source than the first.
            sources = {p.source_id for p in project.panels}
            self.assertEqual(len(sources), 2)
            first = next(p for p in project.panels if p.source_id == project.sources[0].id)
            second = next(p for p in project.panels if p.source_id == project.sources[1].id)
            with self.assertRaises(ValueError):
                project.merge_panels(first.id, second.id)
            self.assertEqual(len(project.panels), len(ids))

    def test_missing_file_and_bad_version(self):
        project = Project()
        with self.assertRaises(FileNotFoundError):
            project.import_files(["/tmp/no-such-comics-scan.png"])
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "project.json").write_text('{"version": 9}\n')
            with self.assertRaises(ValueError):
                Project.load(folder)


if __name__ == "__main__":
    unittest.main()
