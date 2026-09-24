"""Internal helpers shared by printprep and cleanup: loading images,
reading PDF pages to grayscale numpy arrays, and writing multi-page
PDFs back out. Not part of the public API.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import numpy as np
from PIL import Image


def load_gray(path) -> np.ndarray:
    """Load any raster image as a float32 grayscale array, 0..255."""
    im = Image.open(path).convert("L")
    return np.asarray(im).astype(np.float32)


def pdf_to_gray_pages(pdf_path, dpi: int = 300) -> List[np.ndarray]:
    """Render every page of a PDF to a grayscale float32 array.

    Tries PyMuPDF first (pure Python wheel, no system dependency).
    Falls back to the poppler `pdftoppm` command if PyMuPDF isn't
    installed. Raises RuntimeError if neither is available.
    """
    try:
        try:
            import pymupdf as fitz  # PyMuPDF, modern import name
        except ImportError:
            import fitz  # PyMuPDF, legacy import name

        pages = []
        doc = fitz.open(str(pdf_path))
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width
            )
            pages.append(arr.astype(np.float32))
        doc.close()
        if pages:
            return pages
    except ImportError:
        pass

    if shutil.which("pdftoppm") is None:
        raise RuntimeError(
            "Can't read the PDF: install either PyMuPDF (`pip install "
            "pymupdf`) or poppler-utils (provides `pdftoppm`)."
        )

    with tempfile.TemporaryDirectory() as td:
        out_prefix = str(Path(td) / "page")
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(dpi),
                "-gray",
                "-cropbox",
                "-png",
                str(pdf_path),
                out_prefix,
            ],
            check=True,
            capture_output=True,
        )
        files = sorted(Path(td).glob("page-*.png"))
        return [load_gray(f) for f in files]


def save_pages_as_pdf(images: List[Image.Image], out_path, resolution: float = 300.0):
    """Write a list of PIL images out as one multi-page PDF."""
    images = [im.convert("L") for im in images]
    if not images:
        raise ValueError("No pages to save.")
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        resolution=resolution,
    )


def center_on_page(arr: np.ndarray, page_w: int, page_h: int, fill: float = 1.0) -> np.ndarray:
    """Center a 0..1 float array on a blank page of the given pixel size.

    If `arr` is already that size (or larger), it's returned as-is /
    cropped from the center -- this only pads, it never stretches.
    """
    h, w = arr.shape
    canvas = np.full((page_h, page_w), fill, dtype=np.float32)
    if h <= page_h and w <= page_w:
        y = (page_h - h) // 2
        x = (page_w - w) // 2
        canvas[y : y + h, x : x + w] = arr
        return canvas
    # source is bigger than the target page -- center-crop instead of padding
    y0 = max(0, (h - page_h) // 2)
    x0 = max(0, (w - page_w) // 2)
    return arr[y0 : y0 + page_h, x0 : x0 + page_w]
