# -*- coding: utf-8 -*-
"""Solution cache + deterministic skills.

Mixin for RuntimeProcess. Do not instantiate alone — used via Runtime MRO.
"""
from __future__ import annotations

from pathlib import Path
import uuid
from typing import Any

try:
    from core.feature_flags import is_enabled
except Exception:  # pragma: no cover
    def is_enabled(name: str, default: bool = True) -> bool:
        return default

try:
    from core.tasks import Task
except Exception:  # pragma: no cover
    Task = None  # type: ignore

try:
    from core.config import DEFAULT_CHANNEL, PROJECT_ROOT, resolve_project, BUS_ROOT
except Exception:  # pragma: no cover
    DEFAULT_CHANNEL = "gpt"
    PROJECT_ROOT = None
    def resolve_project(x):
        return x
    BUS_ROOT = Path(".")

try:
    from core.ranking import infer_task_type
except Exception:  # pragma: no cover
    def infer_task_type(raw):
        return "general"

try:
    from skills.test_runner import TestRunner
except Exception:  # pragma: no cover
    TestRunner = None  # type: ignore

try:
    from safety.gitops import GitOps
except Exception:  # pragma: no cover
    GitOps = None  # type: ignore

try:
    from intelligence.context import ContextBuilder
except Exception:  # pragma: no cover
    ContextBuilder = None  # type: ignore

try:
    from core.project import ProjectContext
except Exception:  # pragma: no cover
    ProjectContext = None  # type: ignore

