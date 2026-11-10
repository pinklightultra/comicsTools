"""Optional local OCR for panel lettering.

Tesseract does the reading. This module does not invent text when the
engine is missing.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from PIL import Image


@dataclass
class OcrResult:
    text: str
    available: bool
    message: str


def recognize(image: Image.Image) -> OcrResult:
    if shutil.which("tesseract") is None:
        return OcrResult(
            text="",
            available=False,
            message=(
                "Tesseract is not on PATH, so lettering was not read. "
                "Install the binary (Debian/Ubuntu: sudo apt install tesseract-ocr) "
                "and the Python package (pip install pytesseract)."
            ),
        )
    try:
        import pytesseract
    except ImportError:
        return OcrResult(
            text="",
            available=False,
            message=(
                "pytesseract is not installed, so lettering was not read. "
                "pip install pytesseract. The tesseract binary also has to be on PATH."
            ),
        )
    try:
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError:
        return OcrResult(
            text="",
            available=False,
            message=(
                "Tesseract was not found, so lettering was not read. "
                "Debian/Ubuntu: sudo apt install tesseract-ocr"
            ),
        )
    return OcrResult(text=(text or "").strip(), available=True, message="")
