# -*- coding: utf-8 -*
"""TD-027 C: Executor does not create empty target stubs."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.executor import Executor


def test_ensure_target_files_reports_missing(tmp_path: Path) -> None:
    missing = Executor._ensure_target_files(str(tmp_path), ["a.py", "sub/b.py"])
    assert set(missing) == {"a.py", "sub/b.py"}
    assert not (tmp_path / "a.py").exists()
    assert not (tmp_path / "sub" / "b.py").exists()


def test_ensure_target_files_does_not_touch_existing(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    assert Executor._ensure_target_files(str(tmp_path), ["keep.py"]) == []
    assert (tmp_path / "keep.py").read_text(encoding="utf-8") == "x"


def test_ensure_target_files_escapes_rejected(tmp_path: Path) -> None:
    assert Executor._ensure_target_files(str(tmp_path), ["../evil.py"]) == []
    assert not (tmp_path / "evil.py").exists()


def test_ensure_target_files_empty_inputs(tmp_path: Path) -> None:
    assert Executor._ensure_target_files(str(tmp_path), []) == []
    assert Executor._ensure_target_files("", ["a.py"]) == []
    assert Executor._ensure_target_files(str(tmp_path), ["", "   "]) == []


def test_run_fails_when_all_targets_missing(tmp_path: Path) -> None:
    ex = Executor()
    worker = SimpleNamespace(
        name="noop", harness="cli", provider="local", model="",
        enabled=True, cmd=["true"],
    )
    res = ex.run(worker, str(tmp_path), "hi", timeout=5, files=["nope.py"])
    assert res.ok is False
    assert "TD-027" in (res.stderr or "") or "missing" in (res.stderr or "").lower()
