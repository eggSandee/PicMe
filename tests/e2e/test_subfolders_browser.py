"""Browser end-to-end tests: the display shows photos from subfolders of a media folder, and
honours include_subfolders (global and per slot), live.

Reuses the server/browser plumbing from ``test_browser.py``.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import LANDSCAPE, _make_image

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

from test_browser import (  # noqa: E402,F401
    SLOT_COUNT, _write_config, browser, e2e_config_path, open_page, restore_config, server,
)

COLORS = [(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40), (120, 40, 160), (220, 220, 220)]

SHOWN_PATHS_JS = """() => [...document.querySelectorAll('.slot')].map(s => {
    const img = s.querySelector('img.visible');
    if (!img || !img.complete || !img.naturalWidth) return null;
    return new URL(img.src).searchParams.get('path');
})"""


def _photos(folder, prefix, n=SLOT_COUNT):
    folder.mkdir(parents=True, exist_ok=True)
    return {str(_make_image(folder / f"{prefix}_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=COLORS[i % len(COLORS)]))
            for i in range(n)}


def _wait_shown(page, ok, timeout=10.0) -> list:
    deadline = time.monotonic() + timeout
    shown = None
    while time.monotonic() < deadline:
        shown = page.evaluate(SHOWN_PATHS_JS)
        if ok(shown):
            return shown
        page.wait_for_timeout(100)
    raise AssertionError(f"slots never showed the expected photos: {shown}")


def test_display_shows_photos_from_nested_folders(server, open_page, restore_config, tmp_path):
    # The media folder itself holds no photos — only an album two levels down does
    nested = _photos(tmp_path / "root" / "2024" / "album", "nested")
    _write_config(restore_config, tmp_path / "root", cycle_seconds=300)
    w = open_page(f"{server}/display/")
    shown = _wait_shown(w.page, lambda s: None not in s)
    assert set(shown) <= nested and len(set(shown)) == SLOT_COUNT   # no duplicates across slots
    assert w.errors == []


def test_turning_subfolders_off_switches_slots_live(server, open_page, restore_config, tmp_path):
    root = tmp_path / "root"
    top = _photos(root, "top")
    nested = _photos(root / "album", "nested")
    _write_config(restore_config, root, cycle_seconds=300)
    w = open_page(f"{server}/display/")
    _wait_shown(w.page, lambda s: None not in s)
    seen = set(w.page.evaluate("() => [...folderQueues['photos:r:' + config.slots[0].source_path].images]"))
    assert seen == top | nested

    # The editor saves include_subfolders: false → within the config poll every slot draws from
    # the top level only (a queue keyed by folder alone would keep serving nested photos)
    _write_config(restore_config, root, cycle_seconds=300, include_subfolders=False)
    shown = _wait_shown(w.page, lambda s: None not in s and set(s) <= top)
    assert len(set(shown)) == SLOT_COUNT
    assert w.errors == []


def test_slot_with_own_folder_can_opt_out(server, open_page, restore_config, tmp_path):
    root = tmp_path / "root"
    top = _photos(root, "top", n=1)
    _photos(root / "album", "nested", n=SLOT_COUNT + 2)
    cfg = _write_config(restore_config, root, cycle_seconds=300)
    cfg["slots"][0] |= {"use_global": {"source": False}, "include_subfolders": False}
    restore_config.write_text(json.dumps(cfg))
    w = open_page(f"{server}/display/")
    shown = _wait_shown(w.page, lambda s: None not in s)
    assert shown[0] in top                       # slot 1: its folder's top level only
    assert any(p not in top for p in shown[1:])  # the others include the album
    assert w.errors == []
