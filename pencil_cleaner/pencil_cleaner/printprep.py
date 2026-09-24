"""
Step 1 of the workflow: you have a scan of PENCILS ONLY (nothing
inked yet) and you want to print it back out on a mono laser printer,
pale enough that you can comfortably ink directly over it -- and pale
enough that a later scan of the inked page won't pick the pencil back
up as gray noise.

This does three things per page:
  * estimates the paper tone (which drifts across a scanned sheet)
    and flattens it out, so you're not fighting scanner vignetting
  * removes any scanner-lid shadow that leaked in around the edges
  * remaps the remaining pencil darkness to a low, even opacity
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Union

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from ._pageio import center_on_page, load_gray, pdf_to_gray_pages, save_pages_as_pdf

# Preset opacities, as a fraction of full black. "light" is the safest
# starting point -- faint enough that most scanners drop it entirely.
PRESETS = {"light": 0.25, "medium": 0.40, "dark": 0.55}


def _paper_lid_mask(gray: np.ndarray) -> np.ndarray:
    """True = keep. Detects big, very dark blobs touching the image
    border (the scanner lid / surrounding room showing through an
    undersized or curled sheet) and excludes them.
    """
    h, w = gray.shape
    sc = 4
    small = cv2.resize(gray, (w // sc, h // sc), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 2)
    dark = (small < 85).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    lid = np.zeros(dark.shape, np.uint8)
    sh, sw = dark.shape
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        touches_border = x == 0 or y == 0 or x + cw >= sw or y + ch >= sh
        if touches_border and area > 1500:
            lid[labels == i] = 1

    lid = cv2.resize(lid, (w, h), interpolation=cv2.INTER_NEAREST)
    lid = cv2.dilate(lid, np.ones((41, 41), np.uint8))
    return ~lid.astype(bool)


def _estimate_paper_tone(gray: np.ndarray) -> np.ndarray:
    """Low-frequency estimate of the (off-white) paper tone, with a
    tighter local estimate blended in near the page edges where
    scanner shadow/curl is worst.
    """
    h, w = gray.shape
    s = 8
    small = cv2.resize(gray, (w // s, h // s), interpolation=cv2.INTER_AREA)
    bg = ndi.percentile_filter(small, 90, size=71, mode="nearest")
    bg = cv2.GaussianBlur(bg, (0, 0), 12)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC)
    bg = np.maximum(bg, 60)

    bg_tight = ndi.percentile_filter(small, 85, size=25, mode="nearest")
    bg_tight = cv2.GaussianBlur(bg_tight, (0, 0), 5)
    bg_tight = cv2.resize(bg_tight, (w, h), interpolation=cv2.INTER_CUBIC)
    bg_tight = np.maximum(bg_tight, 60)

    yy, xx = np.mgrid[0:h, 0:w]
    dist_from_edge = np.minimum(np.minimum(yy, h - 1 - yy), np.minimum(xx, w - 1 - xx))
    edge_weight = np.clip(1 - (dist_from_edge.astype(np.float32) - 120) / 120, 0, 1)

    return bg * (1 - edge_weight) + np.maximum(bg, bg_tight) * edge_weight


def _lighten_pencils(gray: np.ndarray, opacity: float) -> np.ndarray:
    """Flatten paper tone, drop faint paper-grain noise, and rescale
    the remaining pencil darkness to `opacity` (0..1) of full black.
    Returns a 0..1 float array (1.0 = white).
    """
    bg = _estimate_paper_tone(gray)
    normalized = np.clip(gray / bg, 0, 1)
    darkness = 1.0 - normalized
    darkness = np.clip((darkness - 0.07) / 0.93, 0, 1)  # drop paper grain floor
    peak = np.percentile(darkness, 99.7)
    darkness = np.clip(darkness / max(peak, 1e-3), 0, 1)
    out = 1.0 - darkness * opacity

    mask = _paper_lid_mask(gray)
    out = np.where(mask, out, 1.0)
    return out


def prep_pencils_for_printing(
    image_path: Union[str, Path],
    output_path: Union[str, Path, None] = None,
    opacity: Union[str, float] = "light",
) -> Image.Image:
    """Lighten a single pencils-only scan for printing.

    Parameters
    ----------
    image_path : path to the scanned pencil page (any raster format)
    output_path : where to save the result. If None, nothing is saved
        and only the PIL Image is returned.
    opacity : "light", "medium", "dark", or a float 0..1 giving the
        pencil darkness as a fraction of full black. "light" (0.25)
        is the safest default for inking over.

    Returns
    -------
    PIL.Image.Image
    """
    if isinstance(opacity, str):
        opacity = PRESETS[opacity]
    gray = load_gray(image_path)
    out = _lighten_pencils(gray, opacity)
    im = Image.fromarray((out * 255 + 0.5).astype(np.uint8), "L")
    if output_path is not None:
        im.save(output_path)
    return im


def prep_pdf_for_printing(
    pdf_path: Union[str, Path],
    output_pdf_path: Union[str, Path],
    opacity: Union[str, float] = "light",
    dpi: int = 300,
    page_size_pt=(612, 792),  # US Letter
) -> None:
    """Lighten every page of a pencils-only PDF and write a new,
    print-ready PDF. Pages are centered on a page of `page_size_pt`
    (PDF points; default is US Letter) at the given `dpi`.
    """
    if isinstance(opacity, str):
        opacity = PRESETS[opacity]
    page_w = round(page_size_pt[0] / 72 * dpi)
    page_h = round(page_size_pt[1] / 72 * dpi)

    pages: List[Image.Image] = []
    for gray in pdf_to_gray_pages(pdf_path, dpi=dpi):
        out = _lighten_pencils(gray, opacity)
        out = center_on_page(out, page_w, page_h, fill=1.0)
        pages.append(Image.fromarray((out * 255 + 0.5).astype(np.uint8), "L"))

    save_pages_as_pdf(pages, output_pdf_path, resolution=float(dpi))
