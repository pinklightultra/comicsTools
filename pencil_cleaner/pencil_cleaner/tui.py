"""
traceJockey Interactive Terminal User Interface (TUI).
Built with Textual and Rich for modern terminal workflows.
Supports:
  - A100 GPU linework cleaning (directional morphology, toner suppression)
  - Vector tracing with potrace and live SVG export
  - Pencil ghost prepper for mono laser printing
  - Comic panel isolation and gutter cutting
  - Terminal ASCII/Unicode half-block scan inspector & diff viewer
  - Live NVIDIA GPU hardware telemetry
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Log,
    ProgressBar,
    RadioButton,
    RadioSet,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from .cleanup import remove_pencils, clean_pdf
from .printprep import prep_pencils_for_printing
from .panels import cut_panels, PanelCutError
from .gpu import is_gpu_available, get_gpu_info, compute_scan_metrics, gpu_clean


def render_terminal_ascii(image_path: Path, width: int = 70, height: int = 25) -> str:
    """Render a grayscale preview of an image using terminal Unicode half-block characters."""
    if not image_path.exists():
        return "[red]File does not exist[/red]"
    try:
        im = Image.open(image_path).convert("L")
        # Resize to fit terminal window (height * 2 because each block char represents 2 vertical pixels)
        im = im.resize((width, height * 2), Image.Resampling.BILINEAR)
        arr = np.array(im, dtype=np.uint8)
        
        # Unicode half-blocks: '▀' top half, '▄' bottom half, ' ' white, '█' black
        lines = []
        for y in range(0, height * 2, 2):
            line_chars = []
            for x in range(width):
                top = arr[y, x]
                bot = arr[y + 1, x] if y + 1 < height * 2 else 255
                # Invert logic: 0 = ink (black), 255 = paper (white)
                if top < 128 and bot < 128:
                    line_chars.append("█")
                elif top < 128 and bot >= 128:
                    line_chars.append("▀")
                elif top >= 128 and bot < 128:
                    line_chars.append("▄")
                else:
                    line_chars.append(" ")
            lines.append("".join(line_chars))
        return "\n".join(lines)
    except Exception as e:
        return f"[red]Error rendering preview: {e}[/red]"


class TraceJockeyApp(App):
    """The traceJockey Terminal UI Application."""

    CSS = """
    Screen {
        background: #121619;
        color: #e0e6ed;
    }

    #gpu-banner {
        height: 3;
        background: #1b2820;
        color: #4ade80;
        border: solid #22c55e;
        padding: 0 1;
        margin-bottom: 1;
    }

    .form-group {
        margin: 1 0;
        background: #1e232a;
        padding: 1;
        border: solid #334155;
    }

    .form-label {
        font-style: bold;
        color: #38bdf8;
        margin-bottom: 1;
    }

    Input {
        margin-bottom: 1;
        background: #0f172a;
        border: tall #0284c7;
    }

    Button {
        margin: 1 1;
        min-width: 16;
    }

    .btn-run {
        background: #16a34a;
        color: #ffffff;
    }

    .btn-preset {
        background: #0284c7;
        color: #ffffff;
    }

    #log-pane {
        height: 12;
        background: #090d10;
        border: solid #334155;
        margin-top: 1;
    }

    #preview-box {
        background: #050709;
        border: solid #475569;
        padding: 1;
        height: 28;
    }

    .stat-badge {
        background: #334155;
        color: #f8fafc;
        padding: 0 1;
        margin-right: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_gpu", "Refresh GPU"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="gpu-banner")

        with TabbedContent():
            # TAB 1: CLEAN INKED SCANS
            with TabPane("⚡ Clean Inked Scan", id="tab-clean"):
                with VerticalScroll():
                    with Container(classes="form-group"):
                        yield Label("Input Inked Scan / PDF Path:", classes="form-label")
                        yield Input(
                            placeholder="/content/drive/MyDrive/Newtraces-1.png",
                            value="/content/drive/MyDrive/Newtraces-1.png",
                            id="clean-input",
                        )
                        with Horizontal():
                            yield Button("Preset: Newtraces-1", id="preset-newtraces1", classes="btn-preset")
                            yield Button("Preset: Newtraces-2", id="preset-newtraces2", classes="btn-preset")
                            yield Button("Preset: Scanned-01", id="preset-scanned1", classes="btn-preset")

                    with Container(classes="form-group"):
                        yield Label("Output Cleaned Image / PDF Path:", classes="form-label")
                        yield Input(
                            placeholder="/content/cleaned_page.png",
                            value="/content/cleaned_page.png",
                            id="clean-output",
                        )

                    with Container(classes="form-group"):
                        yield Label("Cleaning Method:", classes="form-label")
                        yield RadioSet(
                            RadioButton("GPU Tensor Directional (A100 Accelerated)", value=True, id="meth-gpu"),
                            RadioButton("Potrace Vector Trace (.svg + .png)", id="meth-vector"),
                            RadioButton("Morphological (Classic Stroke Width)", id="meth-morph"),
                            RadioButton("Levels (Fast Contrast Remap)", id="meth-levels"),
                            id="clean-method-set",
                        )

                    with Container(classes="form-group"):
                        yield Label("Tuning Parameters:", classes="form-label")
                        with Horizontal():
                            with Vertical():
                                yield Label("Dark Threshold (0-255):")
                                yield Input(value="60", id="clean-thresh")
                            with Vertical():
                                yield Label("Min Stroke Width (px):")
                                yield Input(value="5", id="clean-stroke")
                            with Vertical():
                                yield Label("Gamma Curve:")
                                yield Input(value="1.6", id="clean-gamma")

                    yield Button("⚡ Clean Linework on A100", id="btn-run-clean", classes="btn-run")

            # TAB 2: PREP PENCIL GHOSTS
            with TabPane("✏️ Prep Pencil Ghost", id="tab-prep"):
                with VerticalScroll():
                    with Container(classes="form-group"):
                        yield Label("Input Pencils Scan / PDF Path:", classes="form-label")
                        yield Input(
                            placeholder="/content/drive/MyDrive/The comic pencils scanned.pdf",
                            value="/content/drive/MyDrive/The comic pencils scanned.pdf",
                            id="prep-input",
                        )
                    with Container(classes="form-group"):
                        yield Label("Output Laser Print Ghost Path:", classes="form-label")
                        yield Input(
                            placeholder="/content/pencil_ghost_ready.pdf",
                            value="/content/pencil_ghost_ready.pdf",
                            id="prep-output",
                        )
                    with Container(classes="form-group"):
                        yield Label("Ghost Opacity Preset:", classes="form-label")
                        yield RadioSet(
                            RadioButton("Light (Faint Laser Ghost ~0.08)", value=True, id="prep-light"),
                            RadioButton("Medium (Standard Bristol ~0.15)", id="prep-med"),
                            RadioButton("Dark (Heavy Paper ~0.25)", id="prep-dark"),
                            id="prep-opacity-set",
                        )
                    yield Button("⚡ Generate Laser Print Ghost", id="btn-run-prep", classes="btn-run")

            # TAB 3: COMIC PANEL CUTTER
            with TabPane("✂️ Cut Comic Panels", id="tab-panels"):
                with VerticalScroll():
                    with Container(classes="form-group"):
                        yield Label("Input Page Scan:", classes="form-label")
                        yield Input(
                            placeholder="/content/drive/MyDrive/Newtraces-1.png",
                            value="/content/drive/MyDrive/Newtraces-1.png",
                            id="panels-input",
                        )
                    with Container(classes="form-group"):
                        yield Label("Output Panels Folder:", classes="form-label")
                        yield Input(
                            placeholder="/content/panels_out",
                            value="/content/panels_out",
                            id="panels-output",
                        )
                    yield Button("⚡ Isolate & Export All Panels", id="btn-run-panels", classes="btn-run")

            # TAB 4: SCAN INSPECTOR & TERMINAL ART PREVIEW
            with TabPane("🔍 Terminal Scan Visualizer", id="tab-preview"):
                with VerticalScroll():
                    yield Label("Terminal Scan Inspector (Unicode Half-Block Rendering):", classes="form-label")
                    with Horizontal():
                        yield Input(
                            value="/content/cleaned_page.png",
                            placeholder="Path to image to inspect",
                            id="inspect-path",
                        )
                        yield Button("Render Preview", id="btn-render-preview")
                    yield Static(id="preview-box")

            # TAB 5: A100 TELEMETRY & HARDWARE
            with TabPane("🖥️ A100 Hardware Telemetry", id="tab-hw"):
                with VerticalScroll():
                    yield Static(id="hw-details-box")
                    yield Button("Run GPU vs CPU Benchmark", id="btn-run-bench", classes="btn-preset")

        yield Log(id="log-pane")
        yield Footer()

    def on_mount(self) -> None:
        self.update_gpu_banner()
        self.log_msg("traceJockey TUI initialized. Ready for comic linework processing.")

    def log_msg(self, msg: str) -> None:
        log_widget = self.query_one("#log-pane", Log)
        timestamp = time.strftime("%H:%M:%S")
        log_widget.write_line(f"[{timestamp}] {msg}")

    def update_gpu_banner(self) -> None:
        info = get_gpu_info()
        banner = self.query_one("#gpu-banner", Static)
        if info["available"]:
            text = (
                f"🟢 [b]GPU ONLINE[/b] | {info['device_name']} | "
                f"VRAM: {info['vram_allocated_gb']:.2f} GB / {info['vram_total_gb']:.2f} GB | "
                f"CUDA: {info['cuda_version']} | Multiprocessors: {info['multiprocessors']} | "
                f"⚡ Directional Tensor Engine Ready"
            )
        else:
            text = "🟡 [b]CPU MODE[/b] | CUDA not detected. Falling back to CPU OpenCV/SciPy."
        banner.update(text)

        # Update Tab 5 details
        hw_box = self.query_one("#hw-details-box", Static)
        tbl = Table(title="Hardware & Accelerator Profile", border_style="green")
        tbl.add_column("Property", style="cyan")
        tbl.add_column("Value", style="bold white")
        for k, v in info.items():
            tbl.add_row(k.replace("_", " ").title(), str(v))
        hw_box.update(tbl)

    def on_button_pressed(self, event: Button.ButtonEvent) -> None:
        btn_id = event.button.id

        # Presets
        if btn_id == "preset-newtraces1":
            self.query_one("#clean-input", Input).value = "/content/drive/MyDrive/Newtraces-1.png"
            self.log_msg("Selected preset: Newtraces-1.png")
        elif btn_id == "preset-newtraces2":
            self.query_one("#clean-input", Input).value = "/content/drive/MyDrive/Newtraces-2.png"
            self.log_msg("Selected preset: Newtraces-2.png")
        elif btn_id == "preset-scanned1":
            self.query_one("#clean-input", Input).value = "/content/drive/MyDrive/Scanned Document-01.png"
            self.log_msg("Selected preset: Scanned Document-01.png")

        # Action: Clean Inked Scan
        elif btn_id == "btn-run-clean":
            self.action_run_clean()

        # Action: Prep Pencil Ghost
        elif btn_id == "btn-run-prep":
            self.action_run_prep()

        # Action: Cut Comic Panels
        elif btn_id == "btn-run-panels":
            self.action_run_panels()

        # Action: Render Terminal Preview
        elif btn_id == "btn-render-preview":
            self.action_render_preview()

        # Action: Benchmark
        elif btn_id == "btn-run-bench":
            self.action_run_benchmark()

    def action_run_clean(self) -> None:
        in_path = Path(self.query_one("#clean-input", Input).value)
        out_path = Path(self.query_one("#clean-output", Input).value)

        if not in_path.exists():
            self.log_msg(f"❌ Input file not found: {in_path}")
            return

        method = "gpu"
        if self.query_one("#meth-vector", RadioButton).value:
            method = "vector"
        elif self.query_one("#meth-morph", RadioButton).value:
            method = "morphological"
        elif self.query_one("#meth-levels", RadioButton).value:
            method = "levels"

        dark_thresh = int(self.query_one("#clean-thresh", Input).value)
        min_stroke = int(self.query_one("#clean-stroke", Input).value)
        gamma = float(self.query_one("#clean-gamma", Input).value)

        self.log_msg(f"▶ Starting Clean: {in_path.name} | Method: {method.upper()} | GPU: {is_gpu_available()}")
        t0 = time.time()

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if in_path.suffix.lower() == ".pdf":
                clean_pdf(
                    in_path,
                    out_path,
                    method=method,
                    dark_thresh=dark_thresh,
                    min_stroke_px=min_stroke,
                    gamma=gamma,
                )
            else:
                svg_path = out_path.with_suffix(".svg") if method == "vector" else None
                img = remove_pencils(
                    in_path,
                    out_path,
                    method=method,
                    dark_thresh=dark_thresh,
                    min_stroke_px=min_stroke,
                    gamma=gamma,
                    svg_output_path=svg_path,
                )
                metrics = compute_scan_metrics(np.array(img))
                self.log_msg(
                    f"✓ Clean Metrics: Paper Cleanliness={metrics['paper_cleanliness_pct']}% | "
                    f"Solid Ink={metrics['solid_ink_density_pct']}% | Sharpness={metrics['edge_sharpness']}"
                )

            elapsed = (time.time() - t0) * 1000
            self.log_msg(f"✓ Completed in {elapsed:.1f} ms -> Saved: {out_path}")
            # Auto update visualizer input
            self.query_one("#inspect-path", Input).value = str(out_path)
            self.action_render_preview()

        except Exception as e:
            self.log_msg(f"❌ Clean Error: {e}")

    def action_run_prep(self) -> None:
        in_path = Path(self.query_one("#prep-input", Input).value)
        out_path = Path(self.query_one("#prep-output", Input).value)
        if not in_path.exists():
            self.log_msg(f"❌ Input file not found: {in_path}")
            return

        opacity = "light"
        if self.query_one("#prep-med", RadioButton).value:
            opacity = "medium"
        elif self.query_one("#prep-dark", RadioButton).value:
            opacity = "dark"

        self.log_msg(f"▶ Prepping pencil ghost: {in_path.name} (Opacity: {opacity})")
        t0 = time.time()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if in_path.suffix.lower() == ".pdf":
                from .printprep import prep_pdf_for_printing
                prep_pdf_for_printing(in_path, out_path, opacity=opacity)
            else:
                prep_pencils_for_printing(in_path, out_path, opacity=opacity)
            elapsed = (time.time() - t0) * 1000
            self.log_msg(f"✓ Ghost ready in {elapsed:.1f} ms -> Saved: {out_path}")
        except Exception as e:
            self.log_msg(f"❌ Prep Error: {e}")

    def action_run_panels(self) -> None:
        in_path = Path(self.query_one("#panels-input", Input).value)
        out_dir = Path(self.query_one("#panels-output", Input).value)
        if not in_path.exists():
            self.log_msg(f"❌ Input file not found: {in_path}")
            return

        self.log_msg(f"▶ Scanning gutters and isolating comic panels on {in_path.name}...")
        t0 = time.time()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            paths, overview = cut_panels(in_path, out_dir)
            elapsed = (time.time() - t0) * 1000
            self.log_msg(f"✓ Cut {len(paths)} comic panel(s) in {elapsed:.1f} ms!")
            self.log_msg(f"  Overview map: {overview}")
            for p in paths[:4]:
                self.log_msg(f"  - Panel: {p.name}")
            if len(paths) > 4:
                self.log_msg(f"  - ... and {len(paths) - 4} more panels in {out_dir}")
        except Exception as e:
            self.log_msg(f"❌ Panel Cutter Error: {e}")

    def action_render_preview(self) -> None:
        path = Path(self.query_one("#inspect-path", Input).value)
        box = self.query_one("#preview-box", Static)
        if not path.exists():
            box.update(f"[red]Image file does not exist: {path}[/red]")
            return
        ascii_art = render_terminal_ascii(path, width=74, height=20)
        box.update(ascii_art)
        self.log_msg(f"Rendered terminal preview for {path.name}")

    def action_run_benchmark(self) -> None:
        self.log_msg("▶ Running A100 GPU vs CPU Morphological Benchmark (3508x2550)...")
        if not is_gpu_available():
            self.log_msg("❌ Benchmark requires CUDA GPU.")
            return

        import torch
        import torch.nn.functional as F
        import cv2

        H, W = 3508, 2550
        cand_np = (np.random.randint(0, 256, (H, W), dtype=np.uint8) > 60).astype(np.uint8)

        # CPU
        t0 = time.time()
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        seed_cpu = cv2.erode(cand_np, k)
        rec_cpu = seed_cpu.copy()
        k3 = np.ones((3, 3), np.uint8)
        for _ in range(40):
            grown = cv2.dilate(rec_cpu, k3)
            grown = cv2.bitwise_and(grown, cand_np)
            if np.array_equal(grown, rec_cpu):
                break
            rec_cpu = grown
        cpu_ms = (time.time() - t0) * 1000

        # GPU
        t_cand = torch.from_numpy(cand_np).cuda().float().unsqueeze(0).unsqueeze(0)
        t0 = time.time()
        seed_gpu = -F.max_pool2d(-t_cand, 5, 1, 2)
        rec_gpu = seed_gpu.clone()
        for _ in range(40):
            grown_gpu = F.max_pool2d(rec_gpu, 3, 1, 1)
            grown_gpu = torch.minimum(grown_gpu, t_cand)
            rec_gpu = grown_gpu
        torch.cuda.synchronize()
        gpu_ms = (time.time() - t0) * 1000

        speedup = cpu_ms / max(gpu_ms, 0.001)
        self.log_msg(f"🏁 Benchmark Complete: CPU={cpu_ms:.1f}ms | A100 GPU={gpu_ms:.1f}ms | Speedup={speedup:.1f}x!")


def main() -> int:
    app = TraceJockeyApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
