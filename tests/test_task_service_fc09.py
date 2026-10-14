# -*- coding: utf-8 -*-
"""FC-09: TaskService is the canonical desktop intake."""
from __future__ import annotations

import json
from pathlib import Path


def _reset_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBUS_ROOT", str(tmp_path))
    import core.local_queue as lq
    monkeypatch.setattr(lq, "_GLOBAL", None, raising=False)
    # force re-init
    if hasattr(lq, "_GLOBAL"):
        lq._GLOBAL = None


def test_submit_payload_to_desktop_queue(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    from core.task_service import submit_payload
    from core.local_queue import get_local_queue

    tid, err = submit_payload(
        {
            "message": "format code",
            "project": "demo",
            "files": [],
            "metadata": {"source": "test"},
        },
        source="test",
        root=tmp_path,
        soft_intake=True,
    )
    assert err is None, err
    assert tid
    item = get_local_queue(tmp_path).claim()
    assert item is not None
    assert item.get("channel") == "desktop"
    assert "format" in (item.get("message") or "")


def test_default_submit_rejects_invalid_status_without_queueing(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    from core.task_service import submit_payload
    from core.local_queue import get_local_queue

    tid, err = submit_payload(
        {"id": "bad-status", "message": "hello", "status": "RUNNING"},
        root=tmp_path,
    )
    assert tid is None
    assert err and "status" in err
    assert get_local_queue(tmp_path).size() == 0


def test_default_submit_rejects_malformed_metadata_without_queueing(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    from core.task_service import submit_payload
    from core.local_queue import get_local_queue

    tid, err = submit_payload(
        {"id": "bad-metadata", "message": "hello", "metadata": ["not", "an object"]},
        root=tmp_path,
    )
    assert tid is None
    assert err and "metadata" in err
    assert get_local_queue(tmp_path).size() == 0


def test_resubmit_from_row(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    from core.task_service import resubmit_from_row
    from core.local_queue import get_local_queue

    tid, err = resubmit_from_row(
        {"id": "old1", "message": "retry me", "project": "demo", "files": ["a.py"]},
        root=tmp_path,
    )
    assert err is None, err
    assert tid
    item = get_local_queue(tmp_path).claim()
    assert item is not None
    assert item["message"] == "retry me"
    assert (item.get("metadata") or {}).get("resent_from") == "old1"


def test_emit_recipe_uses_desktop(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "01_refactor.json").write_text(
        json.dumps({"message": "refactor target", "files": [], "metadata": {"recipe": "refactor"}}),
        encoding="utf-8",
    )
    import cli.recipes as R
    monkeypatch.setattr(R, "_root", lambda: tmp_path)
    path = R.emit_recipe("refactor", project="demo", root=tmp_path)
    assert path is not None
    from core.local_queue import get_local_queue
    item = get_local_queue(tmp_path).claim()
    assert item is not None
    assert item.get("message")


def test_empty_message_rejected(tmp_path, monkeypatch):
    _reset_queue(tmp_path, monkeypatch)
    from core.task_service import submit
    tid, err = submit("", project="p", root=tmp_path)
    assert tid is None
    assert err
