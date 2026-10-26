"""Browser end-to-end tests for screen sizes (README → Screen sizes).

Every fixed px the display draws is px at the 1920x1080 reference, times one scale factor
s = clamp(min(w / 1920, h / 1080), 0.3, 2) from the display's viewport (frontend/display/scale.js).
These open the real display at several viewports (device scale 1) and check the computed
frame, mat, collage, gallery and label sizes against the reference values times s.
"""
from __future__ import annotations

import json

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import PageWatcher, browser, server  # noqa: E402,F401
from test_frames_browser import MEDIA_READY_JS, _until  # noqa: E402

# (width, height) -> s
VIEWPORTS = {
    (1920, 1080): 1.0,
    (1280, 720): 1280 / 1920,
    (1366, 768): 768 / 1080,
    (3840, 2160): 2.0,
    (1024, 768): 1024 / 1920,   # tablet, landscape
    (768, 1024): 768 / 1920,    # tablet, portrait: the width decides
}
VIEWPORT_IDS = [f"{w}x{h}" for w, h in VIEWPORTS]
FRAME = {"mat_color": "#000000", "mat_width": 30, "show_frame": True, "frame_color": "#2c2318",
         "frame_width": 20, "frame_style": "solid"}
GALLERY = {"mat_color": "#00ffff", "outer_margin": 64, "gutter": 28, "wall_margin": 12, "bevel": True,
           "bevel_width": 4, "bevel_color": "#ffffff", "show_frame": True, "frame_style": "solid",
           "frame_color": "#00ff00", "frame_width": 40}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-screen-sizes")
    (root / "photos").mkdir()
    for i in range(6):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG",
                    color=(40 + 30 * i, 80, 120), exif=_exif_bytes(date=True, camera=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a classic-4 config (photos never cycle, no pills) and restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(frame=None, layout="classic-4", slots=4, slot_frames=None, **extra) -> dict:
        cfg = make_config(root / "photos", layout=layout, slots=slots, cycle_seconds=300)
        cfg["show_metadata"] = False
        cfg["frame_defaults"] = {**FRAME, **(frame or {})}
        for slot_id, slot_frame in (slot_frames or {}).items():
            cfg["slots"][slot_id - 1]["frame"] = slot_frame
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_page(browser, server):
    contexts = []

    def _open(viewport, path="/display/") -> PageWatcher:
        w, h = viewport
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, timezone_id="UTC")
        ctx.add_init_script(editor_mode_init_js("advanced"))
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.goto(server + path, wait_until="networkidle")
        if path.startswith("/display"):
            _until(page, MEDIA_READY_JS)
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


GEOMETRY_JS = """() => {
  const collage = getComputedStyle(document.getElementById('collage'));
  const slots = [...document.querySelectorAll('.slot')].map(slot => {
    const mf = slot.querySelector('.mat-frame');
    const cs = getComputedStyle(mf);
    const r = slot.getBoundingClientRect();
    const media = [...slot.querySelectorAll('.mat-frame img.visible')].map(m => m.getBoundingClientRect())[0];
    return {
      id: slot.id, border: parseFloat(cs.borderTopWidth), borders: [cs.borderTopWidth, cs.borderRightWidth,
        cs.borderBottomWidth, cs.borderLeftWidth].map(parseFloat),
      mat: parseFloat(cs.paddingTop), inset: parseFloat(getComputedStyle(slot).getPropertyValue('--pill-inset')),
      short: Math.min(slot.clientWidth, slot.clientHeight),
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      media: media && { x: media.x, y: media.y, w: media.width, h: media.height },
    };
  });
  return {
    s: PicMeScale.factor(),
    padding: parseFloat(collage.paddingTop), gap: parseFloat(collage.rowGap),
    scroll: { w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight },
    view: { w: innerWidth, h: innerHeight }, slots,
  };
}"""


def geometry(page) -> dict:
    return page.evaluate(GEOMETRY_JS)


def inside(inner, outer, tol=0.5) -> bool:
    return (inner["x"] >= outer["x"] - tol and inner["y"] >= outer["y"] - tol
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + tol
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + tol)


