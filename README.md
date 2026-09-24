# traceJockey

> *A specialized toolkit for the hybrid pencil-to-ink comic pipeline.*

**traceJockey** is an open-source, local-first cartooning suite built for artists working in the hybrid physical/digital comic loop:

$$\text{Pencil Roughs} \longrightarrow \text{Panel Isolation / 3× Blowup} \longrightarrow \text{Laser Ghost Print} \longrightarrow \text{Physical Ink} \longrightarrow \text{Scan & Extraction} \longrightarrow \text{Layout}$$

Standard digital art programs assume you either work 100% digitally or use non-photo blue pencils. If you print laser toner "ghosts" on Bristol paper to ink over with dip pens and brushes, toner is carbon-based—meaning you cannot simply drop a color channel. **traceJockey** handles panel isolation, ghost calibration, stroke-geometry ink separation, and vectorization out of the box.

---

## The Toolkit

| Component | Interface | Purpose |
| :--- | :--- | :--- |
| [**`comic-cropper/`**](comic-cropper/) | Local Web App (Browser) | Import scans or multi-page PDFs, frame small pencil panels, export at 1×–4× (default 3×) with soft bleed, display blue-line/high-contrast views, generate contact sheets, and save `.comiccrop` project files. |
| [**`pencil_cleaner/`**](pencil_cleaner/) | Python CLI & Gradio Web UI | Prep faint printable pencil ghosts, extract ink lines from scanned pages (via levels, morphological stroke filtering, or `potrace` vector tracing), deskew scans, auto-detect gutters / cut panels, and score printer-test swatches. |

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

## License & Attribution

Open-source and modular for cartoonists, illustrators, and indie comic publishers.
