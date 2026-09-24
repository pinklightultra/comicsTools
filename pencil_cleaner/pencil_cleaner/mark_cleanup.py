"""
Corner-mark cleanup for printed inking pages.

Designed for pages printed with four registration marks near the
corners (top-left mark is a square so orientation can be recovered).
Finds the marks, straightens the scan, strips the light-grey laser
print, and keeps the black ink.

Soft dependency: ``pypdfium2`` is required to load PDF scans on this
path (images work with Pillow alone). Install with::

    pip install pypdfium2
    # or
    pip install -e ".[marks]"
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

# ---- page geometry (inches, US Letter) — must match the printed pages ----
PAGE_W, PAGE_H = 8.5, 11.0
MARK_R = 0.10  # corner mark radius / half-side
MARK_INSET = 0.45  # mark centre distance from paper edge
MARKS = np.float32(
    [
        (MARK_INSET, MARK_INSET),
        (PAGE_W - MARK_INSET, MARK_INSET),
        (PAGE_W - MARK_INSET, PAGE_H - MARK_INSET),
        (MARK_INSET, PAGE_H - MARK_INSET),
    ]
)
ART = (0.75, 0.75, PAGE_W - 0.75, PAGE_H - 0.75)  # x0, y0, x1, y1
MARK_D = 0.20
SPAN_RATIO = (PAGE_W - 2 * MARK_INSET) / (PAGE_H - 2 * MARK_INSET)  # ~0.752

PathLike = Union[str, Path]


# ------------------------------------------------------------------ loading
def load_pages(path: PathLike, render_dpi: int = 400) -> Iterator[Tuple[str, np.ndarray]]:
    """Yield ``(name, grayscale_uint8_array)`` for every page in *path*.

    PDFs need ``pypdfium2``. Images (PNG/JPG/TIFF, including multi-frame)
    use Pillow alone.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ImportError(
                "Reading PDFs for corner-mark cleanup needs pypdfium2: "
                "pip install pypdfium2  (or  pip install -e '.[marks]')"
            ) from exc
        pdf = pdfium.PdfDocument(str(path))
        stem = path.stem
        for i in range(len(pdf)):
            img = pdf[i].render(scale=render_dpi / 72, grayscale=True).to_pil().convert("L")
            yield f"{stem}-p{i + 1:02d}", np.asarray(img)
    else:
        img = Image.open(path)
        n = getattr(img, "n_frames", 1)
        stem = path.stem
        for i in range(n):
            img.seek(i)
            name = stem if n == 1 else f"{stem}-p{i + 1:02d}"
            yield name, np.asarray(img.convert("L"))


