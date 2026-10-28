#!/usr/bin/env python3
"""Emergency: restore src/core/executor.py then apply TD-027.

A bad push replaced executor.py with the literal PLACEHOLDER.
This script downloads the last good version (17db516) and applies TD-027.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOOD_URL = (
    "https://raw.githubusercontent.com/bananapowerrr/NVCode/"
    "17db516d74ef94980280ef9470ebec76ad418bf3/src/core/executor.py"
)
TARGET = ROOT / "src" / "core" / "executor.py"


def main() -> int:
    print("Downloading good executor from 17db516…")
    data = urllib.request.urlopen(GOOD_URL, timeout=60).read()
    if b"class Executor" not in data or len(data) < 10000:
        print("ABORT: downloaded content looks invalid", len(data))
        return 1
    TARGET.write_bytes(data)
    print(f"Restored {TARGET} ({len(data)} bytes)")
    apply = ROOT / "scripts" / "apply_td021_td027.py"
    if apply.is_file():
        r = subprocess.run([sys.executable, str(apply)], cwd=str(ROOT))
        return int(r.returncode)
    print("WARN: apply_td021_td027.py missing — restored base only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
