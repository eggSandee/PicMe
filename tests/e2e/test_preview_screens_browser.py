"""Browser end-to-end tests for the editor's Screen picker (README → Editor layout, Screen sizes).

The preview can show the layout on another screen shape: it takes that aspect ratio, and draws
frames, mats, the collage's padding, gallery widths and the slot size tags as the display does
at that viewport (scale.js's factor for it). These compare the editor's preview with the real
display opened at the same viewport (device scale 1), and check that the choice is a per-browser
view preference that never reaches config.json.
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

# The picker's shapes: id -> the viewport it previews
SHAPES = {
    "tv": (1920, 1080),
    "monitor": (1920, 1200),
    "tablet": (1024, 768),
    "tablet-portrait": (768, 1024),
    "portrait": (1080, 1920),
    "ultrawide": (2560, 1080),
}
SHAPE_IDS = list(SHAPES)
LAPTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
FRAME = {"mat_color": "#000000", "mat_width": 30, "show_frame": True, "frame_color": "#2c2318",
         "frame_width": 20, "frame_style": "solid"}
GALLERY = {"mat_color": "#00ffff", "outer_margin": 64, "gutter": 28, "wall_margin": 12, "bevel": True,
           "bevel_width": 4, "bevel_color": "#ffffff", "show_frame": True, "frame_style": "solid",
           "frame_color": "#00ff00", "frame_width": 40}
HINT = "Preview only: the display fits whatever screen it's on."


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-preview-screens")
    (root / "photos").mkdir()
    for i in range(6):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG",
                    color=(40 + 30 * i, 80, 120), exif=_exif_bytes(date=True, camera=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a hero-left config (photos never cycle, no pills) and restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(frame=None, layout="hero-left", slots=5, **extra) -> dict:
        cfg = make_config(root / "photos", layout=layout, slots=slots, cycle_seconds=300)
        cfg["show_metadata"] = False
        cfg["frame_defaults"] = {**FRAME, **(frame or {})}
        cfg["layout_rotation"] = {"enabled": False, "layouts": [], "change_at": "04:00"}
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_page(browser, server):
    contexts = []

    def _open(viewport, path="/display/", touch=False, ctx=None) -> PageWatcher:
        if isinstance(viewport, tuple):
            viewport = {"width": viewport[0], "height": viewport[1]}
        if ctx is None:
            ctx = browser.new_context(viewport=viewport, device_scale_factor=1, timezone_id="UTC",
                                      has_touch=touch, is_mobile=touch)
            ctx.add_init_script(editor_mode_init_js("advanced"))
            contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.goto(server + path, wait_until="networkidle")
        if path.startswith("/display"):
            _until(page, MEDIA_READY_JS)
        else:
            page.wait_for_selector("#collage-preview .preview-slot, #collage-preview .opening", state="attached")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def pick(page, shape):
    page.select_option("#preview-screen", shape)
    w, h = SHAPES[shape]
    _until(page, f"""() => {{ const r = document.getElementById('collage-preview').getBoundingClientRect();
        return r.width > 0 && Math.abs(r.width / r.height - {w / h}) < 0.01; }}""")


# The display's separate frames: each slot's border, mat and photo opening (display px)
DISPLAY_SLOTS_JS = """() => {
  const c = getComputedStyle(document.getElementById('collage'));
  return { padding: parseFloat(c.paddingTop), slots: [...document.querySelectorAll('#collage .slot')].map(slot => {
    const mf = slot.querySelector('.mat-frame'), cs = getComputedStyle(mf);
    const pt = parseFloat(cs.paddingTop), pb = parseFloat(cs.paddingBottom), pl = parseFloat(cs.paddingLeft);
    return { border: parseFloat(cs.borderTopWidth), mat: pt,
             opening: { w: mf.clientWidth - 2 * pl, h: mf.clientHeight - pt - pb } };
  }) };
}"""

# The editor's preview of separate frames, in preview px, with k = preview px per display px
PREVIEW_SLOTS_JS = """(W) => {
  const p = document.getElementById('collage-preview'), r = p.getBoundingClientRect();
  return { box: { w: r.width, h: r.height }, k: p.clientWidth / W, padding: parseFloat(getComputedStyle(p).paddingTop),
           slots: [...p.querySelectorAll('.preview-slot')].map(d => { const cs = getComputedStyle(d);
             const t = d.querySelector('.slot-tags');
             return { border: parseFloat(cs.borderTopWidth), mat: parseFloat(cs.paddingTop),
                      ratio: t.dataset.ratio, title: t.title }; }) };
}"""


@pytest.mark.parametrize("shape", SHAPE_IDS)
def test_preview_matches_the_display_at_each_shape(write_cfg, open_page, shape):
    write_cfg()
    W, H = SHAPES[shape]
    display = open_page((W, H)).page.evaluate(DISPLAY_SLOTS_JS)
    editor = open_page(LAPTOP, path="/editor/").page
    pick(editor, shape)
    got = editor.evaluate(PREVIEW_SLOTS_JS, W)

    # The box has the screen's shape
    assert got["box"]["w"] / got["box"]["h"] == pytest.approx(W / H, rel=0.01), got["box"]
    # The collage padding, frames and mats are the display's at that size, times k
    k = got["k"]
    assert abs(got["padding"] - display["padding"] * k) <= 1, (got["padding"], display["padding"], k)
    assert len(got["slots"]) == len(display["slots"]) == 5
    for pv, dp in zip(got["slots"], display["slots"]):
        assert abs(pv["border"] - dp["border"] * k) <= 1, (pv, dp, k)
        assert abs(pv["mat"] - dp["mat"] * k) <= 1, (pv, dp, k)
    # Each slot's size tag is the ratio of the display's photo opening at that size
    want = editor.evaluate("os => os.map(o => PicMeAspect.label(o.w, o.h))", [d["opening"] for d in display["slots"]])
    assert [s["ratio"] for s in got["slots"]] == want
    assert all(f"on a {W}×{H} screen" in s["title"] for s in got["slots"])
    # The editor's own scale is untouched (the preview draws inside PicMeScale.at)
    assert editor.evaluate("PicMeScale.factor()") == 1


def test_tags_differ_between_shapes(write_cfg, open_page):
    """The tags follow the shape: hero-left's small slots are wider on an ultrawide than on a tablet."""
    write_cfg()
    editor = open_page(LAPTOP, path="/editor/").page
    ratios = {}
    for shape in ("tv", "tablet-portrait", "ultrawide"):
        pick(editor, shape)
        ratios[shape] = editor.evaluate("() => [...document.querySelectorAll('#collage-preview .slot-tags')].map(t => t.dataset.ratio)")
    assert len({tuple(r) for r in ratios.values()}) == 3, ratios


