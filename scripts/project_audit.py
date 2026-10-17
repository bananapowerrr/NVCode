# -*- coding: utf-8 -*-
"""AgentBus pre-release project audit (no network required).

Checks:
  - duplicate basenames in src/
  - Python syntax of src/, ui/, scripts/, tests/
  - imports of known-removed legacy modules
  - root pollution (unexpected top-level files)
  - config references (feature_flags, presets, policy)
  - entrypoints exist
  - optional pytest -q (offline)

Usage:
  python scripts/project_audit.py
  python scripts/project_audit.py --pytest
Exit 0 = clean, 1 = problems found.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
LEGACY_IMPORTS = (
    "runtime_patch",
    "runtime_verify_patch",
    "runtime_project_patch",
    "runtime_dedupe_patch",
    "runtime_latency_patch",
    "supabase",
)
ALLOWED_ROOT = {
    "README.md",
    "AGENTS.md",
    "requirements.txt",
    "requirements-ui.txt",
    "pyproject.toml",
    "pytest.ini",
    "dispatcher.py",
    "dispatcher_ui.py",
    "admin_ui.py",
    ".gitignore",
    ".env",
    ".env.example",
}
ALLOWED_ROOT_DIRS = {
    "src", "ui", "config", "docs", "tests", "scripts",
    "eventbus", "providers", "plugins", "recipes",
    "protocol", "nvcode_client",
    ".agentbus", ".git", ".venv", "venv", "dist", "build", "__pycache__",
}


class Report:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.warn: list[str] = []
        self.fail: list[str] = []

    def add_ok(self, msg: str) -> None:
        self.ok.append(msg)

    def add_warn(self, msg: str) -> None:
        self.warn.append(msg)

    def add_fail(self, msg: str) -> None:
        self.fail.append(msg)

    @property
    def code(self) -> int:
        return 1 if self.fail else 0


def check_entrypoints(r: Report) -> None:
    for rel in (
        "dispatcher.py",
        "dispatcher_ui.py",
        "src/core/dispatcher_main.py",
        "src/core/runtime.py",
        "src/core/tasks.py",
        "src/core/dedupe.py",
        "src/core/reclaim.py",
        "src/core/verify_policy.py",
        "src/core/feature_flags.py",
        "src/core/task_contract.py",
        "src/core/verification_engine.py",
        "config/feature_flags.yaml",
        "config/feature_presets.yaml",
    ):
        p = ROOT / rel
        if p.is_file():
            r.add_ok(f"entrypoint {rel}")
        else:
            r.add_fail(f"missing {rel}")


def check_root_pollution(r: Report) -> None:
    junk = []
    for p in ROOT.iterdir():
        name = p.name
        if name.startswith(".") and name not in {".gitignore", ".env", ".env.example", ".agentbus", ".git", ".venv"}:
            if name.startswith(".audit") or name.endswith(".tmp"):
                junk.append(name)
            continue
        if p.is_dir():
            if name not in ALLOWED_ROOT_DIRS and not name.startswith("."):
                junk.append(name + "/")
        else:
            if name not in ALLOWED_ROOT and not name.startswith("AgentBus"):
                # allow *.md only if README-like already listed
                if name.endswith((".py", ".bat", ".cmd", ".sh", ".yaml", ".yml")):
                    junk.append(name)
    if junk:
        r.add_warn(f"root extra: {', '.join(sorted(junk)[:20])}")
    else:
        r.add_ok("root layout clean")


def check_duplicate_basenames(r: Report) -> None:
    by_name: dict[str, list[str]] = defaultdict(list)
    if not SRC.is_dir():
        r.add_fail("src/ missing")
        return
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        by_name[p.name].append(str(p.relative_to(ROOT)))
    dups = {k: v for k, v in by_name.items() if len(v) > 1 and k != "__init__.py"}
    if dups:
        for name, paths in sorted(dups.items())[:15]:
            r.add_warn(f"duplicate basename {name}: {paths}")
    else:
        r.add_ok("no duplicate src basenames (excl __init__)")


def check_syntax(r: Report) -> None:
    bad = []
    for base in (SRC, ROOT / "ui", ROOT / "scripts", ROOT / "tests"):
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except SyntaxError as e:
                bad.append(f"{p.relative_to(ROOT)}:{e.lineno} {e.msg}")
    if bad:
        for b in bad[:20]:
            r.add_fail(f"syntax {b}")
    else:
        r.add_ok("python syntax ok (src/ui/scripts/tests)")


def check_legacy_imports(r: Report) -> None:
    pat = re.compile(
        r"^\s*(?:from|import)\s+(" + "|".join(re.escape(x) for x in LEGACY_IMPORTS) + r")\b",
        re.M,
    )
    hits = []
    for base in (SRC, ROOT / "ui", ROOT / "scripts"):
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if "_apply_patches" in text and "def _apply_patches" not in text:
                # call site without definition is bad; definition already removed
                if "runtime_patch" in text:
                    hits.append(f"{p.relative_to(ROOT)}: legacy patch ref")
            m = pat.search(text)
            if m:
                hits.append(f"{p.relative_to(ROOT)}: import {m.group(1)}")
    # dispatcher must not call _apply_patches
    dm = SRC / "core" / "dispatcher_main.py"
    if dm.is_file():
        t = dm.read_text(encoding="utf-8")
        if "_apply_patches" in t:
            hits.append("dispatcher_main.py still references _apply_patches")
    if hits:
        for h in hits[:20]:
            r.add_fail(h)
    else:
        r.add_ok("no legacy patch/supabase imports")


def check_feature_flags_api(r: Report) -> None:
    ff = SRC / "core" / "feature_flags.py"
    if not ff.is_file():
        r.add_fail("feature_flags.py missing")
        return
    text = ff.read_text(encoding="utf-8")
    # only one def set_flag
    n = len(re.findall(r"^def set_flag\b", text, re.M))
    if n != 1:
        r.add_fail(f"feature_flags.set_flag defined {n} times (want 1)")
    else:
        r.add_ok("single set_flag")
    for name in ("list_flags", "apply_preset", "phone_filebus", "remote_filebus"):
        if name not in text:
            r.add_warn(f"feature_flags missing symbol/text: {name}")
    presets = ROOT / "config" / "feature_presets.yaml"
    if presets.is_file() and "beginner_ru" in presets.read_text(encoding="utf-8"):
        r.add_ok("beginner_ru preset present")
    else:
        r.add_warn("beginner_ru not in feature_presets.yaml")


def check_task_sm(r: Report) -> None:
    tasks = SRC / "core" / "tasks.py"
    if not tasks.is_file():
        r.add_fail("tasks.py missing")
        return
    text = tasks.read_text(encoding="utf-8")
    if "TRANSITIONS" not in text or "PROCESSING" not in text:
        r.add_fail("tasks.py missing strict TRANSITIONS/PROCESSING")
    else:
        r.add_ok("task state machine present")
    if "def retry(" not in text:
        r.add_warn("Task.retry missing")


def check_verify_policy(r: Report) -> None:
    vp = SRC / "core" / "verify_policy.py"
    if not vp.is_file():
        r.add_fail("verify_policy.py missing")
        return
    text = vp.read_text(encoding="utf-8")
    if "detect_false_done" not in text:
        r.add_fail("detect_false_done missing")
    else:
        r.add_ok("anti false-DONE present")


def check_docs(r: Report) -> None:
    for rel in (
        "README.md",
        "docs/INDEX.md",
        "docs/STRUCTURE.md",
        "docs/TROUBLESHOOTING.md",
    ):
        if (ROOT / rel).is_file():
            r.add_ok(f"doc {rel}")
        else:
            r.add_fail(f"missing {rel}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").is_file() else ""
    if "desktop_queue" not in readme:
        r.add_warn("README missing desktop_queue flow")
    if "phone_filebus" not in readme and "телефон" not in readme.lower():
        r.add_warn("README should mention optional phone bus")


def run_pytest(r: Report) -> None:
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC), str(ROOT), env.get("PYTHONPATH", "")]
    )
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        tail = (p.stdout or "")[-500:]
        if p.returncode == 0:
            r.add_ok(f"pytest ok: {tail.strip().splitlines()[-1] if tail.strip() else 'passed'}")
        else:
            r.add_fail(f"pytest exit {p.returncode}: {tail.strip()[-300:]}")
    except Exception as e:
        r.add_warn(f"pytest not run: {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AgentBus project audit")
    ap.add_argument("--pytest", action="store_true", help="also run pytest -q")
    args = ap.parse_args(argv)
    r = Report()
    check_entrypoints(r)
    check_root_pollution(r)
    check_duplicate_basenames(r)
    check_syntax(r)
    check_legacy_imports(r)
    check_feature_flags_api(r)
    check_task_sm(r)
    check_verify_policy(r)
    check_docs(r)
    if args.pytest:
        run_pytest(r)

    print("=== AgentBus project audit ===")
    for x in r.ok:
        print(f"  OK   {x}")
    for x in r.warn:
        print(f"  WARN {x}")
    for x in r.fail:
        print(f"  FAIL {x}")
    print(
        f"--- summary: {len(r.ok)} ok, {len(r.warn)} warn, {len(r.fail)} fail ---"
    )
    if r.code == 0:
        print("READY")
    else:
        print("NOT READY")
    return r.code


if __name__ == "__main__":
    raise SystemExit(main())
