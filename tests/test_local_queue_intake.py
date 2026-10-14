# -*- coding: utf-8 -*-
"""LocalQueue must reject unsafe paths via intake (fail-closed)."""
from __future__ import annotations

import pytest
import json
from pathlib import Path


def test_put_accepts_safe(tmp_path):
    from core.local_queue import LocalQueue

    q = LocalQueue(spill_dir=tmp_path / "spill")
    tid = q.put({"message": "format a.py", "files": ["src/a.py"]})
    assert tid
    task = q.claim()
    assert task is not None
    assert task["message"].startswith("format")
    assert task["metadata"].get("intake_source") == "desktop_queue" or task["metadata"].get("source")


def test_put_rejects_traversal(tmp_path):
    from core.local_queue import LocalQueue

    q = LocalQueue(spill_dir=tmp_path / "spill")
    with pytest.raises(ValueError, match="reject"):
        q.put({"message": "leak", "files": ["../../etc/passwd"]})
    assert q.size() == 0


def test_put_rejects_task_id_path_traversal(tmp_path):
    from core.local_queue import LocalQueue

    spill = tmp_path / "spill"
    q = LocalQueue(spill_dir=spill)
    with pytest.raises(ValueError, match="id"):
        q.put({"id": "../escaped", "message": "hello"})
    assert q.size() == 0
    assert not (tmp_path / "escaped.json").exists()


def test_put_rejects_invalid_task_status(tmp_path):
    from core.local_queue import LocalQueue

    q = LocalQueue(spill_dir=tmp_path / "spill")
    with pytest.raises(ValueError, match="status"):
        q.put({"id": "bad-status", "message": "hello", "status": "RUNNING"})
    assert q.size() == 0


def test_put_persists_complete_json_atomically(tmp_path):
    from core.local_queue import LocalQueue

    spill = tmp_path / "spill"
    q = LocalQueue(spill_dir=spill)
    tid = q.put({"id": "atomic-task", "message": "hello"})

    persisted = json.loads((spill / f"{tid}.json").read_text(encoding="utf-8"))
    assert persisted["id"] == "atomic-task"
    assert persisted["message"] == "hello"
    assert list(spill.glob("*.tmp")) == []


def test_put_fails_closed_when_persistence_fails(tmp_path, monkeypatch):
    from core.local_queue import LocalQueue

    spill = tmp_path / "spill"
    q = LocalQueue(spill_dir=spill)

    def fail_replace(_src, _dst):
        raise OSError("disk unavailable")

    monkeypatch.setattr("core.local_queue.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        q.put({"id": "not-persisted", "message": "hello"})
    assert q.size() == 0
    assert not (spill / "not-persisted.json").exists()
    assert list(spill.glob("*.tmp")) == []


def test_enqueue_rejects_traversal(tmp_path, monkeypatch):
    from core import local_queue as lq
    from core.local_queue import LocalQueue, enqueue_desktop_task

    lq._GLOBAL = LocalQueue(spill_dir=tmp_path / "spill")
    with pytest.raises(ValueError, match="reject"):
        enqueue_desktop_task("x", files=["../secret"], root=tmp_path)
