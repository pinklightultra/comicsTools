"""Entry point for the comicsTools desktop window."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
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

    return run_window(argv)


if __name__ == "__main__":
    raise SystemExit(main())
