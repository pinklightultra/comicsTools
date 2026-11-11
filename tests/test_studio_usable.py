"""Project-model pieces the desktop window relies on to be safe to use day to day:
change tracking, atomic save, blank-box filtering, the scan cache, and finished-page export."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.studio_project import Project, box_has_ink  # noqa: E402


def _page(path: Path, blank_band: bool = False) -> None:
    image = Image.new("L", (600, 520 if blank_band else 400), 255)
    draw = ImageDraw.Draw(image)
    top = 140 if blank_band else 20
    if blank_band:
        draw.rectangle((20, 20, 580, 110), outline=0, width=4)  # a boxed but empty strip
    draw.rectangle((20, top, 270, top + 360), outline=0, width=4)
    draw.line((40, top + 40, 240, top + 180), fill=40, width=6)
    draw.rectangle((320, top, 580, top + 360), outline=0, width=4)
    draw.line((350, top + 60, 540, top + 280), fill=30, width=6)
    image.save(path)


class TestUsable(unittest.TestCase):
    def test_signature_tracks_every_saved_change(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            empty = project.state_signature()
            project.import_files([scan])
            imported = project.state_signature()
            self.assertNotEqual(empty, imported)
            project.save(Path(td) / "proj")
            self.assertEqual(imported, project.state_signature())  # saving changes nothing in the state
            project.set_meta(project.panels[0].id, tags=["rain"])
            self.assertNotEqual(imported, project.state_signature())
            changed = project.state_signature()
            project.opacity = "dark"
            self.assertNotEqual(changed, project.state_signature())

    def test_has_folder_only_after_a_real_save(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            self.assertFalse(project.has_folder())
            project.import_files([scan])
            self.assertFalse(project.has_folder())  # import lands in a temp folder
            project.save(Path(td) / "proj")
            self.assertTrue(project.has_folder())

    def test_save_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            project.import_files([scan])
            out = project.save(Path(td) / "proj")
            self.assertTrue((out / "project.json").is_file())
            self.assertFalse((out / "project.json.tmp").exists())
            self.assertEqual(len(Project.load(out).panels), 2)

    def test_blank_boxes_are_dropped(self):
        gray = np.full((200, 300), 255, dtype=np.uint8)
        gray[0:4, :] = 0  # a border line inside the box's outer tenth
        self.assertFalse(box_has_ink(gray, (0, 0, 300, 200)))
        gray[90:110, 140:160] = 20
        self.assertTrue(box_has_ink(gray, (0, 0, 300, 200)))

    def test_detect_skips_an_empty_boxed_strip(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan, blank_band=True)
            project = Project()
            project.import_files([scan])
            for panel in project.panels:
                self.assertGreater(panel.y0, 100, "the empty strip at the top came back as a panel")
            self.assertEqual(len(project.panels), 2)

    def test_view_cache_is_read_only_and_follows_the_angle(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            project.import_files([scan])
            sid = project.sources[0].id
            first = project.view_gray(sid)
            self.assertIs(first, project.view_gray(sid))
            with self.assertRaises(ValueError):
                first[0, 0] = 0
            project.sources[0].skew_degrees = 3.0
            self.assertIsNot(first, project.view_gray(sid))

    def test_finished_pages_in_three_formats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _page(scan)
            project = Project()
            project.dpi = 100
            project.import_files([scan])
            pdf = project.write_pages(root / "out.pdf", "pdf")
            self.assertEqual(len(pdf), 1)
            self.assertTrue(pdf[0].is_file())
            pngs = project.write_pages(root / "png", "png")
            self.assertEqual([p.name for p in pngs], ["page-001.png"])
            tiffs = project.write_pages(root / "tif", "tif")
            self.assertEqual([p.name for p in tiffs], ["page-001.tif"])
            page = np.asarray(Image.open(pngs[0]).convert("L"))
            self.assertEqual(int(page[3:12, 3:12].min()), 255, "finished pages carry no corner marks")
            self.assertLess(int(page.min()), 60, "finished pages are full strength, not a ghost")
            with self.assertRaises(ValueError):
                project.write_pages(root / "x", "gif")

    def test_ghost_crops_are_reused_until_the_crop_changes(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            project.dpi = 100
            project.import_files([scan])
            first = [np.asarray(p) for p in project.render_ghost_pages(include_test=False)]
            cached = len(project._ghost_cache)
            self.assertEqual(cached, len(project.panels))
            again = [np.asarray(p) for p in project.render_ghost_pages(include_test=False)]
            self.assertEqual(len(project._ghost_cache), cached, "a second export should reuse every crop")
            for a, b in zip(first, again):
                np.testing.assert_array_equal(a, b)
            project.bleed = project.bleed + 6
            project.render_ghost_pages(include_test=False)
            self.assertEqual(len(project._ghost_cache), cached * 2, "a new bleed is a new crop")

    def test_ghost_crops_survive_a_save(self):
        with tempfile.TemporaryDirectory() as td:
            scan = Path(td) / "page.png"
            _page(scan)
            project = Project()
            project.dpi = 100
            project.import_files([scan])
            project.render_ghost_pages(include_test=False)
            cached = len(project._ghost_cache)
            project.save(Path(td) / "proj")  # moves the project out of its temp folder
            project.render_ghost_pages(include_test=False)
            self.assertEqual(len(project._ghost_cache), cached, "saving should not throw the crops away")

    def test_finished_pages_need_a_layout(self):
        with self.assertRaises(ValueError):
            Project().write_pages(Path(tempfile.gettempdir()) / "nothing.pdf")


if __name__ == "__main__":
    unittest.main()
