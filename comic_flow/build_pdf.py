#!/usr/bin/env python3
"""Build clean printable PDF of Devil revised page flow."""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

SRC = Path("/workspace/comic_flow/DEVIL_REVISED_PAGE_FLOW.md")
OUT = Path("/workspace/comic_flow/DEVIL_REVISED_PAGE_FLOW.pdf")

# ASCII-safe replacements for curly/smart punctuation
REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "--",
    "\u2026": "...",
    "\u2022": "-",
    "\u00a0": " ",
    "\u2192": "->",
    "\u2248": "~",
}


def ascii_safe(s: str) -> str:
    for a, b in REPLACEMENTS.items():
        s = s.replace(a, b)
    # strip remaining non-latin1 that Helvetica can't handle
    return s.encode("latin-1", "replace").decode("latin-1")


class FlowPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, f"{self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)

    def header_line(self, text: str):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(60, 60, 60)
        self.cell(0, 6, ascii_safe(text), align="L")
        self.ln(2)
        self.set_draw_color(180, 180, 180)
        self.line(18, self.get_y(), 198, self.get_y())
        self.ln(6)
        self.set_text_color(0, 0, 0)

    def h1(self, text: str):
        self.set_font("Helvetica", "B", 16)
        self.multi_cell(0, 8, ascii_safe(text))
        self.ln(2)

    def h2(self, text: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 13)
        self.multi_cell(0, 7, ascii_safe(text))
        self.ln(1)

    def h3(self, text: str):
        self.ln(1.5)
        # keep heading with following content
        if self.get_y() > 250:
            self.add_page()
        self.set_font("Helvetica", "B", 11.5)
        self.multi_cell(0, 6, ascii_safe(text))
        self.ln(0.5)

    def body(self, text: str, size: float = 11):
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 5.5, ascii_safe(text))
        self.ln(0.5)

    def bold_body(self, text: str, size: float = 11):
        self.set_font("Helvetica", "B", size)
        self.multi_cell(0, 5.5, ascii_safe(text))
        self.ln(0.5)

    def bullet(self, text: str, indent: float = 4, size: float = 11):
        self.set_font("Helvetica", "", size)
        x = self.l_margin + indent
        self.set_x(x)
        # hanging indent for wrapped lines
        w = self.w - self.r_margin - x
        self.multi_cell(w, 5.2, ascii_safe("- " + text))
        self.ln(0.3)

    def note(self, text: str):
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(70, 70, 70)
        self.set_x(self.l_margin + 4)
        w = self.w - self.r_margin - self.l_margin - 4
        self.multi_cell(w, 5, ascii_safe("Notes: " + text))
        self.set_text_color(0, 0, 0)
        self.ln(1)


def parse_markdown(md: str):
    # TLDR block: lines after title until ---
    lines = md.splitlines()

    tldr_lines = []
    i = 0
    # skip title
    while i < len(lines) and not lines[i].startswith("**TLDR"):
        i += 1
    while i < len(lines) and not lines[i].startswith("---"):
        if lines[i].strip():
            tldr_lines.append(lines[i].strip())
        i += 1

    # Beat inventory: ## Beat inventory ... until ---
    beat_section = []
    while i < len(lines) and not lines[i].startswith("## Beat inventory"):
        i += 1
    i += 1
    while i < len(lines) and not (lines[i].startswith("---") and i > 0):
        beat_section.append(lines[i])
        i += 1
        if lines[i - 1].startswith("---"):
            break
    # remove trailing ---
    while beat_section and beat_section[-1].startswith("---"):
        beat_section.pop()

    # Finished samples overlap
    samples = []
    while i < len(lines) and not lines[i].startswith("## Overlap"):
        i += 1
    # skip to table rows
    while i < len(lines) and not lines[i].startswith("| **Page"):
        i += 1
    while i < len(lines) and lines[i].startswith("|"):
        row = lines[i]
        if "Page" in row and "---" not in row:
            parts = [p.strip() for p in row.strip("|").split("|")]
            if len(parts) >= 2:
                samples.append((parts[0].replace("**", ""), parts[1]))
        i += 1
    sample_note = ""
    while i < len(lines) and not lines[i].startswith("---"):
        if lines[i].startswith("**Not yet"):
            sample_note = lines[i].strip().strip("*")
        i += 1

    # Revised page flow
    while i < len(lines) and not lines[i].startswith("## Revised page flow"):
        i += 1
    intro = ""
    i += 1
    if i < len(lines) and lines[i].startswith("*"):
        intro = lines[i].strip("*").strip()
        i += 1

    pages = []
    current = None
    while i < len(lines) and not lines[i].startswith("---"):
        line = lines[i]
        m = re.match(r"^### Page (\d+)\s*[—–-]\s*(.+)$", line)
        if m:
            if current:
                pages.append(current)
            current = {
                "num": int(m.group(1)),
                "title": m.group(2).strip(),
                "panels": None,
                "beats": [],
                "notes": None,
            }
        elif current:
            pm = re.match(r"^- \*\*Panels:\*\*\s*(\d+)", line)
            if pm:
                current["panels"] = int(pm.group(1))
            elif line.startswith("- **Notes:**"):
                current["notes"] = line.replace("- **Notes:**", "").strip()
            elif re.match(r"^- \d+\.", line):
                beat = re.sub(r"^- \d+\.\s*", "", line)
                # strip markdown bold markers for cleaner print, keep emphasis via caps already in text
                beat = beat.replace("**", "")
                current["beats"].append(beat)
            elif line.startswith("- ") and current["panels"] is None:
                pass
        i += 1
    if current:
        pages.append(current)

    # Summary section
    summary_lines = []
    while i < len(lines) and not lines[i].startswith("## Summary"):
        i += 1
    i += 1
    while i < len(lines):
        summary_lines.append(lines[i])
        i += 1

    return {
        "tldr": tldr_lines,
        "beats": beat_section,
        "samples": samples,
        "sample_note": sample_note,
        "intro": intro,
        "pages": pages,
        "summary": summary_lines,
    }


