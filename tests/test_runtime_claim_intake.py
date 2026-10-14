from __future__ import annotations

import json


class _Log:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(str(message))


def _runtime_ops(bus, monkeypatch):
    import core.runtime_ops as runtime_ops_module
    from core.runtime_ops import RuntimeOps

    monkeypatch.setattr(runtime_ops_module, "CHANNELS", ("gpt",))
    ops = RuntimeOps.__new__(RuntimeOps)
    ops.bus = bus
    ops.log = _Log()
    return ops


def test_filebus_claim_quarantines_invalid_task_and_continues(tmp_path, monkeypatch):
    from core.bus import FileBus

    bus = FileBus(tmp_path, channels=("gpt", "desktop"))
    bus.ensure()
    incoming = bus.paths("gpt")["incoming"]
    (incoming / "bad.json").write_text(
        json.dumps({"id": "bad-state", "message": "bad", "status": "NOT_A_STATE"}),
        encoding="utf-8",
    )
    (incoming / "good.json").write_text(
        json.dumps({"id": "good-task", "message": "valid task", "status": "PENDING"}),
        encoding="utf-8",
    )
    ops = _runtime_ops(bus, monkeypatch)

    # Candidate ordering is scheduler-owned: visit both entries and verify
    # that exactly the valid one is claimed while the bad one is quarantined.
    claimed_rows = [
        row for row in (
            ops._claim_file_task(only_channels=("gpt",)),
            ops._claim_file_task(only_channels=("gpt",)),
        ) if row is not None
    ]
    assert len(claimed_rows) == 1 and claimed_rows[0]["id"] == "good-task"
    assert (bus.paths("gpt")["errors"] / "bad.json").is_file()
    assert not (incoming / "bad.json").exists()
    assert any("intake reject bad.json" in m for m in ops.log.messages)


def test_filebus_claim_rejects_missing_id_without_blocking_later_task(tmp_path, monkeypatch):
    from core.bus import FileBus

    bus = FileBus(tmp_path, channels=("gpt", "desktop"))
    bus.ensure()
    incoming = bus.paths("gpt")["incoming"]
    (incoming / "missing-id.json").write_text(
        json.dumps({"message": "missing id", "status": "PENDING"}),
        encoding="utf-8",
    )
    (incoming / "good.json").write_text(
        json.dumps({"id": "good-task", "message": "valid task", "status": "PENDING"}),
        encoding="utf-8",
    )
    ops = _runtime_ops(bus, monkeypatch)

    claimed_rows = [
        row for row in (
            ops._claim_file_task(only_channels=("gpt",)),
            ops._claim_file_task(only_channels=("gpt",)),
        ) if row is not None
    ]
    assert len(claimed_rows) == 1 and claimed_rows[0]["id"] == "good-task"
    assert (bus.paths("gpt")["errors"] / "missing-id.json").is_file()


def test_filebus_claim_never_revives_done_task(tmp_path, monkeypatch):
    from core.bus import FileBus

    bus = FileBus(tmp_path, channels=("gpt", "desktop"))
    bus.ensure()
    incoming = bus.paths("gpt")["incoming"]
    (incoming / "done.json").write_text(
        json.dumps({"id": "done-task", "message": "already finished", "status": "DONE"}),
        encoding="utf-8",
    )
    ops = _runtime_ops(bus, monkeypatch)

    assert ops._claim_file_task(only_channels=("gpt",)) is None
    assert (bus.paths("gpt")["errors"] / "done.json").is_file()
    assert not (bus.paths("gpt")["processing"] / "done.json").exists()
