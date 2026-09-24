"""
Deskew / flatten only.

Straightens a skewed scan and optionally evens out lighting. Does
NOT strip pencil or ink — just geometry + illumination.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

from ._pageio import load_gray, pdf_to_gray_pages, save_pages_as_pdf

PathLike = Union[str, Path]


def flatten_lighting(gray: np.ndarray, bg_px: Optional[float] = None) -> np.ndarray:
    """Divide out uneven lighting. Returns float32 0..255, paper ~255.

    Reuses the same idea as ``mark_cleanup.flatten`` (large-kernel
    background estimate via downsample → dilate → blur → upsample).
    """
    g = gray.astype(np.float32)
    h, w = g.shape
    if bg_px is None:
        bg_px = max(h, w) * 0.08
    f = max(1, int(bg_px // 24))
    small = cv2.resize(
        g,
        (max(1, w // f), max(1, h // f)),
        interpolation=cv2.INTER_AREA,
    )
    k = max(3, int(bg_px / f) | 1)
    bg = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), max(k / 2, 1))
    bg = np.maximum(bg, np.percentile(bg, 50) * 0.75)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(g / np.maximum(bg, 1.0) * 255.0, 0, 255)


def estimate_skew_angle(gray: np.ndarray, max_angle: float = 15.0) -> float:
    """Estimate page skew in degrees (positive = counter-clockwise).

    Tries minAreaRect on dark content first; falls back to Hough lines
    on Canny edges. Returns 0.0 if nothing reliable is found.
    """
    g = np.clip(gray, 0, 255).astype(np.uint8)
    h, w = g.shape
    # Downscale for speed
    scale = 1200.0 / max(h, w)
    if scale < 1.0:
        small = cv2.resize(g, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = g
        scale = 1.0

    # Dark content mask
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)

    coords = cv2.findNonZero(thr)
    angle_rect: Optional[float] = None
    if coords is not None and len(coords) > 50:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        # OpenCV minAreaRect angle is in [-90, 0); normalize to skew of long axis
        rw, rh = rect[1]
        if rw < rh:
            angle = angle + 90.0
        # Convert to "how much to rotate to straighten" in (-45, 45]
        if angle > 45:
            angle -= 90
        elif angle <= -45:
            angle += 90
        if abs(angle) <= max_angle:
            angle_rect = float(angle)

    # Hough fallback / confirmation
    edges = cv2.Canny(blur, 50, 150)
    lines = cv2.HoughLines(edges, 1, np.pi / 180.0, threshold=max(80, small.shape[1] // 4))
    angle_hough: Optional[float] = None
    if lines is not None and len(lines) > 0:
        angles = []
        for rho_theta in lines[:80]:
            rho, theta = rho_theta[0]
            # theta ~ 0 → vertical line; ~ pi/2 → horizontal
            deg = (theta * 180.0 / np.pi) - 90.0  # horizontal-ish → near 0
            # Keep near-horizontal lines
            if abs(deg) <= max_angle:
                angles.append(deg)
            elif abs(deg + 90) <= max_angle:
                angles.append(deg + 90)
            elif abs(deg - 90) <= max_angle:
                angles.append(deg - 90)
        if angles:
            angle_hough = float(np.median(angles))

    if angle_rect is not None and angle_hough is not None:
        # Prefer Hough if they disagree a lot (minAreaRect can latch on art)
        if abs(angle_rect - angle_hough) > 3.0:
            return angle_hough
        return (angle_rect + angle_hough) / 2.0
    if angle_hough is not None:
        return angle_hough
    if angle_rect is not None:
        return angle_rect
    return 0.0


def rotate_image(gray: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate around center; fill with white (255)."""
    if abs(angle_deg) < 0.05:
        return gray.astype(np.float32)
    h, w = gray.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle_deg, 1.0)
    # Expand canvas so corners aren't clipped
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nW = int(h * sin + w * cos)
    nH = int(h * cos + w * sin)
    M[0, 2] += (nW - w) / 2
    M[1, 2] += (nH - h) / 2
    out = cv2.warpAffine(
        gray.astype(np.float32),
        M,
        (nW, nH),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    return out


def deskew_array(
    gray: np.ndarray,
    deskew: bool = True,
    flatten: bool = True,
    max_angle: float = 15.0,
) -> Tuple[np.ndarray, float]:
    """Deskew and/or flatten a grayscale array. Returns (result, angle_used)."""
    angle = 0.0
    out = gray.astype(np.float32)
    if flatten:
        out = flatten_lighting(out)
    if deskew:
        angle = estimate_skew_angle(out, max_angle=max_angle)
        if abs(angle) > 0.05 and abs(angle) <= max_angle:
            out = rotate_image(out, angle)
        else:
            angle = 0.0 if abs(angle) > max_angle else angle
            if abs(angle) > max_angle:
                angle = 0.0
    return np.clip(out, 0, 255), float(angle)


def deskew_flatten(
    input_path: PathLike,
    output_path: PathLike,
    deskew: bool = True,
    flatten: bool = True,
    max_angle: float = 15.0,
    dpi: int = 300,
) -> Path:
    """Deskew and/or flatten an image (or multipage PDF).

    Does not strip pencil/ink — only straighten and even lighting.

    Parameters
    ----------
    input_path, output_path
        Image or PDF paths. PDF in → PDF out; otherwise PNG/JPEG etc.
    deskew
        Rotate to correct skew (default True).
    flatten
        Even out lighting via large-kernel background division (default True).
    max_angle
        Ignore estimated angles larger than this (degrees).
    dpi
        Render DPI when input is PDF.

    Returns
    -------
    Path
        ``output_path``.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if input_path.suffix.lower() == ".pdf":
        pages_out: List[Image.Image] = []
        for gray in pdf_to_gray_pages(input_path, dpi=dpi):
            result, _ = deskew_array(gray, deskew=deskew, flatten=flatten, max_angle=max_angle)
            pages_out.append(Image.fromarray(result.astype(np.uint8), "L"))
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path.with_suffix(".pdf")
        save_pages_as_pdf(pages_out, output_path, resolution=float(dpi))
        return output_path

    gray = load_gray(input_path)
    result, _ = deskew_array(gray, deskew=deskew, flatten=flatten, max_angle=max_angle)
    Image.fromarray(result.astype(np.uint8), "L").save(output_path)
    return output_path
