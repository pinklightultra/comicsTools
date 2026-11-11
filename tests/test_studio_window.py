"""The desktop window protects work: unsaved changes are tracked, closing asks first,
autosave only writes to a folder the user chose, and recent projects reopen.
Runs offscreen. Skipped when PySide6 is not installed."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pencil_cleaner"))

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - CI without the gui extra
    HAVE_QT = False


def _page(path: Path) -> None:
    image = Image.new("L", (600, 400), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 270, 380), outline=0, width=4)
    draw.line((40, 60, 240, 200), fill=40, width=6)
    draw.rectangle((320, 20, 580, 380), outline=0, width=4)
    draw.line((350, 80, 540, 300), fill=30, width=6)
    image.save(path)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestStudioWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir.name)

    def setUp(self):
        from pencil_cleaner.studio import StudioWindow

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scan = self.root / "page.png"
        _page(self.scan)
        self.window = StudioWindow()
        self.window.settings.clear()

    def tearDown(self):
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Discard):
            self.window.close()
        self.tmp.cleanup()

    def _import(self):
        self.window._import_paths([str(self.scan)])
        end = time.time() + 30
        while time.time() < end:
            self.app.processEvents()
            if self.window._thread is not None and not self.window._thread.isRunning():
                self.app.processEvents()
                return
            time.sleep(0.02)
        self.fail("import did not finish")

    def test_import_marks_the_project_unsaved_and_save_clears_it(self):
        self.assertFalse(self.window._is_dirty())
        self._import()
        self.assertTrue(self.window._is_dirty())
        self.window._update_title()
        self.assertTrue(self.window.isWindowModified())
        self.assertTrue(self.window._save_to(str(self.root / "proj")))
        self.assertFalse(self.window._is_dirty())
        self.assertIn("proj", self.window.windowTitle())

    def test_close_with_unsaved_changes_asks(self):
        self._import()
        event = QCloseEvent()
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel) as ask:
            self.window.closeEvent(event)
        ask.assert_called_once()
        self.assertFalse(event.isAccepted(), "Cancel must keep the window open")
        event = QCloseEvent()
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Discard):
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_close_with_nothing_to_save_does_not_ask(self):
        event = QCloseEvent()
        with mock.patch.object(QMessageBox, "warning") as ask:
            self.window.closeEvent(event)
        ask.assert_not_called()
        self.assertTrue(event.isAccepted())

    def test_autosave_writes_only_to_a_chosen_folder(self):
        self._import()
        self.window._autosave()
        self.assertTrue(self.window._is_dirty(), "a temp-folder project must not autosave")
        folder = self.root / "proj"
        self.window._save_to(str(folder))
        self.window.project.set_meta(self.window.project.panels[0].id, notes="night scene")
        self.assertTrue(self.window._is_dirty())
        self.window._autosave()
        self.assertFalse(self.window._is_dirty())
        self.assertIn("night scene", (folder / "project.json").read_text())

    def test_recent_projects_reopen(self):
        self._import()
        folder = self.root / "proj"
        self.window._save_to(str(folder))
        self.assertEqual(self.window._recent()[0], str(folder.resolve()))
        from pencil_cleaner.studio import StudioWindow

        other = StudioWindow()
        try:
            self.assertTrue(other._open_folder(str(folder)))
            self.assertEqual(len(other.project.panels), 2)
            self.assertFalse(other._is_dirty())
        finally:
            other.close()

    def test_a_missing_recent_project_is_dropped(self):
        gone = self.root / "gone"
        self.window._add_recent(str(gone))
        self.assertFalse(self.window._open_folder(str(gone)))
        self.assertNotIn(str(gone.resolve()), self.window._recent())

    def test_undo_refreshes_scans_and_settings(self):
        self._import()
        ink = self.root / "ink.png"
        _page(ink)
        self.window.project.attach_ink(ink, self.window.project.sources[0].id)
        self.window._fill_sources(self.window._source_id)
        self.assertEqual(self.window.source_list.count(), 2)
        self.window._undo()
        self.assertEqual(self.window.source_list.count(), 1, "the undone take should leave the scan list")
        index = next(i for i in range(self.window.layout_box.count()) if self.window.layout_box.itemData(i) == (3, 3))
        self.window.layout_box.setCurrentIndex(index)
        self.assertEqual((self.window.project.columns, self.window.project.rows), (3, 3))
        self.window._undo()
        self.assertEqual(self.window.layout_box.currentData(), (2, 3))

    def test_page_strip_tracks_add_move_duplicate_and_delete(self):
        self._import()
        self.assertEqual(self.window.page_list.count(), self.window.project.page_count())
        self.window.page_list.setCurrentRow(0)
        self.window._add_page()
        self.assertEqual(self.window.page_list.count(), 2)
        first = self.window.project.page(0).id
        self.window.page_list.setCurrentRow(0)
        self.window._move_page(1)
        self.assertEqual(self.window.project.page(1).id, first)
        self.window._duplicate_page()
        self.assertEqual(self.window.page_list.count(), 3)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window._delete_page()
        self.assertEqual(self.window.page_list.count(), 2)
        self.assertEqual(self.window.page_list.count(), self.window.project.page_count())

    def test_page_settings_dialog_shows_the_selected_page(self):
        from pencil_cleaner.studio import PageSettingsDialog

        self.window.project.set_page_settings(
            0, name="Cover", width_in=6.0, height_in=9.0, margin_in=0.5, columns=1, rows=2
        )
        dialog = PageSettingsDialog(self.window.project, 0, self.window)
        try:
            self.assertEqual(dialog.name_edit.text(), "Cover")
            self.assertAlmostEqual(dialog.width.value(), 6.0)
            self.assertAlmostEqual(dialog.height.value(), 9.0)
            self.assertEqual(dialog.columns.value(), 1)
            self.assertEqual(dialog.rows.value(), 2)
            self.assertEqual(dialog.orientation.currentText(), "Portrait")
        finally:
            dialog.close()

    def test_every_menu_has_its_shortcut(self):
        keys = {}
        for menu_action in self.window.menuBar().actions():
            for action in menu_action.menu().actions():
                for key in action.shortcuts():
                    keys.setdefault(key.toString(), []).append(action.text())
        clashes = {k: v for k, v in keys.items() if len(v) > 1}
        self.assertEqual(clashes, {}, f"two actions share a shortcut: {clashes}")
        for needed in ("Ctrl+S", "Ctrl+O", "Ctrl+Z", "Ctrl+I", "Ctrl+E"):
            self.assertIn(needed, keys)


if __name__ == "__main__":
    unittest.main()
