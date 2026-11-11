"""Edit decision list for the comicsTools window.

A project folder holds untouched copies of the scans plus ``project.json``.
A panel is a clip: source file, crop rectangle, deskew angle, and cleanup
settings. Page layouts reference those clips. Rendering is done when you
export. The source files are not written again after import.
"""
from __future__ import annotations

import csv
import functools
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw

from .deskew import deskew_array, rotate_image
from .mark_cleanup import ART, MARK_D, MARK_INSET, MARK_R, PAGE_H, PAGE_W
from .panels import find_panel_boxes
from .printprep import PRESETS, _lighten_pencils
from .printer_test import DEFAULT_LEVELS
from .register import Alignment, RegistrationError, match_score, page_signature, register, warp_take
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
    # For a take lined up on its pencil scan: register.Alignment.to_dict() plus the
    # pencil's angle it was measured at. The take is then drawn in the pencil's frame.
    alignment: dict = field(default_factory=dict)

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
class Page:
    """One layout page. Zero means "use the project default" for that setting."""

    id: str
    name: str = ""
    width_in: float = 0.0
    height_in: float = 0.0
    margin_in: float = 0.0
    gutter_in: float = 0.0
    columns: int = 0
    rows: int = 0


UNDO_LIMIT = 40


def undoable(fn):
    """One history step per call: taken only if the call succeeds and changed something.

    A call that raises puts the project back exactly as it was, so a refused edit
    (say, merging panels from two scans) leaves no phantom step and keeps redo.
    Edits called from inside another edit join the outer step.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if self._depth:
            return fn(self, *args, **kwargs)
        before = self._capture()
        self._depth += 1
        try:
            result = fn(self, *args, **kwargs)
        except BaseException:
            self._restore(before)
            raise
        finally:
            self._depth -= 1
        if self._capture() != before:
            self._undo.append(before)
            self._redo.clear()
            del self._undo[:-UNDO_LIMIT]
        return result

    return wrapper


@dataclass
class Project:
    name: str = "Untitled"
    root: Optional[Path] = None
    sources: list[SourcePage] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    opacity: str = "light"
    columns: int = 2
    rows: int = 3
    bleed: int = 12
    dpi: int = 300

    def __post_init__(self) -> None:
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._view_cache: dict = {}
        self._ghost_cache: dict = {}
        self._depth = 0
        self._next_id = 0  # ids are never handed out twice, even after a delete
        self._ensure_pages(1)

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

    def page(self, index: int) -> Page:
        return self.pages[int(index)]

    def page_count(self) -> int:
        return max(1, len(self.pages))

    def page_name(self, index: int) -> str:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        return page.name if page and page.name else f"Page {int(index) + 1}"

    def page_size(self, index: int) -> tuple[float, float]:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        width = (page.width_in if page and page.width_in else 0.0) or PAGE_W
        height = (page.height_in if page and page.height_in else 0.0) or PAGE_H
        return float(width), float(height)

    def page_margin(self, index: int) -> float:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        return float((page.margin_in if page and page.margin_in else 0.0) or 0.75)

    def page_gutter(self, index: int) -> float:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        return float((page.gutter_in if page and page.gutter_in else 0.0) or 0.12)

    def page_columns(self, index: int) -> int:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        return max(1, int((page.columns if page and page.columns else 0) or self.columns))

    def page_rows(self, index: int) -> int:
        page = self.pages[int(index)] if 0 <= int(index) < len(self.pages) else None
        return max(1, int((page.rows if page and page.rows else 0) or self.rows))

    def page_slots(self, index: int) -> int:
        return max(1, self.page_columns(index) * self.page_rows(index))

    def _ensure_pages(self, count: int) -> None:
        if not self.pages:
            self.pages.append(Page(id=self._new_id("sheet")))
        while len(self.pages) < max(1, int(count)):
            self.pages.append(Page(id=self._new_id("sheet")))

    def _shift_pages(self, from_index: int, delta: int) -> None:
        """Move every placement on/after a page index by delta, for insert/delete/reorder."""
        for placement in self.placements:
            if placement.board_page >= from_index:
                placement.board_page += delta

    @undoable
    def add_page(self, index: Optional[int] = None, name: str = "") -> Page:
        """Insert a blank page at ``index`` (default: after the last page)."""
        index = self.page_count() if index is None else max(0, min(int(index), self.page_count()))
        self._shift_pages(index, 1)
        page = Page(id=self._new_id("sheet"), name=name)
        self.pages.insert(index, page)
        return page

    @undoable
    def duplicate_page(self, index: int) -> Page:
        """Copy a page and its placements; clips and source scans are shared, not copied."""
        index = max(0, min(int(index), self.page_count() - 1))
        source = self.page(index)
        self._shift_pages(index + 1, 1)
        page = Page(
            id=self._new_id("sheet"),
            name=source.name,
            width_in=source.width_in,
            height_in=source.height_in,
            margin_in=source.margin_in,
            gutter_in=source.gutter_in,
            columns=source.columns,
            rows=source.rows,
        )
        self.pages.insert(index + 1, page)
        clones = []
        for placement in self.placements_on(index):
            clones.append(
                Placement(
                    id=self._new_id("use"),
                    panel_id=placement.panel_id,
                    board_page=index + 1,
                    order=placement.order,
                )
            )
        self.placements.extend(clones)
        return page

    @undoable
    def delete_page(self, index: int) -> list[Placement]:
        """Delete a page and the placements on it. The last page can't be deleted."""
        index = int(index)
        if self.page_count() <= 1:
            raise ValueError("A project needs at least one page.")
        if not 0 <= index < self.page_count():
            raise IndexError(index)
        removed = [p for p in self.placements if p.board_page == index]
        self.placements = [p for p in self.placements if p.board_page != index]
        self._shift_pages(index + 1, -1)
        del self.pages[index]
        return removed

    @undoable
    def move_page(self, from_index: int, to_index: int) -> None:
        """Move a page (and everything on it) to another position."""
        last = self.page_count() - 1
        source, target = int(from_index), max(0, min(int(to_index), last))
        if source == target:
            return
        self.pages.insert(target, self.pages.pop(source))
        for placement in self.placements:
            if placement.board_page == source:
                placement.board_page = target
            elif source < target and source < placement.board_page <= target:
                placement.board_page -= 1
            elif target < source and target <= placement.board_page < source:
                placement.board_page += 1

    @undoable
    def reorder_pages(self, page_ids: Sequence[str]) -> None:
        """Apply a new page order, given every page id once."""
        page_ids = list(page_ids)
        if len(page_ids) != len(self.pages) or set(page_ids) != {p.id for p in self.pages}:
            raise ValueError("The new page order has to include every page once.")
        old_pages = list(self.pages)
        old_index = {page.id: index for index, page in enumerate(old_pages)}
        page_for_placement = {}
        for placement in self.placements:
            page = old_pages[placement.board_page] if 0 <= placement.board_page < len(old_pages) else old_pages[0]
            page_for_placement[placement.id] = page.id
        new_index = {page_id: index for index, page_id in enumerate(page_ids)}
        self.pages = [old_pages[old_index[page_id]] for page_id in page_ids]
        for placement in self.placements:
            placement.board_page = new_index[page_for_placement[placement.id]]

    @undoable
    def set_page_settings(
        self,
        index: int,
        *,
        name: Optional[str] = None,
        width_in: Optional[float] = None,
        height_in: Optional[float] = None,
        margin_in: Optional[float] = None,
        gutter_in: Optional[float] = None,
        columns: Optional[int] = None,
        rows: Optional[int] = None,
        all_pages: bool = False,
    ) -> None:
        """Resize or re-grid one page, or every page, without changing the scans."""
        index = int(index)
        if not 0 <= index < len(self.pages):
            raise IndexError(index)
        if name is not None:
            self.pages[index].name = str(name).strip()
        targets = self.pages if all_pages else [self.pages[index]]
        for page in targets:
            if width_in is not None:
                width = float(width_in)
                if not 2.0 <= width <= 30.0:
                    raise ValueError("Page width must be from 2 to 30 inches.")
                page.width_in = width
            if height_in is not None:
                height = float(height_in)
                if not 2.0 <= height <= 30.0:
                    raise ValueError("Page height must be from 2 to 30 inches.")
                page.height_in = height
            if margin_in is not None:
                margin = float(margin_in)
                limit = min(page.width_in or PAGE_W, page.height_in or PAGE_H) / 2.0
                if not 0.0 <= margin < limit:
                    raise ValueError("Margins must fit inside the page.")
                page.margin_in = margin
            if gutter_in is not None:
                gutter = float(gutter_in)
                if not 0.0 <= gutter <= 3.0:
                    raise ValueError("Gutter must be from 0 to 3 inches.")
                page.gutter_in = gutter
            if columns is not None:
                count = int(columns)
                if not 1 <= count <= 12:
                    raise ValueError("Columns must be from 1 to 12.")
                page.columns = count
            if rows is not None:
                count = int(rows)
                if not 1 <= count <= 12:
                    raise ValueError("Rows must be from 1 to 12.")
                page.rows = count
        changed_layout = (
            columns is not None
            or rows is not None
            or width_in is not None
            or height_in is not None
            or margin_in is not None
            or gutter_in is not None
        )
        if changed_layout:
            pages_to_pack = range(self.page_count()) if all_pages else [index]
            for page_index in pages_to_pack:
                self.pack_page(page_index)

    def pack_page(self, index: int) -> None:
        """Fill a page's slots in reading order; extra placements stay after the slots."""
        index = int(index)
        for order, placement in enumerate(self.placements_on(index)):
            placement.order = order

    def reading_order(self) -> list[Placement]:
        return sorted(self.placements, key=lambda p: (p.board_page, p.order, p.id))

    def placements_on(self, board_page: int) -> list[Placement]:
        return [p for p in self.reading_order() if p.board_page == board_page]

    def placements_of(self, panel_id: str) -> list[Placement]:
        return [p for p in self.reading_order() if p.panel_id == panel_id]

    def board_page_count(self) -> int:
        return self.page_count()

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

    def page_thumbnail(self, index: int, max_size: int = 150, dpi: int = 48) -> Image.Image:
        """A small preview of one page, showing its own size and layout."""
        page = self._compose_page(
            self.placements_on(int(index)),
            1.0,
            packed=False,
            finished=True,
            page_index=int(index),
            dpi_override=int(dpi),
        )
        page.thumbnail((int(max_size), int(max_size)), Image.Resampling.LANCZOS)
        return page

    @undoable
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

    @undoable
    def detect_panels(self, source_id: str, pack: bool = True) -> list[Panel]:
        view = self.view_gray(source_id)
        boxes = [box for box in find_panel_boxes(view) if box_has_ink(view, box)]
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

    @undoable
    def set_rect(self, panel_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        panel = self.panel(panel_id)
        width, height = self.source(panel.source_id).view_size()
        panel.x0, panel.y0, panel.x1, panel.y1 = clamp_box(x0, y0, x1, y1, width, height)
        panel.updated_at = _now()
        return panel

    @undoable
    def add_panel(self, source_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        width, height = self.source(source_id).view_size()
        x0, y0, x1, y1 = clamp_box(x0, y0, x1, y1, width, height)
        panel = self._new_panel(source_id, x0, y0, x1, y1)
        self.panels.append(panel)
        self.place(panel.id, self.board_page_count() - 1 if self.placements else 0, record=False)
        return panel

    @undoable
    def delete_panel(self, panel_id: str) -> None:
        """Remove a clip from the library and every layout that uses it."""
        self.panel(panel_id)
        self.panels = [p for p in self.panels if p.id != panel_id]
        self.placements = [p for p in self.placements if p.panel_id != panel_id]
        self._compact_orders()

    @undoable
    def remove_placement(self, placement_id: str) -> None:
        """Take one use off a layout. The clip stays in the library."""
        self.placement(placement_id)
        self.placements = [p for p in self.placements if p.id != placement_id]
        self._compact_orders()

    @undoable
    def place(self, panel_id: str, board_page: int = 0, record: bool = True) -> Placement:
        """Put a library clip on a layout page. The same clip can be placed again."""
        self.panel(panel_id)
        board_page = max(0, int(board_page))
        self._ensure_pages(board_page + 1)
        item = Placement(
            id=self._new_id("use"),
            panel_id=panel_id,
            board_page=board_page,
            order=10_000,
        )
        self.placements.append(item)
        self._compact_orders()
        return item

    @undoable
    def duplicate_panel(self, panel_id: str) -> Placement:
        """Place the same clip again. This does not copy or rewrite pixels."""
        current = self.placements_of(panel_id)
        page = current[0].board_page if current else 0
        return self.place(panel_id, page)

    @undoable
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

    @undoable
    def merge_panels(self, first_id: str, second_id: str) -> Panel:
        if first_id == second_id:
            raise ValueError("Choose two different panels to merge.")
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

    @undoable
    def move_by(self, placement_id: str, steps: int) -> None:
        ordered = self.reading_order()
        index = next(i for i, item in enumerate(ordered) if item.id == placement_id)
        target = index + steps
        if target < 0 or target >= len(ordered):
            self._undo.pop()
            return
        first, second = ordered[index], ordered[target]
        first.board_page, second.board_page = second.board_page, first.board_page
        first.order, second.order = second.order, first.order

    @undoable
    def move_to_page(self, placement_id: str, board_page: int) -> None:
        if board_page < 0:
            return
        item = self.placement(placement_id)
        item.board_page = int(board_page)
        item.order = 10_000
        self._ensure_pages(int(board_page) + 1)
        self._compact_orders()

    @undoable
    def set_reading_order(self, pairs: Sequence[tuple[str, int]]) -> None:
        seen = [pid for pid, _page in pairs]
        if set(seen) != {p.id for p in self.placements} or len(seen) != len(self.placements):
            raise ValueError("The new order has to include every layout use once.")
        counts: dict[int, int] = {}
        for pid, page in pairs:
            item = self.placement(pid)
            item.board_page = int(page)
            item.order = counts.get(int(page), 0)
            counts[int(page)] = item.order + 1
            self._ensure_pages(int(page) + 1)

    @undoable
    def pack_grid(self) -> None:
        """Lay every clip out in reading order, using each page's own grid."""
        page_index = 0
        slot = 0
        for item in self.reading_order():
            while slot >= self.page_slots(page_index):
                page_index += 1
                slot = 0
                self._ensure_pages(page_index + 1)
            item.board_page = page_index
            item.order = slot
            slot += 1

    @undoable
    def link_takes(self, pencil_id: str, ink_id: str) -> None:
        """Treat two scans as pencil and ink of the same page."""
        if pencil_id == ink_id:
            raise ValueError("Pick two different scans.")
        pencil = self.source(pencil_id)
        ink = self.source(ink_id)
        pencil.take = "pencil"
        ink.take = "ink"
        pencil.linked_id = ink.id
        ink.linked_id = pencil.id
        ink.alignment = {}
        ink.view_width = 0
        ink.view_height = 0

    def _current_alignment(self, source: SourcePage) -> Optional[Alignment]:
        """The take's alignment, if it was measured against the pencil as it is now."""
        if source.take == "pencil" or not source.alignment or not source.linked_id:
            return None
        pencil = self.source(source.linked_id)
        if round(float(source.alignment.get("pencil_skew", 0.0)), 4) != round(float(pencil.skew_degrees), 4):
            return None  # the pencil was straightened again since; align_take() again
        return Alignment.from_dict(source.alignment)

    @undoable
    def align_take(self, take_id: str) -> Alignment:
        """Line a take up on its linked pencil scan. Raises RegistrationError if they don't match."""
        take = self.source(take_id)
        if not take.linked_id or take.take == "pencil":
            raise ValueError("Link this scan to its pencil scan first.")
        pencil = self.source(take.linked_id)
        alignment = register(self.view_gray(pencil.id), load_gray(self.image_path(take_id)))
        take.alignment = {**alignment.to_dict(), "pencil_skew": float(pencil.skew_degrees)}
        take.view_width, take.view_height = pencil.view_size()
        return alignment

    @undoable
    def link_and_align(self, take_ids: Sequence[str], min_score: int = 60) -> list[tuple[str, Optional[str], str]]:
        """Pair each take with the pencil scan it shows, link them, and line them up.

        Takes don't have to be in the pencil scans' order: BLUE175's finished PDF has
        pages 3 and 4 the other way round from the pencil PDF. A pencil page already
        linked to a take is not offered again. Returns (take, pencil or None, note).
        """
        pencils = [s for s in self.sources if s.take == "pencil" and s.id not in take_ids and not s.linked_id]
        pencil_sigs = {s.id: page_signature(self.view_gray(s.id)) for s in pencils}
        scored = []
        for tid in take_ids:
            sig = page_signature(load_gray(self.image_path(tid)))
            for pid, psig in pencil_sigs.items():
                scored.append((match_score(sig, psig), tid, pid))
        scored.sort(reverse=True)
        used_takes: set[str] = set()
        used_pencils: set[str] = set()
        pairs: dict[str, tuple[str, int]] = {}
        for score, tid, pid in scored:
            if score < min_score or tid in used_takes or pid in used_pencils:
                continue
            pairs[tid] = (pid, score)
            used_takes.add(tid)
            used_pencils.add(pid)
        results: list[tuple[str, Optional[str], str]] = []
        for tid in take_ids:
            if tid not in pairs:
                self.source(tid).take = "ink"
                results.append((tid, None, "no pencil scan matched this page"))
                continue
            pid, _score = pairs[tid]
            self.link_takes(pid, tid)
            try:
                alignment = self.align_take(tid)
                results.append((tid, pid, f"lined up, {alignment.median_error_px:.1f} px"))
            except RegistrationError as exc:
                results.append((tid, pid, f"linked, not lined up: {exc}"))
        return results

    @undoable
    def import_takes(self, paths: Sequence[PathLike]) -> list[tuple[str, Optional[str], str]]:
        """Import ink scans or finished pages (files or a PDF) and pair them with their pencils."""
        self._ensure_root()
        added: list[str] = []
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                raise FileNotFoundError(f"No file at {path}")
            if path.suffix.lower() == ".pdf":
                added.extend(self._import_pdf(path, take="ink"))
            else:
                added.append(self._add_file(path, path.stem, take="ink"))
        return self.link_and_align(added)

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
        """Deskewed view. The file on disk is not rotated.

        Cached per source and angle, because one export reads each scan once per
        crop. The array is read-only so a caller can't change the cached copy.
        """
        source = self.source(source_id)
        aligned = self._current_alignment(source)
        key = (
            source_id,
            source.image_name,
            round(float(source.skew_degrees), 4),
            tuple(aligned.homography) if aligned else None,
            json.dumps(aligned.offsets) if aligned else None,
            tuple(aligned.target_size) if aligned else None,
        )
        cached = self._view_cache.get(key)
        if cached is not None:
            self._view_cache[key] = self._view_cache.pop(key)  # most recent last
            return cached
        gray = load_gray(self.image_path(source_id))
        if aligned is not None:
            # A lined-up take is drawn in its pencil scan's frame, so the pencil's boxes fit it.
            width, height = self.source(source.linked_id).view_size()
            gray = warp_take(gray, aligned, width, height)
        elif abs(source.skew_degrees) >= 0.05:
            gray = rotate_image(gray, source.skew_degrees)
        gray = np.asarray(gray)
        gray.setflags(write=False)
        self._view_cache[key] = gray
        while len(self._view_cache) > 12:
            del self._view_cache[next(iter(self._view_cache))]
        return gray

    def checkpoint(self) -> None:
        """Record a history step now. Edits record their own; this is for callers
        that change fields directly. Inside an edit it does nothing."""
        if self._depth:
            return
        self._undo.append(self._capture())
        self._redo.clear()
        del self._undo[:-UNDO_LIMIT]

    @undoable
    def set_settings(
        self,
        *,
        opacity=None,
        columns: Optional[int] = None,
        rows: Optional[int] = None,
        bleed: Optional[int] = None,
        dpi: Optional[int] = None,
    ) -> None:
        """Change print settings as one undoable step. Values are checked first."""
        if opacity is not None:
            if isinstance(opacity, str):
                if opacity not in PRESETS:
                    raise ValueError(f"Opacity must be one of {', '.join(PRESETS)} or a number from 0 to 1.")
            elif not 0.0 <= float(opacity) <= 1.0:
                raise ValueError("Opacity must be from 0 to 1.")
        grid = (int(columns if columns is not None else self.columns), int(rows if rows is not None else self.rows))
        if grid not in LAYOUTS:
            raise ValueError(f"Layout must be one of {', '.join(f'{c}x{r}' for c, r in LAYOUTS)}.")
        if bleed is not None and not 0 <= int(bleed) <= 300:
            raise ValueError("Bleed must be from 0 to 300 px.")
        if dpi is not None and not 72 <= int(dpi) <= 1200:
            raise ValueError("DPI must be from 72 to 1200.")
        if opacity is not None:
            self.opacity = opacity
        if bleed is not None:
            self.bleed = int(bleed)
        if dpi is not None:
            self.dpi = int(dpi)
        if grid != (self.columns, self.rows):
            self.columns, self.rows = grid
            if self.placements:
                self.pack_grid()

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
            pages = [
                self._compose_page(group, opacity, packed=True, page_index=index)
                for index, group in enumerate(groups)
            ]
        else:
            page_numbers = sorted({p.board_page for p in chosen}) if board_pages is not None else list(range(self.page_count()))
            pages = []
            for number in page_numbers:
                group = [p for p in chosen if p.board_page == number]
                pages.append(self._compose_page(group, opacity, packed=False, page_index=number))
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

    def write_layout_image(self, board_page: int, dest: PathLike, finished: bool = False) -> Path:
        """Render one layout page to a PNG without changing the sources."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        page = self._compose_page(
            self.placements_on(board_page),
            _opacity_value(self.opacity),
            packed=False,
            finished=finished,
            page_index=int(board_page),
        )
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
        tmp = target / "project.json.tmp"
        tmp.write_text(json.dumps(self._payload(), indent=2) + "\n")
        tmp.replace(target / "project.json")  # a crash mid-write leaves the old file whole
        self.write_catalog(target / "catalog.json")
        return target

    def _payload(self) -> dict:
        return {
            "version": 3,
            "name": self.name,
            "opacity": self.opacity,
            "columns": self.columns,
            "rows": self.rows,
            "bleed": self.bleed,
            "dpi": self.dpi,
            "sources": [asdict(source) for source in self.sources],
            "panels": [asdict(panel) for panel in self.panels],
            "placements": [asdict(item) for item in self.placements],
            "pages": [asdict(page) for page in self.pages],
            "next_id": self._next_id,
        }

    def state_signature(self) -> str:
        """Everything Save writes, as one string. Equal strings mean nothing to save."""
        return json.dumps(self._payload(), sort_keys=True)

    def has_folder(self) -> bool:
        """True once the project has been saved to a folder the user chose."""
        return self.root is not None and not Path(self.root).name.startswith("comics-tools-")

    @classmethod
    def load(cls, folder: PathLike) -> "Project":
        folder = Path(folder)
        path = folder / "project.json"
        if not path.is_file():
            raise FileNotFoundError(f"No project.json in {folder}")
        data = json.loads(path.read_text())
        version = data.get("version")
        if version not in (1, 2, 3):
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
        project.pages = []
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
        if version >= 3:
            project.pages = [
                Page(**{k: v for k, v in raw.items() if k in Page.__dataclass_fields__})
                for raw in data.get("pages") or []
            ]
        needed = max([p.board_page for p in project.placements] + [0]) + 1
        project._ensure_pages(needed if project.pages else max(1, needed))
        project._next_id = max(int(data.get("next_id") or 0), project._highest_id() + 1)
        catalog_path = folder / "catalog.json"
        if catalog_path.is_file():
            project.apply_catalog(json.loads(catalog_path.read_text()))
        project._undo.clear()
        project._redo.clear()
        return project

    @undoable
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

    def lettering_image(self, panel_id: str) -> Image.Image:
        """The crop an OCR pass would read, drawn from the clip's own take."""
        panel = self.panel(panel_id)
        gray = self.view_gray(self.render_source(panel).id)
        crop = np.asarray(gray[panel.y0 : panel.y1, panel.x0 : panel.x1])
        if crop.size == 0:
            return Image.new("L", (8, 8), 255)
        return Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8), "L")

    def apply_lettering(self, panel_id: str, text: str) -> None:
        """Save lettering on the clip. One undo step, like a typed edit."""
        self.set_meta(panel_id, ocr_text=str(text))

    def read_lettering(self, panel_id: str):
        from .ocr_text import recognize

        result = recognize(self.lettering_image(panel_id))
        if result.available:
            self.apply_lettering(panel_id, result.text)
        return result

    def write_catalog(self, dest: PathLike) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "panels": self.catalog_records()}
        dest.write_text(json.dumps(payload, indent=2) + "\n")
        return dest

    @undoable
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
            source = self.render_source(panel)
            if source.id not in cache:
                cache[source.id] = self.view_gray(source.id)
            gray = cache[source.id]
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

    def write_pages(self, dest: PathLike, fmt: str = "pdf") -> list[Path]:
        """Finished pages: every layout page at full strength, no corner marks.

        ``pdf`` writes one multi-page file at ``dest``. ``png`` and ``tiff`` write
        ``page-001.png`` and so on into the folder ``dest``. Clips set to the ink
        stage render from their linked ink scan. The scans are not changed.
        """
        fmt = fmt.lower().lstrip(".")
        if fmt == "tif":
            fmt = "tiff"
        if fmt not in ("pdf", "png", "tiff"):
            raise ValueError("Pages export as pdf, png, or tiff.")
        if not self.placements:
            raise ValueError("There is no page layout to export.")
        numbers = list(range(self.page_count()))
        pages = [
            self._compose_page(self.placements_on(n), 1.0, packed=False, finished=True, page_index=n)
            for n in numbers
        ]
        dest = Path(dest)
        if fmt == "pdf":
            dest.parent.mkdir(parents=True, exist_ok=True)
            save_pages_as_pdf(pages, dest, resolution=float(self.dpi))
            return [dest]
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for number, page in zip(numbers, pages):
            path = dest / f"page-{number + 1:03d}.{'tif' if fmt == 'tiff' else 'png'}"
            if fmt == "tiff":
                page.save(path, dpi=(self.dpi, self.dpi), compression="tiff_lzw")
            else:
                page.save(path, dpi=(self.dpi, self.dpi))
            written.append(path)
        return written

    def _finished_crop(self, panel: Panel) -> Image.Image:
        take = self.render_source(panel)
        gray = self.view_gray(take.id)
        x0, y0, x1, y1 = self.crop_box(panel)
        crop = np.asarray(gray[y0:y1, x0:x1])
        if crop.size == 0:
            return Image.new("L", (1, 1), 255)
        return Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8), "L")

    def _compose_page(
        self,
        placements: list[Placement],
        opacity: float,
        packed: bool,
        finished: bool = False,
        page_index: int = 0,
        dpi_override: Optional[int] = None,
    ) -> Image.Image:
        dpi = int(dpi_override or self.dpi)
        width_in, height_in = self.page_size(page_index)
        width, height = _px(width_in, dpi), _px(height_in, dpi)
        canvas = Image.new("L", (width, height), 255)
        margin = self.page_margin(page_index)
        art = (
            _px(margin, dpi),
            _px(margin, dpi),
            _px(width_in - margin, dpi),
            _px(height_in - margin, dpi),
        )
        gap = _px(self.page_gutter(page_index), dpi)
        cols, rows = self.page_columns(page_index), self.page_rows(page_index)
        cell_w = max(1, (art[2] - art[0] - gap * (cols - 1)) // cols)
        cell_h = max(1, (art[3] - art[1] - gap * (rows - 1)) // rows)
        for index, placement in enumerate(placements):
            panel = self.panel(placement.panel_id)
            slot = index if packed else placement.order
            col = slot % cols
            row = slot // cols
            if row >= rows or col >= cols:
                continue
            crop = self._finished_crop(panel) if finished else self._ghost_crop(panel, opacity)
            fitted = _fit(crop, cell_w, cell_h)
            x = art[0] + col * (cell_w + gap) + (cell_w - fitted.width) // 2
            y = art[1] + row * (cell_h + gap) + (cell_h - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        if not finished:
            _draw_marks(canvas, dpi, width_in, height_in)
        return canvas

    def overflow_pages(self) -> list[tuple[int, int, int]]:
        """Pages with more placements than their grid holds: (page, placements, slots)."""
        overflow = []
        for index in range(self.page_count()):
            count = len(self.placements_on(index))
            slots = self.page_slots(index)
            if count > slots:
                overflow.append((index, count, slots))
        return overflow

    def _ghost_crop(self, panel: Panel, opacity: float) -> Image.Image:
        # The paper-tone filter is most of an export's time (13.4 of 15.0 s on 3 real
        # pages, 11-10), so a crop is kept until its scan, angle, box, or opacity changes.
        source = self.source(panel.source_id)
        box = self.crop_box(panel)
        key = (panel.source_id, source.image_name, round(float(source.skew_degrees), 4), box, round(float(opacity), 4))
        cached = self._ghost_cache.get(key)
        if cached is not None:
            return cached
        gray = self.view_gray(panel.source_id)
        x0, y0, x1, y1 = box
        crop = np.asarray(gray[y0:y1, x0:x1], dtype=np.float32)
        if crop.size == 0:
            return Image.new("L", (1, 1), 255)
        light = lighten_crop(crop, opacity)
        arr = np.clip(light * 255.0 + 0.5, 0, 255).astype(np.uint8)
        image = Image.fromarray(arr, "L")
        self._ghost_cache[key] = image
        while len(self._ghost_cache) > 400:
            del self._ghost_cache[next(iter(self._ghost_cache))]
        return image

    @undoable
    def attach_ink(self, path: PathLike, pencil_source_id: str) -> str:
        """Copy an ink scan into the project and link it as another take."""
        self._ensure_root()
        source_id = self._add_file(Path(path), Path(path).stem, take="ink")
        self.link_takes(pencil_source_id, source_id)
        try:
            self.align_take(source_id)
        except RegistrationError:
            pass  # still linked; the crops use the take as scanned
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
        """A number never used before in this project. Deleting panel1 and adding a panel
        gives a new number, so a catalog id from an old export can't land on a new clip."""
        number = max(self._next_id, self._highest_id() + 1, 1)
        self._next_id = number + 1
        return f"{prefix}{number}"

    def _highest_id(self) -> int:
        best = 0
        for item in (*self.sources, *self.panels, *self.placements, *self.pages):
            match = re.search(r"(\d+)$", item.id)
            if match:
                best = max(best, int(match.group(1)))
        return best

    def _compact_orders(self) -> None:
        self._assign_sequential(self.reading_order())

    def _new_panel(self, source_id: str, x0: int, y0: int, x1: int, y1: int) -> Panel:
        return Panel(id=self._new_id("panel"), source_id=source_id, x0=x0, y0=y0, x1=x1, y1=y1)

    def _capture(self) -> dict:
        """The whole project as saved: sources and takes, panels, placements, settings, id counter."""
        return json.loads(json.dumps(self._payload()))

    def _restore(self, state: dict) -> None:
        self.name = state["name"]
        self.opacity = state["opacity"]
        self.columns = int(state["columns"])
        self.rows = int(state["rows"])
        self.bleed = int(state["bleed"])
        self.dpi = int(state["dpi"])
        self.sources = [SourcePage(**raw) for raw in state["sources"]]
        self.panels = [Panel(**raw) for raw in state["panels"]]
        self.placements = [Placement(**raw) for raw in state["placements"]]
        self.pages = [Page(**raw) for raw in state.get("pages") or []]
        self._next_id = int(state.get("next_id") or 0)
        self._ensure_pages(1)

    def _assign_sequential(self, ordered: list) -> None:
        by_page: dict[int, list[Panel]] = {}
        for panel in ordered:
            by_page.setdefault(panel.board_page, []).append(panel)
        for page, group in by_page.items():
            for order, panel in enumerate(group):
                panel.board_page = page
                panel.order = order

    def _import_pdf(self, path: Path, take: str = "pencil") -> list[str]:
        # 150 dpi keeps an imported PDF page small enough to edit.
        # Panel coordinates are pixels of this stored image.
        pages = pdf_to_gray_pages(path, dpi=150)
        added = []
        stem = path.stem
        for index, gray in enumerate(pages, start=1):
            name = stem if len(pages) == 1 else f"{stem} p{index}"
            added.append(self._add_gray(gray, name, take=take))
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


def box_has_ink(gray: np.ndarray, box: Sequence[int], min_fraction: float = 0.0025) -> bool:
    """False for a detected box that is blank paper, such as an empty top margin.

    The outer tenth on each side is skipped so a drawn border alone doesn't count.
    On the 21 pinklightultra.com pages (11-10) the blank margin box measured 0.00% ink
    and the sparsest real content 0.74%, so 0.25% drops only empty paper.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    mx, my = (x1 - x0) // 10, (y1 - y0) // 10
    inner = np.asarray(gray[y0 + my : y1 - my, x0 + mx : x1 - mx])
    if inner.size == 0:
        return True
    return float((inner < 160).mean()) >= min_fraction


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


def _draw_marks(canvas: Image.Image, dpi: int, page_w: float = PAGE_W, page_h: float = PAGE_H) -> None:
    draw = ImageDraw.Draw(canvas)
    half = max(1, _px(MARK_D, dpi) // 2)
    radius = max(1, _px(MARK_R, dpi))
    points = [(MARK_INSET, MARK_INSET), (page_w - MARK_INSET, MARK_INSET),
              (page_w - MARK_INSET, page_h - MARK_INSET), (MARK_INSET, page_h - MARK_INSET)]
    for index, (mx, my) in enumerate(points):
        cx, cy = _px(mx, dpi), _px(my, dpi)
        if index == 0:
            draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=0)
        else:
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=0)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]
