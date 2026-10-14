# -*- coding: utf-8 -*-
"""Autopilot — generate actionable tasks from static analysis of a Python tree.

No LLM. Uses stdlib `ast` + regex. Output is list[GeneratedTask] ready to
serialize into file-bus incoming JSON.
"""
from __future__ import annotations

import ast
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".tox", ".eggs",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")



def default_verify_for_complexity(complexity: int, files: list[str] | None = None) -> list[str]:
    """Verify template for autopilot emits (never leave verify empty)."""
    try:
        from core.verify_policy import apply_verify_policy
        raw = {
            "message": "autopilot",
            "files": list(files or []),
            "verify": [],
            "metadata": {"source": "autopilot", "complexity": int(complexity)},
        }
        out = apply_verify_policy(raw)
        return list(out.get("verify") or [])
    except Exception:
        c = int(complexity or 2)
        if c >= 4:
            return ["python -m compileall -q .", "pytest -q"]
        if c >= 3:
            return ["python -m compileall -q .", "pytest -q --tb=no -x"]
        if c >= 2:
            return ["python -m compileall -q ."]
        return ["python -m compileall -q ."]


@dataclass
class GeneratedTask:
    """Task proposed by autopilot (not yet claimed by the bus)."""

    message: str
    files: list[str]
    priority: int = 3  # 1–5, higher = more important
    category: str = "general"
    confidence: float = 0.7
    source_rule: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_bus_payload(
        self,
        *,
        channel: str = "autopilot",
        task_id: str | None = None,
        project: str = "",
        complexity: int | None = None,
    ) -> dict[str, Any]:
        """JSON for channels/<channel>/incoming — executed by existing router/workers.

        priority 1–2 → complexity 2 (local)
        priority 3   → complexity 3 (mid cloud)
        priority 4–5 → complexity 5 (strong / agentic)
        """
        import hashlib

        if complexity is None:
            complexity = Autopilot.map_priority_to_complexity(self.priority)
        if task_id:
            tid = task_id
        else:
            digest = hashlib.md5(
                f"{self.message}|{','.join(self.files)}|{self.category}".encode()
            ).hexdigest()[:10]
            tid = f"auto_{digest}"
        verify_cmds = default_verify_for_complexity(int(complexity), list(self.files))
        # apply_verify_policy also fills ladder metadata
        try:
            from core.verify_policy import apply_verify_policy
            _pre = apply_verify_policy({
                "message": self.message,
                "files": list(self.files),
                "verify": verify_cmds,
                "metadata": {
                    "source": "autopilot",
                    "complexity": int(complexity),
                },
            })
            verify_cmds = list(_pre.get("verify") or verify_cmds)
            ladder_meta = _pre.get("metadata") if isinstance(_pre.get("metadata"), dict) else {}
        except Exception:
            ladder_meta = {}
        meta = {
            "source": "autopilot",
            "category": self.category,
            "priority": self.priority,
            "confidence": self.confidence,
            "rule": self.source_rule,
            "complexity": int(complexity),
            "verify_policy": ladder_meta.get("verify_policy") or "autopilot_template",
            "verify_ladder": ladder_meta.get("verify_ladder"),
            "verify_max_level": ladder_meta.get("verify_max_level"),
            "verify_ladder_label": ladder_meta.get("verify_ladder_label"),
            "git_policy": "branch",
            **(self.extra or {}),
        }
        return {
            "id": tid,
            "message": self.message,
            "files": list(self.files),
            "channel": channel,
            "project": project,
            "verify": verify_cmds,
            "run": [],
            "complexity": int(complexity),
            "metadata": meta,
        }

