"""Line one scan of a page up with another scan of the same page.

Used to put an ink scan (or a cleaned page) on top of its pencil scan, so a panel
box drawn on the pencil cuts the same art out of every take. Nothing is written
back to either scan: the result is a transform that is applied when a take is drawn.

Two steps:
  1. A whole-page fit (perspective) from matched features. This absorbs scale,
     rotation, crop and keystone between scans.
  2. A local touch-up: a coarse grid of small offsets, from dense optical flow
     between the pencil and the fitted take, smoothed hard so it only follows
     paper curl and scanner drift, never the art.

The transform maps a point in the pencil view to a point in the take:
    q = H @ (p + offset(p))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

WORK_SIDE = 1600  # features and flow are measured on copies this size
GRID = (32, 24)  # rows, cols of the touch-up grid


class RegistrationError(RuntimeError):
    """The two scans could not be matched as the same page."""


@dataclass
class Alignment:
    """Pencil-view coordinates to take coordinates, at full resolution."""

    homography: List[float]  # 9 values, row major
    offsets: List[List[List[float]]] = field(default_factory=list)  # GRID rows x cols x (dx, dy), pencil px
    target_size: Tuple[int, int] = (0, 0)  # pencil view (width, height) the offsets were measured on
    inliers: int = 0
    median_error_px: float = 0.0  # at full pencil-view resolution, after the touch-up

    def to_dict(self) -> dict:
        return {
            "homography": [float(v) for v in self.homography],
            "offsets": self.offsets,
            "target_size": list(self.target_size),
            "inliers": int(self.inliers),
            "median_error_px": float(self.median_error_px),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Alignment"]:
        if not data:
            return None
        return cls(
            homography=list(data["homography"]),
            offsets=data.get("offsets") or [],
            target_size=tuple(data.get("target_size") or (0, 0)),
            inliers=int(data.get("inliers", 0)),
            median_error_px=float(data.get("median_error_px", 0.0)),
        )


def _u8(gray: np.ndarray) -> np.ndarray:
    return np.clip(gray, 0, 255).astype(np.uint8)


def _shrink(gray: np.ndarray, side: int = WORK_SIDE) -> Tuple[np.ndarray, float]:
    h, w = gray.shape[:2]
    scale = side / float(max(h, w))
    if scale >= 1.0:
        return _u8(gray), 1.0
    small = cv2.resize(_u8(gray), (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return small, scale


def _match(a: np.ndarray, b: np.ndarray, n: int = 8000) -> Tuple[np.ndarray, np.ndarray]:
    orb = cv2.ORB_create(n)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 20 or len(kb) < 20:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m for m, *rest in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * rest[0].distance]
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    return pa, pb


def _line_image(gray: np.ndarray) -> np.ndarray:
    """What pencil and ink share is where the lines are, not how dark they are."""
    g = _u8(gray)
    paper = float(np.percentile(g, 90))
    lines = np.clip((paper - g.astype(np.float32)) * (255.0 / max(30.0, paper - 40.0)), 0, 255)
    return cv2.GaussianBlur(lines.astype(np.uint8), (0, 0), 2.0)


def _remap_grids(alignment: Alignment, width: int, height: int) -> Tuple[np.ndarray, np.ndarray]:
    """Per-pixel take coordinates for every pixel of a (width x height) pencil view."""
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    if alignment.offsets:
        grid = np.asarray(alignment.offsets, dtype=np.float32)
        tw, th = alignment.target_size
        sx, sy = width / float(tw or width), height / float(th or height)
        dx = cv2.resize(grid[..., 0], (width, height), interpolation=cv2.INTER_CUBIC) * sx
        dy = cv2.resize(grid[..., 1], (width, height), interpolation=cv2.INTER_CUBIC) * sy
        xs, ys = xs + dx, ys + dy
    H = np.asarray(alignment.homography, dtype=np.float64).reshape(3, 3)
    tw, th = alignment.target_size
    if tw and th and (tw, th) != (width, height):
        # the homography was measured on a pencil view of target_size
        S = np.diag([tw / float(width), th / float(height), 1.0])
        H = H @ S
    den = H[2, 0] * xs + H[2, 1] * ys + H[2, 2]
    mx = (H[0, 0] * xs + H[0, 1] * ys + H[0, 2]) / den
    my = (H[1, 0] * xs + H[1, 1] * ys + H[1, 2]) / den
    return mx.astype(np.float32), my.astype(np.float32)


def warp_take(take: np.ndarray, alignment: Alignment, width: int, height: int) -> np.ndarray:
    """Draw the take in the pencil view's frame. Outside the take is white paper."""
    mx, my = _remap_grids(alignment, width, height)
    out = cv2.remap(
        np.asarray(take, dtype=np.float32), mx, my, cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=255.0,
    )
    return out


def page_signature(gray: np.ndarray, side: int = 800):
    """Features of a small copy, kept so one page can be compared with many quickly."""
    small, _ = _shrink(gray, side)
    orb = cv2.ORB_create(3000)
    return orb.detectAndCompute(_line_image(small), None)


def match_score(sig_a, sig_b) -> int:
    """How many features agree on one page shape. The same page scores in the
    hundreds; different BLUE175 pages scored under 10 (11-11)."""
    ka, da = sig_a
    kb, db = sig_b
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m for m, *rest in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * rest[0].distance]
    if len(good) < 10:
        return 0
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    _H, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 3.0)
    return int(mask.sum()) if mask is not None else 0


