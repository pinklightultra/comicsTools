"""Gradio web app for pencil_cleaner.

Launch::

    python -m pencil_cleaner app
    pencil-cleaner-app

Or for Hugging Face Spaces, use the root-level ``app.py`` which imports
``demo`` from here.
"""
from __future__ import annotations

import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
PDF_EXTS = {".pdf"}
ALL_EXTS = IMAGE_EXTS | PDF_EXTS


def _as_path_list(files: Any) -> List[Path]:
    """Normalize Gradio File input (None / single / list / dict) to Paths."""
    if files is None:
        return []
    if isinstance(files, (str, Path)):
        return [Path(files)]
    if isinstance(files, dict):
        # Gradio sometimes returns {"path": ..., "orig_name": ...}
        p = files.get("path") or files.get("name")
        return [Path(p)] if p else []
    out: List[Path] = []
    for f in files:
        if f is None:
            continue
        if isinstance(f, dict):
            p = f.get("path") or f.get("name")
            if p:
                out.append(Path(p))
        else:
            # NamedString / tempfile / str
            name = getattr(f, "name", None) or str(f)
            out.append(Path(name))
    return out


def _orig_name(path: Path, files: Any) -> str:
    """Best-effort original filename for a Gradio upload."""
    if isinstance(files, dict) and files.get("orig_name"):
        return Path(files["orig_name"]).name
    if isinstance(files, (list, tuple)):
        for f in files:
            if isinstance(f, dict):
                p = f.get("path") or f.get("name")
                if p and Path(p).resolve() == path.resolve() and f.get("orig_name"):
                    return Path(f["orig_name"]).name
            else:
                n = getattr(f, "name", None)
                if n and Path(n).resolve() == path.resolve():
                    orig = getattr(f, "orig_name", None)
                    if orig:
                        return Path(orig).name
    return path.name


def _work_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="pencil_cleaner_"))
    return d


def _zip_outputs(paths: Sequence[Path], zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=p.name)
    return zip_path


def _preview_pair(
    before_path: Optional[Path], after_path: Optional[Path]
) -> Tuple[Optional[str], Optional[str]]:
    before = str(before_path) if before_path and before_path.suffix.lower() in IMAGE_EXTS else None
    after = str(after_path) if after_path and after_path.suffix.lower() in IMAGE_EXTS else None
    return before, after


# ---------------------------------------------------------------------------
# Tab handlers
# ---------------------------------------------------------------------------