class Autopilot:
    """Scan a project root and emit ranked GeneratedTask list."""

    def __init__(
        self,
        project_path: str | Path,
        *,
        max_files: int | None = None,
        max_tasks: int | None = None,
        max_file_bytes: int = 400_000,
        long_function_lines: int = 50,
    ) -> None:
        self.root = Path(project_path).resolve()
        self.max_files = max_files if max_files is not None else max(
            20, _env_int("AGENTBUS_AUTOPILOT_MAX_FILES", 800)
        )
        self.max_tasks = max_tasks if max_tasks is not None else max(
            5, _env_int("AGENTBUS_AUTOPILOT_MAX_TASKS", 40)
        )
        self.max_file_bytes = max_file_bytes
        self.long_function_lines = long_function_lines
        self.rules: list[Callable[[], list[GeneratedTask]]] = [
            self._find_todos,
            self._find_unused_imports,
            self._find_missing_docstrings,
            self._find_long_functions,
            self._find_missing_type_hints,
            self._find_missing_init,
            self._find_bare_io_calls,
            self._find_bare_excepts,
            self._find_large_files,
            self._find_print_debug,
        ]

    def _iter_py_files(self) -> Iterable[Path]:
        count = 0
        try:
            paths = sorted(self.root.rglob("*.py"))
        except OSError:
            return
        for path in paths:
            if count >= self.max_files:
                break
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
            except OSError:
                continue
            count += 1
            yield path

    def scan(self) -> list[GeneratedTask]:
        """Run all rules, dedupe by (message, files), rank, cap."""
        tasks: list[GeneratedTask] = []
        for rule in self.rules:
            try:
                tasks.extend(rule())
            except Exception:
                continue
        # dedupe
        seen: set[str] = set()
        unique: list[GeneratedTask] = []
        for t in tasks:
            key = f"{t.message}|{','.join(t.files)}|{t.category}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(t)
        unique.sort(key=lambda t: (-t.priority, -t.confidence, t.category, t.message))
        return unique[: self.max_tasks]

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _find_missing_docstrings(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        for py_file in self._iter_py_files():
            rel = _rel(py_file, self.root)
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name.startswith("_"):
                        continue
                    if not ast.get_docstring(node):
                        kind = "класс" if isinstance(node, ast.ClassDef) else "функцию"
                        tasks.append(
                            GeneratedTask(
                                message=f"Добавь docstring к {kind} {node.name} в {rel}",
                                files=[rel],
                                priority=2,
                                category="docs",
                                confidence=0.85,
                                source_rule="missing_docstrings",
                            )
                        )
        return tasks

    def _find_long_functions(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        max_lines = self.long_function_lines
        for py_file in self._iter_py_files():
            rel = _rel(py_file, self.root)
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                end = getattr(node, "end_lineno", None) or node.lineno
                length = int(end) - int(node.lineno) + 1
                if length > max_lines:
                    tasks.append(
                        GeneratedTask(
                            message=(
                                f"Разбей функцию {node.name} в {rel} на части "
                                f"(сейчас ~{length} строк, порог {max_lines})"
                            ),
                            files=[rel],
                            priority=4,
                            category="refactor",
                            confidence=0.75,
                            source_rule="long_functions",
                            extra={"lines": length},
                        )
                    )
        return tasks

    def _find_todos(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        rx = re.compile(r"#\s*(TODO|FIXME|XXX)\b:?\s*(.*)$", re.I)
        for py_file in self._iter_py_files():
            rel = _rel(py_file, self.root)
            try:
                lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                m = rx.search(line)
                if not m:
                    continue
                body = (m.group(2) or "").strip() or m.group(1).upper()
                tasks.append(
                    GeneratedTask(
                        message=f"Закрой {m.group(1).upper()} в {rel}:{i}: {body[:120]}",
                        files=[rel],
                        priority=3,
                        category="feature",
                        confidence=0.65,
                        source_rule="todos",
                        extra={"line": i},
                    )
                )
        return tasks

    def _find_unused_imports(self) -> list[GeneratedTask]:
        """Heuristic: import names never referenced as Name/Attribute base."""
        tasks: list[GeneratedTask] = []
        for py_file in self._iter_py_files():
            rel = _rel(py_file, self.root)
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            imports: set[str] = set()
            used: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "__future__":
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            imports.clear()
                            break
                        imports.add(alias.asname or alias.name)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    # count only root of attribute chains via Name in value
                    pass
            unused = sorted(imports - used - {"_"})
            # filter dunders / typing-only common false positives lightly
            unused = [u for u in unused if u and not u.startswith("__")]
            if unused:
                shown = ", ".join(unused[:8])
                more = f" (+{len(unused) - 8})" if len(unused) > 8 else ""
                tasks.append(
                    GeneratedTask(
                        message=f"Удали неиспользуемые импорты в {rel}: {shown}{more}",
                        files=[rel],
                        priority=2,
                        category="cleanup",
                        confidence=0.8,
                        source_rule="unused_imports",
                        extra={"unused": unused[:20]},
                    )
                )
        return tasks

    def _find_missing_type_hints(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        for py_file in self._iter_py_files():
            rel = _rel(py_file, self.root)
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                args = [a for a in node.args.args if a.arg not in ("self", "cls")]
                missing_args = [a.arg for a in args if a.annotation is None]
                missing_ret = node.returns is None
                if missing_args or missing_ret:
                    tasks.append(
                        GeneratedTask(
                            message=f"Добавь аннотации типов к функции {node.name} в {rel}",
                            files=[rel],
                            priority=1,
                            category="typing",
                            confidence=0.7,
                            source_rule="missing_type_hints",
                        )
                    )
        return tasks

    @staticmethod
    def map_priority_to_complexity(priority: int) -> int:
        """Map autopilot priority (1–5) → router complexity for existing workers."""
        try:
            p = int(priority)
        except (TypeError, ValueError):
            p = 3
        if p <= 2:
            return 2
        if p == 3:
            return 3
        return 5


    def _find_bare_excepts(self) -> list[GeneratedTask]:
        """Generate tasks for files with bare except: handlers."""
        import ast as _ast
        tasks: list[GeneratedTask] = []
        for path in self._iter_py_files():
            try:
                src = path.read_text(encoding="utf-8")
                tree = _ast.parse(src)
            except (OSError, SyntaxError):
                continue
            lines = []
            for node in _ast.walk(tree):
                if isinstance(node, _ast.ExceptHandler) and node.type is None:
                    lines.append(getattr(node, "lineno", 0))
            if not lines:
                continue
            try:
                rel = str(path.relative_to(self.root))
            except ValueError:
                rel = str(path)
            tasks.append(
                GeneratedTask(
                    message=f"Replace bare except: with typed except in {rel} (lines {', '.join(map(str, lines[:5]))})",
                    files=[rel],
                    priority=4,
                    category="cleanup",
                    confidence=0.85,
                )
            )
        return tasks


    def _find_print_debug(self) -> list[GeneratedTask]:
        """Flag files with leftover print() debug calls."""
        import re as _re
        tasks: list[GeneratedTask] = []
        rx = _re.compile(r"^\s*print\s*\(", _re.M)
        for path in self._iter_py_files():
            try:
                src = path.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = list(rx.finditer(src))
            if not hits:
                continue
            try:
                rel = str(path.relative_to(self.root))
            except ValueError:
                rel = str(path)
            lines = [str(src[:m.start()].count("\n") + 1) for m in hits[:5]]
            tasks.append(
                GeneratedTask(
                    message=f"Replace debug print() with logging in {rel} (lines {', '.join(lines)})",
                    files=[rel],
                    priority=3,
                    category="cleanup",
                    confidence=0.75,
                )
            )
        return tasks

    def emit_tasks(
        self,
        *,
        bus_root: str | Path = ".",
        channel: str = "autopilot",
        project: str = "",
        limit: int | None = None,
        tasks: list[GeneratedTask] | None = None,
    ) -> list[Path]:
        """Scan (or use given tasks) and drop ordinary file-bus JSON into incoming.

        Does **not** execute anything — the existing claim → router → workers path
        picks them up. Channel default is ``autopilot`` so metrics can filter
        ``metadata.source == autopilot``.
        """
        return self.write_incoming(
            tasks,
            bus_root=bus_root,
            channel=channel,
            project=project or self.root.name,
            limit=limit,
        )

    def write_incoming(
        self,
        tasks: list[GeneratedTask] | None = None,
        *,
        bus_root: str | Path,
        channel: str = "autopilot",
        project: str = "",
        limit: int | None = None,
    ) -> list[Path]:
        """Materialize tasks as JSON files under bus_root/channels/<ch>/incoming."""
        import json
        from core.bus import FileBus

        tasks = list(tasks if tasks is not None else self.scan())
        if limit is not None:
            tasks = tasks[: max(0, limit)]
        bus = FileBus(Path(bus_root), (channel,))
        incoming = bus.paths(channel)["incoming"]
        written: list[Path] = []
        for t in tasks:
            payload = t.to_bus_payload(
                channel=channel,
                project=project or self.root.name,
            )
            tid = str(payload["id"])
            path = incoming / f"{tid}.json"
            # Skip if already present (stable id from message hash)
            if path.exists():
                continue
            written.append(bus.write(
                channel,
                "incoming",
                f"{tid}.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            ))
        return written


    def _find_missing_init(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        for d in [self.root, *self.root.rglob("*")]:
            if not d.is_dir():
                continue
            if any(x in d.parts for x in _SKIP_DIRS):
                continue
            if not list(d.glob("*.py")):
                continue
            if (d / "__init__.py").exists():
                continue
            rel = _rel(d, self.root)
            tasks.append(
                GeneratedTask(
                    message=f"Добавь __init__.py в пакет `{rel}` (или удали лишние .py если это не пакет).",
                    files=[f"{rel}/__init__.py"] if rel != "." else ["__init__.py"],
                    priority=2,
                    category="structure",
                    confidence=0.85,
                    source_rule="missing_init",
                )
            )
        return tasks[:15]

    def _find_bare_io_calls(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        for py in self._iter_py():
            try:
                src = py.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            try_depth = 0
            hits: list[int] = []

            class V(ast.NodeVisitor):
                def visit_Try(self, node: ast.Try) -> None:
                    nonlocal try_depth
                    try_depth += 1
                    self.generic_visit(node)
                    try_depth -= 1

                def visit_Call(self, node: ast.Call) -> None:
                    nonlocal try_depth
                    name = ""
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name == "open" and try_depth == 0:
                        hits.append(getattr(node, "lineno", 0))
                    self.generic_visit(node)

            V().visit(tree)
            if hits:
                rel = _rel(py, self.root)
                tasks.append(
                    GeneratedTask(
                        message=f"Оберни open() в try/except в `{rel}` (строки {hits[:5]}).",
                        files=[rel],
                        priority=3,
                        category="robustness",
                        confidence=0.75,
                        source_rule="bare_open",
                    )
                )
        return tasks[:20]

    def _find_large_files(self) -> list[GeneratedTask]:
        tasks: list[GeneratedTask] = []
        limit = _env_int("AUTOPILOT_MAX_FILE_LINES", 500)
        for py in self._iter_py():
            try:
                n = sum(1 for _ in py.open(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if n >= limit:
                rel = _rel(py, self.root)
                tasks.append(
                    GeneratedTask(
                        message=f"Файл `{rel}` слишком большой ({n} строк). Разбей на модули без изменения поведения.",
                        files=[rel],
                        priority=3,
                        category="refactor",
                        confidence=0.7,
                        source_rule="large_file",
                    )
                )
        return tasks[:10]

    def summary(self, tasks: list[GeneratedTask] | None = None) -> dict[str, Any]:
        tasks = list(tasks if tasks is not None else self.scan())
        by_cat: dict[str, int] = {}
        for t in tasks:
            by_cat[t.category] = by_cat.get(t.category, 0) + 1
        return {
            "root": str(self.root),
            "count": len(tasks),
            "by_category": by_cat,
            "top": [
                {
                    "message": t.message[:120],
                    "priority": t.priority,
                    "category": t.category,
                    "files": t.files[:3],
                }
                for t in tasks[:10]
            ],
            "ts": time.time(),
        }

    def emit_goal_graph(
        self,
        steps: list[str],
        *,
        bus_root: str | Path | None = None,
        channel: str = "autopilot",
        project: str = "",
        files: list[str] | None = None,
        parent_id: str = "goal",
        max_emit: int = 1,
    ) -> list[str]:
        """Emit goal as dependency graph (first READY step only by default)."""
        try:
            from intelligence.task_graph import TaskGraph, emit_ready_to_bus
        except Exception:
            ids: list[str] = []
            for step in (steps or [])[: max(1, max_emit)]:
                gt = GeneratedTask(
                    message=step,
                    files=list(files or []),
                    priority=3,
                    category="goal",
                    confidence=0.6,
                )
                self.write_incoming(
                    [gt],
                    bus_root=bus_root or self.root,
                    channel=channel,
                    project=project or self.root.name,
                )
                ids.append(step)
            return ids
        g = TaskGraph.from_goal_steps(list(steps or []), files=files)
        root = bus_root or self.root
        return emit_ready_to_bus(
            g,
            bus_root=root,
            channel=channel,
            project=project or self.root.name,
            parent_id=parent_id,
            max_emit=max_emit,
        )


def scan_project(project_path: str | Path, **kwargs: Any) -> list[GeneratedTask]:
    return Autopilot(project_path, **kwargs).scan()


if __name__ == "__main__":
    import json
    import sys

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    ap = Autopilot(root)
    tasks = ap.scan()
    print(json.dumps(ap.summary(tasks), ensure_ascii=False, indent=2))
    for t in tasks[:15]:
        print(f"[{t.priority}] {t.category:10} {t.message}")
