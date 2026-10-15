# NVCode — Getting Started

## Requirements

- Python 3.11 or newer.
- Git.
- Optional local inference: Ollama or LM Studio.
- Desktop UI dependencies from `requirements-ui.txt`.

## Install

Run from the repository root (the checkout directory is typically `NVCode`):

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ui.txt
```

If the project bootstrap script is appropriate for your platform, you can instead inspect and run `python scripts/bootstrap.py`.

## First run

```bash
python dispatcher.py --init
python dispatcher.py --doctor
```

Start the runtime and UI in separate terminals:

```bash
python dispatcher.py
python dispatcher_ui.py
```

On Windows, review `scripts/start_all.bat` and `docs/INSTALL_WINDOWS.md` for supported launch options.

## Optional local model

For Ollama, one possible starting point is:

```bash
ollama pull qwen2.5-coder:7b
```

Choose the model and provider based on available hardware. A model download does not by itself prove that a worker is correctly configured.

## Workflow

```
Request → task queue → worker / runtime → checks → result and diff → review
```

Use the UI's task and change-review actions to inspect generated modifications before relying on them. A successful individual task is not the same as a release-readiness result for the entire project.

## Verify the checkout

```bash
python scripts/project_audit.py
python -m pytest -q
bash scripts/ci_smoke.sh
```

Run the checks supported by your environment and report failures honestly. Do not treat an unrun command as a pass.

## Documentation

See [docs/INDEX.md](INDEX.md) for architecture, contracts, live checks, troubleshooting, product status, and technical debt.

## Legacy names

NVCode is the product name. Existing configuration keys and local data paths may still use `AGENTBUS_*` and `.agentbus/`; do not rename these manually without a tested migration.