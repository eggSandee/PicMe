"""Browser end-to-end tests for editing media folders while a display is open (README → Editor).

Folder paths are saved when the field is committed (blur, Enter or Browse), not per keystroke,
and an open display only switches a slot to a new folder once that folder has something to show.
Reuses the server/browser plumbing from ``test_browser.py`` and the editor helpers from
``test_slot_drag_browser.py``.
"""
from __future__ import annotations

import json
import time
from urllib.parse import unquote

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

from test_browser import browser, server  # noqa: E402,F401
from test_slot_drag_browser import _saved, _until, _wait_saved, open_at, write_config  # noqa: E402,F401

TYPED = "blue-photos-for-typing"  # 22 characters, typed one at a time
COUNT_LOADS = "sessionStorage.loads = String(+(sessionStorage.loads || 0) + 1)"


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-path-fields")
    for name, color in (("red", (220, 30, 30)), ("blue", (30, 30, 220)), ("green", (30, 200, 30)),
                        (TYPED, (30, 30, 220))):
        folder = root / name
        folder.mkdir()
        for i in range(4):
            _make_image(folder / f"{name}_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                        exif=_exif_bytes(date=True))
    path = root / "config.json"
    path.write_text("{}")
    return path


@pytest.fixture
def folders(e2e_config_path):
    root = e2e_config_path.parent
    return {name: str(root / name) for name in ("red", "blue", "green", TYPED)} | {"root": str(root)}


def _open_display(open_at, server):
    """Opens the display, counting page loads in sessionStorage and `src` changes on slot 1."""
    display = open_at("about:blank")
    page = display.page
    page.add_init_script(COUNT_LOADS)
    page.goto(f"{server}/display/", wait_until="networkidle")
    _until(lambda: page.locator("#slot-1 img.visible").get_attribute("src"), what="the first photo")
    page.evaluate("""() => {
        window.srcChanges = 0;
        new MutationObserver(m => { window.srcChanges += m.length; })
          .observe(document.getElementById('slot-1'), { subtree: true, attributes: true, attributeFilter: ['src'] });
    }""")
    return display


def _loads(page) -> int:
    return int(page.evaluate("sessionStorage.loads"))


def _src(page, n=1) -> str:
    return unquote(page.locator(f"#slot-{n} img.visible").get_attribute("src") or "")


def _count_saves(page) -> list:
    saves = []
    page.on("request", lambda r: r.method == "POST" and r.url.endswith("/api/config") and saves.append(r))
    return saves


def test_typing_the_global_folder_saves_once_on_enter(server, open_at, write_config, e2e_config_path, folders):
    write_config()
    display = _open_display(open_at, server)
    dpage = display.page
    assert folders["red"] in _src(dpage)

    w = open_at(f"{server}/editor/")
    saves = _count_saves(w.page)
    field = w.page.locator("#g-source-path")
    # As if the user deleted "red" off the end and typed a new folder name — slowly, so the
    # 500 ms autosave would fire between keystrokes (and the display polls several times)
    field.evaluate("(el, v) => { el.value = v; }", folders["root"] + "/")
    field.focus()
    w.page.keyboard.press("End")
    w.page.keyboard.type(TYPED, delay=600)
    w.page.wait_for_timeout(3500)  # one more display poll

    assert saves == []
    assert _saved(e2e_config_path)["source_path"] == folders["red"]
    assert _loads(dpage) == 1 and dpage.evaluate("window.srcChanges") == 0
    assert folders["red"] in _src(dpage)

    field.press("Enter")
    _wait_saved(e2e_config_path, lambda c: c.get("source_path") == folders[TYPED])
    _until(lambda: folders[TYPED] in _src(dpage), timeout=8, what="the display to show the new folder")
    w.page.wait_for_timeout(3500)
    assert len(saves) == 1
    # Exactly one refresh: a photo from the new folder, without reloading the page
    assert _loads(dpage) == 1 and dpage.evaluate("window.srcChanges") == 1
    assert w.errors == [] and display.errors == []


def test_typing_a_slot_folder_saves_on_blur(server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config()
    cfg["slots"][0] |= {"use_global": {"source": False, "frame": True, "metadata": True}}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    display = _open_display(open_at, server)
    dpage = display.page

    w = open_at(f"{server}/editor/")
    saves = _count_saves(w.page)
    w.page.locator("#collage-preview .preview-slot").first.click()
    field = w.page.locator("#slot-panel-body .path-row input")
    field.evaluate("(el, v) => { el.value = v; }", folders["root"] + "/")
    field.focus()
    w.page.keyboard.press("End")
    w.page.keyboard.type("green", delay=600)
    w.page.wait_for_timeout(800)
    assert saves == []
    assert _saved(e2e_config_path)["slots"][0]["source_path"] == folders["red"]

    field.press("Tab")
    _wait_saved(e2e_config_path, lambda c: c["slots"][0]["source_path"] == folders["green"])
    _until(lambda: folders["green"] in _src(dpage), timeout=8, what="slot 1 to show the green folder")
    assert len(saves) == 1
    assert _loads(dpage) == 1 and dpage.evaluate("window.srcChanges") == 1
    assert w.errors == [] and display.errors == []


def test_a_missing_video_folder_saved_with_enter_does_not_reload_the_display(
        server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config()
    cfg["slots"][1] |= {"mode": "video", "use_global": {"source": False, "frame": True, "metadata": True}}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    display = _open_display(open_at, server)
    dpage = display.page
    missing = folders["root"] + "/no-such-folder"

    w = open_at(f"{server}/editor/")
    w.page.locator("#collage-preview .preview-slot").nth(1).click()
    field = w.page.locator("#slot-panel-body .path-row input")
    field.fill(missing)
    field.press("Enter")
    _wait_saved(e2e_config_path, lambda c: c["slots"][1]["source_path"] == missing)

    # The display takes the new config without reloading for a folder with no videos
    _until(lambda: dpage.evaluate("config.slots[1].source_path") == missing, timeout=8,
           what="the display to pick up the config")
    dpage.wait_for_timeout(3500)
    assert _loads(dpage) == 1
    assert w.errors == [] and display.errors == []


def test_a_missing_photo_folder_keeps_the_photo_and_leaves_no_retry_behind(
        server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config()
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    display = _open_display(open_at, server)
    dpage = display.page
    shown = _src(dpage)
    missing = folders["root"] + "/no-such-folder"

    w = open_at(f"{server}/editor/")
    field = w.page.locator("#g-source-path")
    field.fill(missing)
    field.press("Enter")
    _until(lambda: dpage.evaluate("config.source_path") == missing, timeout=8,
           what="the display to pick up the missing folder")
    # Nothing to show there: the slot keeps its photo
    assert _src(dpage) == shown and dpage.evaluate("window.srcChanges") == 0

    field.fill(folders["red"])
    field.press("Enter")
    _until(lambda: dpage.evaluate("config.source_path") == folders["red"], timeout=8,
           what="the display to pick up the red folder again")
    assert dpage.evaluate("Object.keys(photoRetryTimers)") == []
    assert _loads(dpage) == 1
    assert w.errors == [] and display.errors == []
