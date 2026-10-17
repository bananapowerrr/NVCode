# NVCode repository structure

This is a conceptual map of the current repository, not a generated file-by-file inventory. Confirm exact filenames in Git before relying on a path listed here.

```
NVCode/
├── dispatcher.py          # CLI / runtime entry point
├── dispatcher_ui.py       # desktop UI entry point
├── admin_ui.py            # administration UI entry point
├── pyproject.toml         # package metadata (pytest config lives in pytest.ini)
├── pytest.ini             # pytest configuration (canonical)
├── requirements*.txt      # dependency sets
├── config/                # policy, workers, providers, presets, UI strings
├── recipes/               # reusable task recipes
├── plugins/               # plugin integrations and examples
├── src/
│   ├── app/               # application services / UI-facing API
│   ├── cli/               # dashboard server, init wizard, recipes
│   ├── core/              # runtime, queue, routing, execution, verification
│   ├── intelligence/      # planning and advisory intelligence
│   ├── safety/            # safety checks and Git operations
│   ├── skills/            # skill registry and built-in skills
│   └── utils/             # shared utilities
├── ui/                    # desktop interface and panels
├── providers/             # provider adapters and registry
├── eventbus/              # event handling
├── protocol/              # SDK / REST protocol surface
├── nvcode_client/         # example client for the protocol
├── tests/                 # automated tests
├── scripts/               # bootstrap, audit, build, release, CI helpers
├── .github/               # GitHub workflows and repository automation
└── docs/                  # current documentation only (historical reports purged 2026-10-17)
```

## Entry points

- `python dispatcher.py --doctor` — inspect runtime prerequisites.
- `python dispatcher.py` — start the runtime/dispatcher.
- `python dispatcher_ui.py` — start the desktop UI.
- `python scripts/project_audit.py` — run the project audit helper.

## Main responsibilities

| Area | Responsibility |
|---|---|
| `src/core/` | Task lifecycle, queue operations, routing, execution, verification, recovery |
| `src/intelligence/` | Context, analysis, planning, advisory decisions |
| `src/app/` | Application-facing services and workflows |
| `src/safety/` | Policy enforcement and guarded operations |
| `src/skills/` | Deterministic and extensible coding skills |
| `ui/` | Desktop chat and supporting panels |
| `protocol/`, `nvcode_client/` | Protocol surface and example client integration |
| `config/` | Runtime policy, worker/provider settings, feature flags and localization |
| `tests/` | Regression and integration coverage |

## Runtime-created directories

Directories such as `.agentbus/`, `channels/`, `logs/`, and `events/` may be created at runtime or by local workflows. Their absence from a fresh Git checkout is not necessarily a defect. Their contents should normally remain local and must not be committed unless a specific fixture or example is intentionally versioned.

## Compatibility naming

The product is named **NVCode**. Internal identifiers such as `AGENTBUS_*`, `agentbus_root()`, and `.agentbus/` are legacy names still present in code. Treat their replacement as a migration task with tests, not as a cosmetic rename.
