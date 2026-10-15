#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble protocol/server.py and rest_handlers.py from protocol/_parts/."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "protocol" / "_parts"


def assemble(name: str) -> None:
    d = PARTS / name
    chunks = sorted(d.glob("*.txt"))
    if not chunks:
        raise SystemExit(f"no parts for {name}")
    text = "".join(p.read_text(encoding="utf-8") for p in chunks)
    out = ROOT / "protocol" / name
    out.write_text(text, encoding="utf-8", newline="\n")
    print("assembled", out, len(text))


def main() -> None:
    assemble("server.py")
    assemble("rest_handlers.py")


if __name__ == "__main__":
    main()
