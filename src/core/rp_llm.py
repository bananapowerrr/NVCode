# -*- coding: utf-8 -*-
"""LLM / worker execution pipeline.

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
    from core.config import (
        DEFAULT_CHANNEL, PROJECT_ROOT, resolve_project, BUS_ROOT,
        MAX_ATTEMPTS, WORKER_TIMEOUT, VERIFY_FAIL_MAX,
    )
except Exception:  # pragma: no cover
    DEFAULT_CHANNEL = "gpt"
    PROJECT_ROOT = None
    MAX_ATTEMPTS = 3
    WORKER_TIMEOUT = 600
    VERIFY_FAIL_MAX = 3
    def resolve_project(x):
        return x
    BUS_ROOT = Path(".")

try:
    from core.router import select_executor
except Exception:  # pragma: no cover
    select_executor = None  # type: ignore

try:
    from core.executor import ExecutionResult
except Exception:  # pragma: no cover
    ExecutionResult = None  # type: ignore

try:
    from safety.gitops import GitOps, GitRun
except Exception:  # pragma: no cover
    GitOps = None  # type: ignore
    GitRun = None  # type: ignore

try:
    from core.repair import decide_failure, categorize
except Exception:  # pragma: no cover
    decide_failure = None  # type: ignore
    categorize = None  # type: ignore

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
    from intelligence.context import ContextBuilder
except Exception:  # pragma: no cover
    ContextBuilder = None  # type: ignore

try:
    from core.project import ProjectContext
except Exception:  # pragma: no cover
    ProjectContext = None  # type: ignore

def _task_baseline(root, task) -> dict:
    """Снимок ТОЛЬКО файлов задачи до старта воркера: {rel: (size, sha1)}.

    Раньше для проверки «что-то изменилось» брался весь `git status` проекта.
    Это неверно: в дереве всегда есть чужие правки (в т.ч. правки самого
    рантайма), и они удовлетворяли guard, поэтому задача без единого
    своего изменения получала DONE (s-short-035301: 11 посторонних файлов
    в changed_files, целевой файл 0 Б).
    """
    import hashlib
    out: dict = {}
    try:
        base = Path(root)
        for f in (getattr(task, "files", None) or []):
            rel = str(f).replace("\\", "/")
            try:
                p = base / rel
                data = p.read_bytes() if p.is_file() else None
            except OSError:
                data = None
            if data is None:
                out[rel] = (None, None)
            else:
                out[rel] = (len(data), hashlib.sha1(data).hexdigest())
    except Exception:
        return {}
    return out


def _task_changed(root, task, baseline) -> list[str]:
    """Файлы задачи, реально изменившиеся после снимка baseline.

    Пустой файл изменением не считается: Executor._ensure_target_files
    создаёт заглушку 0 Б до старта воркера. Пустой Python-файл синтаксически
    валиден, поэтому и `py_compile` на нём проходит — без этой отбраковки
    «проверка успешна» означала бы «воркер ничего не сделал».
    """
    import hashlib
    if not baseline:
        return []
    out: list[str] = []
    base = Path(root)
    for rel, (size, sha) in baseline.items():
        try:
            p = base / rel
            data = p.read_bytes() if p.is_file() else None
        except OSError:
            data = None
        if not data:
            continue
        if (len(data), hashlib.sha1(data).hexdigest()) == (size, sha):
            continue
        out.append(rel)
    return out


def _explicit_verified(result) -> bool:
    """Есть ли в результате явный положительный сигнал верификации."""
    d = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
    if d.get("verified") is True or d.get("tests_passed") is True:
        return True
    ver = d.get("verification")
    if isinstance(ver, dict) and ver.get("ok") is True:
        return True
    return d.get("verify") in (True, "PASS", "pass", "ok")


def _changed_paths(rt, *, project, task) -> list[str]:
    """Файлы, реально изменённые в рабочем дереве проекта.

    Проверка по git status, а не по словам в stdout: модель может
    написать «файл создан» в тексте, не создав его.
    """
    try:
        import subprocess
        root = str(project or "")
        if not root:
            return []
        r = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20,
        )
        if r.returncode != 0:
            return []
        out = []
        for line in (r.stdout or "").splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1]
            if not path or path in out:
                continue
            # Пустой файл — это заглушка, которую Executor создаёт ДО
            # старта воркера (см. Executor._ensure_target_files), а не
            # результат работы. Считать его изменением нельзя, иначе
            # guard обманывается собственным же артефактом: aider ушёл в
            # чат, файл остался пустым, а git status показывает «изменение».
            try:
                fp = Path(project) / path
                if fp.is_file() and fp.stat().st_size == 0:
                    continue
            except OSError:
                pass
            out.append(path)
        return out
    except Exception:
        return []


class RPLlmMixin:

    def _llm_policy_offline_state(self, task) -> tuple:
        """Return (policy, offline_flag) for worker filtering."""
        policy = None
        offline = None
        try:
            from core.policy import load_policy
            policy = load_policy()
        except Exception:
            policy = None
        try:
            from core.fallback import is_offline
            offline = is_offline()
            if policy is not None and not policy.allow_cloud:
                offline = True
            if offline:
                msg = "сеть недоступна / AGENTBUS_OFFLINE — только local backends"
                if policy is not None and not policy.allow_cloud:
                    msg = f"policy={policy.name}: только local backends"
                try:
                    self._emit("OFFLINE", msg, task_id=task.id, worker=self.worker_id)
                except Exception:
                    pass
        except Exception:
            offline = None
        return policy, offline

    def _llm_filter_pool(self, tried: list, policy, offline, failure_kind: str | None):
        """Build ranked worker pool excluding tried."""
        pool = [w for w in self.workers if w.name not in tried and self._worker_provider_ok(w)]
        try:
            if policy is not None:
                from core.policy import filter_workers_by_policy
                pool = filter_workers_by_policy(pool, policy)
        except Exception:
            pass
        try:
            from core.fallback import filter_for_offline, order_candidates
            pool = filter_for_offline(pool, offline=offline)
            if failure_kind:
                ordered = order_candidates(
                    pool, tried=tried, failure_kind=failure_kind, offline=offline
                )
                if ordered:
                    pool = ordered
        except Exception:
            pass
        # DEV-004: switch-backend / prefer-local gate for worker fallback
        try:
            from core.worker_fallback import allow_worker_fallback, filter_fallback_pool

            meta = getattr(getattr(self, "_current_task", None), "metadata", None)
            meta = meta if isinstance(meta, dict) else {}
            fb = allow_worker_fallback(
                kind=str(failure_kind or meta.get("failure_kind") or ""),
                status=str(meta.get("worker_result", {}).get("status", "") if isinstance(meta.get("worker_result"), dict) else ""),
                fallback_kind=str(meta.get("fallback_kind") or ""),
                worker_result=meta.get("worker_result") if isinstance(meta.get("worker_result"), dict) else None,
                recovery_decision=meta.get("recovery_decision") if isinstance(meta.get("recovery_decision"), dict) else None,
            )
            if fb.get("allowed"):
                pool = filter_fallback_pool(
                    pool,
                    tried=tried,
                    kind=str(failure_kind or ""),
                    offline=offline,
                ) or pool
        except Exception:
            pass
        return pool



    def _llm_classify_exec_error(self, result) -> tuple[str, str, str]:
        """Map ExecutionResult flags → (err_type, event_name, health_fail_status)."""
        fail_status = "LOOP" if getattr(result, "loop_error", False) else "ERROR"
        if result.timed_out:
            err_type = "TIMEOUT"
        elif getattr(result, "loop_error", False):
            err_type = "LOOP"
        elif getattr(result, "rate_limit_error", False):
            err_type = "RATE_LIMIT"
        elif getattr(result, "billing_error", False):
            err_type = "BILLING"
        else:
            err_type = fail_status
        if result.timed_out:
            evt = "TIMEOUT"
        elif getattr(result, "loop_error", False):
            evt = "LOOP"
        elif getattr(result, "rate_limit_error", False):
            evt = "RATE_LIMIT"
        else:
            evt = "ERROR"
        return err_type, evt, fail_status

    def _llm_record_worker_failure(
        self, worker, task, result, error: str, err_type: str, fail_status: str,
        *, complexity: int, task_type: str,
    ) -> None:
        """Budget/metrics/health/ranker/capacity after a failed worker attempt."""
        # R4 worker failure contract: classify once, reuse for health/capacity
        # and for the recovery decision (decide_from_task_row reads
        # metadata.worker_outcome).
        worker_outcome = None
        try:
            from core.worker_failure_contract import classify_execution_outcome

            worker_outcome = classify_execution_outcome(result, error_text=error)
            self._last_worker_result = worker_outcome
            meta = getattr(task, "metadata", None)
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["worker_outcome"] = worker_outcome
            if worker_outcome.get("kind"):
                meta["failure_kind"] = str(worker_outcome["kind"])
            meta["prefer_local"] = bool(worker_outcome.get("prefer_local"))
            task.metadata = meta
        except Exception:
            pass
        try:
            from utils.budget import GLOBAL_TRACKER
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_TRACKER.record_error(
                worker.name, err_type, provider=worker.provider)
            GLOBAL_METRICS.record("llm_fail")
            GLOBAL_METRICS.record_task(
                task, worker.name, False,
                getattr(result, "latency", 0.0) or 0.0,
                status=err_type, error_type=err_type)
            self.log.log_task_fail(
                task.id, worker.name, err_type, task.attempts,
                channel=task.channel, detail=error[-200:])
        except Exception:
            pass
        self.health.failure(
            worker.name, error, result.timed_out,
            status=fail_status,
            billing_error=bool(getattr(result, "billing_error", False)))
        try:
            self.ranker.learn(worker.harness, worker.provider, worker.model,
                              ok=False, latency=result.latency,
                              complexity=complexity, task_type=task_type)
        except Exception:
            pass
        try:
            pkey = f"{worker.provider}:{worker.model}" if worker.model else worker.provider
            self.capacity.record_text_error(pkey, error, worker.provider, worker.model)
        except Exception:
            pass


    def _llm_emit_worker_fallback(self, tried: list[str], worker) -> None:
        """Record FALLBACK event when switching workers mid-task."""
        if len(tried) <= 1:
            return
        try:
            self._emit(
                "FALLBACK",
                f"{tried[-2]} → {worker.name}",
                task_id=getattr(self, "_current_task_id", None) or "",
                worker=worker.name,
                payload={
                    "from": tried[-2],
                    "to": worker.name,
                    "tried": list(tried),
                    "reason": "next_worker_after_failure",
                },
            )
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record("worker_fallback")
        except Exception:
            pass

    def _llm_build_worker_message(self, worker, message: str, attempt_messages: dict, ctx) -> str:
        """Resolve per-worker message and inject tool gateway block."""
        worker_message = attempt_messages.get(worker.name, message)
        try:
            from core.tool_registry import UnifiedToolGateway
            root = str(getattr(ctx, "root", ctx) or ".")
            worker_message = UnifiedToolGateway(root).inject_text_block(
                worker_message, worker, allow_write=True
            )
        except Exception:
            pass
        return worker_message

    def _llm_preflight_swap(self, worker, tried: list[str]):
        """If local CLI/runtime is dead, switch to suggested alternate worker.

        Returns the (possibly new) worker instance, or None to skip this slot.
        """
        try:
            from core.preflight import preflight_worker, suggest_alternate
            ok_pf, reason_pf = preflight_worker(worker)
            if ok_pf:
                return worker
            alt = suggest_alternate(worker, self.workers)
            try:
                self._emit(
                    "PREFLIGHT",
                    f"{worker.name} недоступен ({reason_pf})"
                    + (f" → {alt.name}" if alt is not None else ""),
                    task_id=str(getattr(self, "_current_task_id", "") or ""),
                    worker=worker.name,
                )
            except Exception:
                pass
            if alt is None or alt.name in tried:
                try:
                    self.health.end_task(
                        worker.name, ok=False,
                        error=f"preflight: {reason_pf}", status="ERROR")
                except Exception as exp:
                    try:
                        self.log.write(f"health.end_task: {exp}")
                    except Exception:
                        pass
                return None
            try:
                self.health.end_task(
                    worker.name, ok=False,
                    error=f"preflight: {reason_pf}", status="ERROR")
            except Exception as exp:
                try:
                    self.log.write(f"health.end_task: {exp}")
                except Exception:
                    pass
            if not self.health.begin_task(alt.name):
                return None
            tried.append(alt.name)
            return alt
        except Exception:
            return worker


    def _verify_success(self, task, ctx, changed):
        """Выполняет verify-команды задачи на success-пути.

        Раньше этот путь вообще не проверял результат: при exit 0 задача
        сразу получала DONE, а _save() вычислял verified=False, что
        неотличимо от «проверка провалилась».

        Если verify-команд нет, проверяем синтаксис изменённых .py файлов
        через py_compile. Если проверить нечего — возвращаем True с
        явной пометкой, а не молчаливый False.
        """
        import sys
        from core.verify import run_command
        from core.runtime_ops import VERIFY_TIMEOUT

        root = str(getattr(ctx, "root", None) or self.context.root)
        commands = list(getattr(task, "verify", None) or [])

        if not commands:
            py_files = [p for p in (changed or [])
                        if p.endswith(".py") and (self.context.root / p).is_file()]
            if not py_files:
                return True, "no_verify_commands: проверяемых файлов нет"
            # Без кавычек: core.verify._argv() не снимает кавычки, поэтому
            # `py_compile "f.py"` передал бы имя файла вместе с кавычками
            # (Errno 22), а `python -c "..."` выполнился бы как строковый
            # литерал и всегда вернул бы 0.
            commands = [f"{sys.executable} -m py_compile {p}" for p in py_files]
            detail_prefix = "default_py_compile"
        else:
            detail_prefix = "task_verify"

        for command in commands:
            check = run_command(command, root, VERIFY_TIMEOUT)
            if not check.ok:
                return False, f"{detail_prefix}: {command}\n{check.output[-4000:]}"
        return True, f"{detail_prefix}: {len(commands)} команда(ы) пройдены"

    def _stage_llm_pipeline(
        self, raw: dict, task, proj, ctx, tests, gitops, cbuilder, task_type: str
    ) -> str | None:
        """Select workers, execute, verify, gitops, finalize."""
        message, complexity, abs_files = self._stage_prepare_llm_context(
            raw, task, proj, ctx, cbuilder, task_type
        )
        tried: list[str] = []
        attempted = False
        attempt_messages: dict[str, str] = {}
        if task.executor:
            attempt_messages[task.executor] = message
        result = None
        # P0: dirty worktree policy (park/stash/branch/allow)
        early_git = self._ensure_clean_worktree(gitops, task)
        if early_git is not None:
            return early_git
        before_snapshot = gitops.snapshot() if (gitops and gitops.is_repo()) else None
        if self._deferred_capacity(task, complexity):
            self._rollback_task(gitops, before_snapshot, task)
            return "DEFERRED"

        _fb_kind: str | None = None
        _fb_policy, _fb_offline = self._llm_policy_offline_state(task)

        for _ in range(len(self.workers)):
            pool = self._llm_filter_pool(tried, _fb_policy, _fb_offline, _fb_kind)
            if not pool:
                break
            # First attempt may still honor task.executor; later — ranker on biased pool
            req = task.executor if not tried else ""
            worker = select_executor(pool, self.health, raw, requested=req,
                                     ranker=self.ranker, capacity=self.capacity)
            # Причины отсечки воркеров: без этого явный `executor` мог
            # молча не выполниться (aider_local в c-long-040046).
            try:
                from core.router import LAST_SKIPS
                if LAST_SKIPS:
                    self.log.write(
                        f"select_executor: skips={LAST_SKIPS} requested={req or '-'}")
                if req and worker is not None and getattr(worker, 'name', '') != req:
                    self.log.write(
                        f"select_executor: REQUESTED {req} unavailable "
                        f"({LAST_SKIPS.get(req, 'filtered')}) -> fallback {worker.name}")
            except Exception:
                pass
            if worker is None:
                if req:
                    try:
                        from core.router import LAST_SKIPS
                        self.log.write(
                            f"select_executor: requested_executor_unavailable: {req} "
                            f"({LAST_SKIPS.get(req, 'filtered')})")
                    except Exception:
                        pass
                break
            tried.append(worker.name)
            if not self.health.begin_task(worker.name):
                continue
            if len(tried) > 1:
                try:
                    self._current_task_id = task.id
                except Exception:
                    pass
                self._llm_emit_worker_fallback(tried, worker)
            attempted = True
            try:
                from utils.metrics import GLOBAL_METRICS
                GLOBAL_METRICS.record("llm_call")
            except Exception:
                pass
            self.log.task(task.channel, task.id, worker.name, "ЗАПУСК")
            try:
                self.log.log_task_start(
                    task.id, worker.name, complexity,
                    channel=task.channel, task_type=task_type)
            except Exception:
                pass
            worker_message = self._llm_build_worker_message(
                worker, message, attempt_messages, ctx
            )
            exec_timeout = min(worker.timeout, WORKER_TIMEOUT)
            commit_sha = ""
            try:
                # Hot-swap: soft preflight — if local runtime/CLI dead, try alternate
                try:
                    self._current_task_id = task.id
                except Exception:
                    pass
                swapped = self._llm_preflight_swap(worker, tried)
                if swapped is None:
                    continue
                worker = swapped
                self._emit("START", f"{worker.harness}/{worker.provider} · {task_type}",
                           task_id=task.id, worker=worker.name,
                           executor=worker.harness, provider=worker.provider,
                           model=worker.model,
                           payload={"complexity": complexity, "task_type": task_type,
                                    "attempt": task.attempts})
                try:
                    self._touch_task_lease(task, phase="exec")
                except Exception:
                    pass
                # Снимок файлов ЗАДАЧИ ДО старта воркера. Заглушки 0 Б
                # создаются внутри Executor уже после этого снимка, поэтому
                # пустой файл отсеется как «изменение», а настоящая правка
                # будет видна по хешу.
                try:
                    task_baseline = _task_baseline(str(ctx.root), task)
                except Exception:
                    task_baseline = {}
                # Воркеру передаём ТОЛЬКО целевые файлы задачи.
                # Раньше сюда уходил abs_files на весь проект, и aider
                # пытался прочитать файлы в cp1251: aider/io.py:475 на
                # UnicodeError печатает "Use --encoding to set the unicode
                # encoding." и возвращается молча. Таких файлов было
                # несколько, n-gram-guard видел 4 одинаковые строки подряд
                # и объявлял LOOP_ERROR — модель при этом не зацикливалась
                # (u-utf8-041524). --encoding тут не помогает: флаг задаёт
                # кодек, но не errors.
                # ctx, а не self.context: в рантайме self.context равен None
                # и обращение к self.context.root роняло задачу с
                # AttributeError: 'NoneType' object has no attribute 'root'
                # (cmp-044939) ДО запуска воркера.
                _root = Path(str(getattr(ctx, "root", None) or "."))
                _names = {str(t).replace("\\", "/").split("/")[-1]
                          for t in (getattr(task, "files", None) or [])}
                worker_files = [f for f in (abs_files or [])
                                if Path(str(f)).name in _names] or \
                    [str(f) for f in _names if (_root / f).is_file()]
                try:
                    self.log.write(
                        f"worker_files: task={task.id} passing {len(worker_files)} "
                        f"file(s) to {worker.name}: {worker_files}")
                except Exception:
                    pass
                result = self._exec_worker(
                    worker, str(ctx.root), worker_message, exec_timeout, worker_files,
                    task_id=task.id)
                try:
                    self._touch_task_lease(task, phase="post_exec")
                except Exception:
                    pass
                try:
                    from core.fallback import classify_failure
                    if result is not None and not getattr(result, "ok", True):
                        _fb_kind = classify_failure(
                            getattr(result, "stderr", "") or getattr(result, "error", ""),
                            timed_out=bool(getattr(result, "timed_out", False)),
                        )
                except Exception:
                    pass

                # Language self-correction: one extra pass if prose drifted to English
                try:
                    from safety.language_guard import needs_language_repair, language_repair_message
                    meta_lr = task.metadata if isinstance(task.metadata, dict) else {}
                    already = bool(meta_lr.get("_language_repaired"))
                    sample = (result.stdout or "")[-4000:]
                    if result.ok and sample and not already and needs_language_repair(sample):
                        meta_lr["_language_repaired"] = True
                        task.metadata = meta_lr
                        fix = language_repair_message()
                        nl = chr(10)
                        repair_msg = (
                            worker_message.rstrip()
                            + nl + nl
                            + fix
                            + nl + nl
                            + "Previous output (rewrite prose only):"
                            + nl
                            + sample[:2000]
                        )
                        try:
                            self._emit(
                                "LANGUAGE_REPAIR",
                                "english drift — one self-correction pass",
                                task_id=task.id,
                                worker=worker.name,
                            )
                        except Exception:
                            pass
                        result = self._exec_worker(
                            worker, str(ctx.root), repair_msg, exec_timeout, abs_files,
                            task_id=task.id,
                        )
                except Exception:
                    pass
                plan = (gitops.plan_commit(before_snapshot, task.files)
                        if (gitops and before_snapshot is not None) else None)
                if result.ok:
                    message, attempt_messages, commit_sha = self._phase_verify_and_commit(
                        result=result,
                        task=task,
                        ctx=ctx,
                        tests=tests,
                        worker=worker,
                        gitops=gitops,
                        before_snapshot=before_snapshot,
                        plan=plan,
                        message=message,
                        attempt_messages=attempt_messages,
                    )
                if result.ok:
                    # Fail-closed: harness вернул exit 0, но это не значит,
                    # что задача решена. opencode в режиме «только план»
                    # печатал [PLAN] и выходил с кодом 0 -> DONE на пустом
                    # файле (e2e-031910: test_e2e.py = 0 Б).
                    # DONE допустим только при реальных изменениях в дереве
                    # либо при явном положительном сигнале верификации.
                    changed = _task_changed(str(ctx.root), task, task_baseline)
                    verified_ok = _explicit_verified(result)
                    if not changed and not verified_ok:
                        reason = "no_changes_and_no_verification"
                        try:
                            self.log.write(
                                f"{worker.name}: {reason} — DONE недопустим, "
                                f"task={task.id} (файлы не изменены)"
                            )
                        except Exception:
                            pass
                        # 7B не справился — по замыслу подключаем локальную
                        # 1.5B мета-модель: она разбивает задачу на атомарные
                        # подзадачи. Родитель честно уходит в DEFERRED, дети
                        # падают в очередь как PENDING (fail-closed сохранён).
                        meta_dec: dict[str, Any] = {}
                        try:
                            from skills.meta_decompose import try_decompose_failed_task
                            meta_dec = try_decompose_failed_task(
                                self, task, failure_reason=reason,
                            ) or {}
                        except Exception as decomp_err:
                            try:
                                self.log.write(
                                    f"meta_decompose failed (defer без декомпозиции): {decomp_err}"
                                )
                            except Exception:
                                pass
                            meta_dec = {}
                        return self.finish_task(
                            task, "DEFERRED",
                            {"error": reason, "worker": worker.name,
                             "attempts": task.attempts,
                             "changed_files": [],
                             "decomposition_required": True,
                             **meta_dec},
                            error=reason,
                        )
                    try:
                        self._reset_verify_fails(task)
                    except Exception:
                        pass
                    try:
                        from utils.metrics import GLOBAL_METRICS
                        meta_v = task.metadata if isinstance(getattr(task, "metadata", None), dict) else {}
                        lvl = int(meta_v.get("verify_ladder") or meta_v.get("verify_max_level") or 0)
                        GLOBAL_METRICS.record_verify_ladder(success=True, level=lvl)
                    except Exception:
                        pass
                    self.health.success(worker.name, latency=result.latency)
                    try:
                        self.ranker.learn(worker.harness, worker.provider, worker.model,
                                          ok=True, latency=result.latency,
                                          complexity=complexity, task_type=task_type)
                    except Exception:
                        pass
                    # Проблема 1: harness вернул exit 0, но verify-команды
                    # из задачи на success-пути НЕ выполнялись. Из-за этого
                    # _save() вычислял verified=False, хотя файл мог быть
                    # вполне рабочим: отсутствие проверки выдавалось за
                    # провал проверки. Теперь проверяем явно.
                    verify_ok, verify_detail = self._verify_success(task, ctx, changed)
                    if not verify_ok:
                        reason = "verification failed"
                        try:
                            self.log.write(
                                f"{worker.name}: {reason} task={task.id}\n{verify_detail}"
                            )
                        except Exception:
                            pass
                        return self.finish_task(
                            task, "ERROR",
                            {"error": reason, "worker": worker.name,
                             "attempts": task.attempts,
                             "changed_files": changed,
                             "verified": False,
                             "verification": {"ok": False, "detail": verify_detail}},
                            error=reason,
                        )
                    return self._llm_success_finalize(
                        task=task, proj=proj, ctx=ctx, worker=worker, result=result,
                        gitops=gitops, before_snapshot=before_snapshot,
                        commit_sha=commit_sha, verify_ok=verify_ok,
                        verify_detail=verify_detail, changed=changed,
                    )
            except Exception as exc:
                result = ExecutionResult(
                    False, stderr=f"Внутренняя ошибка: {type(exc).__name__}: {exc}")
            finally:
                self.health.end_task(worker.name)

            error = result.stderr or result.stdout or "ошибка исполнителя"
            err_type, evt, fail_status = self._llm_classify_exec_error(result)
            self._llm_record_worker_failure(
                worker, task, result, error, err_type, fail_status,
                complexity=complexity, task_type=task_type,
            )
            self._emit(evt, error[-300:], task_id=task.id, worker=worker.name,
                       executor=worker.harness, provider=worker.provider, model=worker.model,
                       payload={"timed_out": result.timed_out,
                                "loop_error": bool(getattr(result, "loop_error", False)),
                                "attempt": task.attempts, "task_type": task_type})
            dec = decide_failure(error, task.attempts, MAX_ATTEMPTS, result.timed_out)
            if dec.fix_prompt:
                for w in self.workers:
                    if w.name != worker.name:
                        attempt_messages[w.name] = dec.fix_prompt
            self.log.task(task.channel, task.id, worker.name,
                          f"ОШИБКА[{dec.category}]: {error[-200:]}")
            self._record_lesson(task, error, worker=worker.name, category=dec.category)
            self._record_semantic(task, success=False, worker=worker.name, solution=error[-400:])

        return self._llm_workers_exhausted(
            task=task, gitops=gitops, before_snapshot=before_snapshot,
            result=result, attempted=attempted,
        )

    def _llm_success_finalize(
        self, task, proj, ctx, worker, result, gitops, before_snapshot,
        commit_sha: str, verify_ok: bool, verify_detail: str, changed: list,
    ) -> str:
        """Success-путь _stage_llm_pipeline (TD-012): тело перенесено дословно.

        Финализация DONE: queue.finish, bus.move, _save, отчёты/метрики/кэш,
        semantic-память, write_progress, git-cleanup. Возвращает "DONE".
        """
        try:
            self.queue.finish(task.id, self.worker_id, "DONE",
                              result.stdout or commit_sha or "", "")
        except Exception as exc:
            self.log.write(f"finish: {exc}")
        self.bus.move(task.channel, "processing", "done", f"{task.id}.json")
        before_sha = before_snapshot.head if before_snapshot else ""
        run = GitRun(task_id=task.id, before_sha=before_sha,
                     after_sha=commit_sha or before_sha,
                     committed=bool(commit_sha), commit_sha=commit_sha,
                     tests_passed=bool(verify_ok), executor=worker.name,
                     duration=result.latency)
        self._save(task, "done", {"worker": worker.name, "git": run.to_dict(),
                                  "stdout": (result.stdout or "")[-4000:],
                                  "verified": bool(verify_ok),
                                  "changed_files": changed,
                                  "verification": {"ok": bool(verify_ok),
                                                   "detail": verify_detail}})
        self.report.record("DONE", worker.name, task.attempts)
        try:
            self._explain_and_learn("worker", task, worker=worker.name, success=True,
                complexity=int(getattr(task, "complexity", 3) or 3))
            try:
                from utils.cost_tracker import GLOBAL_COST
                from utils.pipeline_events import task_done as _pev_done, diff_policy as _pev_diff
                toks = int(getattr(result, "tokens", 0) or 0)
                if not toks:
                    toks = max(100, len(str(getattr(task, "message", "") or "")) // 3)
                GLOBAL_COST.record(
                    str(task.id),
                    worker=str(worker.name or ""),
                    provider=str(getattr(worker, "provider", "") or ""),
                    model=str(getattr(worker, "model", "") or ""),
                    tokens_total=toks,
                )
                _pev_done(
                    str(task.id),
                    str(worker.name or ""),
                    duration=float(getattr(result, "latency", 0) or 0),
                    commit=commit_sha or "",
                )
            except Exception:
                pass
            try:
                if not is_enabled("diff_preview"):
                    raise ImportError("diff_preview disabled")
                from safety.diff_engine import queue_from_task_result, queue_pending
                from safety.diff_policy import assess_risk
                try:
                    _diff_lines = 0
                    for _fp in (files_payload or {}).values():
                        if isinstance(_fp, dict):
                            _diff_lines += str(_fp.get("new") or "").count("\n")
                        else:
                            _diff_lines += str(_fp or "").count("\n")
                except Exception:
                    _diff_lines = 0
                _decision = assess_risk(
                    files=list(getattr(task, "files", None) or []),
                    complexity=int(getattr(task, "complexity", 3) or 3),
                    diff_lines=_diff_lines,
                    message=str(getattr(task, "message", "") or ""),
                )
                try:
                    meta_d = dict(getattr(task, "metadata", None) or {})
                    meta_d["diff_policy"] = _decision.to_dict()
                    task.metadata = meta_d
                    try:
                        from utils.pipeline_events import diff_policy as _pev_diff
                        _pev_diff(str(task.id), _decision.to_dict())
                    except Exception:
                        pass
                except Exception:
                    pass
                if _decision.action == "require_approval":
                    # still queue for UI — never silent auto-apply on HIGH
                    pass
                payload = getattr(task, "raw", None) or {}
                if not isinstance(payload, dict):
                    payload = {}
                proot = getattr(proj, "root", None) if proj else None
                queued = queue_from_task_result(payload, project_root=proot)
                if not queued and gitops and before_snapshot is not None:
                    try:
                        changed = []
                        if hasattr(gitops, "changed_files_since"):
                            changed = gitops.changed_files_since(before_snapshot) or []
                        if not changed and hasattr(gitops, "diff_names"):
                            changed = gitops.diff_names() or []
                        # Restrict to task.files if non-empty to reduce parallel bleed
                        tf = [str(f).replace("\\", "/") for f in (task.files or [])]
                        if tf:
                            changed = [c for c in changed if str(c).replace("\\", "/") in tf
                                       or any(str(c).replace("\\", "/").endswith(x) for x in tf)]
                        files_payload = {}
                        head_sha = ""
                        try:
                            head_sha = getattr(before_snapshot, "head", "") or ""
                        except Exception:
                            head_sha = ""
                        for rel in list(changed or [])[:30]:
                            rel_s = str(rel).replace("\\", "/")
                            # new content from working tree
                            if hasattr(gitops, "read_working"):
                                new_c = gitops.read_working(rel_s)
                            else:
                                p = Path(proot or ".") / rel_s
                                new_c = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
                            # original from baseline HEAD (empty if new file)
                            if hasattr(gitops, "show_at_head"):
                                orig = gitops.show_at_head(rel_s, head_sha or None)
                            else:
                                orig = ""
                            files_payload[rel_s] = {"original": orig, "new": new_c}
                        if files_payload:
                            queue_pending(str(task.id), files_payload, project_root=proot or Path("."))
                            meta2 = dict(payload.get("metadata") or {})
                            meta2["file_changes"] = [
                                {"file": k, "original": v.get("original", ""), "new": v.get("new", "")}
                                for k, v in files_payload.items()
                            ]
                            payload["metadata"] = meta2
                            try:
                                task.raw = payload
                            except Exception:
                                pass
                    except Exception as _diff_exc:
                        try:
                            self.log.write(f"diff capture: {_diff_exc}")
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass
        self.report.record_provider(worker.provider, "DONE")
        self.report.commits += int(bool(commit_sha))
        try:
            from utils.budget import GLOBAL_BUDGET, GLOBAL_TRACKER
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_BUDGET.record(worker.name, tokens=0)
            GLOBAL_TRACKER.record_request(
                worker.name, tokens=0,
                provider=worker.provider, latency=result.latency)
            GLOBAL_METRICS.record_task(
                task, worker.name, True, result.latency, status="DONE")
        except Exception:
            pass
        try:
            self.log.log_task_done(
                task.id, worker.name, result.latency, 0,
                channel=task.channel)
        except Exception:
            pass
        self.log.task(task.channel, task.id, worker.name, "ГОТОВО")
        self._emit("DONE", "успех", task_id=task.id, worker=worker.name,
                   executor=worker.harness, provider=worker.provider,
                   model=worker.model, duration=result.latency,
                   payload={"commit": commit_sha, "attempts": task.attempts})
        try:
            from utils.task_trace import GLOBAL_TRACES
            GLOBAL_TRACES.complete(str(task.id), "DONE")
        except Exception:
            pass
        self._cache_put(
            task, proj,
            method="llm",
            worker=worker.name,
            stdout=(result.stdout or "")[-4000:],
            commit=commit_sha or "",
            snapshot=True,
        )
        self._record_semantic(
            task, success=True, worker=worker.name,
            solution=(result.stdout or "")[-800:],
        )
        self._maybe_update_project_memory(task, success=True)
        try:
            self._maybe_run_file_sentinel(task, proj)
        except Exception:
            pass
        try:
            from intelligence.pev_loop import write_progress
            n = 0
            try:
                import json as _json
                from pathlib import Path as _P
                pj = _P(str(ctx.root)) / ".agentbus" / "current_plan.json"
                if pj.is_file():
                    n = len((_json.loads(pj.read_text(encoding="utf-8")).get("steps") or []))
            except Exception:
                n = 0
            write_progress(
                ctx.root,
                status="done",
                done_steps=list(range(1, n + 1)),
                task_id=str(task.id),
            )
        except Exception:
            pass
        try:
            self._cleanup_task_git_branch(gitops, task)
            try:
                proot = str(getattr(ctx, "root", None) or getattr(task, "project", "") or "")
                if proot:
                    self._maybe_cleanup_git_worktree(task, proot)
            except Exception:
                pass
        except Exception:
            pass
        return "DONE"

    def _llm_workers_exhausted(self, task, gitops, before_snapshot,
                               result, attempted: bool) -> str:
        """Хвост _stage_llm_pipeline (TD-012): тело перенесено дословно.

        Все исполнители исчерпаны: retry/verify-бюджет, карантин, paid-gate,
        DEFERRED через finish_task. Возвращает финальный статус задачи.
        """
        error = ("Все исполнители не справились" if attempted else "Нет доступного исполнителя")
        worker_err = (result and (result.stderr or result.stdout)) or ""
        if not worker_err:
            worker_err = str((task.metadata or {}).get("prev_failure") or "")
        last_err = worker_err or error
        cat = categorize(last_err)

        # Task Retry Budget: consecutive verify/syntax fails OR total attempts
        verify_exhausted = False
        try:
            verify_exhausted = self._verify_budget_exhausted(task)
        except Exception:
            verify_exhausted = False
        if verify_exhausted and cat in ("CODE_ERROR", "TEST_ERROR", "UNKNOWN_ERROR", "ENV_ERROR"):
            cat = cat or "CODE_ERROR"
        if task.attempts >= MAX_ATTEMPTS or verify_exhausted:
            final = "BLOCKED" if cat in ("CODE_ERROR", "TEST_ERROR", "UNKNOWN_ERROR") else "ERROR"
            if verify_exhausted and task.attempts < MAX_ATTEMPTS:
                final = "BLOCKED"
                last_err = (
                    f"VERIFY_BUDGET_EXHAUSTED (>{VERIFY_FAIL_MAX} consecutive verify fails): "
                    + (last_err or "")
                )[:4000]
            elif task.attempts >= MAX_ATTEMPTS:
                last_err = (
                    f"RETRY_BUDGET_EXHAUSTED (attempts>={MAX_ATTEMPTS}): "
                    + (last_err or "")
                )[:4000]
            qpath = None
            try:
                qpath = self._quarantine_exhausted_task(
                    task,
                    reason=last_err,
                    category=cat,
                    gitops=gitops,
                    before_snapshot=before_snapshot,
                    extra={
                        "final": final,
                        "verify_exhausted": bool(verify_exhausted),
                        "max_attempts": MAX_ATTEMPTS,
                        "consecutive_verify_fails": int(
                            (task.metadata or {}).get("consecutive_verify_fails") or 0
                        ),
                    },
                )
            except Exception as qexc:
                try:
                    self.log.write(f"quarantine enforcer: {qexc}")
                except Exception:
                    pass
                try:
                    self._rollback_task(gitops, before_snapshot, task)
                except Exception:
                    pass
            try:
                return self.finish_task(
                    task,
                    final,
                    {
                        "error": last_err,
                        "attempts": int(task.attempts or 0),
                        "category": cat,
                        "quarantine": str(qpath) if qpath else "",
                        "quarantined": True,
                    },
                    error=last_err,
                )
            except Exception as exc:
                self.log.write(f"quarantine enforcer: {exc}")
                return "ERROR"

        self._rollback_task(gitops, before_snapshot, task)
        gate_reason = self._paid_gate_deferred_reason()
        if gate_reason:
            # Повтор здесь бессмысленен: локальных исполнителей нет, а облака
            # закрыты политикой. Платить за прогон 7B ради того же DEFERRED
            # нельзя — возвращаем задачу в deferred без backoff.
            try:
                self.log.write(f"deferred by paid gate: {gate_reason}")
            except Exception:
                pass
            # Локальных исполнителей нет, облака закрыты → по замыслу это
            # работа для 1.5B мета: она режет задачу на атомарные шаги.
            meta_dec_paid: dict[str, Any] = {}
            try:
                from skills.meta_decompose import try_decompose_failed_task
                meta_dec_paid = try_decompose_failed_task(
                    self, task, failure_reason="paid_gate_blocked",
                ) or {}
            except Exception as decomp_err:
                try:
                    self.log.write(
                        f"meta_decompose failed (defer без декомпозиции): {decomp_err}"
                    )
                except Exception:
                    pass
                meta_dec_paid = {}
            return self.finish_task(
                task,
                "DEFERRED",
                {
                    "error": gate_reason,
                    "attempts": int(task.attempts or 0),
                    "category": cat,
                    "decomposition_required": True,
                    "paid_workers_blocked": True,
                    **meta_dec_paid,
                },
                error=gate_reason,
            )
        self._schedule_retry(task, last_err)
        return self.finish_task(
            task,
            "DEFERRED",
            {
                "error": last_err,
                "attempts": int(task.attempts or 0),
                "category": cat,
            },
            error=last_err,
        )

    def _paid_gate_deferred_reason(self) -> str:
        """Причина DEFERRED, если локальных исполнителей не осталось.

        Архитектура задумана как «1.5B мета (декомпозиция) → 7B исполнитель».
        Когда 7B недоступен, следующий по замыслу шаг — локальная декомпозиция,
        а не прыжок в платное облако (AGENTBUS_ALLOW_PAID=0). Здесь мы честно
        сообщаем об этом и не запускаем бессмысленный повтор.

        Пустая строка = блокировка не при чём, действует обычный retry.
        """
        try:
            from core.router import LAST_SKIPS
        except Exception:
            return ""
        try:
            skips = dict(LAST_SKIPS or {})
        except Exception:
            return ""
        if not skips:
            return ""
        blocked = sorted(n for n, r in skips.items() if "paid_disallowed" in str(r))
        if not blocked:
            return ""
        # Платные были отброшены, но если хоть один локальный кандидат был
        # отобран по другой причине — это не ситуация «нет локальных».
        for name, reason in skips.items():
            if name in blocked:
                continue
            if str(reason) in ("disabled", "plan_only_no_file_write"):
                continue
            return ""
        return (
            "Требуется локальная декомпозиция: свободных локальных исполнителей нет "
            f"(7B недоступен), платные облака заблокированы AGENTBUS_ALLOW_PAID=0 "
            f"[{', '.join(blocked)}]. Ожидаемый путь — мета-модель 1.5B "
            "(skills/task_decomposer.py) для разбиения на атомарные подзадачи."
        )

    # ------------------------------------------------------------------
    # Project index + lessons
    # ------------------------------------------------------------------


