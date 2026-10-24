# -*- coding: utf-8 -*-
"""P0.4 — compact go/no-go checklist for first launch."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Check:
    id: str
    ok: bool
    critical: bool
    detail: str



CHECK_LABELS = {
    "workers_yaml": "workers.yaml",
    "providers_yaml": "providers.yaml",
    "base_dir": "корень проекта",
    "config": "конфиг",
    "policy": "политика",
    "local_runtime": "локальные модели",
    "worker_capacity": "доступные воркеры",
    "desktop_queue": "очередь чата",
    "phone_filebus": "шина телефона",
    "strict_verify": "строгая верификация",
    "ui_deps": "зависимости UI",
    "max_parallel": "параллельность",
    "feature_flags": "feature flags",
    "git": "git",
    "ollama_up": "Ollama",
    "preferred_model": "модель coder",
    "aider_cli": "Aider CLI",
    "opencode_cli": "OpenCode CLI",
    "live_path": "live coding path",
    "cli_ollama": "cli ollama",
    "cli_aider": "cli aider",
    "cli_opencode": "cli opencode",
    "code_worker_stack": "code worker stack",
    "paid_gate": "платный шлюз (ALLOW_PAID)",
}

CHECK_TIPS = {
    "workers_yaml": "положите config/workers.yaml или запустите python dispatcher.py --init",
    "providers_yaml": "нужен config/providers.yaml",
    "base_dir": "запускайте из корня AgentBus",
    "config": "проверьте PYTHONPATH=src и структуру src/core",
    "local_runtime": "установите Ollama и модели: ollama pull qwen2.5-coder:7b",
    "worker_capacity": "включите хотя бы один воркер в workers.yaml / проверьте ключи",
    "desktop_queue": "создастся при первой задаче из чата",
    "ui_deps": "pip install -r requirements-ui.txt",
    "max_parallel": "для стабильности: AGENTBUS_MAX_PARALLEL_PROJECTS=1",
    "git": "git init в проекте задач, если нужен diff/rollback",
    "ollama_up": "ollama serve",
    "preferred_model": "ollama pull qwen2.5-coder:7b",
    "aider_cli": "pip install aider-chat",
    "opencode_cli": "установите OpenCode CLI или задайте OPENCODE_BIN",
    "live_path": "нужны Ollama + модель + Aider (исторический path) или OpenCode",
    "paid_gate": "AGENTBUS_ALLOW_PAID=1 включает платные облака; по умолчанию 0",
}

@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def critical_ok(self) -> bool:
        return all(c.ok for c in self.checks if c.critical)

    @property
    def score(self) -> tuple[int, int]:
        total = len(self.checks)
        ok = sum(1 for c in self.checks if c.ok)
        return ok, total

    def to_dict(self) -> dict[str, Any]:
        ok_n, total = self.score
        return {
            "critical_ok": self.critical_ok,
            "score": [ok_n, total],
            "verdict": "READY" if self.critical_ok else "BLOCKED",
            "checks": [
                {
                    "id": c.id,
                    "ok": c.ok,
                    "critical": c.critical,
                    "detail": c.detail,
                    "label": CHECK_LABELS.get(c.id, c.id),
                }
                for c in self.checks
            ],
        }

    def format_human(self, *, max_len: int = 4000) -> str:
        """FC-18: product text for chat / UI (RU)."""
        lines: list[str] = []
        ok_n, total = self.score
        if self.critical_ok:
            lines.append(f"✓ Диагностика: ГОТОВО К ЗАПУСКУ ({ok_n}/{total})")
        else:
            lines.append(f"✗ Диагностика: НУЖНЫ ИСПРАВЛЕНИЯ ({ok_n}/{total})")
        for c in self.checks:
            mark = "✓" if c.ok else "✗"
            crit = " [критично]" if c.critical and not c.ok else ""
            label = CHECK_LABELS.get(c.id, c.id)
            lines.append(f"  {mark} {label}: {c.detail}{crit}")
        fails = [c for c in self.checks if not c.ok]
        if fails:
            lines.append("")
            lines.append("Что сделать:")
            for c in fails:
                tip = CHECK_TIPS.get(c.id, "")
                if tip:
                    lines.append(f"  • {CHECK_LABELS.get(c.id, c.id)} — {tip}")
        else:
            lines.append("")
            lines.append("Можно: python dispatcher.py  |  python dispatcher_ui.py")
        return "\n".join(lines)[:max_len]


def _check_config(add) -> None:
    """TD-017: пути/конфиг — код перенесён из run_doctor без изменений."""
    try:
        from core.config import WORKERS_FILE, PROVIDERS_FILE, BASE_DIR
        add("workers_yaml", Path(WORKERS_FILE).is_file(), str(WORKERS_FILE), critical=True)
        add("providers_yaml", Path(PROVIDERS_FILE).is_file(), str(PROVIDERS_FILE), critical=True)
        add("base_dir", Path(BASE_DIR).is_dir(), str(BASE_DIR), critical=True)
    except Exception as exp:
        add("config", False, f"{type(exp).__name__}: {exp}", critical=True)


def _check_policy(add) -> tuple[bool, bool]:
    """TD-017: политика; возвращает (allow_cloud, prefer_local)."""
    try:
        from core.policy import load_policy
        pol = load_policy()
        add(
            "policy",
            True,
            f"{pol.name} cloud={pol.allow_cloud} privacy={pol.privacy}",
            critical=False,
        )
        allow_cloud = bool(pol.allow_cloud)
        prefer_local = bool(getattr(pol, "prefer_local", True))
        return allow_cloud, prefer_local
    except Exception as exp:
        allow_cloud, prefer_local = True, True
        add("policy", False, str(exp), critical=False)
        return allow_cloud, prefer_local


def _check_local_runtimes(add, *, prefer_local: bool, allow_cloud: bool) -> bool:
    """TD-017: локальные рантаймы; возвращает local_ok."""
    local_ok = False
    local_detail = "none"
    try:
        from cli.init_wizard import discover_local_stack
        disc = discover_local_stack()
        parts = []
        for r in disc.get("runtimes") or []:
            mark = "OK" if r.get("ok") else "--"
            nmod = len(r.get("models") or [])
            parts.append(f"{r.get('id')}[{mark}/{nmod}]")
            if r.get("ok"):
                local_ok = True
        local_detail = ", ".join(parts) or "no runtimes discovered"
    except Exception as exp:
        local_detail = f"discover err: {exp}"
    add(
        "local_runtime",
        local_ok,
        local_detail,
        critical=bool(prefer_local and not allow_cloud),
    )
    return local_ok


def _check_worker_capacity(add, *, local_ok: bool) -> None:
    """TD-017: ёмкость воркеров + платный гейт (ALLOW_PAID)."""
    cloud_usable = False
    try:
        from providers.registry import load_providers
        from providers.capacity import FreeCapacityManager
        from core.workers import load_workers
        # Платный шлюз: при ALLOW_PAID=0 платные воркеры роутер не выбирает
        # (core/paid_gate.py), поэтому и в отчёте они не должны считаться
        # доступной ёмкостью — иначе doctor показывает несуществующий запас.
        try:
            from core.config import ALLOW_PAID as _ALLOW_PAID
            from core.paid_gate import is_paid_worker
        except Exception:
            _ALLOW_PAID, is_paid_worker = False, None

        def _paid(w) -> bool:
            try:
                return bool(is_paid_worker(w)) if is_paid_worker else False
            except Exception:
                return False

        ps = load_providers()
        cm = FreeCapacityManager(ps)
        usable_workers = []
        paid_workers: list[str] = []
        for w in load_workers():
            is_paid = _paid(w)
            if is_paid:
                paid_workers.append(w.name)
            if not getattr(w, "enabled", True):
                continue
            if is_paid and not _ALLOW_PAID:
                continue
            try:
                if cm.worker_usable(w):
                    usable_workers.append(w.name)
                    if getattr(w, "provider", "") not in ("ollama", "local", ""):
                        cloud_usable = True
                    else:
                        # local worker counts as capacity
                        cloud_usable = cloud_usable or local_ok
            except Exception as w_exp:
                # Do not treat probe failure as "worker absent" silently
                try:
                    import logging
                    logging.getLogger("agentbus.doctor").warning(
                        "worker_usable(%s): %s", getattr(w, "name", w), w_exp
                    )
                except Exception:
                    pass
        add(
            "worker_capacity",
            len(usable_workers) > 0,
            (", ".join(usable_workers[:8]) if usable_workers else "no usable workers"),
            critical=True,
        )
        blocked = [n for n in paid_workers if not _ALLOW_PAID]
        detail = (
            f"ALLOW_PAID={_ALLOW_PAID}; платных воркеров: "
            f"{', '.join(paid_workers) or '—'}"
        )
        if blocked:
            detail += f" — заблокированы гейтом: {', '.join(blocked)}"
        add("paid_gate", True, detail, critical=False)
    except Exception as exp:
        add("worker_capacity", False, str(exp), critical=True)


def _check_cli_tools(add, *, local_ok: bool) -> None:
    """TD-017: CLI-инструменты воркеров (ollama/aider/opencode)."""
    try:
        import os
        import shutil
        # honour AIDER_PATH / OPENCODE_PATH too: the tips below tell the user to
        # set them, so a PATH-only check reported a correctly configured tool as missing
        tools = {
            "ollama": shutil.which("ollama") or os.environ.get("OLLAMA_PATH"),
            "aider": shutil.which("aider") or os.environ.get("AIDER_PATH"),
            "opencode": (
                shutil.which("opencode")
                or os.environ.get("OPENCODE_BIN")
                or os.environ.get("OPENCODE_PATH")
            ),
        }
        for name, path in tools.items():
            ok = bool(path)
            add(
                f"cli_{name}",
                ok,
                path or f"{name} not on PATH (live worker may fail)",
                critical=False,
            )
        # at least one code worker CLI or local runtime
        has_code_cli = bool(tools.get("aider") or tools.get("opencode") or local_ok)
        add(
            "code_worker_stack",
            has_code_cli,
            "aider/opencode/local runtime for code tasks",
            critical=False,
        )
    except Exception as exp:
        add("cli_tools", False, str(exp), critical=False)


def _check_worker_stack(add, *, prefer_local: bool, allow_cloud: bool) -> None:
    """TD-017: Day-1 soft-пробы Ollama/модели/live-path."""
    try:
        from core.worker_diagnostics import run_worker_stack_report
        stack = run_worker_stack_report(timeout=1.5)
        for p in stack.probes:
            # live probes are informational for Doctor critical_ok unless prefer_local-only
            crit = bool(p.critical_for_live and prefer_local and not allow_cloud)
            add(p.id, p.ok, p.detail, critical=crit)
    except Exception as exp:
        add("live_path", False, f"worker_diagnostics: {exp}", critical=False)


def _check_desktop_queue(add) -> None:
    """TD-017: путь desktop-очереди."""
    try:
        from core.local_queue import get_local_queue
        from core.config import BASE_DIR
        n = get_local_queue(Path(BASE_DIR)).size()
        add("desktop_queue", True, f"size={n} (chat primary)", critical=False)
    except Exception as exp:
        add("desktop_queue", False, str(exp), critical=False)


def _check_phone_bus(add) -> None:
    """TD-017: опциональная телефонная шина."""
    try:
        from core.feature_flags import is_enabled
        phone = is_enabled("phone_filebus", default=False)
        add("phone_filebus", True, f"enabled={phone} (optional)", critical=False)
    except Exception as exp:
        add("phone_filebus", False, str(exp), critical=False)


def _check_strict_verify(add) -> None:
    """TD-017: AGENTBUS_STRICT_VERIFY."""
    import os
    strict = (os.getenv("AGENTBUS_STRICT_VERIFY") or "1").strip().lower() in (
        "1", "true", "yes", "on",
    )
    add("strict_verify", True, f"AGENTBUS_STRICT_VERIFY={'1' if strict else '0'}", critical=False)


def _check_ui_deps(add) -> None:
    """TD-017: зависимости UI (customtkinter)."""
    try:
        import customtkinter  # noqa: F401
        add("ui_deps", True, "customtkinter OK", critical=False)
    except Exception:
        add("ui_deps", False, "pip install -r requirements-ui.txt", critical=False)


def _check_max_parallel(add) -> None:
    """TD-017: параллельность проектов (P0.5)."""
    try:
        from core.config import MAX_PARALLEL_PROJECTS
        mp = int(MAX_PARALLEL_PROJECTS or 1)
        add(
            "max_parallel",
            mp <= 1,
            f"AGENTBUS_MAX_PARALLEL_PROJECTS={mp}"
            + ("" if mp <= 1 else " — риск конфликтов git; для beginner ставь 1"),
            critical=False,
        )
    except Exception as exp:
        add("max_parallel", True, f"default 1 ({exp})", critical=False)


def _check_feature_flags(add) -> None:
    """TD-017: feature flags (informational)."""
    try:
        from core.feature_flags import list_flags
        flags = list_flags() if callable(list_flags) else {}
        if not flags:
            from core.feature_flags import get_all
            flags = get_all() if callable(get_all) else {}
        if isinstance(flags, dict) and flags:
            on = [k for k, v in flags.items() if v]
            add("feature_flags", True, f"on={len(on)}/{len(flags)}", critical=False)
        else:
            add("feature_flags", True, "defaults", critical=False)
    except Exception:
        try:
            from core.feature_flags import is_enabled
            add("feature_flags", True, f"skills={is_enabled('skills', True)}", critical=False)
        except Exception as exp:
            add("feature_flags", False, str(exp)[:120], critical=False)


def _check_git(add) -> None:
    """TD-017: git-репозиторий (diff/rollback)."""
    try:
        import subprocess
        from core.config import BASE_DIR
        r = subprocess.run(
            ["git", "-C", str(BASE_DIR), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        ok_git = r.returncode == 0 and "true" in (r.stdout or "").lower()
        add("git", ok_git, "repo OK" if ok_git else "не git-репозиторий (diff/rollback ограничены)", critical=False)
    except Exception as exp:
        add("git", False, str(exp)[:120], critical=False)


def run_doctor() -> DoctorReport:
    """Collect readiness checks without raising."""
    rep = DoctorReport()

    def add(cid: str, ok: bool, detail: str, *, critical: bool = False) -> None:
        rep.checks.append(Check(cid, bool(ok), critical, detail))

    # --- paths / config ---
    _check_config(add)

    # --- policy ---
    allow_cloud, prefer_local = _check_policy(add)

    # --- local runtimes ---
    local_ok = _check_local_runtimes(add, prefer_local=prefer_local, allow_cloud=allow_cloud)

    # --- cloud keys (only critical if policy needs cloud and no local) ---
    _check_worker_capacity(add, local_ok=local_ok)

    # --- worker CLI tools (warning only; live needs these) ---
    _check_cli_tools(add, local_ok=local_ok)

    # --- Day-1: Ollama + preferred model + live path (soft probes, never raise) ---
    _check_worker_stack(add, prefer_local=prefer_local, allow_cloud=allow_cloud)

    # --- desktop queue path ---
    _check_desktop_queue(add)

    # --- phone bus optional ---
    _check_phone_bus(add)

    # --- strict verify ---
    _check_strict_verify(add)

    # --- UI optional ---
    _check_ui_deps(add)

    # --- concurrency safety (P0.5) ---
    _check_max_parallel(add)

    # --- feature flags (informational) ---
    _check_feature_flags(add)

    # --- git (optional, for diff/rollback) ---
    _check_git(add)

    return rep


def print_doctor(rep: DoctorReport | None = None) -> int:
    """Print checklist; return 0 if all critical checks pass."""
    rep = rep or run_doctor()
    print()
    print(rep.format_human())
    print()
    return 0 if rep.critical_ok else 1


def doctor_text() -> str:
    """FC-18: string for UI without print side-effects."""
    return run_doctor().format_human()


def capability_section() -> str:
    """Optional human block for doctor / UI (never raises)."""
    try:
        from core.configuration_advisor import first_run_summary
        return first_run_summary(probe_network=True)
    except Exception:
        try:
            from core.capability_scan import scan_capabilities
            return scan_capabilities(probe_network=True, include_config=True).format_human()
        except Exception as exp:
            return f"Capability scan unavailable: {exp}"


def worker_stack_section() -> str:
    """Day-1: Ollama/model/aider/opencode block for doctor_full_text."""
    try:
        from core.worker_diagnostics import run_worker_stack_report
        return run_worker_stack_report(timeout=1.5).format_human()
    except Exception as exp:
        return f"Worker stack unavailable: {exp}"


def doctor_verdict_line(report: DoctorReport | None = None) -> str:
    """Single-line readiness verdict: 'VERDICT: READY' / 'VERDICT: BLOCKED'."""
    rep = report if report is not None else run_doctor()
    try:
        verdict = rep.to_dict().get("verdict") or ("READY" if rep.critical_ok else "BLOCKED")
    except Exception:
        verdict = "ERROR"
    return f"VERDICT: {verdict}"


def doctor_full_text(*, include_capability: bool = True, include_board: bool = True) -> str:
    """Checklist + optional configuration advisor + status board for UI/CLI."""
    report = run_doctor()
    body = report.format_human()
    body = body + "\n\n" + worker_stack_section()
    if include_capability:
        body = body + "\n\n" + capability_section()
    if include_board:
        try:
            from utils.status_board import format_board_text
            body = body + "\n\n" + format_board_text()
        except Exception as exp:
            body = body + f"\n\n(status board unavailable: {exp})"
    body = body + "\n\n" + doctor_verdict_line(report)
    return body
