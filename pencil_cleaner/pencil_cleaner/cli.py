"""Command-line interface.

    python -m pencil_cleaner prep pencils.pdf -o print_me.pdf --opacity light

    python -m pencil_cleaner clean inked_page.png -o clean.png
    python -m pencil_cleaner clean inked_page.png -o clean.png --method levels
    python -m pencil_cleaner clean inked_page.png -o clean.png --method morphological
    python -m pencil_cleaner clean inked_page.png -o clean.png --method vector --svg ink.svg
    python -m pencil_cleaner clean inked_comic.pdf -o clean.pdf

    python -m pencil_cleaner marks SCAN.pdf -o inked/
    python -m pencil_cleaner printer-test scan.png -o score_dir/
    python -m pencil_cleaner deskew skewed.png -o straight.png
    python -m pencil_cleaner panels page.png -o panels/
    python -m pencil_cleaner app
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cleanup import METHODS, clean_pdf, remove_pencils
from .printprep import prep_pdf_for_printing, prep_pencils_for_printing


def _is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pencil_cleaner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prep", help="lighten a pencils-only scan for printing/inking")
    p_prep.add_argument("input", type=Path)
    p_prep.add_argument("-o", "--output", type=Path, required=True)
    p_prep.add_argument("--opacity", default="light", help="light | medium | dark | 0..1")
    p_prep.add_argument("--dpi", type=int, default=300)

    p_clean = sub.add_parser("clean", help="strip pencil ghosts from an inked scan")
    p_clean.add_argument("input", type=Path)
    p_clean.add_argument("-o", "--output", type=Path, required=True)
    p_clean.add_argument(
        "--method",
        choices=METHODS,
        default="vector",
        help="levels (fast, brightness-only) | morphological (the 'inker', "
        "stroke-thickness based) | vector (default: the 'vectorizer', traces "
        "the morphological mask with potrace for the cleanest edges)",
    )
    p_clean.add_argument("--dpi", type=int, default=300, help="only used for PDF input")
    p_clean.add_argument(
        "--svg", type=Path, default=None, help="save traced ink as .svg (method=vector, single image input only)"
    )
    p_clean.add_argument("--dark-thresh", type=int, default=60, help="levels/morphological/vector mask threshold")
    p_clean.add_argument("--min-stroke-px", type=int, default=5, help="morphological/vector min stroke width")
    p_clean.add_argument("--black", type=int, default=None, help="override the black point (method-specific default otherwise)")
    p_clean.add_argument("--white", type=int, default=None, help="override the white point (method-specific default otherwise)")
    p_clean.add_argument("--gamma", type=float, default=1.6, help="morphological/vector ink-darkening curve")

    p_marks = sub.add_parser(
        "marks",
        help="clean scans of pages printed with corner registration marks",
    )
    p_marks.add_argument("scans", nargs="+", type=Path, help="image or PDF scan(s)")
    p_marks.add_argument("-o", "--output", type=Path, default=Path("inked"), help="output folder")
    p_marks.add_argument("--dpi", type=int, default=600, help="output resolution (default 600)")
    p_marks.add_argument("--no-trim", action="store_true", help="keep the full art area instead of trimming to the ink")
    p_marks.add_argument("--debug", action="store_true", help="also save straightened debug images with found marks")

    p_ptest = sub.add_parser(
        "printer-test",
        help="score a grey-swatch printer test sheet and recommend opacity",
    )
    p_ptest.add_argument("input", type=Path, help="scanned test image")
    p_ptest.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("printer_test_out"),
        help="output folder for annotated preview + CSV table",
    )
    p_ptest.add_argument("--cleaned", type=Path, default=None, help="optional already-cleaned scan")
    p_ptest.add_argument("--n-swatches", type=int, default=7, help="number of vertical swatch columns")
    p_ptest.add_argument(
        "--residual-fail",
        type=float,
        default=0.04,
        help="residual darkness (0..1) above which a swatch fails",
    )
    p_ptest.add_argument(
        "--no-cleanup",
        action="store_true",
        help="do not run internal levels cleanup (use raw scan only)",
    )

    p_deskew = sub.add_parser(
        "deskew",
        help="deskew and/or flatten lighting (no pencil stripping)",
    )
    p_deskew.add_argument("input", type=Path)
    p_deskew.add_argument("-o", "--output", type=Path, required=True)
    p_deskew.add_argument("--no-deskew", action="store_true", help="skip rotation")
    p_deskew.add_argument("--no-flatten", action="store_true", help="skip lighting flatten")
    p_deskew.add_argument("--max-angle", type=float, default=15.0, help="ignore angles larger than this")
    p_deskew.add_argument("--dpi", type=int, default=300, help="PDF render DPI")

    p_panels = sub.add_parser(
        "panels",
        help="cut comic panels into separate PNGs",
    )
    p_panels.add_argument("input", type=Path, help="page image")
    p_panels.add_argument("-o", "--output", type=Path, default=Path("panels"), help="output folder")
    p_panels.add_argument(
        "--min-area-frac",
        type=float,
        default=0.008,
        help="minimum panel area as fraction of page",
    )
    p_panels.add_argument("--padding", type=int, default=8, help="padding pixels around each panel")
    p_panels.add_argument("--min-gutter", type=int, default=8, help="minimum gutter width in pixels")

    p_app = sub.add_parser("app", help="launch the Gradio web app")
    # no required args — app launches immediately

    p_tui = sub.add_parser("tui", help="launch the interactive terminal user interface (TUI)")
    p_gpu = sub.add_parser("gpu-info", help="display NVIDIA GPU hardware and acceleration status")

    args = parser.parse_args(argv)

    if args.command == "tui":
        from .tui import main as tui_main

        return tui_main()

    if args.command == "gpu-info":
        from .gpu import get_gpu_info
        from rich.console import Console
        from rich.table import Table

        c = Console()
        info = get_gpu_info()
        tbl = Table(title="traceJockey Accelerator Telemetry", border_style="green")
        tbl.add_column("Property", style="cyan")
        tbl.add_column("Value", style="bold white")
        for k, v in info.items():
            tbl.add_row(k.replace("_", " ").title(), str(v))
        c.print(tbl)
        return 0

    if args.command == "app":
        from .app import main as app_main

        return app_main()

    if args.command == "prep":
        opacity = args.opacity
        try:
            opacity = float(opacity)
        except ValueError:
            pass
        if _is_pdf(args.input):
            prep_pdf_for_printing(args.input, args.output, opacity=opacity, dpi=args.dpi)
        else:
            prep_pencils_for_printing(args.input, args.output, opacity=opacity)
        print(f"Wrote {args.output}")
        return 0

    if args.command == "clean":
        if args.svg is not None and (args.method != "vector" or _is_pdf(args.input)):
            print("--svg only applies to --method vector on a single image; ignoring.", file=sys.stderr)

        if _is_pdf(args.input):
            clean_pdf(
                args.input,
                args.output,
                method=args.method,
                dpi=args.dpi,
                dark_thresh=args.dark_thresh,
                min_stroke_px=args.min_stroke_px,
                black=args.black,
                white=args.white,
                gamma=args.gamma,
            )
        else:
            svg_out = args.svg if args.method == "vector" else None
            remove_pencils(
                args.input,
                args.output,
                method=args.method,
                dark_thresh=args.dark_thresh,
                min_stroke_px=args.min_stroke_px,
                black=args.black,
                white=args.white,
                gamma=args.gamma,
                svg_output_path=svg_out,
            )
        print(f"Wrote {args.output}")
        return 0

    if args.command == "marks":
        from .mark_cleanup import iter_clean_marks

        ok = bad = 0
        for name, dest, err in iter_clean_marks(
            args.scans,
            args.output,
            dpi=args.dpi,
            trim=not args.no_trim,
            debug=args.debug,
        ):
            if err:
                print(f"  {name}: skipped - {err}. Make sure all four corners of the sheet are in the scan.")
                bad += 1
            else:
                print(f"  {name}: saved {dest}")
                ok += 1
        print(
            f"Done: {ok} page(s) cleaned"
            + (f", {bad} skipped" if bad else "")
            + f". Output in {args.output}/"
        )
        return 0 if ok or not bad else 1

    if args.command == "printer-test":
        from .printer_test import score_printer_test

        args.output.mkdir(parents=True, exist_ok=True)
        result = score_printer_test(
            args.input,
            cleaned_path=args.cleaned,
            n_swatches=args.n_swatches,
            residual_fail=args.residual_fail,
            run_cleanup=not args.no_cleanup,
        )
        ann = args.output / "printer_test_annotated.png"
        tbl = args.output / "printer_test_scores.csv"
        result.save_annotated(ann)
        result.save_table(tbl)
        print(result.summary)
        print(f"Recommended opacity: {result.recommended_opacity} ({result.recommended_value})")
        for s in result.scores:
            flag = "PASS" if s.passed else "FAIL"
            print(
                f"  [{s.index}] {s.label_pct:5.1f}%  residual={s.residual_pct:6.2f}%  {flag}"
            )
        print(f"Wrote {ann}")
        print(f"Wrote {tbl}")
        return 0

    if args.command == "deskew":
        from .deskew import deskew_flatten

        dest = deskew_flatten(
            args.input,
            args.output,
            deskew=not args.no_deskew,
            flatten=not args.no_flatten,
            max_angle=args.max_angle,
            dpi=args.dpi,
        )
        print(f"Wrote {dest}")
        return 0

    if args.command == "panels":
        from .panels import PanelCutError, cut_panels

        try:
            paths, overview = cut_panels(
                args.input,
                args.output,
                min_panel_area_frac=args.min_area_frac,
                padding_px=args.padding,
                min_gutter_px=args.min_gutter,
            )
        except PanelCutError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Cut {len(paths)} panel(s):")
        for p in paths:
            print(f"  {p}")
        print(f"Overview: {overview}")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
