"""Browser end-to-end tests for gallery mat mode on the display (README → Gallery mat).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides ``e2e_config_path``.
Pixel checks screenshot the display at device scale 1 with a test palette — magenta wall, green
solid frame, cyan mat, white bevel — and each slot showing its own solid-colour photo. Shape
checks walk out from each opening's centre to where its outline (PicMeShapes, the same outline
the display clips with) ends, then sample a few px outside (must be mat) and inside the photo's
own outline, past the cast shadow (must be photo).
"""
from __future__ import annotations

import datetime as dt
import io
import json

import pytest
from PIL import Image

from conftest import (LANDSCAPE, PORTRAIT, _exif_bytes, _make_heic, _make_image, editor_mode_init_js,
                      make_config)
from video_fixtures import FFMPEG, make_clip

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401
from test_frames_browser import MEDIA_READY_JS, _until  # noqa: E402
from test_pills_browser import LONG_LOCATION, long_location  # noqa: E402

WALL, FRAME, MAT, BEVEL = (255, 0, 255), (0, 255, 0), (0, 255, 255), (255, 255, 255)
BACKING = (27, 27, 27)  # gallery.js BACKING, behind a photo that doesn't fill its opening
SLOT_COLORS = [(200, 40, 40), (40, 40, 200), (200, 120, 0), (120, 0, 160), (0, 100, 60), (160, 160, 0),
               (90, 50, 20), (0, 90, 140), (220, 100, 140), (60, 60, 60), (140, 70, 200), (100, 140, 40)]
PORTRAIT_COLOR = (230, 150, 30)
HEIC_COLOR = (40, 120, 200)
VIDEO_COLOR = (230, 60, 60)  # ffmpeg "#e63c3c"
GALLERY = {"mat_color": "#00ffff", "outer_margin": 64, "gutter": 28, "wall_margin": 12, "bevel": True,
           "bevel_width": 4, "bevel_color": "#ffffff", "show_frame": True, "frame_style": "solid",
           "frame_color": "#00ff00", "frame_width": 40}
