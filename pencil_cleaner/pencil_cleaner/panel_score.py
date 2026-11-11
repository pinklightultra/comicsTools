"""Score detected panel boxes against an answer key.

A detected box *found* a real panel when they overlap by IoU >= 0.8, one to one.
The rest are sorted into what a person would have to fix by hand:

  merged   one detected box holds two or more real panels (needs Split)
  split    one real panel is cut across two or more detected boxes (needs Merge)
  missed   a real panel that no detected box covers even half of (needs Draw panel)
  extra    a detected box that is mostly not on any real panel (needs Delete)
  loose    found the right panel but IoU < 0.8: the crop needs its edges dragged

``fixes`` is the number of hand edits those imply, which is what a cartoonist feels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

Box = Tuple[int, int, int, int]
MATCH_IOU = 0.8


def _area(b: Sequence[int]) -> float:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _inter(a: Sequence[int], b: Sequence[int]) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a: Sequence[int], b: Sequence[int]) -> float:
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u else 0.0


@dataclass
class PageScore:
    truth: int
    detected: int
    found: int = 0
    loose: int = 0
    merged: int = 0
    split: int = 0
    missed: int = 0
    extra: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def fixes(self) -> int:
        return self.loose + self.merged + self.split + self.missed + self.extra

    def as_dict(self) -> Dict[str, int]:
        return {k: getattr(self, k) for k in ("truth", "detected", "found", "loose", "merged", "split", "missed", "extra", "fixes")}


def score_page(detected: Sequence[Sequence[int]], truth: Sequence[Sequence[int]]) -> PageScore:
    det = [tuple(int(v) for v in b) for b in detected]
    tru = [tuple(int(v) for v in b) for b in truth]
    s = PageScore(truth=len(tru), detected=len(det))
    pairs = sorted(((iou(d, t), i, j) for i, d in enumerate(det) for j, t in enumerate(tru)), reverse=True)
    used_d, used_t = set(), set()
    for v, i, j in pairs:
        if v < MATCH_IOU:
            break
        if i in used_d or j in used_t:
            continue
        used_d.add(i)
        used_t.add(j)
    s.found = len(used_t)
    left_d = [i for i in range(len(det)) if i not in used_d]
    left_t = [j for j in range(len(tru)) if j not in used_t]
    # what each leftover detected box holds, and what covers each leftover real panel
    holds = {i: [j for j in range(len(tru)) if _area(tru[j]) and _inter(det[i], tru[j]) >= 0.7 * _area(tru[j])] for i in left_d}
    pieces = {j: [i for i in range(len(det)) if _area(det[i]) and _inter(det[i], tru[j]) >= 0.7 * _area(det[i])] for j in left_t}
    explained_t, explained_d = set(), set()
    for i, js in holds.items():
        if len(js) >= 2:
            s.merged += 1
            explained_d.add(i)
            explained_t.update(j for j in js if j not in used_t)
    for j, is_ in pieces.items():
        if j in explained_t:
            continue
        if len(is_) >= 2:
            s.split += 1
            explained_t.add(j)
            explained_d.update(i for i in is_ if i not in used_d)
    for j in left_t:
        if j in explained_t:
            continue
        best = max((_inter(det[i], tru[j]) / _area(tru[j]) for i in range(len(det))), default=0.0) if _area(tru[j]) else 0.0
        best_iou = max((iou(det[i], tru[j]) for i in left_d if i not in explained_d), default=0.0)
        if best_iou >= 0.4:
            s.loose += 1
            explained_t.add(j)
            explained_d.add(max((i for i in left_d if i not in explained_d), key=lambda i: iou(det[i], tru[j])))
        elif best < 0.5:
            s.missed += 1
            explained_t.add(j)
        else:
            s.loose += 1  # covered, but by a box that is mostly something else
            explained_t.add(j)
    for i in left_d:
        if i in explained_d:
            continue
        on_truth = sum(_inter(det[i], t) for t in tru) / _area(det[i]) if _area(det[i]) else 0.0
        if on_truth < 0.3:
            s.extra += 1
    return s


def score_pages(pages: Dict[str, Tuple[Sequence, Sequence]]) -> Dict[str, object]:
    """pages: name -> (detected boxes, truth boxes). Returns totals plus per page."""
    per = {name: score_page(d, t) for name, (d, t) in pages.items()}
    total = PageScore(truth=0, detected=0)
    for p in per.values():
        for k in ("truth", "detected", "found", "loose", "merged", "split", "missed", "extra"):
            setattr(total, k, getattr(total, k) + getattr(p, k))
    return {
        "total": total.as_dict(),
        "found_rate": total.found / total.truth if total.truth else 0.0,
        "pages": {k: v.as_dict() for k, v in per.items()},
    }
