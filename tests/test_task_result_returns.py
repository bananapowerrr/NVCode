# -*- coding: utf-8 -*-
"""TD-033 regression: task_result._tr_* helpers must return values.

All six _tr_* helpers ended without a return (returned None), so
build_task_result crashed on tuple unpacking and every caller silently
degraded to fallbacks. These tests pin the restored returns.
"""
import sys
from pathlib import Path

try:
    from core.task_result import _tr_changes, build_task_result
except ImportError:  # pragma: no cover - direct script run
    _root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_root / "src"))
    sys.path.insert(0, str(_root))
    from core.task_result import _tr_changes, build_task_result


def test_build_task_result_done_row() -> None:
    row = {
        "id": "t1",
        "status": "DONE",
        "result": {"ok": True, "summary": "готово", "files": ["a.py", "b.py"]},
        "metadata": {"worker": "w1"},
    }
    tr = build_task_result(row)
    assert tr.task_id == "t1"
    assert tr.ok is True
    assert tr.status == "DONE"
    assert tr.summary == "готово"
    assert tr.changes.files == ["a.py", "b.py"]
    assert tr.worker == "w1"


def test_build_task_result_timeout_beats_done() -> None:
    row = {
        "id": "t2",
        "status": "DONE",
        "result": {"ok": True, "error": "timeout"},
    }
    tr = build_task_result(row)
    assert tr.ok is False
    assert tr.status == "ERROR"
    assert tr.error == "timeout"


def test_tr_changes_counts_line_delta() -> None:
    result = {
        "file_changes": [
            {"file": "x.py", "new": "a\nb\nc", "original": "a"},
            {"file": "y.py", "new": "a", "original": "a\nb"},
        ]
    }
    cs = _tr_changes(result, {})
    assert cs.files == ["x.py", "y.py"]
    assert cs.insertions == 2
    assert cs.deletions == 1
