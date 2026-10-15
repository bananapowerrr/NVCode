# -*- coding: utf-8 -*-
import json
import subprocess
import zipfile
import sys
from pathlib import Path


def test_build_release_script(tmp_path: Path):
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_release.py"
    assert script.is_file()
    out = tmp_path / "dist"
    # run from artifacts as pseudo-repo root by patching — script uses parents[1]
    # so copy script tree expectation: run with cwd artifacts and allow empty src
    proc = subprocess.run(
        [sys.executable, str(script), "--version", "0.10.99", "--out", str(out), "--notes", "test"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    # The updater replaces the install tree; ensure launchers and recipes ship.
    zip_path = out / "NVCode-0.10.99-src.zip"
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    prefix = "NVCode-0.10.99/"
    assert prefix + "dispatcher.py" in names
    assert prefix + "dispatcher_ui.py" in names
    assert prefix + "admin_ui.py" in names
    assert any(name.startswith(prefix + "recipes/") for name in names)

    # manifest
    manifest_path = out / "release.json"
    # script may write relative to repo_root/dist
    candidates = list(Path(__file__).resolve().parent.parent.rglob("release.json"))
    candidates = [p for p in candidates if "0.10.99" in p.read_text(encoding="utf-8") or p.parent.name == "dist"]
    assert manifest_path.is_file() or any(c.is_file() for c in Path(__file__).resolve().parent.parent.glob("dist/release.json"))
    mp = manifest_path if manifest_path.is_file() else Path(__file__).resolve().parent.parent / "dist" / "release.json"
    data = json.loads(mp.read_text(encoding="utf-8"))
    from app.release_manifest import validate_manifest
    ok, err = validate_manifest(data)
    assert ok, err
    assert data["version"] == "0.10.99"
    assert len(data["package"]["sha256"]) == 64


def test_workflow_file_exists():
    wf = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
    assert wf.is_file()
    text = wf.read_text(encoding="utf-8")
    assert "build_release.py" in text
    assert "release.json" in text
    assert "softprops/action-gh-release" in text
