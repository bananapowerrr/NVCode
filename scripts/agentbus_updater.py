#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NVCode external updater (UPDATE-001D/E).

Separate process. Stages:
  dry-run        → validate job
  download-only  → download + sha256
  apply          → backup + replace + health + rollback on failure

Never mutates user_data_dir.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path


def _log(msg: str) -> None:
    print(f"[nvcode-updater] {msg}", flush=True)


def load_job(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("job_not_object")
    return data


def download(url: str, dest: Path, timeout: float = 120.0) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NVCode-Updater/0.10"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NVCode external updater")
    ap.add_argument("--job", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    job_path = Path(args.job)
    if not job_path.is_file():
        _log(f"job missing: {job_path}")
        return 2

    job = load_job(job_path)
    for key in ("package_url", "sha256", "target_version", "install_dir"):
        if not job.get(key):
            _log(f"missing job field: {key}")
            return 2

    sha = str(job["sha256"]).lower().strip()
    if len(sha) != 64:
        _log("invalid sha256")
        return 2

    install = Path(str(job["install_dir"]))
    backup = Path(str(job.get("backup_dir") or (install / ".nvcode_backup")))
    user_data = Path(str(job["user_data_dir"])) if job.get("user_data_dir") else None
    restart_cmd = job.get("restart_cmd") if isinstance(job.get("restart_cmd"), list) else None

    _log(f"target={job.get('target_version')} install={install}")
    if user_data:
        _log(f"user_data(protected)={user_data}")

    if args.dry_run:
        _log("dry-run OK")
        return 0

    if not (args.apply or args.download_only):
        _log("need --dry-run | --download-only | --apply")
        return 3

    tmp = Path(tempfile.mkdtemp(prefix="nvcode_upd_"))
    pkg = tmp / "package.bin"
    try:
        _log(f"download {job['package_url']}")
        download(str(job["package_url"]), pkg)

        # import apply helpers (same tree or installed)
        try:
            from app.updater_apply import apply_update_package, sha256_file
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from app.updater_apply import apply_update_package, sha256_file

        digest = sha256_file(pkg)
        if digest != sha:
            _log(f"sha256 mismatch: got {digest}")
            return 4
        _log("sha256 OK")

        if args.download_only and not args.apply:
            _log("download+verify done")
            return 0

        result = apply_update_package(
            pkg,
            expected_sha256=sha,
            install_dir=install,
            backup_dir=backup,
            user_data_dir=user_data,
            restart_cmd=restart_cmd,
        )
        _log(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            return 5 if result.get("rolled_back") else 1
        _log("apply OK")
        return 0
    except Exception as exc:
        _log(f"failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
