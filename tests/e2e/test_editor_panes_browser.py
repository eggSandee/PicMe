"""Browser end-to-end tests for the editor's panes, the preset and the resets (README → Editor).

- the divider between the preview and the slot settings (drag, touch, keys, persisted, reset);
- folding sidebar sections (persisted; the Layout header's links still work);
- each preview cell's aspect ratio and Fit / Fill tag;
- the sidebar's order (Time-of-Day inside Global Settings, Videos and the Reset section last);
- The preset at the top of Global Settings: reset to defaults, the saved preset and Undo, against the live server.

Reuses the editor-mode module's fixtures (``write_cfg`` writes a first-run-shaped config).
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
from test_editor_mode_browser import _saved, _slot_section, open_editor, write_cfg  # noqa: E402,F401
from test_frames_browser import MEDIA_READY_JS, _until  # noqa: E402

LAPTOP = {"width": 1440, "height": 900}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-editor-panes")
    (root / "photos").mkdir()
    (root / "other").mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40)]):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True))
    _make_image(root / "other" / "photo.jpg", size=LANDSCAPE, fmt="JPEG", color=(90, 90, 90))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture(autouse=True)
def no_profile_files(e2e_config_path):
    """Each test starts without a backup or preset next to the config."""
    for name in ("config.backup.json", "presets.json"):
        (e2e_config_path.parent / name).unlink(missing_ok=True)
    yield
    for name in ("config.backup.json", "presets.json"):
        (e2e_config_path.parent / name).unlink(missing_ok=True)


@pytest.fixture
def laptop(browser, server):
    """An editor page at 1440x900 (where the full-width preview pushed the slot settings away)."""
    contexts = []

    def _open(editor_mode="advanced") -> PageWatcher:
        ctx = browser.new_context(viewport=LAPTOP)
        contexts.append(ctx)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        page = ctx.new_page()
        w = PageWatcher(page)
        page.goto(server + "/editor/", wait_until="networkidle")
        page.wait_for_selector("#layout-picker .layout-thumb")
        return w

    yield _open
    for ctx in contexts:
        ctx.close()


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _in_view(page, selector, within="#slot-panel") -> bool:
    """Whether the element is fully inside the (scrolling) container's visible box."""
    return page.evaluate("""([sel, within]) => {
        const r = document.querySelector(sel).getBoundingClientRect(), c = document.querySelector(within).getBoundingClientRect();
        return r.height > 0 && r.top >= c.top && r.bottom <= c.bottom; }""", [selector, within])


# --- The divider ---

