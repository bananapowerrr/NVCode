#!/usr/bin/env bash
# Offline freeze gate — no Ollama/network required.
# Exit 0 = ready for LIVE_ACCEPTANCE on PC.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/src:${ROOT}:${PYTHONPATH:-}"

echo "=========================================="
echo " AgentBus OFFLINE FREEZE GATE"
echo "=========================================="
echo "ROOT=$ROOT"
echo

fail=0

echo "== 1. offline_acceptance_matrix =="
if python scripts/offline_acceptance_matrix.py; then
  echo "  matrix OK"
else
  echo "  matrix FAIL"
  fail=1
fi
echo

echo "== 2. live_smoke --mock =="
if python scripts/live_smoke.py --mock; then
  echo "  live_smoke mock OK"
else
  echo "  live_smoke mock FAIL"
  fail=1
fi
echo

echo "== 3. doctor VERDICT =="
if python -c "from core.doctor import doctor_verdict_line, run_doctor; r=run_doctor(); print(doctor_verdict_line(r)); raise SystemExit(0 if r.critical_ok else 1)"; then
  echo "  doctor OK"
else
  echo "  doctor FAIL (critical)"
  fail=1
fi
echo

echo "== 4. targeted pytest =="
if python -m pytest -q   tests/test_run_log.py   tests/test_doctor_verdict.py   tests/test_format_task_report.py   tests/test_history_report_wire.py   tests/test_p0_fail_closed.py   tests/test_p1_loader_queue.py   tests/test_analyze_reconcile.py   tests/test_plan_done_ui_message.py   tests/test_status_board_text.py   --tb=line; then
  echo "  pytest OK"
else
  echo "  pytest FAIL"
  fail=1
fi
echo

if [ "$fail" -eq 0 ]; then
  echo "=========================================="
  echo " FREEZE: GREEN — proceed to LIVE on PC"
  echo " See docs/LIVE_ACCEPTANCE.md"
  echo "=========================================="
  exit 0
fi

echo "=========================================="
echo " FREEZE: RED — fix offline before live"
echo "=========================================="
exit 1
