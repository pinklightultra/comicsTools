"""
Step 2 of the workflow: you have a scan of an INKED page. Leftover
pencil (ghost lettering, faint hatching, eraser smudges) and scanner
noise are still visible. This strips everything that isn't ink.

Three independent techniques are exposed here, in increasing order of
how well they cope with a pencil ghost that's nearly as dark as the
ink itself -- and increasing cost:

  levels           A flat brightness/contrast remap: everything at or
                    below a "black" threshold becomes solid ink,
                    everything at or above a "white" threshold becomes
                    paper, with a linear ramp between. Fast, no
                    dependencies beyond numpy. Works well whenever the
                    pencil is clearly lighter than every ink stroke on
                    the page, which is the common case. Struggles when
                    a ghost mark is as dark as thin ink -- there's no
                    single brightness cutoff that keeps one and drops
                    the other.

  morphological     Separates ink from ghost by stroke *thickness*
                    instead of brightness: real inked lines are bold
                    enough to survive being eroded and rebuilt (a
                    morphological opening, reconstructed back to the
                    stroke's original shape); thin pencil ghosts and
                    paper grain don't survive the erosion and vanish
                    outright rather than merely fading. Handles the
                    same-darkness case `levels` can't. Needs
                    opencv-python.

  vector            Takes the morphological mask and traces it to true
                    vector shapes with the `potrace` command-line
                    tool, then rasterizes that back to a bitmap and
                    uses it as the mask instead. The ink/paper
                    boundary becomes a smooth mathematical curve
                    instead of a pixel threshold, and you get an
                    editable .svg of just the linework as a side
                    effect. Needs the `potrace` binary and, to get the
                    vector mask back into the raster output,
                    `cairosvg`. Falls back to the plain morphological
                    result if either is missing.

`remove_pencils` / `clean_pdf` are the orchestrators; pick a method
with `method="levels" | "morphological" | "vector"` (default
`"vector"`, which itself falls back automatically). Each technique is
also just a plain function below if you want to call it directly,
inspect the mask, or build your own pipeline out of the pieces.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

from ._pageio import load_gray, pdf_to_gray_pages, save_pages_as_pdf

Array = np.ndarray
METHODS = ("levels", "morphological", "vector", "gpu")


# ---------------------------------------------------------------------------
# Method 1: levels -- flat brightness/contrast remap, no morphology.
# ---------------------------------------------------------------------------

def levels_clean(gray: Array, black: int = 175, white: int = 208) -> Array:
    """Flat contrast stretch: pixels at/below `black` -> solid ink (0),
    pixels at/above `white` -> paper (255), linear ramp between.

    This is the fast, dependency-free method. Start here; reach for
    `morphological_clean` / `vector_clean` only if a ghost mark
    survives because it's as dark as your actual ink.
    """
    return np.clip((gray - black) / max(white - black, 1), 0, 1) * 255.0


# ---------------------------------------------------------------------------
# Method 2: morphological -- the "inker". Separates by stroke thickness.
# ---------------------------------------------------------------------------

def build_ink_mask(
    gray: Array,
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    max_iterations: int = 40,
) -> Array:
    """Boolean mask, True where a real (bold-enough) ink stroke is.

    Parameters
    ----------
    gray : float32 grayscale page, 0..255 (255 = white paper)
    dark_thresh : how dark (out of 255 darkness) a pixel must be to
        even be considered "possibly ink". Lower catches fainter
        marks but risks catching heavier pencil too.
    min_stroke_px : strokes thinner than this (roughly, in pixels at
        the image's native resolution) are treated as pencil/noise
        and dropped, not just lightened. Scale this with your scan
        DPI -- 5px is tuned for ~300 dpi comic-page scans.
    max_iterations : safety cap on the reconstruction loop.
    """
    darkness = 255.0 - gray
    candidate = (darkness > dark_thresh).astype(np.uint8)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (min_stroke_px, min_stroke_px))
    seed = cv2.erode(candidate, k, iterations=1)

    # Geodesic reconstruction: grow the seed back out, but never past
    # the original candidate region. What's left is exactly the
    # connected components of `candidate` that were thick enough to
    # survive the erosion -- i.e. the real ink strokes.
    rec = seed.copy()
    kernel3 = np.ones((3, 3), np.uint8)
    for _ in range(max_iterations):
        grown = cv2.dilate(rec, kernel3)
        grown = cv2.bitwise_and(grown, candidate)
        if np.array_equal(grown, rec):
            break
        rec = grown

    return rec.astype(bool)


def darken_ink(gray: Array, mask: Array, black: int = 40, white: int = 170, gamma: float = 1.6) -> Array:
    """Push the ink inside `mask` toward solid black, so light
    fineliner strokes print as dark as heavier marker strokes instead
    of just keeping whatever gray value the scanner happened to
    capture. Everything outside `mask` becomes pure white (255).
    """
    normalized = np.clip((gray - black) / max(white - black, 1), 0, 1)
    remapped = 255.0 * (normalized ** gamma)
    out = np.full_like(gray, 255.0)
    out[mask] = remapped[mask]
    return np.clip(out, 0, 255)


def morphological_clean(
    gray: Array,
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    black: int = 40,
    white: int = 170,
    gamma: float = 1.6,
) -> Array:
    """`build_ink_mask` + `darken_ink` in one call -- the "inker"."""
    mask = build_ink_mask(gray, dark_thresh=dark_thresh, min_stroke_px=min_stroke_px)
    return darken_ink(gray, mask, black=black, white=white, gamma=gamma)


# ---------------------------------------------------------------------------
# Method 3: vector -- the "vectorizer". Traces the mask with potrace.
# ---------------------------------------------------------------------------

def trace_mask_to_svg(mask: Array, svg_path: Union[str, Path], opttolerance: float = 0.2) -> bool:
    """Trace a boolean ink mask to a vector .svg with the `potrace`
    command-line tool. Returns True on success, False if `potrace`
    isn't installed (in which case nothing is written).
    """
    if shutil.which("potrace") is None:
        return False

    # potrace's PBM reader wants a real 1-bit bitmap (mode "1"), ink
    # pixels "on" (255/white in the array before bit-packing). Saving
    # an 8-bit grayscale image with just a .pbm extension will load
    # and *look* fine but is NOT a real 1-bit bitmap -- potrace
    # mis-parses it and silently produces badly fragmented traces.
    bilevel = Image.fromarray(mask.astype(np.uint8) * 255).convert("1")

    with tempfile.TemporaryDirectory() as td:
        pbm_path = Path(td) / "mask.pbm"
        bilevel.save(pbm_path)
        subprocess.run(
            [
                "potrace",
                str(pbm_path),
                "-s",  # SVG backend
                "-o",
                str(svg_path),
                "-t",
                "0",  # turdsize 0: don't drop small shapes (dots, punctuation)
                "-O",
                str(opttolerance),
            ],
            check=True,
            capture_output=True,
        )
    return True


def rasterize_svg_mask(svg_path: Union[str, Path], width: int, height: int) -> Optional[Array]:
    """Render a traced SVG back to a boolean mask at the given pixel
    size. Returns None if `cairosvg` isn't installed.
    """
    try:
        import cairosvg
    except ImportError:
        return None

    with tempfile.TemporaryDirectory() as td:
        png_path = Path(td) / "raster.png"
        cairosvg.svg2png(
            url=str(svg_path), write_to=str(png_path), output_width=width, output_height=height
        )
        im = Image.open(png_path)
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        composited = Image.alpha_composite(bg, im.convert("RGBA")).convert("L")
        arr = np.array(composited)
    return arr > 127  # SVG fill is black-on-transparent; opaque black = ink


def vectorize_mask(
    mask: Array, svg_path: Union[str, Path, None] = None, opttolerance: float = 0.2
) -> Tuple[Array, bool]:
    """Trace `mask` to SVG and rasterize it straight back to a boolean
    mask of the same shape. Returns `(mask_out, used_vector)` --
    `mask_out` is the vector-traced mask if both `potrace` and
    `cairosvg` are available, otherwise the original `mask` unchanged
    and `used_vector=False`.

    If `svg_path` is None, the traced SVG is written to a temp file
    and discarded; pass a path to keep it.
    """
    h, w = mask.shape
    with tempfile.TemporaryDirectory() as td:
        target = svg_path if svg_path is not None else Path(td) / "ink.svg"
        if not trace_mask_to_svg(mask, target, opttolerance=opttolerance):
            return mask, False
        vec_mask = rasterize_svg_mask(target, w, h)
    if vec_mask is None:
        return mask, False
    return vec_mask, True


def vector_clean(
    gray: Array,
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    black: int = 40,
    white: int = 170,
    gamma: float = 1.6,
    svg_output_path: Union[str, Path, None] = None,
) -> Tuple[Array, bool]:
    """`build_ink_mask` -> `vectorize_mask` -> `darken_ink` in one
    call -- the full "vectorizer" pipeline. Returns
    `(cleaned_array, used_vector)`; `used_vector` is False if
    `potrace`/`cairosvg` weren't available and it silently fell back
    to the morphological mask.
    """
    mask = build_ink_mask(gray, dark_thresh=dark_thresh, min_stroke_px=min_stroke_px)
    mask, used_vector = vectorize_mask(mask, svg_output_path)
    out = darken_ink(gray, mask, black=black, white=white, gamma=gamma)
    return out, used_vector


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def _run_method(
    gray: Array,
    method: str,
    dark_thresh: int,
    min_stroke_px: int,
    black: int,
    white: int,
    gamma: float,
    svg_output_path: Union[str, Path, None],
) -> Tuple[Array, bool]:
    if method == "levels":
        return levels_clean(gray, black=black, white=white), False
    if method == "morphological":
        return (
            morphological_clean(
                gray,
                dark_thresh=dark_thresh,
                min_stroke_px=min_stroke_px,
                black=black,
                white=white,
                gamma=gamma,
            ),
            False,
        )
    if method == "vector":
        return vector_clean(
            gray,
            dark_thresh=dark_thresh,
            min_stroke_px=min_stroke_px,
            black=black,
            white=white,
            gamma=gamma,
            svg_output_path=svg_output_path,
        )
    if method == "gpu":
        from .gpu import gpu_clean
        return (
            gpu_clean(
                gray,
                dark_thresh=dark_thresh,
                min_stroke_px=min_stroke_px,
                black=black,
                white=white,
                gamma=gamma,
            ),
            False,
        )
    raise ValueError(f"method must be one of {METHODS}, got {method!r}")


def remove_pencils(
    image_path: Union[str, Path],
    output_path: Union[str, Path, None] = None,
    method: str = "vector",
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    black: Optional[int] = None,
    white: Optional[int] = None,
    gamma: float = 1.6,
    svg_output_path: Union[str, Path, None] = None,
) -> Image.Image:
    """Strip pencil ghosts and scanner noise from a single inked page.

    Parameters
    ----------
    image_path : path to the scanned inked page
    output_path : where to save the cleaned PNG. If None, only the
        PIL Image is returned.
    method : "levels" | "morphological" | "vector" (default). See the
        module docstring for what each one trades off. `"vector"`
        automatically falls back to the morphological result if
        `potrace`/`cairosvg` aren't installed.
    dark_thresh, min_stroke_px : see `build_ink_mask`. Ignored by
        `method="levels"`.
    black, white : the remap thresholds. Defaults depend on `method`:
        175/208 for `"levels"` (tuned for a global contrast stretch),
        40/170 for `"morphological"`/`"vector"` (tuned as a per-stroke
        darkening curve). Pass your own to override either way.
    gamma : ink darkening curve exponent for `"morphological"` /
        `"vector"` (ignored by `"levels"`). >1 pushes mid-tones darker.
    svg_output_path : if given and `method="vector"`, save the traced
        vector ink shapes here.

    Returns
    -------
    PIL.Image.Image
    """
    if black is None:
        black = 175 if method == "levels" else 40
    if white is None:
        white = 208 if method == "levels" else 170

    gray = load_gray(image_path)
    out, _ = _run_method(
        gray, method, dark_thresh, min_stroke_px, black, white, gamma, svg_output_path
    )
    im = Image.fromarray(out.astype(np.uint8), "L")
    if output_path is not None:
        im.save(output_path)
    return im


def clean_pdf(
    pdf_path: Union[str, Path],
    output_pdf_path: Union[str, Path],
    method: str = "vector",
    dpi: int = 300,
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    black: Optional[int] = None,
    white: Optional[int] = None,
    gamma: float = 1.6,
) -> None:
    """Clean every page of an inked, multi-page PDF and write a new
    PDF of the same page count. See `remove_pencils` for the tuning
    parameters. Vector tracing (if `method="vector"`) is done per
    page and discarded -- use `remove_pencils` on a single page with
    `svg_output_path` set if you want to keep a page's .svg.
    """
    if black is None:
        black = 175 if method == "levels" else 40
    if white is None:
        white = 208 if method == "levels" else 170

    pages: List[Image.Image] = []
    for gray in pdf_to_gray_pages(pdf_path, dpi=dpi):
        out, _ = _run_method(gray, method, dark_thresh, min_stroke_px, black, white, gamma, None)
        pages.append(Image.fromarray(out.astype(np.uint8), "L"))

    save_pages_as_pdf(pages, output_pdf_path, resolution=float(dpi))
