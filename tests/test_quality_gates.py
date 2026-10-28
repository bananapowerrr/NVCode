# -*- coding: utf-8 -*-
"""ub-mode quality-gate lever tests (offline, hermetic)."""
from __future__ import annotations

import ast
import textwrap

from scripts.quality_gates import (
    complexity,
    load_allowlist,
    main as gates_main,
    normalize_line,
    scan,
    write_allowlist,
)


def _scan_src(src: str) -> list:
    tree = ast.parse(textwrap.dedent(src))
    out: list = []
    scan(tree, "", "t.py", out)
    return out


def test_complexity_counts_branches():
    fns = _scan_src(
        """
        def f(a, b):
            if a and b:
                return 1
            for i in range(3):
                if i:
                    pass
            try:
                x = 1 if a else 2
            except ValueError:
                x = 0
            return x
        """
    )
    assert len(fns) == 1
    # 1 base + if + and + for + if + ifexp + except = 7
    assert fns[0].comp == 7


def test_nested_def_excluded_from_parent():
    fns = _scan_src(
        """
        def outer(x):
            if x:
                def inner(y):
                    if y:
                        return 1
                    return 2
                return inner
            return 0
        """
    )
    by_name = {f.name: f.comp for f in fns}
    assert by_name == {"outer": 2, "inner": 2}


def test_class_methods_qualified():
    fns = _scan_src(
        """
        class C:
            def m(self, x):
                return [i for i in x if i]
        """
    )
    assert [f.name for f in fns] == ["C.m"]
    # 1 base + comprehension for + comprehension if = 3
    assert fns[0].comp == 3


def test_normalize_line_masks_strings_and_numbers():
    assert normalize_line('x = "abc" + 12') == "x = S + N"
    assert normalize_line("return 4  # comment") == "return N"


def _monster_src() -> str:
    body = "".join(f"    if x > {i}:\n        pass\n" for i in range(20))
    return f"def monster(x):\n{body}"


def test_gate_fails_on_new_monster(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(_monster_src(), encoding="utf-8")
    rc = gates_main(
        ["--gate", "--max", "12", "--paths", str(src),
         "--no-dup", "--report-min", "99", "--allowlist", str(tmp_path / "allow.txt")]
    )
    assert rc == 1
    assert "monster" in capsys.readouterr().out


def test_gate_passes_when_clean(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text("def small(x):\n    return x + 1\n", encoding="utf-8")
    rc = gates_main(
        ["--gate", "--max", "12", "--paths", str(src),
         "--no-dup", "--report-min", "99", "--allowlist", str(tmp_path / "a.txt")]
    )
    assert rc == 0
    assert "GATE OK" in capsys.readouterr().out


def test_allowlist_freezes_existing_debt(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(_monster_src(), encoding="utf-8")
    allow = tmp_path / "allow.txt"
    rc = gates_main(
        ["--update-allowlist", "--max", "12", "--paths", str(src),
         "--no-dup", "--report-min", "99", "--allowlist", str(allow)]
    )
    assert rc == 0
    expected = (src / "m.py").as_posix() + "::monster"
    assert allow.read_text(encoding="utf-8").strip() == expected
    assert load_allowlist(str(allow)) == {expected}
    # frozen debt passes the gate
    rc = gates_main(
        ["--gate", "--max", "12", "--paths", str(src),
         "--no-dup", "--report-min", "99", "--allowlist", str(allow)]
    )
    assert rc == 0


def test_complexity_direct():
    tree = ast.parse("def f(x):\n    return x or 1 or 2\n")
    fn = tree.body[0]
    assert complexity(fn) == 3  # 1 base + 2 or
