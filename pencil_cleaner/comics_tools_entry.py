"""PyInstaller entry. Absolute import so the frozen app is a package."""

from pencil_cleaner.desktop_main import main

if __name__ == "__main__":
    raise SystemExit(main())