SHAPES = ["rounded", "ellipse", "circle", "arch", "hexagon", "heart"]
NO_SHUFFLE = "Math.random = () => 0.9999;"


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-gallery")
    for i, color in enumerate(SLOT_COLORS, start=1):
        (root / f"c{i}").mkdir()
        _make_image(root / f"c{i}" / "photo.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True, camera=True))
    (root / "many").mkdir()
    for i in range(14):
        _make_image(root / "many" / f"photo_{i:02}.jpg", size=LANDSCAPE, fmt="JPEG", color=(10 + 15 * i, 60, 90))
    (root / "portrait").mkdir()
    _make_image(root / "portrait" / "photo.jpg", size=PORTRAIT, fmt="JPEG", color=PORTRAIT_COLOR)
    (root / "heic").mkdir()
    _make_heic(root / "heic" / "IMG_0001.HEIC", color=HEIC_COLOR)
    (root / "corrupt").mkdir()
    (root / "corrupt" / "a_broken.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    _make_image(root / "corrupt" / "b_good.jpg", size=LANDSCAPE, fmt="JPEG", color=SLOT_COLORS[0])
    (root / "nested" / "2024" / "trip").mkdir(parents=True)
    _make_image(root / "nested" / "2024" / "trip" / "deep.jpg", size=LANDSCAPE, fmt="JPEG", color=SLOT_COLORS[3])
    if FFMPEG:
        (root / "video").mkdir()
        make_clip(root / "video" / "clip.mp4", color="#e63c3c", duration=4, size=(320, 240))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "c1"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a gallery config — slot N shows folder cN, photos never cycle, no pills — and
    restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(layout="gallery-12", gallery=None, slots=12, slot_overrides=None, folders=None, **extra) -> dict:
        cfg = make_config(root / "c1", layout=layout, slots=slots, cycle_seconds=300)
        for i, slot in enumerate(cfg["slots"], start=1):
            slot["source_path"] = str(root / ((folders or {}).get(i) or f"c{(i - 1) % 12 + 1}"))
            slot["fill_mode"] = "cover"
        cfg["wall_color"] = "#ff00ff"
        cfg["show_metadata"] = False
        cfg["frame_defaults"] = {"mat_color": "#000000", "mat_width": 16, "show_frame": True,
                                 "frame_color": "#2c2318", "frame_width": 8, "frame_style": "solid"}
        cfg["gallery"] = {**GALLERY, **(gallery or {})}
        for slot_id, override in (slot_overrides or {}).items():
            cfg["slots"][slot_id - 1].update(override)
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.root = root
    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_display(browser, server):
    contexts = []

    def _open(viewport=VIEWPORT, at=None, tz="UTC", routes=(), init_scripts=(), wait_media=True) -> PageWatcher:
        ctx = browser.new_context(viewport=viewport, device_scale_factor=1, timezone_id=tz)
        ctx.add_init_script(editor_mode_init_js("advanced"))
        for script in init_scripts:
            ctx.add_init_script(script)
        contexts.append(ctx)
        for pattern, handler in routes:
            ctx.route(pattern, handler)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        if at is not None:
            page.clock.install(time=at)
        page.goto(server + "/display/", wait_until="networkidle")
        if wait_media:
            _until(page, MEDIA_READY_JS)
        page.add_style_tag(content="#editor-btn { display: none !important; }")
        page.wait_for_timeout(900)  # the 0.8 s crossfade
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def shot(page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def near(px, color, tol=14) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(px, color))


def pixel(img, x, y):
    return img.getpixel((int(round(x)), int(round(y))))


OPENING_JS = """id => {
  const el = document.getElementById(`slot-${id}`);
  const rect = e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  const box = rect(el.querySelector('.opening-box')), media = rect(el.querySelector('.mat-frame'));
  const s = PicMeShapes.get(el.dataset.shape).safe(media.w, media.h);
  return { shape: el.dataset.shape, cell: rect(el), box, media,
           safe: { x: media.x + s.x, y: media.y + s.y, w: s.w, h: s.h },
           clip: getComputedStyle(el.querySelector('.mat-frame')).clipPath };
}"""


def opening(page, slot_id) -> dict:
    return page.evaluate(OPENING_JS, slot_id)


# Points `out` px outside the opening's outline and `inn` px inside the photo's outline, along
# `n` rays from the centre. Only points the outline itself puts outside / inside are returned.
SAMPLES_JS = """({ id, n, out, inn }) => {
  const el = document.getElementById(`slot-${id}`);
  const shape = PicMeShapes.get(el.dataset.shape);
  const r = el.querySelector('.opening-box').getBoundingClientRect();
  const m = el.querySelector('.mat-frame').getBoundingClientRect();
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  document.body.appendChild(svg);
  const path = d => { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); svg.appendChild(p); return p; };
  const outer = path(shape.svgPath(0, 0, r.width, r.height)), inner = path(shape.svgPath(0, 0, m.width, m.height));
  const pt = svg.createSVGPoint();
  const inside = (p, x, y) => { pt.x = x; pt.y = y; return p.isPointInFill(pt); };
  const edge = (p, w, h, dx, dy) => { let t = 0; while (inside(p, w / 2 + dx * t, h / 2 + dy * t) && t < 4000) t += 0.25; return t; };
  // Every point within `d` px is on the same side of the outline (not in a narrow notch)
  const clear = (p, x, y, d, want) => [...Array(16).keys()].every(k =>
    inside(p, x + d * Math.cos(k * Math.PI / 8), y + d * Math.sin(k * Math.PI / 8)) === want) && inside(p, x, y) === want;
  const res = { outside: [], inside: [] };
  for (let i = 0; i < n; i++) {
    const a = 2 * Math.PI * (i + 0.5) / n, dx = Math.cos(a), dy = Math.sin(a);
    let t = edge(outer, r.width, r.height, dx, dy) + out;
    let x = r.width / 2 + dx * t, y = r.height / 2 + dy * t;
    if (clear(outer, x, y, out - 1, false)) res.outside.push([r.x + x, r.y + y]);
    t = edge(inner, m.width, m.height, dx, dy) - inn;
    x = m.width / 2 + dx * t; y = m.height / 2 + dy * t;
    if (t > 0 && clear(inner, x, y, Math.min(6, inn - 1), true)) res.inside.push([m.x + x, m.y + y]);
  }
  svg.remove();
  return res;
}"""


def samples(page, slot_id, n=40, out=4, inn=10) -> dict:
    return page.evaluate(SAMPLES_JS, {"id": slot_id, "n": n, "out": out, "inn": inn})


def assert_cut(img, page, slot_id, color, n=40):
    """Mat just outside the opening's outline, the slot's photo just inside the photo's outline."""
    s = samples(page, slot_id, n=n)
    assert len(s["outside"]) >= n * 0.6 and len(s["inside"]) >= n * 0.6, s
    bad_out = [(p, pixel(img, *p)) for p in s["outside"] if not near(pixel(img, *p), MAT, 16)]
    bad_in = [(p, pixel(img, *p)) for p in s["inside"] if not near(pixel(img, *p), color, 24)]
    assert not bad_out, f"slot {slot_id}: not mat outside the opening: {bad_out[:5]}"
    assert not bad_in, f"slot {slot_id}: not photo inside the opening: {bad_in[:5]}"


def gallery_mode(page) -> bool:
    return page.evaluate("() => document.getElementById('collage').classList.contains('gallery')")


# --- Frame, mat and openings ---

def test_frame_mat_gutter_and_openings(write_cfg, open_display):
    write_cfg()
    w = open_display()
    page = w.page
    assert gallery_mode(page)
    assert page.locator(".gallery-frame").count() == 1 and page.locator(".gallery-mat").count() == 1
    assert page.locator(".slot.opening").count() == 12
    img = shot(page)
    # Wall (12 px), then the 40 px frame band, then the mat's 64 px margin
    assert near(pixel(img, 5, 540), WALL, 40)  # the frame casts a soft shadow on the wall
    for x in (14, 31, 50):
        assert near(pixel(img, x, 540), FRAME), x
    assert near(pixel(img, 12 + 40 + 32, 540), MAT)
    assert near(pixel(img, 960, 12 + 40 + 32), MAT)
    frame = page.evaluate("() => getComputedStyle(document.querySelector('.gallery-frame')).borderTopWidth")
    assert frame == "40px"
    # Gutters: 28 px of mat between neighbouring openings
    o1, o2, o5 = opening(page, 1), opening(page, 2), opening(page, 5)
    assert o2["cell"]["x"] - (o1["cell"]["x"] + o1["cell"]["w"]) == pytest.approx(28, abs=1)
    assert o5["cell"]["y"] - (o1["cell"]["y"] + o1["cell"]["h"]) == pytest.approx(28, abs=1)
    assert near(pixel(img, (o1["box"]["x"] + o1["box"]["w"] + o2["box"]["x"]) / 2, o1["box"]["y"] + o1["box"]["h"] / 2), MAT)
    assert near(pixel(img, o1["box"]["x"] + o1["box"]["w"] / 2, (o1["box"]["y"] + o1["box"]["h"] + o5["box"]["y"]) / 2), MAT)
    # Each opening shows its own photo
    for i in range(1, 13):
        o = opening(page, i)
        assert o["shape"] == "rect" and o["clip"] == "none"  # rectangles are never clipped
        assert near(pixel(img, o["safe"]["x"] + o["safe"]["w"] / 2, o["safe"]["y"] + o["safe"]["h"] / 2), SLOT_COLORS[i - 1], 20), i
    assert w.errors == []


def test_rect_opening_edges_are_exact(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"bevel": False})
    w = open_display()
    page = w.page
    assert gallery_mode(page)
    img = shot(page)
    for i in range(1, 5):
        b = opening(page, i)["box"]
        right, bottom, cy, cx = b["x"] + b["w"], b["y"] + b["h"], b["y"] + b["h"] / 2, b["x"] + b["w"] / 2
        # The cast shadow falls on the top and left; the bottom and right edges are crisp
        assert near(pixel(img, right - 2, cy), SLOT_COLORS[i - 1], 20) and near(pixel(img, right + 2, cy), MAT)
        assert near(pixel(img, cx, bottom - 2), SLOT_COLORS[i - 1], 20) and near(pixel(img, cx, bottom + 2), MAT)
        assert near(pixel(img, b["x"] - 2, cy), MAT) and near(pixel(img, cx, b["y"] - 2), MAT)
    assert w.errors == []


@pytest.mark.parametrize("shape", SHAPES)
def test_shaped_openings_are_cut_exactly(write_cfg, open_display, shape):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"},
              slot_overrides={i: {"shape": shape} for i in range(1, 5)})
    w = open_display()
    page = w.page
    img = shot(page)
    for i in range(1, 5):
        o = opening(page, i)
        assert o["shape"] == shape
        assert o["clip"] != "none"
        assert_cut(img, page, i, SLOT_COLORS[i - 1])
    # Fixed-aspect shapes keep their proportions in any cell
    o = opening(page, 1)
    aspect = page.evaluate("s => PicMeShapes.get(s).aspect || null", shape)
    if aspect:
        assert o["box"]["w"] / o["box"]["h"] == pytest.approx(aspect, abs=0.02)
    assert w.errors == []


