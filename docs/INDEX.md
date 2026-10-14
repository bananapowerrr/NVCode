# NVCode documentation

This index is the entry point for current documentation. Check source code and current CI configuration when a document's claim is uncertain. Documents under `docs/archive/` are historical evidence, not current instructions.

## Start here

1. [Getting started](GETTING_STARTED.md) — install, first run, user workflow.
2. [Structure](STRUCTURE.md) — repository layout and module responsibilities.
3. [Contracts](CONTRACTS.md) — task, runtime, and verification expectations.
4. [Live runbook](LIVE_RUNBOOK.md) — machine-level checks and troubleshooting workflow.
5. [Product status](PRODUCT_STATUS.md) — current status snapshot; validate it before release decisions.
6. [Cleanup report](CLEANUP_REPORT.md) — verified cleanup findings and follow-up actions.
7. [NVCode rebrand plan](NVCODE_REBRAND_PLAN.md) — migration status, compatibility policy, and remaining work.
8. [Technical debt registry](TECHNICAL_DEBT_REGISTRY.md) — candidates that still need confirmation or remediation.

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
| [Offline freeze history](OFFLINE_FREEZE.md) | Historical constraints; not a current blanket freeze |
| [Archive guide](archive/README.md) | How to interpret historical documents |

## Development loop

```
Issue / task → reproduce → focused implementation → tests → review diff → update docs
```

A task marked complete means its stated acceptance checks passed. It does not by itself certify the entire product or release.

## Naming and legacy compatibility

The current product name is **NVCode**. Existing `AgentBus` identifiers, `AGENTBUS_*` environment variables, and `.agentbus/` runtime data paths are legacy technical interfaces. Do not rename them by blind search-and-replace: inventory references, design a migration, preserve user data, and test old/new configuration behavior before changing them.