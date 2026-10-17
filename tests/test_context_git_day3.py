# -*- coding: utf-8 -*-
"""Day-3 offline: context pack budget."""
from __future__ import annotations

from intelligence.context_budget import (
    ContextAssembly,
    SlotSpec,
    assemble_worker_message,
    estimate_tokens,
    smart_truncate,
)
from intelligence.context_pack import pack_for_worker, is_context_safe_for_7b, PACK_TOTAL


def test_smart_truncate_keeps_tail():
    text = "A" * 500 + "MIDDLE" + "B" * 500
    out = smart_truncate(text, 200, keep_tail=True)
    assert len(out) <= 200
    assert "truncated" in out or "..." in out


def test_assemble_under_budget():
    huge = "x" * 50_000
    body = assemble_worker_message(
        user_request="fix the bug",
        memory=huge,
        conversation=huge,
        rag=huge,
        active_diff=huge,
        extras=huge,
        system_rules="be careful",
        total_chars=8_000,
    )
    assert len(body) <= 8_000 + 50  # small overhead tolerance
    assert "USER REQUEST" in body or "fix the bug" in body


def test_estimate_tokens_positive():
    assert estimate_tokens("hello") >= 1
    assert estimate_tokens("привет мир") >= 1


def test_pack_for_worker_without_project():
    res = pack_for_worker(
        user_message="Создай test_aider.txt",
        project_memory="use pytest",
        conversation_tail="user: hi\nagent: ok",
        total_chars=5_000,
    )
    assert res.message
    assert res.stats["chars"] <= 5_000
    assert is_context_safe_for_7b(res, max_chars=5_000)


def test_context_assembly_drops_low_priority():
    asm = ContextAssembly(total_chars=500)
    asm.specs["extras"] = SlotSpec("extras", max_chars=400, priority=10)
    asm.specs["user_request"] = SlotSpec("user_request", max_chars=400, min_chars=50, priority=100)
    asm.set("extras", "E" * 400)
    asm.set("user_request", "do the thing")
    body = asm.assemble()
    assert "do the thing" in body
    assert len(body) <= 500 + 20

