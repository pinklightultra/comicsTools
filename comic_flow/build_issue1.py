#!/usr/bin/env python3
"""Issue 1 one-shot builder — rematch map (issue1_redo_map.json) into PDF (option A)."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont

PANELS_DIR = Path("/workspace/pencil_cleaner_real_test/pencils_pdf/panels")
SAMPLES_DIR = Path("/workspace/comic_flow_pdf")
OUT_DIR = Path("/workspace/comic_flow")
REDO_MAP = OUT_DIR / "issue1_redo_map.json"
PAGES_DIR = OUT_DIR / "issue1_pages_build"
PAGES_DIR.mkdir(parents=True, exist_ok=True)

# 150dpi letter
PAGE_W, PAGE_H = 1275, 1650
MARGIN = 45
GUTTER = 10
BG = (255, 255, 255)
GUTTER_COLOR = (0, 0, 0)
PLACEHOLDER_BG = (235, 235, 235)
PLACEHOLDER_BORDER = (120, 120, 120)
PLACEHOLDER_TEXT = (60, 60, 60)

# Issue page count after option-A flashback insert
TOTAL_PAGES = 31
FIRST_REMATCH = 5

# NEW pencil slot labels + where they insert relative to proposed files.
# position: "before" = prepend; "after" = append (default).
NEW_SLOTS = {
    5: [{"label": "NEW: quiet love-her", "position": "after"}],
    13: [{"label": "NEW: timer motif (10)", "position": "after"}],
    16: [
        {"label": "NEW: pavement / spot", "position": "before"},
        {"label": "NEW: Gabi already-with-him at lot", "position": "after"},
    ],
    17: [{"label": "NEW: FLASHBACK — How I got here", "position": "after"}],
    24: [{"label": "NEW: wheel POV sobbing", "position": "before"}],
    31: [{"label": "NEW: End Issue 1 button", "position": "after"}],
}

# Sentinel prefix for placeholder slots in page_panels
NEW_PREFIX = "NEW:"


def font(sz=14):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", sz)
    except Exception:
        return ImageFont.load_default()


def layout_rects(n, area):
    x0, y0, x1, y1 = area
    W, H = x1 - x0, y1 - y0
    g = GUTTER

    def row(y, h, cols):
        cw = (W - g * (cols - 1)) // cols
        o = []
        for c in range(cols):
            cx = x0 + c * (cw + g)
            o.append((cx, y, x1 if c == cols - 1 else cx + cw, y + h))
        return o

    if n == 1:
        return [(x0, y0, x1, y1)]
    if n == 2:
        h = (H - g) // 2
        return [(x0, y0, x1, y0 + h), (x0, y0 + h + g, x1, y1)]
    if n == 3:
        ht = (H - g) * 55 // 100
        return row(y0, ht, 2) + [(x0, y0 + ht + g, x1, y1)]
    if n == 4:
        h = (H - g) // 2
        return row(y0, h, 2) + row(y0 + h + g, H - h - g, 2)
    if n == 5:
        h1 = (H - 2 * g) * 35 // 100
        h2 = (H - 2 * g) * 35 // 100
        return row(y0, h1, 2) + row(y0 + h1 + g, h2, 2) + [
            (x0, y0 + h1 + g + h2 + g, x1, y1)
        ]
    if n == 6:
        h = (H - 2 * g) // 3
        o = []
        for i in range(3):
            y = y0 + i * (h + g)
            o.extend(row(y, h if i < 2 else y1 - y, 2))
        return o
    hh = (H - g * (n - 1)) // n
    return [
        (
            x0,
            y0 + i * (hh + g),
            x1,
            y0 + i * (hh + g) + (hh if i < n - 1 else y1 - (y0 + i * (hh + g))),
        )
        for i in range(n)
    ]


def cover_fit(img, tw, th):
    img = img.convert("RGB")
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = max(1, int(iw * scale + 0.5)), max(1, int(ih * scale + 0.5))
    img = img.resize((nw, nh), Image.Resampling.BILINEAR)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def contain_fit(img, tw, th):
    img = img.convert("RGB")
    iw, ih = img.size
    scale = min(tw / iw, th / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    img = img.resize((nw, nh), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (tw, th), BG)
    canvas.paste(img, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def place_panel(img, tw, th, *, force_contain: bool = False):
    if force_contain:
        return contain_fit(img, tw, th)
    iw, ih = img.size
    ratio = (iw / ih) / (tw / th)
    if ratio > 2.5 or ratio < 1 / 2.5:
        return contain_fit(img, tw, th)
    return cover_fit(img, tw, th)


def make_placeholder(tw, th, label: str) -> Image.Image:
    """White/light-gray labeled NEW pencil slot — structure only, no fake art."""
    canvas = Image.new("RGB", (tw, th), PLACEHOLDER_BG)
    draw = ImageDraw.Draw(canvas)
    inset = max(4, min(tw, th) // 40)
    draw.rectangle(
        [inset, inset, tw - inset - 1, th - inset - 1],
        outline=PLACEHOLDER_BORDER,
        width=2,
    )
    draw.rectangle(
        [inset + 6, inset + 6, tw - inset - 7, th - inset - 7],
        outline=(180, 180, 180),
        width=1,
    )
    # Flashback cards get larger type
    is_flash = "FLASHBACK" in label.upper()
    f = font(max(18 if is_flash else 16, min(42 if is_flash else 28, tw // (12 if is_flash else 18))))
    words = label.split()
    lines = []
    cur = ""
    max_w = tw - 2 * inset - 20
    for w in words:
        trial = (cur + " " + w).strip()
        bb = draw.textbbox((0, 0), trial, font=f)
        if bb[2] - bb[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # Extra subtitle lines for flashback full page
    if is_flash and tw > 600:
        extra = ["YEARS EARLIER", "How I got the mall job"]
        # Keep primary label, then extras below
        lines = lines + [""] + extra
    line_h = (draw.textbbox((0, 0), "Ag", font=f)[3] - draw.textbbox((0, 0), "Ag", font=f)[1]) + 6
    total_h = line_h * len(lines)
    y = (th - total_h) // 2
    for line in lines:
        if not line:
            y += line_h // 2
            continue
        bb = draw.textbbox((0, 0), line, font=f)
        lw = bb[2] - bb[0]
        draw.text(((tw - lw) // 2, y), line, fill=PLACEHOLDER_TEXT, font=f)
        y += line_h
    return canvas


def render_grid(slots, page_num):
    """slots: list of Path | str starting with NEW:"""
    page = Image.new("RGB", (PAGE_W, PAGE_H), BG)
    draw = ImageDraw.Draw(page)
    area = (MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN - 24)
    draw.rectangle(
        [area[0] - 1, area[1] - 1, area[2] + 1, area[3] + 1],
        outline=GUTTER_COLOR,
        width=1,
    )
    draw.rectangle([area[0], area[1], area[2], area[3]], fill=GUTTER_COLOR)
    n = len(slots)
    force_contain = n == 1 and not (
        isinstance(slots[0], str) and str(slots[0]).startswith(NEW_PREFIX)
    )
    for slot, (rx0, ry0, rx1, ry1) in zip(slots, layout_rects(n, area)):
        tw, th = rx1 - rx0, ry1 - ry0
        if isinstance(slot, str) and slot.startswith(NEW_PREFIX):
            fitted = make_placeholder(tw, th, slot)
        else:
            with Image.open(slot) as im:
                fitted = place_panel(im, tw, th, force_contain=force_contain)
        page.paste(fitted, (rx0, ry0))
    f = font(14)
    label = f"p{page_num}"
    bb = draw.textbbox((0, 0), label, font=f)
    tw = bb[2] - bb[0]
    draw.text(((PAGE_W - tw) // 2, PAGE_H - 20), label, fill=(80, 80, 80), font=f)
    return page


def render_sample(sample_path, page_num):
    with Image.open(sample_path) as im:
        fitted = cover_fit(im.convert("RGB"), PAGE_W, PAGE_H)
    draw = ImageDraw.Draw(fitted)
    f = font(14)
    label = f"p{page_num}"
    bb = draw.textbbox((0, 0), label, font=f)
    tw = bb[2] - bb[0]
    draw.rectangle(
        [PAGE_W // 2 - tw // 2 - 4, PAGE_H - 22, PAGE_W // 2 + tw // 2 + 4, PAGE_H - 4],
        fill=BG,
    )
    draw.text(((PAGE_W - tw) // 2, PAGE_H - 20), label, fill=(80, 80, 80), font=f)
    return fitted


def build_slots_from_map(redo: dict) -> dict[int, list]:
    """Build ordered slots (Path or NEW: label) per issue page from rematch map."""
    pages = redo["pages"]
    page_panels: dict[int, list] = {}
    max_page = max(int(k) for k in pages)
    for pnum in range(FIRST_REMATCH, max_page + 1):
        entry = pages[str(pnum)]
        proposed = entry.get("proposed") or []
        paths = []
        for name in proposed:
            p = PANELS_DIR / name
            if not p.exists():
                raise SystemExit(f"Missing panel for page {pnum}: {p}")
            paths.append(p)
        news = NEW_SLOTS.get(pnum, [])
        before = [n["label"] for n in news if n.get("position") == "before"]
        after = [n["label"] for n in news if n.get("position") != "before"]
        slots = before + paths + after
        if not slots:
            raise SystemExit(f"No slots for page {pnum}")
        page_panels[pnum] = slots
    return page_panels


def slot_to_assignment_name(slot) -> str:
    if isinstance(slot, str) and slot.startswith(NEW_PREFIX):
        return slot
    return Path(slot).name


def main():
    with open(REDO_MAP) as f:
        redo = json.load(f)

    print("Building slots from issue1_redo_map.json proposed + NEW placeholders...")
    page_panels = build_slots_from_map(redo)
    total = max(page_panels)  # should be 31
    assert total == TOTAL_PAGES, f"expected {TOTAL_PAGES} rematch end, got {total}"

    assignment = {}
    for i in range(1, 5):
        assignment[f"page_{i:02d}"] = [f"sample:page-{i}.png"]
    for pnum, slots in page_panels.items():
        assignment[f"page_{pnum:02d}"] = [slot_to_assignment_name(s) for s in slots]

    used_files = []
    for slots in page_panels.values():
        for s in slots:
            if not (isinstance(s, str) and s.startswith(NEW_PREFIX)):
                used_files.append(Path(s).name)
    used_set = set(used_files)

    unused_listed = []
    for u in redo.get("unused_good_panels", []):
        unused_listed.append(u["file"] if isinstance(u, dict) else u)
    intentionally = []
    iu = redo.get("intentionally_unused", {})
    if isinstance(iu, dict):
        for group in iu.values():
            if isinstance(group, list):
                intentionally.extend(group)
    for group_key in ("dense_06_07",):
        g = redo.get(group_key, {})
        if isinstance(g, dict) and "panels" in g:
            intentionally.extend(g["panels"])

    sample_panel_counts = {1: 3, 2: 3, 3: 4, 4: 4}
    meta = {
        "title": "I am the Devil — Issue 1 One-Shot (option A rematch v2)",
        "pages": total,
        "layout": "DEVIL_ISSUE1_ONESHOT_LAYOUT.md",
        "rematch_source": "issue1_redo_map.json",
        "option": "A",
        "notes": (
            "Pages 1–4 finished samples. Pages 5–31 use option-A rematch: "
            "kill p4→p5 ten-minutes/Zohan retell; p16 no surprise-meet Gabi; "
            "p17 full-page FLASHBACK cue; page_08_panel_04 on p18 only; "
            "smoke kept on p14. NEW:… strings are labeled placeholder slots."
        ),
        "source_panels_used": len(used_files),
        "source_panels_used_unique": len(used_set),
        "new_pencil_slots": {
            str(k): [n["label"] for n in v] for k, v in NEW_SLOTS.items()
        },
        "panel_counts": {
            f"page_{i:02d}": (
                len(page_panels[i]) if i >= FIRST_REMATCH else sample_panel_counts[i]
            )
            for i in range(1, total + 1)
        },
        "assignment": {
            f"page_{i:02d}": assignment[f"page_{i:02d}"] for i in range(1, total + 1)
        },
        "unused_good_panels": unused_listed,
        "intentionally_unused": intentionally,
        "changelog_v2": {
            "p5": "Dropped panel_09/11 retell; kept 12 + pulled 13 + NEW love-her",
            "p6": "panel_14 only; dropped panel_15 Zohan continuation",
            "p14": "Smoke unchanged (present-day Act B)",
            "p16": "Dropped panel_13 surprise meet; NEW Gabi already-with-him at lot",
            "p17": "NEW full-page FLASHBACK — How I got here",
            "p18": "Was p17 open-mic; keeps page_08_panel_04",
            "p19": "Was p18 venue; deduped — no page_08_panel_04",
            "p20-31": "Former p19–30 shifted +1 after flashback insert",
        },
    }

    map_path = OUT_DIR / "issue1_panel_assignment.json"
    with open(map_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {map_path}")
    for pnum in sorted(page_panels):
        names = [slot_to_assignment_name(s) for s in page_panels[pnum]]
        print(f"  p{pnum:02d}: {names}")

    # Clear stale page_32+ if any, keep folder clean for this build
    for stale in PAGES_DIR.glob("page_*.jpg"):
        stale.unlink()

    def job(pnum):
        out = PAGES_DIR / f"page_{pnum:02d}.jpg"
        if pnum <= 4:
            img = render_sample(SAMPLES_DIR / f"page-{pnum}.png", pnum)
            n = 0
        else:
            img = render_grid(page_panels[pnum], pnum)
            n = len(page_panels[pnum])
        img.save(out, "JPEG", quality=92, optimize=True)
        return pnum, out, n

    print(f"Rendering {total} pages (threaded)...")
    page_files = [None] * total
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(job, i) for i in range(1, total + 1)]
        for fut in as_completed(futs):
            pnum, out, n = fut.result()
            page_files[pnum - 1] = out
            if pnum >= FIRST_REMATCH:
                print(f"  page_{pnum:02d} ok ({n} slots)")
            else:
                print(f"  page_{pnum:02d} sample ok")

    pdf_path = OUT_DIR / "DEVIL_ISSUE1_ONESHOT.pdf"
    try:
        import img2pdf

        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in page_files]))
    except Exception as e:
        print("img2pdf fail", e)
        imgs = [Image.open(p).convert("RGB") for p in page_files]
        imgs[0].save(
            pdf_path, "PDF", save_all=True, append_images=imgs[1:], resolution=150.0
        )
        for im in imgs:
            im.close()

    rematch_copy = OUT_DIR / "DEVIL_ISSUE1_REMATCH_v2.pdf"
    shutil.copy2(pdf_path, rematch_copy)
    oneshot_v2 = OUT_DIR / "DEVIL_ISSUE1_ONESHOT_v2.pdf"
    shutil.copy2(pdf_path, oneshot_v2)

    # Quick verify: no unintended duplicate source panels
    from collections import Counter

    c = Counter(used_files)
    dups = {k: v for k, v in c.items() if v > 1}
    print("Duplicate source panels:", dups or "none")

    sz = pdf_path.stat().st_size
    print(f"PDF {pdf_path} pages={total} size={sz} ({sz/1e6:.2f} MB)")
    print(f"Copy {rematch_copy}")
    print(f"Copy {oneshot_v2}")
    print("DONE")


if __name__ == "__main__":
    main()
