# -*- coding: utf-8 -*-
"""recover_deferred: wake_epoch + mtime fallback."""
from __future__ import annotations
import json
import time
from pathlib import Path

import pytest


def _rt():
    from core.runtime import Runtime
    from _helpers import sync_config_snapshots
    sync_config_snapshots(Runtime)
    return Runtime


def _write_deferred(bus_root: Path, channel: str, tid: str, result: dict) -> Path:
    ddir = bus_root / "channels" / channel / "deferred"
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / f"{tid}.json"
    payload = {"id": tid, "channel": channel, "message": "x", "result": result}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_recover_by_wake_epoch(tmp_path, monkeypatch):
    from core.bus import FileBus
    import core.config as cfg
    monkeypatch.setattr(cfg, "BUS_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "CHANNELS", ("gpt",))
    monkeypatch.setattr(cfg, "RETRY_DELAY_SECONDS", 3600)
    bus = FileBus(tmp_path, ("gpt",))
    bus.ensure()
    _write_deferred(tmp_path, "gpt", "t1", {"wake_epoch": time.time() - 10, "error": "x"})
    _write_deferred(tmp_path, "gpt", "t2", {"wake_epoch": time.time() + 3600, "error": "x"})
    Runtime = _rt()
    rt = Runtime.__new__(Runtime)
    rt.bus = bus
    rt.worker_id = "test"
    rt._emit = lambda *a, **k: None
    assert Runtime._recover_deferred(rt) == 1
    assert (tmp_path / "channels" / "gpt" / "incoming" / "t1.json").is_file()
    assert (tmp_path / "channels" / "gpt" / "deferred" / "t2.json").is_file()


def test_recover_mtime_fallback(tmp_path, monkeypatch):
    from core.bus import FileBus
    import core.config as cfg
    import os
    monkeypatch.setattr(cfg, "BUS_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "CHANNELS", ("gpt",))
    monkeypatch.setattr(cfg, "RETRY_DELAY_SECONDS", 1)
    bus = FileBus(tmp_path, ("gpt",))
    bus.ensure()
    path = _write_deferred(tmp_path, "gpt", "old", {"error": "no wake_epoch"})
    old = time.time() - 10
    os.utime(path, (old, old))
    Runtime = _rt()
    rt = Runtime.__new__(Runtime)
    rt.bus = bus
    rt.worker_id = "test"
    rt._emit = lambda *a, **k: None
    assert Runtime._recover_deferred(rt) == 1
    assert (tmp_path / "channels" / "gpt" / "incoming" / "old.json").is_file()


def test_deferred_capacity_writes_wake_epoch(tmp_path, monkeypatch):
    from core.bus import FileBus
    from core.tasks import Task
    import core.config as cfg
    monkeypatch.setattr(cfg, "BUS_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "CHANNELS", ("gpt",))
    bus = FileBus(tmp_path, ("gpt",))
    bus.ensure()
    pdir = tmp_path / "channels" / "gpt" / "processing"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "tx.json").write_text("{}", encoding="utf-8")
    Runtime = _rt()
    rt = Runtime.__new__(Runtime)
    rt.bus = bus
    rt.worker_id = "test"
    rt._backoff = {}
    rt._emit = lambda *a, **k: None

    class Cap:
        def deferred_snapshot(self):
            return {"deferred": True, "wake_at": 90}

    rt.capacity = Cap()
    task = Task.from_dict({"id": "tx", "channel": "gpt", "message": "m", "attempts": 1})
    assert Runtime._deferred_capacity(rt, task, 2) is True
    data = json.loads((tmp_path / "channels" / "gpt" / "deferred" / "tx.json").read_text(encoding="utf-8"))
    result = data.get("result") or {}
    assert result.get("wake_at") == 90
    assert isinstance(result.get("wake_epoch"), (int, float))
    assert result["wake_epoch"] > time.time()
