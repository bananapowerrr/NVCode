# NVCode rebrand and repository cleanup plan

Updated: 2026-10-15

## Current decision

- Product name: **NVCode**.
- Canonical source: GitHub repository `bananapowerrr/NVCode`.
- Google Drive synchronization is retired.
- No blanket freeze applies to runtime, architecture, UI, verification, or new features.
- Changes should be made in focused branches and reviewed through diffs and tests.

## Completed in this branch

- Replaced the root README with an NVCode-first quick start and documentation links.
- Rewrote `AGENTS.md` to remove obsolete freeze rules and Drive source-of-truth instructions.
- Rebuilt the docs index and updated the structure, getting-started, Windows installation, product status, and cleanup report.
- Replaced obsolete Drive synchronization instructions with explicit retired-workflow notices so historical links remain readable without instructing contributors to use Drive.
- Marked the old offline freeze as superseded.
- Removed the root-level hello-world file after searching references.
- Removed the manual meta-decomposition patch artifact because the integration is already present in source code.
- Updated package metadata to `nvcode`. Kept the supported source-tree launch commands (`python dispatcher.py` and `python dispatcher_ui.py`); deferred pip-installed console scripts because mixed source-layout packaging and runtime data inclusion have not been validated.
- Updated visible application identity, Windows executable names, updater labels, release manifest identity, archive naming, and release repository defaults.

## Legacy identifiers that remain intentionally

The codebase still contains `AGENTBUS_*` environment variables, `agentbus_root()`, `.agentbus/` local state paths, and legacy script filenames. These are compatibility interfaces, not current product branding.

Do not replace these globally without a migration plan. A complete migration should:
1. Add `NVCODE_*` aliases with documented precedence over legacy variables.
2. Add a one-time, idempotent migration for local user-data paths with backup and rollback.
3. Keep old paths readable during the compatibility window.
4. Rename internal Python helpers only when imports and third-party callers are covered by tests.
5. Retire compatibility aliases only after release notes and a deprecation period.

## Remaining work

### Identity and packaging
- Search every user-visible UI string, notification, tray label, dashboard title, log prefix, and CLI output.
- Verify update-checker URL, release asset naming, manifest validation, updater path, and release workflow agree on `NVCode`.
- Inspect install directories and migration logic before changing user-data paths.
- Decide when to remove old package command aliases and legacy updater filename.

### Documentation
- Re-audit dated day reports and old audits; archive or remove documents that have no unique current value.
- Refresh the technical-debt registry against current source and tests; do not carry forward old line numbers as current facts.
- Check every Markdown link and shell/PowerShell command.
- Verify documentation paths against the current tree.
- Add an automated link/reference check to CI if practical.

### Code quality and tests
- Inspect repeated Python basenames and likely duplicate implementations using imports, runtime MRO, call sites, and tests.
- Remove dead code only with reference and coverage evidence.
- Run focused tests for each behavior change, the full pytest suite, CI smoke, release packaging, and updater tests.
- Record actual results; never report checks as passing if they were not run.

## Verification note

This branch has been modified through the GitHub API. A local test suite has not yet been run in this environment. CI and the final diff must be reviewed before merging.
