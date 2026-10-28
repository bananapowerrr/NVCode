#!/usr/bin/env python3
"""Apply TD-021 (desktop-only channels) + TD-027 C (no empty stubs). Idempotent."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_runtime_ops() -> None:
    path = ROOT / "src" / "core" / "runtime_ops.py"
    text = path.read_text(encoding="utf-8")
    if "Phone file-bus channels (gpt/grok/gemini" in text and "for ch in list(CHANNELS)" not in text:
        print("runtime_ops: already TD-021")
        return
    idx = text.find("def _active_channels(self)")
    if idx < 0:
        print("ABORT: _active_channels not found")
        sys.exit(1)
    end = text.find("\n    def ", idx + 10)
    if end < 0:
        print("ABORT: next method not found")
        sys.exit(1)
    new = '''def _active_channels(self) -> list[str]:
        """Desktop-only intake (TD-021).

        Phone file-bus channels (gpt/grok/gemini/…) are no longer polled.
        Tasks enter only via IDE/chat (desktop queue + Protocol). Isolated
        sub-agent dirs under desktop (`desktop__sub_*`) remain claimable.
        """
        found: list[str] = ["desktop"]
        seen: set[str] = {"desktop"}
        try:
            bus_root = Path(getattr(self.bus, "root", None) or "")
            root = bus_root / "channels" if bus_root.is_dir() else Path()
            if not root.is_dir():
                from core.config import CHANNELS_ROOT
                root = Path(CHANNELS_ROOT)
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if not d.is_dir():
                        continue
                    name = d.name
                    if name in seen:
                        continue
                    # only desktop-scoped sub-agents, not legacy phone channels
                    if name.startswith("desktop__sub_") or (
                        name.startswith("desktop") and "__sub_" in name
                    ):
                        found.append(name)
                        seen.add(name)
        except Exception:
            pass
        return found
'''
    text = text[:idx] + new.rstrip() + "\n" + text[end + 1 :]
    path.write_text(text, encoding="utf-8", newline="")
    print("runtime_ops: TD-021 applied")


def patch_executor() -> None:
    path = ROOT / "src" / "core" / "executor.py"
    text = path.read_text(encoding="utf-8")
    if "TD-027 (C):" in text:
        print("executor: already TD-027")
        return
    pat = re.compile(
        r"    @staticmethod\n"
        r"    def _ensure_target_files\(project: str, files: list\[str\]\) -> list\[str\]:.*?"
        r"(?=\n    def run\()",
        re.S,
    )
    new_method = '''    @staticmethod
    def _ensure_target_files(project: str, files: list[str]) -> list[str]:
        """TD-027 (C): не создавать пустые заглушки.

        Returns list of relative paths that are missing (not created).
        """
        missing: list[str] = []
        if not project or not files:
            return missing
        root = Path(project)
        try:
            root_resolved = root.resolve()
        except OSError:
            return missing
        for rel in files:
            try:
                name = str(rel or "").strip()
                if not name:
                    continue
                target = (root / name).resolve()
                try:
                    target.relative_to(root_resolved)
                except ValueError:
                    continue
                if target.is_file():
                    continue
                missing.append(name)
            except OSError:
                missing.append(str(rel or "").strip() or "?")
        return missing

'''
    text2, n = pat.subn(new_method, text, count=1)
    if n != 1:
        print("ABORT: _ensure_target_files not replaced", n)
        sys.exit(1)
    text = text2
    old_run = """        files = files or []
        self._ensure_target_files(project, files)
        # Native-first when profile says so
"""
    new_run = """        files = files or []
        missing = self._ensure_target_files(project, files)
        if missing and files:
            existing = []
            root = Path(project) if project else None
            for rel in files:
                try:
                    if root and (root / str(rel)).is_file():
                        existing.append(rel)
                except OSError:
                    pass
            if not existing:
                return ExecutionResult(
                    ok=False,
                    stdout="",
                    stderr=(
                        "TD-027: target files missing (empty stubs disabled): "
                        + ", ".join(missing[:12])
                    ),
                )
        # Native-first when profile says so
"""
    if old_run not in text:
        old_c = old_run.replace("\n", "\r\n")
        new_c = new_run.replace("\n", "\r\n")
        if old_c not in text:
            print("ABORT: run() hook not found")
            sys.exit(1)
        text = text.replace(old_c, new_c, 1)
    else:
        text = text.replace(old_run, new_run, 1)
    path.write_text(text, encoding="utf-8", newline="")
    print("executor: TD-027 applied")


def main() -> int:
    patch_runtime_ops()
    patch_executor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
