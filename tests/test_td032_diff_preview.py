# -*- coding: utf-8 -*-
"""TD-032: _llm_success_diff_preview must pass real diff_lines to assess_risk."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def mixin():
    from core.rp_llm import RPLlmMixin

    class _T(RPLlmMixin):
        def __init__(self):
            self.log = SimpleNamespace(write=lambda *a, **k: None)

    return _T()


def test_diff_preview_passes_nonzero_diff_lines(mixin):
    """files_payload from capture must drive assess_risk(diff_lines=...)."""
    task = SimpleNamespace(
        id="t-td032",
        files=["a.py"],
        complexity=3,
        message="fix",
        metadata={},
        raw={},
    )
    proj = SimpleNamespace(root="/tmp/proj")
    gitops = SimpleNamespace(
        changed_files_since=lambda snap: ["a.py"],
        read_working=lambda rel: "line1\nline2\nline3\nline4\n",
        show_at_head=lambda rel, sha: "line1\n",
    )
    before = SimpleNamespace(head="abc")

    seen = {}

    def fake_assess(*, files, complexity, diff_lines, message):
        seen["diff_lines"] = diff_lines
        return SimpleNamespace(
            action="allow",
            to_dict=lambda: {"action": "allow", "diff_lines": diff_lines},
        )

    with patch("core.rp_llm.is_enabled", return_value=True), \
         patch("safety.diff_engine.queue_from_task_result", return_value=False), \
         patch("safety.diff_engine.queue_pending", return_value=None), \
         patch("safety.diff_policy.assess_risk", side_effect=fake_assess):
        mixin._llm_success_diff_preview(task, proj, gitops, before)

    assert "diff_lines" in seen, "assess_risk was not called"
    assert seen["diff_lines"] > 0, f"TD-032 regression: diff_lines still 0, got {seen}"
    assert task.metadata.get("diff_policy", {}).get("diff_lines") == seen["diff_lines"]


def test_diff_preview_no_nameerror_without_gitops(mixin):
    """Without gitops/capture, assess_risk still runs with diff_lines=0 (no crash)."""
    task = SimpleNamespace(
        id="t-empty",
        files=[],
        complexity=2,
        message="",
        metadata={},
        raw={},
    )
    seen = {}

    def fake_assess(**kw):
        seen.update(kw)
        return SimpleNamespace(action="allow", to_dict=lambda: {"action": "allow"})

    with patch("core.rp_llm.is_enabled", return_value=True), \
         patch("safety.diff_engine.queue_from_task_result", return_value=False), \
         patch("safety.diff_policy.assess_risk", side_effect=fake_assess):
        mixin._llm_success_diff_preview(task, None, None, None)

    assert seen.get("diff_lines") == 0
