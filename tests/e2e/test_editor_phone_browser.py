"""Browser end-to-end tests for the editor on a phone and a portrait tablet (README → Editing from a phone).

At 820 px wide or less the sidebar becomes a Layout | Settings tab bar: Layout has the full-width
16:9 preview with the slot settings below it, Settings has the sidebar sections. Pages here are
touch contexts (has_touch, is_mobile) at 390x844 and 768x1024; touch drags send real touch events
through the Chrome DevTools protocol. At 1440x900 the desktop layout is unchanged.

Reuses the editor-mode module's fixtures (``write_cfg`` writes a first-run-shaped config).
"""
from __future__ import annotations

import json

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import PageWatcher, browser, server  # noqa: E402,F401
from test_editor_mode_browser import _saved, write_cfg  # noqa: E402,F401
from test_frames_browser import _until  # noqa: E402

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 768, "height": 1024}
LAPTOP = {"width": 1440, "height": 900}
SCREENS = pytest.mark.parametrize("screen", [PHONE, TABLET], ids=["phone", "tablet"])
ROTATING = {"enabled": True, "layouts": ["classic-4", "classic-6"], "change_at": "04:00"}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-editor-phone")
    (root / "photos").mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40)]):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True))
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
def device(browser, server):
    """An editor page in a touch context of the given size (a phone or a tablet), or a laptop."""
    contexts = []

    def _open(viewport, editor_mode="advanced", init_scripts=()) -> PageWatcher:
        touch = viewport["width"] <= 1024
        ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch)
        contexts.append(ctx)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        for script in init_scripts:
            ctx.add_init_script(script)
        page = ctx.new_page()
        w = PageWatcher(page)
        page.goto(server + "/editor/", wait_until="networkidle")
        page.wait_for_selector("#layout-picker .layout-thumb", state="attached")
        page.wait_for_selector("#collage-preview .preview-slot, #collage-preview .opening", state="attached")
        return w

    yield _open
    for ctx in contexts:
        ctx.close()


def _box(page, selector):
    return page.locator(selector).bounding_box()


def _tab(page, name):
    return page.locator(f'#mobile-tabs button[data-tab="{name}"]')


def _cell(n):
    return f"#collage-preview .preview-slot[data-slot='{n}']"


def _no_horizontal_scroll(page):
    return page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")


# Whether the element is fully on screen, below the sticky tab bar
_IN_VIEW_BELOW_TABS_JS = """sel => {
    const r = document.querySelector(sel).getBoundingClientRect();
    const tabs = document.getElementById('mobile-tabs').getBoundingClientRect();
    return r.height > 0 && r.top >= tabs.bottom - 1 && r.bottom <= window.innerHeight; }"""


class Touch:
    """Real touch input through CDP: the browser turns it into pointer, touch and scroll events."""

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)

    def _send(self, kind, x=None, y=None):
        points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    def drag(self, src, dst, *, hold_ms):
        # No panning first: on a phone the whole preview is on screen
        a, b = _box(self.page, src), _box(self.page, dst)
        x0, y0 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
        x1, y1 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self._send("touchStart", x0, y0)
        self.page.wait_for_timeout(hold_ms)
        for i in range(1, 11):
            self._send("touchMove", x0 + (x1 - x0) * i / 10, y0 + (y1 - y0) * i / 10)
            self.page.wait_for_timeout(16)
        self._send("touchEnd")


# --- The layout ---

@SCREENS
def test_no_horizontal_scroll_on_either_tab(write_cfg, device, screen):
    write_cfg(layout_rotation=ROTATING)  # the rotation boxes and Mat chips show on the thumbnails
    w = device(screen)
    page = w.page
    page.locator(_cell(1)).tap()
    page.wait_for_selector("#slot-panel-body .panel-section")
    scroll, width = _no_horizontal_scroll(page)
    assert scroll <= width == screen["width"]
    _tab(page, "settings").tap()
    page.locator("#tod-configure-btn").wait_for()
    scroll, width = _no_horizontal_scroll(page)
    assert scroll <= width
    # Nothing in the sidebar runs off the right edge (overflow that's clipped wouldn't scroll)
    assert page.evaluate("""() => [...document.querySelectorAll('#sidebar *')]
        .filter(el => el.offsetParent && el.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map(el => el.id || el.className)""") == []
    assert w.errors == []


