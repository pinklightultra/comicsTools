# pencil_cleaner

Part of [comicsTools](../README.md). Panel cropping is [comic-cropper](../comic-cropper/README.md).

Tools for hand-drawn comics that go **pencil → print → ink → scan**,
plus printer-test scoring, deskew/flatten, panel cutting, optional
corner-mark cleanup, and a **Gradio web app** (local or Hugging Face Spaces).

1. **`prep_pencils_for_printing`** — you scanned your *pencils only*.
   This lightens them to a faint, even grey so a mono laser printer
   can print them back out on real paper as a "ghost" to ink over —
   faint enough that a later scan of the *inked* page won't pick the
   pencil back up.

2. **`remove_pencils`** — you scanned the *inked* page and there's
   still pencil ghosting, eraser smudges, or scanner-lid shadow in
   the scan. This strips everything that isn't ink, leaving clean
   black linework on a pure white page.

3. **`mark_cleanup`** (optional) — for pages printed with four corner
   registration marks: straighten the scan via the marks, then strip
   the light-grey laser print while keeping your ink.

4. **`score_printer_test`** — print a grey-swatch test sheet, draw a
   pen line across it, scan, and score which opacities still ghost
   after cleanup. Recommends a safer prep opacity preset.

5. **`deskew_flatten`** — straighten a skewed scan and/or even out
   lighting. Does **not** strip pencil or ink.

6. **`cut_panels`** — find comic panel gutters and export each panel
   as a separate PNG (plus an annotated overview).

Prep and clean work on a single image or a whole multi-page PDF.

## Install

```bash
cd pencil_cleaner
pip install -e .
# web UI + PDF + SVG + corner-mark PDF support:
pip install -e ".[all]"
# Gradio only:
pip install -e ".[web]"
```

Core dependencies: `numpy`, `opencv-python-headless`, `Pillow`, `scipy`.

Optional extras (see `pyproject.toml`):

| extra | packages |
|---|---|
| `pdf` | PyMuPDF |
| `svg` | cairosvg |
| `marks` | pypdfium2 |
| `web` | gradio |
| `tui` | textual, rich |
| `gpu` | torch. Not included in `all`. |
| `gui` | PySide6-Essentials and PyMuPDF. The desktop window. Not included in `all`. |
| `all` | PyMuPDF, cairosvg, pypdfium2, gradio, textual, rich. Not torch, not PySide6, and not the `potrace` binary. |

Also useful as a **system** package: `potrace` (`apt install potrace` /
`brew install potrace`) so `method="vector"` can trace true vector edges.
Without it, `"vector"` silently falls back to morphological cleanup.

## Desktop window

From the repository root:

```bash
pip install "./pencil_cleaner[gui]"
comics-tools
```

`comics-tools` is the project window: import scans, deskew, panel boxes, a board you can reorder, catalog ids (`p003-02`), tags, and a US Letter ghost PDF. Lettering uses Tesseract when it is installed. The command-line flags are unchanged. See the root README for the screenshot and the release builds.

## Terminal UI

Textual UI for the same jobs as the CLI. Needs the `tui` extra (included in `all`):

```bash
python -m pencil_cleaner tui
# or, after install:
pencil-cleaner-tui
```

The clean tab selects `gpu`, `vector`, `morphological`, or `levels`. `gpu` uses CUDA when PyTorch can see a device, and otherwise the CPU mask. Opacity presets are the real ones: light 0.25, medium 0.40, dark 0.55. The hardware tab prints `get_gpu_info()`. The benchmark button runs only when CUDA is available. It times a random 3508×2550 field for 40 reconstruction steps, not a comic scan, and the log names the device that answered.

```bash
python -m pencil_cleaner clean inked.png -o clean.png --method gpu
python -m pencil_cleaner gpu-info
```

`gpu-info` imports Rich, so it needs the `tui` extra. With no CUDA device the device name is `CPU (No CUDA)`.