def assert_no_overflow(g):
    assert g["scroll"]["w"] <= g["view"]["w"] and g["scroll"]["h"] <= g["view"]["h"], g["scroll"]
    view = {"x": 0, "y": 0, "w": g["view"]["w"], "h": g["view"]["h"]}
    for slot in g["slots"]:
        assert inside(slot["rect"], view), slot
        assert slot["media"] and inside(slot["media"], slot["rect"]), slot


# ── The factor ──

def test_scale_factor_math(write_cfg, open_page):
    write_cfg()
    page = open_page((1920, 1080)).page
    got = page.evaluate("""() => ({
      ref: PicMeScale.REFERENCE, min: PicMeScale.MIN, max: PicMeScale.MAX,
      cases: [[1920, 1080], [1280, 720], [3840, 2160], [768, 1024], [320, 240], [7680, 4320], [0, 1080], [NaN, 5]]
        .map(([w, h]) => PicMeScale.compute(w, h)),
    })""")
    assert got["ref"] == {"width": 1920, "height": 1080}
    assert (got["min"], got["max"]) == (0.3, 2)
    assert got["cases"] == pytest.approx([1, 1280 / 1920, 2, 0.4, 0.3, 2, 1, 1])


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=VIEWPORT_IDS)
def test_display_publishes_its_scale(write_cfg, open_page, viewport):
    write_cfg()
    g = geometry(open_page(viewport).page)
    s = VIEWPORTS[viewport]
    assert g["s"] == pytest.approx(s)


# ── Separate frames ──