def test_layout_shapes_and_slot_shape_override(write_cfg, open_display):
    """gallery-cameo's own shapes, with slot 1's shape set in config winning over the layout's."""
    write_cfg(layout="gallery-cameo", slots=5, slot_overrides={1: {"shape": "heart"}, 3: {"shape": "bogus"}})
    w = open_display()
    page = w.page
    assert [opening(page, i)["shape"] for i in range(1, 6)] == ["heart", "arch", "ellipse", "ellipse", "ellipse"]
    img = shot(page)
    for i in range(1, 6):
        assert_cut(img, page, i, SLOT_COLORS[i - 1])
    assert w.errors == []


def test_bevel_is_lit_from_the_top_left_and_can_be_turned_off(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"bevel_width": 8})
    page = open_display().page
    img = shot(page)
    b = opening(page, 1)["box"]
    top_left = pixel(img, b["x"] + 0.1 * b["w"], b["y"] + 4)
    bottom_right = pixel(img, b["x"] + 0.9 * b["w"], b["y"] + b["h"] - 4)
    assert min(top_left) >= 235, top_left
    assert 160 <= min(bottom_right) and sum(top_left) - sum(bottom_right) >= 45, (top_left, bottom_right)
    # The photo sits inside the bevel
    m = opening(page, 1)["media"]
    assert m["x"] - b["x"] == pytest.approx(8, abs=0.5) and b["w"] - m["w"] == pytest.approx(16, abs=0.5)

    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"bevel": False})
    _until(page, "() => !document.querySelector('.opening-deco .bevel')")
    page.wait_for_timeout(200)
    img = shot(page)
    b2 = opening(page, 1)["box"]
    assert b2 == b  # the opening itself doesn't move
    for p in ((b["x"] + 0.1 * b["w"], b["y"] + 4), (b["x"] + 0.9 * b["w"], b["y"] + b["h"] - 4)):
        px = pixel(img, *p)
        assert not near(px, BEVEL, 60) and near(px, SLOT_COLORS[0], 70), px


