# -*- coding: utf-8 -*-
"""LoopGuard: детекция зацикливания."""
from __future__ import annotations

from safety.loopguard import LoopGuard, detect_loop_in_text


def test_same_line_triggers():
    g = LoopGuard(same_line_limit=5)
    hit = None
    for _ in range(5):
        hit = g.feed_line("Analyzing the same function again carefully")
    assert hit is not None
    assert hit.kind == "same_line"


def test_ngram_triggers():
    g = LoopGuard(ngram_size=3, ngram_limit=3, same_line_limit=99)
    lines = [
        "step one of the plan is review",
        "step two of the plan is edit",
        "step three of the plan is test",
    ]
    hit = None
    for _ in range(3):
        for ln in lines:
            hit = g.feed_line(ln)
    assert hit is not None
    assert hit.kind == "ngram"


def test_no_false_positive_on_progress():
    g = LoopGuard(same_line_limit=8, ngram_limit=5)
    for i in range(20):
        hit = g.feed_line(f"Working on unique part number {i} of the codebase")
        assert hit is None


def test_detect_loop_in_text():
    text = "\n".join(["stuck repeating this exact long line"] * 10)
    hit = detect_loop_in_text(text, same_line_limit=6)
    assert hit is not None


def test_short_lines_ignored():
    g = LoopGuard(same_line_limit=3)
    for _ in range(10):
        assert g.feed_line("ok") is None


def test_collapse_numbers_default_off():
    """TD-030: числовой прогресс — не цикл, пока collapse_numbers=False (default)."""
    g = LoopGuard(same_line_limit=8)
    for i in range(20):
        assert g.feed_line(f"Working on part {i}") is None

    # Тот же поток с collapse_numbers=True: строки схлопываются в
    # "Working on part #", и 8-я подряд — уже зацикливание.
    g2 = LoopGuard(same_line_limit=8, ngram_limit=99, collapse_numbers=True)
    hit = None
    for i in range(20):
        hit = g2.feed_line(f"Working on part {i}")
        if hit is not None:
            break
    assert hit is not None
    assert hit.kind == "same_line"
    assert hit.repeats == 8
