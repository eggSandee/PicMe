"""Browser end-to-end tests for the photo detail view (README → Photo details).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides ``e2e_config_path``.
Photos carry date and camera EXIF but no GPS (nothing reaches out for reverse geocoding); a test
that needs every field, location included, serves it through a Playwright route on /api/metadata.
The video test needs ffmpeg and a browser that plays H.264 (Chrome), like test_video_browser.py.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from conftest import _exif_bytes, _make_image, make_config
from video_fixtures import FFMPEG, make_clip

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401

PHOTO_COUNT = 12
COLORS = [(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40), (120, 40, 160), (220, 220, 220),
          (90, 160, 160), (160, 90, 60), (60, 60, 140), (200, 140, 180), (100, 180, 60), (240, 200, 120)]
FIELDS_OFF = {"show_date": False, "show_location": False, "show_camera": False, "show_megapixels": False,
              "show_fstop": False, "show_shutter_speed": False, "show_iso": False, "show_focal_length": False}
STUB_FIELDS = {"date_iso": "2019-07-04", "location": "Golden Gate Park, San Francisco", "camera": "Canon EOS R5",
               "megapixels": "45.0 MP", "fstop": "f/2.8", "shutter_speed": "1/250s", "iso": "ISO 200",
               "focal_length": "50mm"}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-detail")
    (root / "photos").mkdir()
    for i, color in enumerate(COLORS[:PHOTO_COUNT]):
        size = (800, 1200) if i % 4 == 3 else (1200, 800)
        _make_image(root / "photos" / f"photo_{i}.jpg", size=size, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True, camera=True))
    if FFMPEG is not None:
        (root / "videos").mkdir()
        for i, color in enumerate(["red", "green", "blue"]):
            make_clip(root / "videos" / f"clip_{i}.mp4", color=color, faststart=True,
                      creation_time="2021-06-15T12:00:00Z")
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a config (photos don't cycle unless asked) and restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(layout="classic-4", slots=4, cycle_seconds=300, slot_overrides=None, **extra) -> dict:
        cfg = make_config(root / "photos", layout=layout, slots=slots, cycle_seconds=cycle_seconds)
        cfg["metadata_defaults"] = {**FIELDS_OFF, "show_date": True}
        cfg["frame_defaults"] = {"mat_color": "#000000", "mat_width": 16, "show_frame": True,
                                 "frame_color": "#2c2318", "frame_width": 8, "frame_style": "solid"}
        for slot_id, override in (slot_overrides or {}).items():
            cfg["slots"][slot_id - 1].update(override)
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.root = root
    yield _write
    e2e_config_path.write_text(original)


ALL_SHOWN_JS = """() => { const slots = [...document.querySelectorAll('#collage .slot')];
  return slots.length > 0 && slots.every(s => s.classList.contains('video-mode')
    ? !!s.querySelector('video.visible[src]')
    : (img => img && img.complete && img.naturalWidth > 0)(s.querySelector('img.visible'))); }"""


@pytest.fixture
def open_display(browser, server):
    contexts = []

    def _open(viewport=VIEWPORT, routes=(), clock=False) -> PageWatcher:
        ctx = browser.new_context(viewport=viewport, device_scale_factor=1, locale="en-US")
        contexts.append(ctx)
        for pattern, handler in routes:
            ctx.route(pattern, handler)
        page = ctx.new_page()
        if clock:
            page.clock.install()
        watcher = PageWatcher(page)
        page.goto(server + "/display/", wait_until="networkidle")
        page.wait_for_function(ALL_SHOWN_JS, timeout=15_000)
        page.add_style_tag(content="#editor-btn { display: none !important; }")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


DETAIL_JS = """() => {
  const root = document.getElementById('detail');
  const media = root.querySelector('.detail-media > *');
  const box = el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  return {
    open: !root.hidden && getComputedStyle(root).display !== 'none',
    slot: detail ? detail.slotCfg.id : null,
    tag: media ? media.tagName : null,
    src: media ? media.getAttribute('src') : null,
    natural: media ? [media.naturalWidth || media.videoWidth, media.naturalHeight || media.videoHeight] : null,
    box: media ? box(media) : null,
    fit: media ? getComputedStyle(media).objectFit : null,
    clip: media ? getComputedStyle(media).clipPath : null,
    rows: [...root.querySelectorAll('.detail-panel dl > div')].map(d => ({
      key: d.dataset.field, label: d.querySelector('dt').textContent, text: d.querySelector('dd').textContent })),
    file: root.querySelector('.detail-file').textContent,
    note: root.querySelector('.detail-note').textContent,
    focus: document.activeElement && (document.activeElement.id || document.activeElement.className),
  };
}"""


def _detail(page) -> dict:
    return page.evaluate(DETAIL_JS)


def _wait_open(page, open_=True):
    page.wait_for_function("o => !document.getElementById('detail').hidden === o", arg=open_, timeout=5_000)


def _slot_src(page, slot_id) -> str:
    return page.evaluate("id => document.querySelector(`#slot-${id} img.visible`).getAttribute('src')", slot_id)


def _current_paths(page) -> dict:
    paths = page.evaluate("() => activeSlots.map(s => [s.id, slotState[s.id].currentPath])")
    return {slot_id: path for slot_id, path in paths}


def _rows_loaded(page):
    page.wait_for_function("() => document.querySelector('#detail .detail-note').textContent === '' "
                           "&& document.querySelectorAll('#detail .detail-panel dl > div').length > 0")


def test_click_opens_the_whole_photo_with_every_field_it_has(write_cfg, open_display):
    # Pills off everywhere: the detail view still shows every field PicMe reads
    write_cfg(show_metadata=False, metadata_defaults=FIELDS_OFF, fill_mode="cover")
    w = open_display()
    page = w.page
    assert _detail(page)["open"] is False
    page.click("#slot-1 .mat-frame")
    _wait_open(page)
    _rows_loaded(page)
    d = _detail(page)
    path = page.evaluate("slotState[1].currentPath")
    assert d["slot"] == 1 and d["tag"] == "IMG"
    assert d["src"] == _slot_src(page, 1)
    # Whole and unclipped, whatever the slot's Fill crop: the box has the photo's own shape
    assert d["fit"] == "contain" and d["clip"] == "none"
    assert abs(d["box"]["w"] / d["box"]["h"] - d["natural"][0] / d["natural"][1]) < 0.01
    assert d["box"]["w"] > 0.5 * VIEWPORT["width"] or d["box"]["h"] > 0.8 * VIEWPORT["height"]
    # Exactly the fields the server has for this file, in panel order; no GPS, so no Location
    assert [(r["label"], r["text"]) for r in d["rows"]] == [
        ("Date", "July 4, 2019"), ("Camera", "Canon EOS R5"),
        ("Megapixels", "1.0 MP"), ("Aperture", "f/2.8"), ("Shutter speed", "1/250s"),
        ("ISO", "ISO 200"), ("Focal length", "50mm")]
    assert d["file"] == os.path.basename(path)
    assert w.errors == []


def test_panel_labels_every_field_and_follows_date_format(write_cfg, open_display):
    write_cfg(date_format="iso")

    def stub(route):
        route.fulfill(json=STUB_FIELDS)

    page = open_display(routes=[("**/api/metadata?*", stub)]).page
    page.click("#slot-2 .mat-frame")
    _wait_open(page)
    _rows_loaded(page)
    assert [(r["key"], r["label"], r["text"]) for r in _detail(page)["rows"]] == [
        ("date", "Date", "2019-07-04"), ("location", "Location", "Golden Gate Park, San Francisco"),
        ("camera", "Camera", "Canon EOS R5"), ("megapixels", "Megapixels", "45.0 MP"),
        ("fstop", "Aperture", "f/2.8"), ("shutter_speed", "Shutter speed", "1/250s"),
        ("iso", "ISO", "ISO 200"), ("focal_length", "Focal length", "50mm")]


def test_a_failed_metadata_fetch_shows_just_the_file_name(write_cfg, open_display):
    write_cfg(show_metadata=False)
    page = open_display(routes=[("**/api/metadata?*", lambda route: route.fulfill(status=500))]).page
    page.click("#slot-1 .mat-frame")
    _wait_open(page)
    page.wait_for_function("() => document.querySelector('#detail .detail-note').textContent === ''")
    d = _detail(page)
    assert d["rows"] == [] and d["file"] == os.path.basename(page.evaluate("slotState[1].currentPath"))


def test_close_by_backdrop_button_and_escape_but_not_the_card(write_cfg, open_display):
    write_cfg()
    w = open_display()
    page = w.page
    page.click("#slot-1 .mat-frame")
    _wait_open(page)
    # Clicks on the photo and on the panel keep it open
    page.click("#detail .detail-media > img")
    page.click("#detail .detail-panel")
    assert _detail(page)["open"]
    # The backdrop (the screen's corner) closes it
    page.mouse.click(5, VIEWPORT["height"] - 5)
    _wait_open(page, False)
    assert page.evaluate("document.querySelector('#detail .detail-media').children.length") == 0

    page.click("#slot-3 .mat-frame")
    _wait_open(page)
    assert _detail(page)["slot"] == 3
    page.click("#detail .detail-close")
    _wait_open(page, False)

    page.click("#slot-4 .mat-frame")
    _wait_open(page)
    page.keyboard.press("Escape")
    _wait_open(page, False)
    assert page.evaluate("detail") is None
    assert w.errors == []


def test_closes_itself_after_45_seconds_without_input(write_cfg, open_display):
    write_cfg()
    page = open_display(clock=True).page
    page.click("#slot-1 .mat-frame")
    _wait_open(page)
    page.clock.fast_forward(44_000)
    assert _detail(page)["open"]
    page.clock.fast_forward(2_000)
    _wait_open(page, False)

    # Any input starts the countdown again
    page.click("#slot-2 .mat-frame")
    _wait_open(page)
    page.clock.fast_forward(30_000)
    page.mouse.move(400, 300)
    page.mouse.move(420, 320)
    page.clock.fast_forward(30_000)
    assert _detail(page)["open"]
    page.keyboard.press("Shift")
    page.clock.fast_forward(40_000)
    assert _detail(page)["open"]
    page.clock.fast_forward(6_000)
    _wait_open(page, False)


def _focused(page):
    return page.evaluate("document.activeElement && document.activeElement.id")


def test_arrow_keys_move_between_slots_and_enter_opens(write_cfg, open_display):
    write_cfg()
    w = open_display()
    page = w.page
    assert page.evaluate("[...document.querySelectorAll('#collage .slot')].every(s => s.tabIndex === 0)")
    # Nothing focused: the first arrow picks the top-left slot (classic-4: 1 2 / 3 4)
    page.keyboard.press("ArrowRight")
    assert _focused(page) == "slot-1"
    for key, expected in [("ArrowRight", "slot-2"), ("ArrowRight", "slot-2"), ("ArrowDown", "slot-4"),
                          ("ArrowLeft", "slot-3"), ("ArrowUp", "slot-1"), ("ArrowDown", "slot-3")]:
        page.keyboard.press(key)
        assert _focused(page) == expected, key
    # The ring shows on the keyboard-focused slot
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"

    page.keyboard.press("Enter")
    _wait_open(page)
    d = _detail(page)
    assert d["slot"] == 3 and d["focus"] == "detail-close"
    page.keyboard.press("Tab")  # trapped on the close button
    assert _detail(page)["focus"] == "detail-close"
    page.keyboard.press("Escape")
    _wait_open(page, False)
    assert _focused(page) == "slot-3"

    # TV remote Back keys, and Enter on the close button
    page.keyboard.press("Enter")
    _wait_open(page)
    page.keyboard.press("Backspace")
    _wait_open(page, False)
    page.keyboard.press("Enter")
    _wait_open(page)
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { keyCode: 461, bubbles: true }))")
    _wait_open(page, False)
    page.keyboard.press("Enter")
    _wait_open(page)
    page.keyboard.press("Enter")
    _wait_open(page, False)
    assert _focused(page) == "slot-3"
    assert page.url.endswith("/display/")
    assert w.errors == []


def test_cycling_pauses_for_the_slot_and_its_group_then_resumes(write_cfg, open_display):
    write_cfg(cycle_seconds=2, slot_overrides={1: {"group": "A"}, 2: {"group": "A"}})
    w = open_display()
    page = w.page
    # Every 50 ms: two slots never show the same photo
    page.evaluate("""() => { window.__dupes = [];
      setInterval(() => {
        const paths = activeSlots.map(s => slotState[s.id].currentPath).filter(Boolean);
        if (new Set(paths).size !== paths.length) window.__dupes.push(paths);
      }, 50); }""")
    page.click("#slot-1 .mat-frame")
    _wait_open(page)
    held = _current_paths(page)
    seen3 = {held[3]}
    deadline = time.monotonic() + 5.5
    while time.monotonic() < deadline:
        page.wait_for_timeout(250)
        now = _current_paths(page)
        assert now[1] == held[1] and now[2] == held[2], "a held slot cycled"
        seen3.add(now[3])
    assert len(seen3) >= 2, "an ungrouped slot stopped cycling"
    assert _detail(page)["src"] == page.evaluate("id => imageUrl(slotState[id].currentPath)", 1)

    page.keyboard.press("Escape")
    _wait_open(page, False)
    page.wait_for_function("p => slotState[1].currentPath !== p[1] && slotState[2].currentPath !== p[2]",
                           arg={"1": held[1], "2": held[2]}, timeout=4_000)
    assert page.evaluate("window.__dupes") == []
    assert w.errors == []


def test_gallery_heart_opens_unclipped_and_the_mat_opens_nothing(write_cfg, open_display):
    write_cfg(layout="gallery-salon", slots=8)
    page = open_display().page
    assert page.evaluate("document.getElementById('slot-2').dataset.shape") == "heart"
    assert page.evaluate("getComputedStyle(document.querySelector('#slot-2 .mat-frame')).clipPath") != "none"

    # The heart box's corner is outside the heart: mat, not photo
    corner = page.evaluate("""() => { const r = document.querySelector('#slot-2 .opening-box').getBoundingClientRect();
        return [r.x + 4, r.y + 4]; }""")
    page.mouse.click(*corner)
    mat = page.evaluate("""() => { const r = document.querySelector('.gallery-mat').getBoundingClientRect();
        return [r.x + 3, r.y + 3]; }""")
    page.mouse.click(*mat)
    page.wait_for_timeout(300)
    assert _detail(page)["open"] is False

    page.click("#slot-2 .mat-frame")
    _wait_open(page)
    d = _detail(page)
    assert d["slot"] == 2 and d["clip"] == "none" and d["fit"] == "contain"
    assert abs(d["box"]["w"] / d["box"]["h"] - d["natural"][0] / d["natural"][1]) < 0.01


def test_setting_off_ignores_clicks_and_keys_and_turns_on_live(write_cfg, open_display):
    cfg = write_cfg(tap_for_details=False)
    w = open_display()
    page = w.page
    assert page.evaluate("[...document.querySelectorAll('#collage .slot')].every(s => !s.hasAttribute('tabindex'))")
    assert page.evaluate("document.getElementById('collage').dataset.details") is None
    page.click("#slot-1 .mat-frame")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert _detail(page)["open"] is False
    assert _focused(page) in ("", None)

    # The editor turns it on: the display picks it up without a reload
    page.evaluate("window.__noReload = true")
    cfg["tap_for_details"] = True
    write_cfg.root.joinpath("config.json").write_text(json.dumps(cfg, indent=2))
    page.wait_for_function("document.getElementById('collage').dataset.details === 'on'", timeout=8_000)
    assert page.evaluate("window.__noReload") is True
    page.click("#slot-1 .mat-frame")
    _wait_open(page)

    # Off again while open: it closes
    cfg["tap_for_details"] = False
    write_cfg.root.joinpath("config.json").write_text(json.dumps(cfg, indent=2))
    _wait_open(page, False)
    assert page.evaluate("window.__noReload") is True
    assert w.errors == []


@pytest.fixture
def h264_browser(browser):
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    page = browser.new_page()
    try:
        support = page.evaluate("document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')")
    finally:
        page.close()
    if not support:
        pytest.skip("browser cannot play H.264 — needs Chrome (channel='chrome')")
    return browser


def test_video_keeps_playing_in_the_view_then_goes_back(h264_browser, write_cfg, open_display):
    # 1 s clips, one loop each: closed, the slot would move on every second
    write_cfg(slot_overrides={2: {"mode": "video", "source_path": str(write_cfg.root / "videos"), "video_loops": 1,
                                  "use_global": {"source": False}}})
    w = open_display()
    page = w.page
    page.wait_for_function("() => { const v = videoEls(activeSlots[1]).current; "
                           "return !!v.getAttribute('src') && !v.paused && v.currentTime > 0; }")
    page.evaluate("videoEls(activeSlots[1]).current.dataset.mark = 'playing'")
    page.wait_for_function("() => !slotState[2].videoLoading")
    page.click("#slot-2 .mat-frame")
    _wait_open(page)
    d = _detail(page)
    assert d["tag"] == "VIDEO" and d["fit"] == "contain" and d["clip"] == "none"
    # The slot's own element, moved: same decoder, still playing
    assert page.evaluate("document.querySelector('#detail video').dataset.mark") == "playing"
    assert page.evaluate("document.querySelectorAll('#slot-2 video').length") == 1
    src = d["src"]
    t0 = page.evaluate("document.querySelector('#detail video').currentTime")
    page.evaluate("document.querySelector('#detail video').addEventListener('ended', () => window.__loops = (window.__loops || 0) + 1)")
    page.wait_for_timeout(3_000)
    v = page.evaluate("(() => { const v = document.querySelector('#detail video'); "
                      "return { paused: v.paused, src: v.getAttribute('src'), loops: window.__loops || 0 }; })()")
    assert v["paused"] is False and v["src"] == src, v
    assert v["loops"] >= 2, "it should loop past video_loops while open"
    assert page.evaluate("slotState[2].currentPath") == page.evaluate("detail.path")
    assert t0 >= 0
    # Date and file name: video metadata has nothing else
    _rows_loaded(page)
    assert [r["label"] for r in _detail(page)["rows"]] == ["Date"]

    page.keyboard.press("Escape")
    _wait_open(page, False)
    back = page.evaluate("(() => { const v = document.querySelector('#slot-2 .mat-frame video[data-mark]'); "
                         "return v && { paused: v.paused, fit: v.style.objectFit, w: v.style.width }; })()")
    assert back is not None and back["paused"] is False and back["w"] == "", back
    assert back["fit"] == "contain" or back["fit"] == "cover"
    # Cycling resumes: the slot moves on to another clip
    page.wait_for_function("s => videoEls(activeSlots[1]).current.getAttribute('src') !== s", arg=src, timeout=8_000)
    assert w.errors == []