def test_contain_in_an_ellipse_letterboxes_inside_the_shape(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"},
              slot_overrides={1: {"shape": "ellipse", "fill_mode": "contain"}}, folders={1: "portrait"})
    w = open_display()
    page = w.page
    img = shot(page)
    o = opening(page, 1)
    m = o["media"]
    # A portrait photo in a landscape oval: backing at the sides, inside the oval
    assert near(pixel(img, m["x"] + 0.2 * m["w"], m["y"] + m["h"] / 2), BACKING, 10)
    assert near(pixel(img, m["x"] + m["w"] / 2, m["y"] + m["h"] / 2), PORTRAIT_COLOR, 20)
    # Nothing outside the oval: the backing is clipped too
    for p in samples(page, 1)["outside"]:
        assert near(pixel(img, *p), MAT, 16), p
    assert page.evaluate("() => getComputedStyle(document.querySelector('#slot-1 img.visible')).objectFit") == "contain"
    assert w.errors == []


# --- Pills ---

PILLS_JS = """id => {
  const el = document.getElementById(`slot-${id}`);
  const r = el.querySelector('.opening-box').getBoundingClientRect();
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  document.body.appendChild(svg);
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', PicMeShapes.get(el.dataset.shape).svgPath(0, 0, r.width, r.height));
  svg.appendChild(p);
  const pt = svg.createSVGPoint();
  const inShape = (x, y) => { pt.x = x - r.x; pt.y = y - r.y; return p.isPointInFill(pt); };
  const pills = [...el.querySelectorAll('.overlay-pill')].map(pill => {
    const b = pill.getBoundingClientRect();
    const text = pill.querySelector('.pill-text');
    return { date: !!pill.dataset.date, text: pill.textContent,
             hidden: pill.classList.contains('fit-hidden'), scrolling: pill.classList.contains('scrolling'),
             animation: text ? getComputedStyle(text).animationName : null,
             corners: [[b.left + 1, b.top + 1], [b.right - 1, b.top + 1], [b.left + 1, b.bottom - 1], [b.right - 1, b.bottom - 1]]
               .map(([x, y]) => inShape(x, y)) };
  });
  svg.remove();
  const m = el.querySelector('.mat-frame').getBoundingClientRect();
  const safe = PicMeShapes.get(el.dataset.shape).safe(m.width, m.height);
  return { pills, font: getComputedStyle(el).getPropertyValue('--pill-font-size').trim(),
           expected: `${PicMePills.fontSize({}, safe.w - 20, safe.h - 20)}px` };
}"""

