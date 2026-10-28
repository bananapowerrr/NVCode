# -*- coding: utf-8 -*
"""Панель метрик: metrics_latest.json + очередь channels."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover — offline/CI without GUI deps
    ctk = None  # type: ignore

from ui.paths import agentbus_root, ensure_sys_path
from ui.i18n_ui import t as _t


def _load_cost() -> dict:
    root = agentbus_root()
    for p in (root / ".agentbus" / "session_cost.json", root / "session_cost.json"):
        if p.is_file():
            try:
                import json
                return json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    try:
        from ui.paths import ensure_sys_path
        ensure_sys_path()
        from utils.cost_tracker import GLOBAL_COST
        return GLOBAL_COST.snapshot().to_dict() if hasattr(GLOBAL_COST, "snapshot") else {}
    except Exception:
        return {}


def _find_metrics_files() -> list[Path]:
    root = agentbus_root()
    candidates = [
        root / "channels" / "gpt" / "logs" / "metrics_latest.json",
        root / "channels" / "grok" / "logs" / "metrics_latest.json",
        root / "channels" / "gemini" / "logs" / "metrics_latest.json",
        root / ".agentbus" / "metrics_latest.json",
        root / "metrics_latest.json",
        root / "metrics_report.json",
    ]
    # any channel logs
    ch = root / "channels"
    if ch.is_dir():
        for p in ch.glob("*/logs/metrics_latest.json"):
            candidates.append(p)
        for p in ch.glob("*/logs/metrics_*.json"):
            candidates.append(p)
    seen = set()
    out = []
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        out.append(p)
    return out


def _load_latest_metrics() -> dict:
    files = _find_metrics_files()
    if not files:
        return {}
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["_source"] = str(files[0])
            return data
    except Exception:
        pass
    return {}


def _queue_counts() -> dict[str, int]:
    root = agentbus_root() / "channels"
    counts = {
        "incoming": 0, "processing": 0, "done": 0, "errors": 0, "deferred": 0,
        "desktop": 0,
    }
    # Primary desktop queue (chat on PC)
    try:
        from ui.paths import ensure_sys_path
        ensure_sys_path()
        from core.local_queue import get_local_queue
        counts["desktop"] = int(get_local_queue(agentbus_root()).size())
    except Exception:
        spill = agentbus_root() / ".agentbus" / "desktop_queue"
        if spill.is_dir():
            try:
                counts["desktop"] = sum(1 for p in spill.glob("*.json"))
            except OSError:
                pass
    if root.is_dir():
        for state in ("incoming", "processing", "done", "errors", "deferred"):
            for d in root.glob(f"*/{state}"):
                try:
                    counts[state] += sum(
                        1 for p in d.iterdir() if p.is_file() and p.suffix == ".json"
                    )
                except OSError:
                    pass
    return counts


def _bar(label: str, value: float, width: int = 20) -> str:
    v = max(0.0, min(1.0, float(value or 0)))
    filled = int(round(v * width))
    return f"{label:12} [{'█' * filled}{'░' * (width - filled)}] {v*100:5.1f}%"


def format_cost_section() -> str:
    """P1.5 — session cost board (local = 0 ₽ explicit)."""
    data = _load_cost()
    lines = ["", "=== СТОИМОСТЬ СЕССИИ ==="]
    if not data:
        lines.append("(пока нет данных — local/skills/cache обычно 0 ₽)")
        lines.append("local Ollama/LM Studio: 0.00 USD  |  0 ₽")
        return "\n".join(lines)
    tin = int(data.get("session_tokens_in") or 0)
    tout = int(data.get("session_tokens_out") or 0)
    usd = float(data.get("session_cost_usd") or 0)
    calls = int(data.get("calls") or 0)
    skill = int(data.get("skill_saves") or 0)
    cache = int(data.get("cache_saves") or 0)
    lines.append(f"tokens in/out : {tin} / {tout}")
    lines.append(f"LLM calls     : {calls}")
    lines.append(f"est. USD      : {usd:.4f}")
    lines.append(f"local share   : skills={skill}  cache={cache}  → 0 ₽ на этих задачах")
    if usd <= 0:
        lines.append("ИТОГО облако  : 0.00 USD  (~0 ₽) — local-first")
    else:
        # rough RUB for display only, no FX API
        lines.append(f"ИТОГО облако  : {usd:.4f} USD  (ориентир ×100 ≈ {usd*100:.0f} ₽)")
    by_w = data.get("by_worker") or {}
    if isinstance(by_w, dict) and by_w:
        lines.append("по воркерам:")
        for k, v in list(by_w.items())[:12]:
            if isinstance(v, dict):
                lines.append(
                    f"  {k}: cost={v.get('cost_usd', v.get('cost', 0))} "
                    f"tok={v.get('tokens', v.get('tokens_in', 0))}"
                )
            else:
                lines.append(f"  {k}: {v}")
    recent = data.get("recent") or []
    if recent:
        lines.append(f"последние записи: {len(recent)}")
    return "\n".join(lines)


def format_metrics_board(m: dict[str, Any] | None, q: dict[str, Any] | None) -> str:
    '''Render the metrics board as plain text (q may include desktop primary queue).'''
    m = dict(m or {})
    q = dict(q or {})
    # q may include desktop primary queue
    lines = []
    # cost board first (P1.5)
    try:
        cost_txt = format_cost_section().lstrip("\n")
        if cost_txt:
            lines.append(cost_txt)
            lines.append("")
    except Exception:
        pass
    lines.append("=== Очередь ===")
    lines.append(f"desktop (чат ПК): {q.get('desktop', 0)}")
    lines.append(
        f"file-bus in:{q['incoming']}  run:{q['processing']}  done:{q['done']}  "
        f"err:{q['errors']}  def:{q['deferred']}"
    )
    lines.append("")
    if not m:
        lines.append("metrics_latest.json ещё нет — запусти dispatcher")
        lines.append("(снимок пишется ~каждые 5 мин и при остановке)")
        return "\n".join(lines)
    try:
        from utils.alerts import GLOBAL_ALERTS
        fired = list(GLOBAL_ALERTS.check_from_metrics(m))
        fired += list(GLOBAL_ALERTS.check_queue(q))
        if fired:
            lines.append("=== Alerts ===")
            for a in fired[-5:]:
                lines.append(f"! [{a.severity}] {a.alert_type}: {a.message}")
            lines.append("")
    except Exception:
        pass
    rates = m.get("hit_rates") or {}
    counters = m.get("counters") or {}
    lines.append("=== Hit rates ===")
    lines.append(_bar("cache", rates.get("cache_hit_rate", 0)))
    lines.append(_bar("skills", rates.get("skill_hit_rate", 0)))
    lines.append(_bar("llm ok", rates.get("llm_success_rate", 0)))
    lines.append(
        f"cache {counters.get('cache_hit', 0)}/{int(rates.get('cache_total', 0))}  "
        f"skill {counters.get('skill_hit', 0)}/{int(rates.get('skill_total', 0))}  "
        f"llm {int(rates.get('llm_calls', 0))}"
    )
    vp = int(counters.get("verify_ladder_pass") or 0)
    vf = int(counters.get("verify_ladder_fail") or 0)
    if vp or vf:
        lines.append(
            f"verify ladder pass={vp} fail={vf} "
            f"L1={counters.get('verify_ladder_fail_L1', 0)} "
            f"L2={counters.get('verify_ladder_fail_L2', 0)} "
            f"L3={counters.get('verify_ladder_fail_L3', 0)} "
            f"diff_budget={counters.get('diff_budget_exceeded', 0)} "
            f"cache_skip={counters.get('cache_skip_no_ladder', 0)}"
        )
    lines.append("")
    lines.append("=== Tasks ===")
    lines.append(
        f"total={m.get('task_count', 0)}  ok={m.get('success_count', 0)}  "
        f"err={m.get('error_count', 0)}  deferred={m.get('deferred_count', 0)}  "
        f"deduped={m.get('deduped_count', 0)}"
    )
    lines.append(f"success_rate={float(m.get('success_rate') or 0)*100:.1f}%  "
                 f"uptime={m.get('uptime_sec', 0)}s")
    lines.append("")
    usage = m.get("worker_usage") or {}
    if usage:
        lines.append("=== Workers ===")
        total_u = sum(int(v) for v in usage.values()) or 1
        for name, n in sorted(usage.items(), key=lambda x: -int(x[1])):
            share = int(n) / total_u
            lines.append(f"{_bar(str(name)[:12], share)}  n={n}")
    dh = m.get("duration_histogram") or {}
    if dh:
        lines.append("")
        lines.append("=== Duration histogram ===")
        total_d = sum(int(v) for v in dh.values()) or 1
        for bucket in ("0-10s", "10-30s", "30-60s", "60-120s", "120s+"):
            n = int(dh.get(bucket, 0))
            lines.append(_bar(bucket, n / total_d) + f"  n={n}")
    rh = m.get("retry_histogram") or {}
    if rh:
        lines.append("")
        lines.append("=== Retries ===")
        total_r = sum(int(v) for v in rh.values()) or 1
        for bucket in ("0", "1", "2", "3+"):
            n = int(rh.get(bucket, 0))
            lines.append(_bar(f"retry {bucket}", n / total_r) + f"  n={n}")
    switches = m.get("worker_switch_count")
    if switches is not None:
        lines.append(f"worker_switches={switches}")
    lat = m.get("latency_stats") or {}
    if lat:
        lines.append("")
        lines.append("=== Latency (s) ===")
        for w, st in sorted(lat.items()):
            if not isinstance(st, dict):
                continue
            lines.append(
                f"{w}: avg={st.get('avg')} min={st.get('min')} max={st.get('max')} n={st.get('n')}"
            )
    src = m.get("_source", "")
    if src:
        lines.append("")
        lines.append(f"source: {src}")
        lines.append(f"ts: {m.get('ts', '')}")
    return "\n".join(lines)


def _require_ctk():
    if ctk is None:
        raise ImportError("customtkinter is required for MetricsPanel GUI")


class MetricsPanel((ctk.CTkFrame if ctk is not None else object)):  # type: ignore[misc]
    def __init__(self, parent, poll_ms: int = 4000):
        _require_ctk()
        super().__init__(parent)
        self.poll_ms = poll_ms

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(top, text=_t("metrics_title", default="Метрики"), font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text=_t("refresh", default="Обновить"), width=90, command=self.refresh).pack(side="right")

        self.box = ctk.CTkTextbox(self, state="disabled", wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.box.pack(fill="both", expand=True, padx=8, pady=8)

        self.after(500, self.refresh)
        self.after(self.poll_ms, self._tick)

    def _set_text(self, text: str) -> None:
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        self.box.configure(state="disabled")

    def refresh(self) -> None:
        def work():
            ensure_sys_path()
            return _load_latest_metrics(), _queue_counts()

        def apply(pair):
            m, q = pair if pair else ({}, {})
            self._render_metrics(m, q)

        try:
            from ui.async_poll import run_bg
            run_bg(self, work, apply, coalesce_key="metrics")
        except Exception:
            ensure_sys_path()
            apply((_load_latest_metrics(), _queue_counts()))

    def _render_metrics(self, m, q) -> None:
        self._set_text(format_metrics_board(m, q))

    def _tick(self) -> None:
        self.refresh()
        self.after(self.poll_ms, self._tick)

    def _cost_section(self) -> str:
        """P1.5 — session cost board (local = 0 ₽ explicit)."""
        return format_cost_section()
