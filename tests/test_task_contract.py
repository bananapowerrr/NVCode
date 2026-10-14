# -*- coding: utf-8 -*-
"""Task Contract + DONE gate."""
from __future__ import annotations

import pytest

from core.task_contract import normalize_task, is_valid_task, TaskContractError
from core.verification_engine import VerificationReport, CheckResult, gate_done
from core.tasks import Task


def test_normalize_minimal():
    t = normalize_task({"id": "a1", "message": "hello"})
    assert t["id"] == "a1"
    assert t["status"] == "PENDING"
    assert t["channel"] == "desktop"
    assert t["files"] == []


def test_task_id_must_be_a_safe_filename_token():
    assert normalize_task({"id": "plan-2_step.a1", "message": "hello"})["id"] == "plan-2_step.a1"
    for unsafe_id in ("../outside", "a/b", "/absolute", "C:\\outside", ".."):
        with pytest.raises(TaskContractError, match="id"):
            normalize_task({"id": unsafe_id, "message": "hello"})


def test_reject_empty_message():
    with pytest.raises(TaskContractError):
        normalize_task({"id": "a1", "message": "  "})


def test_reject_bad_status():
    with pytest.raises(TaskContractError):
        normalize_task({"id": "a1", "message": "x", "status": "RUNNING"})


def test_files_coerce():
    t = normalize_task({"id": "a1", "message": "x", "files": ["a.py", "", "b.py"]})
    assert t["files"] == ["a.py", "b.py"]


def test_is_valid():
    assert is_valid_task({"id": "1", "message": "ok"})
    assert not is_valid_task({"id": "1"})


def test_task_from_dict_strict():
    with pytest.raises(Exception):
        Task.from_dict({"id": "1", "message": ""}, strict=True)


def test_gate_done_requires_both():
    ok, reason = gate_done(True, VerificationReport(passed=True, reason="all_passed"))
    assert ok and reason == "ok"
    ok, reason = gate_done(False, VerificationReport(passed=True))
    assert not ok and reason == "execution_failed"
    ok, reason = gate_done(True, VerificationReport(passed=False, reason="tests"))
    assert not ok
    ok, reason = gate_done(True, None)
    assert not ok and reason == "verification_missing"


def test_gate_short_circuit():
    ok, reason = gate_done(False, None, short_circuit="cache_hit")
    assert ok and "cache_hit" in reason
    ok, reason = gate_done(False, None, short_circuit="skill_success")
    assert ok


def test_report_to_dict():
    r = VerificationReport(
        passed=False,
        checks=[CheckResult("syntax", False, detail="boom")],
        reason="syntax failed",
    )
    d = r.to_dict()
    assert d["passed"] is False
    assert d.get("check_map", {}).get("syntax") == "failed"
    assert isinstance(d["checks"], list)
    assert d["checks"][0]["name"] == "syntax"
    assert d["checks"][0]["status"] == "failed"
