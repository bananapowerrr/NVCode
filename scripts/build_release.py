#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UPDATE-001F: build NVCode release package + release.json manifest.

Usage:
  python scripts/build_release.py --version 0.10.1 --out dist/

Creates:
  dist/NVCode-<version>-src.zip   (source/app tree for now)
  dist/release.json                 (manifest for UpdateChecker)

Does not publish to GitHub — CI workflow does that on tag push.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# Paths included in the package (relative to repo root)
INCLUDE_GLOBS = [
    "src/**/*",
    "ui/**/*",
    "app/**/*",
    "scripts/agentbus_updater.py",
    "config/**/*",
    "requirements*.txt",
    "README.md",
    "pyproject.toml",
    "setup.py",
]

EXCLUDE_PARTS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    ".agentbus_backup",
    ".agentbus_staging",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def should_include(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if path.is_dir():
        return False
    # match coarse include: under src/ui/app/scripts/config or root meta files
    s = str(rel).replace("\\", "/")
    if s.startswith(("src/", "ui/", "app/", "config/")):
        return True
    if s in ("scripts/agentbus_updater.py", "README.md", "pyproject.toml", "setup.py"):
        return True
    if s.startswith("requirements") and s.endswith(".txt"):
        return True
    return False


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    roots = [root / "src", root / "ui", root / "app", root / "config", root / "scripts"]
    for base in roots:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and should_include(p, root):
                files.append(p)
    for name in ("README.md", "pyproject.toml", "setup.py"):
        f = root / name
        if f.is_file():
            files.append(f)
    for f in root.glob("requirements*.txt"):
        if f.is_file():
            files.append(f)
    return sorted(set(files))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_zip(files: list[Path], root: Path, zip_path: Path, prefix: str) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arc = f"{prefix}/{f.relative_to(root).as_posix()}"
            zf.write(f, arcname=arc)


def write_manifest(
    path: Path,
    *,
    version: str,
    channel: str,
    package_url: str,
    sha256: str,
    size_bytes: int,
    notes: str,
) -> dict:
    manifest = {
        "app": "NVCode",
        "version": version,
        "channel": channel,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes or f"NVCode {version}",
        "min_updater": "1.0.0",
        "package": {
            "url": package_url,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "format": "zip",
        },
        "user_data_paths": ["%APPDATA%/AgentBus", "%APPDATA%/NVCode"],
        "install_paths_hint": ["binaries", "runtime", "ui", "skills", "src"],
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="Semver, e.g. 0.10.1")
    ap.add_argument("--channel", default="stable")
    ap.add_argument("--out", default="dist", help="Output directory")
    ap.add_argument("--notes", default="")
    ap.add_argument(
        "--package-url",
        default="",
        help="Public URL of the zip (defaults to GitHub Releases path template)",
    )
    ap.add_argument("--repo", default="bananapowerrr/NVCode")
    args = ap.parse_args(argv)

    root = repo_root()
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)

    version = args.version.lstrip("v")
    asset_name = f"NVCode-{version}-src.zip"
    zip_path = out / asset_name
    files = collect_files(root)
    if not files:
        # minimal fallback for sandbox without full tree
        marker = out / "_build_marker.txt"
        marker.write_text(f"NVCode {version}\n", encoding="utf-8")
        files = [marker]
        build_zip(files, out, zip_path, prefix=f"NVCode-{version}")
    else:
        build_zip(files, root, zip_path, prefix=f"NVCode-{version}")

    digest = sha256_file(zip_path)
    size = zip_path.stat().st_size
    package_url = args.package_url or (
        f"https://github.com/{args.repo}/releases/download/v{version}/{asset_name}"
    )
    manifest_path = out / "release.json"
    manifest = write_manifest(
        manifest_path,
        version=version,
        channel=args.channel,
        package_url=package_url,
        sha256=digest,
        size_bytes=size,
        notes=args.notes,
    )
    # also write sha256sums
    (out / f"{asset_name}.sha256").write_text(f"{digest}  {asset_name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(zip_path), "manifest": str(manifest_path), **manifest["package"], "version": version}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
