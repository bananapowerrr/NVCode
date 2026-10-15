#!/usr/bin/env bash
# Local CI mirror — no network required for core checks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/src:${ROOT}:${PYTHONPATH:-}"

echo "== NVCode CI smoke =="
echo "ROOT=$ROOT"

python scripts/smoke_offline.py
python scripts/live_smoke.py --mock

echo "== pytest offline core (P0/P1 fast) =="
python -m pytest -q \
  tests/test_task_state_machine.py \
  tests/test_anti_false_done.py \
  tests/test_task_contract.py \
  tests/test_task_trace_terminal.py \
  tests/test_reclaim.py \
  tests/test_pipeline_e2e_matrix.py \
  tests/test_pipeline_e2e_mock.py \
  tests/test_context_planner_contract.py \
  tests/test_feature_flags.py \
  tests/test_quarantine_budget.py \
  tests/test_pipeline_events.py \
  tests/test_cost_tracker.py \
  tests/test_cost_board_ui.py \
  --tb=line

# Optional: subprocess timeout tests (slower)
if [[ "${AGENTBUS_CI_EXECUTOR:-0}" == "1" ]]; then
  echo "== pytest executor hardening =="
  python -m pytest -q tests/test_executor_hardening.py --tb=line
fi

# Protocol v0 SDK end-to-end smoke (no core mutation)
echo "== protocol SDK smoke =="
PYTHONPATH=.:src python scripts/smoke_protocol_sdk.py

echo "== OK (offline smoke checks passed) =="
