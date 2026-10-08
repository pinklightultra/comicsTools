"""
pencil_cleaner
==============

Tools for comic pages that are drawn in pencil and inked by hand:

1. ``prep_pencils_for_printing`` / ``prep_pdf_for_printing``
   Take a scan of PENCILS ONLY and print it back out very pale, so a
   laser printer lays down a "ghost" of the drawing you can ink over
   on real paper -- and so a later scan of the inked page won't pick
   the pencil back up.

2. ``remove_pencils`` / ``clean_pdf``
   Take a scan of an INKED page (pencil ghosts, eraser smudges,
   scanner-lid shadows and all) and strip everything that isn't ink,
   leaving clean black linework on a pure white page. Three
   interchangeable techniques are available via ``method=``:

     "levels"          flat brightness/contrast remap -- fast, no
                        opencv/potrace needed. `levels_clean`
     "morphological"    separates ink from ghost by stroke thickness,
                        not just brightness (the "inker"). Handles
                        ghosts nearly as dark as the ink itself.
                        `build_ink_mask` + `darken_ink` /
                        `morphological_clean`
     "vector" (default) traces the morphological mask to true vector
                        shapes with `potrace` (the "vectorizer") and
                        uses that as the mask -- cleanest edges, plus
                        an editable .svg of the linework as a side
                        effect. Falls back to "morphological"
                        automatically if potrace/cairosvg aren't
                        installed. `trace_mask_to_svg` +
                        `rasterize_svg_mask` / `vectorize_mask` /
                        `vector_clean`

   Each technique above is also a plain, independently callable
   function if you want to inspect a mask or build your own pipeline.

3. Corner-mark cleanup (``mark_cleanup``): clean scans of pages that
   were printed with four corner registration marks — straighten via
   the marks, then strip the light-grey laser print while keeping ink.

4. ``score_printer_test`` — score a grey-swatch printer test sheet
   (which opacities still ghost after cleanup) and recommend a preset.

5. ``deskew_flatten`` — straighten + even lighting only (no pencil strip).

6. ``cut_panels`` — find comic panel gutters and export each panel PNG.

Prep and clean work on a single image or on a whole multi-page PDF.

Minimal dependencies: numpy, opencv-python, Pillow.
Optional, for nicer results: PyMuPDF (`pip install pymupdf`) to read
PDFs without poppler installed, the `potrace` command-line tool, and
`cairosvg` to rasterize the traced SVG back to a bitmap. Corner-mark
PDF input additionally needs ``pypdfium2``.

Web app: ``python -m pencil_cleaner app`` or ``pencil-cleaner-app`` (Gradio).
"""

from .printprep import prep_pencils_for_printing, prep_pdf_for_printing
from .cleanup import (
    remove_pencils,
    clean_pdf,
    levels_clean,
    build_ink_mask,
    darken_ink,
    morphological_clean,
    trace_mask_to_svg,
    rasterize_svg_mask,
    vectorize_mask,
    vector_clean,
    METHODS,
)
from .printer_test import PrinterTestResult, SwatchScore, score_printer_test
from .deskew import deskew_flatten, deskew_array, flatten_lighting, estimate_skew_angle
from .panels import PanelCutError, cut_panels, find_panel_boxes

from .gpu import (
    is_gpu_available,
    get_gpu_info,
    gpu_clean,
    gpu_vector_clean,
    compute_scan_metrics,
)

__all__ = [
    "prep_pencils_for_printing",
    "prep_pdf_for_printing",
    "remove_pencils",
    "clean_pdf",
    "levels_clean",
    "build_ink_mask",
    "darken_ink",
    "morphological_clean",
    "trace_mask_to_svg",
    "rasterize_svg_mask",
    "vectorize_mask",
    "vector_clean",
    "METHODS",
    "score_printer_test",
    "PrinterTestResult",
    "SwatchScore",
    "deskew_flatten",
    "deskew_array",
    "flatten_lighting",
    "estimate_skew_angle",
    "cut_panels",
    "find_panel_boxes",
    "PanelCutError",
    "is_gpu_available",
    "get_gpu_info",
    "gpu_clean",
    "gpu_vector_clean",
    "compute_scan_metrics",
]

__version__ = "0.4.9"