`--method gpu` builds the mask on CUDA when that device exists: a disk erosion, plus line kernels (horizontal, vertical, and both diagonals) of length `line_reach` (default 9). `toner_filter=True` computes two speckle tensors and does not apply them, so the flag does not change the mask. Without CUDA, `gpu_build_ink_mask` calls the CPU `build_ink_mask` and ignores `directional` and `toner_filter`. The code does not require a particular board, and this repository has no checked-in timing of a comic scan.

## Gradio web app (browser UX)

Six tabs:

1. **Prep pencils for printing** — upload image/PDF, opacity light/medium/dark/custom, download result, before/after preview
2. **Clean inked scans** — method levels | morphological | vector, advanced sliders, optional SVG for vector + single image, before/after preview
3. **Corner-mark cleanup** — for printed registration-mark pages
4. **Printer test scorer** — score grey-swatch test sheets → opacity recommendation
5. **Deskew & flatten** — straighten / even lighting only
6. **Panel cutter** — export each comic panel as PNG + overview

### Run locally

```bash
cd pencil_cleaner
pip install -e ".[web]"
# optional PDF/SVG/marks support:
pip install -e ".[all]"

pencil-cleaner-app
# or:
python -m pencil_cleaner app
# or:
python app.py
```

Then open **http://127.0.0.1:7860** (Gradio default). The app binds
`server_name="0.0.0.0"` so other machines on your LAN can reach it too.

### Hugging Face Spaces

1. Create a new Space with **Gradio** SDK (Python).
2. Point it at this project (upload the folder, or connect a Git repo whose
   root is this project — it must contain root `app.py` and `requirements.txt`).
3. Spaces will `pip install -r requirements.txt` and load `demo` from `app.py`.
4. No secrets are required; do not hardcode tokens in the Space.

Root files Spaces expects:

- `app.py` — builds `demo = build_demo()` (Spaces launches it)
- `requirements.txt` — runtime deps including `gradio`

## Cleanup methods

`remove_pencils` / `clean_pdf` take a `method=` argument. Each is
also a plain function you can call directly if you want to inspect a
mask or build your own pipeline.

| method | what it does | function(s) | needs |
|---|---|---|---|
| `"levels"` | Flat brightness/contrast remap: below a black point → solid ink, above a white point → paper, linear ramp between. Fast, no morphology. | `levels_clean` | numpy only |
| `"morphological"` (the **inker**) | Separates ink from ghost by stroke *thickness*, not just brightness. Real ink is bold enough to survive being eroded and rebuilt; thin pencil ghosts and grain don't survive and disappear outright. | `build_ink_mask`, `darken_ink`, `morphological_clean` | opencv-python |
| `"vector"` (the **vectorizer**, default) | Traces the morphological mask to true vector shapes with `potrace`, then rasterizes that back and uses it as the mask — a smooth mathematical edge instead of a pixel threshold, plus an editable `.svg` of just the linework. | `trace_mask_to_svg`, `rasterize_svg_mask`, `vectorize_mask`, `vector_clean` | `potrace` binary + `cairosvg` |
| `"gpu"` | Same thickness idea on CUDA when PyTorch can see a device. Without CUDA, the CPU morphological mask. `toner_filter` does not change the mask. The Gradio radio does not list this method. | `gpu_clean` | `torch` and a CUDA device for the GPU path |

Start with `"levels"` when the pencil is clearly lighter than ink.
Use `"morphological"` when a ghost is nearly as dark as ink. Leave
`"vector"` as the default when you want the cleanest edges and/or an SVG.

## Usage (library)

