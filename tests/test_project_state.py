"""Undo covers the whole project, a refused edit leaves no trace, and ids are never reused.

Each case is a fault an outside review reproduced on 11-11 against the GitHub branch."""

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

from pencil_cleaner.studio_project import Project  # noqa: E402


def _page(path: Path, dark: int = 40) -> None:
    image = Image.new("L", (600, 400), 245)
    draw = ImageDraw.Draw(image)
    for x0 in (20, 320):
        draw.rectangle((x0, 20, x0 + 260, 380), outline=0, width=4)
        draw.line((x0 + 30, 60, x0 + 220, 330), fill=dark, width=6)
        draw.line((x0 + 220, 60, x0 + 40, 300), fill=dark, width=5)
    image.save(path)


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scan = self.root / "pencil.png"
        _page(self.scan)
        self.project = Project()
        self.project.import_files([self.scan])

    def tearDown(self):
        self.tmp.cleanup()

    def test_undo_attach_ink_removes_the_take_and_redo_brings_it_back(self):
        ink = self.root / "ink.png"
        _page(ink, dark=0)
        pencil = self.project.sources[0].id
        self.project.attach_ink(ink, pencil)
        self.assertEqual(len(self.project.sources), 2)
        self.assertTrue(self.project.undo())
        self.assertEqual(len(self.project.sources), 1)
        self.assertEqual(self.project.source(pencil).linked_id, "")
        self.assertTrue(self.project.redo())
        self.assertEqual(len(self.project.sources), 2)
        self.assertTrue(self.project.source(pencil).linked_id)

    def test_a_refused_merge_leaves_history_and_redo_alone(self):
        other = self.root / "other.png"
        _page(other)
        self.project.import_files([other])
        a = next(p for p in self.project.panels if p.source_id == self.project.sources[0].id)
        b = next(p for p in self.project.panels if p.source_id == self.project.sources[1].id)
        self.project.set_meta(a.id, notes="first")
        self.project.undo()
        steps, redo = len(self.project._undo), len(self.project._redo)
        before = self.project.state_signature()
        with self.assertRaises(ValueError):
            self.project.merge_panels(a.id, b.id)
        self.assertEqual(self.project.state_signature(), before)
        self.assertEqual((len(self.project._undo), len(self.project._redo)), (steps, redo))

    def test_undo_layout_restores_the_grid_and_the_placements(self):
        before = (self.project.columns, self.project.rows)
        orders = sorted((p.board_page, p.order) for p in self.project.placements)
        self.project.set_settings(columns=3, rows=3)
        self.assertEqual((self.project.columns, self.project.rows), (3, 3))
        self.project.undo()
        self.assertEqual((self.project.columns, self.project.rows), before)
        self.assertEqual(sorted((p.board_page, p.order) for p in self.project.placements), orders)

    def test_metadata_edits_are_their_own_undo_step(self):
        pid = self.project.panels[0].id
        self.project.set_meta(pid, tags=["rain"], notes="night")
        self.project.set_meta(pid, tags=["rain", "roof"])
        self.project.undo()
        self.assertEqual(self.project.panel(pid).tags, ["rain"])
        self.assertEqual(self.project.panel(pid).notes, "night")
        self.project.undo()
        self.assertEqual(self.project.panel(pid).tags, [])

    def test_an_edit_that_changes_nothing_records_nothing(self):
        steps = len(self.project._undo)
        self.project.set_settings(opacity=self.project.opacity)
        self.assertEqual(len(self.project._undo), steps)

    def test_bad_settings_are_refused_before_anything_changes(self):
        before = self.project.state_signature()
        for bad in ({"opacity": 2.0}, {"opacity": "neon"}, {"columns": 7}, {"bleed": -5}, {"dpi": 10}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.project.set_settings(**bad)
        self.assertEqual(self.project.state_signature(), before)

    def test_ids_are_not_reused_after_a_delete(self):
        first = self.project.panels[0].id
        self.project.delete_panel(first)
        added = self.project.add_panel(self.project.sources[0].id, 10, 10, 100, 100)
        self.assertNotEqual(added.id, first)

    def test_ids_are_not_reused_after_save_and_load(self):
        first = self.project.panels[0].id
        self.project.delete_panel(first)
        folder = self.project.save(self.root / "proj")
        again = Project.load(folder)
        self.assertEqual(again._undo, [], "opening a project starts with no history")
        added = again.add_panel(again.sources[0].id, 10, 10, 100, 100)
        self.assertNotEqual(added.id, first)

    def test_old_projects_without_a_counter_still_get_fresh_ids(self):
        folder = self.project.save(self.root / "proj")
        path = folder / "project.json"
        import json

        data = json.loads(path.read_text())
        data.pop("next_id")
        path.write_text(json.dumps(data))
        again = Project.load(folder)
        used = {p.id for p in again.panels} | {p.id for p in again.placements} | {s.id for s in again.sources}
        added = again.add_panel(again.sources[0].id, 10, 10, 100, 100)
        self.assertNotIn(added.id, used)


if __name__ == "__main__":
    unittest.main()
