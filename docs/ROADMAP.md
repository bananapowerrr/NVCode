# NVCode roadmap

## Runtime engineering

| ID | Focus | Status |
|----|--------|--------|
| R1 | Terminal path (`finish_task`) | done / in repo |
| R2 | ExecutionResult → Evidence | done / in repo |
| R3 | Recovery Controller | done / in repo |
| R4 | Worker / fallback contract | done |
| R5 | Context budget | done |
| R6 | Night Mode v0 | done |

## Product distribution — UPDATE-001

| ID | Focus | Status |
|----|--------|--------|
| UPDATE-001A | Version + Release Manifest contract | done |
| UPDATE-001B | UpdateChecker (fetch + compare) | done |
| UPDATE-001C | UI / Chat notification | done |
| UPDATE-001D | External agentbus-updater process | done |
| UPDATE-001E | Hash verify + backup/replace/rollback | done |
| UPDATE-001F | GitHub Release + build pipeline | done |

### Update principles

1. **App does not replace itself while running** — external updater.
2. **User data never in the install package path** (`%APPDATA%/NVCode/` is the target for new installs; migration of existing user data must be explicit).
3. **Fail-closed manifest**: missing sha256 / invalid JSON → no update offer.
4. **Git is for developers**; Releases + UpdateChecker are for users.

### Target UX

```
UpdateChecker → "NVCode update available" → [Update] [Later]
  → download + verify → updater stops app → replace binaries → restart
  → failed start → rollback
```

## Live acceptance

LIVE-001 … acceptance on real PC (Ollama/Aider) — parallel, not a blocker for R4–R5 / UPDATE-001A–B.

## Explicitly not now

MCP, heavy RAG, multi-agent parallelism, cloud provider sprawl, Day-doc spam.


## Feature development (post R/UPDATE)

| ID | Focus | Status |
|----|--------|--------|
| DEV-001 | Execution Evidence → Runtime Decision | done |
| DEV-002 | Recovery Controller v1 (policies) | done |
| DEV-003 | Worker Failure Contract (WorkerResult) | done |
| DEV-004 | Worker Fallback v1 | done |
| DEV-005 | Context Budget (priority + audit) | done |
| DEV-006 | Task context / conversation continuity | done |
| DEV-007 | Night Mode autonomous loop | done |
| DEV-008 | Persistent Night Run / Crash Recovery | done |
| DEV-009 | Night Mode Policy/Scheduler | done |

LIVE-001 remains parallel acceptance on PC.


## Post-audit wiring (after Git HEAD 7cd1859)

| ID | Focus | Status |
|----|--------|--------|
| WIRE-001 | Recovery → Chat (notify_error + row) | done |
| WIRE-002 | rp_context + task_continuity | done |
| WIRE-003 | worker_result on all ERROR exits | next |
| WIRE-004 | context_audit in metadata (assemble returns it) | partial |
| SYNC-SCRIPTS | build_release + agentbus_updater + release.yml → Git | next (local ready) |
| LIVE-001 | Real PC Ollama/Aider | PC |
| NIGHT-UI-001 | Night status + Morning Report in Chat | done |

| NIGHT-RT-001 | execute_fn → Runtime bridge (mockable) | done |


## Pre-UI blockers (blocking for UI phase)

UI work does not start until B1..004 are green. Details and acceptance
criteria in [PRE_UI_BLOCKERS.md](PRE_UI_BLOCKERS.md).

| ID | Focus | Status |
|----|-------|--------|
| B1 | F1 atomic claim + F2 terminal contract | done (desktop caveat) |
| B2 | F3 cache/skill path through `finish_task` | done |
| B3 | F4 post-failure meta decomposition | done |
| B4 | E2E proof of B1..B3 | partial |

Warnings W1 (spill race), W2 (LoopGuard false positives) and W3 (metrics sink)
are deferred to phase 2 and are not blockers.
