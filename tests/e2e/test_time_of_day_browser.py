"""Browser end-to-end tests for time-of-day colours (README → Time of day).

A period's template overrides only the colours it defines; 'Same as default' leaves a key out, so
the user's own wall, mat or frame colour shows. The editor notes under a control when the current
period replaces its value. Every page runs on a fake clock in UTC.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from conftest import LANDSCAPE, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401
from test_frames_browser import MEDIA_READY_JS, _until  # noqa: E402

NIGHT = dt.datetime(2026, 10, 20, 21, 23, tzinfo=dt.timezone.utc)  # the owner's report
DAY = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)
MY_MAT = "#80ffff"
MY_MAT_RGB = "rgb(128, 255, 255)"
NIGHT_MAT_RGB = "rgb(58, 58, 58)"
# The owner's live config: the editor's defaults before this fix, mat colours included
OLD_TOD = {
    "enabled": True, "day_start": "07:00", "night_start": "20:00",
    "day": {"wall_color": "#f5f0e8", "mat_color": "#a8c8e8", "mat_width": 20, "show_frame": True,
            "frame_color": "#6b4226", "frame_width": 8},
    "night": {"wall_color": "#000000", "mat_color": "#3a3a3a", "mat_width": 20, "show_frame": True,
              "frame_color": "#1c1c1c", "frame_width": 8},
}


def _same_as_default(*keys) -> dict:
    tod = json.loads(json.dumps(OLD_TOD))
    for period in ("day", "night"):
        for key in keys:
            del tod[period][key]
    return tod


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-time-of-day")
    (root / "photos").mkdir()
    for i in range(4):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=(40 * i, 90, 160))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(time_of_day=None, gallery_mode=False, **extra) -> dict:
        cfg = make_config(root / "photos", cycle_seconds=300)
        cfg["show_metadata"] = False
        cfg["wall_color"] = "#ff00ff"
        cfg["frame_defaults"] = {"mat_color": MY_MAT, "mat_width": 20, "show_frame": True,
                                 "frame_color": "#00ff00", "frame_width": 8, "frame_style": "solid"}
        cfg["gallery"] = {"mat_color": MY_MAT}
        if gallery_mode:
            cfg["layout_modes"] = {"classic-4": "gallery"}
        if time_of_day is not None:
            cfg["time_of_day"] = time_of_day
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_page(browser, server):
    contexts = []

    def _open(path="/display/", at=NIGHT, editor_mode="basic") -> PageWatcher:
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, timezone_id="UTC")
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.clock.install(time=at)
        page.goto(server + path, wait_until="networkidle")
        if path.startswith("/display"):
            _until(page, MEDIA_READY_JS)
        else:
            page.wait_for_selector("#layout-picker .layout-thumb")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def _saved(write_cfg, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cfg = json.loads(write_cfg.path.read_text())
        except ValueError:
            cfg = None  # read while the server was writing it
        if cfg and check(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never saved as expected: {write_cfg.path.read_text()}")


def gallery_mat(page) -> str:
    return page.evaluate("() => getComputedStyle(document.querySelector('.gallery-mat')).backgroundColor")


def slot_mat(page, slot_id=1) -> str:
    return page.evaluate("id => getComputedStyle(document.querySelector(`#slot-${id} .mat-frame`)).backgroundColor",
                         slot_id)


def wall(page) -> str:
    return page.evaluate("() => getComputedStyle(document.getElementById('collage')).backgroundColor")


def note(page, input_id):
    return page.locator(f".field:has(> #{input_id}) > .tod-note")


# --- The bug: the night mat replaces the user's, and the editor now says so ---

def test_night_mat_shows_with_a_note_and_use_my_colour_restores_it_live(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD, gallery_mode=True)
    display = open_page()
    assert gallery_mat(display.page) == NIGHT_MAT_RGB
    display.page.evaluate("window.__marker = 1")

    editor = open_page("/editor/", editor_mode="basic")
    page = editor.page
    gal = note(page, "g-gal-mat-color")
    assert gal.is_visible()  # Basic: the mat colour is a Basic setting
    assert gal.text_content() == "Night time-of-day is showing #3a3a3a until 07:00 · Edit · Use my colour"
    assert note(page, "g-mat-color").text_content().startswith("Night time-of-day is showing #3a3a3a until 07:00")

    gal.get_by_role("button", name="Use my colour").click()
    cfg = _saved(write_cfg, lambda c: "mat_color" not in c["time_of_day"]["night"])
    assert cfg["time_of_day"]["day"]["mat_color"] == "#a8c8e8"  # only this period's key
    assert cfg["gallery"]["mat_color"] == MY_MAT
    # Both mat notes go: the frame mat's came from the same key
    assert note(page, "g-gal-mat-color").count() == 0
    assert note(page, "g-mat-color").count() == 0
    _until(display.page, f"() => getComputedStyle(document.querySelector('.gallery-mat')).backgroundColor === '{MY_MAT_RGB}'", 8)
    assert display.page.evaluate("window.__marker") == 1  # applied live, no reload
    assert display.errors == [] and editor.errors == []


def test_edit_opens_the_time_of_day_modal(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD)
    page = open_page("/editor/").page
    note(page, "g-mat-color").get_by_role("button", name="Edit").click()
    assert page.locator(".tod-modal-overlay").is_visible()


def test_note_follows_the_period_while_the_editor_is_open(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD)
    page = open_page("/editor/", at=dt.datetime(2026, 10, 20, 6, 59, 30, tzinfo=dt.timezone.utc)).page
    assert note(page, "g-mat-color").text_content().startswith("Night time-of-day is showing #3a3a3a until 07:00")
    page.clock.run_for(90_000)  # past 07:00: the editor's 60 s tick
    assert note(page, "g-mat-color").text_content().startswith("Day time-of-day is showing #a8c8e8 until 20:00")


def test_no_notes_when_time_of_day_is_off_or_the_period_leaves_the_key_out(write_cfg, open_page):
    write_cfg(time_of_day={**OLD_TOD, "enabled": False})
    page = open_page("/editor/", editor_mode="advanced").page
    assert page.locator(".tod-note").count() == 0
    write_cfg(time_of_day=_same_as_default("mat_color", "wall_color", "frame_color"))
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#layout-picker .layout-thumb")
    assert page.locator(".tod-note").count() == 0


def test_wall_and_frame_notes_basic_and_advanced(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD)
    page = open_page("/editor/", editor_mode="basic").page
    assert note(page, "g-wall-color").text_content().startswith("Night time-of-day is showing #000000 until 07:00")
    # Basic hides the frame colour, so the style's note names it
    style = note(page, "g-frame-style")
    assert style.is_visible()
    assert style.text_content().startswith("Night time-of-day is showing #1c1c1c until 07:00")
    page.locator('#editor-mode button[data-mode="advanced"]').click()
    assert note(page, "g-frame-style").count() == 0  # the template sets no style
    assert note(page, "g-frame-color").text_content() == "Night time-of-day is showing #1c1c1c until 07:00 · Edit · Use my colour"
    note(page, "g-wall-color").get_by_role("button", name="Use my colour").click()
    cfg = _saved(write_cfg, lambda c: "wall_color" not in c["time_of_day"]["night"])
    assert cfg["time_of_day"]["night"]["frame_color"] == "#1c1c1c"


def test_frame_style_note_names_the_style(write_cfg, open_page):
    tod = _same_as_default("frame_color")
    tod["night"]["frame_style"] = "gold"
    write_cfg(time_of_day=tod)
    page = open_page("/editor/", editor_mode="advanced").page
    n = note(page, "g-frame-style")
    assert n.text_content().startswith("Night time-of-day is showing Gold")
    n.get_by_role("button", name="Use my style").click()
    _saved(write_cfg, lambda c: "frame_style" not in c["time_of_day"]["night"])


# --- 'Same as default': the user's colour shows day and night, in both modes ---

@pytest.mark.parametrize("at", [DAY, NIGHT], ids=["day", "night"])
@pytest.mark.parametrize("gallery_mode", [True, False], ids=["gallery", "separate"])
def test_same_as_default_mat_shows_my_colour(write_cfg, open_page, at, gallery_mode):
    write_cfg(time_of_day=_same_as_default("mat_color"), gallery_mode=gallery_mode)
    w = open_page(at=at)
    assert (gallery_mat(w.page) if gallery_mode else slot_mat(w.page)) == MY_MAT_RGB
    assert w.errors == []


def test_same_as_default_wall_shows_my_wall(write_cfg, open_page):
    write_cfg(time_of_day=_same_as_default("wall_color"))
    assert wall(open_page(at=NIGHT).page) == "rgb(255, 0, 255)"


def test_modal_same_as_default_for_each_colour(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD)
    page = open_page("/editor/", editor_mode="advanced").page
    page.locator("#tod-configure-btn").click()
    night = page.locator('.tod-modal-overlay [data-period="night"]')
    for key in ("wall_color", "mat_color", "frame_color"):
        box = night.locator(f'[data-tod-key="{key}"] .tod-same')
        assert not box.is_checked()  # the saved colour is kept
        box.check()
        assert night.locator(f'[data-tod-key="{key}"] input[type=color]').is_disabled()
    # The disabled picker previews what shows instead: the user's own colours
    assert night.locator('[data-tod-key="mat_color"] input[type=color]').input_value() == MY_MAT
    assert night.locator('[data-tod-key="wall_color"] input[type=color]').input_value() == "#ff00ff"
    assert night.locator('[data-tod-key="frame_color"] input[type=color]').input_value() == "#00ff00"
    cfg = _saved(write_cfg, lambda c: not {"wall_color", "mat_color", "frame_color"} & c["time_of_day"]["night"].keys())
    assert cfg["time_of_day"]["day"] == OLD_TOD["day"]
    # Unticking puts a colour back, the one in the picker
    night.locator('[data-tod-key="mat_color"] .tod-same').uncheck()
    cfg = _saved(write_cfg, lambda c: "mat_color" in c["time_of_day"]["night"])
    assert cfg["time_of_day"]["night"]["mat_color"] == "#3a3a3a"
    # A day template without a key opens ticked
    page.keyboard.press("Escape")
    write_cfg(time_of_day=_same_as_default("mat_color"))
    page.reload(wait_until="networkidle")
    page.locator("#tod-configure-btn").click()
    assert page.locator('.tod-modal-overlay [data-period="day"] [data-tod-key="mat_color"] .tod-same').is_checked()


# --- Defaults and existing configs ---

def test_new_install_and_reset_defaults_leave_the_mat_alone(write_cfg, open_page, server):
    write_cfg()  # no time_of_day, like the backend's first-run config
    page = open_page("/editor/", editor_mode="advanced").page
    page.locator(".toggle-row:has(#tod-enabled) .toggle-track").click()
    cfg = _saved(write_cfg, lambda c: c.get("time_of_day", {}).get("enabled") is True)
    for period in ("day", "night"):
        assert "mat_color" not in cfg["time_of_day"][period]
        assert {"wall_color", "frame_color"} <= cfg["time_of_day"][period].keys()
    assert cfg["time_of_day"]["night"]["wall_color"] == "#000000"

    # Reset everything drops time_of_day; the editor puts back the same defaults
    assert page.request.post(server + "/api/config/reset").ok
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#layout-picker .layout-thumb")
    tod = page.evaluate("() => config.time_of_day")
    assert tod["enabled"] is False
    assert "mat_color" not in tod["day"] and "mat_color" not in tod["night"]


def test_existing_config_keeps_its_templates(write_cfg, open_page):
    write_cfg(time_of_day=OLD_TOD)
    page = open_page("/editor/", editor_mode="basic").page
    page.locator(".toggle-row:has(#g-tap-details) .toggle-track").click()  # any other edit saves
    cfg = _saved(write_cfg, lambda c: c.get("tap_for_details") is False)
    assert cfg["time_of_day"] == OLD_TOD
