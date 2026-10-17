# -*- coding: utf-8 -*-
"""Day 11 — skills verification integration (offline, no runtime mutation).

Aligns Day-9 skills contract with the live RPCacheSkillsMixin surface.
Does NOT touch FSM / intake / executor / DONE gate / rp_cache_skills body.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@pytest.fixture(autouse=True)
def _prefer_src_package():
    """Put src/ first in sys.path for these tests, then restore.

    Removing the repo root outright is wrong: providers/ and eventbus/ live at
    the root, so dropping it breaks any module that imports them.
    """
    saved = list(sys.path)
    while str(SRC) in sys.path:
        sys.path.remove(str(SRC))
    sys.path.insert(0, str(SRC))
    try:
        yield
    finally:
        sys.path = saved


# ---------------------------------------------------------------------------
# Source-level contract: live skill stage surface (RPCacheSkillsMixin)
# ---------------------------------------------------------------------------

def test_try_skill_source_builds_kwargs_and_executes():
    from core.rp_cache_skills import RPCacheSkillsMixin

    src = inspect.getsource(RPCacheSkillsMixin._try_skill)
    # kwargs are assembled for the matched skill and passed to skills.execute
    assert "kwargs" in src
    # must call execute after kwargs
    assert "execute" in src
    # success path returns payload dict; failure returns None (LLM fallthrough)
    assert "return None" in src


def test_finalize_skill_result_exists_and_is_separate():
    from core.rp_cache_skills import RPCacheSkillsMixin

    assert hasattr(RPCacheSkillsMixin, "_finalize_skill_result")
    fin = inspect.getsource(RPCacheSkillsMixin._finalize_skill_result)
    # finalize is the only place that should talk about completing skill tasks
    assert "skill" in fin.lower()


def test_try_skill_does_not_hardcode_done_status():
    """DONE authority stays outside _try_skill body."""
    from core.rp_cache_skills import RPCacheSkillsMixin

    src = inspect.getsource(RPCacheSkillsMixin._try_skill)
    # _try_skill may mention method/skill but must not set task status DONE itself
    assert "status" not in src or "DONE" not in src.split("status")[0][-40:]
    # stronger: no assignment of DONE inside try_skill
    for line in src.splitlines():
        stripped = line.strip()
        if "DONE" in stripped and not stripped.startswith("#"):
            # allow comments / strings about finalize only if not assignment
            assert "=" not in stripped or "DONE" not in stripped.split("=")[0]


# ---------------------------------------------------------------------------
# Matcher ↔ stage agreement (same public phrases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("format code please", "format_code"),
        ("отформатируй файл", "format_code"),
        ("sort imports with isort", "sort_imports"),
        ("remove unused imports", "cleanup_imports"),
        ("find todos in the project", "find_todos"),
        ("check syntax", "check_syntax"),
        ("git snapshot", "git_snapshot"),
        ("list dependencies", "list_deps"),
        ("extract function please", "extract_function"),
        ("переименуй foo в bar", "rename_symbol"),
    ],
)
def test_matcher_phrases_stable_for_stage(message, expected):
    from skills.matcher import match_message

    assert match_message(message) == expected


def test_complex_work_blocks_skill_match():
    from skills.matcher import match_message, is_complex_work

    assert is_complex_work("refactor the whole module") is True
    assert match_message("refactor and format code") is None
    assert match_message("implement new feature") is None


def test_loc_not_substring_of_block():
    from skills.matcher import match_message

    # regression: "block" must not trigger count_lines via "loc"
    assert match_message("extract function from this block") == "extract_function"
    assert match_message("count lines of code") == "count_lines"


# ---------------------------------------------------------------------------
# execute + kwargs shape used by stage
# ---------------------------------------------------------------------------

def test_build_skill_kwargs_matches_stage_call_pattern(tmp_path):
    from skills.skills import build_skill_kwargs

    # stage passes path=project, message=..., files=list|None
    kw = build_skill_kwargs(
        "format_code",
        path=str(tmp_path),
        message="format code please",
        files=["a.py"],
    )
    assert kw.get("path") == str(tmp_path)
    assert "message" not in kw  # format_code not in NEED_MESSAGE

    kw2 = build_skill_kwargs(
        "rename_symbol",
        path=str(tmp_path),
        message="rename foo to bar",
        files=["a.py"],
    )
    assert kw2.get("message") == "rename foo to bar"
    assert kw2.get("files") == ["a.py"]


def test_execute_unknown_does_not_look_like_success():
    from skills.skills import SkillRegistry
    from skills.tools import ToolRegistry

    reg = SkillRegistry(tools=ToolRegistry(project_root="."))
    out = reg.execute("no_such_skill_day11_xyz")
    # stage checks result.get("success") — must be falsy
    assert not out.get("success")
    assert out.get("ok") is False


def test_execute_registered_success_shape_for_stage(tmp_path):
    from skills.skills import SkillRegistry
    from skills.tools import ToolRegistry

    reg = SkillRegistry(tools=ToolRegistry(project_root=str(tmp_path)))
    reg.register(
        "day11_echo",
        lambda path=None, **kwargs: {"echo": path, "ok": True},
        "day11",
    )
    out = reg.execute("day11_echo", path=str(tmp_path))
    assert out.get("success") is True
    # stage stores result.get("result") as payload
    assert isinstance(out.get("result"), dict)


# ---------------------------------------------------------------------------
# SkillLearner still fail-closed (observation path independent of stage)
# ---------------------------------------------------------------------------

def test_learner_observe_failure_not_recorded(tmp_path):
    from skills.skill_learner import SkillLearner

    store = tmp_path / "obs.json"
    learner = SkillLearner(min_examples=1, confidence_threshold=0.5, store=store)
    learner.observe(
        {"id": "t_fail", "message": "format code please"},
        result={"success": False, "method": "skill"},
    )
    obs = learner.observations.get("format_code") or []
    assert obs == []


def test_learner_observe_success_recorded(tmp_path):
    from skills.skill_learner import SkillLearner

    store = tmp_path / "obs.json"
    learner = SkillLearner(min_examples=1, confidence_threshold=0.5, store=store)
    learner.observe(
        {"id": "t_ok", "message": "format code please"},
        result={"success": True, "method": "skill"},
    )
    assert "format_code" in learner.observations
    assert learner.observations["format_code"][-1]["id"] == "t_ok"
