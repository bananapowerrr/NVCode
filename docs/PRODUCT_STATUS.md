# NVCode — product status

> This document is a status snapshot, not a substitute for running the current test suite, CI, or machine-level acceptance checks. Update evidence and date whenever status is re-audited.

## Product identity

- Product name: **NVCode**
- Repository: `bananapowerrr/NVCode`
- Primary development source: GitHub repository and its reviewed branches / pull requests.
- Legacy implementation names: `AgentBus`, `AGENTBUS_*`, and `.agentbus/` remain in parts of the codebase and require a planned compatibility migration.

## Product workflow

```
User request → task intake → runtime / worker → verification → result + diff → user review
```

The runtime and verification layers are responsible for execution state and evidence. Planning and language-model output are not, by themselves, proof that a change was applied correctly.

## Areas to validate

| Area | What to verify |
|---|---|
| Runtime and task lifecycle | State transitions, retries, error handling, recovery |
| Worker integration | Provider configuration, process lifetime, timeout behavior |
| Verification | Required checks, failure propagation, false-success prevention |
| Desktop UI | Task creation, status visibility, diff/review workflow |
| Configuration | Defaults, feature flags, localization parity |
| Packaging and updates | Build reproducibility, manifest/hash validation, rollback behavior |
| Documentation | Links, commands, current module paths, no stale source-of-truth instructions |

## Required evidence before a release

- Relevant unit and integration tests pass.
- CI for the candidate commit is green.
- Build and packaging checks pass where supported.
- Machine-level worker scenarios have been run and their results recorded.
- User data migration and legacy configuration compatibility are checked for any rename affecting paths or environment variables.
- Release notes and documentation match the actual candidate.

## Current status

No new full-suite or live-machine test run is claimed by this documentation update. Consult the current GitHub Actions results and record the latest local test evidence before assigning a release status.

## Related documents

- [Getting started](GETTING_STARTED.md)
- [Structure](STRUCTURE.md)
- [Contracts](CONTRACTS.md)
- [Live runbook](LIVE_RUNBOOK.md)
- [Technical debt registry](TECHNICAL_DEBT_REGISTRY.md)
- [Cleanup report](CLEANUP_REPORT.md)