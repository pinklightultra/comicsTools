"""Project model for the comicsTools desktop window.

A project is a folder: ``project.json`` plus ``sources/*.png``. Page images
stay in that folder. Panel rectangles are source-image pixels, exclusive
on the right and bottom, the same convention as ``find_panel_boxes``.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw

from .deskew import deskew_array
from .mark_cleanup import ART, MARK_D, MARK_R, MARKS, PAGE_H, PAGE_W
from .panels import find_panel_boxes
from .printprep import PRESETS, _lighten_pencils
from .printer_test import DEFAULT_LEVELS
from ._pageio import load_gray, pdf_to_gray_pages, save_pages_as_pdf

PathLike = Union[str, Path]
Box = tuple[int, int, int, int]

LAYOUTS = ((2, 2), (2, 3), (3, 2), (3, 3))
MIN_PANEL = 8


@dataclass
class SourcePage:
    id: str
    name: str
    image_name: str
    width: int
    height: int
    skew_degrees: float = 0.0


@dataclass
class Panel:
    id: str
    source_id: str
    x0: int
    y0: int
    x1: int
    y1: int
    board_page: int = 0
    order: int = 0

    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)

    def width(self) -> int:
        return self.x1 - self.x0

    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class Project:
    name: str = "Untitled"
    root: Optional[Path] = None
    sources: list[SourcePage] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)
    opacity: str = "light"
    columns: int = 2
    rows: int = 3
    bleed: int = 12
    dpi: int = 300

    def source(self, source_id: str) -> SourcePage:
        for page in self.sources:
            if page.id == source_id:
                return page
        raise KeyError(source_id)

    def panel(self, panel_id: str) -> Panel:
        for panel in self.panels:
            if panel.id == panel_id:
                return panel
        raise KeyError(panel_id)

    def image_path(self, source_id: str) -> Path:
        if self.root is None:
            raise RuntimeError("Project has no folder yet.")
        return self.root / "sources" / self.source(source_id).image_name

    def reading_order(self) -> list[Panel]:
        return sorted(self.panels, key=lambda p: (p.board_page, p.order, p.id))

    def reading_number(self, panel_id: str) -> int:
        for index, panel in enumerate(self.reading_order(), start=1):
            if panel.id == panel_id:
                return index
        raise KeyError(panel_id)

    def panels_on(self, board_page: int) -> list[Panel]:
        return [p for p in self.reading_order() if p.board_page == board_page]

    def board_page_count(self) -> int:
        if not self.panels:
            return 1
        return max(p.board_page for p in self.panels) + 1

    def slots_per_page(self) -> int:
        return max(1, int(self.columns) * int(self.rows))

    def import_files(self, paths: Sequence[PathLike]) -> list[str]:
        self._ensure_root()
        added: list[str] = []
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                raise FileNotFoundError(f"No file at {path}")
            if path.suffix.lower() == ".pdf":
                added.extend(self._import_pdf(path))
            else:
                added.extend(self._import_raster(path))
        for source_id in added:
            self.detect_panels(source_id, pack=False)
        self.pack_grid()
        return added

    def detect_panels(self, source_id: str, pack: bool = True) -> list[Panel]:
        page = self.source(source_id)
        gray = load_gray(self.image_path(source_id))
        boxes = find_panel_boxes(gray)
        self.panels = [p for p in self.panels if p.source_id != source_id]
        created: list[Panel] = []
        start = len(self.reading_order())
        for index, (x0, y0, x1, y1) in enumerate(boxes):
            panel = Panel(
                id=self._new_id("panel"),
                source_id=source_id,
                x0=int(x0),
                y0=int(y0),
                x1=int(x1),
                y1=int(y1),
                board_page=0,
                order=start + index,
            )
            self.panels.append(panel)
            created.append(panel)
        if pack:
            self.pack_grid()
        return created

    def set_rect(self, panel_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        panel = self.panel(panel_id)
        page = self.source(panel.source_id)
        panel.x0, panel.y0, panel.x1, panel.y1 = clamp_box(
            x0, y0, x1, y1, page.width, page.height
        )
        return panel

    def add_panel(self, source_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        page = self.source(source_id)
        x0, y0, x1, y1 = clamp_box(x0, y0, x1, y1, page.width, page.height)
        panel = Panel(
            id=self._new_id("panel"),
            source_id=source_id,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            board_page=self.board_page_count() - 1 if self.panels else 0,
            order=10_000,
        )
        self.panels.append(panel)
        self._compact_orders()
        return panel

    def delete_panel(self, panel_id: str) -> None:
        self.panel(panel_id)
        self.panels = [p for p in self.panels if p.id != panel_id]
        self._compact_orders()

    def duplicate_panel(self, panel_id: str) -> Panel:
        src = self.panel(panel_id)
        copy = Panel(
            id=self._new_id("panel"),
            source_id=src.source_id,
            x0=src.x0,
            y0=src.y0,
            x1=src.x1,
            y1=src.y1,
            board_page=src.board_page,
            order=src.order + 1,
        )
        for panel in self.panels:
            if panel.board_page == src.board_page and panel.order > src.order:
                panel.order += 1
        self.panels.append(copy)
        return copy

    def split_panel(self, panel_id: str) -> tuple[Panel, Panel]:
        panel = self.panel(panel_id)
        x0, y0, x1, y1 = panel.box()
        vertical = (x1 - x0) >= (y1 - y0)
        if vertical and (x1 - x0) < MIN_PANEL * 2:
            raise ValueError("Panel is too narrow to split.")
        if not vertical and (y1 - y0) < MIN_PANEL * 2:
            raise ValueError("Panel is too short to split.")
        if vertical:
            mid = x0 + (x1 - x0) // 2
            panel.x1 = mid
            extra = (mid, y0, x1, y1)
        else:
            mid = y0 + (y1 - y0) // 2
            panel.y1 = mid
            extra = (x0, mid, x1, y1)
        other = Panel(
            id=self._new_id("panel"),
            source_id=panel.source_id,
            x0=extra[0],
            y0=extra[1],
            x1=extra[2],
            y1=extra[3],
            board_page=panel.board_page,
            order=panel.order + 1,
        )
        for item in self.panels:
            if item is other:
                continue
            if item.board_page == panel.board_page and item.order > panel.order:
                item.order += 1
        self.panels.append(other)
        return panel, other

    def merge_panels(self, first_id: str, second_id: str) -> Panel:
        if first_id == second_id:
            raise ValueError("Choose two different panels to merge.")
        first = self.panel(first_id)
        second = self.panel(second_id)
        if first.source_id != second.source_id:
            raise ValueError("Panels on different scans cannot be merged.")
        keep, drop = (first, second) if (first.board_page, first.order) <= (second.board_page, second.order) else (second, first)
        keep.x0 = min(first.x0, second.x0)
        keep.y0 = min(first.y0, second.y0)
        keep.x1 = max(first.x1, second.x1)
        keep.y1 = max(first.y1, second.y1)
        self.panels = [p for p in self.panels if p.id != drop.id]
        self._compact_orders()
        return keep

    def move_by(self, panel_id: str, steps: int) -> None:
        ordered = self.reading_order()
        index = next(i for i, panel in enumerate(ordered) if panel.id == panel_id)
        target = index + steps
        if target < 0 or target >= len(ordered):
            return
        first, second = ordered[index], ordered[target]
        first.board_page, second.board_page = second.board_page, first.board_page
        first.order, second.order = second.order, first.order

    def move_to_page(self, panel_id: str, board_page: int) -> None:
        if board_page < 0:
            return
        panel = self.panel(panel_id)
        panel.board_page = int(board_page)
        panel.order = 10_000
        self._compact_orders()

    def set_reading_order(self, pairs: Sequence[tuple[str, int]]) -> None:
        seen = [pid for pid, _page in pairs]
        if set(seen) != {p.id for p in self.panels} or len(seen) != len(self.panels):
            raise ValueError("The new order has to include every panel once.")
        counts: dict[int, int] = {}
        for pid, page in pairs:
            panel = self.panel(pid)
            panel.board_page = int(page)
            panel.order = counts.get(int(page), 0)
            counts[int(page)] = panel.order + 1

    def pack_grid(self) -> None:
        slots = self.slots_per_page()
        for index, panel in enumerate(self.reading_order()):
            panel.board_page = index // slots
            panel.order = index % slots

    def crop_box(self, panel: Panel) -> Box:
        page = self.source(panel.source_id)
        bleed = max(0, int(self.bleed))
        return (
            max(0, panel.x0 - bleed),
            max(0, panel.y0 - bleed),
            min(page.width, panel.x1 + bleed),
            min(page.height, panel.y1 + bleed),
        )

    def render_ghost_pages(
        self,
        panel_ids: Optional[Iterable[str]] = None,
        board_pages: Optional[Iterable[int]] = None,
        include_test: bool = True,
    ) -> list[Image.Image]:
        chosen = self._export_panels(panel_ids, board_pages)
        if not chosen:
            raise ValueError("There are no panels to print.")
        opacity = _opacity_value(self.opacity)
        cache = {source.id: load_gray(self.image_path(source.id)) for source in self.sources}
        if panel_ids is not None:
            groups = _chunks(chosen, self.slots_per_page())
            pages = [self._compose_page(group, cache, opacity, packed=True) for group in groups]
        else:
            page_numbers = sorted({p.board_page for p in chosen})
            pages = []
            for number in page_numbers:
                group = [p for p in chosen if p.board_page == number]
                pages.append(self._compose_page(group, cache, opacity, packed=False))
        if include_test:
            pages.append(render_printer_test_page(self.dpi))
        return pages

    def write_ghost_pdf(
        self,
        dest: PathLike,
        panel_ids: Optional[Iterable[str]] = None,
        board_pages: Optional[Iterable[int]] = None,
        include_test: bool = True,
    ) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pages = self.render_ghost_pages(panel_ids, board_pages, include_test=include_test)
        save_pages_as_pdf(pages, dest, resolution=float(self.dpi))
        return dest

    def save(self, dest: Optional[PathLike] = None) -> Path:
        if dest is None and self.root is None:
            raise ValueError("Choose a project folder.")
        target = Path(dest) if dest is not None else Path(self.root)
        target.mkdir(parents=True, exist_ok=True)
        sources_dir = target / "sources"
        sources_dir.mkdir(exist_ok=True)
        if self.root is not None and target.resolve() != Path(self.root).resolve():
            for source in self.sources:
                shutil.copy2(self.root / "sources" / source.image_name, sources_dir / source.image_name)
        self.root = target
        payload = {
            "version": 1,
            "name": self.name,
            "opacity": self.opacity,
            "columns": self.columns,
            "rows": self.rows,
            "bleed": self.bleed,
            "dpi": self.dpi,
            "sources": [asdict(source) for source in self.sources],
            "panels": [asdict(panel) for panel in self.panels],
        }
        (target / "project.json").write_text(json.dumps(payload, indent=2) + "\n")
        return target

    @classmethod
    def load(cls, folder: PathLike) -> "Project":
        folder = Path(folder)
        path = folder / "project.json"
        if not path.is_file():
            raise FileNotFoundError(f"No project.json in {folder}")
        data = json.loads(path.read_text())
        if data.get("version") != 1:
            raise ValueError(f"Unsupported project version {data.get('version')!r}.")
        project = cls(
            name=data.get("name") or "Untitled",
            root=folder,
            opacity=data.get("opacity") or "light",
            columns=int(data.get("columns") or 2),
            rows=int(data.get("rows") or 3),
            bleed=int(data.get("bleed") or 0),
            dpi=int(data.get("dpi") or 300),
        )
        for raw in data.get("sources") or []:
            project.sources.append(SourcePage(**raw))
        for raw in data.get("panels") or []:
            project.panels.append(Panel(**raw))
        return project

    def _export_panels(self, panel_ids, board_pages) -> list[Panel]:
        ordered = self.reading_order()
        if panel_ids is not None:
            wanted = set(panel_ids)
            return [p for p in ordered if p.id in wanted]
        if board_pages is not None:
            wanted = {int(n) for n in board_pages}
            return [p for p in ordered if p.board_page in wanted]
        return ordered

    def _compose_page(self, panels: list[Panel], cache, opacity: float, packed: bool) -> Image.Image:
        dpi = int(self.dpi)
        width, height = _px(PAGE_W, dpi), _px(PAGE_H, dpi)
        canvas = Image.new("L", (width, height), 255)
        art = (
            _px(ART[0], dpi),
            _px(ART[1], dpi),
            _px(ART[2], dpi),
            _px(ART[3], dpi),
        )
        gap = _px(0.12, dpi)
        cols, rows = int(self.columns), int(self.rows)
        cell_w = (art[2] - art[0] - gap * (cols - 1)) // cols
        cell_h = (art[3] - art[1] - gap * (rows - 1)) // rows
        ordered = list(panels)
        if packed:
            ordered = list(panels)
        for index, panel in enumerate(ordered):
            slot = index if packed else panel.order
            col = slot % cols
            row = slot // cols
            if row >= rows or col >= cols:
                continue
            crop = self._ghost_crop(panel, cache[panel.source_id], opacity)
            fitted = _fit(crop, cell_w, cell_h)
            x = art[0] + col * (cell_w + gap) + (cell_w - fitted.width) // 2
            y = art[1] + row * (cell_h + gap) + (cell_h - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        _draw_marks(canvas, dpi)
        return canvas

    def _ghost_crop(self, panel: Panel, gray: np.ndarray, opacity: float) -> Image.Image:
        x0, y0, x1, y1 = self.crop_box(panel)
        crop = np.asarray(gray[y0:y1, x0:x1], dtype=np.float32)
        if crop.size == 0:
            return Image.new("L", (1, 1), 255)
        light = lighten_crop(crop, opacity)
        arr = np.clip(light * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, "L")

    def _import_raster(self, path: Path) -> list[str]:
        image = Image.open(path)
        frames = getattr(image, "n_frames", 1)
        added = []
        stem = path.stem
        for index in range(frames):
            image.seek(index)
            gray = np.asarray(image.convert("L"), dtype=np.float32)
            name = stem if frames == 1 else f"{stem} p{index + 1}"
            added.append(self._add_gray(gray, name))
        return added

    def _add_gray(self, gray: np.ndarray, name: str) -> str:
        straightened, angle = deskew_array(gray, deskew=True, flatten=False)
        arr = np.clip(straightened, 0, 255).astype(np.uint8)
        source_id = self._new_id("page")
        image_name = f"{source_id}.png"
        assert self.root is not None
        out = self.root / "sources" / image_name
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr, "L").save(out)
        self.sources.append(
            SourcePage(
                id=source_id,
                name=name,
                image_name=image_name,
                width=int(arr.shape[1]),
                height=int(arr.shape[0]),
                skew_degrees=float(angle),
            )
        )
        return source_id

    def _ensure_root(self) -> None:
        if self.root is None:
            import tempfile

            self.root = Path(tempfile.mkdtemp(prefix="comics-tools-"))

    def _new_id(self, prefix: str) -> str:
        existing = {s.id for s in self.sources} | {p.id for p in self.panels}
        number = 1
        while f"{prefix}{number}" in existing:
            number += 1
        return f"{prefix}{number}"

    def _compact_orders(self) -> None:
        self._assign_sequential(self.reading_order())

    def _assign_sequential(self, ordered: list[Panel]) -> None:
        by_page: dict[int, list[Panel]] = {}
        for panel in ordered:
            by_page.setdefault(panel.board_page, []).append(panel)
        for page, group in by_page.items():
            for order, panel in enumerate(group):
                panel.board_page = page
                panel.order = order

    def _import_pdf(self, path: Path) -> list[str]:
        # 150 dpi keeps an imported PDF page small enough to edit.
        # Panel coordinates are pixels of this stored image.
        pages = pdf_to_gray_pages(path, dpi=150)
        added = []
        stem = path.stem
        for index, gray in enumerate(pages, start=1):
            name = stem if len(pages) == 1 else f"{stem} p{index}"
            added.append(self._add_gray(gray, name))
        return added


def lighten_crop(gray: np.ndarray, opacity: float) -> np.ndarray:
    """Same opacity scale as ``prep_pencils_for_printing``.

    Crops smaller than the paper-tone window use the grain floor and the
    opacity only. The paper-tone estimate needs a wider neighborhood.
    """
    if min(gray.shape[:2]) < 80:
        dark = np.clip((255.0 - gray.astype(np.float32)) / 255.0, 0, 1)
        dark = np.clip((dark - 0.07) / 0.93, 0, 1)
        return 1.0 - dark * float(opacity)
    return _lighten_pencils(gray.astype(np.float32), float(opacity))


def render_printer_test_page(dpi: int = 300) -> Image.Image:
    """US Letter sheet of the swatch opacities ``score_printer_test`` expects."""
    width, height = _px(PAGE_W, dpi), _px(PAGE_H, dpi)
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    art = (_px(ART[0], dpi), _px(ART[1], dpi), _px(ART[2], dpi), _px(ART[3], dpi))
    label_h = _px(0.45, dpi)
    swatch_top = art[1]
    swatch_bottom = art[3] - label_h
    count = len(DEFAULT_LEVELS)
    col_w = (art[2] - art[0]) / count
    for index, level in enumerate(DEFAULT_LEVELS):
        x0 = int(round(art[0] + index * col_w))
        x1 = int(round(art[0] + (index + 1) * col_w))
        gray = int(round(255 * (1.0 - float(level))))
        draw.rectangle([x0, swatch_top, x1 - 1, swatch_bottom], fill=gray)
        label = f"{int(round(level * 100))}%"
        draw.text((x0 + 8, swatch_bottom + 8), label, fill=0)
    draw.text((art[0], art[1] - _px(0.28, dpi)), "Printer test. Draw a pen line across the swatches.", fill=0)
    _draw_marks(canvas, dpi)
    return canvas


def clamp_box(x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> Box:
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    x0 = max(0, min(x0, max(0, width - MIN_PANEL)))
    y0 = max(0, min(y0, max(0, height - MIN_PANEL)))
    x1 = max(x0 + MIN_PANEL, min(x1, width))
    y1 = max(y0 + MIN_PANEL, min(y1, height))
    return x0, y0, x1, y1


def _opacity_value(name: str) -> float:
    if name in PRESETS:
        return float(PRESETS[name])
    return float(name)


def _px(inches: float, dpi: int) -> int:
    return int(round(float(inches) * int(dpi)))


def _fit(image: Image.Image, cell_w: int, cell_h: int) -> Image.Image:
    if cell_w < 1 or cell_h < 1:
        return image
    scale = min(cell_w / image.width, cell_h / image.height)
    size = (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale))))
    if size == image.size:
        return image
    return image.resize(size, Image.Resampling.LANCZOS)


def _draw_marks(canvas: Image.Image, dpi: int) -> None:
    draw = ImageDraw.Draw(canvas)
    half = max(1, _px(MARK_D, dpi) // 2)
    radius = max(1, _px(MARK_R, dpi))
    for index, (mx, my) in enumerate(MARKS):
        cx, cy = _px(mx, dpi), _px(my, dpi)
        if index == 0:
            draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=0)
        else:
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=0)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]