@SCREENS
def test_tabs_switch_and_are_remembered(write_cfg, device, screen):
    write_cfg()
    w = device(screen)
    page = w.page
    tabs = page.locator("#mobile-tabs")
    assert tabs.is_visible()
    # A fresh browser opens on Layout: the preview, no sidebar
    assert _tab(page, "layout").get_attribute("aria-selected") == "true"
    assert page.locator("#collage-preview").is_visible() and not page.locator("#sidebar").is_visible()
    assert not page.locator("#pane-divider").is_visible()

    _tab(page, "settings").tap()
    assert _tab(page, "settings").get_attribute("aria-selected") == "true"
    assert not page.locator("#collage-preview").is_visible()
    # Settings: the sidebar at full width, the Basic / Advanced switch at the top, then Global Settings
    assert page.locator("#sidebar").bounding_box()["width"] == pytest.approx(screen["width"], abs=1)
    switch, tabs_box = _box(page, "#editor-mode"), _box(page, "#mobile-tabs")
    assert tabs_box["y"] + tabs_box["height"] <= switch["y"] < tabs_box["y"] + tabs_box["height"] + 40
    sections = page.evaluate("() => [...document.querySelectorAll('#sidebar > .sidebar-section[data-section]')].map(s => s.dataset.section)")
    assert sections[0] == "global" and sections[-1] == "reset"
    assert page.evaluate("() => localStorage.getItem('picme.editorTab')") == "settings"

    # The tab bar stays at the top while the page scrolls
    page.mouse.wheel(0, 1500)
    _until(page, "() => window.scrollY > 300")
    assert _box(page, "#mobile-tabs")["y"] == pytest.approx(0, abs=1)

    page.reload(wait_until="networkidle")
    page.wait_for_selector("#layout-picker .layout-thumb")
    assert _tab(page, "settings").get_attribute("aria-selected") == "true"
    assert page.locator("#sidebar").is_visible() and not page.locator("#collage-preview").is_visible()

    _tab(page, "layout").tap()
    assert page.evaluate("() => localStorage.getItem('picme.editorTab')") == "layout"
    assert page.locator("#collage-preview").is_visible()
    assert w.errors == []


@SCREENS
def test_preview_fits_the_width_at_16_9_and_is_redrawn_after_the_settings_tab(write_cfg, device, screen):
    write_cfg()
    w = device(screen)
    page = w.page
    preview, area = _box(page, "#collage-preview"), _box(page, "#preview-area")
    assert area["x"] >= 0 and area["x"] + area["width"] <= screen["width"]
    assert preview["width"] >= screen["width"] - 60  # the full width, less the page and area padding
    assert preview["width"] / preview["height"] == pytest.approx(16 / 9, abs=0.02)

    # Change the wall colour on the Settings tab (the preview is redrawn while hidden), then come back:
    # the preview is drawn at its real size, so the wall border is the display's 12 px, scaled
    _tab(page, "settings").tap()
    page.evaluate("""() => { const el = document.getElementById('g-wall-color');
        el.value = '#335577'; el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true })); }""")
    _tab(page, "layout").tap()
    padding = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('collage-preview')).paddingTop)")
    assert padding == pytest.approx(12 * preview["width"] / 1920, abs=0.3)

    # A wider window (still at the breakpoint or below) grows the preview with it
    page.set_viewport_size({"width": 820, "height": screen["height"]})
    _until(page, "() => document.getElementById('collage-preview').getBoundingClientRect().width > 760")
    wide = _box(page, "#collage-preview")
    assert wide["width"] / wide["height"] == pytest.approx(16 / 9, abs=0.02)
    assert w.errors == []


@SCREENS
def test_tapping_a_slot_shows_its_settings(write_cfg, device, screen):
    write_cfg()
    w = device(screen, init_scripts=["try { localStorage.setItem('picme.editorTab', 'layout') } catch (e) {}"])
    page = w.page
    page.locator(_cell(2)).tap()
    assert "Slot 2" in page.locator("#slot-panel-title").text_content()
    assert _tab(page, "layout").get_attribute("aria-selected") == "true"  # stays on Layout
    # The slot settings are brought into view, under the tab bar
    _until(page, """() => { const r = document.getElementById('slot-panel').getBoundingClientRect();
        const tabs = document.getElementById('mobile-tabs').getBoundingClientRect();
        return r.top >= tabs.bottom - 1 && r.top < tabs.bottom + 40; }""")
    assert page.locator("#slot-fill-mode").is_visible()
    assert w.errors == []


@SCREENS
def test_a_toggle_saves(write_cfg, device, screen):
    write_cfg(fill_mode="contain")
    w = device(screen)
    page = w.page
    _tab(page, "settings").tap()
    page.locator(".toggle-row:has(#g-fill-mode) .toggle").tap()
    _saved(write_cfg, lambda c: c["fill_mode"] == "cover")
    # and a slot's own setting, from the Layout tab
    _tab(page, "layout").tap()
    page.locator(_cell(1)).tap()
    page.locator("#slot-fill-mode").select_option("contain")
    _saved(write_cfg, lambda c: c["slots"][0].get("fill_mode") == "contain")
    assert w.errors == []


