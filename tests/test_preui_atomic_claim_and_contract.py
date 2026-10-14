# -*- coding: utf-8 -*-
"""PREUI-001 (F1) atomic claim and PREUI-002 (F2) terminal contract."""
from __future__ import annotations

import inspect
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path



def _bus(tmp_path: Path):
    from core.bus import FileBus

    return FileBus(tmp_path, ("gpt", "desktop"))


# ── PREUI-001 / F1 ────────────────────────────────────────────────────

def test_move_source_has_no_copy_unlink():
    """Claim обязан быть атомарным: никаких copy2 + unlink.

    Проверяем AST, а не текст: иначе ловим собственные же комментарии.
    """
    import ast
    import textwrap

    from core.bus import FileBus

    tree = ast.parse(textwrap.dedent(inspect.getsource(FileBus.move)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "copy2" not in called
    assert "copy" not in called
    assert "unlink" not in called
    assert "replace" in called


def test_move_is_atomic_rename(tmp_path):
    bus = _bus(tmp_path)
    p = bus.paths("desktop")["incoming"] / "t1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")

    assert bus.move("desktop", "incoming", "processing", "t1.json") is True
    assert not p.exists(), "исходник обязан исчезнуть"
    assert (bus.paths("desktop")["processing"] / "t1.json").is_file()


def test_move_returns_false_on_lost_race(tmp_path):
    """Второй инстанс получает False и обязан прекратить обработку."""
    bus = _bus(tmp_path)
    p = bus.paths("gpt")["incoming"] / "t2.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")

    assert bus.move("gpt", "incoming", "processing", "t2.json") is True
    # Второй вызов: исходника уже нет.
    assert bus.move("gpt", "incoming", "processing", "t2.json") is False


def test_concurrent_move_claims_exactly_once(tmp_path):
    """Гонка двух инстансов на обычном канале: ровно один True.

    Регрессия на copy2+unlink, где оба успевали скопировать файл.
    """
    bus = _bus(tmp_path)
    p = bus.paths("gpt")["incoming"] / "t3.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"id": "t3"}', encoding="utf-8")

    def claim(_):
        return bus.move("gpt", "incoming", "processing", "t3.json")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(claim, range(8)))

    assert sum(1 for r in results if r is True) == 1, results
    assert sum(1 for r in results if r is False) == 7, results
    dst = bus.paths("gpt")["processing"] / "t3.json"
    assert dst.read_text(encoding="utf-8") == '{"id": "t3"}'


def test_desktop_missing_source_is_not_a_claim(tmp_path):
    """Отсутствующий incoming источник не даёт вызывающему ownership."""
    bus = _bus(tmp_path)
    p = bus.paths("desktop")["processing"] / "t4.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")

    assert bus.move("desktop", "incoming", "processing", "t4.json") is False


def test_desktop_concurrent_claim_is_single(tmp_path):
    """Гонка desktop claim должна иметь ровно одного владельца."""
    bus = _bus(tmp_path)
    p = bus.paths("desktop")["incoming"] / "t5.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"id": "t5"}', encoding="utf-8")

    def claim(_):
        return bus.move("desktop", "incoming", "processing", "t5.json")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(claim, range(8)))

    assert sum(1 for r in results if r is True) == 1, results
    assert sum(1 for r in results if r is False) == 7, results


# ── PREUI-002 / F2 ────────────────────────────────────────────────────

def test_build_terminal_result_defaults():
    from core.terminal_path import build_terminal_result

    r = build_terminal_result(error="boom")
    assert r["verified"] is False
    assert r["changed_files"] == []


def test_build_terminal_result_normalizes_none():
    """None не должен утекать в терминальный JSON."""
    from core.terminal_path import build_terminal_result

    r = build_terminal_result(extra={"verified": None, "changed_files": None})
    assert r["verified"] is False
    assert r["changed_files"] == []


def test_build_terminal_result_keeps_real_values():
    from core.terminal_path import build_terminal_result

    r = build_terminal_result(extra={"verified": True, "changed_files": ("a.py", "b.py")})
    assert r["verified"] is True
    assert r["changed_files"] == ["a.py", "b.py"]


def test_build_terminal_result_rejects_junk_changed_files():
    from core.terminal_path import build_terminal_result

    r = build_terminal_result(extra={"changed_files": "src/core/bus.py"})
    assert r["changed_files"] == []


def test_deferred_terminal_record_has_contract_keys(tmp_path):
    """DEFERRED из rp_llm обязан нести verified и changed_files."""
    import json

    from core.terminal_path import build_terminal_result, enforce_done_contract

    # Ровно тот payload, который rp_llm отдаёт на пути декомпозиции.
    res = build_terminal_result(
        error="нужна декомпозиция",
        worker="aider_local",
        extra={"attempts": 1, "changed_files": [], "decomposition_required": True},
    )
    state, res = enforce_done_contract("DEFERRED", res)
    assert state == "DEFERRED"
    assert "verified" in res and isinstance(res["verified"], bool)
    assert "changed_files" in res and isinstance(res["changed_files"], list)
    # Сериализуется без потерь (json не терпит NaN/нестандартные типы).
    assert json.loads(json.dumps(res))["verified"] is False