def shorten_beat_inventory(beat_lines):
    """Compact pencil inventory for appendix."""
    entries = []
    current = None
    for line in beat_lines:
        m = re.match(r"^### Pencils (p\d+) \(([^)]+)\)\s*[—–-]\s*(.+)$", line)
        if m:
            if current:
                entries.append(current)
            current = {
                "id": m.group(1),
                "count": m.group(2),
                "title": m.group(3).strip(),
                "items": [],
            }
        elif current and line.startswith("- "):
            item = line[2:].strip().replace("**", "")
            # keep short; truncate long bullets
            if len(item) > 140:
                item = item[:137] + "..."
            current["items"].append(item)
    if current:
        entries.append(current)
    return entries


def parse_summary(summary_lines):
    table_rows = []
    mapping = []
    art_notes = []
    mode = None
    for line in summary_lines:
        if line.startswith("|") and "Pages" not in line and "--" not in line:
            parts = [p.strip().replace("**", "") for p in line.strip("|").split("|")]
            if len(parts) >= 3:
                table_rows.append(parts)
        elif line.startswith("**Old"):
            table_rows.append(["NOTE", line.replace("**", "").strip(), ""])
        elif line.startswith("### Mapping"):
            mode = "map"
        elif line.startswith("### Art"):
            mode = "art"
        elif mode == "map" and line.startswith("- "):
            mapping.append(line[2:].strip().replace("**", ""))
        elif mode == "art" and line.startswith("- "):
            art_notes.append(line[2:].strip().replace("**", ""))
    return table_rows, mapping, art_notes