CAMERA_FIELDS = {"show_date": True, "show_camera": True, "show_fstop": True, "show_iso": True}


@pytest.mark.parametrize("shape", ["heart", "ellipse", "hexagon", "arch"])
def test_pills_stay_inside_the_shape_and_size_for_it(write_cfg, open_display, shape):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, show_metadata=True,
              metadata_defaults=CAMERA_FIELDS, max_metadata_pills=None,
              slot_overrides={i: {"shape": shape} for i in range(1, 5)})
    w = open_display()
    page = w.page
    _until(page, "() => [...document.querySelectorAll('.slot')].every(s => s.querySelector('.overlay-pill.visible'))")
    for i in range(1, 5):
        info = page.evaluate(PILLS_JS, i)
        shown = [p for p in info["pills"] if not p["hidden"]]
        assert shown and shown[-1]["date"], info
        for p in shown:
            assert all(p["corners"]), (shape, i, p)
        assert info["font"] == info["expected"]
    assert w.errors == []


def test_small_heart_keeps_the_date_and_hides_other_pills(write_cfg, open_display):
    write_cfg(show_metadata=True, metadata_defaults={**CAMERA_FIELDS, "show_megapixels": True,
                                                     "show_shutter_speed": True, "show_focal_length": True},
              max_metadata_pills=None, slot_overrides={i: {"shape": "heart"} for i in range(1, 13)})
    page = open_display().page
    _until(page, "() => document.querySelector('#slot-1 .overlay-pill.visible')")
    info = page.evaluate(PILLS_JS, 1)
    assert any(p["hidden"] for p in info["pills"])
    assert [p for p in info["pills"] if p["date"] and not p["hidden"]]
    assert all(all(p["corners"]) for p in info["pills"] if not p["hidden"])


def test_long_pill_scrolls_inside_the_shape(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, show_metadata=True,
              metadata_defaults={"show_date": True, "show_location": True},
              slot_overrides={i: {"shape": "ellipse"} for i in range(1, 5)})
    w = open_display(routes=[("**/api/metadata?*", long_location)])
    page = w.page
    _until(page, "() => document.querySelector('#slot-1 .overlay-pill.scrolling')")
    info = page.evaluate(PILLS_JS, 1)
    loc = info["pills"][0]
    assert loc["text"] == LONG_LOCATION and loc["scrolling"] and loc["animation"].startswith("pill-marquee-")
    assert all(loc["corners"])
    assert w.errors == []


# --- Media in openings: video, HEIC, corrupt files, no duplicates, subfolders ---

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_video_plays_inside_a_heart(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"},
              slot_overrides={1: {"shape": "heart", "mode": "video", "video_loops": 1}}, folders={1: "video"})
    w = open_display()
    page = w.page
    assert page.evaluate("() => document.getElementById('slot-1').classList.contains('video-mode')")
    t0 = page.evaluate("() => document.querySelector('#slot-1 video.visible').currentTime")
    page.wait_for_timeout(600)
    assert page.evaluate("() => document.querySelector('#slot-1 video.visible').currentTime") != t0  # playing
    img = shot(page)
    s = samples(page, 1)
    assert all(near(pixel(img, *p), MAT, 16) for p in s["outside"])
    inside = [pixel(img, *p) for p in s["inside"]]
    assert sum(near(px, VIDEO_COLOR, 40) for px in inside) >= 0.8 * len(inside), inside[:5]
    assert w.errors == []


def test_heic_shows_in_a_hexagon(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"},
              slot_overrides={2: {"shape": "hexagon"}}, folders={2: "heic"})
    page = open_display().page
    img = shot(page)
    assert opening(page, 2)["shape"] == "hexagon"
    assert_cut(img, page, 2, HEIC_COLOR)


