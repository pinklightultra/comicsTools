"""One window for a pencil page: import, crop, rearrange, ghost PDF."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QMimeData, QSize, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .studio_project import LAYOUTS, Project

SCAN_FILTER = "Scans (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp *.pdf);;All files (*)"


def _pixmap_from_gray(gray) -> QPixmap:
    arr = np.ascontiguousarray(np.clip(gray, 0, 255).astype(np.uint8))
    image = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1], QImage.Format.Format_Grayscale8)
    return QPixmap.fromImage(image.copy())


class _Call(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class PageCanvas(QWidget):
    """Source page with panel rectangles in source pixels."""

    changed = Signal(str, int, int, int, int)
    drawn = Signal(int, int, int, int)
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 360)
        self.setMouseTracking(True)
        self._pixmap: QPixmap | None = None
        self._page_w = 0
        self._page_h = 0
        self._panels: list = []
        self._numbers: dict[str, int] = {}
        self._bleed = 12
        self.selected_id: str | None = None
        self.also_id: str | None = None
        self.add_mode = False
        self._drag = None
        self._override: dict = {}

    def set_page(self, path: Path | None, panels, numbers, bleed, page_w, page_h, gray=None) -> None:
        self._panels = list(panels)
        self._numbers = dict(numbers)
        self._bleed = int(bleed)
        self._page_w = int(page_w)
        self._page_h = int(page_h)
        self._override = {}
        if gray is not None:
            self._pixmap = _pixmap_from_gray(gray)
        elif path and Path(path).is_file():
            self._pixmap = QPixmap(str(path))
            if self._pixmap.isNull():
                self._pixmap = None
        else:
            self._pixmap = None
        self._drag = None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor("#f4f1ea"))
        target, scale = self._view()
        if self._pixmap is None or scale <= 0:
            painter.setPen(QColor("#666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Import pencil scans or a PDF.")
            return
        painter.drawPixmap(target, self._pixmap)
        for panel in self._panels:
            box = self._box_of(panel)
            screen = self._to_screen(box, target, scale)
            bleed = self._to_screen(self._bleed_box(box), target, scale)
            selected = panel.id in (self.selected_id, self.also_id)
            if self._bleed:
                painter.setPen(QPen(QColor("#dd6b20"), 1, Qt.PenStyle.DashLine))
                painter.drawRect(bleed)
            painter.setPen(QPen(QColor("#c05621" if selected else "#2b6cb0"), 2))
            painter.drawRect(screen)
            number = self._numbers.get(panel.id)
            if number:
                text = str(number)
                width = painter.fontMetrics().horizontalAdvance(text) + 8
                painter.fillRect(screen.x(), screen.y(), width, 16, QColor("#2b6cb0"))
                painter.setPen(QColor("white"))
                painter.drawText(screen.x() + 4, screen.y() + 12, text)
            if panel.id == self.selected_id:
                for handle in self._handles(screen).values():
                    painter.fillRect(handle, QColor("white"))
                    painter.setPen(QColor("#1a202c"))
                    painter.drawRect(handle)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._to_image(event.position().x(), event.position().y())
        if point is None:
            return
        if self.add_mode:
            self._drag = ("draw", point, point)
            return
        hit, handle = self._hit(event.position().x(), event.position().y())
        if hit is None:
            self.selected_id = None
            self.also_id = None
            self.update()
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.also_id = hit
        else:
            self.selected_id = hit
            self.also_id = None
        panel = self._by_id(hit)
        if handle:
            self._drag = ("resize", hit, handle, panel.box())
        else:
            self._drag = ("move", hit, point, panel.box())
        self.selected.emit(hit)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._drag:
            return
        point = self._to_image(event.position().x(), event.position().y())
        if point is None:
            return
        kind = self._drag[0]
        if kind == "draw":
            self._drag = ("draw", self._drag[1], point)
        elif kind == "move":
            _kind, pid, start, (x0, y0, x1, y1) = self._drag
            dx, dy = point[0] - start[0], point[1] - start[1]
            self._override[pid] = self._clamped(x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:
            _kind, pid, handle, (x0, y0, x1, y1) = self._drag
            nx0, ny0, nx1, ny1 = x0, y0, x1, y1
            if "w" in handle:
                nx0 = point[0]
            if "e" in handle:
                nx1 = point[0]
            if "n" in handle:
                ny0 = point[1]
            if "s" in handle:
                ny1 = point[1]
            self._override[pid] = self._clamped(nx0, ny0, nx1, ny1)
        self.update()

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        if not self._drag:
            return
        kind = self._drag[0]
        if kind == "draw":
            _kind, start, end = self._drag
            x0, x1 = sorted((start[0], end[0]))
            y0, y1 = sorted((start[1], end[1]))
            self._drag = None
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                self.drawn.emit(int(x0), int(y0), int(x1), int(y1))
            self.update()
            return
        pid = self._drag[1]
        box = self._override.get(pid)
        self._drag = None
        self._override = {}
        if box:
            self.changed.emit(pid, *box)
        else:
            self.changed.emit(pid, 0, 0, 0, 0)

    def _clamped(self, x0, y0, x1, y1):
        from .studio_project import clamp_box

        return clamp_box(x0, y0, x1, y1, self._page_w, self._page_h)

    def _by_id(self, pid):
        for panel in self._panels:
            if panel.id == pid:
                return panel
        raise KeyError(pid)

    def _box_of(self, panel):
        return self._override.get(panel.id, (panel.x0, panel.y0, panel.x1, panel.y1))

    def _bleed_box(self, box):
        x0, y0, x1, y1 = box
        return (
            max(0, x0 - self._bleed),
            max(0, y0 - self._bleed),
            min(self._page_w, x1 + self._bleed),
            min(self._page_h, y1 + self._bleed),
        )

    def _view(self):
        if self._pixmap is None or self._page_w <= 0:
            return self.rect(), 0
        avail = self.rect().adjusted(8, 8, -8, -8)
        scale = min(avail.width() / self._page_w, avail.height() / self._page_h)
        width = max(1, int(self._page_w * scale))
        height = max(1, int(self._page_h * scale))
        x = avail.x() + (avail.width() - width) // 2
        y = avail.y() + (avail.height() - height) // 2
        from PySide6.QtCore import QRect

        return QRect(x, y, width, height), scale

    def _to_screen(self, box, target, scale):
        from PySide6.QtCore import QRect

        x0, y0, x1, y1 = box
        return QRect(
            int(target.x() + x0 * scale),
            int(target.y() + y0 * scale),
            max(1, int((x1 - x0) * scale)),
            max(1, int((y1 - y0) * scale)),
        )

    def _to_image(self, sx, sy):
        target, scale = self._view()
        if scale <= 0 or not target.contains(int(sx), int(sy)):
            return None
        return ((sx - target.x()) / scale, (sy - target.y()) / scale)

    def _handles(self, screen):
        from PySide6.QtCore import QRect

        size = 8
        points = {
            "nw": (screen.left(), screen.top()),
            "n": (screen.center().x(), screen.top()),
            "ne": (screen.right(), screen.top()),
            "e": (screen.right(), screen.center().y()),
            "se": (screen.right(), screen.bottom()),
            "s": (screen.center().x(), screen.bottom()),
            "sw": (screen.left(), screen.bottom()),
            "w": (screen.left(), screen.center().y()),
        }
        rects = {}
        for name, (x, y) in points.items():
            rects[name] = QRect(int(x - size / 2), int(y - size / 2), size, size)
        return rects

    def _hit(self, sx, sy):
        from PySide6.QtCore import QPoint

        target, scale = self._view()
        if self.selected_id:
            panel = self._by_id(self.selected_id)
            screen = self._to_screen(panel.box(), target, scale)
            for name, handle in self._handles(screen).items():
                if handle.contains(QPoint(int(sx), int(sy))):
                    return self.selected_id, name
        point = self._to_image(sx, sy)
        if point is None:
            return None, None
        x, y = point
        for panel in reversed(self._panels):
            if panel.x0 <= x < panel.x1 and panel.y0 <= y < panel.y1:
                return panel.id, None
        return None, None


class BoardList(QListWidget):
    changed = Signal()
    panel_dropped = Signal(int, str)

    def __init__(self, page_index: int):
        super().__init__()
        self.page_index = page_index
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setIconSize(QSize(120, 90))
        self.setGridSize(QSize(140, 124))
        self.setMinimumHeight(150)
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-comics-panel"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-comics-panel"):
            panel_id = bytes(event.mimeData().data("application/x-comics-panel")).decode()
            self.panel_dropped.emit(self.page_index, panel_id)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self.changed.emit()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-comics-panel"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)


class BinList(QListWidget):
    """Library thumbnails. Dragging one copies a reference onto a layout page."""

    def startDrag(self, _actions) -> None:  # noqa: N802
        item = self.currentItem()
        panel_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not panel_id:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-comics-panel", str(panel_id).encode())
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("comicsTools")
        self.resize(1180, 780)
        self.setAcceptDrops(True)
        self.project = Project()
        self._source_id: str | None = None
        self._placement_id: str | None = None
        self._thread: _Call | None = None
        self._boards: list[tuple[int, BoardList]] = []
        self._syncing = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel("comicsTools")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        controls = QHBoxLayout()
        self.import_btn = self._button("Import scans", self._import)
        self.open_btn = self._button("Open", self._open)
        self.save_btn = self._button("Save", self._save)
        self.export_btn = self._button("Export ghost PDF", self._export)
        self.catalog_btn = self._button("Export catalog", self._export_catalog)
        self.page_png_btn = self._button("Export page PNG", self._export_page_png)
        controls.addWidget(self.import_btn)
        controls.addWidget(self.open_btn)
        controls.addWidget(self.save_btn)
        controls.addWidget(self.export_btn)
        controls.addWidget(self.catalog_btn)
        controls.addWidget(self.page_png_btn)
        controls.addWidget(QLabel("Opacity"))
        self.opacity = QComboBox()
        self.opacity.addItems(["light", "medium", "dark"])
        self.opacity.currentTextChanged.connect(self._set_opacity)
        controls.addWidget(self.opacity)
        controls.addWidget(QLabel("Layout"))
        self.layout_box = QComboBox()
        for cols, rows in LAYOUTS:
            self.layout_box.addItem(f"{cols}×{rows}", (cols, rows))
        self.layout_box.setCurrentIndex(1)
        self.layout_box.currentIndexChanged.connect(self._set_layout)
        controls.addWidget(self.layout_box)
        controls.addWidget(QLabel("Bleed"))
        self.bleed = QSpinBox()
        self.bleed.setRange(0, 64)
        self.bleed.setValue(12)
        self.bleed.valueChanged.connect(self._set_bleed)
        controls.addWidget(self.bleed)
        controls.addStretch(1)
        layout.addLayout(controls)

        edits = QHBoxLayout()
        for label, slot in (
            ("Detect", self._detect),
            ("Add panel", self._add_mode),
            ("Split", self._split),
            ("Merge", self._merge),
            ("Duplicate", self._duplicate),
            ("Remove from page", self._delete),
            ("Delete clip", self._delete_clip),
            ("Undo", self._undo),
            ("Redo", self._redo),
            ("Link ink", self._link_ink),
            ("Earlier", lambda: self._nudge(-1)),
            ("Later", lambda: self._nudge(1)),
            ("Previous page", lambda: self._shift_page(-1)),
            ("Next page", lambda: self._shift_page(1)),
        ):
            edits.addWidget(self._button(label, slot))
        edits.addStretch(1)
        layout.addLayout(edits)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress)
        self.status = QLabel("Drop scans here, or use Import.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Scans"))
        self.source_list = QListWidget()
        self.source_list.currentRowChanged.connect(self._source_changed)
        self.source_list.setMaximumHeight(110)
        left_layout.addWidget(self.source_list)
        self.canvas = PageCanvas()
        self.canvas.changed.connect(self._panel_edited)
        self.canvas.drawn.connect(self._panel_drawn)
        self.canvas.selected.connect(self._panel_picked)
        left_layout.addWidget(self.canvas, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        self.tag_filter = QLineEdit()
        self.tag_filter.setPlaceholderText("Filter by tag")
        self.tag_filter.textChanged.connect(self._refresh_catalog)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search lettering and notes")
        self.search.textChanged.connect(self._refresh_catalog)
        filter_row.addWidget(self.tag_filter)
        filter_row.addWidget(self.search)
        right_layout.addLayout(filter_row)
        self.catalog_tree = QTreeWidget()
        self.catalog_tree.setHeaderHidden(True)
        self.catalog_tree.setMaximumHeight(160)
        self.catalog_tree.itemSelectionChanged.connect(self._catalog_selected)
        right_layout.addWidget(QLabel("Library. Drag a clip onto a page."))
        self.bin = BinList()
        self.bin.setViewMode(QListWidget.ViewMode.IconMode)
        self.bin.setIconSize(QSize(72, 54))
        self.bin.setGridSize(QSize(96, 78))
        self.bin.setFixedHeight(96)
        self.bin.setMovement(QListWidget.Movement.Static)
        self.bin.setDragEnabled(True)
        self.bin.itemSelectionChanged.connect(self._bin_selected)
        right_layout.addWidget(self.bin)
        right_layout.addWidget(self.catalog_tree)
        meta = QHBoxLayout()
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("Tags, comma separated")
        self.tags_edit.editingFinished.connect(self._save_meta)
        self.stage_box = QComboBox()
        self.stage_box.addItems(["pencil", "ink"])
        self.stage_box.currentTextChanged.connect(self._save_meta)
        meta.addWidget(self.tags_edit, 1)
        meta.addWidget(self.stage_box)
        right_layout.addLayout(meta)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Notes")
        self.notes_edit.setFixedHeight(36)
        self.notes_edit.textChanged.connect(self._save_meta)
        right_layout.addWidget(self.notes_edit)
        ocr_row = QHBoxLayout()
        self.ocr_edit = QPlainTextEdit()
        self.ocr_edit.setPlaceholderText("Lettering. Read it, or type it.")
        self.ocr_edit.setFixedHeight(48)
        self.ocr_edit.textChanged.connect(self._save_meta)
        ocr_row.addWidget(self.ocr_edit, 1)
        ocr_row.addWidget(self._button("Read lettering", self._read_lettering))
        right_layout.addLayout(ocr_row)
        right_layout.addWidget(QLabel("Panel board. Drag to reorder or move between pages."))
        self._loading_meta = False
        self.board_host = QWidget()
        self.board_layout = QVBoxLayout(self.board_host)
        self.board_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.board_host)
        right_layout.addWidget(scroll, 1)
        splitter.addWidget(right)
        splitter.setSizes([640, 500])
        layout.addWidget(splitter, 1)

    def _button(self, label, slot) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(slot)
        return button

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self._import_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import scans", "", SCAN_FILTER)
        if paths:
            self._import_paths(paths)

    def _import_paths(self, paths) -> None:
        files = list(paths)

        def work():
            return self.project.import_files(files)

        self._run(work, self._after_import, "Importing…")

    def _after_import(self, ids) -> None:
        self._fill_sources(select=ids[0] if ids else None)
        count = len(self.project.panels)
        self.status.setText(f"Imported {len(ids)} page(s). {count} panel(s) detected.")

    def _open(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open project folder")
        if not folder:
            return
        try:
            self.project = Project.load(folder)
        except Exception as exc:
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        self.opacity.setCurrentText(self.project.opacity if self.project.opacity in ("light", "medium", "dark") else "light")
        self.bleed.setValue(self.project.bleed)
        self._select_layout(self.project.columns, self.project.rows)
        self._fill_sources(select=self.project.sources[0].id if self.project.sources else None)
        self.status.setText(f"Opened {folder}")

    def _save(self) -> None:
        dest = None
        if self.project.root is None or self.project.root.name.startswith("comics-tools-"):
            folder = QFileDialog.getExistingDirectory(self, "Project folder")
            if not folder:
                return
            dest = folder
        try:
            path = self.project.save(dest)
        except Exception as exc:
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        self.status.setText(f"Saved {path}")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ghost PDF", "ghost.pdf", "PDF (*.pdf)")
        if not path:
            return
        selected = self._selected_board_ids()
        panel_ids = selected or None

        def work():
            return str(self.project.write_ghost_pdf(path, panel_ids=panel_ids))

        self._run(work, lambda dest: self.status.setText(f"Wrote {dest}"), "Writing the ghost PDF…")

    def _detect(self) -> None:
        if not self._source_id:
            self.status.setText("Import a scan first.")
            return
        source_id = self._source_id

        def work():
            return len(self.project.detect_panels(source_id))

        self._run(work, lambda n: self._after_detect(n), "Finding panels…")

    def _after_detect(self, count) -> None:
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText(f"Found {count} panel(s) on this scan.")

    def _add_mode(self) -> None:
        if not self._source_id:
            self.status.setText("Import a scan first.")
            return
        self.canvas.add_mode = True
        self.status.setText("Drag a rectangle on the scan. Bleed stays outside that box.")

    def _panel_drawn(self, x0, y0, x1, y1) -> None:
        self.canvas.add_mode = False
        if not self._source_id:
            return
        panel = self.project.add_panel(self._source_id, x0, y0, x1, y1)
        self.canvas.selected_id = panel.id
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText(f"Added clip {panel.id} to the library and the page.")
        self._load_meta(panel.id)

    def _panel_edited(self, panel_id, x0, y0, x1, y1) -> None:
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        panel = self.project.set_rect(panel_id, x0, y0, x1, y1)
        self._show_source(self._source_id)
        self._refresh_board()
        uses = len(self.project.placements_of(panel_id))
        self.status.setText(
            f"Crop is {panel.width()}×{panel.height()} source pixels. "
            f"Updated {uses} layout use(s). The scan file was not changed."
        )

    def _panel_picked(self, panel_id) -> None:
        self._select_board_item(panel_id)
        self._load_meta(panel_id)
        self._refresh_catalog()

    def _split(self) -> None:
        pid = self.canvas.selected_id
        if not pid:
            self.status.setText("Select a panel to split.")
            return
        try:
            self.project.split_panel(pid)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Split the panel.")

    def _merge(self) -> None:
        first, second = self.canvas.selected_id, self.canvas.also_id
        if not first or not second:
            self.status.setText("Select a panel, then Shift-click a second panel on the same scan.")
            return
        try:
            merged = self.project.merge_panels(first, second)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.canvas.selected_id = merged.id
        self.canvas.also_id = None
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Merged the two panels.")

    def _duplicate(self) -> None:
        pid = self.canvas.selected_id
        if not pid:
            self.status.setText("Select a panel to duplicate.")
            return
        placed = self.project.duplicate_panel(pid)
        self._placement_id = placed.id
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText(f"Placed {pid} again as {self.project.catalog_id(placed)}. The scan was not copied.")
        self._load_meta(pid)

    def _delete(self) -> None:
        placement_id = self._current_placement()
        if not placement_id:
            self.status.setText("Select a panel on a page to remove it from the layout.")
            return
        self.project.remove_placement(placement_id)
        self._placement_id = None
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Removed from the page. The clip is still in the library.")

    def _delete_clip(self) -> None:
        pid = self.canvas.selected_id
        if not pid:
            self.status.setText("Select a clip to delete from the library.")
            return
        self.project.delete_panel(pid)
        self.canvas.selected_id = None
        self.canvas.also_id = None
        self._placement_id = None
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Deleted the clip and every place it was used.")

    def _undo(self) -> None:
        if not self.project.undo():
            self.status.setText("Nothing to undo.")
            return
        self._arrays = {}
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Undid the last layout or crop edit.")

    def _redo(self) -> None:
        if not self.project.redo():
            self.status.setText("Nothing to redo.")
            return
        self._arrays = {}
        self._show_source(self._source_id)
        self._refresh_board()
        self.status.setText("Redid the edit.")

    def _link_ink(self) -> None:
        if not self._source_id:
            self.status.setText("Select the pencil scan first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Ink scan of this page", "", SCAN_FILTER)
        if not path:
            return
        try:
            self.project.attach_ink(path, self._source_id)
        except Exception as exc:
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        self._fill_sources(self._source_id)
        self.status.setText("Linked the ink scan as another take. Set the stage to ink to flip the clip.")

    def _current_placement(self) -> str | None:
        if self._placement_id and any(item.id == self._placement_id for item in self.project.placements):
            return self._placement_id
        panel_id = self.canvas.selected_id
        if not panel_id:
            return self._one_board_selection()
        uses = self.project.placements_of(panel_id)
        return uses[0].id if uses else None

    def _nudge(self, steps: int) -> None:
        placement_id = self._current_placement()
        if not placement_id:
            self.status.setText("Select a panel on a page to move it.")
            return
        self.project.move_by(placement_id, steps)
        self._placement_id = placement_id
        self._show_source(self._source_id)
        self._refresh_board()
        self._select_board_item(placement_id)
        self.status.setText(f"Now {self.project.catalog_id(self.project.placement(placement_id))}.")
        self._load_meta(self.project.placement(placement_id).panel_id)

    def _shift_page(self, steps: int) -> None:
        placement_id = self._current_placement()
        if not placement_id:
            self.status.setText("Select a panel on a page to move it.")
            return
        item = self.project.placement(placement_id)
        self.project.move_to_page(placement_id, item.board_page + steps)
        self._placement_id = placement_id
        self._show_source(self._source_id)
        self._refresh_board()
        self._select_board_item(placement_id)
        self.status.setText(f"Now {self.project.catalog_id(self.project.placement(placement_id))}.")
        self._load_meta(self.project.placement(placement_id).panel_id)

    def _set_opacity(self, name: str) -> None:
        self.project.opacity = name

    def _set_bleed(self, value: int) -> None:
        self.project.bleed = int(value)
        if self._source_id:
            self._show_source(self._source_id)

    def _set_layout(self) -> None:
        cols, rows = self.layout_box.currentData()
        self.project.columns = int(cols)
        self.project.rows = int(rows)
        if self.project.placements:
            self.project.pack_grid()
            self._show_source(self._source_id)
            self._refresh_board()

    def _select_layout(self, cols, rows) -> None:
        self.layout_box.blockSignals(True)
        for index in range(self.layout_box.count()):
            if self.layout_box.itemData(index) == (cols, rows):
                self.layout_box.setCurrentIndex(index)
                break
        self.layout_box.blockSignals(False)

    def _fill_sources(self, select: str | None) -> None:
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for source in self.project.sources:
            item = QListWidgetItem(f"{source.name}  ({source.width}×{source.height})")
            item.setData(Qt.ItemDataRole.UserRole, source.id)
            self.source_list.addItem(item)
        self.source_list.blockSignals(False)
        chosen = select
        if chosen is None and self.source_list.count():
            chosen = self.source_list.item(0).data(Qt.ItemDataRole.UserRole)
        if chosen:
            for row in range(self.source_list.count()):
                if self.source_list.item(row).data(Qt.ItemDataRole.UserRole) == chosen:
                    self.source_list.blockSignals(True)
                    self.source_list.setCurrentRow(row)
                    self.source_list.blockSignals(False)
                    break
            self._show_source(chosen)
        else:
            self._show_source(None)
        self._refresh_board()

    def _source_changed(self, row: int) -> None:
        if row < 0:
            self._show_source(None)
            return
        self._show_source(self.source_list.item(row).data(Qt.ItemDataRole.UserRole))

    def _show_source(self, source_id) -> None:
        self._source_id = source_id
        if not source_id:
            self.canvas.set_page(None, [], {}, self.project.bleed, 0, 0)
            return
        page = self.project.source(source_id)
        panels = [p for p in self.project.panels if p.source_id == source_id]
        numbers = {}
        for panel in panels:
            uses = self.project.placements_of(panel.id)
            numbers[panel.id] = self.project.catalog_id(uses[0]) if uses else panel.id
        width, height = page.view_size()
        self.canvas.set_page(
            self.project.image_path(source_id),
            panels,
            numbers,
            self.project.bleed,
            width,
            height,
            gray=self.project.view_gray(source_id),
        )

    def _refresh_board(self) -> None:
        self._syncing = True
        while self.board_layout.count():
            item = self.board_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boards = []
        pages = self.project.board_page_count()
        # One empty page at the end so a panel can be dragged onto a new page.
        for page_index in range(pages + 1):
            label = QLabel(f"Page {page_index + 1}")
            listing = BoardList(page_index)
            listing.changed.connect(self._queue_sync)
            listing.panel_dropped.connect(self._place_from_bin)
            listing.itemSelectionChanged.connect(lambda widget=listing: self._board_selection(widget))
            for placement in self.project.placements_on(page_index):
                panel = self.project.panel(placement.panel_id)
                item = QListWidgetItem(self.project.catalog_id(placement))
                item.setData(Qt.ItemDataRole.UserRole, placement.id)
                item.setIcon(QIcon(self._icon(panel)))
                listing.addItem(item)
            self.board_layout.addWidget(label)
            self.board_layout.addWidget(listing)
            self._boards.append((page_index, listing))
        self.board_layout.addStretch(1)
        self._syncing = False
        self._refresh_catalog()

    def _queue_sync(self) -> None:
        if self._syncing:
            return
        QTimer.singleShot(0, self._sync_from_board)

    def _sync_from_board(self) -> None:
        if self._syncing:
            return
        pairs = []
        for page_index, widget in self._boards:
            for row in range(widget.count()):
                placement_id = widget.item(row).data(Qt.ItemDataRole.UserRole)
                if placement_id:
                    pairs.append((placement_id, page_index))
        try:
            self.project.set_reading_order(pairs)
        except ValueError as exc:
            self.status.setText(str(exc))
            self._refresh_board()
            return
        self._renumber()
        if self._source_id:
            self._show_source(self._source_id)
        self.status.setText("Reading order updated.")

    def _renumber(self) -> None:
        for _page, widget in self._boards:
            for row in range(widget.count()):
                placement_id = widget.item(row).data(Qt.ItemDataRole.UserRole)
                if placement_id:
                    widget.item(row).setText(self.project.catalog_id(self.project.placement(placement_id)))

    def _board_selection(self, widget: BoardList) -> None:
        if self._syncing:
            return
        items = widget.selectedItems()
        if len(items) != 1:
            return
        placement_id = items[0].data(Qt.ItemDataRole.UserRole)
        placement = self.project.placement(placement_id)
        panel = self.project.panel(placement.panel_id)
        self._placement_id = placement.id
        pid = panel.id
        self.canvas.selected_id = pid
        if panel.source_id != self._source_id:
            self.source_list.blockSignals(True)
            for row in range(self.source_list.count()):
                if self.source_list.item(row).data(Qt.ItemDataRole.UserRole) == panel.source_id:
                    self.source_list.setCurrentRow(row)
                    break
            self.source_list.blockSignals(False)
        self._show_source(panel.source_id)
        self.canvas.selected_id = pid
        self.canvas.update()
        self._load_meta(pid)

    def _select_board_item(self, panel_id: str) -> None:
        self._syncing = True
        for _page, widget in self._boards:
            widget.clearSelection()
            for row in range(widget.count()):
                if widget.item(row).data(Qt.ItemDataRole.UserRole) == panel_id:
                    widget.item(row).setSelected(True)
        self._syncing = False

    def _selected_board_ids(self) -> list[str]:
        ids = []
        for _page, widget in self._boards:
            for item in widget.selectedItems():
                pid = item.data(Qt.ItemDataRole.UserRole)
                if pid:
                    ids.append(pid)
        return ids

    def _one_board_selection(self) -> str | None:
        ids = self._selected_board_ids()
        if len(ids) == 1:
            return ids[0]
        return None

    def _icon(self, panel) -> QPixmap:
        gray = self._page_array(self.project.render_source(panel).id)
        crop = gray[panel.y0 : panel.y1, panel.x0 : panel.x1]
        if crop.size == 0:
            crop = np.full((8, 8), 255, dtype=np.uint8)
        image = QImage(
            np.ascontiguousarray(crop).data,
            crop.shape[1],
            crop.shape[0],
            crop.shape[1],
            QImage.Format.Format_Grayscale8,
        ).copy()
        return QPixmap.fromImage(image).scaled(120, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _page_array(self, source_id: str) -> np.ndarray:
        cache = getattr(self, "_arrays", {})
        if source_id not in cache:
            from ._pageio import load_gray

            cache[source_id] = np.clip(self.project.view_gray(source_id), 0, 255).astype(np.uint8)
            self._arrays = cache
        return cache[source_id]

    def _matches(self, panel) -> bool:
        tag_query = self.tag_filter.text().strip().lower()
        text_query = self.search.text().strip().lower()
        if tag_query and not any(tag_query in tag.lower() for tag in panel.tags):
            return False
        if text_query and text_query not in f"{panel.ocr_text}\n{panel.notes}".lower():
            return False
        return True

    def _refresh_catalog(self) -> None:
        if not hasattr(self, "catalog_tree"):
            return
        self.catalog_tree.blockSignals(True)
        self.catalog_tree.clear()
        grouped: dict[str, list] = {}
        for panel in self.project.panels:
            if self._matches(panel):
                grouped.setdefault(panel.source_id, []).append(panel)
        selected = self.canvas.selected_id
        for source_id, panels in grouped.items():
            source = self.project.source(source_id)
            parent = QTreeWidgetItem([f"{source.name} ({source.take})"])
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            for panel in panels:
                label = panel.id
                if panel.tags:
                    label += "  " + ", ".join(panel.tags)
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, panel.id)
                parent.addChild(child)
                if panel.id == selected:
                    child.setSelected(True)
            self.catalog_tree.addTopLevelItem(parent)
            parent.setExpanded(True)
        self.catalog_tree.blockSignals(False)
        self._refresh_bin()

    def _refresh_bin(self) -> None:
        if not hasattr(self, "bin"):
            return
        self.bin.blockSignals(True)
        self.bin.clear()
        for panel in self.project.panels:
            if not self._matches(panel):
                continue
            item = QListWidgetItem(panel.id)
            item.setData(Qt.ItemDataRole.UserRole, panel.id)
            item.setIcon(QIcon(self._icon(panel)))
            self.bin.addItem(item)
        self.bin.blockSignals(False)

    def _bin_selected(self) -> None:
        item = self.bin.currentItem()
        if item is None:
            return
        panel_id = item.data(Qt.ItemDataRole.UserRole)
        if not panel_id:
            return
        panel = self.project.panel(panel_id)
        self.canvas.selected_id = panel_id
        if panel.source_id != self._source_id:
            self._show_source(panel.source_id)
        else:
            self.canvas.update()
        self._load_meta(panel_id)

    def _place_from_bin(self, board_page: int, panel_id: str) -> None:
        placed = self.project.place(panel_id, board_page)
        self._placement_id = placed.id
        self._refresh_board()
        self.status.setText(
            f"Placed {panel_id} as {self.project.catalog_id(placed)}. The source file was not changed."
        )

    def _catalog_selected(self) -> None:
        items = self.catalog_tree.selectedItems()
        if not items:
            return
        panel_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not panel_id:
            return
        panel = self.project.panel(panel_id)
        self.canvas.selected_id = panel_id
        if panel.source_id != self._source_id:
            self.source_list.blockSignals(True)
            for row in range(self.source_list.count()):
                if self.source_list.item(row).data(Qt.ItemDataRole.UserRole) == panel.source_id:
                    self.source_list.setCurrentRow(row)
                    break
            self.source_list.blockSignals(False)
            self._show_source(panel.source_id)
        else:
            self.canvas.update()
        self.canvas.selected_id = panel_id
        self._select_board_item(panel_id)
        self._load_meta(panel_id)

    def _load_meta(self, panel_id: str | None) -> None:
        self._loading_meta = True
        try:
            if not panel_id:
                self.tags_edit.clear()
                self.notes_edit.clear()
                self.ocr_edit.clear()
                self.stage_box.setCurrentText("pencil")
                return
            panel = self.project.panel(panel_id)
            self.tags_edit.setText(", ".join(panel.tags))
            self.stage_box.setCurrentText(panel.stage if panel.stage in ("pencil", "ink") else "pencil")
            self.notes_edit.setPlainText(panel.notes)
            self.ocr_edit.setPlainText(panel.ocr_text)
        finally:
            self._loading_meta = False

    def _save_meta(self) -> None:
        if self._loading_meta:
            return
        panel_id = self.canvas.selected_id
        if not panel_id:
            return
        tags = [part.strip() for part in self.tags_edit.text().split(",")]
        self.project.set_meta(
            panel_id,
            tags=tags,
            notes=self.notes_edit.toPlainText(),
            stage=self.stage_box.currentText(),
            ocr_text=self.ocr_edit.toPlainText(),
        )
        if self.sender() in (self.tags_edit, self.stage_box):
            self._refresh_catalog()

    def _read_lettering(self) -> None:
        panel_id = self.canvas.selected_id
        if not panel_id:
            self.status.setText("Select a panel to read.")
            return
        result = self.project.read_lettering(panel_id)
        self._load_meta(panel_id)
        if not result.available:
            self.status.setText(result.message)
        elif result.text:
            self.status.setText(f"Read lettering on clip {panel_id}.")
        else:
            self.status.setText("Tesseract read this panel and returned no text.")

    def _export_catalog(self) -> None:
        if not self.project.panels:
            self.status.setText("There are no panels to export.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Catalog folder")
        if not folder:
            return
        try:
            written = self.project.export_catalog(folder)
        except Exception as exc:
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        self.status.setText(
            f"Wrote {written['json'].name}, {written['csv'].name}, and {len(written['images'])} panel image(s)."
        )

    def _export_page_png(self) -> None:
        if not self.project.placements:
            self.status.setText("There is no page layout to render.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Layout page", "page.png", "PNG (*.png)")
        if not path:
            return
        page_index = 0
        if self._placement_id:
            page_index = self.project.placement(self._placement_id).board_page
        try:
            dest = self.project.write_layout_image(page_index, path)
        except Exception as exc:
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        self.status.setText(f"Rendered page {page_index + 1} to {dest}")

    def _run(self, fn, done, label: str) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.status.setText(label)
        self.progress.setRange(0, 0)
        self._thread = _Call(fn)
        self._thread.done.connect(lambda value: self._finish(done, value))
        self._thread.failed.connect(self._fail)
        self._thread.start()

    def _finish(self, done, value) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._arrays = {}
        done(value)

    def _fail(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(message)


def main(argv=None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(argv if argv is not None else sys.argv)
    window = StudioWindow()
    window.show()
    return app.exec()
