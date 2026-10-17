"""Offline smoke checks before first live run (no Ollama/API required).

Usage (from AgentBus root)::

    python scripts/smoke_offline.py
    python scripts/smoke_offline.py --preset minimal

Exit 0 = all checks green.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

def _ok(msg: str) -> None:
    '''"""Internal: _ok(msg)."""'''
    print(f'  [OK] {msg}')

def _fail(msg: str) -> None:
    '''"""Internal: _fail(msg)."""'''
    print(f'  [FAIL] {msg}')

def check_imports() -> list[str]:
    '''"""check_imports()."""'''
    issues = []
    mods = ['core.bus', 'core.runtime', 'core.feature_flags', 'core.plugin_registry', 'skills.matcher', 'skills.builtin', 'core.verify_policy', 'core.task_safety', 'core.pipeline_stages', 'core.tool_registry', 'safety.static_guard', 'core.e2e_harness', 'intelligence.solution_cache', 'skills.autopilot', 'utils.cost_tracker', 'utils.pipeline_events', 'intelligence.task_graph', 'safety.diff_policy', 'core.task_contract', 'core.verification_engine', 'core.mock_worker', 'core.pipeline_e2e']
    for m in mods:
        try:
            __import__(m)
            _ok(f'import {m}')
        except Exception as exc:
            _fail(f'import {m}: {exc}')
            issues.append(str(exc))
    return issues

def check_presets() -> list[str]:
    '''"""check_presets()."""'''
    issues = []
    from core.feature_flags import list_presets, apply_preset, get_flags, reload_flags
    names = {p['name'] for p in list_presets()}
    for need in ('minimal', 'balanced', 'night_autonomous', 'beginner_ru'):
        if need not in names:
            _fail(f'preset missing: {need}')
            issues.append(need)
        else:
            _ok(f'preset {need}')
    return issues

def check_verify_ladder() -> list[str]:
    '''"""check_verify_ladder()."""'''
    issues = []
    from core.verify_policy import apply_verify_policy
    from core.task_safety import resolve_git_policy, check_diff_budget
    raw = apply_verify_policy({'message': 'refactor', 'files': ['a.py'], 'verify': [], 'metadata': {'complexity': 4, 'source': 'autopilot'}})
    if not raw.get('verify'):
        _fail('verify_policy did not inject commands')
        issues.append('verify empty')
    else:
        _ok(f'verify inject: {raw['verify'][:2]}')
    pol = resolve_git_policy({'metadata': {'source': 'autopilot'}}, default='park')
    if pol != 'branch':
        _fail(f'git policy expected branch, got {pol}')
        issues.append('git_policy')
    else:
        _ok('git policy branch for autopilot')
    budget = check_diff_budget(root=str(ROOT), stage_paths=[f'x{i}.py' for i in range(40)], task={'metadata': {'source': 'autopilot'}})
    if budget.get('ok'):
        _fail('diff budget should reject 40 files')
        issues.append('diff_budget')
    else:
        _ok('diff budget rejects oversized change')
    return issues

def check_retry_enforcer() -> list[str]:
    '''"""check_retry_enforcer()."""'''
    issues = []
    from core.runtime import Runtime
    if not hasattr(Runtime, '_quarantine_exhausted_task'):
        _fail('Runtime._quarantine_exhausted_task missing')
        issues.append('quarantine enforcer')
    else:
        _ok('retry quarantine enforcer present')
    from core.tool_registry import UnifiedToolGateway
    g = UnifiedToolGateway('.')
    _ok(f'UnifiedToolGateway: {type(g).__name__}')
    return issues

def check_channels() -> list[str]:
    '''"""check_channels()."""'''
    issues = []
    ch_root = ROOT / 'channels'
    if not ch_root.is_dir():
        ch_root.mkdir(parents=True, exist_ok=True)
    for ch in ('gpt', 'autopilot'):
        for stage in ('incoming', 'processing', 'done', 'errors', 'deferred'):
            d = ch_root / ch / stage
            d.mkdir(parents=True, exist_ok=True)
        _ok(f'channels/{ch}/{{incoming,processing,done,errors,deferred}}')
    return issues

def check_pytest_subset() -> list[str]:
    '''"""check_pytest_subset()."""'''
    issues = []
    import subprocess
    tests = ['tests/test_pipeline_metrics.py', 'tests/test_task_safety.py', 'tests/test_plugins_presets.py', 'tests/test_attachments_and_flags.py']
    existing = [t for t in tests if (ROOT / t).is_file()]
    if not existing:
        _fail('no offline test files found')
        return ['no tests']
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', *existing], cwd=str(ROOT), env={**dict(**{k: v for k, v in __import__('os').environ.items()}), 'PYTHONPATH': f'{SRC}:{ROOT}'}, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        _fail(f'pytest exit {r.returncode}')
        print(r.stdout[-500:] if r.stdout else '')
        print(r.stderr[-500:] if r.stderr else '')
        issues.append('pytest')
    else:
        last = (r.stdout or '').strip().splitlines()[-1] if r.stdout else 'passed'
        _ok(f'pytest: {last}')
    return issues

def maybe_apply_preset(name: str | None) -> None:
    '''"""maybe_apply_preset(name).