def test_corrupt_photo_is_skipped_in_an_opening(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"},
              slot_overrides={1: {"shape": "circle"}}, folders={1: "corrupt"})
    w = open_display(init_scripts=[NO_SHUFFLE])
    page = w.page
    src = page.evaluate("() => document.querySelector('#slot-1 img.visible').getAttribute('src')")
    assert "b_good.jpg" in src
    assert_cut(shot(page), page, 1, SLOT_COLORS[0])


def test_no_duplicates_across_twelve_openings(write_cfg, open_display):
    write_cfg(folders={i: "many" for i in range(1, 13)})
    page = open_display().page
    srcs = page.evaluate("() => [...document.querySelectorAll('.slot img.visible')].map(i => i.getAttribute('src'))")
    assert len(srcs) == 12 and len(set(srcs)) == 12


def test_subfolder_photos_show_in_openings(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, include_subfolders=True,
              folders={3: "nested"})
    page = open_display().page
    src = page.evaluate("() => document.querySelector('#slot-3 img.visible').getAttribute('src')")
    assert "deep.jpg" in src


# --- Rotation, live changes, time of day, per-slot settings ---

def _rotation_day(when: dt.datetime) -> int:
    return (dt.date(when.year, when.month, when.day) - dt.date(1970, 1, 1)).days


def test_rotation_mixes_gallery_and_separate_frame_days(write_cfg, open_display):
    # Eligible layouts in /api/layouts order: classic-4, gallery-cameo
    day = dt.datetime(2026, 10, 13, 3, 59, tzinfo=dt.timezone.utc)
    before, after = ["classic-4", "gallery-cameo"][(_rotation_day(day) - 1) % 2], ["classic-4", "gallery-cameo"][_rotation_day(day) % 2]
    write_cfg(layout="classic-4", slots=5,
              layout_rotation={"enabled": True, "layouts": ["classic-4", "gallery-cameo"], "change_at": "04:00"})
    w = open_display(at=day)
    page = w.page

    def check(layout_id):
        _until(page, f"() => document.getElementById('collage').dataset.layout === '{layout_id}'")
        if layout_id == "gallery-cameo":
            assert gallery_mode(page) and opening(page, 2)["shape"] == "arch"
        else:
            assert not gallery_mode(page)
            assert page.evaluate("() => getComputedStyle(document.querySelector('#slot-1 .mat-frame')).borderTopWidth") == "8px"

    check(before)
    page.evaluate("window.__marker = 1")
    page.clock.run_for(90_000)  # past change_at: the rotation check reloads into the other mode
    check(after)
    assert page.evaluate("window.__marker") is None
    assert w.errors == []


def test_switching_a_layout_to_gallery_reloads_and_colours_patch_in_place(write_cfg, open_display):
    write_cfg(layout="classic-4", slots=4)
    page = open_display().page
    assert not gallery_mode(page)
    page.evaluate("window.__marker = 1")
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"})
    _until(page, "() => document.getElementById('collage')?.classList.contains('gallery')", timeout=8)
    assert page.evaluate("window.__marker") is None  # reloaded
    _until(page, MEDIA_READY_JS)
    page.wait_for_timeout(900)
    page.evaluate("window.__marker = 2")
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"mat_color": "#ffa000"})
    _until(page, "() => getComputedStyle(document.querySelector('.gallery-mat')).backgroundColor === 'rgb(255, 160, 0)'", timeout=8)
    assert page.evaluate("window.__marker") == 2  # patched, not reloaded
    assert near(pixel(shot(page), 12 + 40 + 32, 540), (255, 160, 0))
    # A slot's shape changes in place too, and dropping it goes back to the layout's own
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"mat_color": "#ffa000"},
              slot_overrides={1: {"shape": "heart"}})
    _until(page, "() => document.getElementById('slot-1').dataset.shape === 'heart'", timeout=8)
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, gallery={"mat_color": "#ffa000"})
    _until(page, "() => document.getElementById('slot-1').dataset.shape === 'rect'", timeout=8)
    assert page.evaluate("window.__marker") == 2
    # And back to separate frames: reload again
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "slots"})
    _until(page, "() => document.querySelector('#collage') && !document.getElementById('collage').classList.contains('gallery') && document.querySelectorAll('.slot').length === 4", timeout=8)
    assert page.evaluate("window.__marker") is None


