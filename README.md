# Justin’s Comic Tools (complete pack)

Local toolkit for the pencil → crop/enlarge → print-ghost → ink → clean → layout loop.

## What’s inside

| Folder | What it is |
|--------|------------|
| `comic-cropper/` | Browser app: import scans/PDF, crop panels, 3× export + bleed, blue-line view, contact sheet, straighten, save `.comiccrop` projects |
| `pencil_cleaner/` | Python + Gradio app: prep pencils for print-ink, remove pencil ghost from inked scans, deskew, panel cutter, printer-test scorer, corner-mark cleanup |
| `comic_flow/` | Issue builders + continuity/layout docs for **Devil in Orlando** (scripts + JSON/MD; not the giant preview image dumps) |

## Suggested workflow

1. **Crop / enlarge** — open Comic Cropper, frame small pencils, export 3× with bleed for inking or layout.
2. **Prep for print-ink** (optional) — `pencil_cleaner` → Prep pencils for printing → faint laser ghost on paper → ink over.
3. **Clean inked scans** — `pencil_cleaner` → Clean inked scans / mark cleanup.
4. **Layout / issue assembly** — Comic Cropper contact sheet, or `comic_flow` builders with panel assignment JSON.

## Quick start

### Comic Cropper
```bash
cd comic-cropper
python3 -m http.server 8765
# open http://127.0.0.1:8765
```
Needs network once for PDF.js / JSZip CDN.

### pencil_cleaner
```bash
cd pencil_cleaner
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
pencil-cleaner-app
```

### comic_flow
See markdown files in `comic_flow/` (`DEVIL_COMIC_CONTINUITY.md`, redo maps, page flow). Run builders with Python when panel assets are available:
`build_boards.py`, `build_issue1.py`, `build_pdf.py`.

## Notes
- Packed without `pencil_cleaner/.venv` (recreate with pip).
- Excluded bulky `comic_flow/naive_read` previews and multi‑MB issue PDFs to keep this tools zip lean. Those live on the box under `/workspace/comic_flow/` if you need them.
