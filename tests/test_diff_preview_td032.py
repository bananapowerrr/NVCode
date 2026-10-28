"""TD-032 regression: files_payload читается ДО подсчёта _diff_lines.

До фикса `files_payload` в `_llm_success_diff_preview` использовался до
определения (реально он собирался только внутри `_llm_capture_diff_payload`)
— NameError молча ловился, `assess_risk` всегда получала `diff_lines=0`,
и риск-оценка diff_preview видела пустой диф.
"""
from __future__ import annotations

from unittest.mock import patch

from core.rp_llm import RPLlmMixin


class _Snap:
    head = "deadbeef"


class _GitOps:
    def __init__(self, content):
        self._content = content

    def changed_files_since(self, snapshot):
        return list(self._content.keys())

    def read_working(self, rel):
        return self._content[rel]

    def show_at_head(self, rel, sha):
        return "old"


class _Decision:
    action = "auto"

    def to_dict(self):
        return {"action": "auto", "risk": "LOW", "reasons": []}


class _Task:
    def __init__(self):
        self.id = "td-032"
        self.files = ["a.py"]
        self.complexity = 3
        self.message = "задача"
        self.metadata = {}
        self.raw = {}


def _preview(mixin, task, gitops, snapshot):
    captured = {}

    def fake_assess_risk(*, files, complexity, diff_lines, message):
        captured["diff_lines"] = diff_lines
        return _Decision()

    with (
        patch("core.rp_llm.is_enabled", lambda name, default=True: True),
        patch("safety.diff_policy.assess_risk", fake_assess_risk),
        patch("safety.diff_engine.queue_from_task_result",
              lambda payload, project_root=None: "td-032"),
        patch("safety.diff_engine.queue_pending"),
    ):
        mixin._llm_success_diff_preview(
            task, proj=None, gitops=gitops, before_snapshot=snapshot)
    return captured


def test_td032_diff_lines_counted_from_real_payload():
    mixin = RPLlmMixin()
    task = _Task()
    captured = _preview(
        mixin, task, _GitOps({"a.py": "l1\nl2\nl3\n"}), _Snap())
    assert captured["diff_lines"] == 3
    assert task.metadata["diff_policy"]["action"] == "auto"


def test_td032_without_gitops_zero_lines_and_no_crash():
    mixin = RPLlmMixin()
    task = _Task()
    captured = _preview(mixin, task, None, None)
    assert captured["diff_lines"] == 0


def test_td032_read_diff_payload_restricted_to_task_files():
    mixin = RPLlmMixin()
    task = _Task()
    fp = mixin._llm_read_diff_payload(
        _GitOps({"a.py": "x\n", "b.py": "y\n"}), _Snap(), task, None)
    assert set(fp) == {"a.py"}
    assert fp["a.py"] == {"original": "old", "new": "x\n"}
