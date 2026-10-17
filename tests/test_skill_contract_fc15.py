# -*- coding: utf-8 -*-
"""FC-15: Skill kwargs contract + SkillResult normalization."""
from __future__ import annotations

from skills.skills import build_skill_kwargs, SKILLS_NEED_MESSAGE, SKILLS_WITH_FILES
from core.task_result import SkillResult, build_task_result


def test_build_kwargs_rename_includes_message_and_path():
    kw = build_skill_kwargs(
        "rename_symbol",
        path="/proj",
        message="переименуй old_fn в new_fn",
        files=["a.py"],
    )
    assert kw.get("path") == "/proj"
    assert kw.get("message")
    assert "a.py" in (kw.get("files") or [])


def test_build_kwargs_format_no_message_required():
    kw = build_skill_kwargs("format_code", path="/p", message="format all", files=["x.py"])
    assert kw.get("path") == "/p"
    assert "message" not in kw  # format doesn't need message in SKILLS_NEED_MESSAGE


def test_search_symbol_pattern():
    kw = build_skill_kwargs("search_symbol", path="/p", message="найди foo_bar")
    assert kw.get("pattern") == "foo_bar"


def test_skill_result_from_execute_success():
    raw = {"success": True, "result": {"fixed": 2, "files": ["a.py", "b.py"]}}
    sr = SkillResult.from_execute("cleanup_imports", raw)
    assert sr.success
    assert sr.changed
    assert "a.py" in sr.files
    d = sr.to_dict()
    assert d["ok"] is True
    assert d["files_changed"] == d["files"]


def test_skill_result_failure():
    sr = SkillResult.from_execute("rename_symbol", {"success": False, "error": "parse failed"})
    assert not sr.success
    assert "parse" in sr.error


def test_task_result_with_skill_fields():
    row = {
        "id": "t-sk",
        "message": "format",
        "status": "DONE",
        "result": {
            "ok": True,
            "method": "skill",
            "skill": "format_code",
            "skill_result": {
                "success": True,
                "name": "format_code",
                "files": ["x.py"],
                "message": "formatted",
            },
            "files_changed": ["x.py"],
        },
    }
    tr = build_task_result(row)
    assert tr.ok
    assert tr.skill == "format_code"
    assert "x.py" in tr.changes.files

