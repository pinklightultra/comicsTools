"""Score panel detection on a project against a checked answer key.

The key is a JSON file of panel boxes drawn on pencil scans:

    {"<any id>": {"source": "<pencil source name>", "boxes": [[x0, y0, x1, y1], ...], "ok": true}, ...}

Only entries marked ``ok`` (checked by a person) are scored. Each box is scored on
the pencil scan, and, when the pencil has a lined-up take, carried through the
alignment onto the take's own pixels and scored there too. One key then measures
detection on every take of the page: pencil, ink, cleaned.

    python -m pencil_cleaner.benchmark PROJECT_FOLDER KEY.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

import numpy as np

from ._pageio import load_gray
from .panel_score import score_pages
from .panels import find_panel_boxes
from .register import Alignment, _remap_grids

Box = Tuple[int, int, int, int]


def carry_box(box: Sequence[int], mx: np.ndarray, my: np.ndarray, samples: int = 25) -> Box:
    """A box in the pencil frame, as the bounding box of its edges in the take's pixels."""
    x0, y0, x1, y1 = (int(v) for v in box)
    h, w = mx.shape
    x1, y1 = min(x1, w), min(y1, h)
    xs = np.r_[np.linspace(x0, x1 - 1, samples), np.full(samples, x1 - 1), np.linspace(x0, x1 - 1, samples), np.full(samples, x0)]
    ys = np.r_[np.full(samples, y0), np.linspace(y0, y1 - 1, samples), np.full(samples, y1 - 1), np.linspace(y0, y1 - 1, samples)]
    xs, ys = xs.astype(int), ys.astype(int)
    qx, qy = mx[ys, xs], my[ys, xs]
    return (int(qx.min()), int(qy.min()), int(qx.max()) + 1, int(qy.max()) + 1)


def default_detect(gray: np.ndarray) -> list:
    from .studio_project import box_has_ink

    return [b for b in find_panel_boxes(gray) if box_has_ink(gray, b)]


def run(project_dir, key_path, detect: Optional[Callable[[np.ndarray], list]] = None) -> Dict[str, dict]:
    from .studio_project import Project

    detect = detect or default_detect
    project = Project.load(project_dir)
    key = json.loads(Path(key_path).read_text())
    by_name = {s.name: s for s in project.sources}
    sets: Dict[str, dict] = {"pencil": {}, "take": {}}
    for page_id, entry in key.items():
        if not entry.get("ok"):
            continue
        pencil = by_name[entry["source"]]
        boxes = entry["boxes"]
        sets["pencil"][page_id] = (detect(project.view_gray(pencil.id)), boxes)
        if not pencil.linked_id:
            continue
        take = project.source(pencil.linked_id)
        if not take.alignment:
            continue
        raw = load_gray(project.image_path(take.id))
        width, height = pencil.view_size()
        mx, my = _remap_grids(Alignment.from_dict(take.alignment), width, height)
        carried = []
        for b in boxes:
            x0, y0, x1, y1 = carry_box(b, mx, my)
            carried.append((max(0, x0), max(0, y0), min(raw.shape[1], x1), min(raw.shape[0], y1)))
        sets["take"][page_id] = (detect(raw), carried)
    return {name: score_pages(pages) for name, pages in sets.items() if pages}


def _line(name: str, result: dict) -> str:
    t = result["total"]
    return (
        f"{name:7s} found {t['found']}/{t['truth']} ({result['found_rate']:.0%})  hand fixes {t['fixes']}"
        f"  [loose {t['loose']}, merged {t['merged']}, split {t['split']}, missed {t['missed']}, extra {t['extra']}]"
    )


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    for name, result in run(args[0], args[1]).items():
        print(_line(name, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
