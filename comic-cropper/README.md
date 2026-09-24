# traceJockey // cropper

Local-first web application for isolating penciled comic panels, exporting them enlarged for physical inking, and saving project state for layout assembly.

Designed for the scan → isolate/enlarge → print-ghost → ink-large → layout workflow. Runs fully offline in the browser (no backend) once CDN assets are cached.

## How to run

From this folder:

```bash
python3 -m http.server 8765
```

Then open **http://127.0.0.1:8765** in a desktop browser (Chrome / Firefox / Edge / Safari).

Any static file server works. No build step. Pinned CDNs:

- **JSZip** `jszip@3.10.1` — ZIP export and `.comiccrop` packaging
- **PDF.js** `pdfjs-dist@3.11.174` — multi-page PDF import (worker from the same package)

Images and project data never leave your machine.

## Workflow

1. **Import** penciled page scans or PDFs (drag-and-drop or **Drop a scan**). PNG / JPG / WEBP / GIF / TIFF (if the browser can decode them) and **PDF** (each PDF page becomes a project page). Multiple pages per project.
2. **Draw** panel rectangles on the page (click-drag). Select, move, resize, and **arrow-key nudge** crops. Name panels (auto `page1-panel3`, rename anytime).
3. **Export** selected or all panels at 1×–4× (default **3×**) or custom % as PNG or JPEG, with optional **bleed** padding. Optional ZIP of every panel, or a labeled **contact sheet** PNG.
4. **Save project** as a `.comiccrop` file so you can reopen pages + crops later for layout.

Autosave uses IndexedDB in this browser. Refreshing the page offers **Recovered local project** if work was in progress.

## Features

### Export scale (default 3×)

Fresh projects default to **3× (300%)**. Scale is stored in project prefs / IndexedDB autosave so it sticks.

### Soft bleed

**Bleed** (0–64 px in source image coordinates, default 12) expands each panel crop on export so enlarged inks aren’t clipped at the edge. Clamped to page bounds. When bleed &gt; 0, the canvas and close-up show a dashed outer rect.

### Arrow-key nudge

With a crop selected: **Arrow** keys nudge 1 px; **Shift+Arrow** nudges 10 px (image pixels). Arrow keys are ignored while typing in inputs.

### Blue-line / high-contrast pencil view

Toolbar **Pencil view**: Off | Blue-line | High-contrast.

- **Blue-line** — non-photo blue style filter on the canvas display only
- **High-contrast** — boosts levels for faint pencils on the display only

Exports and baked page images stay true to the original scan. Last mode is remembered in prefs.

### Contact sheet

**Contact sheet** lays every cropped panel in the project onto one PNG grid (consistent cell size from the largest panel at the current export scale + bleed), with names under each cell and dark/cream studio styling. Downloads as `<project>-contact-sheet.png`.

### Multi-page PDF import

Drop or pick a PDF alongside images. Each page is rasterized via PDF.js (~2× CSS/pt scale) and added as a named project page (`file-p1`, `file-p2`, …). Password-protected PDFs show a toast — unlock elsewhere first.

### Rotate / straighten

- **⟲ 90° / ⟳ 90°** — bake immediately; crop rectangles are remapped to the new axes
- **Straighten** slider (±15°) — live preview overlay; **Apply** bakes a rotated bitmap into the page. If crops exist, confirms and **clears crops on that page** (free rotation can’t keep axis-aligned rects cleanly). **Reset** cancels the preview

## Shortcuts

| Key | Action |
|-----|--------|
| Drag | Draw a crop |
| Space + drag / middle-mouse | Pan |
| Scroll wheel | Zoom toward cursor |
| `Z` / `Shift+Z` | Zoom in / out |
| `F` | Fit page to view |
| `1` | Actual size (100%) |
| `←↑↓→` | Nudge selected crop 1 px |
| `Shift` + arrows | Nudge 10 px |
| `Delete` / `Backspace` | Delete selected panel |
| `Ctrl/Cmd+S` | Save `.comiccrop` project |
| `Ctrl/Cmd+E` | Export all panels |
| `Ctrl/Cmd+O` | Open project |

Optional toggles: **Aspect lock**, **Snap** (8px grid), **Sound**, **Flourish**.

## Project files (`.comiccrop`)

A `.comiccrop` file is a ZIP containing:

- `project.json` — page metadata, crop rectangles in **image pixel coordinates**, panel names, export prefs (scale, format, bleed, viewMode, …), timestamps
- `pages/page_001.png` (etc.) — page image blobs

Old projects without newer prefs fields still open; missing fields use defaults (scale 3×, bleed 12, viewMode off).

## Export notes

Upscaling uses `createImageBitmap` / canvas with high-quality smoothing. Pencil view filters are **never** baked into exports unless you rotate/straighten the page image itself. Bleed expands the source rect before upscale.

## Fun polish

- Witty studio copy, toasts (“Panel nabbed”, “Ink-ready pack”, “Project pocketed”)
- Coral pulse when a crop finishes; optional soft click SFX (off by default — toolbar **Sound**)
- Caption-box panel badges; “N panels ready to ink” status chip
- Optional ink-splash flourish on batch export / contact sheet (toolbar **Flourish**; respects reduced motion)
- Dark charcoal + cream + coral/electric blue; subtle paper texture behind the page

## Limitations

- **PDF.js** requires the pinned CDN (and its worker). Offline after first cache; password-protected PDFs are not unlocked in-app.
- Fine **straighten** clears crops on Apply (90° rotates remapped crops instead).
- TIFF depends on browser decode support.
- Desktop-first UI; mobile is not a target.
- Autosave is per-browser / per-origin; use **Studio save** for a portable backup.

## Files

- `index.html` — shell + UI
- `styles.css` — dark theme
- `app.js` — all app logic (vanilla, no build)
- `README.md` — this file
