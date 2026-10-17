# -*- coding: utf-8 -*-
"""Здоровье воркеров: score, circuit, billing, rate-limit, budget и качество verify."""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from core.config import (HEALTH_BASE_COOLDOWN, HEALTH_CIRCUIT_LIMIT, HEALTH_SUCCESS_WEIGHT,
                    HEALTH_SPEED_WEIGHT, HEALTH_AVAIL_WEIGHT, HEALTH_FIT_WEIGHT,
                    HEALTH_FAIL_PENALTY, WORKERS_STATE_FILE)
from utils.budget import GLOBAL_BUDGET

_VERIFY_DEGRADED_THRESHOLD = 3
_VERIFY_DEGRADED_SCORE_PENALTY = 0.55
_VERIFY_FAILURE_SCORE_PENALTY = 0.9
_RATE_TIERS = (300, 900, 3600, 10800)
_FAIL_TIERS = (60, 300, 900, 1800, 3600)
_BILLING_COOLDOWN = 86400.0
# LOOP: шум модели, а не отказ воркера. Ступени по числу подряд идущих
# сбоев вместо плоских 300с (c-long-040046).
_LOOP_TIERS = (60.0, 120.0, 300.0, 900.0, 1800.0)
_VERIFY_COOLDOWN = 600.0
_BILLING_ERROR_MARKERS = (
    "insufficient credits", "billing required", "payment required",
    "quota exceeded", "402", "balance insufficient", "no credits",
)


def _is_billing_error(error: str) -> bool:
    text = (error or "").lower()
    return any(marker in text for marker in _BILLING_ERROR_MARKERS)


def _is_loop_error(error: str, status: str = "") -> bool:
    text = (error or "").lower()
    return status == "LOOP" or "зацикливание" in text or "loop detected" in text


@dataclass
class WorkerState:
    status: str = "UNKNOWN"
    failures: int = 0
    consecutive_failures: int = 0
    consecutive_timeouts: int = 0
    consecutive_verify_failures: int = 0
    cooldown_until: float = 0.0
    rate_limit_until: float = 0.0
    latency_avg: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    timeout_count: int = 0
    verify_fail_count: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_verify_failure: float = 0.0
    tasks_completed: int = 0
    last_error: str = ""
    running_count: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total else 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_timeouts": self.consecutive_timeouts,
            "consecutive_verify_failures": self.consecutive_verify_failures,
            "cooldown_until": self.cooldown_until,
            "rate_limit_until": self.rate_limit_until,
            "latency_avg": self.latency_avg,
            "success_rate": self.success_rate,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "timeout_count": self.timeout_count,
            "verify_fail_count": self.verify_fail_count,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_verify_failure": self.last_verify_failure,
            "tasks_completed": self.tasks_completed,
            "last_error": self.last_error[-2000:],
        }


