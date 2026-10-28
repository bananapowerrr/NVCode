#!/usr/bin/env python3
from pathlib import Path
import base64
import sys

root = Path(__file__).resolve().parent
parts = sorted(root.glob("_exec_b64_*.txt"))
if not parts:
    print("no parts")
    sys.exit(1)
b64 = "".join(p.read_text(encoding="utf-8").strip() for p in parts)
data = base64.b64decode(b64)
out = root.parent / "src" / "core" / "executor.py"
out.write_bytes(data)
print("wrote", out, len(data))
assert b"class Executor" in data and b"PLACEHOLDER" not in data
assert b"TD-027" in data
print("OK: executor restored with TD-027")
