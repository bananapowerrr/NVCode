# -*- coding: utf-8 -*-
"""Запуск NVCode UI. Runtime запускается отдельно: python dispatcher.py"""
from __future__ import annotations

import os
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
        print("Нужно: pip install customtkinter pyyaml")
        print("Опционально для трея: pip install pystray pillow")
        sys.exit(1)
    from ui.main_window import MainWindow

    os.environ.setdefault("AGENTBUS_FEATURE_PRESET", "beginner_ru")
    app = MainWindow()
    try:
        from ui.tray_manager import start_tray

        start_tray(on_show=app.deiconify, on_quit=app.destroy)
    except Exception:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()
