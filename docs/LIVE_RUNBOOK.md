# NVCode live runbook

Updated: 2026-10-15. This guide describes a repeatable validation process; it does not claim that any scenario has passed unless a result is recorded for the candidate commit.

## 1. Environment preflight

From the repository root:

```bash
python --version
git status --short
python dispatcher.py --doctor
```

Install project and UI dependencies according to [Getting Started](GETTING_STARTED.md). If a local worker is part of the scenario, verify that the provider process and requested model are available before testing NVCode.

## 2. Static and automated checks

Run the checks relevant to the change:

```bash
python scripts/project_audit.py
python -m pytest -q
bash scripts/ci_smoke.sh
```

If the repository's CI workflow uses a different command, follow the checked-in workflow rather than this generic list. Record the exact command, commit SHA, exit code, and relevant output. A test collection count is not a pass by itself.

## 3. Desktop end-to-end check

Use a disposable test project, not a production working tree with unrelated edits.

1. Start the runtime: `python dispatcher.py`.
2. Start the UI in a second terminal: `python dispatcher_ui.py`.
3. Submit one small task that creates or modifies a test file.
4. Confirm the worker actually wrote the change.
5. Inspect the diff and verification evidence.
6. Submit a task with an intentionally failing check and confirm it is not reported as successful.
7. Inspect logs and task history for accurate terminal state and error details.

Do not infer success from a chat response alone. Verify the filesystem and the checks relevant to the task.

## 4. Failure triage

For each failure, record:
- candidate commit SHA and operating system / Python version;
- exact command or task prompt;
- expected and actual behavior;
- relevant task/run identifier and logs;
- smallest reproducible case;
- regression test added or reason a test is not practical.

Use [the live bug template](templates/LIVE_BUG.md) where appropriate.

## 5. Release validation

Before publishing a release candidate:

- verify the current Git diff and branch ancestry;
- run focused regression tests and the full test suite where feasible;
- inspect GitHub Actions for the exact candidate commit;
- run packaging and updater checks;
- verify manifest app name, repository URL, artifact filename, and SHA-256;
- confirm that legacy user data is preserved if a migration touches paths or environment variables;
- update [Product Status](PRODUCT_STATUS.md) with actual evidence and the tested commit.

## 6. Evidence record

Use this format in the relevant issue or pull request:

```text
Commit: <sha>
Environment: <OS / Python / provider>
Command/scenario: <exact command or task>
Result: PASS | FAIL | NOT RUN
Evidence: <log, CI run, diff, or artifact>
```

A `NOT RUN` result must remain explicit. Do not convert missing evidence into a green status.
