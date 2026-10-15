# NVCode cleanup and documentation audit

> Audit basis: GitHub `main` tree inspected on 2026-10-15. This is a static repository inspection. No local test suite or live runtime was run as part of this snapshot.

## Confirmed repository facts

- Inspected tree: 786 entries, including directories.
- Python files under `src/`: 229.
- Python test files under `tests/`: 315.
- Baseline root-level Python entry files included `admin_ui.py`, `dispatcher.py`, `dispatcher_ui.py`, and `hello.py`.
- The branch removes the standalone root `hello.py`. Code-search checks found no `import hello` / `from hello` dependency; remaining filename mentions are runtime examples, regression notes, or temporary test targets.
- `docs/rp_llm_meta_decompose.diff.txt` is a patch artifact inside the documentation tree. Decide whether it is needed as a reproducible patch; if retained, move it under an explicitly named archive/patches area.
- No tracked `desktop.ini`, `__pycache__`, `.pyc`, `node_modules`, `test_cache_1.py`, or `Modelfile-t-lite` entries were found in the inspected tree. Older reports listing them are stale for this tree.
- Several Python basenames repeat in separate packages. Duplicate basenames are not proof of duplicate implementations; compare imports, call sites, and tests before consolidating.

## Documentation defects confirmed

1. `README.md` linked to missing `docs/LAUNCH.md`.
2. `AGENTS.md` referenced missing `docs/AUDIT_MAIN_0.10.md` and `docs/FINISH_5DAYS.md`.
3. `docs/INDEX.md` directed contributors to Drive synchronization and historical freeze documents as the primary workflow.
4. `docs/README.md` duplicates the root README and contains root-relative links that are incorrect from its location.
5. `docs/CLEANUP_REPORT.md` previously listed stale artifacts and an old file count.
6. `docs/SYNC_PUSH_LIST.md` described files as missing from `main`, although the 14 listed paths checked during this audit were present.
7. `docs/archive/README.md` points to architecture inventory documents that were not present in the inspected tree.
8. `docs/STRUCTURE.md` mixed repository paths with runtime-created directories and described old freeze assumptions.

## Current cleanup candidates

| Candidate | Evidence | Safe next action |
|---|---|---|
| `hello.py` | Standalone root hello-world script | Removed in this branch after checking for module-import references; full CI remains pending |
| `docs/rp_llm_meta_decompose.diff.txt` | Patch artifact in docs | Check whether patch is applied, useful, or duplicated in Git history |
| `docs/README.md` | Duplicates root README | Replace with a short redirect/index or remove after checking inbound links |
| Drive sync docs | No longer current workflow | Preserve as historical context in archive, remove from current index |
| Old day/audit reports | Snapshot documents with date-bound claims | Archive rather than delete until unique technical evidence is extracted |

## Current naming migration

The product name is **NVCode**. Legacy names still appear in UI titles, environment variables, updater/package filenames, and local runtime data paths. These must be migrated by subsystem with compatibility tests; blind global replacement could break existing installations or lose access to user data. Python console-script entry points are not declared until mixed source-layout packaging and runtime data inclusion can be validated.

## Recommended order

1. Finish a reference scan for each deletion candidate.
2. Remove stale source-of-truth and freeze instructions from the active documentation path.
3. Consolidate current documentation around `docs/INDEX.md`.
4. Inventory and implement the NVCode identity migration across package metadata, CLI entry points, UI strings, build outputs, updater, and environment variables.
5. Run link checks, unit/integration tests, CI, and packaging checks.
6. Re-run this audit and record exact test evidence before merging.

## Verification status

This report records static inspection only. It does not claim that tests, CI, packaging, or live workers passed.