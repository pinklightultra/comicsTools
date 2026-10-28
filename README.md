# traceJockey

Local tools for a hybrid comic page: pencil roughs, a pale laser print to ink over, then a scan whose lines and panel edges a reader can follow. Two programs. The page images stay on the machine that opens them.

Version **0.4.9** of the cleaner (`pencil_cleaner`). The cropper is a static page with no package version. MIT, copyright 2026 Justin Severn.

This note is the system specification. It names each default, the unit it is in, and the reading failure it is there to prevent. Operator detail — shortcuts, every CLI flag, Gradio field — lives in [`comic-cropper/README.md`](comic-cropper/README.md) and [`pencil_cleaner/README.md`](pencil_cleaner/README.md). Where those files state a processing time or name a specific GPU, this file is the one that matches the code: no timing is recorded in the repository.

## Place

A comic page is a reading order. Two neighboring crafts sit outside this repository.

**Pink Light** is the sequential-art practice this plate work sits beside: the published sequence, the page a reader turns. [Pink Light Ultra](https://pinklightultra.com/) is that umbrella, including the comic in progress. This repo does not publish the comic.

**GraphicNovelSans** names the lettering problem on the same page: a text face that stays readable inside a small balloon, with open counters and a weight that does not fight the ink. This repo does not ship a typeface, and the cropper loads no webfont.

traceJockey is the plate under both. If the ghost prints too dark, a later scan reads pencil as plot. If a crop clips the stroke, the panel edge lies. The decisions below are the controls for those failures.

## Who it serves

Cartoonists who print pencil layouts back onto paper and ink them with dip pens, brushes, or markers. Laser toner is carbon. Dropping a color channel does not remove it, which is why non-photo blue is the wrong model for this loop.

The loop the code implements:

1. Isolate penciled panels and export them enlarged, with bleed.
2. Lighten a pencils-only scan to a calibrated gray and print that ghost.
3. Ink on the paper.
4. Scan the inked page and separate ink from leftover ghost, smudge, and paper grain.
5. Optionally trace the ink mask to SVG, cut panels on gutters, or straighten a scan.

Someone maintaining the docs can treat the parameter tables as the design system. Change a default in code and change it here in the same edit. The component READMEs are the task guides.

## The two programs

| Program | What the reader of the page gets | How it runs |
| --- | --- | --- |
| [`comic-cropper/`](comic-cropper/) | Panel rectangles in **source-image pixels**, exported at a chosen scale with bleed. A contact sheet of the set. Display filters that do not enter the file. | One HTML page, `app.js`, `styles.css`. No build. |
| [`pencil_cleaner/`](pencil_cleaner/) | A ghost light enough to ink over, or a page of ink on white. Optional SVG of the traced mask. Gutter cuts, deskew, registration-mark cleanup, a printer-test score. | Python package. Library, CLI, Gradio, and a Textual UI. |

## Decisions

Defaults below are the values in source. “Holds” is the reading condition the default assumes.

### Ghost, ink, and the scan

| Decision | Default | Holds |
| --- | --- | --- |
| `opacity` preset `light` | 0.25 of black | Starting ghost. Faint enough that many scans drop it. |
| `medium` / `dark` | 0.40 / 0.55 | Darker guides. More likely to survive into the ink scan. |
| Grain floor | 0.07 darkness, then ÷ 0.93 | Paper grain under that floor is discarded before the opacity scale. |
| Peak normalize | 99.7th percentile of remaining darkness | The darkest pencil maps to the chosen opacity. |
| `levels` black / white | 175 / 208 | One global cut. Pencil is clearly lighter than every ink stroke. |
| `dark_thresh` | 60 on a 0–255 darkness scale (`255 − gray`) | Pixels darker than this may be ink. Lower values admit fainter marks and heavier pencil. |
| `min_stroke_px` | 5 | Elliptical erosion kernel. Comment in `build_ink_mask` tunes 5 px for about 300 dpi. Hairlines below the kernel disappear. |
| Mask black / white / `gamma` | 40 / 170 / 1.6 | Applied only inside the ink mask. `gamma` above 1 pushes mid-grays darker. `levels` ignores `gamma`. |
| potrace turdsize / opttolerance | `0` / `0.2` | Turdsize 0 keeps small closed shapes (dots, punctuation). |
| Clean / prep PDF render | 300 dpi | PyMuPDF zoom is `dpi / 72`. Fallback is `pdftoppm -r`. |
| Printer-test swatches | 7 columns, labels 0.10 through 0.40 step 0.05 | Equal vertical columns and a roughly horizontal pen line. |
| `residual_fail` | 0.04 darkness | A swatch fails when leftover darkness after a levels pass exceeds this. |
| Corner-mark page | US Letter, marks inset 0.45 in, mark size 0.20 in, art inset 0.75 in, output 600 dpi | The printed sheet matches `mark_cleanup.py`. Top-left mark is a square so orientation can be recovered. |

Prep PDF output is centered on US Letter (612×792 pt) at the requested dpi.

### Panel frame

| Decision | Default | Holds |
| --- | --- | --- |
| Export scale | 3× (custom percent 300) | Small pencil roughs print large enough to ink. Scale is 1×–4× or a custom percent. |
| Bleed | 12 source pixels, clamped to 0–64 and to the page | Enlarged ink is not cut off at the crop. Bleed is applied in source pixels, then the rect is scaled. |
| Snap grid | 8 px, off until toggled | Crop edges land on a coarse grid. |
| Nudge | 1 px; Shift+arrow 10 px | A selected crop moves in source pixels. Ignored while an input is focused. |
| Pencil view | `off` | `blueline` and `contrast` are CSS filters on the canvas. Exports omit them. |
| JPEG quality | 0.92 | Used only when the export format is JPEG. |
| PDF import | PDF.js viewport scale 2 | 144 px per inch on a 72 pt page. Password-protected PDFs are refused. |
| Straighten | slider ±15°, step 0.1° | Preview until Apply. Apply bakes the rotation and clears crops on that page, because a free rotation will not keep axis-aligned rectangles honest. |
| 90° rotate | baked immediately | Crop rectangles are remapped onto the new axes. |
| Contact sheet | columns = ceil(√n), pad 18 px, gap 14 px, label band 28 px | One PNG. Cell size is the largest panel at the current scale and bleed. |
| `cut_panels` min area | 0.008 of the page | Smaller blobs are not panels. |
| Padding / min gutter | 8 px / 8 px | Image input only. Fewer than two panels raises `PanelCutError`. |
| Panel-count score | highest for 12–18 panels, peak near 15 | The scorer prefers a dense indie grid. A splash page can lose to that preference. |

Crop coordinates in `.comiccrop` are image pixels, not screen pixels. Old project files missing newer fields open with scale 3×, bleed 12, view mode off.

## Method

### Preparing the ghost

`prep_pencils_for_printing` loads the scan as float32 grayscale, 0–255, 255 = paper.

`_lighten_pencils` does three things:

1. **Paper tone.** The page is reduced by 8. A 90th-percentile filter (window 71) plus a blur estimates the sheet, and that estimate is held to at least 60 so the later division stays stable. For the first 120 pixels in from the edge, the tone is the maximum of this broad estimate and a tighter 85th-percentile estimate. The tighter blend fades out by 240 pixels, so edge curl and scanner shadow do not become fake pencil.
2. **Pencil scale.** Gray is divided by that tone. Darkness below 0.07 is treated as grain and removed. What remains is divided by its 99.7th percentile and multiplied by the opacity.
3. **Scanner lid.** At quarter resolution, components darker than 85 that touch the border and cover more than 1500 reduced pixels are marked as lid, dilated by 41 pixels at full resolution, and forced to white.

Named presets live in `PRESETS`: `light` 0.25, `medium` 0.40, `dark` 0.55. Any float from 0 to 1 is accepted. `score_printer_test` is how you learn which of those your printer and scanner actually drop.

### Separating ink from ghost

`remove_pencils` / `clean_pdf` take `method`. `METHODS` is `levels`, `morphological`, `vector`, and `gpu`. The default is `vector`.

**`levels`.** `levels_clean` maps gray through `(gray − black) / (white − black)`, clipped to 0–1, then times 255. Defaults 175 and 208. There is no notion of stroke width. A ghost as dark as a thin ink stroke cannot be removed without also removing that stroke.

**`morphological`.** `build_ink_mask` thresholds darkness at `dark_thresh`, erodes with an ellipse of `min_stroke_px`, then dilates back with a 3×3 kernel, never outside the original dark region, for at most 40 iterations. Components too thin to survive the erosion are gone. `darken_ink` remaps gray inside the mask and sets every other pixel to 255. That is the whole “inker.”

**`vector`.** The same mask is written as a real 1-bit PBM (`PIL` mode `"1"`) and passed to the `potrace` binary: SVG output, turdsize 0, opttolerance 0.2. An 8-bit image with a `.pbm` name is not a 1-bit bitmap; potrace will parse it and fragment the trace, which is why the conversion is explicit. `cairosvg` rasterizes the SVG back to the page size; pixels above 127 become the mask, and `darken_ink` runs on that mask.

If `potrace` is not on `PATH`, or `cairosvg` cannot be imported, `vector_clean` returns the morphological mask and `used_vector=False`. `remove_pencils` discards that flag. The default path can therefore write a morphological result and print only `Wrote …`. Keep an SVG path (`--svg` on a single image) when you need evidence that the trace ran. PDF cleaning traces per page and discards the SVG; use a single image when you want the file.

**`gpu`.** Optional, and absent from the Gradio method radio (that radio is `levels`, `morphological`, `vector`). With PyTorch installed and `torch.cuda.is_available()` true, `gpu_build_ink_mask` builds the candidate on that device. Isotropic erosion is a max-pool of the inverted candidate. Four line kernels — horizontal, vertical, and both diagonals — have length `line_reach` (default 9, forced odd). A direction joins the seed when every pixel on that line is a candidate. The seed is the union of the disk and those lines, then the same constrained dilation, up to 50 iterations. `gpu_darken_ink` applies the same gamma remap.

`toner_filter=True` computes two speckle tensors and does not apply them. The mask is unchanged by that flag.

When CUDA is unavailable, `gpu_build_ink_mask` calls CPU `build_ink_mask` and ignores `directional` and `toner_filter`.

The `gpu` extra installs `torch`. The `all` extra does not. `python -m pencil_cleaner gpu-info` imports Rich, which arrives with the `tui` extra (included in `all`).

`gpu.py` and the Textual UI describe Tensor Cores, an A100, and sub-10 ms pages. The functions do not require a particular board and do not store a timing. The TUI benchmark allocates a random 3508×2550 binary field, runs 40 reconstruction steps, and its log line says “A100” for whatever CUDA device answered. This repository has no checked-in measurement of a comic scan.

### Framing panels

The cropper keeps rectangles on the bitmap you imported. Export calls `createImageBitmap` with a high-quality resize when the browser allows it, and falls back to `drawImage`. Smoothing is requested. The view-mode filter is not passed into that path.

Blue-line display is the CSS filter `contrast(1.2) brightness(1.06) grayscale(1) sepia(1) hue-rotate(175deg) saturate(6)`. High-contrast is `contrast(1.55) brightness(1.1) saturate(0.25)`. Both exist so faint pencil can be seen while drawing the box. The file you hand to the printer is the scan.

A `.comiccrop` file is a zip:

- `project.json` — version 1, page names, crop rectangles, export prefs (`scale`, `customPercent`, `format`, `jpegQuality`, `bleed`, `viewMode`), timestamps
- `pages/page_001.png` (or the source extension) — the page blobs

Autosave writes IndexedDB database `comic-cropper`, store `autosave`, key `current`, 1500 ms after a change. It is per browser origin. Studio save is the portable copy.

The contact sheet is a proof of the crop set: charcoal ground `#1a1a1e`, type `#f3efe6`, each panel centered in a cell, its name underneath. The sheet paints that hex itself. The CSS token `--cream` is `#f3eee4`. The sheet is a bitmap of the crop set, separate from a print-imposed page.

### Gutters, marks, deskew

`cut_panels` looks for rows and columns with dense ink, then checks a thin strip just past that edge. The strip counts as gutter paper when at least 55% of it is at or above gray 200. Edges with right-angle junctions score higher, so a speech-balloon curve is a weaker cut than a panel corner. A second strategy handles wavy gutters on pencil pages. Candidate layouts are scored with a peak at 12–18 panels (`panels.py`, count score). Clear light gutters are the assumption. Borderless pages and full-bleed art under-detect.

`mark_cleanup` finds the four printed marks, straightens from them, and strips the light-gray laser print while keeping black ink. PDF input on this path needs `pypdfium2`. Images use Pillow alone.

`deskew_flatten` estimates skew from a minimum-area rectangle on dark content, then from Hough lines, and refuses angles past `max_angle` (default 15°). Flattening divides out a large-kernel background. It does not decide what is ink.

`score_printer_test` isolates the pen line with a hard levels pass (black 90, white 200) and measures residual with the mild levels defaults (175 / 208). If every swatch passes, it recommends the darkest level that passed, snapped to a named preset when the value is within 0.03. If any fail, it recommends going lighter than the lightest failure. The annotated PNG and CSV are the record.

## Interface tokens

The cropper’s chrome is a small set of custom properties in `comic-cropper/styles.css`. They style the tool, not the comic.

| Token | Value | Role |
| --- | --- | --- |
| `--bg` / `--text` | `#161618` / `#f3eee4` | Application ground and primary label color |
| `--text-dim` | `#a89f92` | Status, zoom readout |
| `--cream` | `#f3eee4` | Wordmark |
| `--accent` | `#4db0ff` | Brand tag, inline code, primary button via `--accent-strong` |
| `--coral` | `#ff6b4a` | Mark and alert |
| `--crop` / `--crop-fill` | `#4db0ff` / 12% of that blue | Declared on `:root`. No rule in `styles.css` references them. |
| `--font` | Segoe UI, `system-ui`, sans-serif | UI text at 14 px |
| `--mono` | `ui-monospace`, SF Mono, Menlo, Consolas | Numeric and file-shaped labels |

No font file is downloaded. The contact sheet does not read these variables: its canvas calls set `Segoe UI, system-ui, sans-serif` for the title and `ui-monospace, SF Mono, Menlo, Consolas, monospace` for panel names. Two sources of type, same stacks, written in two places.

The live crop rectangle is also outside the token set. `drawCropRect` strokes `#5eb0ff`, and `#9ed0ff` when the crop is selected. The bleed guide is `rgba(255, 180, 84, 0.85)`. A change to `--crop` will not move the line on the page.

Flourish (the ink-splash on batch export) returns immediately when `prefers-reduced-motion: reduce` matches. Sound is off until the toolbar toggle.

The UI is desktop-first. Touch layout is not a target.

## Run

Python 3.8 or newer for the cleaner. Continuous integration runs 3.11 and 3.12.

### Cropper

From `comic-cropper/`:

```bash
python3 -m http.server 8765
```

Open `http://127.0.0.1:8765`. Any static file server works.

The first view loads two pinned scripts from jsDelivr:

- `jszip@3.10.1` — zip export and `.comiccrop`
- `pdfjs-dist@3.11.174` — PDF import, worker from the same version

After that cache, scans and project files are handled in the page. TIFF appears only if the browser can decode it.

### Cleaner

From `pencil_cleaner/`:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[all]"
```

`all` installs PyMuPDF, cairosvg, pypdfium2, Gradio, Textual, and Rich. It does not install PyTorch, and it does not install the `potrace` binary (`apt install potrace` or `brew install potrace`). Without that binary, `method="vector"` follows the morphological fallback above.

Core only, enough for levels and morphological cleanup on raster images:

```bash
pip install -e .
```

That pulls numpy, `opencv-python-headless`, Pillow, and scipy.

```bash
python -m pencil_cleaner prep pencils.png -o ghost.png --opacity light
python -m pencil_cleaner clean inked.png -o clean.png --method vector --svg ink.svg
python -m pencil_cleaner clean inked.png -o clean.png --method morphological
python -m pencil_cleaner printer-test swatch.png -o printer_test_out/
python -m pencil_cleaner panels page.png -o panels/
pencil-cleaner-app
```

Gradio binds `0.0.0.0` on port 7860. On the same machine open `http://127.0.0.1:7860`. The process is reachable from the LAN at that bind address.

Library entry points are the functions exported from `pencil_cleaner/__init__.py`: `prep_pencils_for_printing`, `remove_pencils`, `score_printer_test`, `deskew_flatten`, `cut_panels`, and the mask helpers if you want to inspect a stage.

A Hugging Face Space that uses `pencil_cleaner/app.py` and `pencil_cleaner/requirements.txt` needs that directory as the Space root. The root of this repository is not a Space root. `requirements.txt` lists Gradio, PyMuPDF, cairosvg, and pypdfium2. It does not list torch, textual, or rich.

## Proof

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) checks out the repo, installs numpy, Pillow, `opencv-python-headless`, and scipy, and runs `python -m unittest discover tests`. The matrix is Ubuntu, Windows, and macOS, for Python 3.11 and 3.12.