@pytest.mark.parametrize("shape", ["tablet-portrait", "ultrawide"])
def test_decorative_frame_cap_matches_the_display(write_cfg, open_page, shape):
    """A moulding is capped at a sixth of the slot's short side, which depends on the screen's shape."""
    write_cfg(frame={"frame_style": "wood", "frame_width": 80, "mat_width": 10})
    W, H = SHAPES[shape]
    display = open_page((W, H)).page.evaluate(DISPLAY_SLOTS_JS)
    editor = open_page(LAPTOP, path="/editor/").page
    pick(editor, shape)
    got = editor.evaluate(PREVIEW_SLOTS_JS, W)
    k = got["k"]
    for pv, dp in zip(got["slots"], display["slots"]):
        assert abs(pv["border"] - dp["border"] * k) <= 1, (pv, dp, k)


def test_single_stays_full_bleed(write_cfg, open_page):
    write_cfg(layout="single", slots=1)
    editor = open_page(LAPTOP, path="/editor/").page
    pick(editor, "portrait")
    got = editor.evaluate(PREVIEW_SLOTS_JS, 1080)
    assert got["padding"] == 0
    assert got["slots"][0]["border"] == 0 and got["slots"][0]["mat"] == 0
    assert got["slots"][0]["ratio"] == "9:16"


# ── Gallery mat ──

DISPLAY_GALLERY_JS = """() => {
  const root = document.getElementById('collage'), rr = root.getBoundingClientRect();
  return { frame: parseFloat(getComputedStyle(root.querySelector('.gallery-frame')).borderTopWidth),
           margin: parseFloat(getComputedStyle(root.querySelector('.gallery-mat')).paddingTop),
           openings: [...root.querySelectorAll('.opening')].map(o => { const r = o.getBoundingClientRect();
             return { x: r.x - rr.x, y: r.y - rr.y, w: r.width, h: r.height }; }) };
}"""

# In stage px: the stage is the previewed screen at display size, scaled to the box
EDITOR_GALLERY_JS = """() => {
  const p = document.getElementById('collage-preview'), stage = p.querySelector('.gallery-stage');
  const sr = stage.getBoundingClientRect(), k = sr.width / stage.offsetWidth;
  return { stage: { w: stage.offsetWidth, h: stage.offsetHeight }, box: (r => ({ w: r.width, h: r.height }))(p.getBoundingClientRect()),
           frame: parseFloat(getComputedStyle(stage.querySelector('.gallery-frame')).borderTopWidth),
           margin: parseFloat(getComputedStyle(stage.querySelector('.gallery-mat')).paddingTop),
           openings: [...stage.querySelectorAll('.opening')].map(o => { const r = o.getBoundingClientRect();
             return { x: (r.x - sr.x) / k, y: (r.y - sr.y) / k, w: r.width / k, h: r.height / k }; }),
           ratios: [...stage.querySelectorAll('.slot-tags')].map(t => t.dataset.ratio) };
}"""


