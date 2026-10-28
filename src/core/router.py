# -*- coding: utf-8 -*
"""Роутер: Stage 3 complexity + context fit + soft quota + ranker."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .config import COMPLEXITY_LOCAL_MAX

_LOG = logging.getLogger("core.router")

# Причины отсечки воркеров на последнем вызове select_executor.
# Раньше отсечки были полностью молчаливыми: в логе не оставалось следа,
# почему явно запрошенный `executor` не выполнился (aider_local в c-long-040046).
LAST_SKIPS: dict[str, str] = {}

SOFT_QUOTA_PENALTY = 3.0
LOCAL_CTX_BUDGET = 6000  # грубо: 4 байта/символа на токен, с запасом для ответа
BYTES_PER_TOKEN = 4


def _safe_size(path: str | os.PathLike[str]) -> int:
    """Безопасно вернуть размер одного целевого файла в байтах."""
    try:
        return Path(path).stat().st_size
    except (OSError, ValueError, TypeError):
        return 0


def _target_bytes(
    files: list[str] | None, project_root: str | os.PathLike[str] | None
) -> int:
    """Суммарный размер целевых файлов в байтах."""
    total = 0
    root = Path(project_root) if project_root else None
    for file in files or []:
        p = Path(str(file))
        if not p.is_absolute() and root is not None:
            p = root / p
        total += _safe_size(p)
    return total


def estimate_tokens(files: list[str] | None, project_root: str | os.PathLike[str] | None = None) -> int:
    """Грубая оценка токенов только по файлам, которые реально передаются задаче.

    Ограничение (осознанное, TD-029): делим байты на BYTES_PER_TOKEN=4.
    Для кириллицы в UTF-8 один символ — ~2 байта, то есть оценка занижает
    реальное число токенов примерно вдвое. Используется только как мягкий
    лимит LOCAL_CTX_BUDGET (решение «поднять complexity до 4»), не для
    биллинга и не для жёстких отсечек. Точный подсчёт токенизатором
    провайдера здесь сознательно не заводится: лишняя зависимость ради
    грубой эвристики не нужна.
    """

    return _target_bytes(files, project_root) // BYTES_PER_TOKEN


def adjust_for_context(complexity: int, files: list[str] | None,
                       project_root: str | os.PathLike[str] | None = None) -> int:
    """Поднимает complexity до 4, если целевые файлы не помещаются в локальный контекст.

    Сравниваем байты, а не округлённые токены: при делении на 4 файл размером
    ровно 4*LOCAL_CTX_BUDGET давал ровно LOCAL_CTX_BUDGET токенов, и переполнение
    даже на один байт терялось — complexity не повышался.
    """
    if files and _target_bytes(files, project_root) > LOCAL_CTX_BUDGET * BYTES_PER_TOKEN:
        return max(complexity, 4)
    return complexity


def task_complexity(raw: dict[str, Any] | None, default: int = 3) -> int:
    if not raw:
        return default
    meta = raw.get("metadata") or {}
    try:
        c = int(meta.get("complexity"))
        if 1 <= c <= 5:
            base = c
        else:
            base = default
    except (TypeError, ValueError):
        text = " ".join([
            str(raw.get("message", "")),
            " ".join(map(str, raw.get("files", []))),
        ]).lower()
        if len(raw.get("files") or []) >= 4 or any(k in text for k in (
                "архитект", "рефактор", "миграц", "интеграц", "сложн",
                "architecture", "refactor")):
            base = 4
        elif len(text) < 300 or len(raw.get("files") or []) == 0:
            base = 2
        else:
            base = default

    # Для относительных путей используем только корень самого проекта, если
    # он уже явно передан в metadata. Никакого сканирования всего проекта.
    project_root = meta.get("project_root") or raw.get("project_root")
    return adjust_for_context(base, list(raw.get("files") or []), project_root)


def max_available_tier(workers, health, capacity=None) -> int:
    """Highest tier among currently healthy/usable workers (0 if none)."""
    best = 0
    cap_worker_usable = getattr(capacity, "worker_usable", None) if capacity is not None else None
    for w in workers or []:
        if not getattr(w, "enabled", True):
            continue
        try:
            if health is not None and not health.available(w.name):
                continue
        except Exception:
            continue
        if cap_worker_usable is not None:
            try:
                if not cap_worker_usable(w):
                    continue
            except Exception:
                pass
        if capacity is not None:
            try:
                cap_key = f"{w.provider}:{w.model or 'auto'}"
                if not capacity.available(cap_key):
                    continue
            except Exception:
                pass
        best = max(best, int(getattr(w, "tier", 5) or 5))
    return best


def needs_capacity_shard(raw: dict | None, workers, health, capacity=None) -> bool:
    """True when task needs tier above what healthy workers can provide."""
    complexity = task_complexity(raw)
    need = min_tier_for_complexity(complexity)
    if complexity < 4 and need <= 5:
        return False
    avail = max_available_tier(workers, health, capacity)
    return avail < need


def min_tier_for_complexity(task_c: int) -> int:
    """Min worker.tier for task complexity (1-5). Stage5 needs tier>=8."""
    c = int(task_c or 3)
    if c <= 2:
        return 1
    if c == 3:
        return 3
    if c == 4:
        return 5
    return 8


def complexity_fit(worker_complexity, task_c: int) -> bool:
    """worker_complexity may be int or (min, max) range from config."""
    wc = worker_complexity
    if isinstance(wc, (list, tuple)) and len(wc) >= 2:
        try:
            w_min, w_max = int(wc[0]), int(wc[1])
        except (TypeError, ValueError):
            return True
        tc = int(task_c or 3)
        return w_min <= tc <= w_max
    try:
        worker_complexity = int(wc)
    except (TypeError, ValueError):
        return True
    if task_c <= COMPLEXITY_LOCAL_MAX:
        return worker_complexity <= COMPLEXITY_LOCAL_MAX
    if task_c >= 4:
        return worker_complexity >= 4
    return True


def _task_type(raw: dict[str, Any] | None, ranker=None) -> str:
    """Prefer meta_classifier / metadata.task_type, else ranker heuristic."""
    if raw:
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        for key in ("task_type", "type"):
            v = (meta.get(key) or raw.get(key) or "")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    if ranker is not None:
        try:
            from core.ranking import infer_task_type
            return str(infer_task_type(raw) or "general").lower()
        except Exception:
            pass
    return "general"


def _role_bonus(worker, task_type: str, complexity: int) -> float:
    """Soft preference: role=code for coding tasks; keep meta out of code pool."""
    role = str(getattr(worker, "role", "") or "").strip().lower()
    if not role:
        return 0.0
    if role == "meta":
        # meta is handled by meta_classifier, not executor workers
        return -5.0
    coding = task_type in {
        "code", "coding", "bugfix", "refactor", "feature", "test", "docs", "general",
    }
    if role == "code" and coding:
        return 0.35
    if role == "ops" and task_type in {"ops", "infra", "deploy"}:
        return 0.35
    if role == "code" and complexity >= 5:
        return -0.1  # slight preference for stronger/cloud on arch-level
    return 0.0


def _local_first_bonus(worker, raw: dict[str, Any] | None) -> float:
    """Mass-product: prefer local providers unless task asks for cloud/long context."""
    prov = str(getattr(worker, "provider", "") or "").lower()
    local = prov in ("ollama", "lmstudio", "local")
    meta: dict[str, Any] = {}
    if isinstance(raw, dict):
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    caps = meta.get("capabilities") if isinstance(meta.get("capabilities"), list) else []
    prefer_cloud = bool(meta.get("prefer_cloud")) or ("long_context" in caps)
    prefer_offline = bool(meta.get("prefer_local") or meta.get("offline")) or ("offline" in caps)
    # Default mass-market bias: slight local boost when complexity is modest
    try:
        cx = task_complexity(raw)
    except Exception:
        cx = 3
    if prefer_cloud:
        return -0.4 if local else 0.35
    if prefer_offline or cx <= 3:
        return 0.55 if local else 0.0
    if local and cx <= 4:
        return 0.25
    return 0.0


def _is_plan_only(w) -> bool:
    """Воркер умеет только планировать и не пишет файлы (capabilities: [plan])."""
    caps = getattr(w, "capabilities", ()) or ()
    if isinstance(caps, str):
        return "plan" in caps
    return "plan" in tuple(caps)


def _worker_skip_reason(
    w, *, health, capacity, cap_worker_usable, allow_paid,
    required_cap, writes_files, soft_penalty,
) -> str | None:
    """Filter gates for one worker: skip reason, or None if usable.

    Дословный перенос тела цикла select_executor (c-long-040046):
    каждая отсечка прежним _skip(w, reason)+continue стала return reason.
    """
    if not getattr(w, "enabled", True):
        return "disabled"
    if not allow_paid:
        try:
            from core.paid_gate import is_paid_worker
            if is_paid_worker(w):
                return "paid_disallowed[ALLOW_PAID=0]"
        except Exception as _paid_err:
            # Неизвестная цена = платно. Шлюз не должен «падать в открытый»
            # из-за внутренней ошибки (c-long-040046).
            return f"paid_gate_error[{type(_paid_err).__name__}]"
    # Plan-only воркер не создаёт файлы: отдача ему задачу на запись
    # гарантированно даёт пустой результат и DEFERRED по построению
    # (c-long-040046). Планирование без files по-прежнему разрешено.
    if writes_files and _is_plan_only(w):
        return "plan_only_no_file_write"
    if not health.available(w.name):
        return "health_unavailable"
    if cap_worker_usable is not None:
        try:
            if not cap_worker_usable(w):
                return "cap_worker_usable_false"
        except Exception:
            pass
    if capacity is not None:
        cap_key = f"{w.provider}:{w.model or 'auto'}"
        try:
            if not capacity.available(cap_key):
                return f"capacity_unavailable[{cap_key}]"
            qf = capacity.quota_factor(cap_key)
            if qf < 1.0:
                soft_penalty[w.name] = (1.0 - qf) * SOFT_QUOTA_PENALTY
        except Exception:
            pass
    if required_cap:
        wc = getattr(w, "capabilities", None) or ()
        if wc and required_cap not in wc:
            return f"required_cap[{required_cap}]"
    return None


def _worker_score(
    w, *, health, raw, ranker, complexity, task_type, soft_penalty,
) -> float:
    """Score one usable worker: capability·health·cost·reliability·risk (Router 2.0)."""
    score = health.score(w.name, complexity, w.complexity, w.quality)
    score -= soft_penalty.get(w.name, 0.0)
    score += _role_bonus(w, task_type, complexity)
    score += _local_first_bonus(w, raw)
    w_tier = int(getattr(w, "tier", 5) or 5)
    need = min_tier_for_complexity(complexity)
    if complexity <= 2:
        score += max(0, 6 - w_tier) * 0.15
    else:
        score += max(0, w_tier - need) * 0.2
    # Stage 5 / Router 2.0: capability·health·cost·reliability·risk
    try:
        from core.router_score import score_worker_v2
        task_payload = dict(raw or {})
        task_payload["complexity"] = complexity
        br = score_worker_v2(
            w,
            task_payload,
            health_ok=True,  # already filtered by health.available
            ranking=ranker,
            prefer_local=True,
        )
        # map 0..1 → contribution in legacy score space
        score += max(-3.0, min(12.0, br.total * 10.0))
        try:
            meta = task_payload.setdefault("metadata", {})
            if isinstance(meta, dict):
                meta.setdefault("_router_v2", {})[w.name] = br.to_dict()
        except Exception:
            pass
    except Exception:
        try:
            from core.worker_api import score_worker
            from types import SimpleNamespace
            task_ns = SimpleNamespace(
                complexity=complexity,
                metadata=(raw or {}).get("metadata") if isinstance(raw, dict) else {},
            )
            api_score = score_worker(w, task_ns)
            score += max(-5.0, min(15.0, float(api_score) * 8.0))
        except Exception:
            pass
    # Meta hint: prefer suggested_worker slightly
    try:
        meta = (raw or {}).get("metadata") if isinstance(raw, dict) else {}
        sug = str((meta or {}).get("suggested_worker") or "").strip()
        if sug and w.name == sug:
            score += 1.5
    except Exception:
        pass
    if ranker is not None and score > 0:
        score = ranker.apply_bias(
            score, getattr(w, "harness", "cli"), w.provider, w.model,
            complexity=complexity, task_type=task_type,
        )
    return score


def select_executor(workers, health, raw: dict[str, Any] | None,
                    requested: str = "", ranker=None, capacity=None,
                    required_cap: str | None = None,
                    allow_paid: bool | None = None) -> object | None:
    complexity = task_complexity(raw)
    task_type = _task_type(raw, ranker)

    if allow_paid is None:
        # AGENTBUS_ALLOW_PAID заводили, печатали, но не проверяли: платные
        # облака выбирались и тратили деньги (c-long-040046). Здесь шлюз
        # наконец закрыт. Явный allow_paid=True оставлен для сознательных
        # прогонов с платным бюджетом.
        try:
            from core.config import ALLOW_PAID
            allow_paid = bool(ALLOW_PAID)
        except Exception:
            allow_paid = False

    soft_penalty: dict[str, float] = {}
    candidates = []
    cap_worker_usable = getattr(capacity, "worker_usable", None)
    LAST_SKIPS.clear()
    _task_id = str((raw or {}).get("id") or (raw or {}).get("task_id") or "") if isinstance(raw, dict) else ""
    # Задача с явными целевыми файлами требует записи на диск.
    _writes_files = bool(isinstance(raw, dict) and (raw.get("files") or []))

    def _skip(w, reason: str) -> None:
        LAST_SKIPS[w.name] = reason
        _LOG.info("select_executor: skip %s (%s) task=%s requested=%s",
                  w.name, reason, _task_id or "-", requested or "-")

    for w in workers:
        reason = _worker_skip_reason(
            w, health=health, capacity=capacity, cap_worker_usable=cap_worker_usable,
            allow_paid=allow_paid, required_cap=required_cap,
            writes_files=_writes_files, soft_penalty=soft_penalty)
        if reason is not None:
            _skip(w, reason)
            continue
        score = _worker_score(
            w, health=health, raw=raw, ranker=ranker,
            complexity=complexity, task_type=task_type, soft_penalty=soft_penalty)
        if score < 0:
            _skip(w, f"negative_health_score({score:.2f})")
            continue
        candidates.append((score, w))

    if not candidates:
        _LOG.warning(
            "select_executor: no workers available task=%s requested=%s skips=%s",
            _task_id or "-", requested or "-", dict(LAST_SKIPS))
        if requested and requested in LAST_SKIPS:
            _LOG.warning(
                "select_executor: REQUESTED executor %s was filtered out: %s",
                requested, LAST_SKIPS[requested])
        return None

    min_tier = min_tier_for_complexity(complexity)
    fitted = [
        (s, w) for s, w in candidates
        if complexity_fit(w.complexity, complexity)
        and int(getattr(w, "tier", 5) or 5) >= min_tier
    ]
    if not fitted:
        fitted = [(s, w) for s, w in candidates if complexity_fit(w.complexity, complexity)]
    pool = fitted if fitted else candidates

    def key(item):
        score, w = item
        is_req = 1 if (requested and w.name == requested) else 0
        fit = 1 if complexity_fit(w.complexity, complexity) else 0
        role = str(getattr(w, "role", "") or "").strip().lower()
        role_pref = 1 if role == "code" and task_type != "ops" else 0
        return (is_req, fit, role_pref, score)

    pool.sort(key=key, reverse=True)
    chosen = pool[0][1]
    _LOG.info("select_executor: selected %s task=%s requested=%s complexity=%s "
              "candidates=%s skipped=%s",
              chosen.name, _task_id or "-", requested or "-", complexity,
              [w.name for _, w in pool], dict(LAST_SKIPS))
    return chosen
