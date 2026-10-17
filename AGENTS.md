# NVCode — instructions for coding agents

You are working in the **NVCode** repository. GitHub is the canonical source for code, documentation, issues, and review.

## Working principles

1. Inspect the current implementation before trusting old reports, handoffs, or audit snapshots.
2. Make the smallest coherent change that solves the verified problem; larger refactors are allowed when the task calls for them and tests cover the behavior.
3. Never claim tests, runtime scenarios, or release checks passed unless they were actually run and their results inspected.
4. For behavior changes, add or update regression tests. For documentation changes, verify internal links and commands against the current repository.
5. Keep user data, credentials, local runtime state, generated caches, and secrets out of Git.
6. Historical iteration reports (DAY*/STATUS*/HANDOFF/SYNC snapshots) are not kept in the repo; keep the docs set current and verified instead.
7. Use the product name **NVCode** in current user-facing copy, documentation, package metadata, and new artifacts. Keep legacy identifiers only where needed for compatibility, migration, or existing data paths.
8. Review the final diff for accidental deletions, generated files, unrelated changes, and misleading claims.

## Verification

Start with the checks relevant to the change. Common repository checks include:

```bash
python -m pytest -q
python scripts/project_audit.py
bash scripts/ci_smoke.sh
```

Inspect the repository's actual CI workflows before assuming a command is required or available. If a command cannot run in the current environment, report that explicitly.

## Architecture map

- Runtime and task execution: `src/core/`
- Intelligence and planning: `src/intelligence/`
- Application services: `src/app/`
- Command-line interfaces: `src/cli/`, `dispatcher.py`
- Desktop UI: `ui/`, `dispatcher_ui.py`
- Safety and policy: `src/safety/`, `config/`
- Tests: `tests/`
- Developer tools: `scripts/`
- Current documentation index: `docs/INDEX.md`

## Change w
orkflow

1. Read `docs/INDEX.md` and the specific source files involved.
2. Reproduce or test the reported behavior where possible.
3. Implement the fix or cleanup in a focused branch.
4. Run targeted tests, then broader checks when practical.
5. Update documentation and tests affected by the change.
6. Report changed paths, verification performed, known risks, and remaining work.

There is no blanket freeze on runtime, architecture, UI, parallel execution, or feature work. Make such changes when they are in scope, with appropriate analysis and validation.