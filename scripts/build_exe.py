# -*- coding: utf-8 -*-
"""Build the NVCode Windows package via PyInstaller.

On Windows (from repo root):
    pip install pyinstaller
    python scripts/build_exe.py

Produces:
    dist/NVCodeUI.exe   — GUI (chat)
    dist/NVCode.exe     — dispatcher CLI (console)

Workers (Aider/Ollama) still need system Python/tools; the executables wrap entry points.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> int:
    try:
        import PyInstaller.__main__ as pi
    except ImportError:
        print("pip install pyinstaller")
        return 1
    pi.run(args)
    return 0


def main() -> int:
    import os
    os.chdir(ROOT)
    sep = ";" if sys.platform == "win32" else ":"
    data = [
        f"--add-data=config{sep}config",
        f"--add-data=recipes{sep}recipes",
        f"--add-data=docs{sep}docs",
    ]
    hidden = [
        "--hidden-import=yaml",
        "--hidden-import=customtkinter",
        "--hidden-import=core",
        "--hidden-import=core.runtime",
        "--hidden-import=core.dispatcher_main",
        "--collect-all=customtkinter",
    ]
    print("Building NVCodeUI (windowed)…")
    rc = _run([
        "dispatcher_ui.py",
        "--name=NVCodeUI",
        "--onefile",
        "--windowed",
        *data,
        *hidden,
        "--hidden-import=pystray",
    ])
    if rc != 0:
        return rc
    print("Building NVCode (console dispatcher)…")
    rc = _run([
        "dispatcher.py",
        "--name=NVCode",
        "--onefile",
        "--console",
        *data,
        *hidden,
    ])
    if rc != 0:
        return rc
    print("OK: dist/NVCodeUI.exe, dist/NVCode.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