def _modal_fits(page, selector, screen):
    box = _box(page, selector)
    assert box, f"{selector} is not visible"
    assert box["x"] >= 0 and box["x"] + box["width"] <= screen["width"] + 0.5, selector
    assert box["y"] >= 0 and box["y"] + box["height"] <= screen["height"] + 0.5, selector
    assert box["width"] <= 0.95 * screen["width"] + 0.5 and box["height"] <= 0.9 * screen["height"] + 0.5, selector
    # Taller content scrolls inside it, never sideways
    scroll_w, client_w = page.evaluate("sel => { const m = document.querySelector(sel); return [m.scrollWidth, m.clientWidth]; }", selector)
    assert scroll_w <= client_w, selector


@SCREENS
def test_dialogs_fit_the_screen(write_cfg, device, screen):
    write_cfg()
    w = device(screen)
    page = w.page
    _tab(page, "settings").tap()

    page.locator("#tod-configure-btn").tap()
    _modal_fits(page, ".tod-modal-overlay .modal", screen)
    page.keyboard.press("Escape")
    page.evaluate("() => document.querySelectorAll('.modal-overlay').forEach(o => o.remove())")

    page.locator("#g-source-browse").tap()
    page.wait_for_selector(".folder-browser-overlay .folder-item")
    _modal_fits(page, ".folder-browser-overlay .modal", screen)
    assert _box(page, ".folder-browser-overlay .folder-item")["height"] >= 40
    page.evaluate("() => document.querySelectorAll('.modal-overlay').forEach(o => o.remove())")

    page.locator("#reset-defaults").tap()
    page.wait_for_selector(".confirm-overlay .modal")
    _modal_fits(page, ".confirm-overlay .modal", screen)
    page.evaluate("() => document.querySelectorAll('.modal-overlay').forEach(o => o.remove())")

    page.locator("#reset-slots").tap()
    _modal_fits(page, ".modal-overlay .modal", screen)
    page.evaluate("() => document.querySelectorAll('.modal-overlay').forEach(o => o.remove())")

    page.locator("#preset-save").tap()  # the first save has no dialog; the second asks to replace
    _until(page, "() => !document.getElementById('preset-apply').disabled")
    page.locator("#preset-save").tap()
    page.wait_for_selector("#save-preset-modal .modal")
    _modal_fits(page, "#save-preset-modal .modal", screen)
    assert w.errors == []


@SCREENS
def test_touch_drag_swaps_slots_without_panning(write_cfg, device, e2e_config_path, screen):
    write_cfg(slot_overrides={1: {"group": "A"}})
    w = device(screen)
    page = w.page
    assert page.evaluate("() => window.scrollX") == 0
    for n in (1, 4):  # both slots are on screen as the page opens
        box = _box(page, _cell(n))
        assert 0 <= box["x"] and box["x"] + box["width"] <= screen["width"]
        assert 0 <= box["y"] and box["y"] + box["height"] <= screen["height"]

    Touch(page).drag(_cell(1), _cell(4), hold_ms=500)

    saved = _saved(write_cfg, lambda c: c["slots"][3].get("group") == "A")
    assert "group" not in saved["slots"][0]
    assert page.locator(".drag-ghost").count() == 0
    assert w.errors == []


@SCREENS
def test_reset_reveals_undo_on_the_settings_tab(write_cfg, device, screen):
    write_cfg(wall_color="#123456")
    w = device(screen, init_scripts=[
        "try { localStorage.setItem('picme.collapsedSections', JSON.stringify(['global'])) } catch (e) {}"])
    page = w.page
    _tab(page, "settings").tap()
    page.locator("#reset-defaults").tap()
    page.locator(".confirm-overlay .modal").get_by_role("button", name="Reset to defaults").tap()
    _saved(write_cfg, lambda c: c["wall_color"] == "#1a1a1a")
    _until(page, "() => !document.getElementById('undo-row').hidden")
    assert _tab(page, "settings").get_attribute("aria-selected") == "true"
    # Global Settings unfolds, and Undo is scrolled on screen below the tab bar
    _until(page, _IN_VIEW_BELOW_TABS_JS, arg="#undo-row")
    assert "flash" in page.locator("#undo-row").get_attribute("class")
    page.locator("#undo-btn").tap()
    page.locator(".confirm-overlay .modal").get_by_role("button", name="Undo").tap()
    _saved(write_cfg, lambda c: c["wall_color"] == "#123456")
    assert w.errors == []