def test_time_of_day_changes_colours_but_not_widths(write_cfg, open_display):
    tod = {"enabled": True, "day_start": "07:00", "night_start": "20:00",
           "day": {"wall_color": "#ff00ff"},
           "night": {"wall_color": "#ff00ff", "mat_color": "#203040", "frame_color": "#0000ff",
                     "frame_width": 8, "mat_width": 20}}
    write_cfg(time_of_day=tod)
    page = open_display(at=dt.datetime(2026, 10, 13, 22, 0, tzinfo=dt.timezone.utc)).page
    img = shot(page)
    assert near(pixel(img, 12 + 40 + 32, 540), (32, 48, 64))   # night mat
    assert near(pixel(img, 31, 540), (0, 0, 255))                # night frame colour…
    assert page.evaluate("() => getComputedStyle(document.querySelector('.gallery-frame')).borderTopWidth") == "40px"  # …same width
    assert page.evaluate("() => getComputedStyle(document.querySelector('.gallery-mat')).paddingTop") == "64px"


def test_per_slot_frame_ignored_on_gallery_days_fit_honoured(write_cfg, open_display):
    override = {"use_global": {"frame": False}, "frame": {"mat_color": "#ff8800", "mat_width": 30}, "fill_mode": "contain"}
    write_cfg(layout="classic-4", slots=4, layout_modes={"classic-4": "gallery"}, slot_overrides={1: override})
    page = open_display().page
    mf = page.evaluate("() => { const cs = getComputedStyle(document.querySelector('#slot-1 .mat-frame')); return [cs.backgroundColor, cs.paddingTop, cs.borderTopWidth]; }")
    assert mf == ["rgb(27, 27, 27)", "0px", "0px"]
    assert page.evaluate("() => getComputedStyle(document.querySelector('#slot-1 img.visible')).objectFit") == "contain"

    write_cfg(layout="classic-4", slots=4, slot_overrides={1: override})  # separate frames
    _until(page, "() => !document.getElementById('collage').classList.contains('gallery') && document.querySelector('#slot-1 .mat-frame')", timeout=8)
    _until(page, "() => getComputedStyle(document.querySelector('#slot-1 .mat-frame')).backgroundColor === 'rgb(255, 136, 0)'")
    assert page.evaluate("() => getComputedStyle(document.querySelector('#slot-1 img')).objectFit") == "contain"


# --- Small screens and decorative frames ---

def test_small_screen_keeps_openings_usable(write_cfg, open_display):
    write_cfg()
    w = open_display(viewport={"width": 640, "height": 360})
    page = w.page
    m = page.evaluate("""() => { const cs = getComputedStyle(document.querySelector('.gallery-mat'));
      const f = getComputedStyle(document.querySelector('.gallery-frame'));
      return { pad: parseFloat(cs.paddingTop), gap: parseFloat(cs.rowGap), frame: parseFloat(f.borderTopWidth),
               sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight }; }""")
    assert m["pad"] <= round(0.12 * 360) and m["gap"] <= round(0.08 * 360) and m["frame"] <= 60
    assert m["sw"] <= 640 and m["sh"] <= 360
    for i in range(1, 13):
        b = opening(page, i)["box"]
        assert b["w"] >= 24 and b["h"] >= 24, (i, b)
        assert b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["w"] <= 640 and b["y"] + b["h"] <= 360
    assert w.errors == []


def test_decorative_frame_on_the_gallery(write_cfg, open_display):
    write_cfg(gallery={"frame_style": "wood", "frame_width": 120})
    page = open_display(viewport={"width": 640, "height": 360}).page
    f = page.evaluate("() => { const cs = getComputedStyle(document.querySelector('.gallery-frame')); return [cs.borderImageSource, cs.borderTopWidth, cs.borderImageRepeat]; }")
    assert "/assets/frames/wood.png" in f[0]
    assert f[1] == "60px"  # capped at a sixth of the 360 px short side
    assert "round" in f[2]
    big = write_cfg(gallery={"frame_style": "gold", "frame_width": 48})
    assert big
    _until(page, "() => getComputedStyle(document.querySelector('.gallery-frame')).borderImageSource.includes('gold.png')", timeout=8)
