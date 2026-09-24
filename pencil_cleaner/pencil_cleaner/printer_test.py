"""
Printer-test scorer.

Workflow: print a grey-swatch test sheet at various opacities, draw a
pen line across the swatches, scan it, optionally clean it, then score
which grey levels still show residual after cleanup.

Typical swatch sheet has N vertical columns of increasing grey (light
→ dark). Default N=7 matching common prep opacity steps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ._pageio import load_gray
from .cleanup import levels_clean
from .printprep import PRESETS

PathLike = Union[str, Path]

# Default swatch labels as opacity fractions (light → dark).
DEFAULT_LEVELS: Tuple[float, ...] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)

# Residual darkness (0..1) above which a swatch "fails" (ghost remains).
DEFAULT_RESIDUAL_FAIL = 0.04


@dataclass
class SwatchScore:
    index: int
    label_pct: float  # printed opacity as percent (e.g. 25.0 for 0.25)
    residual_pct: float  # measured residual darkness as percent
    passed: bool
    mean_gray: float  # 0..255 paper-ish mean of non-ink pixels


@dataclass
class PrinterTestResult:
    scores: List[SwatchScore]
    recommended_opacity: str
    recommended_value: float
    summary: str
    annotated: Optional[np.ndarray] = field(default=None, repr=False)
    n_swatches: int = 0
    residual_threshold: float = DEFAULT_RESIDUAL_FAIL

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "scores": [asdict(s) for s in self.scores],
            "recommended_opacity": self.recommended_opacity,
            "recommended_value": self.recommended_value,
            "summary": self.summary,
            "n_swatches": self.n_swatches,
            "residual_threshold": self.residual_threshold,
        }
        return d

    def save_annotated(self, path: PathLike) -> Path:
        path = Path(path)
        if self.annotated is None:
            raise ValueError("No annotated preview available")
        Image.fromarray(self.annotated).save(path)
        return path

    def save_table(self, path: PathLike) -> Path:
        path = Path(path)
        lines = ["index,label_pct,residual_pct,passed,mean_gray"]
        for s in self.scores:
            lines.append(
                f"{s.index},{s.label_pct:.2f},{s.residual_pct:.3f},"
                f"{int(s.passed)},{s.mean_gray:.2f}"
            )
        lines.append(f"# recommended_opacity={self.recommended_opacity}")
        lines.append(f"# recommended_value={self.recommended_value}")
        lines.append(f"# {self.summary}")
        path.write_text("\n".join(lines) + "\n")
        return path


def _opacity_preset_name(value: float) -> str:
    """Map a float opacity to nearest named preset, or return 'custom:X.XX'."""
    best = min(PRESETS.items(), key=lambda kv: abs(kv[1] - value))
    if abs(best[1] - value) <= 0.03:
        return best[0]
    return f"custom:{value:.2f}"


def _isolate_pen_line(gray: np.ndarray, black: int = 90, white: int = 200) -> np.ndarray:
    """Aggressive levels cleanup to isolate dark pen ink (0=ink, 255=paper)."""
    return levels_clean(gray.astype(np.float32), black=black, white=white)


def _ghost_cleanup(gray: np.ndarray, black: int = 175, white: int = 208) -> np.ndarray:
    """Mild levels cleanup matching ``method='levels'`` defaults.

    Used to judge whether a printed grey swatch would survive a typical
    post-ink scan cleanup (ghost residual).
    """
    return levels_clean(gray.astype(np.float32), black=black, white=white)


def _find_ink_row(cleaned: np.ndarray) -> int:
    """Find the horizontal row with the most dark (ink) pixels."""
    ink = cleaned < 128
    row_counts = ink.sum(axis=1)
    if row_counts.max() < max(10, cleaned.shape[1] * 0.02):
        # Fall back to middle third
        return cleaned.shape[0] // 2
    return int(np.argmax(row_counts))


def _column_bounds(width: int, n: int, margin_frac: float = 0.04) -> List[Tuple[int, int]]:
    """Split page into n vertical strips with small side margins."""
    margin = int(width * margin_frac)
    usable = width - 2 * margin
    bounds = []
    for i in range(n):
        x0 = margin + int(i * usable / n)
        x1 = margin + int((i + 1) * usable / n)
        # inset a little so we avoid gutter between swatches
        inset = max(2, (x1 - x0) // 10)
        bounds.append((x0 + inset, x1 - inset))
    return bounds


def _measure_residual(
    gray: np.ndarray,
    cleaned: np.ndarray,
    x0: int,
    x1: int,
    ink_row: int,
    band_half: int,
) -> Tuple[float, float]:
    """Mean residual darkness (0..1) and mean gray of non-ink pixels in a column band."""
    h = gray.shape[0]
    y0 = max(0, ink_row - band_half)
    y1 = min(h, ink_row + band_half)
    # Sample above and below the ink line, excluding a thin ink corridor
    corridor = max(4, band_half // 5)
    regions = []
    if y0 < ink_row - corridor:
        regions.append((y0, ink_row - corridor))
    if ink_row + corridor < y1:
        regions.append((ink_row + corridor, y1))
    if not regions:
        regions = [(y0, y1)]

    samples = []
    for ya, yb in regions:
        patch = gray[ya:yb, x0:x1].astype(np.float32)
        clean_patch = cleaned[ya:yb, x0:x1]
        # non-ink: cleaned is paper-bright
        mask = clean_patch > 180
        if mask.sum() < 20:
            mask = np.ones_like(clean_patch, dtype=bool)
        samples.append(patch[mask])

    vals = np.concatenate(samples) if samples else gray[:, x0:x1].ravel()
    mean_gray = float(np.mean(vals))
    # residual darkness relative to white paper
    residual = max(0.0, (255.0 - mean_gray) / 255.0)
    return residual, mean_gray


def _annotate(
    gray: np.ndarray,
    scores: Sequence[SwatchScore],
    bounds: Sequence[Tuple[int, int]],
    ink_row: int,
    band_half: int,
) -> np.ndarray:
    """RGB annotated preview with column boxes and pass/fail labels."""
    rgb = cv2.cvtColor(np.clip(gray, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    h, w = gray.shape
    y0 = max(0, ink_row - band_half)
    y1 = min(h - 1, ink_row + band_half)
    draw.line([(0, ink_row), (w - 1, ink_row)], fill=(0, 120, 255), width=2)

    for score, (x0, x1) in zip(scores, bounds):
        color = (40, 180, 60) if score.passed else (220, 50, 50)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
        label = f"{score.label_pct:.0f}% {'OK' if score.passed else 'FAIL'}\nr={score.residual_pct:.1f}%"
        ty = max(4, y0 - 36)
        draw.rectangle([x0, ty, min(w - 1, x0 + 90), ty + 32], fill=(255, 255, 255))
        draw.text((x0 + 4, ty + 2), label, fill=color, font=font)

    return np.asarray(pil)


def score_printer_test(
    image_path: PathLike,
    cleaned_path: Optional[PathLike] = None,
    n_swatches: int = 7,
    levels: Optional[Sequence[float]] = None,
    residual_fail: float = DEFAULT_RESIDUAL_FAIL,
    band_frac: float = 0.12,
    run_cleanup: bool = True,
    region: Optional[Tuple[int, int, int, int]] = None,
) -> PrinterTestResult:
    """Score a scanned printer-test sheet.

    Parameters
    ----------
    image_path
        Scanned test image (grey swatches + pen line across them).
    cleaned_path
        Optional already-cleaned version. If omitted and ``run_cleanup``
        is True, a light ``levels_clean`` is applied internally to
        isolate the pen line.
    n_swatches
        Number of vertical swatch columns (default 7).
    levels
        Opacity values for each column, light→dark. Defaults to
        ``(0.10, 0.15, ..., 0.40)`` truncated/padded to ``n_swatches``.
    residual_fail
        Residual darkness (0..1) above which a swatch fails.
    band_frac
        Fraction of page height used as the sampling band around the
        detected pen line.
    run_cleanup
        If True and no ``cleaned_path``, run internal levels cleanup.
    region
        Optional ``(x0, y0, x1, y1)`` crop before scoring.

    Returns
    -------
    PrinterTestResult
        Scored table, recommended opacity preset string, annotated image.
    """
    gray = load_gray(image_path)
    if region is not None:
        x0, y0, x1, y1 = region
        gray = gray[y0:y1, x0:x1]

    if cleaned_path is not None:
        cleaned = load_gray(cleaned_path)
        if region is not None:
            cleaned = cleaned[y0:y1, x0:x1]
        if cleaned.shape != gray.shape:
            cleaned = cv2.resize(
                cleaned, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA
            )
    elif run_cleanup:
        # Mild cleanup for residual scoring (matches levels method defaults).
        cleaned = _ghost_cleanup(gray)
    else:
        cleaned = gray.copy()

    # Separate aggressive clean just for locating the pen stroke.
    pen_map = _isolate_pen_line(gray)

    if levels is None:
        levels = DEFAULT_LEVELS
    levels = list(levels)
    if len(levels) < n_swatches:
        # extend linearly
        if len(levels) >= 2:
            step = levels[-1] - levels[-2]
        else:
            step = 0.05
        while len(levels) < n_swatches:
            levels.append(round(levels[-1] + step, 4))
    levels = levels[:n_swatches]

    h, w = gray.shape
    ink_row = _find_ink_row(pen_map)
    band_half = max(20, int(h * band_frac / 2))
    bounds = _column_bounds(w, n_swatches)

    scores: List[SwatchScore] = []
    for i, ((x0, x1), lev) in enumerate(zip(bounds, levels)):
        # Pass/fail is about residual AFTER cleanup (ghost that survives).
        residual_c, mean_c = _measure_residual(
            cleaned, cleaned, x0, x1, ink_row, band_half
        )
        passed = residual_c <= residual_fail
        scores.append(
            SwatchScore(
                index=i,
                label_pct=round(lev * 100, 2),
                residual_pct=round(residual_c * 100, 3),
                passed=passed,
                mean_gray=round(mean_c, 2),
            )
        )

    # Recommend: lightest swatch that still had visible residual → go lighter
    # than that. If all clear, report ok at the darkest tested (or 'light').
    failed = [s for s in scores if not s.passed]
    if not failed:
        # all clear — recommend the darkest level that still passed, or light preset
        darkest_ok = levels[-1]
        rec_value = darkest_ok
        # Prefer a known preset at or below darkest_ok
        candidates = [v for v in PRESETS.values() if v <= darkest_ok + 0.01]
        if candidates:
            rec_value = max(candidates)
        rec_name = _opacity_preset_name(rec_value)
        summary = (
            f"All {n_swatches} swatches clear after cleanup. "
            f"Opacity up to ~{darkest_ok:.2f} looks safe; "
            f"recommended preset: {rec_name!r}."
        )
    else:
        # lightest failing swatch — suggest going lighter than that
        lightest_fail = min(failed, key=lambda s: s.label_pct)
        fail_val = lightest_fail.label_pct / 100.0
        # step one notch lighter than the failing level
        lighter = [v for v in levels if v < fail_val - 1e-6]
        if lighter:
            rec_value = max(lighter)
        else:
            rec_value = max(0.05, fail_val * 0.7)
        # Prefer named preset at or below rec_value
        candidates = [v for v in PRESETS.values() if v <= rec_value + 0.01]
        if candidates:
            rec_value = max(candidates)
        rec_name = _opacity_preset_name(rec_value)
        summary = (
            f"{len(failed)}/{n_swatches} swatch(es) still show residual. "
            f"Lightest failing ~{lightest_fail.label_pct:.0f}% — "
            f"go lighter; recommended preset: {rec_name!r}."
        )

    annotated = _annotate(gray, scores, bounds, ink_row, band_half)

    return PrinterTestResult(
        scores=scores,
        recommended_opacity=rec_name,
        recommended_value=float(rec_value),
        summary=summary,
        annotated=annotated,
        n_swatches=n_swatches,
        residual_threshold=residual_fail,
    )
