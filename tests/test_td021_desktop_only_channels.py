# -*- coding: utf-8 -*
"""TD-021: phone file-bus channels are not polled."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from core.config import CHANNELS, DEFAULT_CHANNEL


def test_default_channels_empty_desktop_default():
    assert tuple(CHANNELS) == ()
    assert DEFAULT_CHANNEL == "desktop"


def test_active_channels_desktop_only(tmp_path: Path):
    from core.runtime_ops import RuntimeOps

    class R(RuntimeOps):
        pass

    r = object.__new__(R)
    channels_root = tmp_path / "channels"
    (channels_root / "gpt" / "incoming").mkdir(parents=True)
    (channels_root / "desktop" / "incoming").mkdir(parents=True)
    (channels_root / "desktop__sub_x" / "incoming").mkdir(parents=True)
    r.bus = SimpleNamespace(root=tmp_path)
    active = R._active_channels(r)
    assert "desktop" in active
    assert "gpt" not in active
    assert "desktop__sub_x" in active