@pytest.mark.parametrize("viewport", VIEWPORTS, ids=VIEWPORT_IDS)
def test_solid_frame_mat_and_collage_scale(write_cfg, open_page, viewport):
    write_cfg()
    g = geometry(open_page(viewport).page)
    s = VIEWPORTS[viewport]
    # Whole px, so slot edges (and their borders) stay on the pixel grid
    assert g["padding"] == g["gap"] == round(12 * s), g
    for slot in g["slots"]:
        assert all(abs(b - 20 * s) <= 1 for b in slot["borders"]), slot
        assert abs(slot["mat"] - 30 * s) <= 1, slot
        assert abs(slot["inset"] - 10 * s) <= 1, slot
    assert_no_overflow(g)


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=VIEWPORT_IDS)
def test_decorative_frame_scales_and_keeps_the_small_slot_cap(write_cfg, open_page, viewport):
    write_cfg(frame={"frame_style": "wood", "frame_width": 28})
    g = geometry(open_page(viewport).page)
    s = VIEWPORTS[viewport]
    for slot in g["slots"]:
        cap = max(4, slot["short"] // 6)
        assert slot["border"] == min(round(28 * s), cap), slot
        assert slot["border"] <= max(4, slot["short"] / 6)
        # Pills stay inside the moulding
        assert abs(slot["inset"] - (slot["border"] + 10 * s)) <= 1, slot
    assert_no_overflow(g)


def test_small_slot_cap_still_limits_a_scaled_frame(write_cfg, open_page):
    """A 160 px moulding at s = 2 would be 320 px: the sixth-of-the-slot cap wins."""
    write_cfg(frame={"frame_style": "wood", "frame_width": 160, "mat_width": 0}, layout="classic-6", slots=6)
    g = geometry(open_page((3840, 2160)).page)
    for slot in g["slots"]:
        assert slot["border"] == max(4, slot["short"] // 6) < 320, slot
    assert_no_overflow(g)


def test_time_of_day_and_slot_widths_scale_too(write_cfg, open_page):
    """Widths from a time-of-day template and a slot override are reference px like the global ones."""
    template = {"frame_width": 14, "mat_width": 40}
    write_cfg(time_of_day={"enabled": True, "day_start": "07:00", "night_start": "20:00",
                           "day": template, "night": template},
              slot_frames={2: {"frame_width": 6, "mat_width": 50}})
    s = 1280 / 1920
    for slot in geometry(open_page((1280, 720)).page)["slots"]:
        want = (6, 50) if slot["id"] == "slot-2" else (14, 40)
        assert (slot["border"], slot["mat"]) == (round(want[0] * s), round(want[1] * s)), slot


# ── Gallery mat ──

GALLERY_JS = """() => {
  const root = document.getElementById('collage');
  const frame = root.querySelector('.gallery-frame'), mat = root.querySelector('.gallery-mat');
  const fs = getComputedStyle(frame), ms = getComputedStyle(mat);
  const openings = [...root.querySelectorAll('.opening')].map(o => {
    const r = o.getBoundingClientRect(), mf = o.querySelector('.mat-frame');
    return { rect: { x: r.x, y: r.y, w: r.width, h: r.height }, bevel: parseFloat(mf.style.left),
             inset: parseFloat(getComputedStyle(o).getPropertyValue('--pill-inset')) };
  });
  return { wall: parseFloat(getComputedStyle(root).paddingTop), frame: parseFloat(fs.borderTopWidth),
           margin: parseFloat(ms.paddingTop), gutter: parseFloat(ms.rowGap),
           short: Math.min(root.clientWidth, root.clientHeight),
           frameRect: (r => ({ x: r.x, y: r.y, w: r.width, h: r.height }))(frame.getBoundingClientRect()),
           view: { w: innerWidth, h: innerHeight }, openings };
}"""


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=VIEWPORT_IDS)
def test_gallery_margins_frame_and_bevel_scale(write_cfg, open_page, viewport):
    write_cfg(layout_modes={"classic-4": "gallery"}, gallery=GALLERY)
    page = open_page(viewport).page
    g = page.evaluate(GALLERY_JS)
    s = VIEWPORTS[viewport]
    assert g["wall"] == round(12 * s), g
    assert g["frame"] == round(40 * s), g
    assert g["margin"] == round(64 * s) and g["gutter"] == round(28 * s), g
    view = {"x": 0, "y": 0, "w": g["view"]["w"], "h": g["view"]["h"]}
    assert inside(g["frameRect"], view)
    for o in g["openings"]:
        assert o["bevel"] == round(4 * s), o
        assert o["inset"] == round(10 * s), o
        assert inside(o["rect"], g["frameRect"]), o


def test_gallery_editor_fields_stay_in_reference_px(write_cfg, open_page):
    """The editor reads the configured values, not the screen's: resolveGallery without a scale."""
    write_cfg(layout_modes={"classic-4": "gallery"}, gallery=GALLERY)
    page = open_page((1280, 720)).page
    got = page.evaluate("() => [PicMeGallery.resolveGallery(config, null), PicMeGallery.resolveGallery(config, null, 0.5)]")
    assert {k: got[0][k] for k in ("outer_margin", "gutter", "wall_margin", "bevel_width", "frame_width")} == \
        {"outer_margin": 64, "gutter": 28, "wall_margin": 12, "bevel_width": 4, "frame_width": 40}
    assert {k: got[1][k] for k in ("outer_margin", "gutter", "wall_margin", "bevel_width", "frame_width")} == \
        {"outer_margin": 32, "gutter": 14, "wall_margin": 6, "bevel_width": 2, "frame_width": 20}


# ── Labels ──

PILLS_READY_JS = """() => { const slots = [...document.querySelectorAll('.slot')];
  return slots.length > 0 && slots.every(s => s.querySelector('.overlay-pill')); }"""
LABELS_READY_JS = """() => { const slots = [...document.querySelectorAll('.slot')];
  return slots.length > 0 && slots.every(s => s.querySelector('.slot-overlay .lbl')); }"""


def pill_fonts(page) -> list[float]:
    return page.evaluate("""() => [...document.querySelectorAll('.overlay-pill')]
      .filter(p => !p.classList.contains('fit-hidden'))
      .map(p => parseFloat(getComputedStyle(p).fontSize))""")


def test_pill_text_scales_like_the_rest(write_cfg, open_page):
    """Pills size from the slot, so with the floors and ceilings scaled too, a 16:9 screen at s
    shows the 1080p pill font times s; any screen stays at or over the scaled floor (never under
    10 px). (A 4:3 or portrait screen has differently shaped slots, so its font isn't s times.)"""
    write_cfg(show_metadata=True, metadata_defaults={"show_date": True, "show_camera": True})
    reference = None
    for viewport, s in VIEWPORTS.items():
        page = open_page(viewport).page
        _until(page, PILLS_READY_JS)
        fonts = pill_fonts(page)
        assert fonts and len(set(fonts)) == 1, fonts  # one size per screen
        if reference is None:
            reference = fonts[0]
        if abs(viewport[0] / viewport[1] - 16 / 9) < 0.01:
            assert abs(fonts[0] - reference * s) <= 1, (viewport, fonts[0], reference)
        assert fonts[0] >= max(10, 12 * s) - 0.01
        assert page.evaluate("PicMePills.fontMin()") == pytest.approx(max(10, 12 * s))
        page.context.close()


@pytest.mark.parametrize("style", ["caption", "placard", "whisper"])
@pytest.mark.parametrize("viewport", [(1920, 1080), (1024, 768), (768, 1024)], ids=["1920x1080", "1024x768", "768x1024"])
def test_label_style_floors_scale(write_cfg, open_page, viewport, style):
    write_cfg(show_metadata=True, label_style=style,
              metadata_defaults={"show_date": True, "show_camera": True, "text_size": "small"})
    page = open_page(viewport).page
    _until(page, LABELS_READY_JS)
    s = VIEWPORTS[viewport]
    spec_min = {"caption": 18, "placard": 18, "whisper": 20}[style]
    sizes = page.evaluate("""() => [...document.querySelectorAll('.slot-overlay')].map(o => ({
      label: parseFloat(o.style.getPropertyValue('--label-font-size')),
      smallest: Math.min(...[...o.querySelectorAll('.lbl-item')].map(i => parseFloat(getComputedStyle(i).fontSize))),
    }))""")
    for size in sizes:
        assert size["label"] >= round(max(10, spec_min * s)), size
        if s < 1:
            assert size["label"] < spec_min, size  # the 1080p floor no longer holds it up
        # The smallest visible text (placard details, whisper place) stays at the scaled floor
        assert size["smallest"] >= max(10, 16 * s) - 1, size


# ── Resize ──

def test_resizing_a_live_display_reapplies_the_scale(write_cfg, open_page):
    write_cfg(show_metadata=True, metadata_defaults={"show_date": True})
    page = open_page((1920, 1080)).page
    assert geometry(page)["slots"][0]["border"] == 20
    page.set_viewport_size({"width": 1280, "height": 720})
    _until(page, "() => Math.abs(PicMeScale.factor() - 2 / 3) < 1e-9 && "
                 "parseFloat(getComputedStyle(document.querySelector('.mat-frame')).borderTopWidth) === 13")
    g = geometry(page)
    for slot in g["slots"]:
        assert slot["border"] == 13 and slot["mat"] == 20 and slot["inset"] == 7, slot
    assert abs(g["padding"] - 8) <= 1
    assert_no_overflow(g)
    page.set_viewport_size({"width": 768, "height": 1024})
    _until(page, "() => Math.abs(PicMeScale.factor() - 0.4) < 1e-9 && "
                 "parseFloat(getComputedStyle(document.querySelector('.mat-frame')).borderTopWidth) === 8")
    assert_no_overflow(geometry(page))


# ── Editor ──

def test_editor_previews_the_reference_screen(write_cfg, open_page):
    """The editor's preview and size tags are at 1920x1080 (s = 1), even in a small window."""
    write_cfg()
    page = open_page((1280, 720), path="/editor/").page
    _until(page, "() => !!window.PicMeAspect && !!window.PicMeScale")
    got = page.evaluate("() => ({ s: PicMeScale.factor(), ref: PicMeAspect.REFERENCE, scaleRef: PicMeScale.REFERENCE })")
    assert got["s"] == 1
    assert (got["ref"]["width"], got["ref"]["height"]) == (got["scaleRef"]["width"], got["scaleRef"]["height"]) == (1920, 1080)
