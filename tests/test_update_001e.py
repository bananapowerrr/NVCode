# -*- coding: utf-8 -*-
import hashlib
import zipfile
from pathlib import Path


def _zip_with_file(zip_path: Path, name: str, content: bytes) -> str:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(name, content)
    h = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return h


def test_backup_replace_ok(tmp_path: Path):
    from app.updater_apply import apply_update_package

    install = tmp_path / "install"
    install.mkdir()
    (install / "app.txt").write_text("old", encoding="utf-8")
    user = tmp_path / "userdata"
    user.mkdir()
    (user / "keep.txt").write_text("secret", encoding="utf-8")

    pkg = tmp_path / "pkg.zip"
    sha = _zip_with_file(pkg, "app.txt", b"new-version")

    res = apply_update_package(
        pkg,
        expected_sha256=sha,
        install_dir=install,
        backup_dir=tmp_path / "backup",
        user_data_dir=user,
        restart_cmd=None,
    )
    assert res["ok"] is True
    assert (install / "app.txt").read_text(encoding="utf-8") == "new-version"
    assert (user / "keep.txt").read_text(encoding="utf-8") == "secret"
    assert (tmp_path / "backup" / "app.txt").read_text(encoding="utf-8") == "old"


def test_health_fail_rolls_back(tmp_path: Path):
    from app.updater_apply import apply_update_package

    install = tmp_path / "install"
    install.mkdir()
    (install / "app.txt").write_text("old", encoding="utf-8")
    pkg = tmp_path / "pkg.zip"
    sha = _zip_with_file(pkg, "app.txt", b"bad")

    res = apply_update_package(
        pkg,
        expected_sha256=sha,
        install_dir=install,
        backup_dir=tmp_path / "backup",
        restart_cmd=["python", "-c", "raise SystemExit(1)"],
        health_timeout_sec=5,
    )
    assert res["ok"] is False
    assert res.get("rolled_back") is True
    assert (install / "app.txt").read_text(encoding="utf-8") == "old"


def test_refuse_user_data_as_install(tmp_path: Path):
    from app.updater_apply import backup_install_dir
    import pytest

    ud = tmp_path / "ud"
    ud.mkdir()
    with pytest.raises(RuntimeError):
        backup_install_dir(ud, tmp_path / "b", user_data_dir=ud)



def test_nvcode_nested_backup_and_staging_are_preserved(tmp_path: Path):
    from app.updater_apply import apply_update_package

    install = tmp_path / "install"
    install.mkdir()
    (install / "app.txt").write_text("old", encoding="utf-8")

    pkg = tmp_path / "pkg.zip"
    sha = _zip_with_file(pkg, "app.txt", b"new-version")
    backup = install / ".nvcode_backup"

    result = apply_update_package(
        pkg,
        expected_sha256=sha,
        install_dir=install,
        backup_dir=backup,
    )

    assert result["ok"] is True
    assert (install / "app.txt").read_text(encoding="utf-8") == "new-version"
    assert (backup / "app.txt").read_text(encoding="utf-8") == "old"
    assert not (backup / ".nvcode_backup").exists()
    assert not (install / ".agentbus_staging").exists()
    assert not (install / ".nvcode_staging").exists()


def test_sha_mismatch(tmp_path: Path):
    from app.updater_apply import apply_update_package

    install = tmp_path / "install"
    install.mkdir()
    (install / "x").write_text("1", encoding="utf-8")
    pkg = tmp_path / "pkg.zip"
    _zip_with_file(pkg, "x", b"2")
    res = apply_update_package(
        pkg,
        expected_sha256="0" * 64,
        install_dir=install,
        backup_dir=tmp_path / "b",
    )
    assert res["ok"] is False
    assert res.get("error") == "sha256_mismatch"
