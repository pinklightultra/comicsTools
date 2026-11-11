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
from pencil_cleaner.studio_project import Panel, Project, SourcePage, clamp_box  # noqa: E402


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
            self.assertEqual(json.loads((saved / "project.json").read_text())["version"], 2)
            self.assertEqual(scan.read_bytes(), project.image_path(page.id).read_bytes())
            self.assertEqual(len(loaded.placements), 2)

    def test_edit_split_merge_duplicate_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _two_panel_page(scan)
            project = Project()
            project.import_files([scan])
            first = project.panel(project.reading_order()[0].panel_id)
            before = len(project.panels)
            project.set_rect(first.id, first.x0, first.y0, first.x0 + 80, first.y0 + 80)
            self.assertEqual(project.panel(first.id).width(), 80)
            project.undo()
            self.assertNotEqual(project.panel(first.id).width(), 80)
            project.redo()
            self.assertEqual(project.panel(first.id).width(), 80)
            left, right = project.split_panel(first.id)
            self.assertLess(left.x1, right.x0 + 1)
            self.assertEqual(left.x1, right.x0)
            merged = project.merge_panels(left.id, right.id)
            self.assertGreaterEqual(merged.width(), 80)
            uses_before = len(project.placements)
            again = project.duplicate_panel(merged.id)
            self.assertEqual(again.panel_id, merged.id)
            self.assertEqual(len(project.panels), before)
            self.assertEqual(len(project.placements), uses_before + 1)
            order_before = [p.id for p in project.reading_order()]
            project.move_by(order_before[0], 1)
            order_after = [p.id for p in project.reading_order()]
            self.assertEqual(order_after[0], order_before[1])
            self.assertEqual(order_after[1], order_before[0])
            project.move_to_page(order_after[0], 3)
            self.assertEqual(project.placement(order_after[0]).board_page, 3)
            removed = order_after[0]
            project.remove_placement(removed)
            self.assertNotIn(removed, {p.id for p in project.placements})
            self.assertIn(merged.id, {p.id for p in project.panels})

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
            pages = {p.board_page for p in project.placements}
            self.assertEqual(pages, {0, 1})
            self.assertEqual(len(project.placements_on(0)), 4)
            ordered = project.reading_order()
            pairs = [(p.id, 0 if i < 3 else 1) for i, p in enumerate(ordered)]
            project.set_reading_order(pairs)
            self.assertEqual(len(project.placements_on(0)), 3)
            self.assertEqual(project.catalog_id(project.placement(ordered[0].id)), "p001-01")
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

    def test_catalog_ids_follow_the_board(self):
        from pencil_cleaner.studio_project import Placement

        project = Project()
        project.sources.append(
            SourcePage(id="page1", name="scan", image_name="page1.png", width=200, height=200, view_width=200, view_height=200)
        )
        first = Panel(id="a", source_id="page1", x0=0, y0=0, x1=40, y1=40)
        second = Panel(id="b", source_id="page1", x0=50, y0=0, x1=90, y1=40)
        project.panels = [first, second]
        use_a = Placement(id="useA", panel_id="a", board_page=0, order=0)
        use_b = Placement(id="useB", panel_id="b", board_page=0, order=1)
        project.placements = [use_a, use_b]
        self.assertEqual(project.catalog_id(use_a), "p001-01")
        self.assertEqual(project.catalog_id(use_b), "p001-02")
        project.move_by("useA", 1)
        self.assertEqual(project.catalog_id(project.placement("useA")), "p001-02")
        self.assertEqual(project.catalog_id(project.placement("useB")), "p001-01")
        project.move_to_page("useB", 2)
        self.assertEqual(project.catalog_id(project.placement("useB")), "p003-01")
        record = project.catalog_record(project.placement("useB"))
        self.assertEqual(record["page"], 3)
        self.assertEqual(record["panel"], 1)
        self.assertEqual(record["stable_id"], "b")

    def test_catalog_sidecar_roundtrip_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scan = root / "page.png"
            _two_panel_page(scan)
            project = Project(name="Catalog")
            project.import_files([scan])
            panel = project.panel(project.reading_order()[0].panel_id)
            project.set_meta(
                panel.id,
                tags=["character:ada", "location:dock", "pencilled"],
                notes="rain on the window",
                stage="pencil",
                ocr_text="DOCK GATE",
            )
            saved = project.save(root / "proj")
            sidecar = json.loads((saved / "catalog.json").read_text())
            self.assertEqual(sidecar["panels"][0]["id"], project.catalog_id(project.reading_order()[0]))
            self.assertEqual(sidecar["panels"][0]["stable_id"], panel.id)
            self.assertEqual(sidecar["panels"][0]["tags"], ["character:ada", "location:dock", "pencilled"])
            # The sidecar is what a reload keeps if project.json lost the text.
            stored = json.loads((saved / "project.json").read_text())
            for raw in stored["panels"]:
                raw["tags"] = []
                raw["ocr_text"] = ""
                raw["notes"] = ""
            (saved / "project.json").write_text(json.dumps(stored))
            loaded = Project.load(saved)
            restored = loaded.panel(panel.id)
            self.assertEqual(restored.tags, ["character:ada", "location:dock", "pencilled"])
            self.assertEqual(restored.notes, "rain on the window")
            self.assertEqual(restored.ocr_text, "DOCK GATE")
            self.assertEqual(restored.stage, "pencil")
            exported = loaded.export_catalog(root / "out")
            self.assertTrue(exported["json"].is_file())
            csv_text = exported["csv"].read_text()
            self.assertIn("id,page,panel,bbox", csv_text.splitlines()[0])
            self.assertIn(loaded.catalog_id(loaded.placements_of(restored.id)[0]), csv_text)
            self.assertIn("character:ada|location:dock|pencilled", csv_text)
            names = sorted(path.name for path in exported["images"])
            self.assertEqual(names, [f"{loaded.catalog_id(p)}.png" for p in loaded.reading_order()])

    def test_layout_reuses_a_clip_and_ink_is_another_take(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pencil = root / "pencil.png"
            ink = root / "ink.png"
            _two_panel_page(pencil)
            _two_panel_page(ink)
            project = Project()
            project.import_files([pencil])
            clip = project.panels[0]
            uses = len(project.placements)
            project.place(clip.id, 1)
            self.assertEqual(len(project.panels), 2)
            self.assertEqual(len(project.placements), uses + 1)
            project.set_rect(clip.id, 10, 10, 90, 90)
            self.assertEqual(project.panel(clip.id).box(), (10, 10, 90, 90))
            placement = project.placements_of(clip.id)[0]
            project.remove_placement(placement.id)
            self.assertIn(clip.id, {p.id for p in project.panels})
            self.assertEqual(len(project.placements_of(clip.id)), 1)
            ink_id = project.attach_ink(ink, project.sources[0].id)
            project.set_meta(clip.id, stage="ink")
            self.assertEqual(project.render_source(project.panel(clip.id)).id, ink_id)
            self.assertEqual(pencil.read_bytes(), project.image_path(project.sources[0].id).read_bytes())

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