Returns:
    Result of maybe_apply_preset.
"""'''
    if not name:
        return
    from core.feature_flags import apply_preset
    flags = apply_preset(name, save=True)
    on = sum((1 for v in flags.values() if v))
    print(f'Applied preset {name!r}: {on} features ON')


def check_p0_contracts() -> list[str]:
    """Executor / reclaim / CostSnapshot / gate_done offline contracts."""
    issues = []
    try:
        from core.executor import ExecutionResult
        r = ExecutionResult(ok=True, code=0, stdout="ok", stderr="", timed_out=False)
        assert r.ok is True and r.success is True and r.exit_code == 0
        _ok("ExecutionResult.ok")
    except Exception as exc:
        _fail(f"ExecutionResult: {exc}")
        issues.append(str(exc))
    try:
        from core.reclaim import compute_stuck_timeout_sec, write_lease
        t = compute_stuck_timeout_sec({"metadata": {"complexity": 3}}, base_sec=300, max_sec=1800)
        assert t >= 60
        _ok(f"reclaim timeout scales ({t:.0f}s)")
    except Exception as exp:
        _fail(f"reclaim: {exp}")
        issues.append(str(exp))
    try:
        from utils.cost_tracker import CostSnapshot
        d = CostSnapshot(skill_saves=1).to_dict()
        assert "skill_saves" in d
        _ok("CostSnapshot")
    except Exception as exp:
        _fail(f"CostSnapshot: {exp}")
        issues.append(str(exp))
    try:
        from core.verification_engine import gate_done, VerificationReport, finalize_report
        ok, _ = gate_done(True, None)
        assert ok is False
        rep = finalize_report(VerificationReport(passed=True, checks=[]))
        ok2, _ = gate_done(True, rep)
        assert ok2 is True
        _ok("gate_done contract")
    except Exception as exp:
        _fail(f"gate_done: {exp}")
        issues.append(str(exp))
    try:
        from utils.task_trace import complete_task_trace, GLOBAL_TRACES
        GLOBAL_TRACES.start("smoke-t1", attempt=1)
        tr = complete_task_trace("smoke-t1", "DONE")
        assert tr is not None and tr.final_status == "DONE"
        _ok("TaskTrace terminal")
    except Exception as exp:
        _fail(f"TaskTrace: {exp}")
        issues.append(str(exp))
    return issues

def main() -> int:
    '''"""main()."""'''
    ap = argparse.ArgumentParser(description='AgentBus offline smoke')
    ap.add_argument('--preset', default='', help='minimal|balanced|night_autonomous')
    ap.add_argument('--skip-pytest', action='store_true')
    args = ap.parse_args()
    print('=== AgentBus smoke_offline ===')
    print(f'ROOT={ROOT}')
    issues: list[str] = []
    issues += check_imports()
    issues += check_presets()
    issues += check_verify_ladder()
    issues.extend(check_p0_contracts())
    issues += check_retry_enforcer()
    issues += check_channels()
    if not args.skip_pytest:
        issues += check_pytest_subset()
    if args.preset:
        try:
            maybe_apply_preset(args.preset)
            _ok(f'preset applied: {args.preset}')
        except Exception as exc:
            _fail(f'preset: {exc}')
            issues.append(str(exc))
    print('=== summary ===')
    if issues:
        print(f'ISSUES: {len(issues)}')
        for i in issues:
            print(f'  - {i}')
        return 1
    print('READY (offline smoke green)')
    print('Next: python dispatcher.py --diagnose')
    print('      python dispatcher_ui.py')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
