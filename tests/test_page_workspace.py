"""Explicit layout pages: reorder, resize, duplicate, delete, and per-page grids."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.studio_project import Project  # noqa: E402


def _page(path: Path) -> None:
    image = Image.new("L", (600, 400), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 270, 380), outline=0, width=4)
    draw.line((40, 60, 240, 200), fill=40, width=6)
    draw.rectangle((320, 20, 580, 380), outline=0, width=4)
    draw.line((350, 80, 540, 300), fill=30, width=6)
    image.save(path)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scan = self.root / "page.png"
        _page(self.scan)
        self.project = Project()
        self.project.import_files([self.scan])

    def tearDown(self):
        self.tmp.cleanup()

    def _put_on_page(self, placement_index: int, page: int) -> None:
        item = self.project.reading_order()[placement_index]
        self.project.move_to_page(item.id, page)

    def test_a_new_project_has_one_page_and_settings_roundtrip(self):
        self.assertEqual(self.project.page_count(), 1)
        self.project.set_page_settings(
            0, name="Cover", width_in=6.0, height_in=9.0, margin_in=0.5, gutter_in=0.1, columns=1, rows=2
        )
        self.assertEqual(self.project.page_name(0), "Cover")
        self.assertEqual(self.project.page_size(0), (6.0, 9.0))
        self.assertEqual(self.project.page_margin(0), 0.5)
        self.assertEqual(self.project.page_gutter(0), 0.1)
        self.assertEqual((self.project.page_columns(0), self.project.page_rows(0)), (1, 2))
        folder = self.project.save(self.root / "proj")
        self.assertEqual(json.loads((folder / "project.json").read_text())["version"], 3)
        again = Project.load(folder)
        self.assertEqual(again.page_name(0), "Cover")
        self.assertEqual(again.page_size(0), (6.0, 9.0))
        self.assertEqual((again.page_columns(0), again.page_rows(0)), (1, 2))

    def test_adding_a_page_shifts_placements_and_undo_restores_them(self):
        self._put_on_page(1, 1)
        self.assertEqual(sorted(p.board_page for p in self.project.placements), [0, 1])
        self.project.add_page(0)
        self.assertEqual(self.project.page_count(), 3)
        self.assertEqual(sorted(p.board_page for p in self.project.placements), [1, 2])
        self.assertTrue(self.project.undo())
        self.assertEqual(self.project.page_count(), 2)
        self.assertEqual(sorted(p.board_page for p in self.project.placements), [0, 1])

    def test_duplicate_page_copies_placements_not_clips(self):
        self._put_on_page(1, 1)
        panels = len(self.project.panels)
        uses = len(self.project.placements_on(0))
        self.project.duplicate_page(0)
        self.assertEqual(self.project.page_count(), 3)
        self.assertEqual(len(self.project.panels), panels)
        clones = self.project.placements_on(1)
        self.assertEqual(len(clones), uses)
        self.assertEqual({p.panel_id for p in clones}, {p.panel_id for p in self.project.placements_on(0)})
        self.assertNotEqual({p.id for p in clones}, {p.id for p in self.project.placements_on(0)})

    def test_delete_page_drops_its_placements_but_keeps_the_clips(self):
        self._put_on_page(1, 1)
        doomed = self.project.placements_on(1)[0]
        panels = len(self.project.panels)
        self.project.delete_page(1)
        self.assertEqual(self.project.page_count(), 1)
        self.assertNotIn(doomed.id, {p.id for p in self.project.placements})
        self.assertEqual(len(self.project.panels), panels)
        with self.assertRaises(ValueError):
            self.project.delete_page(0)

    def test_move_and_reorder_pages_keep_each_placement_with_its_page(self):
        self._put_on_page(1, 1)
        first_id = self.project.page(0).id
        second_id = self.project.page(1).id
        page_of_first = self.project.placements_on(0)[0].id
        page_of_second = self.project.placements_on(1)[0].id
        self.project.move_page(0, 1)
        self.assertEqual([self.project.page(0).id, self.project.page(1).id], [second_id, first_id])
        self.assertEqual(self.project.placement(page_of_first).board_page, 1)
        self.assertEqual(self.project.placement(page_of_second).board_page, 0)
        self.project.reorder_pages([first_id, second_id])
        self.assertEqual([self.project.page(0).id, self.project.page(1).id], [first_id, second_id])
        self.assertEqual(self.project.placement(page_of_first).board_page, 0)
        self.assertEqual(self.project.placement(page_of_second).board_page, 1)

    def test_reorder_rejects_a_partial_list_without_changing_anything(self):
        ids = [self.project.page(0).id]
        before = self.project.state_signature()
        with self.assertRaises(ValueError):
            self.project.reorder_pages([])
        self.assertEqual(self.project.state_signature(), before)
        with self.assertRaises(ValueError):
            self.project.reorder_pages(ids + ["missing"])
        self.assertEqual(self.project.state_signature(), before)

    def test_per_page_size_changes_the_composed_page_only(self):
        self.project.dpi = 100
        original = self.scan.read_bytes()
        self.project.set_page_settings(0, width_in=6.0, height_in=9.0, margin_in=0.5, columns=1, rows=1)
        self.project.add_page()
        self.project.set_page_settings(1, width_in=8.5, height_in=11.0, columns=2, rows=3)
        small = self.project._compose_page(
            self.project.placements_on(0), 1.0, packed=False, finished=True, page_index=0
        )
        letter = self.project._compose_page([], 1.0, packed=False, finished=True, page_index=1)
        self.assertEqual(small.size, (600, 900))
        self.assertEqual(letter.size, (850, 1100))
        self.assertEqual(self.scan.read_bytes(), original, "resizing a page must not touch the scan")

    def test_pack_grid_uses_each_page_grid_and_adds_pages_as_needed(self):
        self.project.set_page_settings(0, columns=1, rows=1)
        panel_id = self.project.panels[0].id
        while len(self.project.placements) < 3:
            self.project.place(panel_id, 0)
        self.project.pack_grid()
        self.assertEqual(len(self.project.placements_on(0)), 1)
        self.assertGreaterEqual(self.project.page_count(), 2)
        self.assertEqual(len(self.project.placements_on(1)), 2)
        self.assertEqual(self.project.placement(self.project.placements_on(0)[0].id).order, 0)

    def test_overflow_is_reported(self):
        panel_id = self.project.panels[0].id
        while len(self.project.placements) < 2:
            self.project.place(panel_id, 0)
        self.project.set_page_settings(0, columns=1, rows=1)
        self.assertEqual(self.project.overflow_pages(), [(0, 2, 1)])

    def test_page_settings_are_validated_before_anything_changes(self):
        before = self.project.state_signature()
        for bad in (
            {"width_in": 1.0},
            {"height_in": 50.0},
            {"margin_in": 10.0},
            {"gutter_in": -1.0},
            {"columns": 0},
            {"rows": 20},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.project.set_page_settings(0, **bad)
        self.assertEqual(self.project.state_signature(), before)

    def test_page_operations_are_undoable(self):
        self.project.add_page()
        self.project.duplicate_page(0)
        self._put_on_page(0, 2)
        for _ in range(3):
            self.assertTrue(self.project.undo())
        self.assertEqual(self.project.page_count(), 1)
        self.assertEqual(sorted(p.board_page for p in self.project.placements), [0, 0])

    def test_thumbnail_is_a_small_preview(self):
        thumb = self.project.page_thumbnail(0)
        self.assertLessEqual(max(thumb.size), 150)
        self.assertGreater(min(thumb.size), 0)


if __name__ == "__main__":
    unittest.main()