def build(data):
    pdf = FlowPDF()

    # ----- Title page -----
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font("Helvetica", "B", 22)
    pdf.multi_cell(0, 10, ascii_safe("I am the Devil / Devil in Orlando"), align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 8, ascii_safe("Revised Page Flow"), align="C")
    pdf.ln(10)
    pdf.set_draw_color(40, 40, 40)
    pdf.line(60, pdf.get_y(), 156, pdf.get_y())
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(
        0,
        6,
        ascii_safe(
            "Treatment / pacing document\n"
            "Dense pencils (15 pages) opened to ~37 pages\n"
            "Target: 3-6 panels/page, captions + room for art"
        ),
        align="C",
    )
    pdf.ln(20)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        6,
        ascii_safe("Contents: TLDR | Revised pages 1-37 | Summary | Appendix (beat inventory)"),
        align="C",
    )
    pdf.set_text_color(0, 0, 0)

    # ----- TLDR -----
    pdf.add_page()
    pdf.header_line("I am the Devil / Devil in Orlando -- Revised Page Flow")
    pdf.h1("TLDR")
    for line in data["tldr"]:
        # strip markdown bold
        clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        clean = clean.replace("**", "")
        if clean.startswith("TLDR:"):
            pdf.bold_body(clean, 11.5)
        else:
            pdf.body(clean, 11)

    pdf.ln(2)
    pdf.h2("Style target")
    pdf.body(
        "Open layouts, narration captions, 3-4 panels often, splash/wide establishing OK -- "
        "not dense indie grids. Prefer quiet/wide beats over cramming."
    )

    # Finished samples as pages 1-4
    pdf.h2("Pages 1-4 -- Finished samples (keep; do not redraw)")
    for title, beats in data["samples"]:
        pdf.bold_body(f"{title}: {beats}", 11)
    if data["sample_note"]:
        note = re.sub(r"\*\*([^*]+)\*\*", r"\1", data["sample_note"])
        pdf.note(note.replace("Not yet in samples (still from pencils p01): ", ""))

    if data["intro"]:
        pdf.ln(1)
        pdf.body(data["intro"], 10)

    # ----- Revised page flow (priority) -----
    pdf.add_page()
    pdf.header_line("I am the Devil / Devil in Orlando -- Revised Page Flow")
    pdf.h1("Revised page flow")
    pdf.body(
        "Pages 1-4 = finished samples (above). Pages 5-37 = new open layouts below. "
        "Each entry: panel count, then panel-by-panel beats."
    )

    for p in data["pages"]:
        title = f"Page {p['num']} -- {p['title']}"
        if p["panels"]:
            title += f"  |  {p['panels']} panels"
        pdf.h3(title)
        for idx, beat in enumerate(p["beats"], 1):
            # strip remaining markdown
            beat_clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", beat)
            beat_clean = beat_clean.replace("*", "")
            pdf.bullet(f"{idx}. {beat_clean}", indent=2, size=11)
        if p["notes"]:
            notes = re.sub(r"\*\*([^*]+)\*\*", r"\1", p["notes"])
            pdf.note(notes)

    # ----- Summary -----
    pdf.add_page()
    pdf.header_line("I am the Devil / Devil in Orlando -- Revised Page Flow")
    pdf.h1("Summary counts")

    table_rows, mapping, art_notes = parse_summary(data["summary"])
    pdf.set_font("Helvetica", "B", 11)
    # simple text table
    col_w = [70, 30, 40]
    headers = ["Set", "Pages", "Panels (approx)"]
    pdf.set_fill_color(240, 240, 240)
    x0 = pdf.l_margin
    for i, h in enumerate(headers):
        pdf.set_x(x0 + sum(col_w[:i]))
        pdf.cell(col_w[i], 7, h, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 11)
    for row in table_rows:
        if row[0] == "NOTE":
            pdf.ln(2)
            pdf.body(row[1], 11)
            continue
        # ensure 3 cols
        while len(row) < 3:
            row.append("")
        y_before = pdf.get_y()
        if y_before > 260:
            pdf.add_page()
        for i, cell in enumerate(row[:3]):
            pdf.set_x(x0 + sum(col_w[:i]))
            pdf.cell(col_w[i], 7, ascii_safe(cell)[:40], border=1)
        pdf.ln()

    pdf.h2("Mapping cheat sheet (pencils -> revised)")
    for m in mapping:
        pdf.bullet(m, indent=2)

    pdf.h2("Art / pacing notes")
    for n in art_notes:
        n = re.sub(r"\*\*([^*]+)\*\*", r"\1", n)
        pdf.bullet(n, indent=2)

    # ----- Appendix: compact beat inventory -----
    pdf.add_page()
    pdf.header_line("I am the Devil / Devil in Orlando -- Revised Page Flow")
    pdf.h1("Appendix: Beat inventory (pencils 01-15)")
    pdf.body(
        "Source dense pencils condensed for reference. Prefer Revised page flow above for layouts.",
        10,
    )

    entries = shorten_beat_inventory(data["beats"])
    for e in entries:
        pdf.h3(f"Pencils {e['id']} ({e['count']}) -- {e['title']}")
        # join items into shorter bullets (max ~5 shown fully; rest summarized)
        for item in e["items"]:
            pdf.bullet(item, indent=2, size=10)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    return OUT, pdf.page_no()


def main():
    md = SRC.read_text(encoding="utf-8")
    data = parse_markdown(md)
    path, pages = build(data)
    size = path.stat().st_size
    print(f"WROTE {path}")
    print(f"PAGES {pages}")
    print(f"BYTES {size}")
    print(f"PARSED_REVISED_PAGES {len(data['pages'])}")
    print(f"SAMPLE_ROWS {len(data['samples'])}")


if __name__ == "__main__":
    main()
