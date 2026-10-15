# -*- coding: utf-8 -*-
"""UPDATE-001E: backup / replace / rollback helpers (pure filesystem).

Used by scripts/agentbus_updater.py. Never touches user_data_dir.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any


PROTECTED_NAMES = {
    "update_job.json",
    # Preserve legacy and current backup/staging directories during copy/replace.
    ".agentbus_backup",
    ".nvcode_backup",
    ".agentbus_staging",
    ".nvcode_staging",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def assert_not_user_data(path: Path, user_data: Path | None) -> None:
    if user_data is None:
        return
    if path.resolve() == user_data.resolve() or is_under(path, user_data):
        raise RuntimeError("refusing to mutate user_data_dir")


def backup_install_dir(
    install_dir: Path,
    backup_dir: Path,
    *,
    user_data_dir: Path | None = None,
) -> Path:
    """Copy install_dir → backup_dir (replace previous backup)."""
    install_dir = install_dir.resolve()
    backup_dir = backup_dir.resolve()
    if user_data_dir:
        assert_not_user_data(install_dir, user_data_dir.resolve())
        assert_not_user_data(backup_dir, user_data_dir.resolve())
    if not install_dir.is_dir():
        raise FileNotFoundError(f"install_dir_missing:{install_dir}")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.parent.mkdir(parents=True, exist_ok=True)

    def _ignore(dirpath: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for n in names:
            if n in PROTECTED_NAMES:
                ignored.add(n)
            # never copy user data if nested by mistake
            if user_data_dir and Path(dirpath, n).resolve() == user_data_dir.resolve():
                ignored.add(n)
        return ignored

    shutil.copytree(install_dir, backup_dir, ignore=_ignore)
    return backup_dir


def extract_package_to_staging(package_path: Path, staging: Path) -> Path:
    """Extract zip (or copy single file tree) into empty staging dir."""
    staging = staging.resolve()
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if zipfile.is_zipfile(package_path):
        with zipfile.ZipFile(package_path, "r") as zf:
            zf.extractall(staging)
        # if single top-level folder, use it as root content
        kids = [p for p in staging.iterdir()]
        if len(kids) == 1 and kids[0].is_dir():
            return kids[0]
        return staging
    # non-zip: place file as-is
    dest = staging / package_path.name
    shutil.copy2(package_path, dest)
    return staging


def replace_install_from_staging(
    staging_root: Path,
    install_dir: Path,
    *,
    user_data_dir: Path | None = None,
) -> None:
    """Replace contents of install_dir with staging_root (not the dir itself)."""
    install_dir = install_dir.resolve()
    staging_root = staging_root.resolve()
    if user_data_dir:
        ud = user_data_dir.resolve()
        assert_not_user_data(install_dir, ud)
        assert_not_user_data(staging_root, ud)
    if not staging_root.exists():
        raise FileNotFoundError("staging_missing")
    install_dir.mkdir(parents=True, exist_ok=True)
    # remove current children except protected
    for child in list(install_dir.iterdir()):
        if child.name in PROTECTED_NAMES:
            continue
        if user_data_dir and child.resolve() == user_data_dir.resolve():
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in staging_root.iterdir():
        dest = install_dir / child.name
        if child.is_dir():
            shutil.copytree(child, dest)
        else:
            shutil.copy2(child, dest)


def rollback_from_backup(
    backup_dir: Path,
    install_dir: Path,
    *,
    user_data_dir: Path | None = None,
) -> None:
    """Restore install_dir from backup_dir."""
    if not backup_dir.is_dir():
        raise FileNotFoundError("backup_missing")
    replace_install_from_staging(
        backup_dir, install_dir, user_data_dir=user_data_dir
    )


def run_restart_health(
    restart_cmd: list[str] | None,
    *,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """Optional post-replace probe. Empty cmd → skip (ok)."""
    import subprocess

    if not restart_cmd:
        return {"ok": True, "skipped": True}
    try:
        proc = subprocess.run(
            list(restart_cmd),
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-500:],
            "stderr": (proc.stderr or "")[-500:],
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def apply_update_package(
    package_path: Path,
    expected_sha256: str,
    install_dir: Path,
    backup_dir: Path,
    user_data_dir: Path | None = None,
    restart_cmd: list[str] | None = None,
    health_timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """Full 001E path: verify, backup, replace, health, rollback on fail."""
    out: dict[str, Any] = {"ok": False, "rolled_back": False}
    pkg = Path(package_path)
    digest = sha256_file(pkg)
    if digest != str(expected_sha256).lower():
        out["error"] = "sha256_mismatch"
        out["got"] = digest
        return out

    install_dir = Path(install_dir)
    backup_dir = Path(backup_dir)
    ud = Path(user_data_dir) if user_data_dir else None

    backup_install_dir(install_dir, backup_dir, user_data_dir=ud)
    out["backup"] = str(backup_dir)

    staging_base = backup_dir.parent / ".agentbus_staging"
    try:
        staging_root = extract_package_to_staging(pkg, staging_base)
        replace_install_from_staging(staging_root, install_dir, user_data_dir=ud)
        health = run_restart_health(restart_cmd, timeout_sec=health_timeout_sec)
        out["health"] = health
        if not health.get("ok"):
            rollback_from_backup(backup_dir, install_dir, user_data_dir=ud)
            out["rolled_back"] = True
            out["error"] = "health_failed_rolled_back"
            return out
        out["ok"] = True
        return out
    except Exception as exc:
        try:
            if backup_dir.is_dir():
                rollback_from_backup(backup_dir, install_dir, user_data_dir=ud)
                out["rolled_back"] = True
        except Exception as rb_exc:
            out["rollback_error"] = f"{type(rb_exc).__name__}: {rb_exc}"
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        if staging_base.exists():
            shutil.rmtree(staging_base, ignore_errors=True)