class RPCacheSkillsMixin:
    def _stage_cache_and_skills(self, task, raw: dict, proj) -> str | None:
        """Cache hit or skill hit → terminal status; else None."""
        cache_hit = self._try_cache(task, raw, proj)
        # Истина, а не "is not None": "" означает "кэш есть, но восстановление
        # ничего не дало". Раньше "" считался попаданием и возвращался вверх
        # как терминал, из-за чего задача висела в processing до reclaim
        # вместо того, чтобы уйти в worker/LLM.
        if cache_hit:
            try:
                from utils.metrics import GLOBAL_METRICS
                GLOBAL_METRICS.record("cache_hit")
                try:
                    from utils.pipeline_events import cache_hit as _pev_cache
                    _pev_cache(str(getattr(task, "id", "")))
                except Exception:
                    pass
                try:
                    from utils.cost_tracker import GLOBAL_COST
                    GLOBAL_COST.record_save("cache")
                except Exception:
                    pass
                try:
                    self._explain_and_learn("cache", task, entry=locals().get("cached") or locals().get("entry"))
                except Exception:
                    pass
            except Exception:
                pass
            return cache_hit
        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record("cache_miss")
        except Exception:
            pass

        skill_result = self._try_skill(task, proj)
        # _try_skill returns {skill, result, method} without "ok" — any dict is a hit
        if skill_result is not None:
            try:
                from utils.metrics import GLOBAL_METRICS
                GLOBAL_METRICS.record("skill_hit")
                try:
                    from utils.pipeline_events import skill_hit as _pev_skill
                    _pev_skill(str(getattr(task, "id", "")), str(skill_result.get("skill") or ""))
                except Exception:
                    pass
                try:
                    GLOBAL_METRICS.record_skill(
                        str(skill_result.get("skill") or "skill"),
                        success=True,
                        latency=float(skill_result.get("latency") or 0.0),
                        matched=True,
                    )
                except Exception:
                    pass
                try:
                    from utils.cost_tracker import GLOBAL_COST
                    GLOBAL_COST.record_save("skill")
                except Exception:
                    pass
                try:
                    self._explain_and_learn(
                        "skill", task,
                        skill=str(skill_result.get("skill") or "skill"),
                        success=True,
                    )
                except Exception:
                    pass
            except Exception:
                pass
            status = self._finalize_skill_result(task, skill_result, proj)
            if status == "DONE":
                self._cache_put(
                    task, proj,
                    method="skill",
                    worker="skill",
                    skill=skill_result.get("skill") or "",
                    stdout=str(skill_result.get("result") or "")[:4000],
                    result=skill_result.get("result"),
                )
                return status
            if status:
                # Терминальное состояние (например контракт демотил
                # DONE -> ERROR, и терминальная запись уже записана).
                # Раньше любой не-DONE молча превращался в None, и задача
                # продолжала обрабатываться поверх готовой записи.
                return status
            # "" = навык отработал, но задачу не решил. Возвращаем None,
            # чтобы вызывающий продолжил в worker/LLM. Раньше здесь был
            # безусловный return status, и "" считался терминалом: воркер
            # не запускался, задача висела в processing до reclaim.
            return None
        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record("skill_miss")
        except Exception:
            pass
        return None


    def _try_cache(self, task: Task, raw: dict, proj) -> str | None:
        """Lookup solution cache. Returns terminal status or None."""
        try:
            if not is_enabled("solution_cache"):
                raise ImportError("solution_cache disabled")
            from intelligence.solution_cache import GLOBAL_CACHE, force_refresh_requested
        except ImportError:
            return None
        if force_refresh_requested(raw, task):
            return None
        try:
            entry = GLOBAL_CACHE.get(task, project_root=str(proj))
        except Exception:
            return None
        if not entry:
            return None
        try:
            return self._apply_cached_solution(task, entry, proj)
        except Exception as exc:
            try:
                self.log.write(f"cache apply: {exc}")
            except Exception:
                pass
            return None

    def _non_empty_files(self, paths) -> list[str]:
        """Оставляет только реально записанные (непустые) файлы.

        Путь в списке — не доказательство работы: заглушка 0 Б проходит
        проверку «файл применён», но задача не решена. Такой случай давал
        ложный DONE мимо finish_task.
        """
        out = []
        try:
            from pathlib import Path as _P
            root = _P(str(getattr(getattr(self, "context", None), "root", ".") or "."))
            for p in (paths or []):
                f = root / str(p)
                if f.is_file() and f.stat().st_size > 0:
                    out.append(str(p))
        except Exception:
            return []
        return out

    def _apply_cached_solution(self, task: Task, entry: dict, proj) -> str:
        """Replay cached DONE: optional file restore + finalize.

        Guard от 2026-09-29: кэш переигрывал запись, сделанную ложным
        skill-DONE (навык отработал, но файлы не изменились). Кэш считал
        это успешным решением и выдавал DONE снова и снова, хотя файла
        не существует. Теперь проверяем, что восстановление реально
        что-то применило, иначе отдаём задачу нормальному пути.
        """
        sol = entry.get("solution") or {}
        method = sol.get("method") or "cache"
        worker = sol.get("worker") or "cache"
        apply_info: dict = {}
        from intelligence.solution_cache import GLOBAL_CACHE
        if entry.get("file_snapshots"):
            apply_info = GLOBAL_CACHE.apply_snapshots(entry, str(proj))

        # Кэш применил изменения -> можно завершать.
        applied = 0
        if isinstance(apply_info, dict):
            for key in ("applied", "restored", "written", "files"):
                v = apply_info.get(key)
                if isinstance(v, int):
                    applied = v
                    break
                if isinstance(v, (list, tuple)) and v:
                    applied = len(v)
                    applied_paths = list(v)
                    break
        if not applied:
            try:
                self.log.write(
                    f"cache miss {method}: восстановление ничего не применило "
                    f"({apply_info!r}) — задача не решена, иду в worker/LLM "
                    f"(task={getattr(task, 'id', '?')})"
                )
            except Exception:
                pass
            return ""
        # Непустой список путей ещё не доказательство: файл может быть
        # создан, но остаться пустым (заглушка). Такой cache-hit давал
        # ложный DONE в обход finish_task. Проверяем содержимое.
        real_files = self._non_empty_files(applied_paths)
        if not real_files:
            try:
                self.log.write(
                    f"cache miss {method}: путь есть, но файлы пусты "
                    f"{applied_paths!r} — задача не решена, иду в worker/LLM "
                    f"(task={getattr(task, 'id', '?')})"
                )
            except Exception:
                pass
            return ""

        summary = sol.get("summary") or sol.get("stdout") or f"cache:{method}"
        restored = apply_info.get("written") or []
        # B3: терминал пишется только через finish_task. Раньше здесь стояли
        # bus.move -> done и _save в обход контракта, из-за чего ложный DONE
        # проходил без verified/changed_files. emit=False: ниже остаётся
        # свой _emit с богатым payload для UI, двойного события не будет.
        state = self.finish_task(
            task,
            "DONE",
            {
                "worker": worker,
                "method": "cache",
                "cache_method": method,
                "skill": sol.get("skill") or "",
                "stdout": str(sol.get("stdout") or "")[:4000],
                "summary": str(summary)[:4000],
                "commit": sol.get("commit") or "",
                "cache_key": entry.get("_key") or "",
                "restored_files": restored,
                "restored_from_cache": True,
                # DEV-001: ok -> execution_ok в evidence_snapshot. Без него
                # гейт читал exec_ok=None/False и демотил cache-hit в ERROR
                # с reason=execution_failed, хотя файлы восстановлены.
                "ok": True,
                "short_circuit": "cache_hit",
                # verified не выдумывается: выше _non_empty_files уже
                # убедился, что файлы существуют и непусты. Фиксируем, чем
                # именно проверено, чтобы флаг не был голым обещанием.
                "verified": True,
                "verify_ok": True,
                "verification": {
                    "ok": True,
                    # passed обязателен: report_from_dict читает только
                    # 'passed'; без него verification выглядит как FAIL и
                    # гейт отвергает DONE.
                    "passed": True,
                    "source": "cache_restore",
                    "files_checked": len(real_files),
                },
                "changed_files": list(real_files),
            },
            emit=False,
        )
        if state != "DONE":
            # Контракт демотил DONE -> ERROR. Терминальная запись уже
            # записана finish_task, поэтому возвращаем состояние: дальше
            # идти нельзя, иначе задача продолжит обрабатываться.
            try:
                self.log.write(
                    f"cache {method}: контракт не дал DONE ({state}) — "
                    f"task={getattr(task, 'id', '?')}"
                )
            except Exception:
                pass
            return state
        try:
            self.report.record("DONE", "cache", task.attempts)
        except Exception:
            pass
        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record_task(task, "cache", True, 0.0, status="DONE")
        except Exception:
            pass
        try:
            self.log.log_task_done(task.id, "cache", 0.0, 0, channel=task.channel)
        except Exception:
            pass
        try:
            self.log.task(
                task.channel, task.id, "cache",
                f"ГОТОВО[cache:{method}]",
            )
        except Exception:
            pass
        self._emit(
            "DONE",
            f"cache:{method}",
            task_id=task.id,
            worker="cache",
            payload={
                "method": "cache",
                "cache_method": method,
                "worker": worker,
                "skill": sol.get("skill") or "",
                "attempts": task.attempts,
                "restored_files": apply_info.get("written") or [],
            },
        )
        return "DONE"

    def _cache_put(
        self,
        task: Task,
        proj,
        *,
        method: str,
        worker: str = "",
        skill: str = "",
        stdout: str = "",
        commit: str = "",
        result=None,
        snapshot: bool = False,
    ) -> None:
        # ROADMAP A: no cache without L1+ (avoid poisoning cache with untested DONE)
        try:
            meta = task.metadata if isinstance(getattr(task, "metadata", None), dict) else {}
            ladder = int(meta.get("verify_ladder") or meta.get("verify_max_level") or 0)
        except (TypeError, ValueError):
            ladder = 0
            meta = {}
        if ladder < 1:
            # allow deterministic skills on truly trivial tasks only
            try:
                c = int(meta.get("complexity") or getattr(task, "complexity", None) or 2)
            except (TypeError, ValueError):
                c = 2
            if not (method == "skill" and c <= 1):
                try:
                    self.log.write(
                        f"cache skip: ladder={ladder} method={method} "
                        f"(need L1+; verify_policy={meta.get('verify_policy')})"
                    )
                except Exception:
                    pass
                try:
                    from utils.metrics import GLOBAL_METRICS
                    GLOBAL_METRICS.record("cache_skip_no_ladder")
                except Exception:
                    pass
                return
        try:
            from intelligence.solution_cache import GLOBAL_CACHE
        except ImportError:
            return
        try:
            file_snapshots = None
            content_fp = None
            if snapshot and task.files:
                # fingerprint *before* we already changed files is ideal;
                # here we store post-success snapshots for replay.
                file_snapshots = GLOBAL_CACHE.snapshot_files(str(proj), task.files)
                content_fp = GLOBAL_CACHE.content_fingerprint(str(proj), task.files)
            GLOBAL_CACHE.put(
                task,
                {
                    "method": method,
                    "worker": worker,
                    "skill": skill,
                    "stdout": stdout,
                    "commit": commit,
                    "summary": stdout[:500] if stdout else f"{method}:{worker or skill}",
                    "result": result,
                },
                project_root=str(proj),
                file_snapshots=file_snapshots,
                content_fp=content_fp,
            )
        except Exception as exc:
            try:
                self.log.write(f"cache put: {exc}")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Skill fast-path (deterministic tools, no LLM)
    # ------------------------------------------------------------------

    def _try_skill(self, task: Task, proj) -> dict | None:
        """Попробовать решить задачу через rule-based skill.

        Returns skill payload dict on success, None if LLM path needed.
        """
        message = (getattr(task, "message", None) or "").strip()
        if not message:
            return None

        # Deterministic micro-refactor (stdlib/libcst) before broader skills
        try:
            from skills.refactorer import try_refactor_from_message
            rr = try_refactor_from_message(
                message, list(task.files or []), root=str(proj) if proj else None
            )
            if rr is not None and rr.ok and rr.changed:
                self._emit(
                    "SKILL",
                    f"refactor:{rr.message[:80]}",
                    task_id=task.id,
                    worker=self.worker_id,
                    payload={"skill": "refactorer", "path": rr.path},
                )
                return {
                    "skill": "refactorer",
                    "result": rr.as_dict(),
                    "method": "skill",
                }
        except Exception:
            pass

        try:
            if not is_enabled("skills"):
                raise ImportError("skills disabled")
            from skills.skills import SKILLS
            from pathlib import Path as _Path
            # Reuse global registry; point tools at current project
            try:
                SKILLS.tools.root = _Path(str(proj)) if proj else SKILLS.tools.root
            except Exception:
                pass
            skills = SKILLS
        except ImportError:
            return None

        skill_name = None
        try:
            meta = task.metadata if isinstance(getattr(task, "metadata", None), dict) else {}
            sug = str(meta.get("suggested_skill") or "").strip()
            if sug:
                skill_name = sug
        except Exception:
            pass
        if not skill_name:
            skill_name = skills.match(message)
        if not skill_name:
            return None

        # Prefer project root as path; scope files via files=
        # (passing a single file as path broke skills that join root/files)
        path_arg = str(proj) if proj else None

        self._emit(
            "SKILL",
            f"skill:{skill_name}",
            task_id=task.id,
            worker=self.worker_id,
            payload={"skill": skill_name, "message": message[:500]},
        )
        try:
            self.log.write(f"skill try: {skill_name} task={task.id}")
        except Exception:
            pass

        kwargs: dict = {}
        if path_arg is not None:
            kwargs["path"] = path_arg

        # files for skills that accept an explicit file list
        _SKILLS_WITH_FILES = {
            "check_syntax", "convert_print_to_logging", "rename_symbol",
            "extract_function", "strip_trailing_whitespace", "find_bare_except",
            "normalize_newlines", "ensure_utf8_coding", "count_lines",
            "add_basic_type_hints", "add_docstring_stubs", "dead_code_report",
        }
        if task.files and skill_name in _SKILLS_WITH_FILES:
            kwargs["files"] = list(task.files)

        # refactor skills parse params from message (old/new name, line range)
        if skill_name in ("rename_symbol", "extract_function"):
            kwargs["message"] = message

        if skill_name == "search_symbol":
            import re as _re
            m = _re.search(
                r"(?:найди|поищи|find|search(?:\s+for)?)\s+(?:где\s+)?(?:используется\s+)?[`'\"]?([a-zA-Z_][\w.]{2,})[`'\"]?",
                message.lower(),
            )
            if m:
                kwargs["pattern"] = m.group(1)
            else:
                return None

        import time as _time
        _skill_t0 = _time.monotonic()
        result = skills.execute(skill_name, **kwargs)
        if not result.get("success"):
            try:
                self.log.write(f"skill fail: {skill_name} {result.get('error')}")
            except Exception:
                pass
            try:
                from utils.metrics import GLOBAL_METRICS
                GLOBAL_METRICS.record_skill(skill_name, success=False, matched=True)
            except Exception:
                pass
            return None

        payload = result.get("result")
        # Heuristic: empty / no-op skills fall through to LLM
        if skill_name == "cleanup_imports":
            if isinstance(payload, dict) and int(payload.get("fixed") or 0) == 0:
                # nothing to fix — still success if no error
                if payload.get("error"):
                    return None
        if skill_name == "find_todos" and not payload:
            # empty list is valid result
            pass

        latency = 0.0
        try:
            import time as _time
            latency = max(0.0, _time.monotonic() - float(locals().get("_skill_t0") or _time.monotonic()))
        except Exception:
            latency = 0.0
        return {
            "ok": True,
            "skill": skill_name,
            "result": payload,
            "method": "skill",
            "latency": latency,
        }

    def _finalize_skill_result(self, task: Task, skill_payload: dict, proj) -> str:
        """Завершить задачу, решённую skill'ом (DONE).

        Guard от 2026-09-29. Реальный путь завершения (этот метод, а не
        копия в rp_skills_stage): задача «add file hello.py» уходила в
        навык по слову в тексте, навык успешно отрабатывал, не создав файл,
        и метод завершал её DONE БЕЗ верификации. Задача исчезала как
        успешная, файл не появлялся.

        Правило: DONE только если skill реально изменил файлы
        (files_changed / skill_result.files). Иначе — пустой статус,
        и вызывающий продолжает нормальный worker/LLM-путь.
        """
        skill_name = skill_payload.get("skill", "unknown")
        detail = skill_payload.get("result")

        changed = list(skill_payload.get("files_changed") or [])
        if not changed:
            sr = skill_payload.get("skill_result")
            if isinstance(sr, dict):
                changed = list(sr.get("files") or [])
        if not changed:
            try:
                self.log.write(
                    f"skill {skill_name}: файлов не изменено — задача не решена, "
                    f"передаю в worker/LLM (task={getattr(task, 'id', '?')})"
                )
            except Exception:
                pass
            return ""
        # Непустой список путей — ещё не доказательство: файл может быть
        # применён, но остаться пустым (заглушка) -> ложный DONE мимо
        # finish_task. Проверяем содержимое так же, как в cache-пути.
        real_files = self._non_empty_files(changed)
        if not real_files:
            try:
                self.log.write(
                    f"skill {skill_name}: путь есть, но файлы пусты "
                    f"{changed!r} — задача не решена, передаю в worker/LLM "
                    f"(task={getattr(task, 'id', '?')})"
                )
            except Exception:
                pass
            return ""

        summary = ""
        try:
            import json
            summary = json.dumps(detail, ensure_ascii=False, default=str)[:2000]
        except Exception:
            summary = str(detail)[:2000]

        # B3: тот же контракт, что и в cache-пути. Навык отработал, но
        # раньше это не считалось доказательством: DONE писался мимо
        # finish_task. Теперь терминал идёт через контракт, а verified
        # подтверждён проверкой реальных файлов выше.
        state = self.finish_task(
            task,
            "DONE",
            {
                "worker": "skill",
                "skill": skill_name,
                "method": "skill",
                "result": detail,
                "stdout": summary,
                "restored_from_skill": True,
                # Те же два поля, что и в cache-пути: ok даёт execution_ok,
                # short_circuit помечает детерминированный путь. Иначе
                # skill-DONE демотится в ERROR (DEV-001).
                "ok": True,
                "short_circuit": "skill_success",
                "verified": True,
                "verify_ok": True,
                "verification": {
                    "ok": True,
                    # report_from_dict смотрит только на 'passed'.
                    "passed": True,
                    "source": "skill_apply",
                    "files_checked": len(real_files),
                },
                "changed_files": list(real_files),
            },
            emit=False,
        )
        if state != "DONE":
            try:
                self.log.write(
                    f"skill {skill_name}: контракт не дал DONE ({state}) — "
                    f"task={getattr(task, 'id', '?')}"
                )
            except Exception:
                pass
            return state
        try:
            self.report.record("DONE", "skill", task.attempts)
        except Exception:
            pass
        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record_task(
                task, "skill", True, 0.0, status="DONE")
        except Exception:
            pass
        try:
            self._maybe_update_project_memory(task, success=True)
        except Exception:
            pass
        try:
            self.log.log_task_done(
                task.id, "skill", 0.0, 0, channel=task.channel)
        except Exception:
            pass
        try:
            self.log.task(task.channel, task.id, "skill", f"ГОТОВО[skill:{skill_name}]")
        except Exception:
            pass
        self._emit(
            "DONE",
            f"skill:{skill_name}",
            task_id=task.id,
            worker="skill",
            payload={
                "skill": skill_name,
                "method": "skill",
                "attempts": task.attempts,
                "result_preview": summary[:400],
            },
        )
        return "DONE"



# ---------------------------------------------------------------------------
# Runtime orchestration
# ---------------------------------------------------------------------------
try:
    from skills.test_runner import TestRunner
except ImportError:
    from skills.test_runner import TestRunner  # type: ignore
from .workers import load_workers
from providers import load_providers, FreeCapacityManager
from utils.stream import StreamNormalizer
from .dynamicpool import build_dynamic_workers, emit_pool_event
from .dispatcher_lock import DispatcherLock
from safety.project_lock import ProjectLock, FileLockSet
from .dedupe import DedupeRegistry, task_fingerprint
from utils.budget import GLOBAL_BUDGET, GLOBAL_TRACKER
from utils.metrics import GLOBAL_METRICS

try:
    from core.config import _int as _cfg_int
    # Default 1: one shared git worktree — parallel root tasks without worktrees = FS chaos
    MAX_PARALLEL_PROJECTS = max(1, _cfg_int("AGENTBUS_MAX_PARALLEL_PROJECTS", 1))
except Exception:
    MAX_PARALLEL_PROJECTS = 1


