"""Frozen-app entry point (PyInstaller).

A top-level launcher with an **absolute** import so PyInstaller — which runs the entry script
as ``__main__`` with no package context — can start the package CLI. (``python -m mynah``
still goes through ``mynah/__main__.py``.)
"""

from mynah.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