@pytest.mark.parametrize("shape", ["tv", "tablet", "portrait", "ultrawide"])
def test_gallery_preview_is_the_display_at_each_shape(write_cfg, open_page, shape):
    write_cfg(layout_modes={"hero-left": "gallery"}, gallery=GALLERY)
    W, H = SHAPES[shape]
    display = open_page((W, H)).page.evaluate(DISPLAY_GALLERY_JS)
    editor = open_page(LAPTOP, path="/editor/").page
    pick(editor, shape)
    got = editor.evaluate(EDITOR_GALLERY_JS)
    assert got["stage"] == {"w": W, "h": H}
    assert got["box"]["w"] / got["box"]["h"] == pytest.approx(W / H, rel=0.01)
    assert got["frame"] == display["frame"] and got["margin"] == display["margin"], (got, display)
    assert len(got["openings"]) == len(display["openings"]) == 5
    for pv, dp in zip(got["openings"], display["openings"]):
        for key in "xywh":
            assert abs(pv[key] - dp[key]) <= 1.5, (key, pv, dp)
    assert all(got["ratios"]), got["ratios"]
    # The gallery fields still show the configured (reference) widths
    assert editor.evaluate("PicMeGallery.resolveGallery(config, null).frame_width") == 40


# ── The picker ──

def test_choice_is_remembered_and_never_saved(write_cfg, open_page):
    write_cfg()
    before = write_cfg.path.read_text()
    w = open_page(LAPTOP, path="/editor/")
    page = w.page
    assert page.input_value("#preview-screen") == "tv"
    assert page.locator("#screen-hint").is_hidden()
    pick(page, "portrait")
    assert page.locator("#screen-hint").is_visible()
    assert page.locator("#screen-hint").inner_text() == HINT
    assert page.evaluate("localStorage.getItem('picme.previewScreen')") == "portrait"

    # A new page in the same browser opens on the remembered shape
    again = open_page(LAPTOP, path="/editor/", ctx=page.context).page
    assert again.input_value("#preview-screen") == "portrait"
    box = again.locator("#collage-preview").bounding_box()
    assert box["width"] / box["height"] == pytest.approx(1080 / 1920, rel=0.01)
    assert again.locator("#screen-hint").is_visible()
    pick(again, "tv")
    assert again.locator("#screen-hint").is_hidden()
    assert again.evaluate("localStorage.getItem('picme.previewScreen')") is None

    again.wait_for_timeout(1500)  # past the autosave delay
    assert write_cfg.path.read_text() == before
    saved = again.evaluate("fetch('/api/config').then(r => r.json())")
    assert not any("screen" in key.lower() or "preview" in key.lower() for key in saved), sorted(saved)
    assert w.errors == []


def test_picker_is_in_basic_mode_too(write_cfg, browser, server):
    write_cfg()
    ctx = browser.new_context(viewport=LAPTOP)
    try:
        ctx.add_init_script(editor_mode_init_js("basic"))
        page = ctx.new_page()
        page.goto(server + "/editor/", wait_until="networkidle")
        page.wait_for_selector("#collage-preview .preview-slot")
        assert page.locator("#preview-screen").is_visible()
        assert page.locator("#preview-screen option").all_inner_texts() == [
            "TV 16:9", "Monitor 16:10", "Tablet 4:3", "Portrait tablet 3:4", "Portrait 9:16", "Ultrawide 21:9"]
    finally:
        ctx.close()


def test_tv_round_trip_is_pixel_identical(write_cfg, open_page):
    """Back on TV 16:9 after another shape, the preview is the default one, pixel for pixel."""
    write_cfg(show_metadata=True, metadata_defaults={"show_date": True, "show_camera": True})
    page = open_page(LAPTOP, path="/editor/").page
    page.wait_for_timeout(300)
    default = page.locator("#collage-preview").screenshot()
    pick(page, "ultrawide")
    assert page.locator("#collage-preview").screenshot() != default
    pick(page, "tv")
    page.wait_for_timeout(300)
    assert page.locator("#collage-preview").screenshot() == default


def test_phone_layout_tab_fits_a_portrait_shape(write_cfg, open_page):
    write_cfg()
    w = open_page(PHONE, path="/editor/", touch=True)
    page = w.page
    full = page.locator("#collage-preview").bounding_box()
    pick(page, "portrait")
    got = page.evaluate("""() => { const r = document.getElementById('collage-preview').getBoundingClientRect(),
        f = document.getElementById('preview-fit').getBoundingClientRect();
        return { x: r.x, w: r.width, h: r.height, fitX: f.x, fitW: f.width,
                 scroll: document.documentElement.scrollWidth, view: innerWidth, viewH: innerHeight }; }""")
    assert got["scroll"] <= got["view"]
    assert got["w"] / got["h"] == pytest.approx(1080 / 1920, rel=0.01)
    assert got["h"] <= 0.6 * got["viewH"] + 1 and got["h"] > full["height"]
    assert got["x"] >= got["fitX"] and got["x"] + got["w"] <= got["fitX"] + got["fitW"] + 0.5
    assert abs((got["x"] - got["fitX"]) - (got["fitX"] + got["fitW"] - got["x"] - got["w"])) <= 1  # centred
    assert w.errors == []
