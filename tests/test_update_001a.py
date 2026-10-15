# -*- coding: utf-8 -*-

def test_version_present():
    from app.version import get_version, get_version_info, is_newer

    assert get_version()
    info = get_version_info()
    assert info["app"] == "NVCode"
    assert is_newer("0.10.1", "0.10.0")
    assert not is_newer("0.10.0", "0.10.1")
    assert is_newer("1.0.0", "0.99.99")
    assert is_newer("0.10.0", "0.10.0-dev")  # release > dev pre


def test_manifest_validate():
    from app.release_manifest import example_manifest, parse_manifest, validate_manifest

    m = example_manifest()
    ok, err = validate_manifest(m)
    assert ok, err
    bad = dict(m)
    bad["package"] = {"url": "x"}  # no sha
    ok2, err2 = validate_manifest(bad)
    assert not ok2


def test_checker_compare():
    from app.release_manifest import example_manifest
    from app.update_checker import compare_to_manifest
    from app.version import get_version

    m = example_manifest(version="9.9.9")
    r = compare_to_manifest(m)
    assert r["manifest_ok"] is True
    assert r["update_available"] is True
    assert r["current"] == get_version()

    m2 = example_manifest(version="0.0.1")
    r2 = compare_to_manifest(m2)
    assert r2["update_available"] is False