def run_prep(
    files: Any,
    opacity_preset: str,
    use_custom: bool,
    custom_opacity: float,
    dpi: int,
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Prep pencils. Returns (download, before_preview, after_preview, status)."""
    from .printprep import prep_pdf_for_printing, prep_pencils_for_printing

    paths = _as_path_list(files)
    if not paths:
        return None, None, None, "Upload at least one image or PDF of your pencils-only page."

    opacity: Union[str, float] = custom_opacity if use_custom else opacity_preset
    out_dir = _work_dir()
    written: List[Path] = []
    first_before: Optional[Path] = None
    first_after: Optional[Path] = None
    messages: List[str] = []

    try:
        for src in paths:
            if src.suffix.lower() not in ALL_EXTS:
                messages.append(f"Skipped {src.name}: unsupported type")
                continue
            base = _orig_name(src, files)
            stem = Path(base).stem
            if src.suffix.lower() == ".pdf":
                dest = out_dir / f"{stem}_print.pdf"
                prep_pdf_for_printing(src, dest, opacity=opacity, dpi=int(dpi))
            else:
                dest = out_dir / f"{stem}_print.png"
                prep_pencils_for_printing(src, dest, opacity=opacity)
                if first_before is None:
                    first_before = src
                    first_after = dest
            written.append(dest)
            messages.append(f"Wrote {dest.name}")

        if not written:
            return None, None, None, "Nothing processed.\n" + "\n".join(messages)

        if len(written) == 1:
            download: Optional[str] = str(written[0])
        else:
            zpath = out_dir / "prep_results.zip"
            _zip_outputs(written, zpath)
            download = str(zpath)

        before, after = _preview_pair(first_before, first_after)
        status = (
            f"Done — {len(written)} file(s). Opacity={opacity!r}.\n"
            + "\n".join(messages)
        )
        return download, before, after, status
    except Exception:
        return None, None, None, "Error:\n" + traceback.format_exc()


def run_clean(
    files: Any,
    method: str,
    dark_thresh: int,
    min_stroke_px: int,
    black: Optional[float],
    white: Optional[float],
    gamma: float,
    dpi: int,
    save_svg: bool,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str]:
    """Clean inked scans. Returns (download, svg, before, after, status)."""
    from .cleanup import clean_pdf, remove_pencils

    paths = _as_path_list(files)
    if not paths:
        return None, None, None, None, "Upload at least one inked scan (image or PDF)."

    def _opt_int(v) -> Optional[int]:
        if v is None:
            return None
        try:
            if isinstance(v, float) and v != v:  # NaN
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    black_i = _opt_int(black)
    white_i = _opt_int(white)

    out_dir = _work_dir()
    written: List[Path] = []
    svg_out: Optional[Path] = None
    first_before: Optional[Path] = None
    first_after: Optional[Path] = None
    messages: List[str] = []

    try:
        for src in paths:
            if src.suffix.lower() not in ALL_EXTS:
                messages.append(f"Skipped {src.name}: unsupported type")
                continue
            base = _orig_name(src, files)
            stem = Path(base).stem
            if src.suffix.lower() == ".pdf":
                dest = out_dir / f"{stem}_clean.pdf"
                clean_pdf(
                    src,
                    dest,
                    method=method,
                    dpi=int(dpi),
                    dark_thresh=int(dark_thresh),
                    min_stroke_px=int(min_stroke_px),
                    black=black_i,
                    white=white_i,
                    gamma=float(gamma),
                )
            else:
                dest = out_dir / f"{stem}_clean.png"
                this_svg = None
                if save_svg and method == "vector" and len(paths) == 1:
                    this_svg = out_dir / f"{stem}_ink.svg"
                    svg_out = this_svg
                remove_pencils(
                    src,
                    dest,
                    method=method,
                    dark_thresh=int(dark_thresh),
                    min_stroke_px=int(min_stroke_px),
                    black=black_i,
                    white=white_i,
                    gamma=float(gamma),
                    svg_output_path=this_svg,
                )
                if first_before is None:
                    first_before = src
                    first_after = dest
            written.append(dest)
            messages.append(f"Wrote {dest.name}")

        if not written:
            return None, None, None, None, "Nothing processed.\n" + "\n".join(messages)

        extras = list(written)
        if svg_out and svg_out.exists():
            extras.append(svg_out)

        if len(extras) == 1:
            download: Optional[str] = str(extras[0])
        else:
            zpath = out_dir / "clean_results.zip"
            _zip_outputs(extras, zpath)
            download = str(zpath)

        svg_dl = str(svg_out) if svg_out and svg_out.exists() else None
        before, after = _preview_pair(first_before, first_after)
        note = ""
        if save_svg and method != "vector":
            note = "\n(SVG only applies with method=vector — skipped.)"
        elif save_svg and len(paths) > 1:
            note = "\n(SVG export is for a single image input — skipped for batch.)"
        status = (
            f"Done — {len(written)} file(s). Method={method!r}.{note}\n"
            + "\n".join(messages)
        )
        return download, svg_dl, before, after, status
    except Exception:
        return None, None, None, None, "Error:\n" + traceback.format_exc()


def run_marks(
    files: Any,
    dpi: int,
    trim: bool,
    debug: bool,
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Corner-mark cleanup. Returns (download, before, after, status)."""
    from .mark_cleanup import iter_clean_marks

    paths = _as_path_list(files)
    if not paths:
        return None, None, None, (
            "Upload scans of pages that were printed with four corner "
            "registration marks (image or PDF)."
        )

    out_dir = _work_dir()
    written: List[Path] = []
    messages: List[str] = []
    first_before: Optional[Path] = None
    first_after: Optional[Path] = None

    try:
        for name, dest, err in iter_clean_marks(
            paths,
            out_dir,
            dpi=int(dpi),
            trim=bool(trim),
            debug=bool(debug),
        ):
            if err:
                messages.append(f"{name}: skipped — {err}")
            else:
                messages.append(f"{name}: saved {dest.name if dest else '?'}")
                if dest:
                    written.append(dest)
                    if first_after is None:
                        first_after = dest
                        # pick a matching source image for preview if possible
                        for src in paths:
                            if src.suffix.lower() in IMAGE_EXTS:
                                first_before = src
                                break

        if not written:
            return None, None, None, (
                "No pages cleaned. Make sure all four corners of the sheet "
                "are visible in the scan.\n" + "\n".join(messages)
            )

        if len(written) == 1:
            download: Optional[str] = str(written[0])
        else:
            # include debug jpgs if present
            extras = list(written)
            for p in out_dir.glob("*-marks.jpg"):
                extras.append(p)
            zpath = out_dir / "marks_results.zip"
            _zip_outputs(extras, zpath)
            download = str(zpath)

        before, after = _preview_pair(first_before, first_after)
        status = f"Done — {len(written)} page(s).\n" + "\n".join(messages)
        return download, before, after, status
    except Exception:
        return None, None, None, "Error:\n" + traceback.format_exc()



def run_printer_test(
    file: Any,
    n_swatches: int,
    residual_fail: float,
    run_cleanup: bool,
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Score printer test. Returns (download, annotated, table_csv, status)."""
    from .printer_test import score_printer_test

    paths = _as_path_list(file)
    if not paths:
        return None, None, None, (
            "Upload a scanned grey-swatch test sheet (with a pen line across the swatches)."
        )
    src = paths[0]
    out_dir = _work_dir()
    try:
        result = score_printer_test(
            src,
            n_swatches=int(n_swatches),
            residual_fail=float(residual_fail),
            run_cleanup=bool(run_cleanup),
        )
        ann = out_dir / "printer_test_annotated.png"
        tbl = out_dir / "printer_test_scores.csv"
        result.save_annotated(ann)
        result.save_table(tbl)
        zpath = out_dir / "printer_test_results.zip"
        _zip_outputs([ann, tbl], zpath)
        lines = [
            result.summary,
            f"Recommended opacity: {result.recommended_opacity} ({result.recommended_value})",
            "",
            "index  label%  residual%  pass",
        ]
        for s in result.scores:
            lines.append(
                f"  {s.index:2d}   {s.label_pct:5.1f}   {s.residual_pct:7.2f}   "
                f"{'PASS' if s.passed else 'FAIL'}"
            )
        return str(zpath), str(ann), str(tbl), "\n".join(lines)
    except Exception:
        return None, None, None, "Error:\n" + traceback.format_exc()


def run_deskew(
    files: Any,
    do_deskew: bool,
    do_flatten: bool,
    max_angle: float,
    dpi: int,
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Deskew/flatten. Returns (download, before, after, status)."""
    from .deskew import deskew_flatten

    paths = _as_path_list(files)
    if not paths:
        return None, None, None, "Upload at least one skewed / unevenly lit scan (image or PDF)."

    out_dir = _work_dir()
    written: List[Path] = []
    messages: List[str] = []
    first_before: Optional[Path] = None
    first_after: Optional[Path] = None

    try:
        for src in paths:
            if src.suffix.lower() not in ALL_EXTS:
                messages.append(f"Skipped {src.name}: unsupported type")
                continue
            base = _orig_name(src, files)
            stem = Path(base).stem
            if src.suffix.lower() == ".pdf":
                dest = out_dir / f"{stem}_deskew.pdf"
            else:
                dest = out_dir / f"{stem}_deskew.png"
            deskew_flatten(
                src,
                dest,
                deskew=bool(do_deskew),
                flatten=bool(do_flatten),
                max_angle=float(max_angle),
                dpi=int(dpi),
            )
            written.append(dest)
            messages.append(f"Wrote {dest.name}")
            if first_before is None and src.suffix.lower() in IMAGE_EXTS:
                first_before = src
                first_after = dest

        if not written:
            return None, None, None, "Nothing processed.\n" + "\n".join(messages)

        if len(written) == 1:
            download: Optional[str] = str(written[0])
        else:
            zpath = out_dir / "deskew_results.zip"
            _zip_outputs(written, zpath)
            download = str(zpath)

        before, after = _preview_pair(first_before, first_after)
        status = (
            f"Done — {len(written)} file(s). deskew={do_deskew}, flatten={do_flatten}.\n"
            + "\n".join(messages)
        )
        return download, before, after, status
    except Exception:
        return None, None, None, "Error:\n" + traceback.format_exc()


def run_panels(
    file: Any,
    min_area_frac: float,
    padding_px: int,
    min_gutter_px: int,
) -> Tuple[Optional[str], Optional[str], str]:
    """Cut panels. Returns (download_zip, overview, status)."""
    from .panels import PanelCutError, cut_panels

    paths = _as_path_list(file)
    if not paths:
        return None, None, "Upload a comic page image with clear gutters between panels."
    src = paths[0]
    if src.suffix.lower() not in IMAGE_EXTS:
        return None, None, "Panel cutter currently accepts raster images (PNG/JPG/TIFF…), not PDF."

    out_dir = _work_dir()
    try:
        panel_paths, overview = cut_panels(
            src,
            out_dir,
            min_panel_area_frac=float(min_area_frac),
            padding_px=int(padding_px),
            min_gutter_px=int(min_gutter_px),
        )
        extras = list(panel_paths) + [overview]
        zpath = out_dir / "panels_results.zip"
        _zip_outputs(extras, zpath)
        status = f"Cut {len(panel_paths)} panel(s).\n" + "\n".join(p.name for p in panel_paths)
        return str(zpath), str(overview), status
    except PanelCutError as exc:
        return None, None, f"Could not cut panels: {exc}"
    except Exception:
        return None, None, "Error:\n" + traceback.format_exc()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

INTRO = """
# pencil_cleaner

**Workflow:** draw in pencil → prep & print a faint ghost → ink on paper → scan → clean.

Also: score printer-test swatches, deskew/flatten scans, and cut comic panels.

Use the tabs below. Upload comic-page scans (PNG/JPG/TIFF or PDF). Results download as PNG/PDF (or a zip for batches).
"""


def build_demo():
    """Construct the Gradio Blocks demo (does not launch)."""
    import gradio as gr

    with gr.Blocks(title="pencil_cleaner") as demo:
        gr.Markdown(INTRO)

        with gr.Tabs():
            # ---- Tab 1: Prep ----
            with gr.Tab("Prep pencils for printing"):
                gr.Markdown(
                    "Upload a **pencils-only** scan. This lightens it to a faint, even grey "
                    "so a mono laser printer can print a ghost for inking — faint enough that "
                    "a later scan of the inked page usually won't pick the pencil back up."
                )
                prep_files = gr.File(
                    label="Pencils scan(s) — image or PDF",
                    file_count="multiple",
                    file_types=["image", ".pdf"],
                )
                with gr.Row():
                    prep_opacity = gr.Radio(
                        choices=["light", "medium", "dark"],
                        value="light",
                        label="Opacity preset",
                        info="light (0.25) is safest for scanners to drop later",
                    )
                    prep_use_custom = gr.Checkbox(label="Use custom opacity", value=False)
                    prep_custom = gr.Slider(
                        0.05, 1.0, value=0.25, step=0.05, label="Custom opacity (0–1)"
                    )
                prep_dpi = gr.Number(value=300, label="DPI (PDF only)", precision=0)
                prep_btn = gr.Button("Run prep", variant="primary")
                prep_download = gr.File(label="Download result")
                with gr.Row():
                    prep_before = gr.Image(label="Before (first image)", type="filepath")
                    prep_after = gr.Image(label="After (first image)", type="filepath")
                prep_status = gr.Textbox(label="Status", lines=6)

                prep_btn.click(
                    fn=run_prep,
                    inputs=[prep_files, prep_opacity, prep_use_custom, prep_custom, prep_dpi],
                    outputs=[prep_download, prep_before, prep_after, prep_status],
                )

            # ---- Tab 2: Clean ----
            with gr.Tab("Clean inked scans"):
                gr.Markdown(
                    "Upload an **inked** scan that still shows pencil ghosts, eraser smudges, "
                    "or scanner-lid shadow. Strips everything that isn't ink.\n\n"
                    "- **levels** — fast brightness remap (when pencil is clearly lighter than ink)\n"
                    "- **morphological** — separates by stroke thickness (the “inker”)\n"
                    "- **vector** (default) — traces with potrace for cleanest edges + optional SVG"
                )
                clean_files = gr.File(
                    label="Inked scan(s) — image or PDF",
                    file_count="multiple",
                    file_types=["image", ".pdf"],
                )
                clean_method = gr.Radio(
                    choices=["levels", "morphological", "vector"],
                    value="vector",
                    label="Method",
                )
                with gr.Accordion("Advanced", open=False):
                    with gr.Row():
                        clean_dark = gr.Slider(10, 200, value=60, step=1, label="dark_thresh")
                        clean_stroke = gr.Slider(1, 30, value=5, step=1, label="min_stroke_px")
                    with gr.Row():
                        clean_black = gr.Number(
                            value=None,
                            label="black (blank = method default)",
                            precision=0,
                        )
                        clean_white = gr.Number(
                            value=None,
                            label="white (blank = method default)",
                            precision=0,
                        )
                        clean_gamma = gr.Slider(0.5, 3.0, value=1.6, step=0.1, label="gamma")
                        clean_dpi = gr.Number(value=300, label="DPI (PDF)", precision=0)
                    clean_svg = gr.Checkbox(
                        label="Also save SVG (vector method, single image only)",
                        value=False,
                    )
                clean_btn = gr.Button("Run clean", variant="primary")
                with gr.Row():
                    clean_download = gr.File(label="Download cleaned result")
                    clean_svg_dl = gr.File(label="Download SVG (if requested)")
                with gr.Row():
                    clean_before = gr.Image(label="Before", type="filepath")
                    clean_after = gr.Image(label="After", type="filepath")
                clean_status = gr.Textbox(label="Status", lines=6)

                clean_btn.click(
                    fn=run_clean,
                    inputs=[
                        clean_files,
                        clean_method,
                        clean_dark,
                        clean_stroke,
                        clean_black,
                        clean_white,
                        clean_gamma,
                        clean_dpi,
                        clean_svg,
                    ],
                    outputs=[
                        clean_download,
                        clean_svg_dl,
                        clean_before,
                        clean_after,
                        clean_status,
                    ],
                )

            # ---- Tab 3: Corner marks ----
            with gr.Tab("Corner-mark cleanup"):
                gr.Markdown(
                    "For pages **printed with four corner registration marks** "
                    "(e.g. square at top-left). Finds the marks, straightens the scan, "
                    "strips the light-grey laser print, and keeps your black ink.\n\n"
                    "All four corners of the sheet must be visible. PDF scans need "
                    "`pypdfium2` (`pip install pencil-cleaner[all]`)."
                )
                marks_files = gr.File(
                    label="Printed-page scan(s) — image or PDF",
                    file_count="multiple",
                    file_types=["image", ".pdf"],
                )
                with gr.Row():
                    marks_dpi = gr.Number(value=600, label="Output DPI", precision=0)
                    marks_trim = gr.Checkbox(label="Trim to ink bounding box", value=True)
                    marks_debug = gr.Checkbox(label="Also save mark-debug images", value=False)
                marks_btn = gr.Button("Run corner-mark cleanup", variant="primary")
                marks_download = gr.File(label="Download result")
                with gr.Row():
                    marks_before = gr.Image(label="Before (first image)", type="filepath")
                    marks_after = gr.Image(label="After (first cleaned page)", type="filepath")
                marks_status = gr.Textbox(label="Status", lines=8)

                marks_btn.click(
                    fn=run_marks,
                    inputs=[marks_files, marks_dpi, marks_trim, marks_debug],
                    outputs=[marks_download, marks_before, marks_after, marks_status],
                )

            # ---- Tab 4: Printer test ----
            with gr.Tab("Printer test scorer"):
                gr.Markdown(
                    "Print a **grey swatch test sheet**, draw a pen line across the swatches, "
                    "scan it, then score which grey levels still show after cleanup.\n\n"
                    "Use the recommendation to pick a safer **prep opacity** "
                    "(light / medium / dark) so the laser ghost disappears on the next scan."
                )
                pt_file = gr.File(
                    label="Scanned test sheet (image)",
                    file_count="single",
                    file_types=["image"],
                )
                with gr.Row():
                    pt_n = gr.Slider(3, 12, value=7, step=1, label="Number of swatches")
                    pt_fail = gr.Slider(
                        0.01, 0.15, value=0.04, step=0.01, label="Residual fail threshold"
                    )
                    pt_cleanup = gr.Checkbox(label="Run light levels cleanup internally", value=True)
                pt_btn = gr.Button("Score printer test", variant="primary")
                pt_download = gr.File(label="Download (zip: annotated + CSV)")
                with gr.Row():
                    pt_ann = gr.Image(label="Annotated preview", type="filepath")
                    pt_csv = gr.File(label="Scores CSV")
                pt_status = gr.Textbox(label="Status / recommendation", lines=10)

                pt_btn.click(
                    fn=run_printer_test,
                    inputs=[pt_file, pt_n, pt_fail, pt_cleanup],
                    outputs=[pt_download, pt_ann, pt_csv, pt_status],
                )

            # ---- Tab 5: Deskew ----
            with gr.Tab("Deskew & flatten"):
                gr.Markdown(
                    "Straighten a skewed scan and/or even out lighting. "
                    "**Does not strip pencil or ink** — geometry and illumination only. "
                    "Useful before cleaning or for scans that just need to sit square on the page."
                )
                dk_files = gr.File(
                    label="Scan(s) — image or PDF",
                    file_count="multiple",
                    file_types=["image", ".pdf"],
                )
                with gr.Row():
                    dk_deskew = gr.Checkbox(label="Deskew (rotate)", value=True)
                    dk_flatten = gr.Checkbox(label="Flatten lighting", value=True)
                    dk_angle = gr.Slider(1, 45, value=15, step=1, label="Max angle (°)")
                    dk_dpi = gr.Number(value=300, label="DPI (PDF)", precision=0)
                dk_btn = gr.Button("Run deskew / flatten", variant="primary")
                dk_download = gr.File(label="Download result")
                with gr.Row():
                    dk_before = gr.Image(label="Before", type="filepath")
                    dk_after = gr.Image(label="After", type="filepath")
                dk_status = gr.Textbox(label="Status", lines=6)

                dk_btn.click(
                    fn=run_deskew,
                    inputs=[dk_files, dk_deskew, dk_flatten, dk_angle, dk_dpi],
                    outputs=[dk_download, dk_before, dk_after, dk_status],
                )

            # ---- Tab 6: Panels ----
            with gr.Tab("Panel cutter"):
                gr.Markdown(
                    "Find comic **panel gutters** (horizontal/vertical white gaps) and export "
                    "each panel as its own PNG, plus an annotated overview with numbered boxes.\n\n"
                    "Works best when gutters are clearly lighter than the art. "
                    "Fails with a clear error if fewer than 2 panels are found."
                )
                pn_file = gr.File(
                    label="Comic page image",
                    file_count="single",
                    file_types=["image"],
                )
                with gr.Row():
                    pn_area = gr.Slider(
                        0.002, 0.15, value=0.008, step=0.001, label="Min panel area (fraction)"
                    )
                    pn_pad = gr.Slider(0, 40, value=8, step=1, label="Padding (px)")
                    pn_gutter = gr.Slider(2, 40, value=8, step=1, label="Min gutter width (px)")
                pn_btn = gr.Button("Cut panels", variant="primary")
                pn_download = gr.File(label="Download panels zip")
                pn_overview = gr.Image(label="Overview with boxes", type="filepath")
                pn_status = gr.Textbox(label="Status", lines=8)

                pn_btn.click(
                    fn=run_panels,
                    inputs=[pn_file, pn_area, pn_pad, pn_gutter],
                    outputs=[pn_download, pn_overview, pn_status],
                )


        gr.Markdown(
            "---\n"
            "Library & CLI: `pip install -e '.[web]'` then `pencil-cleaner` / "
            "`pencil-cleaner-app`. See the project README for Hugging Face Spaces deploy."
        )

    return demo


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Launch the Gradio app (entry point for ``pencil-cleaner-app``)."""
    # argv unused — kept for CLI symmetry
    demo = build_demo()
    demo.launch(server_name="0.0.0.0", share=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
