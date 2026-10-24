# -*- coding: utf-8 -*-
"""RuntimeOps — bus/git/verify/lease helpers (isolated from orchestration)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from .bus import FileBus
from .config import (
    BUS_ROOT, CHANNELS, DEFAULT_CHANNEL, MAX_ATTEMPTS, RETRY_DELAY_SECONDS,
    LEASE_SECONDS, POLL_SECONDS, PROJECT_ROOT, WORKER_TIMEOUT, VERIFY_TIMEOUT,
    GIT_ENABLED, REPAIR_ENABLED, REPORT_DIR, LOG_ROOT, resolve_project, USE_DYNAMIC,
    VERIFY_FAIL_MAX, DIRTY_GIT_POLICY,
)
from eventbus import BUS, AgentEvent
from eventbus.jsonl import JsonlSink
from eventbus.console import ConsoleSink
from intelligence.context import ContextBuilder
from .executor import Executor, ExecutionResult
from safety.gitops import GitOps, GitRun, build_commit_message
from safety.health import HealthRegistry
from .logger import Logger
from .project import ProjectContext
from .ranking import AdaptiveRanker, make_key, infer_task_type
from intelligence.report import NightlyReport
from .tasks import Task
from .router import select_executor, task_complexity
from .repair import decide_failure, categorize
from .workers import Worker

try:
    from skills.test_runner import TestRunner, _pytest_status
except ImportError:  # pragma: no cover
    TestRunner = None  # type: ignore
    def _pytest_status(*_a, **_k):  # type: ignore
        return "unknown"

try:
    from core.feature_flags import is_enabled
except Exception:  # pragma: no cover
    def is_enabled(name: str, default: bool = True) -> bool:
        return default

# reroute when predicted latency exceeds this share of the worker timeout
_LATENCY_TIMEOUT_RATIO = 0.80

class RuntimeOps:
    """Mixin: provider checks, exec, verify, recover, rollback."""


    def _set_phase(self, task, phase: str, **extra) -> None:
        """Write processing snapshot with human-readable phase for UI poll."""
        try:
            payload = {"phase": phase, "status": "PROCESSING"}
            payload.update(extra)
            self._save(task, "processing", payload)
        except Exception:
            try:
                self.log.write(f"phase {phase}: save failed")
            except Exception:
                pass

    def _provider_of(self, worker: Worker):
        for p in self.providers:
            if p.id == worker.provider:
                return p if p.is_usable() else None
        return None

    def _provider_has_key(self, prov) -> bool:
        if getattr(prov, "api_key", "") or "":
            return True
        ke = getattr(prov, "api_key_env", "") or ""
        return bool(ke) and bool(os.getenv(ke, ""))

    def _is_foreign(self, worker: Worker) -> bool:
        return bool(worker.provider) and worker.provider not in ("ollama", "local", "zen", "")

    def _worker_provider_ok(self, worker: Worker) -> bool:
        if worker.provider in ("", "local", "ollama", "zen"):
            return True
        prov = self._provider_of(worker)
        return prov is not None and self._provider_has_key(prov)

    def _exec_worker(self, worker: Worker, project: str, message: str,
                     timeout: int, files: list[str] | None,
                     *, task_id: str = "") -> ExecutionResult:
        # latency prediction: reroute if estimated >> timeout
        complexity = getattr(self, "_latency_task_complexity", 3)
        task_type = getattr(self, "_latency_task_type", "general")
        try:
            key = make_key(getattr(worker, "harness", "cli"), worker.provider, worker.model)
            prof = self.ranker.profiles.get(key)
            estimated = prof.estimated_latency(complexity, task_type) if prof is not None else 0.0
        except Exception:
            estimated = 0.0

        chosen = worker
        if estimated > 0 and timeout and estimated > timeout * _LATENCY_TIMEOUT_RATIO:
            try:
                alternative = select_executor(
                    self.workers, self.health,
                    {"message": message, "files": list(files or []),
                     "metadata": {"complexity": complexity}, "executor": ""},
                    ranker=self.ranker, capacity=self.capacity,
                )
            except Exception:
                alternative = None
            if alternative is not None and alternative.name != worker.name:
                self._emit(
                    "LATENCY_REROUTE",
                    f"{worker.name}: прогноз {estimated:.0f}с > {timeout * _LATENCY_TIMEOUT_RATIO:.0f}с, → {alternative.name}",
                    task_id=task_id, worker=worker.name,
                    payload={"from": worker.name, "to": alternative.name,
                             "estimated_latency": estimated, "timeout": timeout,
                             "complexity": complexity, "task_type": task_type},
                )
                chosen = alternative
            else:
                self._emit(
                    "LATENCY_WARNING",
                    f"{worker.name}: прогноз {estimated:.0f}с близок к таймауту {timeout}с",
                    task_id=task_id, worker=worker.name,
                    payload={"estimated_latency": estimated, "timeout": timeout,
                             "complexity": complexity, "task_type": task_type},
                )

        self.normalizer.begin()

        def _line(line: str) -> None:
            self.normalizer.feed(
                line, task_id=task_id, worker=chosen.name,
                executor=chosen.harness, provider=chosen.provider, model=chosen.model)
            # Heartbeat while worker streams output → reclaim won't kill long honest jobs
            try:
                now = time.monotonic()
                last = float(getattr(self, "_lease_touch_mono", 0.0) or 0.0)
                if (now - last) >= 20.0:
                    self._lease_touch_mono = now
                    t_obj = getattr(self, "_current_task", None)
                    if t_obj is not None:
                        self._touch_task_lease(t_obj, phase="stream")
            except Exception:
                pass

        self.executor.on_line = _line
        result: ExecutionResult | None = None
        try:
            if self._is_foreign(chosen):
                prov = self._provider_of(chosen)
                if prov is not None and self._provider_has_key(prov):
                    result = self.executor.run_foreign(
                        chosen, prov, project, message, timeout, files=files)
                elif prov is None:
                    result = ExecutionResult(
                        False, stderr=f"провайдер '{chosen.provider}' недоступен")
                else:
                    result = ExecutionResult(
                        False, stderr=f"нет api_key для '{chosen.provider}'")
            else:
                result = self.executor.run(chosen, project, message, timeout, files=files)
            # WorkerExecution contract snapshot (does not alter FSM / DONE gate)
            try:
                from core.worker_execution import from_worker_result_object
                from core.worker_failure_contract import to_worker_result
                contract = from_worker_result_object(
                    result,
                    worker=str(getattr(chosen, "name", "") or ""),
                    model=str(getattr(chosen, "model", "") or ""),
                )
                self._last_execution_contract = contract
                wr = to_worker_result(
                    result,
                    worker=str(getattr(chosen, "name", "") or ""),
                    model=str(getattr(chosen, "model", "") or ""),
                )
                self._last_worker_result = wr
                t_obj = getattr(self, "_current_task", None)
                if t_obj is not None:
                    meta = t_obj.metadata if isinstance(getattr(t_obj, "metadata", None), dict) else {}
                    meta = dict(meta)
                    meta["execution_result"] = {
                        k: contract[k]
                        for k in ("ok", "worker", "model", "timed_out", "error", "latency_sec")
                        if k in contract
                    }
                    meta["worker_result"] = {
                        k: wr.get(k)
                        for k in (
                            "status", "kind", "ok", "timed_out", "exit_code",
                            "error", "switch_backend", "prefer_local",
                        )
                    }
                    t_obj.metadata = meta
            except Exception:
                pass
            return result
        finally:
            self.executor.on_line = None

    def _save(self, task: Task, state: str, result: dict) -> None:
        status = {"done": "DONE", "errors": "ERROR", "deferred": "DEFERRED",
                  "processing": "CLAIMED"}.get(state)
        if status:
            task.status = status
        payload = {**task.to_dict(), "result": result}
        # Execution evidence on terminal folders (contract: Runtime sets terminal_state)
        if state in ("done", "errors", "deferred"):
            try:
                from core.execution_evidence import (
                    evidence_from_task_payload,
                    merge_evidence_into_payload,
                )
                ev = evidence_from_task_payload(
                    payload, terminal_state=status or "", state_folder=state
                )
                # DONE requires verification marker when result carries verify outcome
                if status == "DONE":
                    res = result if isinstance(result, dict) else {}
                    verified = bool(
                        res.get("verified")
                        or res.get("verify_ok")
                        or (res.get("verification") or {}).get("ok")
                        or res.get("verify") in (True, "PASS", "pass", "ok")
                    )
                    if res.get("error") and not verified:
                        verified = False
                    # Итог верификации должен попасть в файл задачи.
                    # Раньше здесь считалось локально и уходило только в
                    # evidence, поэтому result.verified оставался None
                    # (в run-030427 так и было при успешном DONE).
                    res["verified"] = verified
                    ev.setdefault("verification", {})
                    if isinstance(ev["verification"], dict) and "ok" not in ev["verification"]:
                        ev["verification"]["ok"] = verified
                if status == "ERROR":
                    try:
                        from core.recovery_controller import (
                            annotate_row_with_recovery,
                            run_recovery,
                        )
                        # apply_plan=False at save-time: Chat/product_surface applies plan
                        # with project_root; here we only record decision + mechanism.
                        outcome = run_recovery(payload, apply_plan=False, save_plan=False)
                        payload = annotate_row_with_recovery(payload, outcome)
                    except Exception:
                        try:
                            from core.recovery_decision import decide_from_task_row
                            decision = decide_from_task_row(payload)
                            meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                            meta = dict(meta)
                            meta["recovery_decision"] = decision
                            meta["recovery_mechanism"] = {
                                "retry": "existing_reclaim_or_retry_states",
                                "replan": "plan_layer_only",
                                "ask_user": "ui_only",
                                "stop": "none",
                            }.get(str(decision.get("action") or ""), "none")
                            payload["metadata"] = meta
                        except Exception:
                            pass
                payload = merge_evidence_into_payload(payload, ev)
            except Exception as exc:
                try:
                    self.log.write(f"execution_evidence: {exc}")
                except Exception:
                    pass
        self.bus.write(task.channel, state, f"{task.id}.json",
                       json.dumps(payload, ensure_ascii=False, indent=2))

    def _apply_runtime_decision(self, task, state: str, res: dict, error: str) -> str:
        """DEV-001: evidence-backed decision (may demote DONE). Returns state."""
        try:
            from core.runtime_decision import decide_terminal, evidence_snapshot
            contract = getattr(self, "_last_execution_contract", None) or {}
            snap = evidence_snapshot(
                task_id=str(getattr(task, "id", "") or ""),
                worker=str(res.get("worker") or (contract.get("worker") if isinstance(contract, dict) else "") or ""),
                model=str(res.get("model") or (contract.get("model") if isinstance(contract, dict) else "") or ""),
                attempt=int(getattr(task, "attempts", 0) or 0),
                exit_code=res.get("exit_code", res.get("code")),
                exec_ok=res.get("ok") if "ok" in res else (False if res.get("error") else None),
                timed_out=bool(res.get("timed_out") or (contract.get("timed_out") if isinstance(contract, dict) else False)),
                changed_files=list(res.get("changed_files") or []),
                stdout_summary=str(res.get("stdout") or "")[-500:],
                stderr_summary=str(res.get("stderr") or "")[-500:],
                error=str(res.get("error") or error or ""),
                verification=res.get("verification") if isinstance(res.get("verification"), dict) else None,
            )
            if res.get("verified") is True or res.get("verify_ok") is True or res.get("tests_passed") is True:
                snap["verification"] = dict(snap.get("verification") or {})
                snap["verification"]["ok"] = True
            snap["terminal_state"] = state  # claimed — decide_terminal will re-check
            decision = decide_terminal(snap, allow_retry=False)
            if decision.get("contradictions"):
                res["contradictions"] = decision["contradictions"]
            if state == "DONE" and decision.get("terminal_state") != "DONE":
                state = "ERROR"
                res.setdefault("error", decision.get("reason") or "evidence_rejected_done")
                res["verified"] = False
            res["runtime_decision"] = {
                "terminal_state": decision.get("terminal_state"),
                "reason": decision.get("reason"),
                "contradictions": decision.get("contradictions") or [],
            }
        except Exception:
            pass
        return state

    def _fold_execution_contract(self, res: dict) -> None:
        """R2: fold last ExecutionResult contract into terminal result."""
        try:
            contract = getattr(self, "_last_execution_contract", None)
            if isinstance(contract, dict):
                for k in ("worker", "model", "timed_out", "latency_sec", "error"):
                    if contract.get(k) not in (None, "") and not res.get(k):
                        res[k] = contract[k]
                if contract.get("changed_files") and not res.get("changed_files"):
                    res["changed_files"] = list(contract["changed_files"])
                res.setdefault("exec_ok", contract.get("ok"))
        except Exception:
            pass

    def _stash_evidence_preview(self, task, res: dict, state: str) -> None:
        """Build execution evidence preview stashed for the _save merge."""
        try:
            from core.execution_evidence import evidence_from_execution_result

            meta = getattr(task, "metadata", None)
            meta = meta if isinstance(meta, dict) else {}
            ev2 = evidence_from_execution_result(
                res,
                task_id=str(getattr(task, "id", "") or ""),
                attempt=int(getattr(task, "attempts", 0) or 0),
                plan_step=str(meta.get("plan_step_id") or meta.get("step_id") or ""),
                terminal_state=state,
                started_at=str(meta.get("started_at") or ""),
                worker_fallback=str(res.get("worker") or ""),
                model_fallback=str(res.get("model") or ""),
            )
            # stash for _save merge (metadata.execution_evidence also built in _save)
            res["_evidence_preview"] = {
                k: ev2.get(k)
                for k in (
                    "task_id", "worker", "model", "attempt", "terminal_state",
                    "error", "timed_out", "latency_sec", "exec_ok",
                )
                if k in ev2 or k in ("timed_out", "latency_sec", "exec_ok")
            }
        except Exception:
            pass

    def _record_terminal_metrics(self, task, res: dict, state: str, error: str) -> None:
        """GLOBAL_METRICS on every terminal transition (+ attempts on result)."""
        # Раньше счётчики обновляли только rp_* (кэш/LLM/skills), а сам
        # terminal path — нет: из-за этого в лог уходило вечное
        # "metrics tasks=0 ok=0 err=0", хотя задачи падали с ERROR.
        try:
            from utils.metrics import GLOBAL_METRICS
            GLOBAL_METRICS.record_task(
                task,
                str(res.get("worker") or getattr(task, "worker", "") or ""),
                state == "DONE",
                float((res.get("latency_sec") or 0.0) or 0.0),
                status=state,
                error_type=str(res.get("error") or error or "")[:80] if state == "ERROR" else "",
            )
        except Exception:
            pass

        # attempts on result for evidence
        try:
            res.setdefault("attempts", int(getattr(task, "attempts", 0) or 0))
        except Exception:
            pass

    def _annotate_last_result(self, task, res: dict, state: str, error: str) -> None:
        """last_result + worker_result on every terminal (WIRE-003: always on ERROR)."""
        try:
            meta_lr = dict(getattr(task, "metadata", None) or {})
            meta_lr["last_result"] = {
                "error": str(res.get("error") or error or "")[:2000],
                "ok": res.get("ok"),
                "verified": res.get("verified"),
                "verification": res.get("verification") if isinstance(res.get("verification"), dict) else {},
                "changed_files": list(res.get("changed_files") or [])[:40],
                "worker": str(res.get("worker") or ""),
                "timed_out": bool(res.get("timed_out")),
                "terminal_state": state,
            }
            wr = getattr(self, "_last_worker_result", None)
            if not isinstance(wr, dict) or not wr:
                # synthesize when ERROR without prior _exec_worker snapshot
                try:
                    from core.worker_failure_contract import to_worker_result
                    ver = res.get("verification") if isinstance(res.get("verification"), dict) else None
                    ver_ok = None
                    if isinstance(ver, dict) and "ok" in ver:
                        ver_ok = bool(ver.get("ok"))
                    elif res.get("verified") is True:
                        ver_ok = True
                    elif state == "ERROR" and (res.get("error") or error):
                        ver_ok = False
                    wr = to_worker_result(
                        res if isinstance(res, dict) else None,
                        error_text=str(res.get("error") or error or ""),
                        worker=str(res.get("worker") or getattr(task, "worker", "") or ""),
                        model=str(res.get("model") or ""),
                        verification_ok=ver_ok,
                    )
                    self._last_worker_result = wr
                except Exception:
                    wr = None
            if isinstance(wr, dict):
                meta_lr["worker_result"] = {
                    k: wr.get(k)
                    for k in (
                        "status", "kind", "ok", "timed_out", "exit_code",
                        "error", "switch_backend", "prefer_local", "worker", "model",
                    )
                    if k in wr
                }
                meta_lr["failure_kind"] = str(wr.get("kind") or meta_lr.get("failure_kind") or "")
                # also mirror into terminal result for Chat/recovery consumers
                if state == "ERROR":
                    res.setdefault("worker_result", meta_lr["worker_result"])
            if not hasattr(task, "metadata") or task.metadata is None:
                task.metadata = {}
            if isinstance(task.metadata, dict):
                task.metadata.update(meta_lr)
            else:
                task.metadata = meta_lr
        except Exception:
            pass

    def _move_to_terminal_folder(self, task, from_folder: str, folder: str) -> None:
        """bus move processing → terminal folder FIRST.

        FileBus.move is rename-based, so moving after _save would overwrite the
        terminal payload with the stale processing snapshot (status=CLAIMED +
        phase placeholder), losing error/evidence/verification.
        """
        try:
            fname = f"{task.id}.json"
            self.bus.move(task.channel, from_folder, folder, fname)
        except Exception as exc:
            try:
                self.log.write(f"finish_task move: {exc}")
            except Exception:
                pass

    def _drop_task_lease(self, task, from_folder: str) -> None:
        """Lease sidecar must not survive a terminal transition."""
        try:
            if from_folder:
                lease = (
                    self.bus.paths(task.channel)[from_folder]
                    / f"{task.id}.lease.json"
                )
                if lease.is_file():
                    lease.unlink()
        except Exception:
            pass

    def _queue_terminal(self, task, res: dict, state: str, error: str) -> None:
        """Report terminal state to the queue (finish for DONE, terminal otherwise)."""
        try:
            if state == "DONE":
                self.queue.finish(
                    task.id,
                    getattr(self, "worker_id", "") or "",
                    "DONE",
                    error="",
                    attempts=int(getattr(task, "attempts", 0) or 0),
                )
            else:
                self.queue.terminal(
                    task.id,
                    state,
                    error=str(res.get("error") or error or "")[:500],
                    attempts=int(getattr(task, "attempts", 0) or 0),
                )
        except Exception as exc:
            try:
                self.log.write(f"finish_task queue: {exc}")
            except Exception:
                pass

    def _emit_terminal(self, task, res: dict, state: str, folder: str) -> None:
        """Emit terminal event for UI/bus consumers."""
        try:
            msg = str(res.get("error") or state)[:300]
            self._emit(
                state,
                msg,
                task_id=getattr(task, "id", ""),
                worker=str(res.get("worker") or getattr(self, "worker_id", "") or ""),
                payload={"terminal": state, "folder": folder},
            )
        except Exception:
            pass

    def _log_terminal(self, task, res: dict, state: str, error: str) -> None:
        """Log terminal transition; ERROR must expose its reason (and traceback)."""
        try:
            tid = getattr(task, "id", "")
            self.log.write(f"finish_task {tid} → {state}")
            # Причина обязана быть видна в логе. Раньше писалось только
            # "→ ERROR", и по логу нельзя было понять, что случилось:
            # файл задачи в errors/ содержал лишь заглушку phase=prepare.
            if state == "ERROR":
                reason = str(res.get("error") or error or "").strip()
                self.log.write(
                    f"  error[{tid}]: {reason or '(пусто — причина не передана)'}"
                )
                if not reason:
                    # совсем без причины: фиксируем контекст терминала
                    self.log.write(
                        f"  context[{tid}]: phase={res.get('phase')} "
                        f"worker={res.get('worker')!r} "
                        f"exit={res.get('exit_code')!r} "
                        f"verified={res.get('verified')!r}"
                    )
                else:
                    # полный traceback, если исключение ещё живое в контексте
                    exc = sys.exc_info()[1]
                    if exc is not None:
                        self.log.write(
                            "  traceback:\n"
                            + "".join(
                                traceback.format_exception(type(exc), exc, exc.__traceback__)
                            ).rstrip()
                        )
        except Exception:
            pass

    def finish_task(
        self,
        task: "Task",
        terminal_state: str,
        result: dict | None = None,
        *,
        error: str = "",
        from_folder: str = "processing",
        emit: bool = True,
        queue_terminal: bool = True,
    ) -> str:
        """R1 unified terminal path: evidence → bus → queue → emit.

        Returns normalized terminal state (DONE/ERROR/DEFERRED).
        Does not change FSM vocabulary. Demotes DONE without verification.
        """
        from core.terminal_path import (
            FOLDER,
            build_terminal_result,
            enforce_done_contract,
        )

        res = build_terminal_result(
            error=error,
            worker=str(
                (result.get("worker") if isinstance(result, dict) else "")
                or getattr(task, "worker", "")
                or ""
            ),
            extra=dict(result or {}),
        )
        if error and not res.get("error"):
            res["error"] = str(error)[:2000]
        state, res = enforce_done_contract(terminal_state, res)

        state = self._apply_runtime_decision(task, state, res, error)
        self._fold_execution_contract(res)
        self._stash_evidence_preview(task, res, state)

        folder = FOLDER.get(state)
        if not folder:
            state = "ERROR"
            folder = "errors"
            res.setdefault("error", f"invalid_terminal:{terminal_state}")

        self._record_terminal_metrics(task, res, state, error)
        self._annotate_last_result(task, res, state, error)
        self._move_to_terminal_folder(task, from_folder, folder)
        self._drop_task_lease(task, from_folder)

        # _save LAST so the terminal payload is the one that persists
        try:
            self._save(task, folder, res)
        except Exception as exc:
            try:
                self.log.write(f"finish_task save: {exc}")
            except Exception:
                pass

        if queue_terminal:
            self._queue_terminal(task, res, state, error)
        if emit:
            self._emit_terminal(task, res, state, folder)
        self._log_terminal(task, res, state, error)
        return state


    def _verify_commands(self, task: Task, ctx=None) -> tuple[bool, str]:
        context = ctx or self.context
        for command in [*task.verify, *task.run]:
            from core.verify import run_command
            check = run_command(command, context.root, VERIFY_TIMEOUT)
            if not check.ok:
                return False, f"Команда не прошла: {command}\n{check.output[-10000:]}"
        return True, ""

    def _verification_engine_gate(self, task, ctx=None, *, execution_ok: bool = True,
                                  short_circuit: str | None = None) -> tuple[bool, str]:
        """DONE Gate via VerificationEngine.

        Восстановлено 2026-09-29 из f0444b2:src/core/runtime_ops.py.
        Метод потерялся при переносе кода в runtime.py, но его вызов
        остался в rp_verify.py:91. Из-за этого ЛЮБАЯ задача падала с
            AttributeError: 'Runtime' object has no attribute
            '_verification_engine_gate'
        то есть DONE был недостижим: задача уходила в DEFERRED с
        verification_gate_exception.

        Fail-closed по контракту: проверка не пройдена -> не DONE.

        Default: лёгкий режим — pytest не перезапускается (это уже сделал
        escalating). AGENTBUS_VERIFY_ENGINE_FULL=1 включает полный прогон.
        """
        import os
        try:
            from core.verification_engine import (
                CheckResult,
                VerificationEngine,
                VerificationReport,
                gate_done,
            )
        except Exception:
            return bool(execution_ok), "" if execution_ok else "verification_engine_unavailable"

        if short_circuit:
            ok, reason = gate_done(True, None, short_circuit=short_circuit)
            return ok, reason

        full = (os.getenv("AGENTBUS_VERIFY_ENGINE_FULL") or "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        root = getattr(ctx or getattr(self, "context", None), "root", None)

        if full:
            raw: dict = {}
            try:
                raw = task.to_dict() if hasattr(task, "to_dict") else {
                    "id": getattr(task, "id", ""),
                    "message": getattr(task, "message", ""),
                    "files": list(getattr(task, "files", None) or []),
                    "verify": list(getattr(task, "verify", None) or []),
                    "metadata": dict(getattr(task, "metadata", None) or {}),
                }
            except Exception:
                raw = {"id": getattr(task, "id", ""), "message": "x"}
            try:
                from core.verify_policy import apply_verify_policy
                raw = apply_verify_policy(raw)
            except Exception:
                pass
            try:
                report = VerificationEngine(project_root=root).run(raw, project_root=root)
            except Exception as exc:
                return False, f"verification_engine_error: {type(exc).__name__}: {exc}"
        else:
            # Доверяем escalating, но всё равно требуем execution_ok и отчёт
            report = VerificationReport(
                passed=bool(execution_ok),
                checks=[CheckResult("escalating", bool(execution_ok),
                                    detail="from_verify_escalating")],
                reason="escalating_ok" if execution_ok else "escalating_failed",
            )

        try:
            meta = dict(getattr(task, "metadata", None) or {})
            meta["verification_report"] = report.to_dict()
            task.metadata = meta
        except Exception:
            pass
        try:
            self._last_verification_report = report
        except Exception:
            pass

        passed = bool(getattr(report, "passed", False))
        reason = str(getattr(report, "reason", "") or "")
        if not passed:
            return False, reason or "verification_engine_failed"
        return True, ""

    def _verify_escalating(self, task: Task, ctx=None, tests=None,
                           worker_name: str = "") -> tuple[bool, str]:
        context = ctx or self.context
        # L0 syntax + L0.5 static anti-patterns BEFORE pytest/git
        files = list(getattr(task, "files", None) or [])
        root = getattr(context, "root", None)
        try:
            from safety.syntax_guard import guard_or_error as syntax_guard
            ok_syn, err_syn = syntax_guard(files, root=root)
            if not ok_syn:
                if worker_name:
                    try:
                        self.health.verify_failure(worker_name, err_syn[:300])
                        self._emit(
                            "SYNTAX_FAIL", err_syn[-300:],
                            task_id=getattr(task, "id", ""), worker=worker_name,
                        )
                    except Exception:
                        pass
                return False, err_syn
        except Exception:
            pass
        try:
            from safety.static_guard import guard_or_error as static_guard
            ok_st, err_st = static_guard(files, root=root)
            if not ok_st:
                if worker_name:
                    try:
                        self.health.verify_failure(worker_name, err_st[:300])
                        self._emit(
                            "STATIC_FAIL", err_st[-300:],
                            task_id=getattr(task, "id", ""), worker=worker_name,
                        )
                    except Exception:
                        pass
                return False, err_st
        except Exception:
            pass
        tr = tests or self.tests
        full = any("pytest" in c for c in task.verify) or not task.verify
        meta_v = task.metadata if isinstance(getattr(task, "metadata", None), dict) else {}
        try:
            ladder_max = int(meta_v.get("verify_max_level") or 0)
        except (TypeError, ValueError):
            ladder_max = 0
        if not task.files:
            ok, err = self._verify_commands(task, context)
        else:
            if ladder_max > 0:
                max_level = max(1, min(3, ladder_max))
            else:
                max_level = 3 if full else 2
            ok, err = True, ""
            for step in tr.run_escalating(task.files, max_level=max_level):
                if _pytest_status(step.result) in ("FAIL", "INFRA"):
                    ok = False
                    err = f"[{step.level}] {step.command}\n{step.result.output[-10000:]}"
                    break
            if ok:
                ok, err = self._verify_commands(task, context)
        if worker_name:
            try:
                if ok:
                    self.health.verify_success(worker_name)
                else:
                    self.health.verify_failure(worker_name, err or "verify failed")
                    self._emit(
                        "VERIFY_FAIL", (err or "verify failed")[-300:],
                        task_id=getattr(task, "id", ""), worker=worker_name,
                        payload={"consecutive": self.health.state(worker_name).consecutive_verify_failures},
                    )
            except Exception:
                pass
        return ok, err

    def _claim_desktop_incoming(self) -> dict | None:
        """Claim one task from desktop/incoming.

        Deferred or stuck desktop (PC chat) tasks must be recoverable without
        the phone file-bus. On a successful move the file lands in
        desktop/processing, so it is not visible to the UI as pending anymore.
        """
        try:
            incoming = self.bus.paths("desktop")["incoming"]
        except Exception as exc:
            self.log.write(f"desktop channel unavailable: {exc}")
            return None
        try:
            paths = sorted(incoming.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError as exc:
            self.log.write(f"desktop incoming unreadable: {exc}")
            return None
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                self.log.write(f"битая desktop задача {path.name}: {exc}")
                try:
                    self.bus.move("desktop", "incoming", "errors", path.name)
                except Exception:
                    pass
                continue
            if not isinstance(raw, dict):
                continue
            try:
                if not self.bus.move("desktop", "incoming", "processing", path.name):
                    continue
            except Exception as exc:
                self.log.write(f"desktop move failed {path.name}: {exc}")
                continue
            claimed = dict(raw)
            claimed.setdefault("channel", "desktop")
            return claimed
        return None

    def _active_channels(self) -> list[str]:
        """Configured CHANNELS plus desktop plus on-disk isolated sub-agent channels."""
        found: list[str] = []
        seen: set[str] = set()
        # desktop = primary PC chat channel: always polled, even when
        # AGENTBUS_CHANNELS does not list it, so deferred desktop work is
        # claimable without the phone file-bus.
        for ch in list(CHANNELS) + ["desktop"]:
            if ch and ch not in seen:
                found.append(ch)
                seen.add(ch)
        try:
            bus_root = Path(getattr(self.bus, "root", None) or "")
            root = bus_root / "channels" if bus_root.is_dir() else Path()
            if not root.is_dir():
                from core.config import CHANNELS_ROOT
                root = Path(CHANNELS_ROOT)
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if not d.is_dir():
                        continue
                    name = d.name
                    if name in seen:
                        continue
                    # isolated children: gpt__sub_xxx or any configured prefix
                    if "__sub_" in name:
                        found.append(name)
                        seen.add(name)
                        continue
                    # also pick up channels that have pending work even if not in env list
                    if (d / "incoming").is_dir() or (d / "processing").is_dir():
                        # only auto-add __sub_ to avoid scanning random dirs; base channels from env
                        pass
        except Exception:
            pass
        return found

    def _claim_file_task(
        self, *, only_channels: tuple[str, ...] | list[str] | None = None
    ) -> dict | None:
        """Claim next incoming task; prefer grouper order (project/type/files).

        only_channels restricts polling to the given channels (used by the
        desktop-only path so it never picks up phone file-bus traffic).
        """
        channels = self._active_channels()
        if only_channels:
            allowed = {str(c).strip() for c in only_channels if str(c).strip()}
            channels = [c for c in channels if c in allowed]
        candidates: list[tuple[object, dict, str]] = []
        for channel in channels:
            incoming = self.bus.paths(channel)["incoming"]
            for path in sorted(incoming.glob("*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        self.log.write(f"intake reject {path.name}: task must be a JSON object")
                        try:
                            self.bus.move(channel, "incoming", "errors", path.name)
                        except Exception as move_exc:
                            self.log.write(f"intake quarantine {path.name}: {move_exc}")
                        continue
                    raw = dict(raw)
                    raw.setdefault("channel", channel)
                    candidates.append((path, raw, channel))
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    self.log.write(f"битая задача {path.name}: {exc}")
                    try:
                        self.bus.move(channel, "incoming", "errors", path.name)
                    except Exception:
                        pass
        if not candidates:
            return None
        try:
            from skills.task_grouper import GLOBAL_GROUPER
            ordered_raw = GLOBAL_GROUPER.sort_for_processing([c[1] for c in candidates])
            # map back to paths by id/message
            by_id = {}
            for path, raw, channel in candidates:
                key = str(raw.get("id") or "") or id(raw)
                by_id[key] = (path, raw, channel)
            ordered: list[tuple[object, dict, str]] = []
            used = set()
            for raw in ordered_raw:
                key = str(raw.get("id") or "")
                item = by_id.get(key)
                if item and key not in used:
                    ordered.append(item)
                    used.add(key)
            for path, raw, channel in candidates:
                key = str(raw.get("id") or "") or id(raw)
                if key not in used:
                    ordered.append((path, raw, channel))
        except Exception:
            ordered = candidates

        for path, raw, channel in ordered:
            try:
                from core.intake_pipeline import IntakeError, task_from_raw

                if not str(raw.get("id") or "").strip():
                    raise IntakeError(f"intake[{channel}]: id is required for file-bus tasks")
                task = task_from_raw(raw, source=f"filebus:{channel}", strict=True)
                if str(task.status or "").upper() == "DONE":
                    raise IntakeError(f"intake[{channel}]: terminal DONE task cannot be claimed")
                task.channel = channel
                try:
                    from core.capability_router import enrich_task_from_plugins, infer_capabilities
                    if not isinstance(task.metadata, dict):
                        task.metadata = {}
                    task.metadata = enrich_task_from_plugins(
                        task.message or "", task.metadata
                    )
                    caps = infer_capabilities(task)
                    if caps:
                        task.metadata.setdefault("capabilities", caps)
                except Exception:
                    pass
                if not self.bus.move(channel, "incoming", "processing", path.name):
                    continue
                try:
                    from core.reclaim import write_lease, compute_stuck_timeout_sec, task_complexity
                    proc = self.bus.paths(channel)["processing"] / path.name
                    td = task.to_dict()
                    write_lease(
                        proc,
                        task_id=str(task.id),
                        worker=str(task.worker or task.executor or ""),
                        complexity=task_complexity(td),
                        attempts=int(task.attempts or 0),
                        stuck_timeout_sec=compute_stuck_timeout_sec(td),
                        phase="claim",
                    )
                except Exception:
                    pass
                return task.to_dict()
            except Exception as exc:
                name = getattr(path, "name", str(path))
                self.log.write(f"intake reject {name}: {type(exc).__name__}: {exc}")
                try:
                    if not self.bus.move(channel, "incoming", "errors", name):
                        self.log.write(f"intake quarantine lost race: {name}")
                except Exception as move_exc:
                    self.log.write(f"intake quarantine {name}: {move_exc}")
                continue
        return None

    def _recover_stale_processing(self, stale_seconds: int = LEASE_SECONDS) -> int:
        """Reclaim stuck processing tasks with adaptive timeout (complexity/worker).

        Uses core.reclaim when available; falls back to fixed mtime lease.
        Returns number of successfully moved tasks.
        """
        try:
            from core.reclaim import reclaim_stuck
            from core.config import STUCK_BASE_SEC, STUCK_TIMEOUT_MAX, MAX_ATTEMPTS as _MAX_ATT
        except Exception:
            recovered = 0
            for channel in self._active_channels():
                pdir = self.bus.paths(channel)["processing"]
                now = time.time()
                for path in pdir.glob("*.json"):
                    if path.name.endswith(".lease.json"):
                        continue
                    try:
                        if now - path.stat().st_mtime <= stale_seconds:
                            continue
                        if self.bus.move(channel, "processing", "incoming", path.name):
                            recovered += 1
                    except OSError:
                        continue
            return recovered

        recovered = 0
        base = float(STUCK_BASE_SEC)
        # floor: never stricter than caller stale_seconds for simple tasks
        base = max(base, min(float(stale_seconds), base * 2))
        for channel in self._active_channels():
            paths = self.bus.paths(channel)
            try:
                results = reclaim_stuck(
                    processing_dir=paths["processing"],
                    incoming_dir=paths["incoming"],
                    errors_dir=paths["errors"],
                    channel=channel,
                    bus_move=self.bus.move,
                    max_attempts=int(_MAX_ATT),
                    base_sec=base,
                    max_sec=float(STUCK_TIMEOUT_MAX),
                )
            except Exception as exc:
                self.log.write(f"reclaim {channel}: {exc}")
                continue
            for item in results:
                if item.get("moved"):
                    recovered += 1
                    self.log.write(
                        f"RECLAIM {item.get('action')} {item.get('file')} "
                        f"age={item.get('age_sec')}s timeout={item.get('timeout_sec')}s "
                        f"attempts={item.get('attempts')}"
                    )
                    try:
                        self._emit(
                            "RECLAIM",
                            f"{item.get('action')} · {item.get('file')}",
                            task_id=str(item.get("file") or "").replace(".json", ""),
                            worker=self.worker_id,
                            payload=item,
                        )
                    except Exception:
                        pass
        return recovered

    def _touch_task_lease(self, task: "Task", phase: str = "work") -> None:
        """Refresh processing lease heartbeat for long-running tasks.

        TD-006: pass worker_id so touch_lease refuses to refresh a foreign lease.
        """
        try:
            from core.reclaim import touch_lease, write_lease, compute_stuck_timeout_sec, task_complexity
            p = self.bus.paths(task.channel)["processing"] / f"{task.id}.json"
            if not p.is_file():
                return
            wid = str(
                getattr(task, "worker", "")
                or getattr(task, "executor", "")
                or getattr(self, "worker_id", "")
                or ""
            )
            lease = p.with_name(p.stem + ".lease.json")
            if not lease.is_file():
                write_lease(
                    p,
                    task_id=str(task.id),
                    worker=wid,
                    complexity=task_complexity(task.to_dict()),
                    attempts=int(getattr(task, "attempts", 0) or 0),
                    stuck_timeout_sec=compute_stuck_timeout_sec(task.to_dict()),
                    phase=phase,
                )
            else:
                touch_lease(p, phase=phase, expected_worker_id=wid or None)
            try:
                p.touch()
            except OSError:
                pass
        except Exception:
            pass

    def _recover_deferred(self) -> int:
        recovered = 0
        now = time.time()
        for channel in self._active_channels():
            ddir = self.bus.paths(channel)["deferred"]
            for path in list(ddir.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
                result = raw.get("result") if isinstance(raw, dict) else None
                if not isinstance(result, dict):
                    result = {}
                wake_epoch = result.get("wake_epoch")
                due = False
                if isinstance(wake_epoch, (int, float)) and wake_epoch > 0:
                    due = now >= float(wake_epoch)
                else:
                    try:
                        due = (now - path.stat().st_mtime) >= RETRY_DELAY_SECONDS
                    except OSError:
                        continue
                if not due:
                    continue
                if self.bus.move(channel, "deferred", "incoming", path.name):
                    recovered += 1
                    tid = (raw.get("id") if isinstance(raw, dict) else None) or path.stem
                    self._emit("RETRY", f"deferred→incoming · {tid}",
                               task_id=str(tid), worker=self.worker_id,
                               payload={"from": "deferred"})
        return recovered

    def _schedule_retry(self, task: Task, error: str) -> None:
        self._backoff[task.id] = time.monotonic() + RETRY_DELAY_SECONDS
        task.metadata["prev_failure"] = (error or "")[-2000:]
        try:
            self.queue.bump_attempts(task.id, task.attempts, error)
        except Exception as exc:
            self.log.write(f"bump_attempts: {exc}")

    def _flush_backoff(self) -> None:
        now = time.monotonic()
        due = [tid for tid, when in self._backoff.items() if now >= when]
        for tid in due:
            del self._backoff[tid]
            try:
                self.queue.release(tid, error="повтор после backoff")
            except Exception as exc:
                self.log.write(f"release backoff: {exc}")
        try:
            n = self._recover_deferred()
            if n:
                self.log.write(f"deferred→incoming: {n}")
        except Exception as exc:
            self.log.write(f"recover_deferred: {exc}")

    def _deferred_capacity(self, task: Task, complexity: int) -> bool:
        try:
            snap = self.capacity.deferred_snapshot()
            if not snap.get("deferred"):
                return False
        except Exception:
            return False
        delay = int(snap.get("wake_at") or 60)
        if delay > 86400:
            delay = min(3600, max(60, delay % 86400 or 60))
        delay = max(30, min(delay, 3600))
        wake_epoch = time.time() + delay
        self._emit("DEFERRED_QUOTA", f"пул недоступен, повтор ~{delay}с",
                   task_id=task.id, worker=self.worker_id,
                   payload={"wake_at": delay, "wake_epoch": wake_epoch})
        self._backoff[task.id] = time.monotonic() + delay
        self.bus.move(task.channel, "processing", "deferred", f"{task.id}.json")
        self._save(task, "deferred", {
            "error": "DEFERRED_QUOTA", "attempts": task.attempts,
            "category": "RATE_LIMIT", "wake_at": delay, "wake_epoch": wake_epoch,
        })
        return True


    def _cleanup_task_git_branch(self, gitops, task) -> None:
        """Drop agentbus/task-* branch after success or quarantine (disk hygiene)."""
        if gitops is None or not getattr(gitops, "is_repo", lambda: False)():
            return
        try:
            info = gitops.cleanup_task_branch(str(getattr(task, "id", "") or ""))
            if info.get("deleted"):
                try:
                    self.log.write(f"git branch cleanup: {info.get('deleted')}")
                except Exception:
                    pass
            # opportunistic prune of old agentbus branches
            try:
                pruned = gitops.prune_stale_agentbus_branches(keep=8)
                if pruned:
                    self.log.write(f"git prune agentbus branches: {pruned[:5]}")
            except Exception:
                pass
        except Exception as exc:
            try:
                self.log.write(f"git branch cleanup: {exc}")
            except Exception:
                pass


    def _maybe_prepare_git_worktree(self, task: "Task", project_path: str) -> str:
        """If AGENTBUS_GIT_WORKTREE=1, isolate task in a git worktree; return path to use."""
        try:
            from safety.worktree import worktree_enabled, WorktreeManager
            if not worktree_enabled():
                return project_path
            wm = WorktreeManager(project_path)
            info = wm.add(str(getattr(task, "id", "") or "task"))
            if info.get("ok") and info.get("path"):
                try:
                    if not isinstance(task.metadata, dict):
                        task.metadata = {}
                    task.metadata["git_worktree"] = info["path"]
                    task.metadata["git_worktree_branch"] = info.get("branch", "")
                except Exception:
                    pass
                try:
                    self.log.write(f"worktree: {info.get('reason')} → {info['path']}")
                except Exception:
                    pass
                return str(info["path"])
        except Exception as exc:
            try:
                self.log.write(f"worktree: {exc}")
            except Exception:
                pass
        return project_path

    def _maybe_cleanup_git_worktree(self, task: "Task", project_path: str) -> None:
        try:
            from safety.worktree import worktree_enabled, WorktreeManager
            if not worktree_enabled():
                return
            meta = task.metadata if isinstance(getattr(task, "metadata", None), dict) else {}
            if not meta.get("git_worktree"):
                return
            wm = WorktreeManager(project_path)
            # keep branch for review; remove worktree dir to free disk
            wm.remove(str(getattr(task, "id", "") or ""), delete_branch=False)
            try:
                pruned = wm.prune_stale(keep=6)
                if pruned:
                    self.log.write(f"worktree prune: {len(pruned)}")
            except Exception:
                pass
        except Exception as exc:
            try:
                self.log.write(f"worktree cleanup: {exc}")
            except Exception:
                pass

    def _rollback_task(self, gitops, before_snapshot, task) -> list[str]:
        if gitops is None or before_snapshot is None or not gitops.is_repo():
            return []
        try:
            plan = gitops.plan_commit(before_snapshot, task.files)
            rolled = gitops.discard_task_changes(before_snapshot, plan)
        except Exception as exc:
            self.log.write(f"откат git: {exc}")
            return []
        return rolled or []


    def _ensure_clean_worktree(self, gitops, task: "Task") -> str | None:
        """Pre-flight dirty git. Returns status string if task should stop, else None."""
        if gitops is None or not getattr(gitops, "is_repo", lambda: False)():
            return None
        try:
            default_pol = DIRTY_GIT_POLICY
        except NameError:
            default_pol = "park"
        try:
            from core.task_safety import resolve_git_policy
            policy = resolve_git_policy(task, default=default_pol)
        except Exception:
            policy = default_pol
        try:
            meta = dict(getattr(task, "metadata", None) or {})
            meta["git_policy_applied"] = policy
            task.metadata = meta
        except Exception:
            pass
        try:
            info = gitops.ensure_worktree_ready(policy=policy, task_id=str(getattr(task, "id", "")))
        except Exception as exc:
            try:
                self.log.write(f"dirty git check: {exc}")
            except Exception:
                pass
            return None
        if info.get("ok", True):
            if info.get("action") not in ("clean", "no_repo", ""):
                try:
                    self._emit(
                        "GIT_PREP",
                        str(info.get("reason") or info.get("action"))[:300],
                        task_id=getattr(task, "id", ""),
                        worker=self.worker_id,
                        payload=info,
                    )
                except Exception:
                    pass
            return None
        # park / failed
        reason = str(info.get("reason") or "dirty worktree")
        try:
            self._emit(
                "DIRTY_GIT",
                reason[:300],
                task_id=getattr(task, "id", ""),
                worker=self.worker_id,
                payload=info,
            )
        except Exception:
            pass
        try:
            meta = dict(getattr(task, "metadata", None) or {})
            meta["dirty_git"] = info
            task.metadata = meta
        except Exception:
            pass
        try:
            self.bus.move(task.channel, "processing", "deferred", f"{task.id}.json")
            self._save(
                task,
                "deferred",
                {
                    "error": reason,
                    "attempts": getattr(task, "attempts", 0),
                    "category": "DIRTY_GIT",
                    "dirty_git": info,
                },
            )
        except Exception as exc:
            try:
                self.log.write(f"dirty park: {exc}")
            except Exception:
                pass
        return "DEFERRED"

    def _bump_verify_fails(self, task: "Task", error: str = "") -> int:
        """Track consecutive verify/syntax fails across attempts. Returns new count."""
        try:
            meta = dict(getattr(task, "metadata", None) or {})
        except Exception:
            meta = {}
        n = int(meta.get("consecutive_verify_fails") or 0) + 1
        meta["consecutive_verify_fails"] = n
        if error:
            meta["last_verify_error"] = (error or "")[-500:]
        try:
            task.metadata = meta
        except Exception:
            pass
        return n

    def _reset_verify_fails(self, task: "Task") -> None:
        try:
            meta = dict(getattr(task, "metadata", None) or {})
            meta["consecutive_verify_fails"] = 0
            task.metadata = meta
        except Exception:
            pass

    def _verify_budget_exhausted(self, task: "Task") -> bool:
        try:
            meta = getattr(task, "metadata", None) or {}
            n = int(meta.get("consecutive_verify_fails") or 0)
        except Exception:
            n = 0
        try:
            limit = int(VERIFY_FAIL_MAX)
        except Exception:
            limit = 3
        return n >= limit

    def _bind(self, proj: Path):
        if proj == PROJECT_ROOT:
            return self.context, self.cbuilder, self.gitops, self.tests
        ctx = ProjectContext(proj)
        return ctx, ContextBuilder(ctx), GitOps(proj, GIT_ENABLED), TestRunner(proj, VERIFY_TIMEOUT)



# ---------------------------------------------------------------------------
# RuntimeProcess
# ---------------------------------------------------------------------------
