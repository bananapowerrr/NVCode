# NVCode documentation

This index is the entry point for current documentation. Check source code and current CI configuration when a document's claim is uncertain.

> Исторические отчёты (DAY*, STATUS_*, HANDOFF, SYNC_*, аудиты и матрицы приёмки прошлых итераций) удалены 2026-10-17 при полной зачистке репозитория: они описывали завершённые этапы разработки и не соответствовали текущему коду. Актуальное состояние продукта отражают этот индекс, реестр технического долга и сами тесты.

## Start here

1. [Getting started](GETTING_STARTED.md) — install, first run, user workflow.
2. [Structure](STRUCTURE.md) — repository layout and module responsibilities.
3. [Contracts](CONTRACTS.md) — task, runtime, and verification expectations.
4. [Live runbook](LIVE_RUNBOOK.md) — machine-level checks and troubleshooting workflow.
5. [Technical debt registry](TECHNICAL_DEBT_REGISTRY.md) — open debt items with `file:line` anchors; fixed items are marked `FIXED` with a date.

## Reference

| Document | Purpose |
|---|---|
| [README](../README.md) | Project overview and quick start |
| [Windows installation](INSTALL_WINDOWS.md) | Windows-specific setup |
| [Troubleshooting](TROUBLESHOOTING.md) | Common failures and diagnostics |
| [Live acceptance](LIVE_ACCEPTANCE.md) | Machine-level acceptance scenarios |
| [Live acceptance suite](LIVE_ACCEPTANCE_SUITE.md) | Scenario definitions |
| [Roadmap](ROADMAP.md) | Product and engineering roadmap |
| [Runtime contracts](RUNTIME_CONTRACTS.md) | Runtime-specific contract reference |
| [Data flow diagram](DATA_FLOW_DIAGRAM.md) | High-level task flow |

## Development loop

```
Issue / task → reproduce → focused implementation → tests → review diff → update docs
```

A task marked complete means its stated acceptance checks passed. It
 does not by itself certify the entire product or release.

## Naming and legacy compatibility

The current product name is **NVCode**. Existing `AgentBus` identifiers, `AGENTBUS_*` environment variables, and `.agentbus/` runtime data paths are legacy technical interfaces. Do not rename them by blind search-and-replace: inventory references, design a migration, preserve user data, and test old/new configuration behavior before changing them.
