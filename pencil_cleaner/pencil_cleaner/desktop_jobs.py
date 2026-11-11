"""Run the pencil_cleaner jobs the desktop window calls.

No Qt imports here, so the unit tests can run these without a display.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .cleanup import clean_pdf, remove_pencils
from .deskew import deskew_flatten
from .mark_cleanup import iter_clean_marks
from .panels import cut_panels
from .printer_test import score_printer_test
from .printprep import prep_pdf_for_printing, prep_pencils_for_printing

PathLike = Union[str, Path]

TASKS = ("prep", "clean", "deskew", "panels", "printer", "marks")


@dataclass
class JobResult:
    summary: str
    preview: Optional[Path] = None
    outputs: list[Path] = field(default_factory=list)


def run_job(task: str, **kwargs) -> JobResult:
    if task not in TASKS:
        raise ValueError(f"Unknown task {task!r}")
    return _RUNNERS[task](**kwargs)


def run_prep(
    input_path: PathLike,
    output_path: PathLike,
    opacity: str = "light",
    dpi: int = 300,
) -> JobResult:
    src, dest = _file_paths(input_path, output_path)
    if _is_pdf(src):
        prep_pdf_for_printing(src, dest, opacity=opacity, dpi=int(dpi))
        return JobResult(f"Wrote {dest}", None, [dest])
    prep_pencils_for_printing(src, dest, opacity=opacity)
    return JobResult(f"Wrote {dest}", dest, [dest])


def run_clean(
    input_path: PathLike,
    output_path: PathLike,
    method: str = "morphological",
    dpi: int = 300,
    dark_thresh: int = 60,
    min_stroke_px: int = 5,
    gamma: float = 1.6,
) -> JobResult:
    src, dest = _file_paths(input_path, output_path)
    kwargs = dict(
        method=method,
        dark_thresh=int(dark_thresh),
        min_stroke_px=int(min_stroke_px),
        gamma=float(gamma),
    )
    if _is_pdf(src):
        clean_pdf(src, dest, dpi=int(dpi), **kwargs)
        preview = None
    else:
        remove_pencils(src, dest, **kwargs)
        preview = dest
    note = _clean_note(method)
    summary = f"Wrote {dest}"
    if note:
        summary = f"{summary}.{note}"
    return JobResult(summary, preview, [dest])


def run_deskew(
    input_path: PathLike,
    output_path: PathLike,
    deskew: bool = True,
    flatten: bool = True,
    max_angle: float = 15.0,
    dpi: int = 300,
) -> JobResult:
    src, dest = _file_paths(input_path, output_path)
    written = deskew_flatten(
        src,
        dest,
        deskew=bool(deskew),
        flatten=bool(flatten),
        max_angle=float(max_angle),
        dpi=int(dpi),
    )
    preview = None if _is_pdf(Path(written)) else Path(written)
    return JobResult(f"Wrote {written}", preview, [Path(written)])


def run_panels(
    input_path: PathLike,
    output_path: PathLike,
    min_panel_area_frac: float = 0.008,
    padding_px: int = 8,
    min_gutter_px: int = 8,
) -> JobResult:
    src = _require_file(input_path)
    if _is_pdf(src):
        raise ValueError("Panel cutting takes an image, not a PDF.")
    dest = _require_output(output_path)
    dest.mkdir(parents=True, exist_ok=True)
    paths, overview = cut_panels(
        src,
        dest,
        min_panel_area_frac=float(min_panel_area_frac),
        padding_px=int(padding_px),
        min_gutter_px=int(min_gutter_px),
    )
    lines = [f"Cut {len(paths)} panel(s):"]
    lines.extend(f"  {p}" for p in paths)
    lines.append(f"Overview: {overview}")
    return JobResult("\n".join(lines), Path(overview), [Path(p) for p in paths] + [Path(overview)])


def run_printer(
    input_path: PathLike,
    output_path: PathLike,
    n_swatches: int = 7,
    residual_fail: float = 0.04,
) -> JobResult:
    src = _require_file(input_path)
    dest = _require_output(output_path)
    dest.mkdir(parents=True, exist_ok=True)
    result = score_printer_test(
        src,
        n_swatches=int(n_swatches),
        residual_fail=float(residual_fail),
    )
    annotated = dest / "printer_test_annotated.png"
    table = dest / "printer_test_scores.csv"
    result.save_annotated(annotated)
    result.save_table(table)
    summary = (
        f"{result.summary}\n"
        f"Recommended opacity: {result.recommended_opacity} ({result.recommended_value})\n"
        f"Wrote {annotated}\n"
        f"Wrote {table}"
    )
    return JobResult(summary, annotated, [annotated, table])


def run_marks(
    input_path: PathLike,
    output_path: PathLike,
    dpi: int = 600,
    trim: bool = True,
) -> JobResult:
    src = _require_file(input_path)
    dest = _require_output(output_path)
    lines: list[str] = []
    outputs: list[Path] = []
    preview: Optional[Path] = None
    ok = bad = 0
    for name, written, err in iter_clean_marks(
        [src],
        dest,
        dpi=int(dpi),
        trim=bool(trim),
    ):
        if err:
            lines.append(f"{name}: skipped. {err}")
            bad += 1
        else:
            lines.append(f"{name}: saved {written}")
            ok += 1
            outputs.append(Path(written))
            preview = Path(written)
    if ok == 0:
        detail = "\n".join(lines) or "No pages cleaned."
        raise RuntimeError(detail)
    lines.append(f"Done: {ok} page(s) cleaned" + (f", {bad} skipped." if bad else "."))
    return JobResult("\n".join(lines), preview, outputs)


def suggest_output(task: str, input_path: PathLike) -> Path:
    src = Path(input_path)
    parent = src.parent if str(src.parent) else Path(".")
    stem = src.stem or "scan"
    pdf = _is_pdf(src)
    if task == "prep":
        return parent / f"{stem}-ghost{'.pdf' if pdf else '.png'}"
    if task == "clean":
        return parent / f"{stem}-clean{'.pdf' if pdf else '.png'}"
    if task == "deskew":
        return parent / f"{stem}-straight{'.pdf' if pdf else '.png'}"
    if task == "panels":
        return parent / f"{stem}-panels"
    if task == "printer":
        return parent / f"{stem}-printer-test"
    if task == "marks":
        return parent / f"{stem}-marks"
    raise ValueError(f"Unknown task {task!r}")


def _clean_note(method: str) -> str:
    if method == "gpu":
        from .gpu import is_gpu_available

        if not is_gpu_available():
            return " CUDA is not available, so this used the CPU mask."
        return ""
    if method != "vector":
        return ""
    missing: list[str] = []
    if shutil.which("potrace") is None:
        missing.append("potrace")
    try:
        import cairosvg  # noqa: F401
    except Exception:
        missing.append("cairosvg")
    if not missing:
        return ""
    return " Missing " + " and ".join(missing) + ", so this used the morphological mask."


def _file_paths(input_path: PathLike, output_path: PathLike) -> tuple[Path, Path]:
    src = _require_file(input_path)
    dest = _require_output(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return src, dest


def _require_file(path: PathLike) -> Path:
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"No file at {src}")
    return src


def _require_output(path: PathLike) -> Path:
    text = str(path).strip()
    if not text:
        raise ValueError("Choose an output path.")
    return Path(text)


def _is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


_RUNNERS = {
    "prep": run_prep,
    "clean": run_clean,
    "deskew": run_deskew,
    "panels": run_panels,
    "printer": run_printer,
    "marks": run_marks,
}
