"""
Comic panel cutter.

Finds panel borders (horizontal/vertical gutters) and exports each
panel as a separate PNG, plus an annotated overview with boxes.

Primary strategy (gutter & edge density heuristic):
  1. Trace candidate panel *edges* (high ink density in a row/col band).
  2. Confirm a gutter by checking a thin strip *below* a horizontal edge
     (or *to the right* of a vertical edge): mostly white paper → real
     break; still ink-dense → "just a line" inside a panel, reject.
  3. Prefer cuts near ~90° H∩V junctions (panel corners), rejecting
     wavy hatching / speech-bubble curves that lack orthogonal corners.

Also runs a border-greedy row/col cutter for dense pencil pages with
wavy gutters. Scores prefer ~12–18 panels (dense indie grids).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import find_peaks

from ._pageio import load_gray

PathLike = Union[str, Path]
Box = Tuple[int, int, int, int]  # x0, y0, x1, y1


class PanelCutError(RuntimeError):
    """Raised when fewer than two panels can be found."""


def _to_uint8(gray: np.ndarray) -> np.ndarray:
    return np.clip(gray, 0, 255).astype(np.uint8)


def _smooth(profile: np.ndarray, k: int = 9) -> np.ndarray:
    k = max(3, k | 1)
    return np.convolve(profile.astype(np.float64), np.ones(k) / k, mode="same")


def _spans_of(gutters: Sequence[Tuple[int, int]], lo: int, hi: int) -> List[Tuple[int, int]]:
    """Convert sorted gutter spans into complementary content spans."""
    out: List[Tuple[int, int]] = []
    cur = lo
    for a, b in sorted(gutters):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if hi > cur:
        out.append((cur, hi))
    return out


def _strip_is_white(
    gray: np.ndarray,
    axis: str,
    edge_pos: int,
    lo: int,
    hi: int,
    strip_px: int = 10,
    white_thr: int = 200,
    min_white_frac: float = 0.55,
) -> bool:
    """Gutter paper heuristic: after an edge, is the adjacent strip gutter paper?

    axis='h': edge is a horizontal line at row ``edge_pos``; inspect rows
    just *below* it within columns [lo, hi).
    axis='v': edge is a vertical line at col ``edge_pos``; inspect cols
    just *to the right* within rows [lo, hi).
    """
    g = _to_uint8(gray)
    H, W = g.shape
    if axis == "h":
        y0 = min(H - 1, max(0, edge_pos + 1))
        y1 = min(H, y0 + max(3, strip_px))
        x0, x1 = max(0, lo), min(W, hi)
        if y1 <= y0 or x1 <= x0:
            return False
        strip = g[y0:y1, x0:x1]
    else:
        x0 = min(W - 1, max(0, edge_pos + 1))
        x1 = min(W, x0 + max(3, strip_px))
        y0, y1 = max(0, lo), min(H, hi)
        if x1 <= x0 or y1 <= y0:
            return False
        strip = g[y0:y1, x0:x1]
    if strip.size == 0:
        return False
    return float((strip >= white_thr).mean()) >= min_white_frac


def _right_angle_support(
    gray: np.ndarray,
    axis: str,
    edge_pos: int,
    lo: int,
    hi: int,
    sample_n: int = 7,
) -> float:
    """Score how many ~90° H∩V junctions sit along a candidate edge.

    Samples points along the edge, looks for a strong perpendicular edge
    nearby (Sobel gradients nearly orthogonal). Returns 0..1.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    blur = cv2.GaussianBlur(g, (3, 3), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    # Angle of gradient (normal to edge); two edges meet at ~90° when
    # their gradient directions differ by ~90°.
    hits = 0
    checks = 0
    positions = np.linspace(lo + 8, hi - 8, num=max(3, sample_n), dtype=int)
    for p in positions:
        if axis == "h":
            y = int(np.clip(edge_pos, 1, H - 2))
            x = int(np.clip(p, 1, W - 2))
            # Horizontal edge → strong |gy|; look for vertical edge (|gx|) nearby
            if abs(float(gy[y, x])) < 30:
                continue
            checks += 1
            # Search a small window for strong orthogonal gradient
            win = gx[max(0, y - 4) : min(H, y + 5), max(0, x - 6) : min(W, x + 7)]
            if win.size and float(np.max(np.abs(win))) > 40:
                # Gradient directions: atan2(gy,gx) vs atan2 of neighbor
                ang_h = abs(np.degrees(np.arctan2(float(gy[y, x]), float(gx[y, x]) + 1e-6)))
                # Prefer nearly horizontal edge (ang near 90) + vertical neighbor
                if 60 <= ang_h <= 120:
                    hits += 1
        else:
            x = int(np.clip(edge_pos, 1, W - 2))
            y = int(np.clip(p, 1, H - 2))
            if abs(float(gx[y, x])) < 30:
                continue
            checks += 1
            win = gy[max(0, y - 6) : min(H, y + 7), max(0, x - 4) : min(W, x + 5)]
            if win.size and float(np.max(np.abs(win))) > 40:
                ang_v = abs(np.degrees(np.arctan2(float(gy[y, x]), float(gx[y, x]) + 1e-6)))
                # Vertical edge → ang near 0 or 180
                if ang_v <= 30 or ang_v >= 150:
                    hits += 1
    if checks == 0:
        return 0.0
    return hits / checks


def _morph_border_density(gray: np.ndarray, axis: str) -> np.ndarray:
    """Density of long dark H (axis='h') or V (axis='v') border lines."""
    g = _to_uint8(gray)
    H, W = g.shape
    _, bw = cv2.threshold(g, 100, 255, cv2.THRESH_BINARY_INV)
    if axis == "h":
        kern = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, W // 12), 1))
        lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kern)
        return _smooth((lines > 0).mean(axis=1), 5)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, H // 15)))
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kern)
    return _smooth((lines > 0).mean(axis=0), 5)


