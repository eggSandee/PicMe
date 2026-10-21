"""Browser end-to-end tests: editor settings that change nothing right now are greyed out, saying why
(README → Editor modes → Greyed-out settings).

Each rule: the control's field gets .field-inactive, the control is disabled, it shows at reduced
opacity with a not-allowed cursor, the reason is there, and it all flips back live when the deciding
setting changes. Reuses the gallery editor tests' server, config and page fixtures.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

from test_browser import browser, server  # noqa: E402,F401
from test_gallery_editor_browser import _opening, _saved, e2e_config_path, open_page, write_cfg  # noqa: E402,F401

# The control's state as the user sees it: its opacity is everything above it multiplied (a
# toggle's checkbox is invisible by design, so its track)
STATE_JS = """el => {
  let opacity = 1;
  const seen = el.matches('.toggle input') ? el.nextElementSibling : el;
  for (let n = seen; n && n.nodeType === 1; n = n.parentElement) opacity *= Number(getComputedStyle(n).opacity);
  const f = el.closest('.field-inactive');
  const after = f?.nextElementSibling?.classList.contains('inactive-reason') ? f.nextElementSibling : null;
  const line = f && (f.querySelector(':scope > .inactive-reason') || after);
  return { inactive: !!f, disabled: el.disabled, opacity: Math.round(opacity * 100) / 100,
           cursor: getComputedStyle(el).cursor,
           reason: line ? (line.hidden ? '' : line.textContent) : (f ? f.title : '') };
}"""

SEPARATE_ONLY = "Separate-frame layouts only."
SOLID_ONLY = "Color applies to solid frames only."
FRAME_OFF = "Show frame border is off."
GALLERY_UNUSED = "For gallery layouts: one frame and mat around every photo."


def _state(page, selector) -> dict:
    return page.locator(selector).first.evaluate(STATE_JS)


def _assert_inactive(page, selector, reason):
    s = _state(page, selector)
    assert s["inactive"] and s["disabled"], (selector, s)
    assert 0.3 <= s["opacity"] <= 0.5, (selector, s)
    assert s["cursor"] == "not-allowed", (selector, s)
    assert reason in s["reason"], (selector, s)


def _assert_active(page, selector):
    s = _state(page, selector)
    assert not s["inactive"] and not s["disabled"] and s["opacity"] == 1, (selector, s)
    assert s["cursor"] != "not-allowed", (selector, s)


def _toggle(page, input_selector):
    page.click(f"{input_selector} + .toggle-track")


def _slot_section(page, title):
    return page.locator("#slot-panel-body .panel-section").filter(
        has=page.locator(f"h4:text-is('{title}')"))


# --- Frame section ---

def test_frame_colour_is_greyed_for_a_decorative_style(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True})
    w = open_page()
    page = w.page
    _assert_active(page, "#g-frame-color")
    page.select_option("#g-frame-style", "wood")
    _assert_inactive(page, "#g-frame-color", SOLID_ONLY)
    # The reason is on screen under the field, and the label greys with the swatch
    assert page.locator(".field:has(#g-frame-color) + .inactive-reason").is_visible()
    assert page.locator(".field:has(#g-frame-color) > label").evaluate("el => getComputedStyle(el).opacity") == "0.45"
    page.select_option("#g-frame-style", "solid")
    _assert_active(page, "#g-frame-color")
    assert page.locator(".field:has(#g-frame-color) + .inactive-reason").is_hidden()
    assert w.errors == []


def test_frame_style_colour_and_width_are_greyed_with_the_border_off(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True})
    w = open_page()
    page = w.page
    _toggle(page, "#g-show-frame")
    _saved(write_cfg, lambda c: c["frame_defaults"]["show_frame"] is False)
    for sel in ("#g-frame-style", "#g-frame-color", "#g-frame-width"):
        _assert_inactive(page, sel, FRAME_OFF)
    _assert_active(page, "#g-show-frame")
    _assert_active(page, "#g-mat-color")  # the mat still shows
    _toggle(page, "#g-show-frame")
    for sel in ("#g-frame-style", "#g-frame-color", "#g-frame-width"):
        _assert_active(page, sel)
    assert w.errors == []


def test_a_time_of_day_template_that_shows_the_frame_keeps_its_settings_live(write_cfg, open_page):
    # The border is off, but the day template turns it back on (and is solid): nothing is greyed
    tod = {"enabled": True, "day_start": "07:00", "night_start": "20:00",
           "day": {"show_frame": True}, "night": {"show_frame": False}}
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": False}, time_of_day=tod)
    w = open_page()
    page = w.page
    for sel in ("#g-frame-style", "#g-frame-color", "#g-frame-width"):
        _assert_active(page, sel)
    # Time of day off: only the border setting counts
    page.click("#tod-configure-btn")
    _toggle(page, "#tod-modal-enabled")
    _saved(write_cfg, lambda c: c["time_of_day"]["enabled"] is False)
    assert not page.locator("#tod-enabled").is_checked()  # the sidebar switch follows
    page.click(".tod-modal-overlay button:text('Done')")
    _assert_inactive(page, "#g-frame-style", FRAME_OFF)
    assert w.errors == []


def test_separate_frame_settings_are_greyed_on_a_gallery_only_screen(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    for sel in ("#g-mat-color", "#g-mat-width", "#g-frame-width"):
        _assert_active(page, sel)
    page.click("#layout-mode button[data-mode='gallery']")
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"classic-4": "gallery"})
    for sel in ("#g-mat-color", "#g-mat-width", "#g-frame-width"):
        _assert_inactive(page, sel, SEPARATE_ONLY)
    _assert_active(page, "#g-frame-style")  # the gallery's outer frame still follows it
    page.click("#layout-mode button[data-mode='slots']")
    for sel in ("#g-mat-color", "#g-mat-width", "#g-frame-width"):
        _assert_active(page, sel)
    assert w.errors == []


def test_greying_leaves_a_folded_section_folded(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True})
    w = open_page()
    page = w.page
    page.click("[data-section=frame] .section-toggle")
    assert page.locator("[data-section=frame].collapsed").count() == 1
    page.click("#layout-mode button[data-mode='gallery']")  # greys the Frame section's mat and widths
    _saved(write_cfg, lambda c: c.get("layout_modes") == {"classic-4": "gallery"})
    assert page.locator("[data-section=frame].collapsed").count() == 1
    assert page.locator("#g-mat-color").is_hidden() and page.locator("#g-mat-color").is_disabled()
    assert w.errors == []


# --- Gallery Mat ---

def test_gallery_mat_is_greyed_until_a_gallery_layout_is_on_screen(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    for sel in ("#g-gal-mat-color", "#g-gal-margin", "#g-gal-bevel", "#g-gal-frame-override"):
        _assert_inactive(page, sel, GALLERY_UNUSED)
    assert page.locator("#gallery-off-hint").is_visible()
    page.click("#layout-mode button[data-mode='gallery']")
    for sel in ("#g-gal-mat-color", "#g-gal-margin", "#g-gal-bevel", "#g-gal-frame-override"):
        _assert_active(page, sel)
    assert page.locator("#gallery-off-hint").is_hidden()
    assert w.errors == []


def test_bevel_width_and_colour_are_greyed_with_the_bevel_off(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12)
    w = open_page()
    page = w.page
    _assert_active(page, "#g-gal-bevel-width")
    _toggle(page, "#g-gal-bevel")
    _saved(write_cfg, lambda c: c["gallery"]["bevel"] is False)
    _assert_inactive(page, "#g-gal-bevel-width", "Bevelled openings are off.")
    _assert_inactive(page, "#g-gal-bevel-color", "Bevelled openings are off.")
    _toggle(page, "#g-gal-bevel")
    _assert_active(page, "#g-gal-bevel-width")
    _assert_active(page, "#g-gal-bevel-color")
    assert w.errors == []


def test_the_gallerys_own_frame_greys_like_the_frame_section(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12, gallery={"frame_override": True, "frame_style": "solid"})
    w = open_page()
    page = w.page
    _assert_active(page, "#g-gal-frame-color")
    page.select_option("#g-gal-frame-style", "gold")
    _assert_inactive(page, "#g-gal-frame-color", SOLID_ONLY)
    _toggle(page, "#g-gal-show-frame")
    _saved(write_cfg, lambda c: c["gallery"]["show_frame"] is False)
    for sel in ("#g-gal-frame-style", "#g-gal-frame-color", "#g-gal-frame-width"):
        _assert_inactive(page, sel, "Show frame is off.")
    _toggle(page, "#g-gal-show-frame")
    _assert_active(page, "#g-gal-frame-style")
    _assert_active(page, "#g-gal-frame-width")
    assert w.errors == []


# --- Labels ---

def test_label_position_and_pill_size_are_for_pills(write_cfg, open_page):
    write_cfg(show_metadata=True, label_style="pill")
    w = open_page()
    page = w.page
    _assert_active(page, "#g-label-position")
    _assert_active(page, "#g-pill-size")
    page.click(".label-style-option[data-style='caption']")
    _assert_inactive(page, "#g-label-position", "Caption and placard always sit on the mat.")
    _assert_inactive(page, "#g-pill-size", "Pill size is for the Pills style.")
    page.click(".label-style-option[data-style='whisper']")
    _assert_inactive(page, "#g-label-position", "Corner whisper always sits on the photo.")
    page.click(".label-style-option[data-style='pill']")
    _assert_active(page, "#g-label-position")
    _assert_active(page, "#g-pill-size")
    assert w.errors == []


# --- Slot panel ---

def test_slot_frame_use_global_and_its_own_rules(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True})
    w = open_page()
    page = w.page
    page.click("#collage-preview .preview-slot[data-slot='1']")
    frame = _slot_section(page, "Frame")
    color = "#slot-panel-body .panel-section:has(h4:text-is('Frame')) .field:has-text('Frame color') input"
    style = "#slot-panel-body .panel-section:has(h4:text-is('Frame')) .field:has-text('Frame style') select"
    _assert_inactive(page, color, "Using the Frame section.")
    assert frame.locator(".adopt-row input").is_enabled()  # the switch that brings them back
    frame.locator(".adopt-row .toggle-track").click()
    _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["frame"] is False)
    _assert_active(page, color)
    page.select_option(style, "wood")
    _assert_inactive(page, color, SOLID_ONLY)
    page.select_option(style, "solid")
    _assert_active(page, color)
    _slot_section(page, "Frame").locator(".toggle-row:has-text('Show frame border') .toggle-track").click()
    _saved(write_cfg, lambda c: (c["slots"][0].get("frame") or {}).get("show_frame") is False)
    _assert_inactive(page, style, FRAME_OFF)
    assert w.errors == []


def test_slot_frame_is_greyed_on_a_gallery_only_screen(write_cfg, open_page):
    write_cfg(layout="gallery-12", slots=12)
    w = open_page()
    page = w.page
    page.click(_opening(1))
    adopt = "#slot-panel-body .panel-section:has(h4:text-is('Frame')) .adopt-row input"
    _assert_inactive(page, adopt, "This layout uses the Gallery Mat settings.")
    page.click("#layout-mode button[data-mode='slots']")
    page.click("#collage-preview .preview-slot[data-slot='1']")
    _assert_active(page, adopt)
    assert w.errors == []


def test_opening_shape_is_greyed_without_a_gallery_layout(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    page.click("#collage-preview .preview-slot[data-slot='1']")
    _assert_inactive(page, "#slot-shape", "Shapes show on gallery-mat layouts")
    page.click("#layout-mode button[data-mode='gallery']")
    page.click(_opening(1))
    _assert_active(page, "#slot-shape")
    assert w.errors == []


def test_slot_metadata_use_global_show_metadata_and_pill_size(write_cfg, open_page):
    write_cfg(show_metadata=False, label_style="pill",
              metadata_defaults={"show_date": True, "show_location": True})
    w = open_page()
    page = w.page
    page.click("#collage-preview .preview-slot[data-slot='1']")
    show = "#slot-panel-body .toggle-row:has-text('Show metadata') input"
    _assert_inactive(page, show, "Metadata is off for every slot")
    _toggle(page, "#g-show-metadata")
    _saved(write_cfg, lambda c: c["show_metadata"] is True)
    _assert_active(page, show)
    _assert_inactive(page, "#slot-text-size", "Using Default Metadata.")
    page.locator("#slot-meta-body .adopt-row .toggle-track").click()
    _saved(write_cfg, lambda c: c["slots"][0]["use_global"]["metadata"] is False)
    _assert_active(page, "#slot-text-size")
    _assert_active(page, "#slot-pill-size")
    page.click(".label-style-option[data-style='placard']")
    _assert_inactive(page, "#slot-pill-size", "Pill size is for the Pills style.")
    _assert_active(page, "#slot-text-size")
    assert w.errors == []


def test_schedule_is_greyed_for_a_video_slot_and_media_source_while_adopted(write_cfg, open_page):
    write_cfg(slot_overrides={1: {"use_global": {"source": True, "frame": True, "metadata": True}}})
    w = open_page()
    page = w.page
    page.click("#collage-preview .preview-slot[data-slot='1']")
    folder = "#slot-panel-body .panel-section:has(h4:text-is('Media Source')) input[type=text]"
    _assert_inactive(page, folder, "Using the default media folder")
    cycle = "#slot-panel-body .field:has-text('Change every') input"
    _assert_active(page, cycle)
    _slot_section(page, "Mode").locator("select").select_option("video")
    _assert_inactive(page, cycle, "Video slots advance after their loops finish.")
    _slot_section(page, "Mode").locator("select").select_option("photos")
    _assert_active(page, cycle)
    assert w.errors == []


def test_basic_keeps_the_slot_folder_editable_and_greys_what_it_shows(write_cfg, open_page):
    write_cfg(show_metadata=True, label_style="caption",
              slot_overrides={1: {"use_global": {"source": True, "frame": True, "metadata": True}}})
    w = open_page(editor_mode="basic")
    page = w.page
    _assert_inactive(page, "#g-label-position", "Caption and placard always sit on the mat.")
    hint_before = page.locator("#advanced-hint-text").text_content()
    page.click("#collage-preview .preview-slot[data-slot='1']")
    _assert_active(page, "#slot-panel-body .panel-section:has(h4:text-is('Media Source')) input[type=text]")
    # Greying is a view: the advanced-settings count only follows config
    page.select_option("#g-frame-style", "wood")
    _saved(write_cfg, lambda c: c.get("frame_defaults", {}).get("frame_style") == "wood")
    assert page.locator("#advanced-hint-text").text_content() == hint_before
    # The frame colour is Advanced: its reason hides with it
    assert page.locator(".field:has(#g-frame-color) + .inactive-reason").is_hidden()
    assert w.errors == []


# --- Rotation ---

def test_rotation_switch_time_and_picks_are_greyed_while_off(write_cfg, open_page):
    write_cfg()
    w = open_page()
    page = w.page
    _assert_inactive(page, "#g-rotation-change-at", "Turn on Rotate layout daily to use these.")
    _assert_inactive(page, ".rotation-pick", "Turn on Rotate layout daily to pick layouts.")
    _toggle(page, "#g-rotation-enabled")
    _saved(write_cfg, lambda c: (c.get("layout_rotation") or {}).get("enabled") is True)
    _assert_active(page, "#g-rotation-change-at")
    _assert_active(page, ".rotation-pick")
    assert page.locator(".rotation-pick-corner").first.get_attribute("title").startswith("Rotate ")
    assert w.errors == []


# --- Time of day ---

TOD = {"enabled": False, "day_start": "07:00", "night_start": "20:00",
       "day": {"wall_color": "#f5f0e8", "show_frame": True, "frame_width": 8},
       "night": {"wall_color": "#000000", "show_frame": True, "frame_width": 8}}


def test_time_of_day_templates_are_greyed_while_off(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True}, time_of_day=TOD)
    w = open_page()
    page = w.page
    page.click("#tod-configure-btn")
    day = ".tod-modal-overlay [data-period='day']"
    _assert_inactive(page, f"{day} .field:has-text('Frame width') input", "Time of day is off.")
    _assert_inactive(page, ".tod-modal-overlay input[type=time]", "Time of day is off.")
    _toggle(page, "#tod-modal-enabled")
    _saved(write_cfg, lambda c: c["time_of_day"]["enabled"] is True)
    assert page.locator("#tod-enabled").is_checked()
    _assert_active(page, f"{day} .field:has-text('Frame width') input")
    _assert_active(page, ".tod-modal-overlay input[type=time]")
    assert w.errors == []


def test_time_of_day_template_frame_rules(write_cfg, open_page):
    write_cfg(frame_defaults={"frame_style": "solid", "frame_width": 8, "show_frame": True},
              time_of_day={**TOD, "enabled": True})
    w = open_page()
    page = w.page
    page.click("#tod-configure-btn")
    day = ".tod-modal-overlay [data-period='day']"
    picker = f"{day} [data-tod-key='frame_color'] input[type=color]"
    same = f"{day} [data-tod-key='frame_color'] .tod-same"
    # Same as default ticked: the picker alone is greyed (the box is the reason); unticked, it works
    _assert_inactive(page, picker, "Same as default is ticked")
    _assert_active(page, same)
    page.locator(same).uncheck()
    _assert_active(page, picker)
    # A decorative style for this period: the whole colour field, box included
    page.locator(f"{day} .field:has-text('Frame style') select").select_option("gold")
    _assert_inactive(page, picker, SOLID_ONLY)
    _assert_inactive(page, same, SOLID_ONLY)
    page.locator(f"{day} .field:has-text('Frame style') select").select_option("solid")
    _assert_active(page, picker)
    # This period's frame off: style, colour and width
    page.locator(f"{day} .toggle-row:has-text('Show frame') .toggle-track").click()
    _saved(write_cfg, lambda c: c["time_of_day"]["day"]["show_frame"] is False)
    for sel in (f"{day} .field:has-text('Frame style') select", picker, f"{day} .field:has-text('Frame width') input"):
        _assert_inactive(page, sel, "No frame shows in this period")
    _assert_active(page, ".tod-modal-overlay [data-period='night'] .field:has-text('Frame width') input")
    assert w.errors == []


def test_a_time_of_day_note_stays_usable_under_a_greyed_colour(write_cfg, open_page):
    # Wood all day, and both periods set a frame colour: the colour field is greyed, and the
    # time-of-day note under it keeps its links
    tod = {**TOD, "enabled": True, "day": {**TOD["day"], "frame_color": "#112233"},
           "night": {**TOD["night"], "frame_color": "#112233"}}
    write_cfg(frame_defaults={"frame_style": "wood", "frame_width": 28, "show_frame": True}, time_of_day=tod)
    w = open_page()
    page = w.page
    _assert_inactive(page, "#g-frame-color", SOLID_ONLY)
    note = page.locator(".field:has(#g-frame-color) > .tod-note")
    assert note.is_visible()
    assert note.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert note.locator(".tod-note-mine").is_enabled() and note.locator(".tod-note-edit").is_enabled()
    note.locator(".tod-note-mine").click()  # Use my colour: Same as default for the current period
    _saved(write_cfg, lambda c: any("frame_color" not in c["time_of_day"][p] for p in ("day", "night")))
    assert w.errors == []
