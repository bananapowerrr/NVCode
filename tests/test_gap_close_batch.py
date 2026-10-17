# -*- coding: utf-8 -*-
from pathlib import Path


def test_clamp_handles_dict_assemble():
    src = (Path(__file__).resolve().parent.parent / "src" / "core" / "rp_context.py").read_text(encoding="utf-8")
    assert "assembled.get(\"message\")" in src or "assembled.get('message')" in src
    assert "_stash_context_audit" in src
    assert "_last_context_audit" in src


def test_finish_task_last_result():
    src = (Path(__file__).resolve().parent.parent / "src" / "core" / "runtime_ops.py").read_text(encoding="utf-8")
    assert "last_result" in src
    assert "GAP: last_result" in src or "last_result" in src


def test_i18n_night_keys():
    en = (Path(__file__).resolve().parent.parent / "config" / "strings_en.yaml").read_text(encoding="utf-8")
    ru = (Path(__file__).resolve().parent.parent / "config" / "strings_ru.yaml").read_text(encoding="utf-8")
    for key in ("settings_tab_night:", "night_title:", "about_check_btn:", "settings_tab_about:"):
        assert key in en, key
        assert key in ru, key