def _gutter_via_edge_flood(gray: np.ndarray, border_thresh: int = 200) -> np.ndarray:
    """Mark gutters by flood-filling bright paper from the page edge."""
    g = _to_uint8(gray)
    bright = (g >= border_thresh).astype(np.uint8) * 255
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    dark = cv2.bitwise_not(bright)
    dark = cv2.dilate(dark, np.ones((3, 3), np.uint8), iterations=1)
    sealed = cv2.bitwise_not(dark)

    h, w = sealed.shape
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    fill = sealed.copy()
    seeds = []
    for x in range(0, w, max(1, w // 16)):
        if sealed[0, x] > 0:
            seeds.append((x, 0))
        if sealed[h - 1, x] > 0:
            seeds.append((x, h - 1))
    for y in range(0, h, max(1, h // 16)):
        if sealed[y, 0] > 0:
            seeds.append((0, y))
        if sealed[y, w - 1] > 0:
            seeds.append((w - 1, y))
    if not seeds:
        seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]

    for sx, sy in seeds:
        if fill[sy, sx] == 0:
            continue
        if ff_mask[sy + 1, sx + 1] != 0:
            continue
        cv2.floodFill(fill, ff_mask, (sx, sy), 128)

    return (fill == 128).astype(np.uint8) * 255


def _component_boxes(
    content_mask: np.ndarray,
    min_area: float,
    min_panel_px: int,
) -> List[Box]:
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(content_mask, 8)
    boxes: List[Box] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area:
            continue
        if bw < min_panel_px or bh < min_panel_px:
            continue
        boxes.append((int(x), int(y), int(x + bw), int(y + bh)))
    return boxes


def _find_edge_candidates(
    edge_dens: np.ndarray,
    lo: int,
    hi: int,
    min_panel: int,
    min_dens: float = 0.20,
    min_prom: float = 0.06,
) -> List[Tuple[int, float]]:
    """Peaks in border-line density = candidate panel edges."""
    seg = edge_dens[lo:hi]
    if len(seg) < 2 * min_panel:
        return []
    peaks, props = find_peaks(
        seg, distance=max(20, min_panel // 3), prominence=min_prom, height=min_dens
    )
    out = []
    for i, p in enumerate(peaks):
        ap = lo + int(p)
        if ap - lo < min_panel // 4 or hi - ap < min_panel // 4:
            continue
        out.append((ap, float(edge_dens[ap])))
    return out


def _validate_h_cut(
    gray: np.ndarray,
    y_edge: int,
    x0: int,
    x1: int,
    strip_px: int,
) -> Tuple[bool, float]:
    """Accept horizontal cut if strip below edge is white; boost for right angles."""
    white_ok = _strip_is_white(
        gray, "h", y_edge, x0, x1, strip_px=strip_px, min_white_frac=0.50
    )
    # Also accept very clear white a bit further below (thick double borders)
    if not white_ok:
        white_ok = _strip_is_white(
            gray, "h", y_edge + strip_px // 2, x0, x1,
            strip_px=max(4, strip_px // 2), min_white_frac=0.55,
        )
    ra = _right_angle_support(gray, "h", y_edge, x0, x1)
    if not white_ok and ra < 0.35:
        return False, 0.0
    if not white_ok:
        # Right-angle corner support can rescue a borderline edge whose
        # immediate below-strip is partially inked (bubble tails).
        if ra < 0.5:
            return False, 0.0
    score = (0.7 if white_ok else 0.25) + 0.3 * ra
    return True, score


def _validate_v_cut(
    gray: np.ndarray,
    x_edge: int,
    y0: int,
    y1: int,
    strip_px: int,
) -> Tuple[bool, float]:
    white_ok = _strip_is_white(
        gray, "v", x_edge, y0, y1, strip_px=strip_px, min_white_frac=0.50
    )
    if not white_ok:
        white_ok = _strip_is_white(
            gray, "v", x_edge + strip_px // 2, y0, y1,
            strip_px=max(4, strip_px // 2), min_white_frac=0.55,
        )
    ra = _right_angle_support(gray, "v", x_edge, y0, y1)
    if not white_ok and ra < 0.35:
        return False, 0.0
    if not white_ok and ra < 0.5:
        return False, 0.0
    score = (0.7 if white_ok else 0.25) + 0.3 * ra
    return True, score


def _ink_profile(gray: np.ndarray, axis: int) -> np.ndarray:
    dark = (255.0 - _to_uint8(gray).astype(np.float32)) / 255.0
    return dark.mean(axis=axis)


def _recursive_projection_boxes(
    gray: np.ndarray,
    min_area: float,
    min_panel_px: int,
    min_gutter_px: int,
) -> List[Box]:
    """Row-then-col split using white-below / white-right + right-angle checks."""
    g = _to_uint8(gray)
    H, W = g.shape
    dark = (255.0 - g.astype(np.float32)) / 255.0
    ink_row = _smooth(dark.mean(axis=1), 9)
    h_border = _morph_border_density(g, "h")

    # Content bounds
    idx = np.where(ink_row > 0.025)[0]
    if len(idx) == 0:
        return []
    y0, y1 = int(idx[0]), int(idx[-1]) + 1
    min_panel_y = max(min_panel_px, H // 20, 80)
    strip = max(6, min_gutter_px)

    def near_border(y: int, rad: int = 55, min_dens: float = 0.35) -> bool:
        lo = max(0, y - rad)
        hi = min(H, y + rad + 1)
        return float(h_border[lo:hi].max()) >= min_dens

    def best_h_cut(lo: int, hi: int, lim: float, require_border: bool) -> Optional[Tuple[float, int, int]]:
        """Return (score, g0, g1) for best horizontal gutter in [lo,hi), or None."""
        if hi - lo < 2 * min_panel_y:
            return None
        # Candidate edges: border-density peaks + ink valleys (clear white gutters)
        cands: List[Tuple[float, int]] = []  # (-prefer, y)
        for y, dens in _find_edge_candidates(h_border, lo, hi, min_panel_y, min_dens=0.18):
            cands.append((-dens, y))
        seg = ink_row[lo:hi]
        valleys, props = find_peaks(-seg, distance=min_panel_y, prominence=0.04)
        for i, p in enumerate(valleys):
            y = lo + int(p)
            depth = float(ink_row[y])
            if depth > lim:
                continue
            cands.append((depth, y))

        best = None
        seen = set()
        for _key, y in sorted(cands, key=lambda t: t[0]):
            if y in seen:
                continue
            seen.add(y)
            if y - lo < min_panel_y or hi - y < min_panel_y:
                continue
            depth = float(ink_row[y])
            if depth > lim and not near_border(y):
                continue
            if require_border and depth >= 0.05 and not near_border(y):
                continue
            ok, vscore = _validate_h_cut(g, y, 0, W, strip)
            # Clear deep white valleys can pass without strict white-below
            # (the valley *is* the gutter).
            if not ok and depth < 0.06:
                ok, vscore = True, 0.85
            if not ok:
                continue
            # Expand gutter span around edge while ink stays low / white
            thr = depth + 0.02
            a = y
            while a > lo and ink_row[a] <= max(thr, 0.12):
                a -= 1
            b = y
            while b < hi - 1 and ink_row[b] <= max(thr, 0.12):
                b += 1
            # If expansion collapsed, use a thin band around the border edge
            if b - a < 2:
                a, b = max(lo, y - 2), min(hi, y + 3)
            if a - lo < min_panel_y or hi - b < min_panel_y:
                continue
            score = depth - 0.2 * vscore - (0.04 if near_border(y) else 0)
            if best is None or score < best[0]:
                best = (score, a, b)
        return best

    def h_recurse(lo: int, hi: int, lim: float, require_border: bool) -> List[Tuple[int, int]]:
        best = best_h_cut(lo, hi, lim, require_border)
        if best is None:
            return []
        _, a, b = best
        return h_recurse(lo, a, lim, require_border) + [(a, b)] + h_recurse(b, hi, lim, require_border)

    guts = h_recurse(y0, y1, 0.06, False)
    for lo, hi in _spans_of(guts, y0, y1):
        if hi - lo > 2.5 * min_panel_y:
            guts += h_recurse(lo, hi, 0.15, True)
    guts = sorted(set(guts))

    # Strong interior borders with white-below confirmation on large regions
    for lo, hi in _spans_of(guts, y0, y1):
        if hi - lo <= 2.8 * min_panel_y:
            continue
        edges = _find_edge_candidates(h_border, lo, hi, min_panel_y, min_dens=0.45)
        if not edges:
            continue
        y_edge, dens = max(edges, key=lambda t: t[1])
        if not (lo + min_panel_y < y_edge < hi - min_panel_y):
            continue
        ok, _ = _validate_h_cut(g, y_edge, 0, W, strip)
        if not ok and float(ink_row[y_edge]) > 0.25:
            # Thick border itself: check strip below the border band
            ok, _ = _validate_h_cut(g, y_edge + 8, 0, W, strip)
        if not ok:
            continue
        a, b = y_edge, y_edge + 1
        for y in range(max(lo, y_edge - 30), min(hi, y_edge + 30)):
            if ink_row[y] < 0.20:
                a = min(a, y)
                b = max(b, y + 1)
        if a - lo >= min_panel_y and hi - b >= min_panel_y:
            guts.append((a, b))
    guts = sorted(set(guts))

    rows: List[Tuple[int, int]] = []
    cur = y0
    for a, b in guts:
        a, b = max(a, y0), min(b, y1)
        if a <= cur:
            cur = max(cur, b)
            continue
        if a - cur >= min_panel_y:
            rows.append((cur, a))
        cur = b
    if y1 - cur >= min_panel_y:
        rows.append((cur, y1))

    # --- Per-row vertical splits ---
    min_panel_x = max(100, W // 11, min_panel_px)
    boxes: List[Box] = []

    for r0, r1 in rows:
        bright = (g[r0:r1, :] >= 205).mean(axis=0)
        bright_sm = _smooth(bright, 7)
        col_ink = dark[r0:r1, :].mean(axis=0)
        col_ink_sm = _smooth(col_ink, 11)
        cidx = np.where(col_ink_sm > 0.03)[0]
        if len(cidx) == 0:
            continue
        x0, x1 = int(cidx[0]), int(cidx[-1]) + 1

        # Vertical border density within this row band
        _, bw = cv2.threshold(g[r0:r1, :], 100, 255, cv2.THRESH_BINARY_INV)
        vert = cv2.morphologyEx(
            bw,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, (r1 - r0) // 6))),
        )
        v_border = _smooth((vert > 0).mean(axis=0), 5)

        def near_v(x: int, rad: int = 55) -> bool:
            lo = max(0, x - rad)
            hi = min(W, x + rad + 1)
            return float(v_border[lo:hi].max()) >= 0.28

        def best_v_cut(
            lo: int, hi: int, min_white: float, min_prom: float, require_border: bool
        ) -> Optional[Tuple[float, int, int]]:
            if hi - lo < 2 * min_panel_x:
                return None
            seg = bright_sm[lo:hi]
            peaks, props = find_peaks(
                seg,
                distance=min_panel_x,
                prominence=min_prom * 0.35,
                height=min_white * 0.85,
            )
            best = None
            for i, p in enumerate(peaks):
                x = lo + int(p)
                if x - lo < min_panel_x or hi - x < min_panel_x:
                    continue
                wh = float(bright_sm[x])
                pr = float(props["prominences"][i])
                if wh < min_white or pr < min_prom:
                    continue
                ok, vscore = _validate_v_cut(g, x, r0, r1, strip)
                if not ok and wh < 0.92:
                    continue
                if require_border and wh < 0.93 and not near_v(x):
                    continue
                score = wh + 0.35 * pr + 0.25 * vscore + (0.12 if near_v(x) else 0)
                thr = max(min_white * 0.88, wh - max(0.06, pr * 0.2))
                a = x
                while a > lo and bright_sm[a] >= thr:
                    a -= 1
                b = x
                while b < hi - 1 and bright_sm[b] >= thr:
                    b += 1
                if a - lo < min_panel_x or hi - b < min_panel_x:
                    continue
                if best is None or score > best[0]:
                    best = (score, a, b)
            # Also try strong vertical border edges with white-to-the-right
            if best is None or require_border:
                for x, dens in _find_edge_candidates(
                    v_border, lo, hi, min_panel_x, min_dens=0.28
                ):
                    if x - lo < min_panel_x or hi - x < min_panel_x:
                        continue
                    ok, vscore = _validate_v_cut(g, x, r0, r1, strip)
                    if not ok:
                        continue
                    wh = float(bright_sm[x])
                    if wh < 0.70 and vscore < 0.55:
                        continue
                    score = wh + 0.3 * dens + 0.3 * vscore
                    a, b = x, x + 1
                    for xx in range(max(lo, x - 20), min(hi, x + 20)):
                        if bright_sm[xx] >= 0.70:
                            a = min(a, xx)
                            b = max(b, xx + 1)
                    if a - lo < min_panel_x or hi - b < min_panel_x:
                        continue
                    if best is None or score > best[0]:
                        best = (score, a, b)
            return best

        def v_recurse(
            lo: int, hi: int, min_white: float, min_prom: float, require_border: bool
        ) -> List[Tuple[int, int]]:
            best = best_v_cut(lo, hi, min_white, min_prom, require_border)
            if best is None:
                return []
            _, a, b = best
            return (
                v_recurse(lo, a, min_white, min_prom, require_border)
                + [(a, b)]
                + v_recurse(b, hi, min_white, min_prom, require_border)
            )

        vguts = v_recurse(x0, x1, 0.85, 0.28, False)
        for lo, hi in _spans_of(vguts, x0, x1):
            if hi - lo > 2.5 * min_panel_x:
                vguts += v_recurse(lo, hi, 0.78, 0.25, True)
        vguts = sorted(set(vguts))

        cols: List[Tuple[int, int]] = []
        curx = x0
        for a, b in vguts:
            if a - curx >= min_panel_x:
                cols.append((curx, a))
            curx = b
        if x1 - curx >= min_panel_x:
            cols.append((curx, x1))

        for c0, c1 in cols:
            area = (c1 - c0) * (r1 - r0)
            if area < min_area:
                continue
            if float(dark[r0:r1, c0:c1].mean()) < 0.01:
                continue
            boxes.append((c0, r0, c1, r1))

    return boxes


def _contour_boxes(
    gray: np.ndarray, min_area: float, min_panel_px: int
) -> List[Box]:
    g = _to_uint8(gray)
    H, W = g.shape
    blur = cv2.GaussianBlur(g, (3, 3), 0)
    dark = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    page = float(H * W)
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw * bh < min_area or bw * bh > 0.55 * page:
            continue
        if bw < min_panel_px or bh < min_panel_px:
            continue
        area = cv2.contourArea(cnt)
        if area < 0.4 * bw * bh:
            continue
        boxes.append((int(x), int(y), int(x + bw), int(y + bh)))
    return boxes



def _page_ink_stats(gray: np.ndarray) -> dict:
    """Adaptive thresholds for raw pencil vs cleaned ink pages."""
    g = _to_uint8(gray)
    p50 = float(np.percentile(g, 50))
    p90 = float(np.percentile(g, 90))
    mid = float(((g > 40) & (g < 220)).mean())
    pencil = p50 < 235 or mid > 0.30
    return {
        "pencil": pencil,
        "p50": p50,
        "p90": p90,
        "mid": mid,
        "white_thr": int(max(155, min(220, p90 - 20))) if pencil else 210,
        "border_thr": int(max(100, min(175, p50 - 35))) if pencil else 100,
        "h_dens_min": 0.22 if pencil else 0.28,
        "v_near_min": 0.18 if pencil else 0.25,
        "min_white_h": 0.32 if pencil else 0.55,
    }


def _border_greedy_boxes(
    gray: np.ndarray,
    min_area: float,
    min_panel_px: int,
) -> List[Box]:
    """Row/col cutter for dense pencil grids (white-below / RA).

    1. Strong horizontal morph borders → rows (white-below or dens+RA rescue).
    2. Per row: spanning vertical borders with nearby paper gutter
       (white-to-the-right / near-white), preferring ~90° corners.
    3. Thin fixed gutters — do not expand on flat pencil profiles.
    Full-width tiers with no spanning V borders stay one panel.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    st = _page_ink_stats(g)
    wt = st["white_thr"]
    bthr = st["border_thr"]
    h_dens_min = st["h_dens_min"]
    v_near_min = st["v_near_min"]
    min_white_h = st["min_white_h"]
    dark = (255.0 - g.astype(np.float32)) / 255.0
    ink = _smooth(dark.mean(axis=1), 9)
    _, bw = cv2.threshold(g, bthr, 255, cv2.THRESH_BINARY_INV)
    hlines = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(28, W // 14), 1)),
    )
    hbd = _smooth((hlines > 0).mean(axis=1), 5)

    thr = max(0.02, float(np.percentile(ink, 12)))
    idx = np.where(ink > thr)[0]
    if len(idx) == 0:
        return []
    y0, y1 = int(idx[0]), int(idx[-1]) + 1
    min_py = max(100, H // 14, min_panel_px)
    gutter_half = 6

    peaks, _ = find_peaks(
        hbd[y0:y1],
        distance=max(50, min_py // 2),
        prominence=0.02,
        height=0.10,  # allow faint pencil borders; filter with white/RA
    )
    cands: List[Tuple[float, int]] = []
    page_span = max(1, y1 - y0)
    for p in peaks:
        y = y0 + int(p)
        if y - y0 < min_py or y1 - y < min_py:
            continue
        dens = float(hbd[y])
        sy0 = min(H - 1, y + 1)
        sy1 = min(H, sy0 + 12)
        wb = (
            float((g[sy0:sy1, int(W * 0.06) : int(W * 0.94)] >= wt).mean())
            if sy1 > sy0
            else 0.0
        )
        ra = _right_angle_support(g, "h", y, int(W * 0.06), int(W * 0.94))
        # White-below / RA acceptance
        ok = False
        if dens >= h_dens_min and wb >= min_white_h:
            ok = True
        elif dens >= 0.28 and ra >= 0.25 and wb >= 0.10:
            ok = True
        elif dens >= 0.24 and ra >= 0.40 and wb >= 0.12:
            ok = True
        # Strong right-angle rescue for thin but real tier gutters
        elif dens >= 0.18 and ra >= 0.75 and wb >= 0.40:
            ok = True
        # Faint pencil borders: very high RA + some paper (sparse hatching pages)
        elif dens >= 0.12 and ra >= 0.85 and wb >= 0.35:
            ok = True
        if not ok:
            continue
        # Bottom-of-page cuts need stronger evidence (avoid text↔art
        # internal rules), but allow strong morph borders with weak paper.
        frac = (y - y0) / float(page_span)
        if frac >= 0.72:
            if dens >= 0.50 and ra >= 0.50:
                pass  # strong spanning border
            elif dens >= 0.28 and wb >= 0.52:
                pass
            elif dens >= 0.28 and ra >= 0.70 and wb >= 0.35:
                pass
            elif dens >= 0.20 and ra >= 0.85 and wb >= 0.40:
                pass  # faint but high-RA bottom tier rule
            else:
                continue
        cands.append((dens * 2.0 + wb * 0.8 + ra * 0.5, y))
    # Faint-border pages only (max long-kernel dens low): retry shorter morph
    _hbd_interior = hbd[y0 + min_py : max(y0 + min_py + 1, y1 - min_py)]
    _max_long = float(_hbd_interior.max()) if _hbd_interior.size else 0.0
    if len(cands) < 3 and _max_long < 0.25:
        hlines_s = cv2.morphologyEx(
            bw,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(24, W // 28), 1)),
        )
        hbd_s = _smooth((hlines_s > 0).mean(axis=1), 5)
        peaks_s, _ = find_peaks(
            hbd_s[y0:y1],
            distance=max(50, min_py // 2),
            prominence=0.02,
            height=0.12,
        )
        for p in peaks_s:
            y = y0 + int(p)
            if y - y0 < min_py or y1 - y < min_py:
                continue
            dens = float(hbd_s[y])
            sy0 = min(H - 1, y + 1)
            sy1 = min(H, sy0 + 12)
            wb = (
                float((g[sy0:sy1, int(W * 0.06) : int(W * 0.94)] >= wt).mean())
                if sy1 > sy0
                else 0.0
            )
            ra = _right_angle_support(g, "h", y, int(W * 0.06), int(W * 0.94))
            ok = False
            # Require RA for short-kernel cuts (avoid mid-panel hatching)
            if dens >= 0.18 and wb >= min_white_h and ra >= 0.40:
                ok = True
            elif dens >= 0.14 and ra >= 0.70 and wb >= 0.35:
                ok = True
            elif dens >= 0.20 and ra >= 0.50 and wb >= 0.40:
                ok = True
            if not ok:
                continue
            frac = (y - y0) / float(page_span)
            if frac >= 0.72 and dens < 0.16 and wb < 0.45:
                continue
            cands.append((dens * 2.0 + wb * 0.8 + ra * 0.5, y))
    cands.sort(reverse=True)
    chosen: List[int] = []
    for _score, y in cands:
        if any(abs(y - cy) < min_py for cy in chosen):
            continue
        chosen.append(y)
    chosen.sort()

    cuts: List[Tuple[int, int]] = []
    for y in chosen:
        a, b = max(y0, y - gutter_half), min(y1, y + gutter_half + 1)
        if a - y0 >= min_py and y1 - b >= min_py:
            cuts.append((a, b))
    rows: List[Tuple[int, int]] = []
    cur = y0
    for a, b in cuts:
        if a - cur >= min_py:
            rows.append((cur, a))
        cur = b
    if y1 - cur >= min_py:
        rows.append((cur, y1))

    boxes: List[Box] = []
    min_px = max(120, W // 9, min_panel_px)  # avoid sliver panels
    margin = int(W * 0.05)

    for r0, r1 in rows:
        bdark = dark[r0:r1, :]
        cink = _smooth(bdark.mean(axis=0), 11)
        t = max(0.012, float(np.percentile(cink, 10)))
        cidx = np.where(cink > t)[0]
        if len(cidx) == 0:
            continue
        x0, x1 = int(cidx[0]), int(cidx[-1]) + 1
        band = g[r0:r1, :]
        bh = r1 - r0
        bsm = _smooth((band >= wt).mean(axis=0), 7)
        _, bw2 = cv2.threshold(band, bthr, 255, cv2.THRESH_BINARY_INV)
        vert = cv2.morphologyEx(
            bw2,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(14, bh // 8))),
        )
        vbd = _smooth((vert > 0).mean(axis=0), 5)
        interior_vmax = (
            float(vbd[x0 + margin : max(x0 + margin + 1, x1 - margin)].max())
            if x1 - x0 > 2 * margin
            else 0.0
        )
        if interior_vmax < v_near_min:
            if (x1 - x0) * (r1 - r0) >= min_area and float(bdark.mean()) >= 0.008:
                boxes.append((x0, r0, x1, r1))
            continue

        vcands: List[Tuple[float, int]] = []
        bp, _ = find_peaks(
            vbd[x0:x1],
            distance=max(70, min_px // 2),
            prominence=0.04,
            height=v_near_min * 0.8,
        )
        for p in bp:
            x = x0 + int(p)
            if x < margin or x > W - margin:
                continue
            if x - x0 < min_px or x1 - x < min_px:
                continue
            dens = float(vbd[x])
            vcol = vert[:, max(0, x - 3) : min(W, x + 4)].max(axis=1) > 0
            vspan = float(vcol.mean())
            wr = (
                float((band[:, x + 1 : min(W, x + 14)] >= wt).mean())
                if x + 1 < W
                else 0.0
            )
            wh_r = float(bsm[x : min(len(bsm), x + 45)].max())
            wh_l = float(bsm[max(0, x - 45) : x + 1].max())
            paper = max(wr, wh_r, wh_l)
            ra = _right_angle_support(g, "v", x, r0, r1)
            strong = vspan >= 0.85 and dens >= 0.55
            if strong and paper >= 0.55:
                ok = True
            # Very strong spanning border with thin/dirty gutter paper
            elif vspan >= 0.85 and dens >= 0.70 and paper >= 0.40:
                ok = True
            elif vspan >= 0.55 and paper >= 0.55:
                ok = True
            elif vspan >= 0.48 and paper >= 0.72 and dens >= 0.28:
                ok = True
            # Moderate spanning border + RA (faint pencil gutters)
            elif vspan >= 0.50 and dens >= 0.30 and paper >= 0.45 and ra >= 0.55:
                ok = True
            elif vspan >= 0.60 and dens >= 0.45 and paper >= 0.38 and ra >= 0.40:
                ok = True
            else:
                ok = False
            if not ok:
                continue
            if wh_l >= wh_r and wh_l >= 0.50:
                cut_x = max(0, x - 45) + int(np.argmax(bsm[max(0, x - 45) : x + 1]))
            elif wh_r >= 0.50:
                cut_x = x + int(np.argmax(bsm[x : min(len(bsm), x + 45)]))
            else:
                cut_x = x
            # Reject cuts that create a sliver unless the immediate
            # white-to-the-right gutter is convincing (gutter heuristic).
            left_w = (cut_x - x0) / float(W)
            right_w = (x1 - cut_x) / float(W)
            if min(left_w, right_w) < 0.18:
                if wr < 0.45 or vspan < 0.80:
                    continue
            score = dens * 1.5 + vspan * 2.0 + paper * 1.0 + ra * 0.4
            vcands.append((score, cut_x))

        wp, _ = find_peaks(
            bsm[x0:x1], distance=min_px, prominence=0.12, height=0.75
        )
        for p in wp:
            x = x0 + int(p)
            if x < margin or x > W - margin:
                continue
            if x - x0 < min_px or x1 - x < min_px:
                continue
            wh = float(bsm[x])
            near = float(vbd[max(0, x - 40) : min(len(vbd), x + 41)].max())
            loc = int(np.argmax(vbd[max(0, x - 40) : min(len(vbd), x + 41)]))
            bx = max(0, x - 40) + loc
            vcol = vert[:, max(0, bx - 3) : min(W, bx + 4)].max(axis=1) > 0
            vspan = float(vcol.mean())
            if near < 0.32 or vspan < 0.55:
                continue
            ra = _right_angle_support(g, "v", x, r0, r1)
            score = wh * 1.2 + near * 1.5 + vspan * 1.5 + ra * 0.4
            vcands.append((score, x))

        vcands.sort(reverse=True)
        # Relative gap: drop weak trailing cuts (internal hatching)
        vchosen: List[int] = []
        best_sc = vcands[0][0] if vcands else 0.0
        for score, x in vcands:
            if score < 2.4:
                continue
            if best_sc > 0 and score < best_sc * 0.65:
                continue
            if any(abs(x - cx) < min_px for cx in vchosen):
                continue
            vchosen.append(x)
        vchosen.sort()

        vguts: List[Tuple[int, int]] = []
        for x in vchosen:
            a, b = max(x0, x - gutter_half), min(x1, x + gutter_half + 1)
            if a - x0 >= min_px and x1 - b >= min_px:
                vguts.append((a, b))
        cols: List[Tuple[int, int]] = []
        curx = x0
        for a, b in vguts:
            if a - curx >= min_px:
                cols.append((curx, a))
            curx = b
        if x1 - curx >= min_px:
            cols.append((curx, x1))
        if not cols:
            cols = [(x0, x1)]

        # One-shot split of leftover double-wide panels
        final_cols: List[Tuple[int, int]] = []
        for c0, c1 in cols:
            if (c1 - c0) >= 0.45 * W:
                sub: List[Tuple[float, int]] = []
                bp2, _ = find_peaks(
                    vbd[c0:c1],
                    distance=max(70, min_px // 2),
                    prominence=0.04,
                    height=0.25,
                )
                for p in bp2:
                    x = c0 + int(p)
                    dens = float(vbd[x])
                    vcol = vert[:, max(0, x - 3) : min(W, x + 4)].max(axis=1) > 0
                    vspan = float(vcol.mean())
                    wr = (
                        float((band[:, x + 1 : min(W, x + 14)] >= wt).mean())
                        if x + 1 < W
                        else 0.0
                    )
                    paper = max(
                        wr, float(bsm[max(0, x - 45) : min(len(bsm), x + 45)].max())
                    )
                    if vspan >= 0.80 and dens >= 0.45 and paper >= 0.48:
                        sub.append((vspan + dens + paper, x))
                    elif vspan >= 0.55 and paper >= 0.60:
                        sub.append((vspan + dens + paper, x))
                if sub:
                    sub.sort(reverse=True)
                    x = min([s[1] for s in sub[:3]], key=lambda xx: abs(xx - (c0 + c1) / 2))
                    if x - c0 >= min_px and c1 - x >= min_px:
                        final_cols.append((c0, x - gutter_half))
                        final_cols.append((x + gutter_half, c1))
                        continue
            final_cols.append((c0, c1))

        for c0, c1 in final_cols:
            if (c1 - c0) * (r1 - r0) >= min_area and float(
                dark[r0:r1, c0:c1].mean()
            ) >= 0.008:
                boxes.append((c0, r0, c1, r1))

    boxes.sort(key=lambda b: (b[1] // max(1, H // 20), b[0]))
    return boxes


def _split_one_wide_merge(
    gray: np.ndarray,
    boxes: List[Box],
    min_area: float,
) -> List[Box]:
    """If exactly one box is a clear side-by-side merge, split it once.

    Requires a high-span vertical border in the middle third with nearby
    paper (white-to-the-right / near-white). Used to recover a
    single missed V gutter without over-splitting intentional wide panels
    that lack a spanning mid border.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    st = _page_ink_stats(g)
    wt = st["white_thr"]
    bthr = st["border_thr"]
    best = None  # (score, idx, x_local, box)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        bw, bh = x1 - x0, y1 - y0
        if bw < 0.45 * W:
            continue
        band = g[y0:y1, x0:x1]
        _, bw_i = cv2.threshold(band, bthr, 255, cv2.THRESH_BINARY_INV)
        vert = cv2.morphologyEx(
            bw_i,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(14, bh // 8))),
        )
        vbd = _smooth((vert > 0).mean(axis=0), 5)
        bsm = _smooth((band >= wt).mean(axis=0), 7)
        lo, hi = int(bw * 0.30), int(bw * 0.70)
        for x in range(lo, hi):
            dens = float(vbd[x])
            vcol = vert[:, max(0, x - 3) : min(bw, x + 4)].max(axis=1) > 0
            vspan = float(vcol.mean())
            paper = float(bsm[max(0, x - 45) : min(bw, x + 45)].max())
            wr = (
                float((band[:, x + 1 : min(bw, x + 12)] >= wt).mean())
                if x + 1 < bw
                else 0.0
            )
            if vspan < 0.88 or dens < 0.55:
                continue
            if max(paper, wr) < 0.45:
                continue
            sc = vspan * 2.0 + dens + max(paper, wr)
            if best is None or sc > best[0]:
                best = (sc, i, x, (x0, y0, x1, y1))
    if best is None:
        return list(boxes)
    _sc, bi, x, (x0, y0, x1, y1) = best
    left = (x0, y0, x0 + x - 6, y1)
    right = (x0 + x + 6, y0, x1, y1)
    for box in (left, right):
        if (box[2] - box[0]) * (box[3] - box[1]) < min_area * 0.35:
            return list(boxes)
    out: List[Box] = []
    for j, b in enumerate(boxes):
        if j == bi:
            out.extend([left, right])
        else:
            out.append(b)
    out.sort(key=lambda b: (b[1] // max(1, H // 20), b[0]))
    return out


def _score_boxes(boxes: List[Box], h: int, w: int) -> Tuple[float, float]:
    """Prefer panel counts in ~2–24 with a peak around 12–18."""
    n = len(boxes)
    if n < 2:
        return (-1e9, 0.0)
    # Smooth peak: max at 15, gentle falloff toward 2 and 24
    if 12 <= n <= 18:
        count_score = 100.0 + (18 - abs(n - 15))
    elif 2 <= n < 12:
        count_score = 40.0 + n * 4.0  # rises toward 12
    elif 18 < n <= 24:
        count_score = 80.0 - (n - 18) * 5.0
    else:
        count_score = max(0.0, 30.0 - abs(n - 15) * 3.0)
    coverage = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes) / float(h * w)
    # Mild preference for sensible coverage (not tiny crumbs, not one blob)
    if 0.55 <= coverage <= 0.98:
        count_score += 5.0 * coverage
    # Penalize sliver panels (internal stripes / list rules mistaken for gutters)
    thin = sum(1 for b in boxes if (b[2] - b[0]) < 0.14 * w or (b[3] - b[1]) < 0.08 * h)
    if thin:
        count_score -= 8.0 * thin
    return (count_score, coverage)


def _dedupe_sort(boxes: List[Box], h: int, w: int, min_area: float, padding_px: int) -> List[Box]:
    padded: List[Box] = []
    for x0, y0, x1, y1 in boxes:
        x0 = max(0, x0 - padding_px)
        y0 = max(0, y0 - padding_px)
        x1 = min(w, x1 + padding_px)
        y1 = min(h, y1 + padding_px)
        if (x1 - x0) * (y1 - y0) >= min_area:
            padded.append((x0, y0, x1, y1))
    padded.sort(key=lambda b: (b[1] // max(1, h // 20), b[0]))
    final: List[Box] = []
    for b in padded:
        x0, y0, x1, y1 = b
        area = (x1 - x0) * (y1 - y0)
        keep = True
        for fx0, fy0, fx1, fy1 in final:
            ix0, iy0 = max(x0, fx0), max(y0, fy0)
            ix1, iy1 = min(x1, fx1), min(y1, fy1)
            inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
            if inter > 0.7 * min(area, (fx1 - fx0) * (fy1 - fy0)):
                keep = False
                break
        if keep:
            final.append(b)
    return final



def _rescue_underseg_boxes(
    gray: np.ndarray,
    boxes: List[Box],
    min_area: float,
) -> List[Box]:
    """One-level V/H split of mega-panels when the page is clearly under-cut.

    Only runs for very low panel counts (≤5) so correct sparse pages
    (e.g. 6-panel grids) are left alone. Uses white-below/right
    and right-angle checks with slightly relaxed dens for dirty pencil.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    do_v = len(boxes) <= 5
    do_h = len(boxes) <= 8
    if not do_v and not do_h:
        return list(boxes)

    st = _page_ink_stats(g)
    wt, bthr = st["white_thr"], st["border_thr"]
    dark = (255.0 - g.astype(np.float32)) / 255.0
    min_px = max(120, W // 9)
    min_py = max(100, H // 14)
    out: List[Box] = []

    for x0, y0, x1, y1 in boxes:
        bw, bh = x1 - x0, y1 - y0
        band = g[y0:y1, x0:x1]
        pieces: List[Box] = [(x0, y0, x1, y1)]

        # --- V-split very wide pieces ---
        new_pieces: List[Box] = []
        for px0, py0, px1, py1 in pieces:
            pbw, pbh = px1 - px0, py1 - py0
            # Full under-seg: chase w>=0.60. Partial: only leftover mid-wides.
            if do_v:
                if pbw < 0.60 * W:
                    new_pieces.append((px0, py0, px1, py1))
                    continue
            else:
                if not (0.50 * W <= pbw <= 0.72 * W):
                    new_pieces.append((px0, py0, px1, py1))
                    continue
            sub = g[py0:py1, px0:px1]
            bsm = _smooth((sub >= wt).mean(axis=0), 7)
            _, bw_i = cv2.threshold(sub, bthr, 255, cv2.THRESH_BINARY_INV)
            vert = cv2.morphologyEx(
                bw_i,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, pbh // 8))),
            )
            vbd = _smooth((vert > 0).mean(axis=0), 5)
            vcands: List[Tuple[float, int]] = []
            bp, _ = find_peaks(
                vbd, distance=max(60, min_px // 2), prominence=0.03, height=0.18
            )
            for p in bp:
                x = int(p)
                if x < min_px or pbw - x < min_px:
                    continue
                dens = float(vbd[x])
                vspan = float((vert[:, max(0, x - 3) : x + 4].max(axis=1) > 0).mean())
                wr = (
                    float((sub[:, x + 1 : min(pbw, x + 14)] >= wt).mean())
                    if x + 1 < pbw
                    else 0.0
                )
                paper = max(wr, float(bsm[max(0, x - 40) : min(len(bsm), x + 40)].max()))
                ra = _right_angle_support(g, "v", px0 + x, py0, py1)
                ok = False
                if vspan >= 0.50 and dens >= 0.40 and paper >= 0.45 and ra >= 0.40:
                    ok = True
                elif vspan >= 0.55 and dens >= 0.48 and paper >= 0.42:
                    ok = True
                elif vspan >= 0.85 and dens >= 0.70 and paper >= 0.40:
                    ok = True
                elif ra >= 0.70 and paper >= 0.55 and dens >= 0.35:
                    ok = True
                if not ok:
                    continue
                if min(x, pbw - x) / float(W) < 0.16 and wr < 0.40:
                    continue
                vcands.append((dens + vspan + paper + ra, x))
            wp, _ = find_peaks(bsm, distance=min_px, prominence=0.10, height=0.62)
            for p in wp:
                x = int(p)
                if x < min_px or pbw - x < min_px:
                    continue
                near = float(vbd[max(0, x - 50) : min(len(vbd), x + 51)].max())
                ra = _right_angle_support(g, "v", px0 + x, py0, py1)
                wr = (
                    float((sub[:, x + 1 : min(pbw, x + 12)] >= wt).mean())
                    if x + 1 < pbw
                    else 0.0
                )
                if float(bsm[x]) < 0.65 or ra < 0.70:
                    continue
                if near < 0.15 and wr < 0.50:
                    continue
                if min(x, pbw - x) / float(W) < 0.16:
                    continue
                vcands.append((float(bsm[x]) + ra + near, x))
            vcands.sort(reverse=True)
            chosen: List[int] = []
            for _sc, x in vcands:
                if any(abs(x - c) < min_px for c in chosen):
                    continue
                chosen.append(x)
            chosen.sort()
            if not chosen:
                new_pieces.append((px0, py0, px1, py1))
                continue
            xs = [0] + chosen + [pbw]
            for i in range(len(xs) - 1):
                a, b = xs[i], xs[i + 1]
                if i > 0:
                    a += 6
                if i < len(xs) - 2:
                    b -= 6
                if (b - a) * pbh >= min_area * 0.4 and float(
                    dark[py0:py1, px0 + a : px0 + b].mean()
                ) >= 0.008:
                    new_pieces.append((px0 + a, py0, px0 + b, py1))
        pieces = new_pieces

        # --- H-split tall leftover pieces ---
        # Only full-width (or near) mega-panels — not already V-split columns
        new_pieces = []
        for px0, py0, px1, py1 in pieces:
            pbw, pbh = px1 - px0, py1 - py0
            min_w = 0.55 * W if do_v else 0.70 * W  # stricter if only H-rescue
            min_h = 0.32 * H if do_v else 0.38 * H
            if (not do_h) or pbh < min_h or pbw < min_w:
                new_pieces.append((px0, py0, px1, py1))
                continue
            sub = g[py0:py1, px0:px1]
            ink = _smooth(dark[py0:py1, px0:px1].mean(axis=1), 9)
            _, bw_i = cv2.threshold(sub, bthr, 255, cv2.THRESH_BINARY_INV)
            hlines = cv2.morphologyEx(
                bw_i,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, pbw // 10), 1)),
            )
            hbd = _smooth((hlines > 0).mean(axis=1), 5)
            hcands: List[Tuple[float, int]] = []
            bp, _ = find_peaks(
                hbd, distance=max(40, min_py // 2), prominence=0.02, height=0.10
            )
            for p in bp:
                y = int(p)
                if y < min_py or pbh - y < min_py:
                    continue
                dens = float(hbd[y])
                sy0 = min(pbh - 1, y + 1)
                sy1 = min(pbh, sy0 + 12)
                wb = float((sub[sy0:sy1, :] >= wt).mean()) if sy1 > sy0 else 0.0
                ra = _right_angle_support(g, "h", py0 + y, px0, px1)
                if dens >= 0.15 and wb >= 0.55 and ra >= 0.70:
                    hcands.append((dens + wb + ra, y))
                elif dens >= 0.25 and wb >= 0.50 and ra >= 0.50:
                    hcands.append((dens + wb + ra, y))
            vl, _ = find_peaks(-ink, distance=min_py, prominence=0.025)
            for p in vl:
                y = int(p)
                if y < min_py or pbh - y < min_py:
                    continue
                sy0 = min(pbh - 1, y + 1)
                sy1 = min(pbh, sy0 + 12)
                wb = float((sub[sy0:sy1, :] >= wt).mean()) if sy1 > sy0 else 0.0
                ra = _right_angle_support(g, "h", py0 + y, px0, px1)
                if wb >= 0.65 and float(ink[y]) < 0.22 and ra >= 0.40:
                    hcands.append((wb + ra, y))
            if not hcands:
                new_pieces.append((px0, py0, px1, py1))
                continue
            hcands.sort(reverse=True)
            y = hcands[0][1]
            top = (px0, py0, px1, py0 + y - 6)
            bot = (px0, py0 + y + 6, px1, py1)
            if (top[2] - top[0]) * (top[3] - top[1]) >= min_area * 0.35 and (
                bot[2] - bot[0]
            ) * (bot[3] - bot[1]) >= min_area * 0.35:
                new_pieces.extend([top, bot])
            else:
                new_pieces.append((px0, py0, px1, py1))
        pieces = new_pieces
        out.extend(pieces)

    out.sort(key=lambda b: (b[1] // max(1, H // 20), b[0]))
    return out




def _tighten_panel_boxes(
    gray: np.ndarray,
    boxes: List[Box],
    max_trim: int = 24,
    pad: int = 0,
) -> List[Box]:
    """Tighten crop: trim white gutter beyond panel borders (v0.4.6-tight).

    Aggressive edge trim of near-white strips; pad=0 keeps the crop flush
    to art/borders. Skips an edge when dark strokes sit near that edge.
    Count-preserving; never expands.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    st = _page_ink_stats(g)
    wt = st["white_thr"]
    bthr = st["border_thr"]
    out: List[Box] = []
    for x0, y0, x1, y1 in boxes:
        bw, bh = x1 - x0, y1 - y0
        if bw < 20 or bh < 20:
            out.append((x0, y0, x1, y1))
            continue
        sub = g[y0:y1, x0:x1]
        _, bw_i = cv2.threshold(sub, bthr, 255, cv2.THRESH_BINARY_INV)
        hlines = cv2.morphologyEx(
            bw_i,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, bw // 16), 1)),
        )
        vlines = cv2.morphologyEx(
            bw_i,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, bh // 16))),
        )
        hbd = (hlines > 0).mean(axis=1)
        vbd = (vlines > 0).mean(axis=0)
        bright = (sub >= wt).mean(axis=1)
        bright_c = (sub >= wt).mean(axis=0)
        # fraction of near-dark pixels per row/col (stroke presence)
        dark_r = (sub < bthr).mean(axis=1)
        dark_c = (sub < bthr).mean(axis=0)

        nx0, ny0, nx1, ny1 = 0, 0, bw, bh
        lim_x = max(3, int(0.08 * bw))
        lim_y = max(3, int(0.08 * bh))
        mt = min(max_trim, lim_x, lim_y)

        def edge_has_stroke(dark_prof, border_prof, lo: int, hi: int) -> bool:
            band = dark_prof[lo:hi]
            bband = border_prof[lo:hi]
            if band.size == 0:
                return False
            return float(band.max()) >= 0.04 or float(bband.max()) >= 0.12

        # top — skip tighten if strokes already near top edge
        if not edge_has_stroke(dark_r, hbd, 0, min(8, bh)):
            for t in range(mt):
                if bright[t] < 0.87 or hbd[t] >= 0.12 or dark_r[t] >= 0.06:
                    break
                ny0 = t + 1
        # bottom
        if not edge_has_stroke(dark_r, hbd, max(0, bh - 8), bh):
            for t in range(mt):
                yi = bh - 1 - t
                if bright[yi] < 0.87 or hbd[yi] >= 0.12 or dark_r[yi] >= 0.06:
                    break
                ny1 = yi
        # left
        if not edge_has_stroke(dark_c, vbd, 0, min(8, bw)):
            for t in range(mt):
                if bright_c[t] < 0.87 or vbd[t] >= 0.12 or dark_c[t] >= 0.06:
                    break
                nx0 = t + 1
        # right
        if not edge_has_stroke(dark_c, vbd, max(0, bw - 8), bw):
            for t in range(mt):
                xi = bw - 1 - t
                if bright_c[xi] < 0.87 or vbd[xi] >= 0.12 or dark_c[xi] >= 0.06:
                    break
                nx1 = xi

        # restore margin around remaining border / art
        nx0 = max(0, nx0 - pad)
        ny0 = max(0, ny0 - pad)
        nx1 = min(bw, nx1 + pad)
        ny1 = min(bh, ny1 + pad)
        if (nx1 - nx0) < 0.80 * bw or (ny1 - ny0) < 0.80 * bh:
            out.append((x0, y0, x1, y1))
            continue
        out.append((x0 + nx0, y0 + ny0, x0 + nx1, y0 + ny1))
    return out



def _reassign_caption_under_row(
    gray: np.ndarray,
    boxes: List[Box],
) -> List[Box]:
    """Trim a shared row bottom that swallowed a caption belonging below.

    When ≥2 side-by-side panels share nearly the same bottom and a wide
    panel sits just underneath, look for an art-border + white-below
    gutter in the lower part of the row. Trim the row to that border and
    expand the panel below so the continuous caption band stays with it.
    Count-preserving; no page-name hardcoding.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    if len(boxes) < 3:
        return list(boxes)
    st = _page_ink_stats(g)
    dark = (255.0 - g.astype(np.float32)) / 255.0
    out: List[Box] = list(boxes)
    used: set = set()

    for i, bi in enumerate(boxes):
        if i in used:
            continue
        x0, y0, x1, y1 = bi
        peers = [i]
        for j, bj in enumerate(boxes):
            if j == i or j in used:
                continue
            if abs(bj[3] - y1) > 18:
                continue
            if abs(bj[1] - y0) > max(40, int(0.04 * H)):
                continue
            peers.append(j)
        if len(peers) < 2:
            continue
        bots = [boxes[k][3] for k in peers]
        tops = [boxes[k][1] for k in peers]
        if max(bots) - min(bots) > 18:
            continue
        if max(tops) - min(tops) > max(50, int(0.05 * H)):
            continue
        left = min(boxes[k][0] for k in peers)
        right = max(boxes[k][2] for k in peers)
        bot = int(np.median(bots))
        top = int(np.median(tops))
        below = None
        for j, bj in enumerate(boxes):
            if j in peers:
                continue
            if bj[1] < bot - 10 or bj[1] > bot + 80:
                continue
            span = min(bj[2], right) - max(bj[0], left)
            if span >= 0.65 * (right - left) and (bj[2] - bj[0]) >= 0.55 * W:
                below = j
                break
        if below is None:
            continue
        lo = top + int(0.55 * (bot - top))
        hi = bot - 8
        if hi <= lo + 10:
            continue
        band = g[lo:hi, left:right]
        if band.size == 0:
            continue
        _, bw = cv2.threshold(band, st["border_thr"], 255, cv2.THRESH_BINARY_INV)
        hlines = cv2.morphologyEx(
            bw,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(24, (right - left) // 12), 1)
            ),
        )
        hbd = _smooth((hlines > 0).mean(axis=1), 5)
        bright = _smooth((band >= st["white_thr"]).mean(axis=1), 5)
        cands: List[Tuple[float, int]] = []
        bp, _ = find_peaks(hbd, distance=15, prominence=0.015, height=0.08)
        for p in bp:
            y = lo + int(p)
            dens = float(hbd[int(p)])
            wb = float(
                (g[min(H - 1, y + 1) : min(H, y + 14), left:right] >= st["white_thr"]).mean()
            )
            ra = _right_angle_support(g, "h", y, left, right)
            if dens >= 0.15 and wb >= 0.45 and ra >= 0.50:
                cands.append((dens + wb + ra, y))
        # White valley just under an art border (caption band start)
        for yi in range(len(bright)):
            if float(bright[yi]) < 0.75:
                continue
            above = hbd[max(0, yi - 40) : yi]
            if above.size == 0 or float(above.max()) < 0.12:
                continue
            local = int(np.argmax(above))
            by = lo + max(0, yi - 40) + local
            dens = float(hbd[by - lo]) if 0 <= by - lo < len(hbd) else 0.0
            wb = float(
                (g[min(H - 1, by + 1) : min(H, by + 14), left:right] >= st["white_thr"]).mean()
            )
            if dens >= 0.12 and (wb >= 0.50 or float(bright[yi]) >= 0.85):
                cands.append((dens + wb + float(bright[yi]) * 0.5, by))
        if not cands:
            continue
        cands.sort(reverse=True)
        cut = cands[0][1]
        if bot - cut < 25:
            continue
        if cut - top < 0.35 * (bot - top):
            continue
        for k in peers:
            px0, py0, px1, _py1 = out[k]
            out[k] = (px0, py0, px1, cut - 4)
            used.add(k)
        bx0, _by0, bx1, by1 = out[below]
        out[below] = (bx0, cut + 4, bx1, by1)
        used.add(below)
    return out


def _split_thin_banner_over_wide(
    gray: np.ndarray,
    boxes: List[Box],
    min_area: float,
) -> List[Box]:
    """Split a full-width mega that swallowed a thin multi-panel banner.

    Indie layouts often put a thin road/text strip above a wide scene.
    Only fires when:
      - box is nearly page-wide and tall enough
      - H cut makes a very thin top (≤ ~0.048·H) with paper + RA
      - top has a strong V gutter that does NOT continue into the bottom
    Keeps locked pages safe; may raise count when the banner is real.
    """
    g = _to_uint8(gray)
    H, W = g.shape
    st = _page_ink_stats(g)
    dark = (255.0 - g.astype(np.float32)) / 255.0
    out: List[Box] = []

    for x0, y0, x1, y1 in boxes:
        bw, bh = x1 - x0, y1 - y0
        if bw < 0.70 * W or bh < 0.18 * H:
            out.append((x0, y0, x1, y1))
            continue
        sub = g[y0:y1, x0:x1]
        _, bw_i = cv2.threshold(sub, st["border_thr"], 255, cv2.THRESH_BINARY_INV)
        hlines = cv2.morphologyEx(
            bw_i,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, bw // 10), 1)),
        )
        hbd = _smooth((hlines > 0).mean(axis=1), 5)
        lo = int(0.035 * H)
        hi = min(int(0.042 * H), bh - int(0.12 * H))
        if hi <= lo:
            out.append((x0, y0, x1, y1))
            continue
        best: Optional[Tuple[float, int, List[int]]] = None
        for y in range(lo, hi, 2):
            dens = float(hbd[y])
            wb = float(
                (sub[min(bh - 1, y + 1) : min(bh, y + 12)] >= st["white_thr"]).mean()
            )
            band = sub[max(0, y - 3) : min(bh, y + 4), :]
            ps = float((band.mean(axis=0) >= st["white_thr"] - 5).mean())
            ra = _right_angle_support(g, "h", y0 + y, x0, x1)
            if not (ps >= 0.60 and (ra >= 0.45 or wb >= 0.55)):
                continue
            top = g[y0 : y0 + y, x0:x1]
            th = y
            bsm = _smooth((top >= st["white_thr"]).mean(axis=0), 5)
            _, bwv = cv2.threshold(top, st["border_thr"], 255, cv2.THRESH_BINARY_INV)
            vert = cv2.morphologyEx(
                bwv,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, th // 4))),
            )
            vbd = _smooth((vert > 0).mean(axis=0), 5)
            bot = g[y0 + y : y1, x0:x1]
            bh2 = bot.shape[0]
            _, bwv2 = cv2.threshold(bot, st["border_thr"], 255, cv2.THRESH_BINARY_INV)
            vert2 = cv2.morphologyEx(
                bwv2,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, bh2 // 8))),
            )
            vbd2 = _smooth((vert2 > 0).mean(axis=0), 5)
            min_px = max(70, W // 12)
            local: List[int] = []
            bp, _ = find_peaks(
                vbd, distance=min_px, prominence=0.025, height=0.15
            )
            for p in bp:
                x = int(p)
                if x < min_px or bw - x < min_px:
                    continue
                densv = float(vbd[x])
                vspan = float((vert[:, max(0, x - 3) : x + 4].max(axis=1) > 0).mean())
                paper = float(bsm[max(0, x - 35) : x + 35].max())
                rav = _right_angle_support(g, "v", x0 + x, y0, y0 + y)
                dens_b = (
                    float(vbd2[max(0, x - 5) : min(len(vbd2), x + 6)].max())
                    if len(vbd2)
                    else 0.0
                )
                vspan_b = (
                    float((vert2[:, max(0, x - 3) : x + 4].max(axis=1) > 0).mean())
                    if vert2.size
                    else 0.0
                )
                # Keep cuts in the mid band so a speech-bubble top on the
                # far right is not mistaken for its own banner panel.
                abs_x = x0 + x
                if abs_x < 0.22 * W or abs_x > 0.68 * W:
                    continue
                if (
                    densv >= 0.25
                    and vspan >= 0.80
                    and paper >= 0.72
                    and rav >= 0.55
                    and dens_b < 0.16
                    and vspan_b < 0.20
                ):
                    local.append(x)
            if not local:
                continue
            score = ps + ra + wb + float(len(local))
            if best is None or score > best[0]:
                best = (score, y, sorted(local))
        if best is None:
            out.append((x0, y0, x1, y1))
            continue
        _sc, y, vxs = best
        top_y1 = y0 + y - 4
        bot_box = (x0, y0 + y + 4, x1, y1)
        xs = [0] + vxs + [bw]
        tops: List[Box] = []
        for i in range(len(xs) - 1):
            a, b = xs[i], xs[i + 1]
            if i > 0:
                a += 4
            if i < len(xs) - 2:
                b -= 4
            if (b - a) * y >= min_area * 0.12:
                tops.append((x0 + a, y0, x0 + b, top_y1))
        if len(tops) < 2:
            out.append((x0, y0, x1, y1))
            continue
        # Trailing near-empty banner cell (speech-bubble top): fold into wide below
        while len(tops) >= 2:
            tx0, ty0, tx1, ty1 = tops[-1]
            if float(dark[ty0:ty1, tx0:tx1].mean()) >= 0.045:
                break
            tops.pop()
            # widen bottom upward over that x-range by lowering its top only
            # if it already spans that x; else just drop the empty cell into bot y
            bx0, by0, bx1, by1 = bot_box
            bot_box = (min(bx0, tx0), min(by0, ty0), max(bx1, tx1), by1)
        if len(tops) < 2:
            out.append((x0, y0, x1, y1))
            continue
        out.extend(tops)
        out.append(bot_box)

    out.sort(key=lambda b: (b[1] // max(1, H // 20), b[0]))
    return out


def find_panel_boxes(
    gray: np.ndarray,
    min_panel_area_frac: float = 0.008,
    min_gutter_px: int = 8,
    min_panel_px: Optional[int] = None,
    padding_px: Optional[int] = None,
) -> List[Box]:
    """Detect panel bounding boxes (x0, y0, x1, y1).

    Multi-candidate:
      - recursive projection (white-below / white-right + ~90° corners)
      - edge flood-fill (+ subdivide under-segments)
      - contours
      - border-greedy row/col cutter (dense pencil pages)
      - optional single wide-merge split of the classic best

    Scores prefer ~12–18 panels; greedy recovers under-split pencil grids.
    """
    g = _to_uint8(gray)
    h, w = g.shape
    # v0.4.6-tight: no extra outer gutter (tighten handles edge trim)
    if padding_px is None:
        padding_px = 0
    if min_panel_px is None:
        min_panel_px = max(40, int(min(h, w) * 0.03))
    min_area = min_panel_area_frac * h * w

    candidates: List[List[Box]] = []

    # --- 1. Projection recursive (preferred for grids) ---
    boxes_proj = _recursive_projection_boxes(g, min_area, min_panel_px, min_gutter_px)
    candidates.append(boxes_proj)

    # --- 2. Edge flood-fill ---
    gutter = _gutter_via_edge_flood(g)
    content = cv2.bitwise_not(gutter)
    k = max(3, (min_gutter_px // 2) | 1)
    content = cv2.morphologyEx(
        content,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (k * 2 + 1, k * 2 + 1)),
        iterations=2,
    )
    content = cv2.morphologyEx(
        content, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    )
    boxes_ff = _component_boxes(content, min_area, min_panel_px)
    candidates.append(boxes_ff)

    # If flood-fill under-segments (few huge boxes), try subdividing each
    # mega-region with the projection recursive splitter.
    if 1 <= len(boxes_ff) <= 6:
        subdivided: List[Box] = []
        for x0, y0, x1, y1 in boxes_ff:
            region = g[y0:y1, x0:x1]
            if region.size == 0:
                continue
            sub = _recursive_projection_boxes(
                region,
                min_area * 0.5,
                max(20, min_panel_px // 2),
                min_gutter_px,
            )
            if len(sub) >= 2:
                for sx0, sy0, sx1, sy1 in sub:
                    subdivided.append((sx0 + x0, sy0 + y0, sx1 + x0, sy1 + y0))
            else:
                subdivided.append((x0, y0, x1, y1))
        if len(subdivided) > len(boxes_ff):
            candidates.append(subdivided)

    # --- 3. Contour rectangles ---
    candidates.append(_contour_boxes(g, min_area, min_panel_px))

    # Classic best (pre-greedy), then optional one-merge split
    classic = max(candidates, key=lambda b: _score_boxes(b, h, w))
    if _score_boxes(classic, h, w)[0] < 0:
        merged: List[Box] = []
        for group in candidates:
            merged.extend(group)
        classic = merged
    classic = _dedupe_sort(classic, h, w, min_area, 0)
    candidates_final: List[List[Box]] = [classic]

    # One-merge rescue on raw pencil pages only — cleaned ink pages
    # (barbershop / batch2) already score well and intentional wide
    # panels often have spanning mid borders that would false-split.
    if _page_ink_stats(g)["pencil"]:
        classic_plus = _split_one_wide_merge(g, classic, min_area)
        if len(classic_plus) == len(classic) + 1:
            candidates_final.append(classic_plus)

    # --- 4. Border-greedy (dense pencil / wavy gutters) ---
    greedy = _border_greedy_boxes(g, min_area, min_panel_px)
    if greedy:
        candidates_final.append(greedy)

    best = max(candidates_final, key=lambda b: _score_boxes(b, h, w))
    best = _dedupe_sort(best, h, w, min_area, 0)
    # Rescue clearly under-segmented pencil pages (mega-panels left intact)
    rescued = _rescue_underseg_boxes(g, best, min_area)
    if len(rescued) > len(best) and len(rescued) <= 18:
        if _score_boxes(rescued, h, w)[0] >= _score_boxes(best, h, w)[0] - 15:
            best = rescued
    # Caption band under a shared row → reassign to panel below (count-safe)
    best = _reassign_caption_under_row(g, best)
    # Thin multi-panel banner swallowed by a wide mega (may raise n)
    bannered = _split_thin_banner_over_wide(g, best, min_area)
    if len(bannered) > len(best) and len(bannered) <= 18:
        best = bannered
        best = _tighten_panel_boxes(g, best)
        # Thin banner strips are real panels but below full min_area
        return _dedupe_sort(best, h, w, min_area * 0.12, padding_px)
    best = _tighten_panel_boxes(g, best)
    return _dedupe_sort(best, h, w, min_area, padding_px)


def _annotate_overview(gray: np.ndarray, boxes: Sequence[Box]) -> np.ndarray:
    rgb = cv2.cvtColor(_to_uint8(gray), cv2.COLOR_GRAY2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    colors = [
        (220, 50, 50),
        (40, 160, 60),
        (40, 100, 220),
        (200, 140, 20),
        (160, 40, 180),
        (20, 160, 160),
    ]
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        color = colors[i % len(colors)]
        draw.rectangle([x0, y0, x1, y1], outline=color, width=max(2, min(gray.shape) // 400))
        draw.rectangle([x0, y0, x0 + 28, y0 + 22], fill=color)
        draw.text((x0 + 6, y0 + 3), str(i + 1), fill=(255, 255, 255))
    return np.asarray(pil)



def prepare_page_for_panels(
    gray: np.ndarray,
    deskew: bool = True,
    flatten: bool = False,
    **find_kwargs,
) -> Tuple[np.ndarray, List[Box], float]:
    """Return (page_used, boxes, angle). Deskew kept only if count matches raw."""
    g = _to_uint8(gray)
    boxes_raw = find_panel_boxes(g, **find_kwargs)
    if not deskew and not flatten:
        return g, boxes_raw, 0.0
    from .deskew import deskew_array
    straightened, angle = deskew_array(g, deskew=deskew, flatten=flatten)
    boxes_d = find_panel_boxes(straightened, **find_kwargs)
    if abs(angle) >= 0.05 and len(boxes_d) == len(boxes_raw):
        return _to_uint8(straightened), boxes_d, float(angle)
    return g, boxes_raw, float(angle)

def cut_panels(
    input_path: PathLike,
    output_dir: PathLike,
    min_panel_area_frac: float = 0.008,
    padding_px: int = 0,
    min_gutter_px: int = 8,
    min_panel_px: Optional[int] = None,
    deskew: bool = True,
    flatten: bool = False,
) -> Tuple[List[Path], Path]:
    """Cut comic panels from a page image.

    Parameters
    ----------
    input_path
        Page image (PNG/JPG/TIFF…).
    output_dir
        Directory for ``panel_01.png``, ``panel_02.png``, … and
        ``panels_overview.png``.
    min_panel_area_frac
        Ignore regions smaller than this fraction of the page (default 0.008).
    padding_px
        Extra pixels kept around each panel box.
    min_gutter_px
        Minimum gutter width/height in pixels to count as a separator.
    min_panel_px
        Minimum panel width/height in pixels (default ~3% of min side).
    deskew, flatten
        Straighten page and even lighting before detection/crops (default True).

    Returns
    -------
    panel_paths, overview_path

    Raises
    ------
    PanelCutError
        If fewer than 2 panels are found.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    gray = load_gray(input_path)
    boxes = None
    if deskew or flatten:
        from .deskew import deskew_array
        # Flatten shifts ink stats and breaks GT counts on pencil pages —
        # default flatten=False. Deskew only kept when panel count matches
        # the undeskewed page (straighten without re-segmentation).
        straightened, _angle = deskew_array(
            gray, deskew=deskew, flatten=flatten
        )
        boxes_raw = find_panel_boxes(
            gray,
            min_panel_area_frac=min_panel_area_frac,
            min_gutter_px=min_gutter_px,
            min_panel_px=min_panel_px,
            padding_px=padding_px,
        )
        boxes_d = find_panel_boxes(
            straightened,
            min_panel_area_frac=min_panel_area_frac,
            min_gutter_px=min_gutter_px,
            min_panel_px=min_panel_px,
            padding_px=padding_px,
        )
        if abs(_angle) >= 0.05 and len(boxes_d) == len(boxes_raw):
            gray = straightened
            boxes = boxes_d
        else:
            boxes = boxes_raw
    if boxes is None:
        boxes = find_panel_boxes(
            gray,
            min_panel_area_frac=min_panel_area_frac,
            min_gutter_px=min_gutter_px,
            min_panel_px=min_panel_px,
            padding_px=padding_px,
        )

    if len(boxes) < 2:
        raise PanelCutError(
            f"Found {len(boxes)} panel(s); need at least 2. "
            "Try lowering min_gutter_px / min_panel_area_frac, or check that "
            "gutters are clearly lighter than panel art."
        )

    g8 = _to_uint8(gray)
    panel_paths: List[Path] = []
    for i, (x0, y0, x1, y1) in enumerate(boxes, start=1):
        crop = g8[y0:y1, x0:x1]
        dest = output_dir / f"panel_{i:02d}.png"
        Image.fromarray(crop, "L").save(dest)
        panel_paths.append(dest)

    overview = _annotate_overview(gray, boxes)
    overview_path = output_dir / "panels_overview.png"
    Image.fromarray(overview).save(overview_path)

    return panel_paths, overview_path