def test_divider_drag_resizes_the_preview_persists_and_reveals_settings(write_cfg, laptop):
    write_cfg()
    w = laptop()
    page = w.page
    page.locator(".preview-slot").first.click()
    assert not _in_view(page, "#slot-fill-mode")  # below the fold with the default split
    before = _box(page, "#preview-area")
    preview = _box(page, "#collage-preview")
    assert preview["width"] / preview["height"] == pytest.approx(16 / 9, abs=0.02)

    d = _box(page, "#pane-divider")
    x, y = d["x"] + d["width"] / 2, d["y"] + d["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 120, steps=4)
    page.mouse.move(x, y - 220, steps=4)
    page.mouse.up()
    after = _box(page, "#preview-area")
    assert after["height"] == pytest.approx(before["height"] - 220, abs=2)
    small = _box(page, "#collage-preview")
    assert small["width"] < preview["width"] - 300
    assert small["width"] / small["height"] == pytest.approx(16 / 9, abs=0.02)  # still the display, smaller
    assert _in_view(page, "#slot-fill-mode")  # the Fit / Fill choice is on screen now
    stored = page.evaluate("() => localStorage.getItem('picme.previewSplit')")
    assert 0 < float(stored) < 1

    page.reload(wait_until="networkidle")
    page.wait_for_selector("#layout-picker .layout-thumb")
    assert _box(page, "#preview-area")["height"] == pytest.approx(after["height"], abs=2)

    page.locator("#pane-divider").dblclick()  # back to the default
    assert _box(page, "#preview-area")["height"] == pytest.approx(before["height"], abs=2)
    assert page.evaluate("() => localStorage.getItem('picme.previewSplit')") is None
    assert w.errors == []


def test_divider_keys_touch_and_limits(write_cfg, laptop):
    write_cfg()
    w = laptop()
    page = w.page
    start = _box(page, "#preview-area")["height"]
    page.locator("#pane-divider").focus()
    page.keyboard.press("ArrowUp")
    up = _box(page, "#preview-area")["height"]
    assert up < start - 20
    assert page.locator("#pane-divider").get_attribute("aria-valuenow") is not None
    page.keyboard.press("Home")  # the smallest preview
    assert _box(page, "#preview-area")["height"] == pytest.approx(160, abs=1)
    page.keyboard.press("End")  # the largest: the full-width preview, the settings kept on screen
    end = _box(page, "#preview-area")["height"]
    assert _box(page, "#slot-panel")["height"] >= 119
    assert end > start

    # A touch drag (pointer events from a finger)
    d = _box(page, "#pane-divider")
    page.evaluate("""([y]) => {
        const el = document.getElementById('pane-divider');
        const ev = (type, cy) => el.dispatchEvent(new PointerEvent(type, { pointerId: 7, pointerType: 'touch', button: 0,
                                                                         clientY: cy, bubbles: true, isPrimary: true }));
        ev('pointerdown', y); ev('pointermove', y - 100); ev('pointerup', y - 100); }""", [d["y"] + 8])
    assert _box(page, "#preview-area")["height"] == pytest.approx(end - 100, abs=2)
    assert w.errors == []


# --- Folding sidebar sections ---

def test_sections_fold_persist_and_the_layout_links_still_work(write_cfg, laptop):
    write_cfg(layout_rotation={"enabled": True, "layouts": ["classic-4", "classic-6"], "change_at": "04:00"})
    w = laptop(editor_mode="basic")
    page = w.page
    frame = page.locator('.sidebar-section[data-section="frame"]')
    toggle = frame.locator(".section-toggle")
    assert toggle.get_attribute("aria-expanded") == "true" and page.locator("#g-mat-color").is_visible()
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert not page.locator("#g-mat-color").is_visible()
    assert json.loads(page.evaluate("() => localStorage.getItem('picme.collapsedSections')")) == ["frame"]

    # The Layout header: folding hides the thumbnails; Select all still works and doesn't unfold it
    layout = page.locator('.sidebar-section[data-section="layout"]')
    layout.locator(".section-toggle").click()
    assert not page.locator("#layout-picker").is_visible()
    page.locator("#rotation-select-all").click()
    assert layout.locator(".section-toggle").get_attribute("aria-expanded") == "false"
    _saved(write_cfg, lambda c: c["layout_rotation"]["layouts"] == [])

    page.reload(wait_until="networkidle")
    page.wait_for_selector("#layout-picker .layout-thumb", state="attached")
    assert not page.locator("#g-mat-color").is_visible() and not page.locator("#layout-picker").is_visible()
    page.locator('.sidebar-section[data-section="frame"] .section-toggle').click()
    assert page.locator("#g-mat-color").is_visible()
    # Basic still hides the sections that are all advanced, folded or not
    assert not page.locator('.sidebar-section[data-section="videos"]').is_visible()
    # Show metadata's own collapse still works inside its section
    page.locator(".toggle-row:has(#g-show-metadata) .toggle-track").click()
    _saved(write_cfg, lambda c: c["show_metadata"] is False)
    assert w.errors == []


# --- Sidebar order: Global Settings first (with the preset and Time-of-Day), Videos and Reset at the bottom ---

SECTIONS_JS = """() => [...document.querySelectorAll('#sidebar > .sidebar-section[data-section]')]
    .filter(s => s.checkVisibility()).map(s => [s.dataset.section, s.querySelector('h3').textContent.trim()])"""
ADVANCED_ORDER = [["global", "Global Settings"], ["layout", "Layout"],
                  ["frame", "Frame"], ["gallery", "Gallery Mat"], ["metadata", "Default Metadata"],
                  ["videos", "Videos"], ["reset", "Reset"]]


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_sidebar_order_with_time_of_day_in_global_and_resets_at_the_bottom(write_cfg, open_editor, mode):
    write_cfg()
    w = open_editor(editor_mode=mode)
    page = w.page
    expected = [s for s in ADVANCED_ORDER if mode == "advanced" or s[0] != "videos"]
    assert page.evaluate(SECTIONS_JS) == expected
    assert page.locator('[data-section="time_of_day"]').count() == 0
    assert page.locator('[data-section="profile"]').count() == 0
    # The last section in the sidebar, Basic and Advanced alike
    assert page.evaluate("() => [...document.querySelectorAll('#sidebar > .sidebar-section')].pop().dataset.section") == "reset"

    glob = page.locator('.sidebar-section[data-section="global"]')
    reset = page.locator('.sidebar-section[data-section="reset"]')
    for sel in ("#tod-enabled", "#tod-configure-btn"):
        assert glob.locator(sel).count() == 1
    assert reset.locator("#reset-defaults").is_visible()
    assert glob.locator("#reset-defaults").count() == 0
    # The preset sits at the top of Global Settings, before its first setting, in both modes
    for sel in ("#preset-save", "#preset-apply"):
        assert glob.locator(sel).is_visible()
    assert page.evaluate("""() => { const g = document.querySelector('[data-section="global"]');
        return !!(g.querySelector('#preset-save').compareDocumentPosition(g.querySelector('#g-source-path'))
                  & Node.DOCUMENT_POSITION_FOLLOWING); }""")
    reset_slots = reset.locator('[data-setting="slots.reset_all"]')
    assert reset_slots.count() == 1 and reset_slots.text_content().strip() == "Reset slots to global settings"
    assert page.locator('[data-section="global"] [data-setting="slots.reset_all"]').count() == 0
    if mode == "advanced":
        assert glob.locator(".toggle-row:has(#tod-enabled)").is_visible() and glob.locator("#tod-configure-btn").is_visible()
        assert reset_slots.is_visible()
    else:
        assert not glob.locator("#tod-configure-btn").is_visible() and not reset_slots.is_visible()
    assert w.errors == []


def test_time_of_day_in_global_counts_as_advanced_and_still_works(write_cfg, open_editor):
    write_cfg(time_of_day={"enabled": True, "day_start": "07:00", "night_start": "20:00", "day": {}, "night": {}})
    page = open_editor(editor_mode="basic").page
    assert page.locator("#advanced-hint-text").text_content() == "1 advanced setting active"
    page.locator("#advanced-hint-show").click()
    glob = page.locator('.sidebar-section[data-section="global"]')
    assert glob.locator("#tod-enabled").is_checked()
    glob.locator(".toggle-row:has(#tod-enabled) .toggle-track").click()
    _saved(write_cfg, lambda c: c["time_of_day"]["enabled"] is False)
    glob.locator("#tod-configure-btn").click()
    assert page.locator(".tod-modal-overlay").is_visible()
    assert not page.locator("#tod-modal-enabled").is_checked()


def test_both_resets_work_from_the_reset_section(write_cfg, open_editor):
    write_cfg(fill_mode="cover", wall_color="#123456", slot_overrides={2: {"fill_mode": "contain"}})
    page = open_editor(editor_mode="advanced").page
    reset = page.locator('.sidebar-section[data-section="reset"]')
    reset.get_by_role("button", name="Reset slots to global settings").click()
    page.locator(".modal").get_by_role("button", name="Reset all slots").click()
    cfg = _saved(write_cfg, lambda c: "fill_mode" not in c["slots"][1])
    assert cfg["wall_color"] == "#123456"

    reset.locator("#reset-defaults").click()
    _modal(page).get_by_role("button", name="Reset to defaults").click()
    _saved(write_cfg, lambda c: c["wall_color"] == "#1a1a1a")
    # Undo is at the top of Global Settings, and is scrolled into view and flashed (the reset is at the bottom)
    _until(page, "() => !document.getElementById('undo-row').hidden")
    assert page.locator('[data-section="global"] #undo-row').is_visible()
    _until(page, """() => { const r = document.getElementById('undo-row').getBoundingClientRect();
                               return r.top >= 0 && r.bottom <= window.innerHeight; }""")
    assert "flash" in page.locator("#undo-row").get_attribute("class")
    page.locator("#undo-btn").click()
    _modal(page).get_by_role("button", name="Undo").click()
    _saved(write_cfg, lambda c: c["wall_color"] == "#123456")


def test_a_reset_unfolds_global_settings_for_undo_and_remembers_it(write_cfg, open_editor):
    write_cfg(wall_color="#123456")
    folded = "try { localStorage.setItem('picme.collapsedSections', JSON.stringify(['global', 'frame'])) } catch (e) {}"
    page = open_editor(editor_mode="basic", init_scripts=[folded]).page
    stored = lambda: json.loads(page.evaluate("() => localStorage.getItem('picme.collapsedSections')"))
    assert not page.locator("#preset-save").is_visible()
    page.locator("#reset-defaults").click()
    _modal(page).get_by_role("button", name="Reset to defaults").click()
    _saved(write_cfg, lambda c: c["wall_color"] == "#1a1a1a")
    _until(page, "() => !document.getElementById('undo-row').hidden")
    assert page.locator("#undo-btn").is_visible()
    assert stored() == ["frame"]  # opened for Undo, and saved open


def test_reset_folds_and_the_old_time_of_day_and_my_settings_folds_are_forgotten(write_cfg, open_editor):
    write_cfg()
    folded = "try { localStorage.setItem('picme.collapsedSections', JSON.stringify(['time_of_day', 'profile', 'frame'])) } catch (e) {}"
    page = open_editor(editor_mode="basic", init_scripts=[folded]).page
    stored = lambda: json.loads(page.evaluate("() => localStorage.getItem('picme.collapsedSections')"))
    assert stored() == ["frame"]  # the other sections' folds are kept
    assert not page.locator("#g-mat-color").is_visible()
    reset = page.locator('.sidebar-section[data-section="reset"]')
    reset.locator(".section-toggle").click()
    assert not page.locator("#reset-defaults").is_visible()
    assert reset.locator(".section-toggle").get_attribute("aria-expanded") == "false"
    assert stored() == ["frame", "reset"]


# --- Ratio and Fit / Fill tags on preview cells ---

def _tags(page):
    return page.evaluate("() => [...document.querySelectorAll('#collage-preview .slot-tags')].map(t => [t.dataset.ratio, t.dataset.fit, t.title])")


def test_tags_match_the_displays_photo_openings(write_cfg, open_editor, server, browser):
    write_cfg(layout="hero-left", fill_mode="cover", frame={"mat_width": 20, "frame_width": 10},
              slot_overrides={2: {"fill_mode": "contain"}})
    ctx = browser.new_context(viewport=VIEWPORT)
    try:
        display = ctx.new_page()
        display.goto(server + "/display/", wait_until="networkidle")
        _until(display, MEDIA_READY_JS)
        sizes = display.evaluate("""() => [...document.querySelectorAll('.slot')].map(s => {
            const r = s.querySelector('img.visible').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })""")
    finally:
        ctx.close()
    page = open_editor().page
    tags = _tags(page)
    assert len(tags) == len(sizes) == 5
    for (ratio, fit, title), (w, h) in zip(tags, sizes):
        assert f"{w}×{h} px" in title, (title, w, h)
        assert ratio == page.evaluate("([w, h]) => PicMeAspect.label(w, h)", [w, h])
    assert [t[1] for t in tags] == ["fill", "fit", "fill", "fill", "fill"]
    texts = page.evaluate("() => [...document.querySelectorAll('#collage-preview .slot-tags')].map(t => t.innerText.replace(/\\n/g, ' '))")
    assert texts[1].endswith("Fit") and texts[0].endswith("Fill") and "·" in texts[0]

    # The global toggle: every slot without its own choice follows it
    page.locator(".toggle-row:has(#g-fill-mode) .toggle-track").click()
    assert [t[1] for t in _tags(page)] == ["fit"] * 5


def test_ratio_labels(write_cfg, open_editor):
    write_cfg()
    page = open_editor().page
    assert page.evaluate("""() => [[1500, 1000], [1000, 1500], [1600, 900], [1540, 1000], [1772, 434], [434, 1772], [700, 700]]
                                  .map(([w, h]) => PicMeAspect.label(w, h))""") == \
        ["3:2", "2:3", "16:9", "3:2", "4.1:1", "1:4.1", "1:1"]


def test_close_cells_use_the_common_name_and_odd_ones_a_decimal(write_cfg, open_editor):
    write_cfg()
    page = open_editor().page
    sizes = [[880, 1000], [850, 1000], [1160, 1000], [1106, 1000], [1111, 1000],
             [2090, 1000], [2300, 1000], [2800, 1000], [3800, 1000], [4200, 1000], [1000, 2300]]
    got = page.evaluate("(sizes) => sizes.map(([w, h]) => PicMeAspect.label(w, h))", sizes)
    assert got == ["4:5", "4:5", "5:4", "1:1", "1:1",
                   "2.1:1", "2.3:1", "2.8:1", "3.8:1", "4.2:1", "1:2.3"]


def test_gallery_tags_use_the_opening_inside_the_bevel(write_cfg, open_editor):
    write_cfg(layout="gallery-cameo", slot_overrides={1: {"fill_mode": "contain"}})
    page = open_editor().page
    got = page.evaluate("""() => [...document.querySelectorAll('#collage-preview .opening')].map(o => {
        const m = o.querySelector('.mat-frame'), t = o.querySelector('.slot-tags');
        return [t.dataset.ratio, PicMeAspect.label(m.offsetWidth, m.offsetHeight), t.dataset.fit, t.innerText]; })""")
    assert got and all(r == expected for r, expected, _, _ in got)
    assert got[0][2] == "fit" and got[0][3].endswith("Fit")
    assert all(g[2] == "fit" for g in got)  # no fill_mode anywhere else: an older config, so Fit


# --- Preset (top of Global Settings): reset, preset, undo ---

def _modal(page):
    return page.locator(".confirm-overlay .modal")


def test_reset_shows_the_count_resets_keeps_folders_and_undoes(write_cfg, open_editor, server):
    other = str(write_cfg.root / "other")
    before = write_cfg(layout="hero-left", wall_color="#123456", fill_mode="contain",
                       layout_rotation={"enabled": True, "layouts": [], "change_at": "06:00"},
                       slot_overrides={2: {"use_global": {"source": False, "metadata": True, "frame": True},
                                           "source_path": other, "group": "B"}})
    w = open_editor()
    page = w.page
    assert not page.locator("#undo-row").is_visible()
    dry = page.evaluate("async () => (await fetch('/api/config/reset?dry_run=true', {method: 'POST'})).json()")
    page.locator("#reset-defaults").click()
    modal = _modal(page)
    assert modal.locator("p").first.text_content() == f"{dry['count']} settings differ from default."
    modal.locator("li").first.wait_for()
    assert "Layout" in modal.locator("li").all_text_contents()
    assert "Kept: your media folders" in modal.text_content()
    modal.get_by_role("button", name="Reset to defaults").click()
    cfg = _saved(write_cfg, lambda c: c["layout"] == "classic-4")
    assert cfg["layout_rotation"]["enabled"] is False and cfg["wall_color"] == "#1a1a1a" and cfg["fill_mode"] == "cover"
    assert cfg["source_path"] == before["source_path"]
    assert cfg["slots"][1]["source_path"] == other and cfg["slots"][1]["use_global"]["source"] is False
    assert "group" not in cfg["slots"][1] and len(cfg["slots"]) == len(before["slots"])
    # The server saves before it replies, so wait for the editor to take in its reply
    _until(page, "() => !document.getElementById('undo-row').hidden")
    assert page.locator("#g-wall-color").input_value() == "#1a1a1a"  # the editor shows the reset config

    page.locator("#undo-btn").click()
    _modal(page).get_by_role("button", name="Undo").click()
    _saved(write_cfg, lambda c: c == before)
    _until(page, "() => document.getElementById('undo-row').hidden")
    assert page.locator("#g-wall-color").input_value() == "#123456"
    assert w.errors == []


def test_a_pending_edit_is_saved_before_reset_and_cant_overwrite_it(write_cfg, open_editor):
    write_cfg()
    page = open_editor().page
    page.locator("#g-wall-color").evaluate("el => { el.value = '#654321'; el.dispatchEvent(new Event('input')); }")
    page.locator("#reset-defaults").click()  # within the 300 ms autosave delay
    modal = _modal(page)
    modal.locator("li").first.wait_for()
    assert "Wall color" in modal.locator("li").all_text_contents()
    modal.get_by_role("button", name="Reset to defaults").click()
    _saved(write_cfg, lambda c: c["wall_color"] == "#1a1a1a" and c["layout"] == "classic-4")
    time.sleep(1)
    assert json.loads(write_cfg.path.read_text())["wall_color"] == "#1a1a1a"


def test_preset_save_apply_keeps_folders_and_is_shared(write_cfg, open_editor):
    other = str(write_cfg.root / "other")
    write_cfg(wall_color="#224466", fill_mode="contain", label_position="mat")
    w = open_editor()
    page = w.page
    assert page.locator("#preset-apply").is_disabled()
    page.locator("#preset-save").click()  # nothing to replace yet: saved at once
    page.wait_for_function("() => document.getElementById('preset-status').textContent.startsWith('My preset: saved')")
    assert page.locator("#preset-apply").is_enabled()

    # Change looks and folders, then apply: looks come back, folders stay as they are now
    page.locator("#g-wall-color").evaluate("el => { el.value = '#999999'; el.dispatchEvent(new Event('input')); }")
    page.locator(".toggle-row:has(#g-fill-mode) .toggle-track").click()
    page.locator("#g-source-path").fill(other)
    page.locator("#g-source-path").dispatch_event("change")
    page.locator("#preset-apply").click()
    modal = _modal(page)
    assert "2 settings will change" in modal.text_content()
    modal.get_by_role("button", name="Apply my preset").click()
    cfg = _saved(write_cfg, lambda c: c["wall_color"] == "#224466")
    assert cfg["fill_mode"] == "contain" and cfg["label_position"] == "mat" and cfg["source_path"] == other
    _until(page, "() => document.getElementById('undo-text').textContent.startsWith('Preset applied')")

    # Saving again asks first
    page.locator("#preset-save").click()
    assert _modal(page).locator("h3").text_content() == "Replace my preset?"
    _modal(page).get_by_role("button", name="Cancel").click()

    # Another device's editor sees the preset (it's on the server)
    page2 = open_editor().page
    assert page2.locator("#preset-apply").is_enabled()
    assert page2.locator("#preset-status").text_content().startswith("My preset: saved")
    assert w.errors == []


def test_applying_a_preset_with_a_removed_layout_warns(write_cfg, open_editor):
    write_cfg(layout="classic-6")
    (write_cfg.root / "presets.json").write_text(json.dumps({"version": 1, "presets": [
        {"id": "my-preset", "name": "My preset", "created": "2026-10-01T10:00:00+00:00",
         "updated": "2026-10-01T10:00:00+00:00", "settings": {"layout": "gone-layout", "wall_color": "#445566"}}]}))
    page = open_editor().page
    page.locator("#preset-apply").click()
    modal = _modal(page)
    assert "gone-layout" in modal.locator("li.warn").first.text_content()
    modal.get_by_role("button", name="Apply my preset").click()
    cfg = _saved(write_cfg, lambda c: c["wall_color"] == "#445566")
    assert cfg["layout"] == "classic-6"


@pytest.mark.parametrize("layout", ["classic-4", "classic-6", "hero-center", "gallery-12"])
def test_preview_stays_16_9_at_the_dividers_minimum(write_cfg, laptop, layout):
    write_cfg(layout=layout)
    page = laptop().page
    page.locator("#pane-divider").focus()
    page.keyboard.press("Home")
    assert _box(page, "#preview-area")["height"] == pytest.approx(160, abs=1)
    p = _box(page, "#collage-preview")
    assert p["width"] / p["height"] == pytest.approx(16 / 9, abs=0.01)
    f = _box(page, "#preview-fit")
    assert p["height"] <= f["height"] + 1 and p["width"] <= f["width"] + 1


# --- Polish ---

def test_apply_my_preset_is_greyed_until_a_preset_exists(write_cfg, open_editor):
    write_cfg()
    page = open_editor(editor_mode="basic").page
    apply = page.locator("#preset-apply")
    _until(page, "() => document.getElementById('preset-apply').classList.contains('field-inactive')")
    assert apply.is_disabled() and apply.get_attribute("title") == "Save a preset first."
    page.locator("#preset-save").click()
    _until(page, "() => !document.getElementById('preset-apply').classList.contains('field-inactive')")
    assert apply.is_enabled() and not apply.get_attribute("title")


def test_an_adopted_slot_folder_box_cannot_be_edited_by_keyboard(write_cfg, open_editor):
    """QA a4d84956: in Advanced, a slot following the default folder had a greyed box that the
    keyboard could still edit, saving a stale source_path."""
    write_cfg()
    page = open_editor(editor_mode="advanced").page
    page.locator(".preview-slot").nth(1).click()
    box = page.locator("#slot-panel input[type=text]").first
    assert box.is_disabled()
