"""Browser end-to-end tests for the editor's Basic / Advanced mode (README → Editor modes).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides ``e2e_config_path``.
Each test writes a config shaped like the backend's first-run defaults, so a fresh install shows
no "advanced settings active" hint, and opens the editor with no stored mode (Basic) unless it
asks for one.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401

FIELDS_OFF = {"show_date": False, "show_location": False, "show_camera": False, "show_megapixels": False,
              "show_fstop": False, "show_shutter_speed": False, "show_iso": False, "show_focal_length": False}
SHOW_KEYS = list(FIELDS_OFF)

# Controls each mode must show or hide in the sidebar
ADVANCED_SIDEBAR = ["#g-frame-width", "#g-mat-width", ".toggle-row:has(#g-show-frame)", "#g-frame-color", "#tod-configure-btn",
                    "#g-video-max-edge", ".toggle-row:has(#g-include-live-photos)", ".toggle-row:has(#g-include-subfolders)", "#g-date-format", "#g-max-pills",
                    "#g-rotation-change-at"]
BASIC_SIDEBAR = ["#g-wall-color", ".toggle-row:has(#g-fill-mode)", "#g-frame-style", "#g-mat-color", "#g-text-size", "#g-pill-size", "#g-label-position",
                 "#g-source-path", "#layout-picker", "#g-meta-toggles"]


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-editor-mode")
    (root / "photos").mkdir()
    (root / "other").mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40)]):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a first-run-shaped config (plus `extra`) and restores the original afterwards."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(meta=None, frame=None, slot_overrides=None, **extra) -> dict:
        folder = str(root / "photos")
        cfg = make_config(folder, cycle_seconds=300)
        cfg["source_path"] = folder
        cfg["max_metadata_pills"] = 3
        cfg["metadata_defaults"] = {**FIELDS_OFF, "show_date": True, "text_size": "medium",
                                    "pill_size": "medium", **(meta or {})}
        cfg["frame_defaults"] = {"mat_color": "#000000", "mat_width": 16, "show_frame": True,
                                 "frame_color": "#2c2318", "frame_width": 8, "frame_style": "solid",
                                 **(frame or {})}
        cfg["layout_rotation"] = {"enabled": False, "layouts": [], "change_at": "04:00"}
        for slot in cfg["slots"]:
            slot["use_global"] = {"source": True, "metadata": True, "frame": True}
        for slot_id, override in (slot_overrides or {}).items():
            cfg["slots"][slot_id - 1].update(override)
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    _write.root = root
    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_editor(browser, server):
    contexts = []

    def _open(path="/editor/", editor_mode=None, reduced_motion="no-preference", init_scripts=()) -> PageWatcher:
        ctx = browser.new_context(viewport=VIEWPORT, reduced_motion=reduced_motion)
        contexts.append(ctx)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        for script in init_scripts:
            ctx.add_init_script(script)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.goto(server + path, wait_until="networkidle")
        if path.startswith("/editor"):
            page.wait_for_selector("#layout-picker .layout-thumb")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def _saved(write_cfg, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cfg = json.loads(write_cfg.path.read_text())
        if check(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never saved as expected: {write_cfg.path.read_text()}")


def _visible(page, selector) -> bool:
    return page.locator(selector).first.is_visible()


def _slot_section(page, title):
    return page.locator("#slot-panel-body .panel-section").filter(
        has=page.get_by_role("heading", name=title, exact=True))


def _slot_show_metadata(page):
    return _slot_section(page, "Metadata Overlays").locator(".toggle-row").filter(has_text="Show metadata")


def _preview_pill_counts(page) -> list[int]:
    return page.evaluate("""() => [...document.querySelectorAll('.preview-slot')]
        .map(el => el.querySelectorAll('.preview-pills > div').length)""")


def test_fresh_browser_starts_in_basic(write_cfg, open_editor):
    write_cfg()
    w = open_editor()
    page = w.page
    assert page.locator('#editor-mode button[data-mode="basic"]').get_attribute("aria-pressed") == "true"
    for sel in ADVANCED_SIDEBAR:
        assert not _visible(page, sel), sel
    for sel in BASIC_SIDEBAR:
        assert _visible(page, sel), sel
    assert not _visible(page, "#advanced-hint")  # first-run config: nothing advanced is set
    assert not page.get_by_role("button", name="Reset slots to global settings").is_visible()

    page.locator(".preview-slot").nth(0).click()
    for title in ("Media Source", "Mode", "Schedule", "Metadata Overlays"):
        assert _slot_section(page, title).is_visible(), title
    for title in ("Frame", "Fit"):  # Basic has the global Fill frames instead of a slot's own Fit
        assert not _slot_section(page, title).is_visible(), title
    assert page.locator("#slot-panel-body .adopt-row:visible").count() == 0
    schedule = _slot_section(page, "Schedule")
    assert schedule.locator(".field").filter(has_text="Change every (seconds)").is_visible()
    assert not schedule.locator(".field").filter(has_text="Group").is_visible()
    # The folder is editable without the "Use global" toggle
    folder = _slot_section(page, "Media Source").locator("input[type=text]")
    assert folder.is_editable()
    assert page.evaluate("el => !el.closest('.field-inactive')", folder.element_handle())
    assert w.errors == []


def test_switching_mode_shows_everything_persists_and_never_writes_config(write_cfg, open_editor):
    write_cfg()
    before = (write_cfg.path.read_bytes(), write_cfg.path.stat().st_mtime_ns)
    w = open_editor()
    page = w.page
    page.locator('#editor-mode button[data-mode="advanced"]').click()
    for sel in ADVANCED_SIDEBAR + BASIC_SIDEBAR:
        if sel == "#g-rotation-change-at":
            continue  # only while rotation is on
        assert _visible(page, sel), sel
    page.locator(".preview-slot").nth(0).click()
    assert _slot_section(page, "Frame").is_visible()
    assert page.locator("#slot-panel-body .adopt-row:visible").count() == 3  # source, frame, metadata

    page.reload(wait_until="networkidle")
    assert page.locator('#editor-mode button[data-mode="advanced"]').get_attribute("aria-pressed") == "true"
    assert _visible(page, "#g-frame-width")

    page.locator('#editor-mode button[data-mode="basic"]').click()
    page.reload(wait_until="networkidle")
    assert not _visible(page, "#g-frame-width")
    assert page.evaluate("localStorage.getItem('picme.editorMode')") == "basic"

    time.sleep(1.0)  # past the autosave debounce
    assert (write_cfg.path.read_bytes(), write_cfg.path.stat().st_mtime_ns) == before
    assert w.errors == []


def test_hint_counts_active_advanced_settings(write_cfg, open_editor):
    write_cfg(frame={"frame_width": 20}, time_of_day={"enabled": True}, slot_overrides={2: {"group": "A"}})
    w = open_editor()
    page = w.page
    hint = page.locator("#advanced-hint")
    assert hint.is_visible()
    assert page.locator("#advanced-hint-text").text_content() == "3 advanced settings active"
    assert hint.get_attribute("title").split("\n") == ["Frame width", "Time-of-day colors", "Slot 2: Group"]

    hint.get_by_role("button", name="Show").click()
    assert page.locator('#editor-mode button[data-mode="advanced"]').get_attribute("aria-pressed") == "true"
    assert not hint.is_visible()
    assert _visible(page, "#g-frame-width")
    assert w.errors == []


def test_basic_frame_style_width_bump_is_not_an_advanced_setting(write_cfg, open_editor):
    write_cfg()
    w = open_editor()
    page = w.page
    page.select_option("#g-frame-style", "wood")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"]["frame_style"] == "wood")
    assert cfg["frame_defaults"]["frame_width"] > 8  # wood's own width
    assert not page.locator("#advanced-hint").is_visible()
    # A width that isn't wood's own is counted
    page.locator('#editor-mode button[data-mode="advanced"]').click()
    page.fill("#g-frame-width", "40")
    page.locator('#editor-mode button[data-mode="basic"]').click()
    assert page.locator("#advanced-hint-text").text_content() == "1 advanced setting active"
    assert page.locator("#advanced-hint").get_attribute("title") == "Frame width"
    assert w.errors == []


@pytest.mark.parametrize("style", ["wood", "gold"])
def test_basic_back_to_solid_undoes_the_frame_width_bump(write_cfg, open_editor, style):
    write_cfg()
    w = open_editor()
    page = w.page
    page.select_option("#g-frame-style", style)
    _saved(write_cfg, lambda c: c["frame_defaults"]["frame_style"] == style and c["frame_defaults"]["frame_width"] > 8)
    page.select_option("#g-frame-style", "solid")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"]["frame_style"] == "solid")
    assert cfg["frame_defaults"]["frame_width"] == 8
    assert not page.locator("#advanced-hint").is_visible()
    assert w.errors == []


def test_back_to_solid_keeps_a_frame_width_changed_after_the_bump(write_cfg, open_editor):
    write_cfg()
    w = open_editor(editor_mode="advanced")
    page = w.page
    page.select_option("#g-frame-style", "wood")
    page.fill("#g-frame-width", "40")
    _saved(write_cfg, lambda c: c["frame_defaults"]["frame_style"] == "wood" and c["frame_defaults"]["frame_width"] == 40)
    page.select_option("#g-frame-style", "solid")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"]["frame_style"] == "solid")
    assert cfg["frame_defaults"]["frame_width"] == 40
    assert w.errors == []


def _slot_subfolders(page):
    return _slot_section(page, "Media Source").locator(".toggle-row").filter(has_text="Include subfolders")


def test_include_subfolders_is_advanced_and_counted_when_off(write_cfg, open_editor):
    other = str(write_cfg.root / "other")
    write_cfg(include_subfolders=False,
              slot_overrides={3: {"use_global": {"source": False}, "source_path": other, "include_subfolders": False}})
    w = open_editor()
    page = w.page
    assert page.locator("#advanced-hint").get_attribute("title").split("\n") == [
        "Include subfolders", "Slot 3: Include subfolders"]
    assert not _visible(page, ".toggle-row:has(#g-include-subfolders)")
    page.locator(".preview-slot").nth(2).click()
    assert not _slot_subfolders(page).is_visible()

    page.locator('#editor-mode button[data-mode="advanced"]').click()
    assert not page.locator("#g-include-subfolders").is_checked()
    page.locator(".preview-slot").nth(2).click()
    toggle = _slot_subfolders(page)
    assert not toggle.locator("input").is_checked()
    toggle.locator(".toggle").click()
    cfg = _saved(write_cfg, lambda c: c["slots"][2].get("include_subfolders") is True)
    assert cfg["include_subfolders"] is False   # only this slot changed

    # A slot on the default folder shows the global value, disabled; the global toggle updates it
    page.locator(".preview-slot").nth(0).click()
    assert _slot_section(page, "Media Source").locator(".field-inactive").count() == 1
    assert not _slot_subfolders(page).locator("input").is_checked()
    page.locator(".toggle-row:has(#g-include-subfolders) .toggle").click()
    _saved(write_cfg, lambda c: c["include_subfolders"] is True)
    assert _slot_subfolders(page).locator("input").is_checked()
    assert w.errors == []


def test_live_photo_clips_are_on_by_default_and_counted_when_off(write_cfg, open_editor):
    cfg = write_cfg()
    assert "include_live_photos" not in cfg  # a config without the key: on
    w = open_editor(editor_mode="advanced")
    page = w.page
    assert page.locator("#g-include-live-photos").is_checked()
    page.locator(".toggle-row:has(#g-include-live-photos) .toggle").click()
    _saved(write_cfg, lambda c: c.get("include_live_photos") is False)
    page.locator('#editor-mode button[data-mode="basic"]').click()
    assert page.locator("#advanced-hint").get_attribute("title").split("\n") == ["Video settings"]
    assert w.errors == []

    write_cfg(include_live_photos=True)
    page = open_editor(editor_mode="basic").page
    assert page.locator("#advanced-hint").is_hidden()  # on is the default, so not counted


def test_reset_all_clears_per_slot_subfolders(write_cfg, open_editor):
    write_cfg(slot_overrides={2: {"use_global": {"source": False}, "source_path": str(write_cfg.root / "other"),
                                  "include_subfolders": False}})
    w = open_editor(editor_mode="advanced")
    page = w.page
    page.locator('[data-setting="slots.reset_all"]').click()
    page.get_by_role("button", name="Reset all slots", exact=True).click()
    # Reset all edits the editor's config (saved with the next change)
    assert page.evaluate("() => [config.slots[1].use_global.source, 'include_subfolders' in config.slots[1]]") == [True, False]
    assert w.errors == []


def test_hint_hidden_in_advanced_and_counts_only_values_that_differ(write_cfg, open_editor):
    write_cfg(date_format="iso")
    w = open_editor(editor_mode="advanced")
    page = w.page
    assert not page.locator("#advanced-hint").is_visible()
    page.locator('#editor-mode button[data-mode="basic"]').click()
    assert page.locator("#advanced-hint-text").text_content() == "1 advanced setting active"
    assert w.errors == []


GLOBAL_COLLAPSE_JS = """async () => {
  const body = document.getElementById('g-meta-body');
  const height = () => body.getBoundingClientRect().height;
  const full = height();
  document.getElementById('g-show-metadata').click();
  const now = height();
  await new Promise(r => setTimeout(r, 90));
  const mid = height();
  await new Promise(r => setTimeout(r, 400));
  return { full, now, mid, end: height() };
}"""


def test_global_show_metadata_collapses_smoothly_and_keeps_values(write_cfg, open_editor):
    write_cfg(meta={"show_camera": True, "text_size": "large"})
    w = open_editor()
    page = w.page
    assert page.locator("#g-show-metadata").is_checked()
    assert page.locator("#g-show-metadata").get_attribute("aria-expanded") == "true"

    h = page.evaluate(GLOBAL_COLLAPSE_JS)
    assert h["full"] > 100
    assert 0 < h["mid"] < h["full"]  # animating, not jumping
    assert h["end"] == 0
    assert not _visible(page, "#g-text-size")
    assert page.locator("#g-show-metadata").get_attribute("aria-expanded") == "false"
    cfg = _saved(write_cfg, lambda c: c["show_metadata"] is False)
    assert cfg["metadata_defaults"]["show_camera"] is True  # everything else is kept
    assert cfg["metadata_defaults"]["text_size"] == "large"
    assert _preview_pill_counts(page) == [0, 0, 0, 0]

    page.locator(".toggle:has(#g-show-metadata)").click()
    page.wait_for_function("document.getElementById('g-meta-body').getBoundingClientRect().height > 100")
    assert page.locator("#g-text-size").input_value() == "large"
    _saved(write_cfg, lambda c: c["show_metadata"] is True)
    assert w.errors == []


def test_reduced_motion_toggles_instantly(write_cfg, open_editor):
    write_cfg()
    w = open_editor(reduced_motion="reduce")
    h = w.page.evaluate(GLOBAL_COLLAPSE_JS)
    assert h["full"] > 100
    assert h["now"] == 0
    assert w.errors == []


def test_global_show_metadata_off_disables_each_slot_box(write_cfg, open_editor):
    write_cfg(show_metadata=False)
    w = open_editor()
    page = w.page
    assert not page.locator("#g-show-metadata").is_checked()
    assert page.evaluate("document.getElementById('g-meta-body').getBoundingClientRect().height") == 0
    page.locator(".preview-slot").nth(0).click()
    box = _slot_show_metadata(page).locator("input")
    assert box.is_disabled()
    assert not box.is_checked()
    assert page.get_by_text("Metadata is off for every slot").is_visible()
    assert w.errors == []


def test_slot_show_metadata_maps_to_fields(write_cfg, open_editor):
    write_cfg(meta={"show_camera": True})
    w = open_editor()
    page = w.page
    page.locator(".preview-slot").nth(1).click()
    row = _slot_show_metadata(page)
    assert row.locator("input").is_checked()
    assert page.get_by_text("Shows Date, Camera — set in Default Metadata.").is_visible()

    row.locator(".toggle").click()
    cfg = _saved(write_cfg, lambda c: c["slots"][1]["use_global"]["metadata"] is False)
    assert all(cfg["slots"][1]["metadata"][k] is False for k in SHOW_KEYS)
    assert cfg["metadata_defaults"]["show_camera"] is True  # the defaults are untouched
    page.wait_for_function("document.getElementById('slot-meta-body').getBoundingClientRect().height === 0")
    assert _preview_pill_counts(page)[1] == 0
    assert _preview_pill_counts(page)[0] > 0
    assert not page.locator("#advanced-hint").is_visible()  # Basic's own "off" isn't an advanced setting

    # The display agrees: no pills on slot 2, pills on slot 1
    display = open_editor("/display/")
    display.page.wait_for_function("document.querySelector('.slot .overlay-pill.visible')")
    pills = display.page.evaluate("""() => [...document.querySelectorAll('.slot')]
        .map(s => s.querySelectorAll('.overlay-pill').length)""")
    assert pills[0] > 0 and pills[1] == 0

    row.locator(".toggle").click()
    cfg = _saved(write_cfg, lambda c: c["slots"][1]["use_global"]["metadata"] is True)
    assert not any(k in (cfg["slots"][1].get("metadata") or {}) for k in SHOW_KEYS)
    page.wait_for_function("document.getElementById('slot-meta-body').getBoundingClientRect().height > 0")
    assert _preview_pill_counts(page)[1] > 0
    assert w.errors == [] and display.errors == []


def test_slot_show_metadata_on_with_no_default_fields_shows_the_date(write_cfg, open_editor):
    write_cfg(meta={"show_date": False})
    w = open_editor()
    page = w.page
    page.locator(".preview-slot").nth(2).click()
    row = _slot_show_metadata(page)
    assert not row.locator("input").is_checked()
    row.locator(".toggle").click()
    cfg = _saved(write_cfg, lambda c: (c["slots"][2].get("metadata") or {}).get("show_date") is True)
    assert cfg["slots"][2]["use_global"]["metadata"] is False
    assert _preview_pill_counts(page)[2] > 0
    assert w.errors == []


def test_advanced_field_toggles_close_the_section_with_the_last_pill(write_cfg, open_editor):
    write_cfg()
    w = open_editor(editor_mode="advanced")
    page = w.page
    page.locator(".preview-slot").nth(0).click()
    section = _slot_section(page, "Metadata Overlays")
    section.locator(".adopt-row .toggle").click()
    section.locator(".meta-grid .toggle-row").filter(has_text="Date").locator(".toggle").click()
    page.wait_for_function("!document.querySelector('#slot-meta-body').classList.contains('open')")
    assert not _slot_show_metadata(page).locator("input").is_checked()
    assert w.errors == []


def test_basic_slot_folder_sets_and_clears_the_override(write_cfg, open_editor):
    write_cfg()
    w = open_editor()
    page = w.page
    page.locator(".preview-slot").nth(0).click()
    folder = _slot_section(page, "Media Source").locator("input[type=text]")
    assert page.get_by_text("Same as the default folder.").is_visible()

    other = str(write_cfg.root / "other")
    folder.fill(other)
    folder.press("Enter")  # folders are saved when committed, not per keystroke
    cfg = _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["source"] is False)
    assert cfg["slots"][0]["source_path"] == other
    assert not page.get_by_text("Same as the default folder.").is_visible()

    folder.fill(str(write_cfg.root / "photos"))
    folder.press("Enter")
    cfg = _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["source"] is True)
    assert cfg["slots"][0]["source_path"] is None
    assert page.get_by_text("Same as the default folder.").is_visible()
    assert w.errors == []


def test_basic_cleared_slot_folder_shows_the_default_again(write_cfg, open_editor):
    other = str(write_cfg.root / "other")
    write_cfg(slot_overrides={1: {"use_global": {"source": False, "metadata": True, "frame": True},
                                  "source_path": other}})
    w = open_editor()
    page = w.page
    page.locator(".preview-slot").nth(0).click()
    folder = _slot_section(page, "Media Source").locator("input[type=text]")
    assert folder.input_value() == other
    folder.fill("")
    folder.press("Enter")
    cfg = _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["source"] is True)
    assert cfg["slots"][0]["source_path"] is None
    assert folder.input_value() == str(write_cfg.root / "photos")  # as when first opened
    assert page.get_by_text("Same as the default folder.").is_visible()
    assert w.errors == []


@pytest.mark.parametrize("suffix", ["/", "//", "/./"])
def test_basic_slot_folder_with_a_trailing_slash_is_the_default(write_cfg, open_editor, suffix):
    other = str(write_cfg.root / "other")
    write_cfg(slot_overrides={1: {"use_global": {"source": False, "metadata": True, "frame": True},
                                  "source_path": other}})
    w = open_editor()
    page = w.page
    page.locator(".preview-slot").nth(0).click()
    folder = _slot_section(page, "Media Source").locator("input[type=text]")
    folder.fill(str(write_cfg.root / "photos") + suffix)
    folder.press("Enter")
    cfg = _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["source"] is True)
    assert cfg["slots"][0]["source_path"] is None
    assert page.get_by_text("Same as the default folder.").is_visible()
    assert w.errors == []


@pytest.mark.parametrize("editor_mode", ["basic", "advanced"])
def test_adopted_slot_folder_follows_a_new_default_folder(write_cfg, open_editor, editor_mode):
    write_cfg()
    w = open_editor(editor_mode=editor_mode)
    page = w.page
    page.locator(".preview-slot").nth(0).click()
    folder = _slot_section(page, "Media Source").locator("input[type=text]")
    assert folder.input_value() == str(write_cfg.root / "photos")

    other = str(write_cfg.root / "other")
    page.fill("#g-source-path", other)
    page.press("#g-source-path", "Enter")
    _saved(write_cfg, lambda c: c["source_path"] == other)
    assert folder.input_value() == other  # not the old default

    if editor_mode == "basic":
        # Committing the box as shown keeps following the default (no pinned old path)
        folder.fill(other + "/")
        folder.press("Enter")
        page.locator("#g-wall-color").evaluate("el => { el.value = '#123456'; el.dispatchEvent(new Event('input')); }")
        cfg = _saved(write_cfg, lambda c: c["wall_color"] == "#123456")
        assert cfg["slots"][0]["use_global"]["source"] is True and cfg["slots"][0]["source_path"] is None
        assert folder.input_value() == other
    assert w.errors == []


def test_basic_edits_keep_advanced_values(write_cfg, open_editor):
    slot_frame = {"use_global": {"source": True, "metadata": True, "frame": False},
                  "frame": {"mat_color": "#ff0000"}, "group": "A", "video_loops": 5}
    write_cfg(frame={"frame_width": 20}, date_format="iso", slot_overrides={2: slot_frame})
    w = open_editor()
    page = w.page
    page.locator("#g-wall-color").evaluate("el => { el.value = '#123456'; el.dispatchEvent(new Event('input')); }")
    page.locator(".preview-slot").nth(1).click()
    assert not _slot_section(page, "Frame").is_visible()  # Basic hides them, and must keep them
    assert not _slot_section(page, "Fit").is_visible()  # a slot's own Fit / Fill is Advanced
    page.locator(".toggle-row:has(#g-fill-mode) .toggle-track").click()  # Basic's global Fill frames
    cfg = _saved(write_cfg, lambda c: c["wall_color"] == "#123456" and c.get("fill_mode") == "cover")
    assert "fill_mode" not in cfg["slots"][1]
    assert cfg["frame_defaults"]["frame_width"] == 20
    assert cfg["date_format"] == "iso"
    slot = cfg["slots"][1]
    assert slot["group"] == "A" and slot["video_loops"] == 5
    assert slot["use_global"]["frame"] is False and slot["frame"] == {"mat_color": "#ff0000"}
    assert w.errors == []


THROWING_STORAGE = """
Storage.prototype.getItem = () => { throw new Error('storage disabled'); };
Storage.prototype.setItem = () => { throw new Error('storage disabled'); };
"""


def test_editor_works_without_local_storage(write_cfg, open_editor):
    write_cfg()
    w = open_editor(init_scripts=[THROWING_STORAGE])
    page = w.page
    assert not _visible(page, "#g-frame-width")
    page.locator('#editor-mode button[data-mode="advanced"]').click()
    assert _visible(page, "#g-frame-width")
    page.locator(".preview-slot").nth(0).click()
    assert _slot_section(page, "Frame").is_visible()  # re-renders keep the in-page choice
    assert w.errors == []


# --- Fill frames: a Basic global toggle; a slot's own Fit / Fill is an Advanced override ---

def test_fill_frames_is_basic_and_a_slot_override_is_advanced(write_cfg, open_editor):
    write_cfg(fill_mode="cover", slot_overrides={2: {"fill_mode": "contain"}, 3: {"fill_mode": "cover"}})
    w = open_editor()
    page = w.page
    assert page.locator("#g-fill-mode").is_checked()
    assert _visible(page, ".toggle-row:has(#g-fill-mode)")
    # Slot 2's Fit differs from the global Fill: counted; slot 3's matches it: not counted
    assert page.locator("#advanced-hint-text").text_content() == "1 advanced setting active"
    assert page.locator("#advanced-hint").get_attribute("title") == "Slot 2: Fit / fill"
    page.locator(".preview-slot").nth(1).click()
    assert not _slot_section(page, "Fit").is_visible()

    page.locator('#editor-mode button[data-mode="advanced"]').click()
    sel = page.locator("#slot-fill-mode")
    assert sel.input_value() == "contain"
    assert sel.locator("option").first.text_content() == "Default (global: Fill)"
    sel.select_option("")  # back to the global setting: the key goes, so it follows Fill frames
    cfg = _saved(write_cfg, lambda c: "fill_mode" not in c["slots"][1])
    assert cfg["fill_mode"] == "cover" and cfg["slots"][2]["fill_mode"] == "cover"

    page.locator(".toggle-row:has(#g-fill-mode) .toggle-track").click()
    _saved(write_cfg, lambda c: c["fill_mode"] == "contain")
    assert page.locator("#slot-fill-mode option").first.text_content() == "Default (global: Fit)"
    assert w.errors == []


def test_a_config_without_fill_mode_shows_fit(write_cfg, open_editor):
    write_cfg()  # from before the global setting: no fill_mode anywhere
    page = open_editor().page
    assert not page.locator("#g-fill-mode").is_checked()
    assert not page.locator("#advanced-hint").is_visible()


def test_reset_slots_to_global_clears_slot_fill(write_cfg, open_editor):
    write_cfg(fill_mode="cover", slot_overrides={2: {"fill_mode": "contain"}})
    page = open_editor(editor_mode="advanced").page
    page.get_by_role("button", name="Reset slots to global settings").click()
    page.locator(".modal").get_by_role("button", name="Reset all slots").click()
    cfg = _saved(write_cfg, lambda c: "fill_mode" not in c["slots"][1])
    assert cfg["fill_mode"] == "cover"


def test_tap_for_details_is_basic_and_saves(write_cfg, open_editor):
    write_cfg()  # from before the setting: no tap_for_details, which means on
    w = open_editor(editor_mode="basic")
    page = w.page
    assert _visible(page, ".toggle-row:has(#g-tap-details)")
    assert page.locator("#g-tap-details").is_checked()
    assert not page.locator("#advanced-hint").is_visible()
    page.locator(".toggle-row:has(#g-tap-details) .toggle-track").click()
    _saved(write_cfg, lambda c: c.get("tap_for_details") is False)
    page.locator(".toggle-row:has(#g-tap-details) .toggle-track").click()
    _saved(write_cfg, lambda c: c.get("tap_for_details") is True)
    assert w.errors == []
