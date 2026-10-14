# -*- coding: utf-8 -*-
"""Offline: security intake fail-closed on Task.from_dict / normalize_task."""
from __future__ import annotations

import pytest

from safety.security import SecurityError, validate_path, validate_paths, validate_command
from core.task_contract import TaskContractError, normalize_task, is_valid_task
from core.tasks import Task


def test_validate_path_blocks_traversal():
    with pytest.raises(SecurityError):
        validate_path("../etc/passwd")
    with pytest.raises(SecurityError):
        validate_path("/abs/path")


def test_validate_command_blocks_rm():
    with pytest.raises(SecurityError):
        validate_command("rm -rf /")


def test_normalize_task_rejects_traversal():
    with pytest.raises(TaskContractError):
        normalize_task(
            {
                "id": "t1",
                "message": "fix me",
                "files": ["../../secret"],
            }
        )


def test_from_dict_security_always_on():
    with pytest.raises(SecurityError):
        Task.from_dict(
            {
                "id": "t2",
                "message": "ok",
                "files": ["foo/../../../etc/passwd"],
            },
            strict=False,
        )


def test_from_dict_rejects_unsafe_task_id_even_when_not_strict():
    with pytest.raises(SecurityError):
        Task.from_dict(
            {"id": "../../escaped", "message": "hello"},
            strict=False,
        )


def test_from_dict_ok_relative():
    t = Task.from_dict(
        {"id": "t3", "message": "hello", "files": ["src/main.py"]},
        strict=False,
    )
    assert t.id == "t3"
    assert t.files == ["src/main.py"]


def test_is_valid_task_false_on_bad_path():
    assert not is_valid_task({"id": "x", "message": "m", "files": ["../x"]})