# Every visible control in the given container, with its size
CONTROLS_JS = """root => [...document.querySelectorAll(root)].flatMap(r => [...r.querySelectorAll(
    'button, a.btn, select, input:not([type=checkbox]):not([type=radio]), .toggle, .layout-thumb')])
  .filter(el => el.offsetParent && getComputedStyle(el).visibility !== 'hidden')
  .map(el => { const b = el.getBoundingClientRect();
    return { what: el.id || el.textContent.trim().slice(0, 30) || el.className, w: b.width, h: b.height }; })"""


@SCREENS
def test_tap_targets_are_at_least_40px(write_cfg, device, screen):
    write_cfg(layout_rotation=ROTATING)
    w = device(screen)
    page = w.page
    page.locator(_cell(1)).tap()
    page.wait_for_selector("#slot-panel-body .panel-section")
    layout_tab = page.evaluate(CONTROLS_JS, "#mobile-tabs, #main, #footer")
    _tab(page, "settings").tap()
    settings_tab = page.evaluate(CONTROLS_JS, "#sidebar")
    controls = layout_tab + settings_tab
    assert len(controls) > 40  # sampled from both tabs
    small = [c for c in controls if c["h"] < 40 or c["w"] < 40]
    assert small == []
    # The rotation box in each thumbnail's corner keeps a 24 px target or more
    corners = page.evaluate("""() => [...document.querySelectorAll('.rotation-pick-corner')]
        .map(el => { const b = el.getBoundingClientRect(); return Math.min(b.width, b.height); })""")
    assert corners and min(corners) >= 24
    assert w.errors == []


# --- Everything else keeps working on a phone ---

def test_basic_advanced_and_folding_work_on_a_phone(write_cfg, device):
    write_cfg(slot_overrides={2: {"fill_mode": "cover"}})  # an advanced setting away from its default
    w = device(PHONE, editor_mode="basic")
    page = w.page
    _tab(page, "settings").tap()
    assert not page.locator('.sidebar-section[data-section="videos"]').is_visible()
    assert page.locator("#advanced-hint").is_visible()  # the advanced-settings count
    page.locator("#advanced-hint-show").tap()
    assert page.locator('.sidebar-section[data-section="videos"]').is_visible()
    page.locator('#editor-mode button[data-mode="basic"]').tap()
    assert not page.locator('.sidebar-section[data-section="videos"]').is_visible()

    toggle = page.locator('.sidebar-section[data-section="frame"] .section-toggle')
    toggle.tap()
    assert toggle.get_attribute("aria-expanded") == "false" and not page.locator("#g-mat-color").is_visible()
    toggle.tap()
    assert page.locator("#g-mat-color").is_visible()

    # Greyed controls stay greyed: Pill size is for the Pills style only
    page.locator(".label-style-option", has_text="Caption").first.tap()
    _until(page, "() => document.getElementById('g-pill-size').closest('.field').classList.contains('field-inactive')")
    assert w.errors == []


def test_frame_and_fill_on_single_work_on_a_phone(write_cfg, device):
    write_cfg()
    w = device(PHONE)
    page = w.page
    _tab(page, "settings").tap()
    page.locator('.layout-thumb[data-id="single"]').tap()
    _saved(write_cfg, lambda c: c["layout"] == "single")
    page.locator("#layout-fill-block .toggle").tap()
    _saved(write_cfg, lambda c: c.get("layout_fill_modes", {}).get("single") == "cover")
    page.locator("#frame-layout-block .toggle").tap()
    _saved(write_cfg, lambda c: c.get("layout_frames", {}).get("single") is True)
    _tab(page, "layout").tap()
    preview = _box(page, "#collage-preview")
    assert preview["width"] / preview["height"] == pytest.approx(16 / 9, abs=0.02)
    assert w.errors == []


# --- Desktop is unchanged ---

def test_laptop_has_no_tab_bar_and_keeps_the_sidebar_and_divider(write_cfg, device):
    write_cfg()
    w = device(LAPTOP, init_scripts=["try { localStorage.setItem('picme.editorTab', 'settings') } catch (e) {}"])
    page = w.page
    assert not page.locator("#mobile-tabs").is_visible()
    # Both the sidebar and the preview show, whatever tab a phone left stored
    assert page.locator("#sidebar").is_visible() and page.locator("#collage-preview").is_visible()
    assert _box(page, "#sidebar")["width"] == pytest.approx(280, abs=1)
    assert page.locator("#pane-divider").is_visible()
    preview = _box(page, "#collage-preview")
    assert preview["width"] / preview["height"] == pytest.approx(16 / 9, abs=0.02)
    assert preview["width"] < LAPTOP["width"] - 280
    # The page itself doesn't scroll; the slot settings scroll inside their pane
    assert page.evaluate("() => document.documentElement.scrollHeight <= window.innerHeight")
    assert w.errors == []
