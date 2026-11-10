"""Edit decision list for the comicsTools window.

A project folder holds untouched copies of the scans plus ``project.json``.
A panel is a clip: source file, crop rectangle, deskew angle, and cleanup
settings. Page layouts reference those clips. Rendering is done when you
export. The source files are not written again after import.
"""
from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw

from .deskew import deskew_array, rotate_image
from .mark_cleanup import ART, MARK_D, MARK_R, MARKS, PAGE_H, PAGE_W
from .panels import find_panel_boxes
from .printprep import PRESETS, _lighten_pencils
from .printer_test import DEFAULT_LEVELS
from ._pageio import load_gray, pdf_to_gray_pages, save_pages_as_pdf

PathLike = Union[str, Path]
Box = tuple[int, int, int, int]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

LAYOUTS = ((2, 2), (2, 3), (3, 2), (3, 3))
MIN_PANEL = 8


@dataclass
class SourcePage:
    """Untouched scan. Deskew is a viewing angle, not a saved pixel change."""

    id: str
    name: str
    image_name: str
    width: int
    height: int
    skew_degrees: float = 0.0
    view_width: int = 0
    view_height: int = 0
    take: str = "pencil"
    linked_id: str = ""

    def view_size(self) -> tuple[int, int]:
        if self.view_width and self.view_height:
            return self.view_width, self.view_height
        return self.width, self.height


@dataclass
class Panel:
    """A clip in the library. The crop is in the deskewed view of ``source_id``."""

    id: str
    source_id: str
    x0: int
    y0: int
    x1: int
    y1: int
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    stage: str = "pencil"
    cleanup_method: str = ""
    ocr_text: str = ""
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)

    def width(self) -> int:
        return self.x1 - self.x0

    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class Placement:
    """One use of a library clip on a layout page."""

    id: str
    panel_id: str
    board_page: int = 0
    order: int = 0