```python
import pencil_cleaner as pc

# 1. Pencils only -> faint printable page
pc.prep_pencils_for_printing("pencils_page.png", "print_me.png", opacity="light")
pc.prep_pdf_for_printing("pencils.pdf", "print_me.pdf", opacity="light")

# 2. Inked scan -> pencil ghosts removed (default method="vector")
pc.remove_pencils("inked_page.png", "clean_page.png")
pc.clean_pdf("inked_comic.pdf", "clean_comic.pdf")

pc.remove_pencils("inked_page.png", "clean.png", method="levels")
pc.remove_pencils("inked_page.png", "clean.png", method="morphological")
pc.remove_pencils("inked_page.png", "clean.png", method="vector", svg_output_path="ink.svg")

# 3. Printer-test scorer
result = pc.score_printer_test("swatch_scan.png", n_swatches=7)
print(result.summary, result.recommended_opacity)
result.save_annotated("annotated.png")
result.save_table("scores.csv")

# 4. Deskew / flatten only (no pencil strip)
pc.deskew_flatten("skewed.png", "straight.png", deskew=True, flatten=True)

# 5. Panel cutter
paths, overview = pc.cut_panels("page.png", "panels_out/", padding_px=8)
```

### Corner-mark cleanup (library)

```python
from pencil_cleaner.mark_cleanup import clean_marks_page, clean_marks_batch

clean_marks_page("scan.png", "out/scan-ink.png", dpi=600)
clean_marks_batch(["scan1.pdf", "scan2.png"], "out/", dpi=600)
```

### Command line

```bash
python -m pencil_cleaner prep pencils.pdf -o print_me.pdf --opacity light

python -m pencil_cleaner clean inked_page.png -o clean.png
python -m pencil_cleaner clean inked_page.png -o clean.png --method levels
python -m pencil_cleaner clean inked_page.png -o clean.png --method morphological
python -m pencil_cleaner clean inked_page.png -o clean.png --method vector --svg ink.svg
python -m pencil_cleaner clean inked_comic.pdf -o clean.pdf

python -m pencil_cleaner marks SCAN.pdf SCAN2.png -o inked/

python -m pencil_cleaner printer-test swatch_scan.png -o score_dir/
python -m pencil_cleaner deskew skewed.png -o straight.png
python -m pencil_cleaner panels page.png -o panels/

python -m pencil_cleaner app
```

## Tuning

`prep_pencils_for_printing(..., opacity=...)`
Presets `"light"` (0.25), `"medium"` (0.40), `"dark"` (0.55), or any
float 0–1. Start with `"light"`. Use **printer-test** to pick a safer
value for your printer/scanner combo.

`remove_pencils(...)` / `clean_pdf(...)`
- `method` — `"levels"` | `"morphological"` | `"vector"` (default).
- `black` / `white` — remap thresholds. Defaults: 175/208 for `"levels"`,
  40/170 for `"morphological"`/`"vector"`.
- `dark_thresh` (default 60) — morphological/vector only.
- `min_stroke_px` (default 5, ~300 dpi) — morphological/vector only.
- `gamma` (default 1.6) — morphological/vector only.

`score_printer_test(...)`
- `n_swatches` (default 7), `levels` (opacity labels per column),
  `residual_fail` (default 0.04).

`deskew_flatten(...)`
- `deskew` / `flatten` booleans, `max_angle` (default 15°).

`cut_panels(...)`
- `min_panel_area_frac`, `padding_px`, `min_gutter_px`. Raises
  `PanelCutError` if fewer than 2 panels are found.

## A note on the vector step

If you write your own code around `trace_mask_to_svg`: potrace's PBM
reader wants a genuine 1-bit bitmap. This module always converts to
real mode `"1"` before handing anything to potrace.

## Limitations

- **Printer test** assumes roughly equal vertical swatch columns and a
  roughly horizontal pen line; heavily skewed or non-grid sheets need
  a manual crop (`region=`) first.
- **Deskew** uses minAreaRect + Hough on dark content; sparse pages or
  strong perspective (not just rotation) may need a manual straighten.
- **Panel cutter** expects clear light gutters; borderless / bleeds /
  complex layouts may under-detect. Image input only (not PDF) in v0.4.
