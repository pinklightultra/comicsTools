"""Desktop window for the pencil_cleaner jobs.

PySide6 is optional. Install it with ``pip install "./pencil_cleaner[gui]"``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .desktop_jobs import run_job, suggest_output

SCAN_FILTER = "Scans (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp *.pdf);;All files (*)"

TASKS = (
    ("prep", "Prep"),
    ("clean", "Clean"),
    ("deskew", "Deskew"),
    ("panels", "Panels"),
    ("printer", "Printer test"),
    ("marks", "Corner marks"),
)

TASK_HELP = {
    "prep": "Lighten a pencils-only scan so a laser printer can lay down a faint ghost. light is 0.25 of black.",
    "clean": "Strip pencil ghost, smudge, and paper from an inked scan. morphological is the default here (minimum stroke 5 px). The command line still defaults to vector.",
    "deskew": "Straighten a scan and even out the lighting. This does not remove pencil or ink.",
    "panels": "Cut a page into panel PNGs along light gutters. Needs an image with at least two panels, not a PDF.",
    "printer": "Score a gray-swatch printer test sheet and recommend a prep opacity. A normal comic page is the wrong input.",
    "marks": "Straighten a page that was printed with four corner marks, and keep the ink. A scan without those marks is reported as an error.",
}

FOLDER_TASKS = {"panels", "printer", "marks"}


class DropLineEdit(QLineEdit):
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                self.file_dropped.emit(local)
                event.acceptProposedAction()
                return
        event.ignore()


class JobThread(QThread):
    succeeded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, task: str, kwargs: dict):
        super().__init__()
        self.task = task
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = run_job(self.task, **self.kwargs)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        preview = "" if result.preview is None else str(result.preview)
        self.succeeded.emit(result.summary, preview)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("comicsTools")
        self.setAcceptDrops(True)
        self.resize(1100, 760)
        self._thread: JobThread | None = None
        self._auto_output = True

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = QLabel("comicsTools")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        self.task_box = QComboBox()
        for key, label in TASKS:
            self.task_box.addItem(label, key)
        self.task_box.currentIndexChanged.connect(self._on_task_changed)
        layout.addWidget(self.task_box)

        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)

        self.input_edit = DropLineEdit()
        self.input_edit.setPlaceholderText("Drop a scan here, or choose a file")
        self.input_edit.textEdited.connect(self._on_input_edited)
        self.input_edit.file_dropped.connect(self.set_input)
        choose_in = QPushButton("Choose…")
        choose_in.clicked.connect(self._choose_input)
        layout.addLayout(self._row("Input", self.input_edit, choose_in))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output file or folder")
        self.output_edit.textEdited.connect(self._on_output_edited)
        choose_out = QPushButton("Choose…")
        choose_out.clicked.connect(self._choose_output)
        layout.addLayout(self._row("Output", self.output_edit, choose_out))

        self.options = QStackedWidget()
        self.options.addWidget(self._prep_options())
        self.options.addWidget(self._clean_options())
        self.options.addWidget(self._deskew_options())
        self.options.addWidget(self._panels_options())
        self.options.addWidget(self._printer_options())
        self.options.addWidget(self._marks_options())
        layout.addWidget(self.options)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.progress, 1)
        layout.addLayout(run_row)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setPlaceholderText("Status and errors show up here.")
        self.status.setFixedHeight(110)
        layout.addWidget(self.status)

        previews = QHBoxLayout()
        self.input_preview = self._preview_label("Input preview")
        self.output_preview = self._preview_label("Output preview")
        previews.addWidget(self.input_preview, 1)
        previews.addWidget(self.output_preview, 1)
        layout.addLayout(previews, 1)

        self._on_task_changed()

    def _row(self, label: str, field: QWidget, button: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setFixedWidth(64)
        row.addWidget(name)
        row.addWidget(field, 1)
        row.addWidget(button)
        return row

    def _preview_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(220)
        label.setFrameStyle(QLabel.Shape.Box | QLabel.Shadow.Plain)
        label.setStyleSheet("color: #444;")
        return label

    def _prep_options(self) -> QWidget:
        self.opacity = QComboBox()
        self.opacity.addItems(["light", "medium", "dark"])
        self.prep_dpi = self._spin(72, 1200, 300)
        return self._form(("Opacity", self.opacity), ("DPI (PDF)", self.prep_dpi))

    def _clean_options(self) -> QWidget:
        self.method = QComboBox()
        self.method.addItems(["morphological", "levels", "vector", "gpu"])
        self.clean_dpi = self._spin(72, 1200, 300)
        self.dark_thresh = self._spin(0, 255, 60)
        self.min_stroke = self._spin(1, 40, 5)
        self.gamma = self._double(0.2, 4.0, 1.6, 0.1)
        return self._form(
            ("Method", self.method),
            ("DPI (PDF)", self.clean_dpi),
            ("Dark threshold", self.dark_thresh),
            ("Min stroke (px)", self.min_stroke),
            ("Gamma", self.gamma),
        )

    def _deskew_options(self) -> QWidget:
        self.do_deskew = QCheckBox("Straighten")
        self.do_deskew.setChecked(True)
        self.do_flatten = QCheckBox("Flatten lighting")
        self.do_flatten.setChecked(True)
        self.max_angle = self._double(1.0, 45.0, 15.0, 0.5)
        self.deskew_dpi = self._spin(72, 1200, 300)
        return self._form(
            ("", self.do_deskew),
            ("", self.do_flatten),
            ("Max angle", self.max_angle),
            ("DPI (PDF)", self.deskew_dpi),
        )

    def _panels_options(self) -> QWidget:
        self.min_area = self._double(0.001, 0.5, 0.008, 0.001, decimals=3)
        self.padding = self._spin(0, 64, 8)
        self.min_gutter = self._spin(1, 64, 8)
        return self._form(
            ("Min area fraction", self.min_area),
            ("Padding (px)", self.padding),
            ("Min gutter (px)", self.min_gutter),
        )

    def _printer_options(self) -> QWidget:
        self.n_swatches = self._spin(1, 20, 7)
        self.residual = self._double(0.0, 1.0, 0.04, 0.01)
        return self._form(
            ("Swatches", self.n_swatches),
            ("Residual fail", self.residual),
        )

    def _marks_options(self) -> QWidget:
        self.marks_dpi = self._spin(150, 1200, 600)
        self.trim = QCheckBox("Trim to the ink")
        self.trim.setChecked(True)
        return self._form(("DPI", self.marks_dpi), ("", self.trim))

    def _form(self, *rows: tuple[str, QWidget]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        for label, widget in rows:
            form.addRow(label, widget)
        return page

    def _spin(self, lo: int, hi: int, value: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        return box

    def _double(
        self,
        lo: float,
        hi: float,
        value: float,
        step: float,
        decimals: int = 2,
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setDecimals(decimals)
        box.setSingleStep(step)
        box.setValue(value)
        return box

    def current_task(self) -> str:
        return str(self.task_box.currentData())

    def _on_task_changed(self) -> None:
        index = self.task_box.currentIndex()
        self.options.setCurrentIndex(index)
        task = self.current_task()
        self.help_label.setText(TASK_HELP[task])
        self._fill_output()

    def _on_input_edited(self) -> None:
        self._auto_output = True
        self._fill_output()
        self._show_input_preview()

    def _on_output_edited(self) -> None:
        self._auto_output = False

    def _fill_output(self) -> None:
        if not self._auto_output:
            return
        text = self.input_edit.text().strip()
        if not text:
            return
        self.output_edit.setText(str(suggest_output(self.current_task(), text)))

    def _choose_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a scan", "", SCAN_FILTER)
        if path:
            self.set_input(path)

    def _choose_output(self) -> None:
        task = self.current_task()
        if task in FOLDER_TASKS:
            path = QFileDialog.getExistingDirectory(self, "Choose an output folder")
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Choose an output file", self.output_edit.text())
        if path:
            self._auto_output = False
            self.output_edit.setText(path)

    def set_input(self, path: str) -> None:
        self.input_edit.setText(path)
        self._auto_output = True
        self._fill_output()
        self._show_input_preview()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                self.set_input(local)
                event.acceptProposedAction()
                return
        event.ignore()

    def _show_input_preview(self) -> None:
        self._set_preview(self.input_preview, self.input_edit.text().strip(), "Input preview")

    def _set_preview(self, label: QLabel, path: str, empty: str) -> None:
        if not path or not Path(path).is_file() or Path(path).suffix.lower() == ".pdf":
            label.setPixmap(QPixmap())
            label.setText("No image preview for a PDF." if path.lower().endswith(".pdf") else empty)
            return
        image = _preview_image(Path(path))
        if image is None:
            label.setPixmap(QPixmap())
            label.setText("Could not preview this file.")
            return
        label.setText("")
        label.setPixmap(
            QPixmap.fromImage(image).scaled(
                480,
                280,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _collect(self, task: str) -> dict:
        src = self.input_edit.text().strip()
        dest = self.output_edit.text().strip()
        if task == "prep":
            return {
                "input_path": src,
                "output_path": dest,
                "opacity": self.opacity.currentText(),
                "dpi": self.prep_dpi.value(),
            }
        if task == "clean":
            return {
                "input_path": src,
                "output_path": dest,
                "method": self.method.currentText(),
                "dpi": self.clean_dpi.value(),
                "dark_thresh": self.dark_thresh.value(),
                "min_stroke_px": self.min_stroke.value(),
                "gamma": self.gamma.value(),
            }
        if task == "deskew":
            return {
                "input_path": src,
                "output_path": dest,
                "deskew": self.do_deskew.isChecked(),
                "flatten": self.do_flatten.isChecked(),
                "max_angle": self.max_angle.value(),
                "dpi": self.deskew_dpi.value(),
            }
        if task == "panels":
            return {
                "input_path": src,
                "output_path": dest,
                "min_panel_area_frac": self.min_area.value(),
                "padding_px": self.padding.value(),
                "min_gutter_px": self.min_gutter.value(),
            }
        if task == "printer":
            return {
                "input_path": src,
                "output_path": dest,
                "n_swatches": self.n_swatches.value(),
                "residual_fail": self.residual.value(),
            }
        if task == "marks":
            return {
                "input_path": src,
                "output_path": dest,
                "dpi": self.marks_dpi.value(),
                "trim": self.trim.isChecked(),
            }
        raise ValueError(f"Unknown task {task!r}")

    def _run(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        task = self.current_task()
        try:
            kwargs = self._collect(task)
        except Exception as exc:
            self._on_fail(f"{type(exc).__name__}: {exc}")
            return
        self.run_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setPlainText("Working…")
        self._thread = JobThread(task, kwargs)
        self._thread.succeeded.connect(self._on_ok)
        self._thread.failed.connect(self._on_fail)
        self._thread.start()

    def _on_ok(self, summary: str, preview: str) -> None:
        self._idle()
        self.status.setPlainText(summary)
        self._set_preview(self.output_preview, preview, "Output preview")

    def _on_fail(self, message: str) -> None:
        self._idle()
        self.status.setPlainText(message)

    def _idle(self) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)


def _preview_image(path: Path, max_side: int = 1200) -> QImage | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            rgb.thumbnail((max_side, max_side))
            data = rgb.tobytes("raw", "RGB")
            image = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
            return image.copy()
    except Exception:
        return None


def main(argv=None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(argv if argv is not None else sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