class HealthRegistry:
    """Tracks worker health, cooldowns, circuit breaker and verify quality."""

    def __init__(self, base_cooldown: int = HEALTH_BASE_COOLDOWN,
                 circuit_limit: int = HEALTH_CIRCUIT_LIMIT,
                 state_file: str | Path | None = None) -> None:
        self.states: dict[str, WorkerState] = {}
        self.max_parallel: dict[str, int] = {}
        self.base_cooldown = base_cooldown
        self.circuit_limit = circuit_limit
        self.state_file = Path(state_file) if state_file else Path(WORKERS_STATE_FILE)
        self.budget = GLOBAL_BUDGET
        self._lock = threading.Lock()
        self.load_state()

    def load_state(self) -> None:
        if not self.state_file.is_file():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        fields = (
            "status", "failures", "consecutive_failures", "consecutive_timeouts",
            "consecutive_verify_failures", "cooldown_until", "rate_limit_until",
            "latency_avg", "success_count", "fail_count", "timeout_count",
            "verify_fail_count", "last_success", "last_failure", "last_verify_failure",
            "tasks_completed", "last_error",
        )
        for name, raw in (data or {}).items():
            st = self.states.setdefault(name, WorkerState())
            for key in fields:
                if key in raw and raw[key] is not None:
                    setattr(st, key, raw[key])
            if st.status == "BUSY":
                st.status = "AVAILABLE"

    def save_state(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({n: s.to_dict() for n, s in self.states.items()},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            # TD-008: a swallowed save failure silently lost worker health state
            try:
                import logging
                logging.getLogger("agentbus.health").warning(
                    "save_state failed %s: %s: %s",
                    self.state_file, type(exc).__name__, exc,
                )
            except Exception:
                pass

    def state(self, name: str) -> WorkerState:
        return self.states.setdefault(name, WorkerState())

    def register(self, name: str, max_parallel: int = 1) -> None:
        self.max_parallel[name] = max(1, int(max_parallel or 1))

    def available(self, name: str) -> bool:
        st = self.state(name)
        now = time.monotonic()
        return (
            now >= st.cooldown_until
            and now >= st.rate_limit_until
            and st.running_count < self.max_parallel.get(name, 1)
            and self.budget.can_use(name)
        )

    def running(self, name: str) -> bool:
        return self.state(name).running_count >= self.max_parallel.get(name, 1)

    def running_count(self, name: str) -> int:
        return self.state(name).running_count

    def score(self, name: str, task_complexity: int = 3,
              worker_complexity: int = 3, quality: float = 1.0) -> float:
        st = self.state(name)
        if not self.available(name):
            return -1.0
        avail = 1.0 if st.status in ("AVAILABLE", "UNKNOWN", "") else 0.8
        fit = 1 / (1 + abs(worker_complexity - task_complexity))
        speed = 1 / (1 + st.latency_avg / 10)
        rec = 1 - (HEALTH_FAIL_PENALTY if time.monotonic() - st.last_failure < 600 else 0)
        score = (
            (st.success_count + 1) / (st.success_count + st.fail_count + 1) * HEALTH_AVAIL_WEIGHT * avail
            + HEALTH_SUCCESS_WEIGHT * st.success_rate
            + HEALTH_FIT_WEIGHT * fit
            + HEALTH_SPEED_WEIGHT * speed
            + quality * 0.2
        ) * rec
        if st.consecutive_verify_failures > 0:
            score *= _VERIFY_FAILURE_SCORE_PENALTY ** st.consecutive_verify_failures
        if st.consecutive_verify_failures >= _VERIFY_DEGRADED_THRESHOLD:
            score *= _VERIFY_DEGRADED_SCORE_PENALTY
        return round(max(0, score), 3)

    def success(self, name: str, latency: float = 0.0) -> None:
        st = self.state(name)
        st.status = "AVAILABLE"
        st.failures = 0
        st.consecutive_failures = 0
        st.consecutive_timeouts = 0
        st.consecutive_verify_failures = 0
        # Успех доказывает, что воркер жив: снимаем cooldown. Иначе после
        # серии сбоев ступени разгоняются до 1800с, а единственный рабочий
        # воркер остаётся выбитым из пула даже после удачной задачи
        # (c-long-040046). rate_limit_until не трогаем: это ограничение
        # провайдера, а не здоровья воркера.
        st.cooldown_until = 0.0
        st.success_count += 1
        st.tasks_completed += 1
        st.last_success = time.monotonic()
        if latency:
            n = st.success_count
            st.latency_avg = (st.latency_avg * (n - 1) + latency) / n
        self.save_state()

    def verify_success(self, name: str) -> None:
        """Сбросить серию плохих verify после успешной проверки."""
        st = self.state(name)
        st.consecutive_verify_failures = 0
        if st.status == "DEGRADED":
            st.status = "AVAILABLE"
        self.save_state()

    def verify_failure(self, name: str, error: str = "") -> None:
        """Учитывает провал verify отдельно от ошибки запуска воркера."""
        st = self.state(name)
        st.consecutive_verify_failures += 1
        st.verify_fail_count += 1
        st.last_verify_failure = time.monotonic()
        st.last_error = (error or "verify failed")[-2000:]
        if st.consecutive_verify_failures >= _VERIFY_DEGRADED_THRESHOLD:
            st.status = "DEGRADED"
            st.cooldown_until = max(st.cooldown_until, time.monotonic() + _VERIFY_COOLDOWN)
        self.save_state()

    def failure(self, name: str, error: str, timed_out: bool = False,
                status: str = "ERROR", billing_error: bool = False) -> None:
        st = self.state(name)
        st.failures += 1
        st.consecutive_failures += 1
        st.fail_count += 1
        st.last_failure = time.monotonic()
        st.last_error = (error or "")[-2000:]
        if billing_error or _is_billing_error(error):
            st.status = "BILLING"
            st.cooldown_until = time.monotonic() + _BILLING_COOLDOWN
            self.save_state()
            return
        if _is_loop_error(error, status):
            st.status = "LOOP"
            st.cooldown_until = time.monotonic() + self._loop_cooldown(st)
            self.save_state()
            return
        if timed_out:
            st.consecutive_timeouts += 1
            st.timeout_count += 1
            st.status = "TIMEOUT"
        else:
            st.status = status
        st.cooldown_until = time.monotonic() + self._cooldown_delay(st, error, timed_out)
        self.save_state()

    def _loop_cooldown(self, st: WorkerState) -> float:
        """Cooldown для LOOP_ERROR — ступенчатый, а не плоский.

        LOOP у локальной 7B — это шум модели, а не отказ воркера: тот же
        воркер сразу после этого успешно создавал файлы (c-long-040046).
        Плоские 300 секунд после ПЕРВОГО же повтора выбивали единственную
        рабочую машину из пула на 5 минут, и весь поток задач уходил по
        fallback-цепочке в plan-only opencode -> DEFERRED по построению.
        Теперь: 60 -> 120 -> 300 -> 900 -> 1800 секунд по числу подряд
        идущих сбоев; успех обнуляет счётчик в success().
        """
        idx = min(max(st.consecutive_failures - 1, 0), len(_LOOP_TIERS) - 1)
        return float(_LOOP_TIERS[idx])

    def _cooldown_delay(self, st: WorkerState, error: str, timed_out: bool) -> float:
        delay = float(self.base_cooldown)
        retry = _retry_after_seconds(error)
        if retry:
            delay = max(delay, retry)
            st.rate_limit_until = time.monotonic() + retry
            st.status = "RATE_LIMITED"
        elif _is_rate_limit(error):
            delay = max(delay, float(_RATE_TIERS[min(st.failures - 1, len(_RATE_TIERS) - 1)]))
            st.rate_limit_until = time.monotonic() + delay
            st.status = "RATE_LIMITED"
        elif timed_out or st.status in ("TIMEOUT", "ERROR"):
            delay = max(delay, float(_FAIL_TIERS[min(st.failures - 1, len(_FAIL_TIERS) - 1)]))
        if st.consecutive_failures >= self.circuit_limit:
            delay = max(delay, self.base_cooldown * 3)
        elif st.consecutive_timeouts >= self.circuit_limit:
            delay = max(delay, self.base_cooldown * 4)
        return delay

    def begin_task(self, name: str, tokens: int = 0) -> bool:
        with self._lock:
            st = self.state(name)
            now = time.monotonic()
            if (now < st.cooldown_until or now < st.rate_limit_until
                    or st.running_count >= self.max_parallel.get(name, 1)
                    or not self.budget.can_use(name)):
                return False
            st.running_count += 1
            st.status = "BUSY"
            self.budget.record(name, tokens)
            self.save_state()
            return True

    def end_task(self, name: str, ok: bool = True, error: str = "",
                 status: str = "ERROR") -> None:
        """Освободить слот воркера.

        ok=False РАНЬШЕ ПОЛНОСТЬЮ ИГНОРИРОВАЛСЯ: параметр принимался, но не
        читался, и вызовы вида health.end_task(w, ok=False) из preflight-пути
        (rp_llm.py) не оставляли в health ни следа. Воркер, отброшенный
        preflight, снова выбирался роутером и тратил 2-3с на каждой задаче
        (c-long-040046). Теперь неуспех фиксируется через failure().
        """
        with self._lock:
            st = self.state(name)
            st.running_count = max(0, st.running_count - 1)
            if st.running_count == 0 and st.status == "BUSY":
                st.status = "UNKNOWN"
            if not ok:
                self.failure(name, error or f"slot/preflight неуспех: {status}",
                             status=status)
            else:
                self.save_state()

    def budget_snapshot(self) -> dict[str, dict[str, Any]]:
        return self.budget.snapshot()

    def operator_snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        rows = []
        for name, st in sorted(self.states.items()):
            status = st.status or "UNKNOWN"
            detail = ""
            if st.running_count > 0:
                status, detail = "BUSY", f"слот {st.running_count}"
            elif now < st.rate_limit_until:
                status, detail = "RATE_LIMITED", f"ещё {int(st.rate_limit_until - now)}с"
            elif now < st.cooldown_until:
                detail = f"ещё {int(st.cooldown_until - now)}с"
                if status not in ("BILLING", "LOOP", "TIMEOUT", "ERROR", "DEGRADED"):
                    status = "COOLDOWN"
            elif status in ("", "UNKNOWN"):
                status = "AVAILABLE"
            rows.append({
                "name": name,
                "status": status,
                "detail": detail,
                "success_rate": round(st.success_rate, 2),
                "consecutive_verify_failures": st.consecutive_verify_failures,
            })
        return rows

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {k: v.to_dict() for k, v in sorted(self.states.items())}

    def effective_status(self, name: str) -> str:
        """UI-facing status: HEALTHY | BUSY | COOLDOWN | RATE_LIMIT | CIRCUIT | DEGRADED | BILLING | UNKNOWN."""
        st = self.state(name)
        now = time.monotonic()
        if st.running_count > 0:
            return "BUSY"
        if st.status in ("CIRCUIT", "BILLING"):
            return st.status
        if st.cooldown_until > now:
            return "COOLDOWN"
        if st.rate_limit_until > now:
            return "RATE_LIMIT"
        if st.status == "DEGRADED" or st.consecutive_verify_failures >= _VERIFY_DEGRADED_THRESHOLD:
            return "DEGRADED"
        if st.status in ("AVAILABLE", "UNKNOWN", ""):
            return "HEALTHY" if self.available(name) else "UNAVAILABLE"
        return st.status or "UNKNOWN"

    def dashboard_rows(self) -> list[dict[str, Any]]:
        """Rows for Live Worker Dashboard (UI / diagnose)."""
        rows: list[dict[str, Any]] = []
        now = time.monotonic()
        for name in sorted(self.states.keys()) or sorted(self.max_parallel.keys()):
            st = self.state(name)
            eff = self.effective_status(name)
            cd_left = max(0.0, st.cooldown_until - now)
            rl_left = max(0.0, st.rate_limit_until - now)
            rows.append({
                "worker": name,
                "status": eff,
                "raw_status": st.status,
                "running": st.running_count,
                "max_parallel": self.max_parallel.get(name, 1),
                "success_rate": round(st.success_rate, 3),
                "latency_avg": round(st.latency_avg, 2),
                "tasks_completed": st.tasks_completed,
                "failures": st.failures,
                "cooldown_sec": int(cd_left),
                "rate_limit_sec": int(rl_left),
                "last_error": (st.last_error or "")[:120],
                "score": self.score(name) if self.available(name) else -1.0,
            })
        return rows


def _retry_after_seconds(error: str) -> float:
    match = re.search(r"(?:retry[_ -]?after|retry[_ -]?in|retry after|RateLimit|429)[^0-9]*(\d+)", error or "", re.I)
    return float(min(int(match.group(1)), 86400)) if match else 0.0


def _is_rate_limit(error: str) -> bool:
    return bool(re.search(r"\b429\b|rate.?limit|too many requests", error or "", re.I))

