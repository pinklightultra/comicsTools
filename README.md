# traceJockey

> *A specialized toolkit for the hybrid pencil-to-ink comic pipeline.*

**traceJockey** is an open-source, local-first cartooning suite built for artists working in the hybrid physical/digital comic loop:

$$\text{Pencil Roughs} \longrightarrow \text{Panel Isolation / 3× Blowup} \longrightarrow \text{Laser Ghost Print} \longrightarrow \text{Physical Ink} \longrightarrow \text{Scan & Extraction} \longrightarrow \text{Layout}$$

Standard digital art programs assume you either work 100% digitally or use non-photo blue pencils. If you print laser toner "ghosts" on Bristol paper to ink over with dip pens and brushes, toner is carbon-based (meaning you cannot simply drop a color channel). **traceJockey** handles panel isolation, ghost calibration, stroke-geometry ink separation, and vectorization out of the box.

---

## The Toolkit

| Component | Interface | Purpose |
| :--- | :--- | :--- |
| [**`comic-cropper/`**](comic-cropper/) | Local Web App (Browser) | Import scans or multi-page PDFs, frame small pencil panels, export at 1×–4× (default 3×) with soft bleed, display blue-line/high-contrast views, generate contact sheets, and save `.comiccrop` project files. |
| [**`pencil_cleaner/`**](pencil_cleaner/) | **Terminal UI (TUI)**, Python CLI & Gradio Web UI | **GPU Directional Inker (A100 Tensor-accelerated)**, mono laser ghost prepper, `potrace` vector tracing, gutter panel cutter, printer-test scorer, and terminal scan visualizer. |

---

## Suggested Workflow

1. **Panel Isolation & Enlargement** (`comic-cropper`)
   - Draw thumbnail or page layouts on paper or digitally.
   - Load scans or PDFs into **Comic Cropper**, draw panel bounds, and export 300% enlarged panels with bleed.
2. **Ghost Print Prep** (`pencil_cleaner`)
   - Run `prep_pencils_for_printing` to lighten pencil linework into a faint, calibrated grey ghost.
   - Print directly onto Bristol board or watercolor paper using a mono laser printer.
3. **Physical Inking**
   - Ink over the faint laser ghost using your choice of tools (dip pens, sable brushes, markers, ink wash).
4. **Linework Restoration & Vectorization** (`pencil_cleaner`)
   - Scan the inked page.
   - Use `remove_pencils` with `method="morphological"` or `method="vector"` to strip residual toner specks, eraser smudges, and paper grain while preserving sharp, organic ink edges.
   - Optionally export resolution-independent `.svg` vector linework.

---

## Quick Start

### 1. Comic Cropper (Web Console)

A zero-build, local-first browser tool running purely on HTML5/Canvas/Vanilla JS.

```bash
cd comic-cropper
python3 -m http.server 8765
```
Open **`http://127.0.0.1:8765`** in your browser.

*Features: Arrow-key nudging (1px / 10px), grid snapping, 90° rotation & fine straighten, dashed bleed previews, and `.comiccrop` project archive save/load.*

---

### 2. Pencil Cleaner (Python & Gradio Console)

```bash
cd pencil_cleaner

# Create & activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install all dependencies (Web UI, PDF, SVG, Corner-marks)
pip install -e ".[all]"

# (Optional Linux system dependency for vector tracing)
# sudo apt-get install -y potrace

# Launch the Gradio web interface
pencil-cleaner-app
# Or run CLI:
# python -m pencil_cleaner --help
```
Open **`http://127.0.0.1:7860`** in your browser.

---

## Justinbot Terminology Glossary

For cartoonists, illustrators, and print engineers exploring hybrid media pipelines:

| Term | Domain / Cartooning Context | Definition in `comicsTools` |
|---|---|---|
| **Hybrid Pipeline** | Physical / Digital workflow | Composition loop moving between pencil layouts, digital panel cropping, physical ink on Bristol board, and vector cleanup. |
| **Laser Ghost Print** | Physical underdrawing guide | Ultra-light, low-density halftone print of pencil layouts on paper, faint enough to guide physical inking without competing visually. |
| **Non-Photo Blue** | Traditional photolithography | Cyan pencil pigment invisible to vintage orthochromatic graphic arts film, superseded in modern laser printing by calibrated grey ghosts. |
| **Morphological Separation** | Mathematical morphology filtering | Computer vision operations (dilation, erosion, opening) that separate thin carbon laser toner specks from dense organic brush strokes. |
| **Potrace Tracing** | Bitmap to vector conversion | Transform bitmap raster scans into resolution-independent Bezier spline curves for publication-ready SVG linework. |
| **3x Panel Blowup** | Detail enlargement | Enlarging small pencil roughs by 300% to print across full Bristol boards, allowing cartoonists to ink tight physical details comfortably. |
| **Bleed Margin** | Print production boundary | Artwork extending past the final trim line to ensure ink reaches the page edge without accidental white paper gaps. |
| **Comic Cropper** | Zero-dependency canvas tool | In-browser crop and framing workstation allowing rapid bounding box export and multi-page contact sheet generation. |

## License & Attribution

Open-source and modular for cartoonists, illustrators, and indie comic publishers.
