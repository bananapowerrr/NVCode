# -*- coding: utf-8 -*-
"""AgentBus Runtime orchestrator (thin).

Heavy logic lives in:
  - runtime_ops.py      — claim, verify, git, leases
  - runtime_process.py  — per-task pipeline stages
  - pipeline_stages.py  — pure helpers

A failure in an optional stage must not crash the process: stages use try/except
and feature_flags; Runtime.run_forever always continues the poll loop.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from .bus import FileBus
from .config import (
    BUS_ROOT, CHANNELS, DEFAULT_CHANNEL, MAX_ATTEMPTS, RETRY_DELAY_SECONDS,
    LEASE_SECONDS, POLL_SECONDS, PROJECT_ROOT, WORKER_TIMEOUT, VERIFY_TIMEOUT,
    GIT_ENABLED, REPAIR_ENABLED, REPORT_DIR, LOG_ROOT, resolve_project, USE_DYNAMIC,
    VERIFY_FAIL_MAX, DIRTY_GIT_POLICY, MAX_PARALLEL_PROJECTS,
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
from .workers import Worker, load_workers
from .dedupe import DedupeRegistry, task_fingerprint
from .dynamicpool import build_dynamic_workers, emit_pool_event
from .dispatcher_lock import DispatcherLock
from providers import load_providers, FreeCapacityManager
from skills.test_runner import TestRunner
from utils.stream import StreamNormalizer
from utils.budget import GLOBAL_BUDGET, GLOBAL_TRACKER
from utils.metrics import GLOBAL_METRICS
from safety.project_lock import ProjectLock, FileLockSet

try:
    from core.feature_flags import is_enabled
except Exception:  # pragma: no cover
    def is_enabled(name: str, default: bool = True) -> bool:
        return default

from .runtime_ops import RuntimeOps
from .runtime_process import RuntimeProcess, _NullQueue

class Runtime(RuntimeOps, RuntimeProcess):
    def __init__(self) -> None:
        self.bus = FileBus(BUS_ROOT, CHANNELS)
        self.bus.ensure()
        self.queue = _NullQueue()  # file-bus only
        self.executor = Executor()
        self.health = HealthRegistry()
        self.workers = load_workers()
        for w in self.workers:
            self.health.register(w.name, w.max_parallel)
        self.providers = load_providers()
        self.capacity = FreeCapacityManager(self.providers)
        self.ranker = AdaptiveRanker()
        for w in self.workers:
            self.ranker.register_worker(w)
        self.normalizer = StreamNormalizer()
        self.context = ProjectContext(PROJECT_ROOT) if PROJECT_ROOT else None
        self.cbuilder = ContextBuilder(self.context) if self.context else None
        self.tests = TestRunner(PROJECT_ROOT, VERIFY_TIMEOUT) if PROJECT_ROOT else None
        self.gitops = GitOps(PROJECT_ROOT, GIT_ENABLED) if PROJECT_ROOT else None
        self.worker_id = f"agentbus-{uuid.uuid4().hex[:8]}"
        self.log = Logger(LOG_ROOT / "dispatcher.log")
        self.report = NightlyReport(self.log, REPORT_DIR)
        self.budget = GLOBAL_BUDGET
        self.tracker = GLOBAL_TRACKER
        self.metrics = GLOBAL_METRICS
        self._backoff: dict[str, float] = {}
        self._running = False
        self._desktop_queue_cursor = 0
        self._hb_last = 0.0
        self._probe_last = 0.0
        self._metrics_last = 0.0
        # consolidated: project parallel + dedupe
        self.project_lock = ProjectLock(max_global=MAX_PARALLEL_PROJECTS)
        self.file_locks = FileLockSet()
        self.dedupe = DedupeRegistry()
        self._dedupe_inflight: set[str] = set()
        self._dedupe_lock = threading.Lock()
        self._latency_task_complexity = 3
        self._latency_task_type = "general"
        self._setup_eventbus()

    def _setup_eventbus(self) -> None:
        if getattr(Runtime, "_bus_attached", False):
            if getattr(self, "_console_sink", None):
                self._console_sink.status_fn = self._pool_status
            return
        Runtime._bus_attached = True
        self._jsonl_sink = JsonlSink()
        self._console_sink = ConsoleSink()
        self._console_sink.status_fn = self._pool_status
        BUS.attach(self._jsonl_sink)
        BUS.attach(self._console_sink)

    def _pool_status(self) -> list[dict]:
        by_name = {w.name: w for w in self.workers}
        rows = self.health.operator_snapshot()
        seen = set()
        out = []
        for r in rows:
            w = by_name.get(r["name"])
            if w:
                r["provider"] = w.provider
                if not self._worker_provider_ok(w):
                    r["status"] = "NO_KEY"
                    reason = ""
                    try:
                        reason = self.capacity.worker_reason(w) or ""
                    except Exception:
                        pass
                    if not reason and self._is_foreign(w):
                        reason = "нет api_key"
                    r["detail"] = (reason or "провайдер недоступен")[:80]
            else:
                r.setdefault("provider", "")
            out.append(r)
            seen.add(r["name"])
        for w in self.workers:
            if w.name not in seen:
                ok = self._worker_provider_ok(w)
                status = "AVAILABLE" if ok else "NO_KEY"
                detail = ""
                if not ok:
                    try:
                        detail = self.capacity.worker_reason(w) or "нет api_key"
                    except Exception:
                        detail = "нет api_key"
                out.append({
                    "name": w.name, "status": status,
                    "detail": detail[:80], "provider": w.provider,
                })
        return out

    def _emit(self, type_: str, message: str = "", **kw) -> None:
        try:
            kw.setdefault("ts", time.time())
            BUS.emit(AgentEvent(type=type_, message=message, **kw))
        except Exception:
            pass

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if (now - self._hb_last) < 30:
            return
        self._hb_last = now
        try:
            busy = sum(1 for w in self.workers if self.health.running(w.name))
            cool = sum(1 for w in self.workers if not self.health.available(w.name))
            self._emit("HEARTBEAT", f"жив · занято={busy} · пауза={cool}",
                       worker=self.worker_id,
                       payload={"busy": busy, "cooldown": cool, "workers": len(self.workers)})
        except Exception:
            pass

    def _probe_providers(self) -> None:
        now = time.monotonic()
        if (now - getattr(self, "_probe_last", 0.0)) < 600.0:
            return
        self._probe_last = now
        try:
            pool = self.capacity.probe_dynamic()
            if not pool:
                return
            enabled = [r for r in pool if r["ok"]]
            self._emit("SYSTEM", f"пул: {len(enabled)}/{len(pool)} доступно",
                       provider="pool", payload={"pool": pool})
        except Exception:
            pass

    def _sync_dynamic_pool(self) -> None:
        try:
            cand = build_dynamic_workers(self.providers, self.workers)
            if not cand:
                return
            emit_pool_event(cand)
            if not USE_DYNAMIC:
                return
            for w in cand:
                if w.name in {x.name for x in self.workers}:
                    continue
                if self._is_foreign(w):
                    prov = self._provider_of(w)
                    if prov is None or not self._provider_has_key(prov):
                        continue
                self.workers.append(w)
                self.health.register(w.name, w.max_parallel)
                self.ranker.register_worker(w)
        except Exception:
            pass

    def _finalize_deduped(self, raw: dict, *, reason: str = "completed") -> None:
        """PC-26: write terminal done JSON for a skipped duplicate.

        Without this the task file stays in processing/, so the UI keeps
        listing it as pending and the reclaim loop keeps re-queuing it.
        """
        payload = dict(raw or {})
        try:
            task = Task.from_dict(payload)
        except Exception as exc:
            try:
                self.log.write(f"dedupe finalize: bad task: {exc}")
            except Exception:
                pass
            return
        task.id = str(task.id or uuid.uuid4())
        result = {
            "error": "",
            "method": "dedupe",
            "worker": "dedupe",
            "reason": reason,
            "summary": f"дубликат: задача уже выполнена (dedupe, {reason})",
        }
        # move first: bus.move copies the source over the destination, so a
        # preceding _save would be overwritten by the raw processing file
        try:
            self.bus.move(task.channel, "processing", "done", f"{task.id}.json")
        except Exception:
            pass
        try:
            self._save(task, "done", result)
        except Exception as exc:
            try:
                self.log.write(f"dedupe finalize: {exc}")
            except Exception:
                pass
            return
        try:
            self.queue.terminal(
                task.id, "DONE", error="",
                attempts=int(getattr(task, "attempts", 0) or 0),
            )
        except Exception:
            pass

    def _notify_plan_terminal(self, raw: dict, status: str) -> None:
        """Push a terminal task status into the LivingPlan step that spawned it.

        The Plan is the terminal authority, so a plan-sourced step must learn
        the outcome; otherwise it stays READY forever and the queue keeps
        re-emitting the same step.
        """
        try:
            from intelligence.dynamic_queue import notify_plan_task_terminal

            payload = dict(raw or {})
            meta = payload.get("metadata")
            meta = dict(meta) if isinstance(meta, dict) else {}
            root = str(meta.get("project_root") or payload.get("project") or "")
            if not root:
                return
            notify_plan_task_terminal(
                root,
                task_id=str(payload.get("id") or ""),
                plan_step_id=str(meta.get("plan_step_id") or meta.get("step_id") or ""),
                status=str(status or ""),
                metadata=meta,
            )
        except Exception as exc:
            try:
                self.log.write(f"plan terminal: {exc}")
            except Exception:
                pass

    def process(self, raw: dict) -> str | None:
        """Обработать задачу. Возвращает DONE/ERROR/DEFERRED/DEDUPED или None."""
        raw = dict(raw or {})
        self._process_desktop_seed(raw)

        # --- night filter (optional): defer hard / autopilot work until night ---
        if self._maybe_defer_for_night(raw):
            return "DEFERRED"

        # --- dedupe (skip on retries) ---
        fp, deduped = self._process_dedupe(raw)
        if deduped:
            return deduped

        # --- project lock + project_root for complexity ---
        project_key = self._process_project_key(raw)
        tid = str(raw.get("id") or "")
        meta_lock = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        is_sub = bool(meta_lock.get("is_subtask") or meta_lock.get("source") in ("sub_agent", "decomposer") or str(meta_lock.get("parent_id") or ""))
        parent_id = str(meta_lock.get("parent_id") or "")
        if not self.project_lock.acquire(project_key, tid, is_subtask=is_sub, parent_id=parent_id):
            return self._process_defer_busy(
                raw,
                event="PROJECT_BUSY",
                message=f"проект занят, отложено · {project_key}",
                payload={"project": project_key, "active": self.project_lock.snapshot()},
                error="PROJECT_BUSY",
                delay=15,
                fp=fp,
            )

        # Subtask file isolation: do not run two children touching the same paths
        files_lock = list(raw.get("files") or [])
        if is_sub and files_lock:
            if not self.file_locks.acquire(files_lock, tid):
                self.project_lock.release(project_key, tid)
                return self._process_defer_busy(
                    raw,
                    event="FILE_BUSY",
                    message=f"файлы заняты другим subtask · {project_key}",
                    payload={"files": files_lock[:12], "held": self.file_locks.snapshot()},
                    error="FILE_BUSY",
                    delay=10,
                    fp=fp,
                )

        return self._process_complete(
            raw, fp=fp, project_key=project_key, tid=tid,
            is_sub=is_sub, files_lock=files_lock)

    def _process_desktop_seed(self, raw: dict) -> None:
        """Desktop chat primary: seed processing so bus.move(done) works."""
        try:
            ch = str(raw.get("channel") or "")
            if ch == "desktop" or (raw.get("metadata") or {}).get("primary_channel") == "desktop":
                raw["channel"] = "desktop"
                from core.tasks import Task as _Task
                _t = _Task.from_dict(raw)
                _t.channel = "desktop"
                self._save(_t, "processing", {"phase": "desktop_claim", "source": "desktop_chat"})
        except Exception as exc:
            try:
                self.log.write(f"desktop seed: {exc}")
            except Exception:
                pass

    def _process_dedupe(self, raw: dict) -> tuple[str | None, str | None]:
        """Dedupe (skip on retries). Возвращает (fp, "DEDUPED" | None)."""
        try:
            attempts_peek = int(raw.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts_peek = 0
        fp = None
        if attempts_peek <= 0:
            try:
                fp = task_fingerprint(raw)
            except Exception as exc:
                self.log.write(f"dedupe fingerprint: {exc}")
                fp = None
            if fp and self.dedupe.contains(fp):
                tid = str(raw.get("id") or "")
                self._emit("DEDUPED", f"дубликат уже успешно выполненной задачи пропущен · {tid}",
                           task_id=tid, worker=self.worker_id,
                           payload={"fingerprint": fp, "reason": "completed"})
                self._finalize_deduped(raw, reason="completed")
                return fp, "DEDUPED"
            if fp:
                with self._dedupe_lock:
                    if fp in self._dedupe_inflight:
                        tid = str(raw.get("id") or "")
                        self._emit("DEDUPED", f"дубликат выполняемой задачи пропущен · {tid}",
                                   task_id=tid, worker=self.worker_id,
                                   payload={"fingerprint": fp, "reason": "in_flight"})
                        self._finalize_deduped(raw, reason="in_flight")
                        return fp, "DEDUPED"
                    self._dedupe_inflight.add(fp)
        return fp, None

    def _process_project_key(self, raw: dict) -> str:
        """Ключ project lock + project_root в metadata (для complexity)."""
        project = str(raw.get("project") or "").strip()
        try:
            if project:
                root = resolve_project(project)
            else:
                root = PROJECT_ROOT
            if root is not None:
                meta = dict(raw.get("metadata") or {})
                meta.setdefault("project_root", str(root))
                raw["metadata"] = meta
                project_key = project or str(root)
            else:
                project_key = project or "_default"
        except Exception:
            project_key = project or "_default"
        return project_key

    def _process_defer_busy(self, raw: dict, *, event: str, message: str, payload: dict, error: str, delay: float, fp: str | None) -> str:
        """PROJECT_BUSY/FILE_BUSY: эмит, парковка в deferred, снятие inflight."""
        tid = str(raw.get("id") or "")
        self._emit(event, message, task_id=tid, worker=self.worker_id,
                   payload=payload)
        try:
            task = Task.from_dict(raw)
            task.id = tid or task.id
            self._backoff[task.id] = time.monotonic() + delay
            self.bus.move(task.channel, "processing", "deferred", f"{task.id}.json")
            self._save(task, "deferred", {
                "error": error,
                "attempts": int(raw.get("attempts") or 0),
                "category": "BUSY",
            })
        except Exception:
            pass
        if fp:
            with self._dedupe_lock:
                self._dedupe_inflight.discard(fp)
        return "DEFERRED"

    def _process_complete(self, raw: dict, *, fp: str | None, project_key: str, tid: str, is_sub: bool, files_lock: list) -> str | None:
        """Тело задачи + финализация: dedupe mark/forget, plan-notify, локи."""
        try:
            status = self._process_body(raw)
            if status == "DONE" and fp:
                self.dedupe.mark(fp, str(raw.get("id") or ""))
            elif fp and status in ("ERROR", "DEFERRED", "BLOCKED"):
                # Метка ставится только на DONE. Но если задача не решена,
                # её fingerprint не должен навсегда блокировать повтор:
                # метка могла остаться от прошлого ложного DONE
                # (например skill-путь завершал задачу, ничего не сделав).
                # Иначе «fix the same thing again» вечно уходил в DEDUPED.
                try:
                    self.dedupe.forget(fp)
                except Exception:
                    pass
            if status in ("DONE", "ERROR", "DEFERRED", "DEDUPED", "BLOCKED"):
                self._notify_plan_terminal(raw, status)
            return status
        finally:
            if is_sub and files_lock:
                try:
                    self.file_locks.release(tid, files_lock)
                except Exception:
                    pass
            self.project_lock.release(project_key, tid)
            if fp:
                with self._dedupe_lock:
                    self._dedupe_inflight.discard(fp)

    def _claim_desktop_queue(self) -> dict | None:
        """Poll the shared and project-local desktop queues fairly."""
        try:
            from core.config import BASE_DIR, PROJECTS, PROJECT_ROOT
            from core.local_queue import get_local_queue

            candidates = [Path(BASE_DIR), *(Path(p) for p in PROJECTS.values())]
            if PROJECT_ROOT:
                candidates.append(Path(PROJECT_ROOT))
            roots: list[Path] = []
            seen: set[str] = set()
            for path in candidates:
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key not in seen:
                    seen.add(key)
                    roots.append(path)
            if not roots:
                return None

            start = int(getattr(self, "_desktop_queue_cursor", 0)) % len(roots)
            for offset in range(len(roots)):
                index = (start + offset) % len(roots)
                root = roots[index]
                if not (root / ".agentbus" / "desktop_queue").is_dir():
                    continue
                raw = get_local_queue(root).claim()
                if raw is not None:
                    self._desktop_queue_cursor = (index + 1) % len(roots)
                    return raw
            self._desktop_queue_cursor = (start + 1) % len(roots)
        except Exception as exc:
            try:
                self.log.write(f"desktop queue poll: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        return None

    def run_forever(self) -> None:
        lock = DispatcherLock()
        if not lock.acquire():
            print(f"Уже запущен (lock: {lock.path})")
            sys.exit(0)
        if getattr(self, "_console_sink", None):
            self._console_sink.status_fn = self._pool_status
        self.report = NightlyReport(self.log, LOG_ROOT)
        self.log.write(f"AgentBus запущен | {self.worker_id} | channel=file-bus")
        self.log.write(f"воркеры: {', '.join(w.name for w in self.workers)}")
        self._startup_recover()

        self._running = True
        while True:
            try:
                self._flush_backoff()
                self._heartbeat()
                self._loop_tick_flags()
                self._loop_tick_reclaim()
                self._probe_providers()
                self._maybe_dump_metrics()
                try:
                    self._maybe_run_autopilot()
                except Exception as exc:
                    self.log.write(f"autopilot tick: {exc}")
                self._loop_tick_status_board()
                self._loop_tick_archive()
                raw = self._loop_claim()
                if raw:
                    if raw.get("id") in self._backoff:
                        time.sleep(POLL_SECONDS)
                        continue
                    self.process(raw)
                else:
                    time.sleep(POLL_SECONDS)
            except KeyboardInterrupt:
                self._loop_interrupt(lock)
                return
            except Exception as exc:
                self.log.write(f"цикл: {type(exc).__name__}: {exc}")
                time.sleep(POLL_SECONDS)

    def _startup_recover(self) -> None:
        """Восстановление перед стартом цикла: reclaim processing, deferred, dynamic pool."""
        try:
            rec = self._recover_stale_processing(LEASE_SECONDS)
            if rec:
                self.log.write(f"processing→incoming: {rec}")
        except Exception as exc:
            self.log.write(f"recover: {exc}")
        try:
            n = self._recover_deferred()
            if n:
                self.log.write(f"deferred→incoming(start): {n}")
        except Exception as exc:
            self.log.write(f"recover_deferred: {exc}")
        try:
            self._sync_dynamic_pool()
        except Exception as exc:
            self.log.write(f"dynamic: {exc}")

    def _loop_tick_flags(self) -> None:
        """Тик цикла: hot-reload feature_flags.yaml (mtime) без рестарта."""
        try:
            # hot-reload feature_flags.yaml (mtime) without restart
            if not hasattr(self, "_flags_mtime"):
                self._flags_mtime = 0.0
            if (time.monotonic() - getattr(self, "_flags_check_at", 0.0)) >= 15.0:
                self._flags_check_at = time.monotonic()
                from core.feature_flags import config_path, reload_flags
                cp = config_path()
                if cp is not None and cp.is_file():
                    mt = cp.stat().st_mtime
                    if mt != self._flags_mtime:
                        self._flags_mtime = mt
                        snap = reload_flags()
                        try:
                            from core.plugin_registry import clear_cache
                            clear_cache()
                        except Exception:
                            pass
                        self.log.write(
                            f"feature_flags reload: "
                            f"on={sum(1 for v in snap.values() if v)} "
                            f"off={sum(1 for v in snap.values() if not v)}"
                        )
        except Exception as exc:
            try:
                self.log.write(f"feature_flags tick: {exc}")
            except Exception:
                pass

    def _loop_tick_reclaim(self) -> None:
        """Тик цикла: периодический reclaim застрявших processing-задач."""
        try:
            # periodic reclaim (adaptive); cheap if nothing stuck
            if not hasattr(self, "_reclaim_last"):
                self._reclaim_last = 0.0
            if (time.monotonic() - self._reclaim_last) >= max(30.0, float(POLL_SECONDS) * 6):
                self._reclaim_last = time.monotonic()
                n = self._recover_stale_processing(LEASE_SECONDS)
                if n:
                    self.log.write(f"processing reclaim tick: {n}")
        except Exception as exc:
            self.log.write(f"reclaim tick: {exc}")

    def _loop_tick_status_board(self) -> None:
        """Тик цикла: периодическая печать статус-борда."""
        try:
            if not hasattr(self, "_status_board_at"):
                self._status_board_at = 0.0
            if (time.monotonic() - self._status_board_at) >= float(__import__("os").getenv("AGENTBUS_STATUS_BOARD_SEC", "300") or 300):
                self._status_board_at = time.monotonic()
                from utils.status_board import print_board
                print_board()
        except Exception:
            pass

    def _loop_tick_archive(self) -> None:
        """Тик цикла: периодическая чистка старых архивов логов."""
        try:
            if not hasattr(self, "_archive_prune_at"):
                self._archive_prune_at = 0.0
            if (time.monotonic() - self._archive_prune_at) >= 3600.0:
                self._archive_prune_at = time.monotonic()
                from utils.log_archive import prune_old_archives
                from core.config import BUS_ROOT
                stats = prune_old_archives(BUS_ROOT)
                if stats.get("deleted"):
                    self.log.write(f"archive prune: {stats}")
        except Exception as exc:
            try:
                self.log.write(f"archive prune: {exc}")
            except Exception:
                pass

    def _loop_claim(self) -> dict | None:
        """Клейм desktop-очереди; опционально phone/remote file-bus."""
        raw = None
        try:
            raw = self._claim_desktop_queue()
        except Exception:
            raw = None
        if raw is None:
            # Optional phone/remote file-bus
            try:
                from core.feature_flags import is_enabled
                phone = is_enabled("phone_filebus", default=False) or is_enabled(
                    "remote_filebus", default=False
                )
            except Exception:
                phone = False
            if phone:
                raw = self._claim_file_task()
        return raw

    def _loop_interrupt(self, lock: DispatcherLock) -> None:
        """Ctrl+C: сохранить отчёт/метрики/budget, снять dispatcher lock."""
        self._running = False
        self.log.write("стоп Ctrl+C")
        try:
            self.report.set_provider_cooldowns(self.capacity.cooldown_list())
        except Exception:
            pass
        path = self.report.save("interrupt")
        if path:
            self.log.write(f"отчёт: {path}")
        try:
            mp = self.metrics.save_to_file(LOG_ROOT / "metrics_interrupt.json")
            self.log.write(f"metrics: {mp}")
            bp = self.tracker.save_report(LOG_ROOT / "budget_interrupt.json")
            self.log.write(f"budget: {bp}")
        except Exception as exc:
            self.log.write(f"metrics save: {exc}")
        lock.release()
        return

    def _maybe_defer_for_night(self, raw: dict) -> bool:
        """If AGENTBUS_NIGHT_MODE is on and task should wait for night → deferred.

        Uses existing bus.move processing→deferred path; recover_deferred will
        requeue later. Does not invent a parallel executor.
        """
        import os

        flag = (os.getenv("AGENTBUS_NIGHT_MODE") or "").strip().lower()
        if flag not in ("1", "true", "yes", "on"):
            return False
        # retries / already deferred cycles should not bounce forever on night gate
        try:
            if int(raw.get("attempts") or 0) > 0:
                return False
        except (TypeError, ValueError):
            pass
        try:
            if not is_enabled("night_scheduler"):
                raise ImportError("night_scheduler disabled")
            from intelligence.night_scheduler import GLOBAL_NIGHT
            decision = GLOBAL_NIGHT.filter_for_now(raw)
        except Exception as exc:
            self.log.write(f"night filter: {exc}")
            return False
        if decision != "defer_to_night":
            return False
        tid = str(raw.get("id") or "")
        channel = str(raw.get("channel") or DEFAULT_CHANNEL)
        self._emit(
            "NIGHT_DEFER",
            f"отложено до ночи · {tid}",
            task_id=tid,
            worker=self.worker_id,
            payload={"decision": decision, "channel": channel},
        )
        try:
            task = Task.from_dict(raw)
            task.id = tid or task.id
            task.channel = channel or task.channel
            # Claim may have already moved file to processing — park in deferred
            try:
                self.bus.move(task.channel, "processing", "deferred", f"{task.id}.json")
            except Exception:
                try:
                    self.bus.move(task.channel, "incoming", "deferred", f"{task.id}.json")
                except Exception:
                    pass
            self._save(task, "deferred", {
                "error": "NIGHT_DEFER",
                "attempts": int(raw.get("attempts") or 0),
                "category": "NIGHT",
            })
        except Exception as exc:
            self.log.write(f"night defer save: {exc}")
        return True

    def _maybe_run_autopilot(self) -> None:
        """Optional periodic scan → emit_tasks into channels/autopilot/incoming.

        Env:
          AGENTBUS_AUTOPILOT=1
          AGENTBUS_AUTOPILOT_INTERVAL_SEC=3600 (default)
          AGENTBUS_AUTOPILOT_LIMIT=15
        Project path: PROJECT_ROOT or AGENTBUS_AUTOPILOT_ROOT.
        """
        import os

        flag = (os.getenv("AGENTBUS_AUTOPILOT") or "").strip().lower()
        if flag not in ("1", "true", "yes", "on"):
            return
        try:
            interval = float(os.getenv("AGENTBUS_AUTOPILOT_INTERVAL_SEC") or "3600")
        except (TypeError, ValueError):
            interval = 3600.0
        now = time.monotonic()
        last = float(getattr(self, "_autopilot_last", 0.0) or 0.0)
        if last and (now - last) < interval:
            return
        self._autopilot_last = now
        root = os.getenv("AGENTBUS_AUTOPILOT_ROOT") or ""
        if not root:
            try:
                root = str(PROJECT_ROOT) if PROJECT_ROOT else ""
            except Exception:
                root = ""
        if not root:
            return
        try:
            limit = int(os.getenv("AGENTBUS_AUTOPILOT_LIMIT") or "15")
        except (TypeError, ValueError):
            limit = 15
        try:
            if not is_enabled("autopilot"):
                raise ImportError("autopilot disabled")
            from skills.autopilot import Autopilot
            from core.config import BUS_ROOT

            written = Autopilot(root).emit_tasks(
                bus_root=BUS_ROOT,
                channel="autopilot",
                project=Path(root).name,
                limit=max(1, limit),
            )
            if written:
                self.log.write(f"autopilot emit: {len(written)} → channels/autopilot/incoming")
                try:
                    from utils.metrics import GLOBAL_METRICS
                    GLOBAL_METRICS.record("autopilot_emit", len(written))
                except Exception:
                    pass
        except Exception as exc:
            self.log.write(f"autopilot: {exc}")

    def _maybe_dump_metrics(self) -> None:
        """Периодический снимок metrics/budget (~каждые 5 мин)."""
        now = time.monotonic()
        if (now - getattr(self, "_metrics_last", 0.0)) < 300.0:
            return
        self._metrics_last = now
        try:
            summary = self.metrics.get_summary()
            self.log.info(
                f"metrics tasks={summary.get('task_count')} ok={summary.get('success_count')} "
                f"err={summary.get('error_count')} deferred={summary.get('deferred_count')}",
                event="metrics_snapshot",
                **{k: summary[k] for k in ("task_count", "success_count", "error_count")
                   if k in summary},
            )
            self.metrics.save_to_file(LOG_ROOT / "metrics_latest.json")
            self.tracker.save_report(LOG_ROOT / "budget_latest.json")
            # Alerts (non-fatal)
            try:
                from utils.alerts import GLOBAL_ALERTS
                fired = list(GLOBAL_ALERTS.check_from_metrics(summary))
                # queue depth from channels
                try:
                    from pathlib import Path as _P
                    from core.config import CHANNELS_ROOT
                    counts = {"incoming": 0}
                    root = _P(CHANNELS_ROOT)
                    if root.is_dir():
                        for d in root.glob("*/incoming"):
                            counts["incoming"] += sum(
                                1 for p in d.iterdir() if p.is_file() and p.suffix == ".json"
                            )
                    fired += list(GLOBAL_ALERTS.check_queue(counts))
                except Exception:
                    pass
                # workers available?
                try:
                    any_ok = any(
                        self.health.available(w.name)
                        for w in (self.workers or [])
                        if getattr(w, "enabled", True)
                    )
                    fired += list(GLOBAL_ALERTS.check_workers_available(any_ok))
                except Exception:
                    pass
                for a in fired:
                    self.log.write(f"ALERT {a.alert_type}: {a.message}")
            except Exception as aexc:
                self.log.write(f"alerts: {aexc}")
        except Exception as exc:
            self.log.write(f"metrics dump: {exc}")




def diagnose() -> int:
    """TD-020: единая диагностика живёт в dispatcher_main.run_diagnose.

    Общие секции (конфиг, инструменты, проекты, модули, провайдеры/воркеры,
    бюджет/метрики, окружение, desktop-канал, doctor go/no-go) печатает
    общий diagnose. Ниже — только секции, которых в нём нет: кэш решений,
    уроки, плагины/пресеты, профили моделей, локальные рантаймы, реестр
    расширений и статус-борда.
    """
    print("=== AgentBus diagnose ===")
    try:
        from core.dispatcher_main import run_diagnose
        code = run_diagnose()
    except Exception as e:
        print(f"diagnose ERR      : {e}")
        code = 1

    # --- секции, специфичные для runtime ---
    try:
        from intelligence.solution_cache import GLOBAL_CACHE
        print(f"solution_cache    : {GLOBAL_CACHE.stats()}")
    except Exception as e:
        print(f"solution_cache ERR: {e}")
    try:
        from intelligence.lesson_learner import GLOBAL_LEARNER
        print(f"lessons           : {GLOBAL_LEARNER.stats()}")
    except Exception as e:
        print(f"lessons ERR       : {e}")
    try:
        from core.plugin_registry import status_dict
        from core.feature_flags import list_presets
        plugs = status_dict()
        on = [p for p in plugs if p["enabled"]]
        loaded = [p for p in plugs if p["loaded"]]
        failed = [p for p in plugs if p["enabled"] and not p["loaded"] and p.get("error")]
        print(f"plugins enabled   : {len(on)} / {len(plugs)}")
        print(f"plugins loaded    : {len(loaded)}")
        if failed:
            print(f"plugins failed    : {', '.join(p['name']+':'+p['error'][:40] for p in failed[:8])}")
        presets = list_presets()
        print(f"feature presets   : {', '.join(p['name'] for p in presets) or '(none)'}")
    except Exception as e:
        print(f"plugins ERR       : {e}")
    try:
        from core.model_profiles import load_profiles, profile_for_worker
        from core.workers import load_workers
        profiles = load_profiles()
        print(f"model profiles    : {len(profiles)} ({', '.join(list(profiles)[:6])})")
        for w in load_workers()[:5]:
            p = profile_for_worker(w)
            print(f"  {w.name:22} → {p.name} backend={p.backend} ctx={p.context_window} rag={p.rag_strictness}")
    except Exception as e:
        print(f"model profiles ERR: {e}")
    try:
        from core.harness_registry import discover_local_stack, recommend_stack
        snap = discover_local_stack()
        print(f"local runtimes    : {[r['id']+(' OK' if r.get('ok') else ' —') for r in snap.get('runtimes',[])]}")
        print(f"harnesses         : {[h['id']+(' OK' if h.get('ok') else ' —') for h in snap.get('harnesses',[])]}")
        for tip in recommend_stack():
            print(f"  → {tip}")
    except Exception as e:
        print(f"adapters ERR      : {e}")
    try:
        from core.plugin_registry import discover_plugins, load_extension_manifest, PLUGIN_MODULES
        load_extension_manifest()
        disc = discover_plugins(force=True)
        on = [e for e in disc if e.get("enabled")]
        print(f"plugins registry  : {len(PLUGIN_MODULES)} built-in/manifest")
        print(f"plugins discovered: {len(disc)} (enabled {len(on)})")
        for e in disc:
            print(f"  [{'ON' if e.get('enabled') else 'off'}] {e.get('name')} → {e.get('module')}")
        try:
            from utils.metrics import GLOBAL_METRICS
            snap = GLOBAL_METRICS.snapshot() if hasattr(GLOBAL_METRICS, "snapshot") else {}
            c = (snap.get("counters") if isinstance(snap, dict) else None) or getattr(GLOBAL_METRICS, "counters", {}) or {}
            if isinstance(c, dict):
                print(f"skill metrics     : hit={c.get('skill_hit', 0)} miss={c.get('skill_miss', 0)}")
        except Exception:
            pass
    except Exception as e:
        print(f"extensions ERR    : {e}")
    print("=== end diagnose ===")
    try:
        from utils.status_board import print_board
        print_board()
    except Exception as e:
        print(f"status_board ERR  : {e}")
    return code


def main(argv: list[str] | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--diagnose" in args or "diagnose" in args:
        raise SystemExit(diagnose())
    Runtime().run_forever()


if __name__ == "__main__":
    main()
