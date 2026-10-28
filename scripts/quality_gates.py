#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ub-mode quality gates — offline, deterministic, no third-party deps.

Proxy for the Crapper/Dryer gates from the ub-mode skill:

* Cyclomatic complexity per function, AST-based (exact).
  Worst-case CRAP at zero coverage: comp^2 + comp.
  With full test coverage CRAP == comp, so comp <= 9 is the
  "CRAP < 10" target. The gate ratchets down via --max.
* Structural duplication: 6-line normalized windows occurring twice
  or more (report only — warn, never fail).

Modes:
    python scripts/quality_gates.py                       # report (exit 0)
    python scripts/quality_gates.py --gate --max 12      # fail on new debt
    python scripts/quality_gates.py --update-allowlist --max 12

Gate semantics: a function with complexity > --max fails the gate unless
"<path>::<name>" is listed in the allowlist file. The allowlist freezes
existing debt so NEW violations always fail. After fixing debt,
regenerate and review the diff:
    python scripts/quality_gates.py --update-allowlist --max 12
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

MATCH_CASE = getattr(ast, "match_case", None)
_FUNC_TYPES: tuple[Any, ...] = (ast.FunctionDef, ast.AsyncFunctionDef)


class Fn:
    """One measured function (path, qualified name, complexity)."""

    __slots__ = ("path", "name", "comp", "lineno", "end_lineno")

    def __init__(self, path: str, name: str, comp: int, lineno: int, end_lineno: int) -> None:
        self.path = path
        self.name = name
        self.comp = comp
        self.lineno = lineno
        self.end_lineno = end_lineno

    @property
    def lines(self) -> int:
        return max(1, int(self.end_lineno) - int(self.lineno) + 1)

    @property
    def key(self) -> str:
        return f"{self.path}::{self.name}"

    @property
    def crap_worst(self) -> int:
        return self.comp * self.comp + self.comp


def complexity(fn: ast.AST) -> int:
    """Cyclomatic complexity of one function (nested defs excluded)."""
    total = 1

    def visit(node: ast.AST) -> None:
        nonlocal total
        if isinstance(node, _FUNC_TYPES + (ast.Lambda,)):
            return  # nested callable: measured separately
        if isinstance(
            node,
            (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert),
        ):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += 1 + len(node.ifs)
        elif MATCH_CASE is not None and isinstance(node, MATCH_CASE):
            total += 1
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in getattr(fn, "body", []):
        visit(stmt)
    return total


def scan(node: ast.AST, prefix: str, path: str, out: list[Fn]) -> None:
    """Collect functions; class methods get Class.method names."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNC_TYPES):
            end = getattr(child, "end_lineno", None) or child.lineno
            out.append(Fn(path, prefix + child.name, complexity(child), child.lineno, end))
            scan(child, "", path, out)  # nested defs get flat names
        elif isinstance(child, ast.ClassDef):
            scan(child, prefix + child.name + ".", path, out)
        else:
            scan(child, prefix, path, out)


def iter_py_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        p = Path(root)
        if p.is_file():
            files.append(p)
        else:
            files.extend(sorted(p.rglob("*.py")))
    return files


def collect(roots: list[str]) -> tuple[list[Fn], list[str]]:
    fns: list[Fn] = []
    errors: list[str] = []
    for f in iter_py_files(roots):
        rel = f.as_posix()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
        except SyntaxError as exc:
            errors.append(f"{rel}: syntax error: {exc}")
            continue
        scan(tree, "", rel, fns)
    return fns, errors


def load_allowlist(path: str) -> set[str]:
    p = Path(path)
    if not p.is_file():
        return set()
    keys: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            keys.add(s)
    return keys


def write_allowlist(path: str, entries: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(sorted(set(entries))) + "\n", encoding="utf-8")


def normalize_line(line: str) -> str:
    """Strip comment, mask strings and numbers so near-duplicates collapse."""
    t = line.strip().replace("\t", " ")
    hash_pos = t.find("#")
    if hash_pos >= 0:
        t = t[:hash_pos]
    t = t.strip()
    out: list[str] = []
    i = 0
    while i < len(t):
        c = t[i]
        if c in "\"'":
            q = c
            i += 1
            while i < len(t) and t[i] != q:
                i += 1
            i += 1
            out.append("S")
            continue
        if c.isdigit():
            while i < len(t) and (t[i].isdigit() or t[i] == "."):
                i += 1
            out.append("N")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def duplication_report(roots: list[str], window: int = 6, min_rich: int = 2) -> list[tuple[int, str, str]]:
    """Duplicated normalized windows; returns (count, first_location, head)."""
    seen: dict[str, list] = {}
    for f in iter_py_files(roots):
        rel = f.as_posix()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
        except OSError:
            continue
        norm = [normalize_line(x) for x in lines]
        for i in range(0, len(norm) - window + 1):
            win = norm[i:i + window]
            if any(not x for x in win):
                continue
            rich = sum(1 for x in win if ("(" in x or "=" in x))
            if rich < min_rich:
                continue
            key = "\x01".join(win)
            if key in seen:
                seen[key][0] += 1
            else:
                seen[key] = [1, f"{rel}:{i + 1}", win[0]]
    out = [(cnt, loc, head) for cnt, loc, head in seen.values() if cnt >= 2]
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ub-mode quality gates (offline)")
    ap.add_argument("--gate", action="store_true", help="fail on complexity > max (allowlist exempt)")
    ap.add_argument("--update-allowlist", action="store_true")
    ap.add_argument("--max", type=int, default=12)
    ap.add_argument("--report-min", type=int, default=10)
    ap.add_argument("--paths", nargs="+", default=["src"])
    ap.add_argument("--allowlist", default="config/quality_gates_allowlist.txt")
    ap.add_argument("--no-dup", action="store_true")
    args = ap.parse_args(argv)

    fns, errors = collect(args.paths)
    for e in errors:
        print(f"[quality-gates] WARN unparsed: {e}")

    offenders = sorted(
        (f for f in fns if f.comp >= args.report_min),
        key=lambda f: (-f.comp, f.path, f.name),
    )
    print(f"[quality-gates] functions: {len(fns)}; comp >= {args.report_min}: {len(offenders)}")
    for f in offenders:
        print(f"  comp={f.comp:<3} CRAP<={f.crap_worst:<5} L={f.lines:<4} {f.key}")

    if not args.no_dup:
        dups = duplication_report(args.paths)
        print(f"[quality-gates] duplicated 6-line windows (>=2): {len(dups)} (top 10 shown)")
        for cnt, loc, head in dups[:10]:
            print(f"  x{cnt}  {loc}  {head[:70]}")

    if args.update_allowlist:
        entries = [f.key for f in fns if f.comp > args.max]
        write_allowlist(args.allowlist, entries)
        print(
            f"[quality-gates] allowlist updated: {len(set(entries))} entries (comp > {args.max}) -> {args.allowlist}"
        )
        return 0

    if args.gate:
        allowed = load_allowlist(args.allowlist)
        violations = [f for f in fns if f.comp > args.max and f.key not in allowed]
        if violations:
            print(f"[quality-gates] GATE FAIL: {len(violations)} function(s) exceed comp {args.max}:")
            for f in sorted(violations, key=lambda f: -f.comp):
                print(f"  comp={f.comp}  {f.key}")
            return 1
        print(f"[quality-gates] GATE OK: nothing exceeds comp {args.max} outside allowlist")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