def tile_shifts(pencil: np.ndarray, warped_take: np.ndarray, tile: int = 128) -> np.ndarray:
    """Sub-pixel shift left over in each tile that has lines in both, in pencil-view pixels.

    Phase correlation per tile, so it measures the lines themselves, not where a
    feature detector happened to put its points (those sit on a coarse pyramid grid).
    """
    a, sa = _shrink(pencil)
    b = cv2.resize(_u8(warped_take), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    la, lb = _line_image(a).astype(np.float32), _line_image(b).astype(np.float32)
    win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    out = []
    for y in range(0, a.shape[0] - tile + 1, tile):
        for x in range(0, a.shape[1] - tile + 1, tile):
            ta, tb = la[y:y + tile, x:x + tile], lb[y:y + tile, x:x + tile]
            if (ta > 40).mean() < 0.03 or (tb > 40).mean() < 0.03:
                continue
            (dx, dy), response = cv2.phaseCorrelate(ta, tb, win)
            if response > 0.15 and abs(dx) < tile / 4 and abs(dy) < tile / 4:
                out.append((dx / sa, dy / sa))
    return np.asarray(out, dtype=np.float32).reshape(-1, 2)


def measure_error(pencil: np.ndarray, warped_take: np.ndarray) -> Tuple[float, int]:
    """Median leftover misalignment across tiles, in pencil-view pixels, and the tile count."""
    shifts = tile_shifts(pencil, warped_take)
    if len(shifts) < 8:
        return float("inf"), int(len(shifts))
    return float(np.median(np.linalg.norm(shifts, axis=1))), int(len(shifts))


def register(pencil: np.ndarray, take: np.ndarray, refine: bool = True, passes: int = 2) -> Alignment:
    """Find the transform from the pencil view to the take."""
    ph, pw = pencil.shape[:2]
    a, sa = _shrink(pencil)
    b, sb = _shrink(take)
    la, lb = _line_image(a), _line_image(b)
    pa, pb = _match(la, lb)
    if len(pa) < 40:
        raise RegistrationError("Too few matching features; these may not be the same page.")
    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    Hs, mask = cv2.findHomography(pa, pb, method, 3.0, maxIters=10000, confidence=0.999)
    if Hs is None or mask is None or int(mask.sum()) < 40:
        raise RegistrationError("The scans did not agree on one page shape; these may not be the same page.")
    inliers = int(mask.sum())
    # full-res pencil -> small pencil -> small take -> full-res take
    H = np.diag([1 / sb, 1 / sb, 1.0]) @ Hs @ np.diag([sa, sa, 1.0])
    alignment = Alignment(homography=H.ravel().tolist(), target_size=(pw, ph), inliers=inliers)
    if refine:
        for _ in range(passes):
            step = _touch_up(pencil, take, alignment)
            if not step:
                break
            alignment.offsets = (np.asarray(alignment.offsets or np.zeros_like(step)) + np.asarray(step)).tolist()
    warped = warp_take(take, alignment, pw, ph)
    alignment.median_error_px, _ = measure_error(pencil, warped)
    return alignment


def _touch_up(pencil: np.ndarray, take: np.ndarray, alignment: Alignment) -> List[List[List[float]]]:
    ph, pw = pencil.shape[:2]
    a, sa = _shrink(pencil)
    sh, sw = a.shape[:2]
    warped = warp_take(take, alignment, pw, ph)
    b = cv2.resize(_u8(warped), (sw, sh), interpolation=cv2.INTER_AREA)
    la, lb = _line_image(a), _line_image(b)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flow = dis.calc(la, lb, None)  # pencil -> take offsets, small px
    # only trust flow where both have lines; elsewhere it is guessing
    weight = ((la > 40) & (lb > 40)).astype(np.float32)
    rows, cols = GRID
    out = np.zeros((rows, cols, 2), np.float32)
    have = np.zeros((rows, cols), bool)
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * sh // rows, (r + 1) * sh // rows
            x0, x1 = c * sw // cols, (c + 1) * sw // cols
            wcell = weight[y0:y1, x0:x1]
            if wcell.sum() < 0.01 * wcell.size:
                continue
            fx = flow[y0:y1, x0:x1, 0][wcell > 0]
            fy = flow[y0:y1, x0:x1, 1][wcell > 0]
            out[r, c] = (np.median(fx), np.median(fy))
            have[r, c] = True
    if not have.any():
        return []
    # fill empty cells from their neighbours, then smooth: drift is slow across a page
    filled = out.copy()
    for _ in range(max(rows, cols)):
        if have.all():
            break
        grown = have.copy()
        for r in range(rows):
            for c in range(cols):
                if have[r, c]:
                    continue
                nb = [(rr, cc) for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                      if 0 <= rr < rows and 0 <= cc < cols and have[rr, cc]]
                if nb:
                    filled[r, c] = np.mean([filled[rr, cc] for rr, cc in nb], axis=0)
                    grown[r, c] = True
        have = grown
    filled = cv2.GaussianBlur(filled, (5, 5), 1.2, borderType=cv2.BORDER_REPLICATE)
    limit = 0.02 * max(sh, sw)  # a touch-up, never a re-draw
    filled = np.clip(filled, -limit, limit)
    return (filled / sa).tolist()  # small px -> full-res pencil px
