# -*- coding: utf-8 -*
"""Offline smoke: python diagnose.py

Не меняет runtime. Проверяет providers/workers/capacity/file-bus/CLI.
"""
from __future__ import annotations

import sys
from pathlib import Path
from shutil import which

SRC = Path(__file__).resolve().parents[1]  # src/
ROOT = Path(__file__).resolve().parents[2]  # AgentBus root
for _p in (SRC, ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)



def _env_check_python(issues: list[str]) -> None:
    import sys
    ver = sys.version_info
    py = f"{ver.major}.{ver.minor}.{ver.micro}"
    ok_py = (ver.major, ver.minor) >= (3, 10)
    print(f"python            : {py} ({'OK' if ok_py else 'нужна 3.10+'})")
    if not ok_py:
        issues.append(f"python {py} < 3.10")


def _env_check_ollama(issues: list[str]) -> list[str]:
    import json
    import os
    import urllib.request

    host = (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    models_found: list[str] = []
    try:
        req = urllib.request.Request(f"{host}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models_found = [str(m.get("name") or "") for m in (data.get("models") or [])]
        print(f"ollama API        : OK ({host})")
    except Exception as exc:
        print(f"ollama API        : НЕДОСТУПЕН ({host}) — {type(exc).__name__}")
        issues.append(f"ollama API unavailable: {host}")
    return models_found


def _env_check_models(issues: list[str], models_found: list[str]) -> None:
    import os

    need_coder = (os.getenv("CODER_MODEL") or "qwen2.5-coder:7b").replace("ollama_chat/", "")
    need_meta = (os.getenv("META_MODEL") or "qwen2.5:1.5b-instruct").replace("ollama_chat/", "")
    for label, need in (("coder", need_coder), ("meta", need_meta)):
        want = need.lower()
        family = want.split(":")[0]
        hit = any(
            want == m.lower() or family in m.lower() or m.lower().startswith(family)
            for m in models_found
        )
        mark = "OK" if hit else "НЕТ"
        print(f"  модель {label:5}     : [{mark}] {need}")
        if models_found and not hit:
            issues.append(f"missing ollama model: {need}")
    if models_found:
        print(f"  все модели      : {', '.join(models_found[:10])}")
    elif not models_found and "ollama API unavailable" not in " ".join(issues):
        print("  все модели      : (пусто)")


def _env_print_feature_toggles() -> None:
    import os

    pev = os.getenv("AGENTBUS_PEV", "1")
    pev_cx = os.getenv("AGENTBUS_PEV_MIN_CX", "4")
    pev_llm = os.getenv("AGENTBUS_PEV_LLM", "0")
    print(f"PEV               : enabled={pev} min_cx={pev_cx} llm_plan={pev_llm}")
    print(f"ctx budget chars  : {os.getenv('AGENTBUS_CTX_TOTAL_CHARS', '24000')}")
    print(f"RAG BM25          : {os.getenv('AGENTBUS_RAG_BM25', '1')}")
    print(f"custom skills     : {os.getenv('AGENTBUS_LOAD_CUSTOM_SKILLS', '0')}")
    print(f"sub isolate       : {os.getenv('AGENTBUS_SUB_ISOLATE', '0')}")
    print(f"syntax guard      : {os.getenv('AGENTBUS_SYNTAX_GUARD', '1')}")
    print(f"file sentinel     : {os.getenv('AGENTBUS_FILE_SENTINEL', '1')}")


def _env_check_feature_flags(issues: list[str]) -> None:
    try:
        from core.feature_flags import snapshot
        snap = snapshot()
        enabled = snap.get("enabled") or []
        disabled = snap.get("disabled") or []
        print(f"features ON       : {len(enabled)}")
        if disabled:
            print(f"features OFF      : {', '.join(disabled)}")
        cfg = snap.get("config")
        if cfg:
            print(f"features file     : {cfg}")
    except Exception as exc:
        print(f"feature_flags ERR : {exc}")
        issues.append(f"feature_flags: {exc}")


def _env_check_channels(issues: list[str]) -> None:
    from pathlib import Path

    try:
        from core.config import BUS_ROOT, CHANNELS
        missing: list[str] = []
        for ch in CHANNELS:
            for stage in ("incoming", "processing", "done", "errors", "deferred"):
                p = Path(str(BUS_ROOT)) / "channels" / ch / stage
                if not p.is_dir():
                    missing.append(str(p))
        if missing:
            print(f"channels missing  : {len(missing)}")
            for m in missing[:8]:
                print(f"  отсутствует     : {m}")
            issues.append(f"channels missing: {len(missing)}")
        else:
            print(f"channels          : OK ({', '.join(CHANNELS)})")
    except Exception as exc:
        print(f"channels ERR      : {exc}")
        issues.append(f"channels: {exc}")


def _env_check_provider_keys(issues: list[str]) -> None:
    import os

    try:
        try:
            from providers import load_providers
        except ImportError:
            from providers.registry import load_providers  # type: ignore
        for p in load_providers():
            pid = getattr(p, "id", "") or ""
            if pid in ("ollama", "local"):
                continue
            ke = getattr(p, "api_key_env", "") or ""
            has = bool(getattr(p, "api_key", "")) or (bool(ke) and bool(os.getenv(ke, "")))
            if not has and not (hasattr(p, "is_usable") and p.is_usable() and not ke):
                if ke or getattr(p, "billing", "") in ("free", "paid", "hybrid"):
                    if not has:
                        print(f"  ключ отсутствует: {pid} (env: {ke or '—'})")
                        if ke:
                            issues.append(f"missing key env: {ke} for {pid}")
    except Exception as exp:
        print(f"keys ERR          : {exp}")


def _env_check_optional_deps(issues: list[str]) -> None:
    for mod, purpose in (("customtkinter", "UI"), ("pystray", "трей"), ("yaml", "config")):
        try:
            __import__(mod if mod != "yaml" else "yaml")
            print(f"dep {mod:14} : OK ({purpose})")
        except ImportError:
            print(f"dep {mod:14} : НЕТ ({purpose}, опционально)" if mod != "yaml"
                  else f"dep {mod:14} : НЕТ ({purpose})")
            if mod == "yaml":
                issues.append("missing PyYAML")


def _env_check_workers(issues: list[str]) -> None:
    import os

    try:
        from core.workers import load_workers
        prov_by_id = {}
        try:
            try:
                from providers import load_providers as _lp
            except ImportError:
                from providers.registry import load_providers as _lp  # type: ignore
            for _p in _lp():
                prov_by_id[str(getattr(_p, "id", "") or "")] = _p
        except Exception as exp:
            print(f"worker keys ERR : {exp}")
        print("  --- workers ---")
        try:
            from core.config import ALLOW_PAID as _ALLOW_PAID
            from core.paid_gate import is_paid_worker
        except Exception:
            _ALLOW_PAID, is_paid_worker = False, None
        _paid_names: list[str] = []
        _free_names: list[str] = []

        def _line(w, status: str, note: str = "") -> str:
            try:
                paid = bool(is_paid_worker(w)) if is_paid_worker else False
            except Exception:
                paid = False
            mark = "[PAID] " if paid else "[FREE] "
            (_paid_names if paid else _free_names).append(w.name)
            blocked = paid and not _ALLOW_PAID
            tail = f"  {'- ЗАБЛОКИРОВАН ALLOW_PAID=0' if blocked else ''}"
            return f"  {w.name:<22} {mark}{status:<10} {note}{tail}"

        for w in load_workers():
            if not getattr(w, "enabled", True):
                print(_line(w, "DISABLED"))
                continue
            caps = getattr(w, "capabilities", ()) or ()
            if "plan" in (caps if not isinstance(caps, str) else (caps,)):
                print(_line(w, "PLAN_ONLY", "файлы не пишет, задачи на запись исключены роутером"))
                continue
            prov = prov_by_id.get(str(getattr(w, "provider", "") or ""))
            ke = str(getattr(w, "api_key_env", "") or "") or str(
                getattr(prov, "api_key_env", "") or "")
            pid = str(getattr(w, "provider", "") or "")
            if pid in ("local", "ollama", ""):
                print(_line(w, "OK", "local, ключ не нужен"))
                continue
            if prov is None:
                print(_line(w, "NO_PROVIDER", f"провайдер {pid!r} отсутствует в providers.yaml"))
                issues.append(f"worker {w.name}: провайдер {pid} не зарегистрирован")
                continue
            has = bool(getattr(prov, "api_key", "")) or (bool(ke) and bool(os.getenv(ke, "")))
            if not has and not (hasattr(prov, "is_usable") and prov.is_usable() and not ke):
                print(_line(w, "NO_KEY", f"env: {ke or '—'}"))
                issues.append(f"worker {w.name} без ключа (env: {ke or '—'})")
            else:
                print(_line(w, "OK", f"env: {ke or '—'}"))
        print(f"  ALLOW_PAID={_ALLOW_PAID}: платных воркеров {len(_paid_names)} "
              f"({', '.join(_paid_names) or '—'}), бесплатных {len(_free_names)} "
              f"({', '.join(_free_names) or '—'})")
        if _paid_names and not _ALLOW_PAID:
            print("  → платные воркеры отсечены роутером (ALLOW_PAID=0)")
    except Exception as exc:
        print(f"workers ERR       : {exc}")
        issues.append(f"workers: {exc}")


def diagnose_environment() -> list[str]:
    """Extra env checks for first launch. Returns list of issue strings (empty = OK)."""
    issues: list[str] = []
    print("\n--- окружение ---")
    _env_check_python(issues)
    models_found = _env_check_ollama(issues)
    _env_check_models(issues, models_found)
    _env_print_feature_toggles()
    _env_check_feature_flags(issues)
    _env_check_channels(issues)
    _env_check_provider_keys(issues)
    _env_check_optional_deps(issues)
    _env_check_workers(issues)
    return issues


def main() -> int:
    print("=== AgentBus diagnose ===")
    errors = 0
    from core.config import (
        BUS_ROOT, PROJECT_ROOT, PROVIDERS_FILE, WORKERS_FILE,
        ALLOW_PAID, USE_DYNAMIC,
        AIDER_PATH, OPENCODE_PATH, OLLAMA_PATH,
    )
    print(f"BUS_ROOT       : {BUS_ROOT}")
    print(f"PROJECT_ROOT   : {PROJECT_ROOT}")
    print(f"PROVIDERS_FILE : {PROVIDERS_FILE} exists={Path(PROVIDERS_FILE).is_file()}")
    print(f"WORKERS_FILE   : {WORKERS_FILE} exists={Path(WORKERS_FILE).is_file()}")
    print(f"ALLOW_PAID     : {ALLOW_PAID}")
    print(f"USE_DYNAMIC    : {USE_DYNAMIC}")
    import os
    print(f"AGENTBUS_META  : {os.getenv('AGENTBUS_META', '') or '0'}")
    print(f"META_MODEL     : {os.getenv('META_MODEL', 'qwen2.5:1.5b-instruct')}")

    try:
        from providers import load_providers, FreeCapacityManager
        ps = load_providers()
        print(f"providers      : {len(ps)}")
        for p in ps:
            flag = "OK" if p.is_usable() else "skip"
            print(f"  [{flag}] {p.id:14} billing={p.billing:6} models={len(p.models)}")
        cap = FreeCapacityManager(ps)
        snap = cap.deferred_snapshot()
        print(f"deferred       : {snap}")
    except Exception as e:
        print(f"providers ERR  : {e}")
        errors += 1

    try:
        from core.workers import load_workers
        ws = load_workers()
        print(f"workers        : {len(ws)}")
        for w in ws:
            print(f"  [{'ON' if w.enabled else 'off'}] {w.name:22} {w.harness}/{w.provider} t={w.timeout}")
    except Exception as e:
        print(f"workers ERR    : {e}")
        errors += 1

    try:
        from safety.health import HealthRegistry
        from core.workers import load_workers
        hl = HealthRegistry()
        for w in load_workers():
            hl.register(w.name, w.max_parallel)
            hl.state(w.name)
        snap = hl.operator_snapshot()
        print(f"health         : {len(snap)} workers")
        for r in snap[:8]:
            print(f"  {r.get('name','?'):22} {r.get('status','?')}")
    except Exception as e:
        print(f"health ERR     : {e}")
        errors += 1

    try:
        from core.bus import FileBus
        from core.config import CHANNELS
        bus = FileBus(BUS_ROOT, CHANNELS)
        bus.ensure()
        print(f"file-bus       : OK ({BUS_ROOT})")
    except Exception as e:
        print(f"file-bus ERR   : {e}")
        errors += 1

    for name, path in (("aider", AIDER_PATH), ("opencode", OPENCODE_PATH), ("ollama", OLLAMA_PATH)):
        found = which(path) or which(name)
        print(f"CLI {name:10}: {found or 'NOT FOUND'}")

    try:
        from core.feature_flags import snapshot, config_path
        snap = snapshot()
        print(f"features file  : {snap.get('config') or 'defaults (no yaml)'}")
        en = snap.get("enabled") or []
        dis = snap.get("disabled") or []
        print(f"features ON    : {len(en)} → {', '.join(en) if en else '(none)'}")
        print(f"features OFF   : {len(dis)} → {', '.join(dis) if dis else '(none)'}")
    except Exception as e:
        print(f"features ERR   : {e}")
        errors += 1

    try:
        print("--- product contracts ---")
        from core.runtime_decision import decide_terminal, evidence_snapshot
        d = decide_terminal(evidence_snapshot(exec_ok=True, verification={"ok": True, "passed": True}))
        print(f"  decide_terminal DONE gate : {d.get('terminal_state')}")
        from core.worker_fallback import allow_worker_fallback
        print(f"  fallback deny verify     : {not allow_worker_fallback(kind='verification_failed')}")
        from core.night_runtime_bridge import make_execute_fn
        out = make_execute_fn(mock=True)({"metadata": {"plan_step_id": "diag"}})
        print(f"  night mock execute       : {out.get('terminal_state')}")
        from core.task_continuity import continuity_for_next_prompt
        b = continuity_for_next_prompt({"attempts": 1, "result": {"error": "x", "changed_files": ["a.py"]}})
        print(f"  continuity block         : {'yes' if 'PREVIOUS' in b else 'no'}")
    except Exception as e:
        print(f"product contracts ERR: {e}")
        errors += 1

    try:
        env_issues = diagnose_environment()
        errors += len(env_issues)
    except Exception as e:
        print(f"environment ERR: {e}")
        errors += 1

    try:
        from core.worker_route_surface import format_route_for_doctor
        print("--- worker route (sample) ---")
        print(format_route_for_doctor())
    except Exception as e:
        print(f"worker route   : ERR {e}")

    print("=== end ===")
    print("READY" if errors == 0 else f"ISSUES: {errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
