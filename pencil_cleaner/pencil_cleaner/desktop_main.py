"""Entry point for the comicsTools desktop window."""
from __future__ import annotations

import sys


def _selftest() -> int:
    """Smoke test the packaged app: the model round-trips and the window opens.

    Runs headless (offscreen Qt) so it works on CI runners with no display.
    Prints nothing on success unless run from a terminal; the exit code is the result.
    """
    import os
    import tempfile
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PIL import Image, ImageDraw

    from .studio_project import Project

    with tempfile.TemporaryDirectory(prefix="comics-tools-selftest-") as td:
        root = Path(td)
        scan = root / "scan.png"
        image = Image.new("L", (600, 400), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 280, 380), outline=0, width=4)
        draw.line((40, 60, 240, 200), fill=40, width=6)
        draw.rectangle((320, 20, 580, 380), outline=0, width=4)
        image.save(scan)
        project = Project(name="selftest")
        project.import_files([scan])
        assert project.panels, "panel detection found nothing"
        project.set_page_settings(0, name="Page one", width_in=6.0, height_in=9.0)
        project.set_settings(bw=True)
        thumb = project.page_thumbnail(0)
        assert thumb.size[0] > 0 and thumb.size[1] > 0
        project.set_meta(project.panels[0].id, tags="cover, hero", notes="selftest")
        folder = project.save(root / "proj")
        again = Project.load(folder)
        assert again.page_count() == project.page_count()
        assert len(again.panels) == len(project.panels)
        assert again.page_name(0) == "Page one"
        assert again.panel(again.panels[0].id).tags == ["cover", "hero"]

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from .studio import StudioWindow

    window = StudioWindow()
    window.close()
    del app
    print("selftest OK")
    return 0


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv
    if "--selftest" in list(argv)[1:]:
        try:
            return _selftest()
        except Exception as exc:  # a packaged app that can't smoke-test itself is broken
            sys.stderr.write(f"selftest failed: {type(exc).__name__}: {exc}\n")
            return 1
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "comics-tools needs PySide6.\n"
            "From a checkout of this repository:\n"
            '  pip install "./pencil_cleaner[gui]"\n'
            "  comics-tools\n"
        )
        return 1
    from .studio import main as run_window

    return run_window(list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
