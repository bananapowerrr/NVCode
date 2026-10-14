# -*- coding: utf-8 -*-
"""NVCode Admin UI — feature flags / optional modules (separate from dispatcher_ui)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
for p in (ROOT, SRC):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> None:
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("Нужно: pip install customtkinter")
        sys.exit(1)
    from ui.admin_window import main as run

    run()


if __name__ == "__main__":
    main()