@dataclass
class Project:
    name: str = "Untitled"
    root: Optional[Path] = None
    sources: list[SourcePage] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    opacity: str = "light"
    columns: int = 2
    rows: int = 3
    bleed: int = 12
    dpi: int = 300

    def __post_init__(self) -> None:
        self._undo: list[dict] = []
        self._redo: list[dict] = []

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

    def placement(self, placement_id: str) -> Placement:
        for item in self.placements:
            if item.id == placement_id:
                return item
        raise KeyError(placement_id)

    def reading_order(self) -> list[Placement]:
        return sorted(self.placements, key=lambda p: (p.board_page, p.order, p.id))

    def placements_on(self, board_page: int) -> list[Placement]:
        return [p for p in self.reading_order() if p.board_page == board_page]

    def placements_of(self, panel_id: str) -> list[Placement]:
        return [p for p in self.reading_order() if p.panel_id == panel_id]

    def board_page_count(self) -> int:
        if not self.placements:
            return 1
        return max(p.board_page for p in self.placements) + 1

    def catalog_id(self, placement: Placement) -> str:
        """Page and panel numbers are 1-based. Page 3, panel 2 is ``p003-02``."""
        return f"p{int(placement.board_page) + 1:03d}-{int(placement.order) + 1:02d}"

    def catalog_record(self, placement: Placement) -> dict:
        panel = self.panel(placement.panel_id)
        source = self.render_source(panel)
        return {
            "id": self.catalog_id(placement),
            "stable_id": panel.id,
            "placement_id": placement.id,
            "page": int(placement.board_page) + 1,
            "panel": int(placement.order) + 1,
            "bbox": [panel.x0, panel.y0, panel.x1, panel.y1],
            "source_file": source.image_name,
            "deskew_degrees": source.skew_degrees,
            "cleanup_method": panel.cleanup_method,
            "tags": list(panel.tags),
            "notes": panel.notes,
            "stage": panel.stage,
            "ocr_text": panel.ocr_text,
            "created_at": panel.created_at,
            "updated_at": panel.updated_at,
        }

    def catalog_records(self) -> list[dict]:
        return [self.catalog_record(item) for item in self.reading_order()]

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
        self.checkpoint()
        view = self.view_gray(source_id)
        boxes = find_panel_boxes(view)
        removed = {p.id for p in self.panels if p.source_id == source_id}
        self.panels = [p for p in self.panels if p.source_id != source_id]
        self.placements = [p for p in self.placements if p.panel_id not in removed]
        created: list[Panel] = []
        for x0, y0, x1, y1 in boxes:
            panel = self._new_panel(source_id, int(x0), int(y0), int(x1), int(y1))
            self.panels.append(panel)
            self.placements.append(
                Placement(id=self._new_id("use"), panel_id=panel.id, board_page=0, order=10_000)
            )
            created.append(panel)
        self._compact_orders()
        if pack:
            self.pack_grid()
        return created

    def set_rect(self, panel_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        self.checkpoint()
        panel = self.panel(panel_id)
        width, height = self.source(panel.source_id).view_size()
        panel.x0, panel.y0, panel.x1, panel.y1 = clamp_box(x0, y0, x1, y1, width, height)
        panel.updated_at = _now()
        return panel

    def add_panel(self, source_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        self.checkpoint()
        width, height = self.source(source_id).view_size()
        x0, y0, x1, y1 = clamp_box(x0, y0, x1, y1, width, height)
        panel = self._new_panel(source_id, x0, y0, x1, y1)
        self.panels.append(panel)
        self.place(panel.id, self.board_page_count() - 1 if self.placements else 0, record=False)
        return panel

    def delete_panel(self, panel_id: str) -> None:
        """Remove a clip from the library and every layout that uses it."""
        self.checkpoint()
        self.panel(panel_id)
        self.panels = [p for p in self.panels if p.id != panel_id]
        self.placements = [p for p in self.placements if p.panel_id != panel_id]
        self._compact_orders()

    def remove_placement(self, placement_id: str) -> None:
        """Take one use off a layout. The clip stays in the library."""
        self.checkpoint()
        self.placement(placement_id)
        self.placements = [p for p in self.placements if p.id != placement_id]
        self._compact_orders()

    def place(self, panel_id: str, board_page: int = 0, record: bool = True) -> Placement:
        """Put a library clip on a layout page. The same clip can be placed again."""
        if record:
            self.checkpoint()
        self.panel(panel_id)
        board_page = max(0, int(board_page))
        item = Placement(
            id=self._new_id("use"),
            panel_id=panel_id,
            board_page=board_page,
            order=10_000,
        )
        self.placements.append(item)
        self._compact_orders()
        return item

    def duplicate_panel(self, panel_id: str) -> Placement:
        """Place the same clip again. This does not copy or rewrite pixels."""
        current = self.placements_of(panel_id)
        page = current[0].board_page if current else 0
        return self.place(panel_id, page)

    def split_panel(self, panel_id: str) -> tuple[Panel, Panel]:
        self.checkpoint()
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
        other = self._new_panel(panel.source_id, *extra)
        other.tags = list(panel.tags)
        other.stage = panel.stage
        other.cleanup_method = panel.cleanup_method
        self.panels.append(other)
        anchor = self.placements_of(panel.id)
        page = anchor[0].board_page if anchor else 0
        order = anchor[0].order + 1 if anchor else 10_000
        for item in self.placements:
            if item.board_page == page and item.order >= order:
                item.order += 1
        self.placements.append(Placement(id=self._new_id("use"), panel_id=other.id, board_page=page, order=order))
        panel.updated_at = _now()
        return panel, other

    def merge_panels(self, first_id: str, second_id: str) -> Panel:
        if first_id == second_id:
            raise ValueError("Choose two different panels to merge.")
        self.checkpoint()
        first = self.panel(first_id)
        second = self.panel(second_id)
        if first.source_id != second.source_id:
            raise ValueError("Panels on different scans cannot be merged.")
        first.x0 = min(first.x0, second.x0)
        first.y0 = min(first.y0, second.y0)
        first.x1 = max(first.x1, second.x1)
        first.y1 = max(first.y1, second.y1)
        first.updated_at = _now()
        for item in self.placements:
            if item.panel_id == second.id:
                item.panel_id = first.id
        self.panels = [p for p in self.panels if p.id != second.id]
        self._compact_orders()
        return first

    def move_by(self, placement_id: str, steps: int) -> None:
        self.checkpoint()
        ordered = self.reading_order()
        index = next(i for i, item in enumerate(ordered) if item.id == placement_id)
        target = index + steps
        if target < 0 or target >= len(ordered):
            self._undo.pop()
            return
        first, second = ordered[index], ordered[target]
        first.board_page, second.board_page = second.board_page, first.board_page
        first.order, second.order = second.order, first.order

    def move_to_page(self, placement_id: str, board_page: int) -> None:
        if board_page < 0:
            return
        self.checkpoint()
        item = self.placement(placement_id)
        item.board_page = int(board_page)
        item.order = 10_000
        self._compact_orders()

    def set_reading_order(self, pairs: Sequence[tuple[str, int]]) -> None:
        seen = [pid for pid, _page in pairs]
        if set(seen) != {p.id for p in self.placements} or len(seen) != len(self.placements):
            raise ValueError("The new order has to include every layout use once.")
        self.checkpoint()
        counts: dict[int, int] = {}
        for pid, page in pairs:
            item = self.placement(pid)
            item.board_page = int(page)
            item.order = counts.get(int(page), 0)
            counts[int(page)] = item.order + 1

    def pack_grid(self) -> None:
        self.checkpoint()
        slots = self.slots_per_page()
        for index, item in enumerate(self.reading_order()):
            item.board_page = index // slots
            item.order = index % slots

    def link_takes(self, pencil_id: str, ink_id: str) -> None:
        """Treat two scans as pencil and ink of the same page."""
        if pencil_id == ink_id:
            raise ValueError("Pick two different scans.")
        self.checkpoint()
        pencil = self.source(pencil_id)
        ink = self.source(ink_id)
        pencil.take = "pencil"
        ink.take = "ink"
        pencil.linked_id = ink.id
        ink.linked_id = pencil.id

    def render_source(self, panel: Panel) -> SourcePage:
        """The take currently chosen for this clip. Pencil is the geometry source."""
        if panel.stage == "ink" and self.source(panel.source_id).linked_id:
            return self.source(self.source(panel.source_id).linked_id)
        return self.source(panel.source_id)

    def crop_box(self, panel: Panel) -> Box:
        width, height = self.source(panel.source_id).view_size()
        bleed = max(0, int(self.bleed))
        return (
            max(0, panel.x0 - bleed),
            max(0, panel.y0 - bleed),
            min(width, panel.x1 + bleed),
            min(height, panel.y1 + bleed),
        )

    def view_gray(self, source_id: str) -> np.ndarray:
        """Deskewed view. The file on disk is not rotated."""
        source = self.source(source_id)
        gray = load_gray(self.image_path(source_id))
        if abs(source.skew_degrees) < 0.05:
            return gray
        return rotate_image(gray, source.skew_degrees)

    def checkpoint(self) -> None:
        self._undo.append(self._capture())
        self._redo.clear()
        if len(self._undo) > 40:
            del self._undo[0]

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._capture())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._capture())
        self._restore(self._redo.pop())
        return True

    def render_ghost_pages(
        self,
        panel_ids: Optional[Iterable[str]] = None,
        board_pages: Optional[Iterable[int]] = None,
        include_test: bool = True,
    ) -> list[Image.Image]:
        chosen = self._export_placements(panel_ids, board_pages)
        if not chosen:
            raise ValueError("There are no panels to print.")
        opacity = _opacity_value(self.opacity)
        if panel_ids is not None:
            groups = _chunks(chosen, self.slots_per_page())
            pages = [self._compose_page(group, opacity, packed=True) for group in groups]
        else:
            page_numbers = sorted({p.board_page for p in chosen})
            pages = []
            for number in page_numbers:
                group = [p for p in chosen if p.board_page == number]
                pages.append(self._compose_page(group, opacity, packed=False))
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

    def write_layout_image(self, board_page: int, dest: PathLike) -> Path:
        """Render one layout page to a PNG without changing the sources."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        page = self._compose_page(self.placements_on(board_page), _opacity_value(self.opacity), packed=False)
        page.save(dest)
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
            "version": 2,
            "name": self.name,
            "opacity": self.opacity,
            "columns": self.columns,
            "rows": self.rows,
            "bleed": self.bleed,
            "dpi": self.dpi,
            "sources": [asdict(source) for source in self.sources],
            "panels": [asdict(panel) for panel in self.panels],
            "placements": [asdict(item) for item in self.placements],
        }
        (target / "project.json").write_text(json.dumps(payload, indent=2) + "\n")
        self.write_catalog(target / "catalog.json")
        return target

    @classmethod
    def load(cls, folder: PathLike) -> "Project":
        folder = Path(folder)
        path = folder / "project.json"
        if not path.is_file():
            raise FileNotFoundError(f"No project.json in {folder}")
        data = json.loads(path.read_text())
        version = data.get("version")
        if version not in (1, 2):
            raise ValueError(f"Unsupported project version {version!r}.")
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
        if version == 1:
            for index, raw in enumerate(data.get("panels") or []):
                raw = dict(raw)
                board = int(raw.pop("board_page", 0))
                order = int(raw.pop("order", index))
                panel = Panel(**raw)
                project.panels.append(panel)
                project.placements.append(
                    Placement(id=f"use{index + 1}", panel_id=panel.id, board_page=board, order=order)
                )
        else:
            for raw in data.get("panels") or []:
                project.panels.append(Panel(**{k: v for k, v in raw.items() if k in Panel.__dataclass_fields__}))
            for raw in data.get("placements") or []:
                project.placements.append(Placement(**raw))
        catalog_path = folder / "catalog.json"
        if catalog_path.is_file():
            project.apply_catalog(json.loads(catalog_path.read_text()))
        return project

    def set_meta(
        self,
        panel_id: str,
        *,
        tags: Optional[Sequence[str]] = None,
        notes: Optional[str] = None,
        stage: Optional[str] = None,
        ocr_text: Optional[str] = None,
    ) -> Panel:
        panel = self.panel(panel_id)
        if tags is not None:
            panel.tags = _clean_tags(tags)
        if notes is not None:
            panel.notes = str(notes)
        if stage is not None:
            if stage not in ("pencil", "ink"):
                raise ValueError("Stage is pencil or ink.")
            panel.stage = stage
        if ocr_text is not None:
            panel.ocr_text = str(ocr_text)
        panel.updated_at = _now()
        return panel

    def read_lettering(self, panel_id: str):
        from .ocr_text import recognize

        panel = self.panel(panel_id)
        gray = self.view_gray(panel.source_id)
        crop = np.asarray(gray[panel.y0 : panel.y1, panel.x0 : panel.x1])
        if crop.size == 0:
            from .ocr_text import OcrResult

            return OcrResult(text="", available=True, message="The panel crop is empty.")
        image = Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8), "L")
        result = recognize(image)
        if result.available:
            panel.ocr_text = result.text
            panel.updated_at = _now()
        return result

    def write_catalog(self, dest: PathLike) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "panels": self.catalog_records()}
        dest.write_text(json.dumps(payload, indent=2) + "\n")
        return dest

    def apply_catalog(self, payload: dict) -> None:
        """Copy metadata from a catalog sidecar onto the matching panels."""
        by_stable = {panel.id: panel for panel in self.panels}
        for record in payload.get("panels") or []:
            panel = by_stable.get(record.get("stable_id"))
            if panel is None:
                continue
            if "tags" in record:
                panel.tags = _clean_tags(record.get("tags") or [])
            if "notes" in record:
                panel.notes = str(record.get("notes") or "")
            if record.get("stage") in ("pencil", "ink"):
                panel.stage = record["stage"]
            if "ocr_text" in record:
                panel.ocr_text = str(record.get("ocr_text") or "")
            if record.get("created_at"):
                panel.created_at = str(record["created_at"])
            if record.get("updated_at"):
                panel.updated_at = str(record["updated_at"])

    def export_catalog(self, dest: PathLike) -> dict:
        """Write catalog.json, panels.csv, and one PNG per panel id."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        json_path = self.write_catalog(dest / "catalog.json")
        csv_path = dest / "panels.csv"
        fields = [
            "id",
            "page",
            "panel",
            "bbox",
            "source_file",
            "tags",
            "notes",
            "stage",
            "ocr_text",
            "created_at",
            "updated_at",
        ]
        records = self.catalog_records()
        with csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                row = dict(record)
                row["bbox"] = ",".join(str(v) for v in record["bbox"])
                row["tags"] = "|".join(record["tags"])
                writer.writerow(row)
        image_dir = dest / "panels"
        image_dir.mkdir(exist_ok=True)
        cache: dict[str, np.ndarray] = {}
        written = []
        for placement in self.reading_order():
            panel = self.panel(placement.panel_id)
            if panel.source_id not in cache:
                cache[panel.source_id] = self.view_gray(panel.source_id)
            gray = cache[panel.source_id]
            crop = np.clip(gray[panel.y0 : panel.y1, panel.x0 : panel.x1], 0, 255).astype(np.uint8)
            if crop.size == 0:
                crop = np.full((8, 8), 255, dtype=np.uint8)
            path = image_dir / f"{self.catalog_id(placement)}.png"
            Image.fromarray(crop, "L").save(path)
            written.append(path)
        return {"json": json_path, "csv": csv_path, "images": written}

    def _export_placements(self, placement_ids, board_pages) -> list[Placement]:
        ordered = self.reading_order()
        if placement_ids is not None:
            wanted = set(placement_ids)
            return [p for p in ordered if p.id in wanted or p.panel_id in wanted]
        if board_pages is not None:
            wanted = {int(n) for n in board_pages}
            return [p for p in ordered if p.board_page in wanted]
        return ordered

    def _compose_page(self, placements: list[Placement], opacity: float, packed: bool) -> Image.Image:
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
        for index, placement in enumerate(placements):
            panel = self.panel(placement.panel_id)
            slot = index if packed else placement.order
            col = slot % cols
            row = slot // cols
            if row >= rows or col >= cols:
                continue
            crop = self._ghost_crop(panel, opacity)
            fitted = _fit(crop, cell_w, cell_h)
            x = art[0] + col * (cell_w + gap) + (cell_w - fitted.width) // 2
            y = art[1] + row * (cell_h + gap) + (cell_h - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        _draw_marks(canvas, dpi)
        return canvas

    def _ghost_crop(self, panel: Panel, opacity: float) -> Image.Image:
        gray = self.view_gray(panel.source_id)
        x0, y0, x1, y1 = self.crop_box(panel)
        crop = np.asarray(gray[y0:y1, x0:x1], dtype=np.float32)
        if crop.size == 0:
            return Image.new("L", (1, 1), 255)
        light = lighten_crop(crop, opacity)
        arr = np.clip(light * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, "L")

    def attach_ink(self, path: PathLike, pencil_source_id: str) -> str:
        """Copy an ink scan into the project and link it as another take."""
        self._ensure_root()
        source_id = self._add_file(Path(path), Path(path).stem, take="ink")
        self.link_takes(pencil_source_id, source_id)
        return source_id

    def _import_raster(self, path: Path) -> list[str]:
        with Image.open(path) as image:
            frames = getattr(image, "n_frames", 1)
            if frames == 1:
                return [self._add_file(path, path.stem)]
            added = []
            stem = path.stem
            for index in range(frames):
                image.seek(index)
                gray = np.asarray(image.convert("L"), dtype=np.float32)
                name = f"{stem} p{index + 1}"
                added.append(self._add_gray(gray, name))
            return added

    def _add_file(self, path: Path, name: str, take: str = "pencil") -> str:
        """Copy the file bytes. Deskew is measured and stored, not written back."""
        if not path.is_file():
            raise FileNotFoundError(f"No file at {path}")
        source_id = self._new_id("page")
        image_name = f"{source_id}{path.suffix.lower()}"
        assert self.root is not None
        out = self.root / "sources" / image_name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        return self._remember_source(out, source_id, image_name, name, take)

    def _add_gray(self, gray: np.ndarray, name: str, take: str = "pencil") -> str:
        """Store an unmodified raster page. Used for PDF pages, which have no single file to copy."""
        arr = np.clip(gray, 0, 255).astype(np.uint8)
        source_id = self._new_id("page")
        image_name = f"{source_id}.png"
        assert self.root is not None
        out = self.root / "sources" / image_name
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr, "L").save(out)
        return self._remember_source(out, source_id, image_name, name, take)

    def _remember_source(self, out: Path, source_id: str, image_name: str, name: str, take: str) -> str:
        gray = load_gray(out)
        straight, angle = deskew_array(gray, deskew=True, flatten=False)
        self.sources.append(
            SourcePage(
                id=source_id,
                name=name,
                image_name=image_name,
                width=int(gray.shape[1]),
                height=int(gray.shape[0]),
                view_width=int(straight.shape[1]),
                view_height=int(straight.shape[0]),
                skew_degrees=float(angle),
                take=take,
            )
        )
        return source_id

    def _ensure_root(self) -> None:
        if self.root is None:
            import tempfile

            self.root = Path(tempfile.mkdtemp(prefix="comics-tools-"))

    def _new_id(self, prefix: str) -> str:
        existing = {s.id for s in self.sources} | {p.id for p in self.panels} | {p.id for p in self.placements}
        number = 1
        while f"{prefix}{number}" in existing:
            number += 1
        return f"{prefix}{number}"

    def _compact_orders(self) -> None:
        self._assign_sequential(self.reading_order())

    def _new_panel(self, source_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        return Panel(id=self._new_id("panel"), source_id=source_id, x0=x0, y0=y0, x1=x1, y1=y1)

    def _capture(self) -> dict:
        return {
            "panels": [asdict(panel) for panel in self.panels],
            "placements": [asdict(item) for item in self.placements],
        }

    def _restore(self, state: dict) -> None:
        self.panels = [Panel(**raw) for raw in state["panels"]]
        self.placements = [Placement(**raw) for raw in state["placements"]]

    def _assign_sequential(self, ordered: list) -> None:
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


def _clean_tags(tags: Sequence[str]) -> list[str]:
    cleaned = []
    seen = set()
    for raw in tags:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


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