# ------------------------------------------------------------ mark finding
def flatten(g: np.ndarray, bg_px: float) -> np.ndarray:
    """Divide out uneven lighting. Returns float 0..255, paper ~255."""
    g = g.astype(np.float32)
    f = max(1, int(bg_px // 24))
    small = cv2.resize(
        g,
        (max(1, g.shape[1] // f), max(1, g.shape[0] // f)),
        interpolation=cv2.INTER_AREA,
    )
    k = max(3, int(bg_px / f) | 1)
    bg = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 2)
    bg = np.maximum(bg, np.percentile(bg, 50) * 0.75)
    bg = cv2.resize(bg, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR)
    return np.clip(g / np.maximum(bg, 1) * 255.0, 0, 255)


def find_marks(g: np.ndarray) -> np.ndarray:
    """Locate the four corner registration marks. Returns float32 (4, 2)."""
    H, W = g.shape
    f = 1600.0 / max(H, W)
    small = cv2.resize(g, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    flat = flatten(small, 60)
    dark = (flat < 110).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(dark, 8)
    L = max(small.shape)
    cands = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        d = max(w, h)
        if not (0.006 * L <= d <= 0.04 * L):
            continue
        if not (0.7 <= w / h <= 1.43):
            continue
        fill = a / float(w * h)
        if fill < 0.62:
            continue
        cands.append((cen[i][0], cen[i][1], fill, d))
    if len(cands) < 4:
        raise RuntimeError("could not find the four corner marks")
    cx, cy = small.shape[1] / 2, small.shape[0] / 2
    picks = []
    for qx, qy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        q = [c for c in cands if np.sign(c[0] - cx) == qx and np.sign(c[1] - cy) == qy]
        if not q:
            raise RuntimeError("could not find a corner mark in every corner")
        picks.append(max(q, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2))
    pts = np.float32([(p[0], p[1]) for p in picks])
    sides = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]
    short, long_ = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    ratio = min(short, long_) / max(short, long_)
    if abs(ratio - SPAN_RATIO) > 0.08:
        raise RuntimeError(
            f"corner marks don't form the expected rectangle (ratio {ratio:.2f})"
        )
    order = sorted(
        range(4),
        key=lambda i: np.arctan2(pts[i][1] - pts[:, 1].mean(), pts[i][0] - pts[:, 0].mean()),
    )
    fills = [picks[i][2] for i in order]
    start = (
        int(np.argmax(fills))
        if max(fills) - sorted(fills)[-2] > 0.05
        else int(np.argmin([pts[i][0] + pts[i][1] for i in order]))
    )
    order = order[start:] + order[:start]
    pts = pts[order] / f
    ref = []
    r = int(max(picks[0][3], 4) / f * 1.5)
    for x, y in pts:
        x0, y0 = int(max(0, x - r)), int(max(0, y - r))
        win = g[y0 : int(y + r), x0 : int(x + r)]
        m = (win < (np.percentile(win, 98) * 0.5)).astype(np.uint8)
        n2, lab2, st2, cen2 = cv2.connectedComponentsWithStats(m, 8)
        if n2 > 1:
            j = 1 + int(np.argmax(st2[1:, 4]))
            ref.append((x0 + cen2[j][0], y0 + cen2[j][1]))
        else:
            ref.append((x, y))
    return np.float32(ref)


# ------------------------------------------------------------ ink extraction
def extract_ink(
    flat: np.ndarray,
    dpi: int,
    t_strong: int = 95,
    t_weak: int = 150,
    t_black: int = 60,
) -> Tuple[np.ndarray, int, int]:
    """Keep bold ink; drop light laser print. Returns (ink_uint8, kept, dropped)."""
    s1 = cv2.GaussianBlur(flat, (0, 0), max(0.5, 0.0012 * dpi))
    strong = (s1 < t_strong).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(strong, 8)
    a_min = (0.012 * dpi) ** 2 * 0.785  # ~0.3 mm dot
    l_min = 0.02 * dpi  # ~0.5 mm long
    keep_ids = np.zeros(n, bool)
    keep_ids[1:] = (st[1:, 4] >= a_min) | (np.maximum(st[1:, 2], st[1:, 3]) >= l_min)
    core = keep_ids[lab]
    grow = cv2.dilate(core.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    keep = grow & (s1 < t_weak)
    tone = np.clip((s1 - t_black) / (t_weak - t_black), 0, 1) * 255
    out = np.full(flat.shape, 255, np.uint8)
    out[keep] = tone[keep].astype(np.uint8)
    return out, int(keep_ids[1:].sum()), int((~keep_ids[1:]).sum())


def process(
    g: np.ndarray,
    dpi: int = 600,
    trim: bool = True,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Straighten via corner marks and extract ink.

    Returns ``(ink, straightened_page, mark_pts, kept, dropped)``.
    """
    pts = find_marks(g)
    dst = MARKS * dpi
    Hm = cv2.getPerspectiveTransform(pts, dst)
    W, H = int(round(PAGE_W * dpi)), int(round(PAGE_H * dpi))
    page = cv2.warpPerspective(g, Hm, (W, H), flags=cv2.INTER_CUBIC, borderValue=255)
    x0, y0, x1, y1 = [int(round(v * dpi)) for v in ART]
    b = int(0.05 * dpi)
    art = page[y0 - b : y1 + b, x0 - b : x1 + b]
    flat = flatten(art, 0.35 * dpi)
    ink, kept, dropped = extract_ink(flat, dpi)
    if trim:
        ys, xs = np.where(ink < 200)
        if len(xs):
            p = int(0.08 * dpi)
            ink = ink[max(0, ys.min() - p) : ys.max() + p, max(0, xs.min() - p) : xs.max() + p]
    return ink, page, pts, kept, dropped


def clean_marks_page(
    image_or_array: Union[PathLike, np.ndarray],
    output_path: Optional[PathLike] = None,
    dpi: int = 600,
    trim: bool = True,
) -> Image.Image:
    """Clean a single scanned page that has corner registration marks.

    Parameters
    ----------
    image_or_array : path to a raster image, or a grayscale numpy array
    output_path : optional path to save the cleaned PNG
    dpi : output / warp resolution (default 600)
    trim : crop to ink bounding box with a small pad

    Returns
    -------
    PIL.Image.Image
    """
    if isinstance(image_or_array, (str, Path)):
        arr = np.asarray(Image.open(image_or_array).convert("L"))
    else:
        arr = np.asarray(image_or_array)
        if arr.ndim == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.shape[2] == 3 else arr[..., 0]
    ink, *_ = process(arr, dpi=dpi, trim=trim)
    im = Image.fromarray(ink)
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        im.save(output_path, dpi=(dpi, dpi), optimize=True)
    return im


def clean_marks_batch(
    inputs: Iterable[PathLike],
    output_dir: PathLike,
    dpi: int = 600,
    trim: bool = True,
    render_dpi: int = 400,
    debug: bool = False,
) -> List[Path]:
    """Clean one or more scans (images or PDFs). Writes PNGs into *output_dir*.

    Returns the list of written output paths. Pages that fail mark
    detection are skipped (error message stored on the returned path
    object's ``.skip_reason`` attribute is not used; callers should
    catch via the status callbacks — this function raises nothing per
    page and collects failures in the returned list of ``(name, error)``
    pairs accessible via the companion ``results`` dict… actually we
    keep it simple: return written paths, and raise on total failure
    only when every page fails if you check the return.

    For a richer API, use :func:`iter_clean_marks` instead.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    errors: List[str] = []
    for path in inputs:
        for name, g in load_pages(path, render_dpi=render_dpi):
            try:
                ink, page, pts, kept, dropped = process(g, dpi=dpi, trim=trim)
            except RuntimeError as e:
                errors.append(f"{name}: {e}")
                continue
            out = output_dir / f"{name}-ink.png"
            Image.fromarray(ink).save(out, dpi=(dpi, dpi), optimize=True)
            if debug:
                dbg = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
                for k, (x, y) in enumerate(pts):
                    cv2.circle(dbg, (int(x), int(y)), 30, (0, 0, 255), 6)
                    cv2.putText(
                        dbg,
                        str(k + 1),
                        (int(x) + 35, int(y)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        2,
                        (0, 0, 255),
                        4,
                    )
                cv2.imwrite(
                    str(output_dir / f"{name}-marks.jpg"),
                    dbg,
                    [cv2.IMWRITE_JPEG_QUALITY, 80],
                )
            written.append(out)
    if not written and errors:
        raise RuntimeError(
            "No pages cleaned. Failures:\n  " + "\n  ".join(errors)
        )
    return written


def iter_clean_marks(
    inputs: Iterable[PathLike],
    output_dir: PathLike,
    dpi: int = 600,
    trim: bool = True,
    render_dpi: int = 400,
    debug: bool = False,
) -> Iterator[Tuple[str, Optional[Path], Optional[str]]]:
    """Yield ``(name, output_path_or_None, error_or_None)`` per page."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in inputs:
        for name, g in load_pages(path, render_dpi=render_dpi):
            try:
                ink, page, pts, kept, dropped = process(g, dpi=dpi, trim=trim)
            except RuntimeError as e:
                yield name, None, str(e)
                continue
            out = output_dir / f"{name}-ink.png"
            Image.fromarray(ink).save(out, dpi=(dpi, dpi), optimize=True)
            if debug:
                dbg = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
                for k, (x, y) in enumerate(pts):
                    cv2.circle(dbg, (int(x), int(y)), 30, (0, 0, 255), 6)
                cv2.imwrite(
                    str(output_dir / f"{name}-marks.jpg"),
                    dbg,
                    [cv2.IMWRITE_JPEG_QUALITY, 80],
                )
            yield name, out, None