`tests/test_pencil_cleaner.py` contains three tests:

1. `load_gray` on a 64×64 mid-gray PNG returns float32 of that shape, with a sample near 128.
2. `flatten_lighting` on a horizontal gradient returns the same shape and dtype.
3. `estimate_skew_angle` on a blank white field returns 0 within half a degree.

That is the automated proof. The suite does not exercise morphology, potrace, the CUDA path, the cropper, printer scoring, or panel cutting. There is no benchmark file. A docstring that quotes milliseconds is not a result.

## Limits

- Vector output depends on a system `potrace` and on `cairosvg`. The fallback is silent at the CLI.
- `min_stroke_px` is in scan pixels. A 600 dpi page with the default of 5 drops strokes the 300 dpi tuning meant to keep.
- The panel cutter wants light gutters and is biased toward 12–18 panels. It accepts an image, not a PDF.
- Printer scoring wants a regular column grid and a pen line that is roughly horizontal. The library call accepts `region=(x0, y0, x1, y1)` when the sheet needs a manual crop first. The CLI does not expose that argument.
- Deskew handles rotation up to 15°. Strong perspective is left for a manual straighten.
- Corner-mark cleanup assumes the inch geometry in `mark_cleanup.py`.
- Cropper PDF import needs the pinned CDN on first load. Fine straighten clears crops on that page.
- Autosave does not travel with the browser profile you did not use. Save a `.comiccrop`.
- The GPU toner filter is a stub. Directional seeds run only when CUDA is actually available.

## License

MIT. See [LICENSE](LICENSE).
