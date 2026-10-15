# UPDATE-001 — Update System

## Why

Users need versioned desktop updates without Git/Python. Developers keep Git; end users get GitHub Releases + UpdateChecker.

## Architecture

```
GitHub Releases (release.json + package.zip)
        ↓
UpdateChecker (in NVCode) — compare only
        ↓
UI notification
        ↓
[Update] → nvcode-updater (external)
        ↓
download → sha256 → stop app → backup → replace → start
        ↓ fail → rollback
```

## Data vs install

| Keep (user) | Replace (app) |
|-------------|----------------|
| `existing %APPDATA%/AgentBus data (legacy path; migrate only with an explicit migration)` | binaries / exe |
| projects, memory, history | bundled runtime |
| credentials | UI, skills (shipped) |

## Phases

- **A** Version + manifest schema (this doc + `src/app/version.py`, `release_manifest.py`)
- **B** UpdateChecker network fetch (optional later; offline compare exists)
- **C** Chat/Settings “update available”
- **D** updater.exe protocol (JSON args: urls, hashes, paths)
- **E** signature/hash + rollback
- **F** CI: build → attach release.json + zip to GitHub Release

## Manifest keys

See `config/release.example.json` and `app/release_manifest.validate_manifest`.
