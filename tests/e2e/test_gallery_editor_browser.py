"""Browser end-to-end tests for gallery mat mode in the editor (README → Gallery mat, Editor modes).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides ``e2e_config_path``.
The editor saves on its own (500 ms debounce); tests poll the config file for the result.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import LANDSCAPE, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401
from test_frames_browser import MEDIA_READY_JS, _until  # noqa: E402

GALLERY = {"mat_color": "#00ffff", "outer_margin": 64, "gutter": 28, "wall_margin": 12, "bevel": True,
           "bevel_width": 4, "bevel_color": "#ffffff", "show_frame": True, "frame_style": "solid",
           "frame_color": "#00ff00", "frame_width": 40}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-gallery-editor")
    for i in range(1, 13):
        (root / f"c{i}").mkdir()
        _make_image(root / f"c{i}" / "photo.jpg", size=LANDSCAPE, fmt="JPEG", color=(20 * i, 60, 120))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "c1"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(layout="classic-4", slots=4, gallery=None, slot_overrides=None, **extra) -> dict:
        cfg = make_config(root / "c1", layout=layout, slots=slots, cycle_seconds=300)
        for i, slot in enumerate(cfg["slots"], start=1):
            slot["source_path"] = str(root / f"c{i}")
            slot["use_global"] = {"source": False, "frame": True, "metadata": True}
        cfg["source_path"] = str(root / "c1")
        cfg["wall_color"] = "#ff00ff"
        cfg["show_metadata"] = False
        if gallery is not False:
            cfg["gallery"] = {**GALLERY, **(gallery or {})}
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
def open_page(browser, server):
    contexts = []

    def _open(path="/editor/", editor_mode="advanced", viewport=VIEWPORT) -> PageWatcher:
        ctx = browser.new_context(viewport=viewport, device_scale_factor=1)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
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
        try:
            cfg = json.loads(write_cfg.path.read_text())
        except ValueError:
            cfg = None  # read while the server was writing it
        if cfg is not None and check(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never saved as expected: {write_cfg.path.read_text()}")


def _opening(n):
    return f"#collage-preview .opening[data-slot='{n}']"


def _pressed(page) -> str:
    return page.locator("#layout-mode button[aria-pressed='true']").get_attribute("data-mode")


def _center(page, selector):
    box = page.locator(selector).bounding_box()
    assert box, f"{selector} is not visible"
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _set_number(page, selector, value):
    page.fill(selector, str(value))
    page.locator(selector).dispatch_event("change")


# --- Frames switch and layout picker ---

def test_frames_switch_saves_the_layout_mode(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    assert _pressed(page) == "slots"
    assert page.locator("#collage-preview .preview-slot").count() == 4
    assert page.locator("#gallery-fields").is_hidden() and page.locator("#gallery-off-hint").is_visible()

    page.click("#layout-mode button[data-mode='gallery']")
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"classic-4": "gallery"})
    assert page.locator("#collage-preview.gallery-preview .opening").count() == 4
    assert page.locator("#gallery-fields").is_visible()
    assert page.locator(".layout-thumb[data-id='classic-4'] .gallery-badge").count() == 1

    page.click("#layout-mode button[data-mode='slots']")
    _saved(write_cfg, lambda c: c.get("layout_modes") == {})  # the layout's own default: no entry
    assert page.locator("#collage-preview .preview-slot").count() == 4
    assert w.errors == []


def test_gallery_layouts_default_to_the_mat_and_can_use_separate_frames(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    for lid in ("gallery-12", "gallery-feature", "gallery-mosaic", "gallery-cameo", "gallery-arches", "gallery-salon"):
        assert page.locator(f".layout-thumb[data-id='{lid}'] .gallery-badge").count() == 1, lid
        assert page.locator(f".layout-thumb[data-id='{lid}'] .thumb-gallery .opening").count() > 0, lid
    assert page.locator(".layout-thumb[data-id='hero-left'] .gallery-badge").count() == 0

    page.click(".layout-thumb[data-id='gallery-cameo']")
    cfg = _saved(write_cfg, lambda c: c["layout"] == "gallery-cameo")
    assert cfg.get("layout_modes", {}) == {} and _pressed(page) == "gallery"
    shapes = page.evaluate("() => [...document.querySelectorAll('#collage-preview .opening')].map(o => o.dataset.shape)")
    assert shapes == ["ellipse", "arch", "ellipse", "ellipse", "ellipse"]

    page.click("#layout-mode button[data-mode='slots']")
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"gallery-cameo": "slots"})
    assert page.locator("#collage-preview .preview-slot").count() == 5
    assert w.errors == []


# --- Gallery Mat section ---

def test_gallery_mat_fields_update_the_preview_and_save(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12)
    w = open_page()
    page = w.page
    gap = "() => getComputedStyle(document.querySelector('#collage-preview .gallery-mat')).rowGap"
    assert page.evaluate(gap) == "28px"
    _set_number(page, "#g-gal-gutter", 50)
    _saved(write_cfg, lambda c: c["gallery"]["gutter"] == 50)
    assert page.evaluate(gap) == "50px"
    # Out of range or not a whole number: reverts, nothing saved
    for bad in ("500", "-3", "2.5", "1e1"):
        _set_number(page, "#g-gal-gutter", bad)
        assert page.input_value("#g-gal-gutter") == "50", bad
    page.wait_for_timeout(700)
    assert _saved(write_cfg, lambda c: True)["gallery"]["gutter"] == 50

    page.fill("#g-gal-mat-color", "#123456")
    _saved(write_cfg, lambda c: c["gallery"]["mat_color"] == "#123456")
    assert page.evaluate("() => getComputedStyle(document.querySelector('#collage-preview .gallery-mat')).backgroundColor") == "rgb(18, 52, 86)"

    # The gallery's own frame style shows only with "Use a different frame" on
    assert page.locator("#g-gal-frame-style").is_hidden()
    page.click("#g-gal-frame-override + .toggle-track")
    _saved(write_cfg, lambda c: c.get("gallery", {}).get("frame_override") is True)
    page.select_option("#g-gal-frame-style", "gold")
    _saved(write_cfg, lambda c: c["gallery"]["frame_style"] == "gold")
    assert "gold.png" in page.evaluate("() => getComputedStyle(document.querySelector('#collage-preview .gallery-frame')).borderImageSource")
    assert page.locator("#g-gal-frame-color").is_disabled()

    page.click("#g-gal-bevel + .toggle-track")
    _saved(write_cfg, lambda c: c["gallery"]["bevel"] is False)
    assert page.locator("#collage-preview .opening-deco .bevel").count() == 0
    assert w.errors == []


def test_basic_shows_mat_colour_and_the_frame_style_and_counts_the_rest(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12, gallery={"gutter": 50})
    page = open_page(editor_mode="basic").page
    assert page.locator("#g-gal-mat-color").is_visible()
    # The frame style you see is set in the Frame section, which says so on a gallery screen
    assert page.locator("#g-frame-style").is_visible() and page.locator("#g-frame-style").is_enabled()
    assert page.locator("#frame-gallery-hint").is_visible()
    assert page.locator("#frame-gallery-hint").text_content() == "Also the outer frame of gallery-mat layouts."
    # Mat colour applies to separate frames only: disabled on a gallery-only screen, saying why
    assert page.locator("#g-mat-color").is_disabled()
    assert page.locator(".field:has(#g-mat-color) + .scope-hint").text_content() == "Separate-frame layouts only."
    for sel in ("#g-gal-gutter", "#g-gal-margin", "#g-gal-frame-width", "#g-gal-bevel-width", "#g-gal-wall-margin",
                "#g-gal-frame-style", "#g-gal-frame-color", "#g-gal-show-frame", "#g-gal-frame-override"):
        assert page.locator(sel).is_hidden(), sel
    assert page.locator("#layout-mode").is_visible()  # the Frames switch is Basic
    assert page.locator("#advanced-hint").is_visible()
    assert "Gallery: mat between openings" in page.locator("#advanced-hint").get_attribute("title")
    page.click("#advanced-hint-show")
    assert page.locator("#g-gal-gutter").is_visible()


# --- One frame control: the Frame section drives the frame in both modes ---

FRAME_STYLE_JS = """() => {
  const g = document.querySelector('.gallery-frame');
  if (g) return 'gallery:' + g.dataset.frameStyle;
  const src = getComputedStyle(document.querySelector('#slot-1 .mat-frame')).borderImageSource;
  return 'slots:' + (src.includes('gold.png') ? 'gold' : src.includes('wood.png') ? 'wood' : 'solid');
}"""


def test_frame_style_control_changes_the_frame_in_both_modes(write_cfg, open_page):
    write_cfg(frame_defaults={"mat_color": "#000000", "mat_width": 16, "show_frame": True,
                              "frame_color": "#2c2318", "frame_width": 8, "frame_style": "solid"})
    display = open_page("/display/").page
    _until(display, MEDIA_READY_JS)
    w = open_page(editor_mode="basic")
    page = w.page
    assert display.evaluate(FRAME_STYLE_JS) == "slots:solid"

    page.select_option("#g-frame-style", "gold")
    _until(display, f"() => ({FRAME_STYLE_JS})() === 'slots:gold'", timeout=8)

    page.click("#layout-mode button[data-mode='gallery']")
    _until(display, "() => document.getElementById('collage')?.classList.contains('gallery')", timeout=8)
    _until(display, f"() => ({FRAME_STYLE_JS})() === 'gallery:gold'", timeout=8)
    _until(display, MEDIA_READY_JS)
    display.evaluate("window.__marker = 1")
    assert page.locator("#frame-gallery-hint").is_visible()
    page.select_option("#g-frame-style", "wood")  # the same, visible control
    _until(display, f"() => ({FRAME_STYLE_JS})() === 'gallery:wood'", timeout=8)
    assert display.evaluate("window.__marker") == 1  # patched in place
    # The editor's own preview and thumbnail follow it too
    assert page.evaluate("() => document.querySelector('#collage-preview .gallery-frame').dataset.frameStyle") == "wood"
    assert page.evaluate("() => document.querySelector(\".layout-thumb[data-id='classic-4'] .gallery-frame\").dataset.frameStyle") == "wood"

    # Frame colour, on a solid frame (Advanced)
    page.click("#editor-mode button[data-mode='advanced']")
    page.select_option("#g-frame-style", "solid")
    page.fill("#g-frame-color", "#00ff00")
    _saved(write_cfg, lambda c: c["frame_defaults"]["frame_color"] == "#00ff00")
    _until(display, "() => getComputedStyle(document.querySelector('.gallery-frame')).borderTopColor === 'rgb(0, 255, 0)'", timeout=8)
    assert display.evaluate("window.__marker") == 1
    assert w.errors == []


def test_gallery_frame_override_and_controls_that_do_nothing_are_disabled(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12, frame_defaults={"frame_style": "wood", "frame_width": 28})
    w = open_page()
    page = w.page
    frame = "() => document.querySelector('#collage-preview .gallery-frame').dataset.frameStyle"
    assert page.evaluate(frame) == "wood"
    # Gallery-only screen: mat and widths are separate-frame settings
    for sel in ("#g-mat-color", "#g-mat-width", "#g-frame-width"):
        assert page.locator(sel).is_disabled(), sel
    assert page.locator(".field:has(#g-mat-width) + .scope-hint").is_visible()
    assert page.locator("#g-frame-style").is_enabled()

    page.click("#g-gal-frame-override + .toggle-track")
    _saved(write_cfg, lambda c: c.get("gallery", {}).get("frame_override") is True)
    assert page.locator("#g-gal-frame-style").is_visible()
    page.select_option("#g-gal-frame-style", "gold")
    _saved(write_cfg, lambda c: c["gallery"]["frame_style"] == "gold")
    assert page.evaluate(frame) == "gold"  # the gallery's own frame wins
    assert page.locator("#frame-gallery-hint").is_hidden()
    # Now nothing on screen uses the Frame section's style: disabled, with the reason
    assert page.locator("#g-frame-style").is_disabled()
    assert "Gallery mats use their own frame" in page.locator(".field:has(#g-frame-style) + .scope-hint").text_content()
    page.click("#editor-mode button[data-mode='basic']")
    assert page.locator("#g-gal-frame-style").is_visible()  # Basic shows the control that does take effect

    # A day with separate frames (rotation) makes them all useful again
    page.click("#editor-mode button[data-mode='advanced']")
    page.click("#g-rotation-enabled + .toggle-track")
    _saved(write_cfg, lambda c: c["layout_rotation"]["enabled"] is True)
    for sel in ("#g-mat-color", "#g-mat-width", "#g-frame-width", "#g-frame-style"):
        assert page.locator(sel).is_enabled(), sel
    assert page.locator(".scope-hint:visible").count() == 0
    assert w.errors == []


# --- Label position ---

def test_label_position_control_saves_and_previews_captions(write_cfg, open_page):
    write_cfg(layout="gallery-salon", slots=8, show_metadata=True,
              metadata_defaults={"show_date": True, "show_camera": True})
    w = open_page(editor_mode="basic")
    page = w.page
    sel = page.locator("#g-label-position")
    assert sel.is_visible() and sel.input_value() == "auto"
    caption = "() => [...document.querySelectorAll('#collage-preview .slot-overlay')].map(o => o.classList.contains('caption'))"
    assert page.evaluate(caption) == [True] * 8  # auto: on the mat for a gallery mat
    # Every preview caption is under its opening's box
    assert page.evaluate("""() => [...document.querySelectorAll('#collage-preview .opening')].every(o => {
        const ov = o.querySelector('.slot-overlay').getBoundingClientRect(), b = o.querySelector('.opening-box').getBoundingClientRect();
        return ov.top >= b.bottom - 0.5; })""")
    sel.select_option("photo")
    _saved(write_cfg, lambda c: c.get("label_position") == "photo")
    assert page.evaluate(caption) == [False] * 8

    page.click("#layout-mode button[data-mode='slots']")
    sel.select_option("mat")
    _saved(write_cfg, lambda c: c.get("label_position") == "mat")
    # Separate frames: the bars sit in each cell's (taller) bottom mat, under the photo, one row
    bars = page.evaluate("""() => [...document.querySelectorAll('.preview-slot')].map(el => {
        const pills = el.querySelector('.preview-pills');
        const photo = [...el.children].find(c => !c.classList.contains('preview-pills') && c.offsetHeight > 20);
        return { top: pills.getBoundingClientRect().top, photoBottom: photo.getBoundingClientRect().bottom,
                 row: getComputedStyle(pills).flexDirection }; })""")
    assert all(b["top"] >= b["photoBottom"] - 0.5 and b["row"] == "row" for b in bars), bars
    assert w.errors == []


# --- Opening shape picker ---

def test_shape_picker_sets_and_clears_a_slot_shape(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12)
    w = open_page()
    page = w.page
    page.click(_opening(3))
    shape_select = page.locator("#slot-shape")
    assert shape_select.input_value() == ""
    assert shape_select.locator("option").first.text_content() == "Layout default (Rectangle)"
    shape_select.select_option("heart")
    _saved(write_cfg, lambda c: c["slots"][2].get("shape") == "heart")
    assert page.get_attribute(_opening(3), "data-shape") == "heart"
    assert page.locator(f"{_opening(3)} .opening-selected").count() == 1  # the outline follows the shape
    # Fit (contain) in a shape gets a hint
    assert page.get_by_text("Fill usually looks better in shaped openings").is_visible()
    page.locator("#slot-shape").select_option("")
    _saved(write_cfg, lambda c: "shape" not in c["slots"][2])
    assert page.get_attribute(_opening(3), "data-shape") == "rect"
    assert w.errors == []


def test_shape_picker_hint_on_separate_frames(write_cfg, open_page):
    write_cfg()
    page = open_page().page
    page.click("#collage-preview .preview-slot[data-slot='1']")
    assert page.get_by_text("Shapes show on gallery-mat layouts").is_visible()
    assert page.locator("#slot-shape").is_visible()  # still editable for gallery days


def test_shape_picker_is_basic_and_reset_all_leaves_shapes(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12, slot_overrides={2: {"shape": "circle"}})
    page = open_page(editor_mode="basic").page
    page.click(_opening(2))
    assert page.locator("#slot-shape").is_visible() and page.locator("#slot-shape").input_value() == "circle"
    page.click("#editor-mode button[data-mode='advanced']")
    page.get_by_role("button", name="Reset slots to global settings").click()
    assert "Opening shape" in page.locator(".modal-note").text_content()
    page.get_by_role("button", name="Reset all slots", exact=True).click()
    page.click("#footer .btn-primary")
    cfg = _saved(write_cfg, lambda c: c["slots"][1].get("use_global", {}).get("source") is True)
    assert cfg["slots"][1]["shape"] == "circle"


# --- Drag to swap keeps shapes with the position ---

def test_drag_swap_in_the_gallery_preview_keeps_shapes(write_cfg, open_page):
    cfg = write_cfg(layout_modes={"classic-4": "gallery"}, slot_overrides={3: {"shape": "heart", "mode": "photos"}})
    w = open_page()
    page = w.page
    x0, y0 = _center(page, _opening(1))
    x1, y1 = _center(page, _opening(3))
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=10)
    assert page.locator(f"{_opening(3)}.drop-target").count() == 1
    page.mouse.up()
    saved = _saved(write_cfg, lambda c: c["slots"][0]["source_path"] == cfg["slots"][2]["source_path"])
    assert saved["slots"][2]["source_path"] == cfg["slots"][0]["source_path"]
    assert saved["slots"][2]["shape"] == "heart" and "shape" not in saved["slots"][0]
    assert [s["id"] for s in saved["slots"]] == [1, 2, 3, 4]
    assert page.get_attribute(_opening(3), "data-shape") == "heart"
    assert w.errors == []


# --- The preview is the display ---

GEOMETRY_JS = """sel => {
  const root = document.querySelector(sel).getBoundingClientRect();
  const k = root.width / 1920;
  return [...document.querySelectorAll(`${sel} .opening`)].map(o => {
    const r = o.querySelector('.opening-box').getBoundingClientRect();
    return { id: +o.dataset.slotId, shape: o.dataset.shape,
             x: (r.x - root.x) / k, y: (r.y - root.y) / k, w: r.width / k, h: r.height / k };
  });
}"""


@pytest.mark.parametrize("layout", ["gallery-salon", "gallery-cameo"])
def test_editor_preview_matches_the_display(write_cfg, open_page, layout):
    write_cfg(layout=layout, slots=12, slot_overrides={1: {"shape": "hexagon"}})
    display = open_page("/display/").page
    _until(display, MEDIA_READY_JS)
    editor = open_page().page
    shown = display.evaluate(GEOMETRY_JS, "#collage")
    preview = editor.evaluate(GEOMETRY_JS, "#collage-preview")
    assert [(o["id"], o["shape"]) for o in preview] == [(o["id"], o["shape"]) for o in shown]
    for a, b in zip(shown, preview):
        for key in ("x", "y", "w", "h"):
            assert b[key] == pytest.approx(a[key], abs=3), (a, b)
    mat = "sel => getComputedStyle(document.querySelector(`${sel} .gallery-mat`)).backgroundColor"
    assert display.evaluate(mat, "#collage") == editor.evaluate(mat, "#collage-preview") == "rgb(0, 255, 255)"
    frame = "sel => getComputedStyle(document.querySelector(`${sel} .gallery-frame`)).borderTopWidth"
    assert display.evaluate(frame, "#collage") == editor.evaluate(frame, "#collage-preview") == "40px"


@pytest.mark.parametrize("layout", ["gallery-salon", "gallery-cameo"])
def test_editor_preview_captions_match_the_display(write_cfg, open_page, layout):
    write_cfg(layout=layout, slots=12, show_metadata=True, metadata_defaults={"show_date": True, "show_camera": True},
              slot_overrides={1: {"shape": "heart"}})
    display = open_page("/display/").page
    _until(display, MEDIA_READY_JS)
    _until(display, "() => document.querySelector('.slot-overlay.caption')")
    editor = open_page().page
    caption_js = GEOMETRY_JS.replace("const r = o.querySelector('.opening-box').getBoundingClientRect();",
                                     "const r = o.querySelector('.slot-overlay').getBoundingClientRect();")
    for js in (GEOMETRY_JS, caption_js):
        shown, preview = display.evaluate(js, "#collage"), editor.evaluate(js, "#collage-preview")
        assert len(shown) == len(preview)
        for a, b in zip(shown, preview):
            for key in ("y", "h") if js is caption_js else ("x", "y", "w", "h"):  # captions are as wide as their pills
                assert b[key] == pytest.approx(a[key], abs=3), (key, a, b)


# --- Rotation: per-layout mode chips ---

def test_rotation_mode_chips_and_today_badge(write_cfg, open_page):
    write_cfg(slots=5, layout_rotation={"enabled": True, "layouts": ["classic-4", "gallery-cameo"], "change_at": "04:00"})
    w = open_page()
    page = w.page
    chip = lambda lid: page.locator(f".layout-thumb[data-id='{lid}'] .layout-mode-chip")  # noqa: E731
    assert chip("classic-4").get_attribute("aria-pressed") == "false"
    assert chip("gallery-cameo").get_attribute("aria-pressed") == "true"
    chip("classic-4").click()
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"classic-4": "gallery"})
    assert chip("classic-4").get_attribute("aria-pressed") == "true"
    assert _pressed(page) == "gallery"  # classic-4 is the base layout
    chip("gallery-cameo").click()
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"classic-4": "gallery", "gallery-cameo": "slots"})
    assert _saved(write_cfg, lambda c: True)["layout"] == "classic-4"  # a chip click doesn't select the layout
    assert page.locator(".layout-thumb .today-badge").count() == 1
    # Basic hides the chips and counts them
    page.click("#editor-mode button[data-mode='basic']")
    assert chip("classic-4").is_hidden()
    assert "Frames per rotated layout" in page.locator("#advanced-hint").get_attribute("title")
    assert w.errors == []


def test_basic_mode_with_gallery_frame_override_has_a_visible_gallery_frame_control(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12, gallery={"frame_override": True, "frame_style": "solid"},
              frame_defaults={"frame_style": "wood", "frame_width": 28})
    w = open_page(editor_mode="basic")
    page = w.page
    frame = "() => document.querySelector('#collage-preview .gallery-frame').dataset.frameStyle"
    assert page.evaluate(frame) == "solid"
    # The Frame section's style does nothing here, so the place that does must be visible in Basic
    assert page.locator("#g-frame-style").is_disabled()
    assert page.locator("#g-gal-frame-override").evaluate("e => e.closest('.toggle-row').getBoundingClientRect().height") > 0
    assert page.locator("#g-gal-frame-style").is_visible()
    assert page.locator("#g-gal-frame-color").is_visible()
    # The help text agrees with the override
    help_text = page.locator("#gallery-help").text_content()
    assert "come from the Frame section" not in help_text
    assert "set above" in help_text

    page.select_option("#g-gal-frame-style", "gold")
    _saved(write_cfg, lambda c: c["gallery"]["frame_style"] == "gold")
    assert page.evaluate(frame) == "gold"

    # Override off: the Frame section drives the frame again, and the text says so
    page.click("#g-gal-frame-override + .toggle-track")
    _saved(write_cfg, lambda c: c["gallery"]["frame_override"] is False)
    assert page.evaluate(frame) == "wood"
    assert page.locator("#g-frame-style").is_enabled()
    assert "come from the Frame section" in page.locator("#gallery-help").text_content()
    assert w.errors == []
