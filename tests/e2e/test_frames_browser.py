"""Browser end-to-end tests for decorative frames (README → Frames).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides
``e2e_config_path``. Pixel checks screenshot a slot at device scale 1 and compare it with the
committed frame assets: corners must be the asset's corners scaled uniformly, and edges must
repeat the asset's edge tile a whole number of times (``round``), never one stretched tile.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import time
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops, ImageStat

from conftest import LANDSCAPE, PORTRAIT, REPO_ROOT, _exif_bytes, _make_heic, _make_image, editor_mode_init_js, make_config
from video_fixtures import FFMPEG, make_clip

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401

FRAMES_DIR = REPO_ROOT / "frontend" / "assets" / "frames"
MANIFEST = {
    name: {"repeat": "round"} | {k: int(v) if v.isdigit() else v.strip("'")
                                 for k, v in re.findall(r"(slice|tile|default_width|repeat): ('\w+'|\d+)", body)}
    for name, body in re.findall(r"^\s*(\w+): \{(.*?)\},?$", (FRAMES_DIR / "frames.js").read_text(), re.M)
}
S, T = MANIFEST["wood"]["slice"], MANIFEST["wood"]["tile"]
# The design-vote styles (2026-10-20), after wood and gold
NEW_STYLES = ["black", "silver", "white", "barnwood"]
ALL_STYLES = ["wood", "gold", *NEW_STYLES]
ROUND_STYLES = [s for s in ALL_STYLES if MANIFEST[s]["repeat"] == "round"]  # barnwood: stretch
MAGENTA, GREEN, CYAN = (255, 0, 255), (0, 255, 0), (0, 255, 255)
PHOTO_COLORS = [(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40)]
NOON_UTC = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-frames")
    for folder, size in (("photos", LANDSCAPE), ("portrait", PORTRAIT)):
        (root / folder).mkdir()
        for i, color in enumerate(PHOTO_COLORS):
            _make_image(root / folder / f"photo_{i}.jpg", size=size, fmt="JPEG", color=color,
                        exif=_exif_bytes(date=True))
    (root / "heic").mkdir()
    _make_heic(root / "heic" / "IMG_0001.HEIC", color=(40, 120, 200))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a frames config (photos never cycle, no pills) and restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(frame=None, layout="classic-4", slots=4, folder="photos", slot_overrides=None, **extra) -> dict:
        cfg = make_config(root / folder, layout=layout, slots=slots, cycle_seconds=300)
        cfg["show_metadata"] = False
        cfg["frame_defaults"] = {"mat_color": "#000000", "mat_width": 0, "show_frame": True,
                                 "frame_color": "#2c2318", "frame_width": 8, "frame_style": "solid",
                                 **(frame or {})}
        for slot_id, override in (slot_overrides or {}).items():
            cfg["slots"][slot_id - 1].update(override)
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    _write.root = root
    yield _write
    e2e_config_path.write_text(original)


def _until(page, js, timeout=10.0, arg=None):
    """Polls `js` from Python (rAF-based waits don't run under the fake clock) until truthy."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = page.evaluate(js, arg)
            if last:
                return last
        except sync_api.Error:
            pass  # the page is reloading
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {js} (last: {last!r})")


MEDIA_READY_JS = """() => { const slots = [...document.querySelectorAll('.slot')];
  return slots.length > 0 && slots.every(s => {
    if (s.classList.contains('video-mode')) {
      const v = s.querySelector('video.visible');
      return v && v.readyState >= 2;
    }
    const img = s.querySelector('img.visible');
    return img && img.complete && img.naturalWidth > 0;
  }); }"""


@pytest.fixture
def open_display(browser, server):
    contexts = []

    def _open(path="/display/", viewport=VIEWPORT, at=None, tz="UTC", routes=(), editor_mode="advanced") -> PageWatcher:
        ctx = browser.new_context(viewport=viewport, device_scale_factor=1, timezone_id=tz)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
        for pattern, handler in routes:
            ctx.route(pattern, handler)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        if at is not None:
            page.clock.install(time=at)
        page.goto(server + path, wait_until="networkidle")
        if path.startswith("/display"):
            _until(page, MEDIA_READY_JS)
            # The editor button fades in on hover; keep it out of screenshots
            page.add_style_tag(content="#editor-btn { display: none !important; }")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


FRAME_JS = """id => {
  const slot = document.getElementById(`slot-${id}`);
  const cs = getComputedStyle(slot.querySelector('.mat-frame'));
  return {
    widths: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].map(parseFloat),
    image: cs.borderImageSource, slice: cs.borderImageSlice, repeat: cs.borderImageRepeat,
    color: cs.borderTopColor, mat: cs.backgroundColor,
    short: Math.min(slot.clientWidth, slot.clientHeight),
  };
}"""

RECT_JS = """sel => { const r = document.querySelector(sel).getBoundingClientRect();
  return { x: r.x, y: r.y, width: r.width, height: r.height }; }"""


def frame(page, slot_id) -> dict:
    return page.evaluate(FRAME_JS, slot_id)


def slot_ids(page) -> list[int]:
    return page.evaluate("() => [...document.querySelectorAll('.slot')].map(s => +s.id.slice(5))")


def rect(page, selector) -> dict:
    return page.evaluate(RECT_JS, selector)


def slot_shot(page, slot_id) -> Image.Image:
    png = page.screenshot(clip=rect(page, f"#slot-{slot_id}"), animations="disabled")
    return Image.open(io.BytesIO(png)).convert("RGB")


def asset(name) -> Image.Image:
    with Image.open(FRAMES_DIR / f"{name}.png") as img:
        return img.convert("RGB")


def mad(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference per channel (0-255)."""
    assert a.size == b.size, (a.size, b.size)
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def near(px, color, tol=12) -> bool:
    return all(abs(p - c) <= tol for p, c in zip(px, color))


def corner_mads(shot: Image.Image, src: Image.Image, w: int) -> list[float]:
    """MAD of each w x w corner of a framed slot against the asset's corner scaled to w x w."""
    n = src.width - S
    out = []
    for sx, sy, dx, dy in [(0, 0, 0, 0), (n, 0, shot.width - w, 0),
                           (0, n, 0, shot.height - w), (n, n, shot.width - w, shot.height - w)]:
        expected = src.crop((sx, sy, sx + S, sy + S))
        if w != S:
            expected = expected.resize((w, w), Image.LANCZOS)
        out.append(mad(shot.crop((dx, dy, dx + w, dy + w)), expected))
    return out


def expected_edge(src: Image.Image, side: str, length: int, w: int, repeat: str = "round") -> Image.Image:
    """An edge of a `w` px border-image frame, `length` px long between the corners, built the
    way `round` lays it out: the asset's edge tile scaled to `w` deep and repeated a whole
    number of times, each copy squeezed to fit (`stretch`: one copy, stretched)."""
    tile = src.width - 2 * S
    n = 1 if repeat == "stretch" else max(1, int(length / (tile * w / S) + 0.5))  # half up, like the browser
    crops = {"top": (S, 0, S + tile, S), "bottom": (S, S + tile, S + tile, src.width),
             "left": (0, S, S, S + tile), "right": (S + tile, S, src.width, S + tile)}
    piece = src.crop(crops[side])
    horizontal = side in ("top", "bottom")
    out = Image.new("RGB", (length, w) if horizontal else (w, length))
    for i in range(n):
        a, b = round(i * length / n), round((i + 1) * length / n)
        out.paste(piece.resize((b - a, w) if horizontal else (w, b - a), Image.LANCZOS),
                  (a, 0) if horizontal else (0, a))
    return out


def edge_mads(shot: Image.Image, src: Image.Image, w: int, repeat: str = "round") -> dict[str, float]:
    """MAD of each edge of a framed slot against the asset's edge tile laid out with `repeat`."""
    W, H = shot.size
    boxes = {"top": (w, 0, W - w, w), "bottom": (w, H - w, W - w, H),
             "left": (0, w, w, H - w), "right": (W - w, w, W, H - w)}
    return {side: mad(shot.crop(box), expected_edge(src, side, (W if side in ("top", "bottom") else H) - 2 * w, w, repeat))
            for side, box in boxes.items()}


def _best_period(line: list, lo: int, hi: int) -> tuple[int, float]:
    """The shift p in [lo, hi] at which `line` best repeats, and the MAD at that shift."""
    best = None
    for p in range(lo, hi + 1):
        pairs = list(zip(line, line[p:]))
        d = sum(abs(a[i] - b[i]) for a, b in pairs for i in range(3)) / (3 * len(pairs))
        if best is None or d < best[1]:
            best = (p, d)
    return best


def _no_errors(w) -> list[str]:
    return [e for e in w.errors if "Failed to load resource" not in e]


# --- Pixels: corners don't stretch, edges tile ---

def test_corners_match_the_asset_at_1_to_1(write_cfg, open_display):
    # hero-left's big slot (942 x 1056) is big enough that 96 px isn't clamped: 1:1 with the asset
    write_cfg({"frame_style": "wood", "frame_width": S}, layout="hero-left", slots=5)
    w = open_display()
    f = frame(w.page, 1)
    assert f["widths"] == [S] * 4
    assert "/assets/frames/wood.png" in f["image"]
    assert f["repeat"] == "round"
    mads = corner_mads(slot_shot(w.page, 1), asset("wood"), S)
    assert max(mads) <= 3, mads
    assert w.errors == []


@pytest.mark.parametrize("style", ALL_STYLES)
def test_corners_scale_uniformly_at_the_default_width(write_cfg, open_display, style):
    width = MANIFEST[style]["default_width"]
    write_cfg({"frame_style": style, "frame_width": width})
    w = open_display()
    for slot_id in slot_ids(w.page):
        assert frame(w.page, slot_id)["widths"] == [width] * 4
    shot = slot_shot(w.page, 1)
    mads = corner_mads(shot, asset(style), width)
    assert max(mads) <= 12, mads
    # The edges between them are the asset's edge tile, repeated with `round` (no seams), or for
    # barnwood one long board stretched to fit
    assert frame(w.page, 1)["repeat"] == MANIFEST[style]["repeat"]
    edges = edge_mads(shot, asset(style), width, MANIFEST[style]["repeat"])
    assert max(edges.values()) <= 12, edges
    assert w.errors == []


@pytest.mark.parametrize("style", ROUND_STYLES)
def test_edges_repeat_the_tile_rather_than_stretch(write_cfg, open_display, style):
    tile = MANIFEST[style]["tile"]
    fw = S * T // tile  # the width at which this style's edge tile is T px long on screen
    write_cfg({"frame_style": style, "frame_width": fw}, layout="hero-left", slots=5)
    w = open_display()
    shot = slot_shot(w.page, 1)
    W, H = shot.size
    v = fw // 2
    edges = {
        "top": [shot.getpixel((x, v)) for x in range(fw, W - fw)],
        "bottom": [shot.getpixel((x, H - 1 - v)) for x in range(fw, W - fw)],
        "left": [shot.getpixel((v, y)) for y in range(fw, H - fw)],
        "right": [shot.getpixel((W - 1 - v, y)) for y in range(fw, H - fw)],
    }
    # Only the grained woods have one clear period to find: gold's beads repeat every 24 px, and
    # the flat styles have almost no texture along the board
    if style == "wood":
        for side, line in edges.items():
            period, diff = _best_period(line, T // 2 + 10, T * 3 // 2)
            # The texture repeats about every tile length, a whole number of times along the edge
            assert diff <= 8, (side, period, diff)
            assert abs(len(line) / period - round(len(line) / period)) < 0.05, (side, len(line), period)
            assert abs(period - T) <= T / 2, (side, period)
    # And the first tile on the top edge is the asset's edge tile, squeezed only to fit
    n = round((W - 2 * fw) / T)
    tile_len = round((W - 2 * fw) / n)
    expected = asset(style).crop((S, 0, S + tile, S)).resize((tile_len, fw), Image.LANCZOS)
    assert mad(shot.crop((fw, 0, fw + tile_len, fw)), expected) <= 10
    # Every edge, tile after tile
    edges = edge_mads(shot, asset(style), fw)
    assert max(edges.values()) <= 10, edges
    assert w.errors == []


def _peak_autocorrelation(line: list[float], min_lag: int = 100) -> tuple[float, int]:
    """The strongest normalised autocorrelation of `line` at a lag of `min_lag` px or more (a
    repeated tile gives ~1.0 at its period), and that lag."""
    n = len(line)
    mean = sum(line) / n
    d = [x - mean for x in line]
    var = sum(x * x for x in d)
    return max((sum(d[i] * d[i + k] for i in range(n - k)) / var * n / (n - k), k) for k in range(min_lag, n // 2))


@pytest.mark.parametrize("width", [36, 48])
def test_barnwood_does_not_repeat_along_a_gallery_edge(write_cfg, open_display, width):
    """QA on PR #27: with a repeated 576 px tile the knot and cracks recurred about every 300 px
    along a 1920 px gallery edge (autocorrelation 1.0). Now each edge is one board, stretched."""
    write_cfg({"frame_style": "barnwood", "frame_width": 36}, layout="gallery-salon", slots=8,
              gallery={"frame_width": width})
    w = open_display()
    g = w.page.evaluate(GALLERY_FRAME_JS)
    assert g["style"] == "barnwood" and g["widths"] == [width] * 4 and g["repeat"] == "stretch"
    shot = Image.open(io.BytesIO(w.page.screenshot(clip=rect(w.page, ".gallery-frame"), animations="disabled"))).convert("RGB")
    # The edges are the asset's one board each, and the corners the asset's corners
    edges = edge_mads(shot, asset("barnwood"), width, "stretch")
    assert max(edges.values()) <= 12, edges
    assert max(corner_mads(shot, asset("barnwood"), width)) <= 12
    # No repeat along the long top edge: luminance down the middle of the moulding
    band = shot.convert("L").crop((width, width // 4, shot.width - width, 3 * width // 4))
    line = [sum(band.getpixel((x, y)) for y in range(band.height)) / band.height for x in range(band.width)]
    peak, lag = _peak_autocorrelation(line)
    assert peak < 0.6, (peak, lag)
    assert w.errors == []


@pytest.mark.parametrize("style", ["gold", *NEW_STYLES])
def test_small_slots_cap_the_moulding_and_keep_corners_square(write_cfg, open_display, style):
    write_cfg({"frame_style": style, "frame_width": 36, "mat_color": "#ff00ff", "mat_width": 6},
              layout="hero-left", slots=5, wall_color="#00ffff")
    w = open_display(viewport={"width": 800, "height": 450})
    widths = {}
    for slot_id in slot_ids(w.page):
        f = frame(w.page, slot_id)
        assert f"/assets/frames/{style}.png" in f["image"]
        assert len(set(f["widths"])) == 1
        widths[slot_id] = f["widths"][0]
        # Capped at a sixth of the slot's short side, never above the configured width
        assert widths[slot_id] == min(36, f["short"] // 6), (slot_id, f)
    assert widths[1] == 36          # the big slot keeps the configured width...
    assert widths[2] < 36           # ...the small ones (185 x 207) are capped
    shot = slot_shot(w.page, 2)
    assert max(corner_mads(shot, asset(style), int(widths[2]))) <= 14
    # The moulding is opaque: no wall color shows through anywhere inside the slot
    assert not any(near(px, CYAN, 20) for px in shot.getdata())
    assert w.errors == []


def test_frame_refits_when_the_window_is_resized(write_cfg, open_display):
    write_cfg({"frame_style": "wood", "frame_width": 80})
    w = open_display()
    assert frame(w.page, 1)["widths"][0] == 80  # 942 x 522 slot: cap 87
    w.page.set_viewport_size({"width": 960, "height": 540})  # 462 x 252 slots: cap 42
    _until(w.page, "() => parseFloat(getComputedStyle(document.querySelector('#slot-1 .mat-frame')).borderTopWidth) === 42")


# --- The mat shows around the photo ---

@pytest.mark.parametrize("fill_mode, folder", [("cover", "photos"), ("contain", "portrait")])
def test_mat_insets_the_photo(write_cfg, open_display, fill_mode, folder):
    """QA regression: the photo covered the mat — mat_width only ever showed as letterbox bars."""
    write_cfg({"frame_style": "wood", "frame_width": 28, "mat_color": "#ff00ff", "mat_width": 30},
              folder=folder, slot_overrides={i: {"fill_mode": fill_mode} for i in range(1, 5)})
    w = open_display()
    inset = 28 + 30
    slot = rect(w.page, "#slot-1")
    img = rect(w.page, "#slot-1 img.visible")
    assert img["x"] - slot["x"] == pytest.approx(inset, abs=0.5)
    assert img["y"] - slot["y"] == pytest.approx(inset, abs=0.5)
    assert slot["x"] + slot["width"] - (img["x"] + img["width"]) == pytest.approx(inset, abs=0.5)
    assert slot["y"] + slot["height"] - (img["y"] + img["height"]) == pytest.approx(inset, abs=0.5)
    shot = slot_shot(w.page, 1)
    W, H = shot.size
    # Just inside the moulding on every side: mat
    for px in [(28 + 4, H // 2), (W - 28 - 5, H // 2), (W // 2, 28 + 4), (W // 2, H - 28 - 5)]:
        assert near(shot.getpixel(px), MAGENTA), (px, shot.getpixel(px))
    center = shot.getpixel((W // 2, H // 2))
    assert any(near(center, c, 30) for c in PHOTO_COLORS), center
    if fill_mode == "contain":
        # A portrait photo in a landscape slot: letterbox bars show the mat color too
        assert near(shot.getpixel((inset + 10, H // 2)), MAGENTA)
    assert w.errors == []


# --- Where the style comes from: global, slot, time of day ---

def test_slot_frame_overrides_the_global_style(write_cfg, open_display):
    write_cfg({"frame_style": "gold", "frame_width": 36}, slot_overrides={2: {
        "use_global": {"frame": False},
        "frame": {"frame_style": "solid", "frame_color": "#00ff00", "frame_width": 12}}})
    w = open_display()
    for slot_id in (1, 3, 4):
        assert "/assets/frames/gold.png" in frame(w.page, slot_id)["image"]
    f = frame(w.page, 2)
    assert f["image"] == "none"
    assert f["widths"] == [12] * 4
    assert f["color"] == "rgb(0, 255, 0)"
    shot = slot_shot(w.page, 2)
    assert near(shot.getpixel((6, shot.height // 2)), GREEN)
    assert w.errors == []


def _tod(**day) -> dict:
    base = {"wall_color": "#1a1a1a", "mat_color": "#ff00ff", "mat_width": 20, "show_frame": True,
            "frame_color": "#6b4226", "frame_width": 28}
    return {"enabled": True, "day_start": "07:00", "night_start": "20:00",
            "day": {**base, **day}, "night": {**base, "mat_color": "#3a3a3a"}}


def test_time_of_day_template_and_frame_style(write_cfg, open_display):
    # No frame_style in the template: the global wood stays, with the template's mat
    write_cfg({"frame_style": "wood", "frame_width": 28}, time_of_day=_tod())
    w = open_display(at=NOON_UTC)
    f = frame(w.page, 1)
    assert "/assets/frames/wood.png" in f["image"]
    assert f["mat"] == "rgb(255, 0, 255)"
    # A template style overrides the global one
    write_cfg({"frame_style": "wood", "frame_width": 28}, time_of_day=_tod(frame_style="gold"))
    w = open_display(at=NOON_UTC)
    assert "/assets/frames/gold.png" in frame(w.page, 1)["image"]
    # A key the template leaves out keeps the global value (it was overwritten with undefined → 8 px)
    tod = _tod()
    del tod["day"]["frame_width"]
    write_cfg({"frame_style": "solid", "frame_width": 40}, time_of_day=tod)
    w = open_display(at=NOON_UTC)
    assert frame(w.page, 1)["widths"] == [40] * 4
    assert w.errors == []


def test_frame_style_change_applies_live_without_reload(write_cfg, open_display):
    cfg = write_cfg({"frame_style": "solid", "frame_width": 20})
    w = open_display()
    page = w.page
    page.evaluate("window.__marker = 1")
    cfg["frame_defaults"]["frame_style"] = "wood"
    write_cfg.path.write_text(json.dumps(cfg))
    _until(page, "() => getComputedStyle(document.querySelector('#slot-1 .mat-frame')).borderImageSource.includes('wood.png')", 6)
    cfg["frame_defaults"] |= {"frame_style": "solid", "frame_color": "#00ff00"}
    write_cfg.path.write_text(json.dumps(cfg))
    _until(page, "() => getComputedStyle(document.querySelector('#slot-1 .mat-frame')).borderTopColor === 'rgb(0, 255, 0)'", 6)
    for slot_id in slot_ids(page):
        f = frame(page, slot_id)
        assert f["image"] == "none" and f["widths"] == [20] * 4
    assert page.evaluate("window.__marker") == 1
    assert w.errors == []


def test_rotated_layout_gets_frames_fitted_to_its_slots(write_cfg, open_display):
    # Rotation day 2026-10-12 → classic-4; at 04:00 on the 13th it reloads into hero-left
    # (see test_rotation_browser.py for the day numbers)
    write_cfg({"frame_style": "wood", "frame_width": 80}, slots=5,
              layout_rotation={"enabled": True, "layouts": ["classic-4", "hero-left"], "change_at": "04:00"})
    w = open_display(at=dt.datetime(2026, 10, 13, 3, 59, 30, tzinfo=ZoneInfo("America/New_York")),
                     tz="America/New_York")
    page = w.page
    assert page.evaluate("document.getElementById('collage').dataset.layout") == "classic-4"
    assert all(frame(page, i)["widths"][0] == 80 for i in slot_ids(page))  # 942 x 522: cap 87
    page.clock.run_for(90_000)
    _until(page, "() => document.getElementById('collage')?.dataset.layout === 'hero-left' && document.querySelectorAll('.slot').length === 5")
    _until(page, MEDIA_READY_JS)
    assert frame(page, 1)["widths"][0] == 80   # 942 x 1056
    for slot_id in (2, 3, 4, 5):               # 465 x 522: cap 77
        f = frame(page, slot_id)
        assert f["widths"][0] == 77 and "/assets/frames/wood.png" in f["image"]
    assert w.errors == []


# --- Media types and failure modes ---

def test_video_slot_is_framed(write_cfg, open_display, browser):
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    probe = browser.new_page()
    try:
        if not probe.evaluate("document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')"):
            pytest.skip("browser cannot play H.264 — needs Chrome (channel='chrome')")
    finally:
        probe.close()
    videos = write_cfg.root / "videos"
    if not videos.exists():
        videos.mkdir()
        make_clip(videos / "clip.mp4", color="blue", duration=3, faststart=True)
    write_cfg({"frame_style": "wood", "frame_width": 28, "mat_width": 16},
              slot_overrides={1: {"mode": "video", "source_path": str(videos),
                                  "use_global": {"source": False}}})
    w = open_display()
    assert w.page.evaluate("document.getElementById('slot-1').classList.contains('video-mode')")
    assert "/assets/frames/wood.png" in frame(w.page, 1)["image"]
    slot, video = rect(w.page, "#slot-1"), rect(w.page, "#slot-1 video.visible")
    assert video["x"] - slot["x"] == pytest.approx(28 + 16, abs=0.5)
    assert video["y"] - slot["y"] == pytest.approx(28 + 16, abs=0.5)
    assert max(corner_mads(slot_shot(w.page, 1), asset("wood"), 28)) <= 12
    assert w.errors == []


def test_heic_photo_is_framed(write_cfg, open_display):
    write_cfg({"frame_style": "gold", "frame_width": 36, "mat_width": 10}, folder="heic")
    w = open_display()
    assert ".HEIC" in w.page.evaluate("decodeURIComponent(document.querySelector('#slot-1 img.visible').src)")
    assert "/assets/frames/gold.png" in frame(w.page, 1)["image"]
    shot = slot_shot(w.page, 1)
    assert near(shot.getpixel((shot.width // 2, shot.height // 2)), (40, 120, 200), 30)
    assert max(corner_mads(shot, asset("gold"), 36)) <= 12
    assert w.errors == []


@pytest.mark.parametrize("style", ["wood", *NEW_STYLES])
def test_missing_frames_js_falls_back_to_solid(write_cfg, open_display, style):
    requested = []

    def not_found(route):
        requested.append(route.request.url)
        route.fulfill(status=404, body="")

    write_cfg({"frame_style": style, "frame_width": 20, "frame_color": "#00ff00"})
    w = open_display(routes=[("**/assets/frames/frames.js", not_found)])
    assert requested, "the display never asked for frames.js"
    for slot_id in slot_ids(w.page):
        f = frame(w.page, slot_id)
        assert f["image"] == "none"
        assert f["widths"] == [20] * 4 and f["color"] == "rgb(0, 255, 0)"
    assert _no_errors(w) == []


# --- The new styles on a gallery mat and with time of day ---

GALLERY_FRAME_JS = """() => { const el = document.querySelector('.gallery-frame'); const cs = getComputedStyle(el);
  return { style: el.dataset.frameStyle, image: cs.borderImageSource, slice: cs.borderImageSlice,
           repeat: cs.borderImageRepeat, widths: [cs.borderTopWidth, cs.borderRightWidth,
           cs.borderBottomWidth, cs.borderLeftWidth].map(parseFloat) }; }"""


@pytest.mark.parametrize("style", NEW_STYLES)
def test_new_style_frames_the_gallery_mat(write_cfg, open_display, style):
    # The Frame section's style on the gallery's outer frame, at the gallery's own width (48)
    write_cfg({"frame_style": style, "frame_width": MANIFEST[style]["default_width"]},
              layout="gallery-salon", slots=8)
    w = open_display()
    g = w.page.evaluate(GALLERY_FRAME_JS)
    assert g["style"] == style and f"/assets/frames/{style}.png" in g["image"]
    assert g["widths"] == [48] * 4 and g["repeat"] == MANIFEST[style]["repeat"]
    shot = Image.open(io.BytesIO(w.page.screenshot(clip=rect(w.page, ".gallery-frame"), animations="disabled"))).convert("RGB")
    assert max(corner_mads(shot, asset(style), 48)) <= 12
    edges = edge_mads(shot, asset(style), 48, MANIFEST[style]["repeat"])
    assert max(edges.values()) <= 12, edges
    assert w.errors == []


@pytest.mark.parametrize("style", NEW_STYLES)
def test_time_of_day_switches_to_a_new_style_in_both_modes(write_cfg, open_display, style):
    night = dt.datetime(2026, 10, 13, 22, 0, tzinfo=dt.timezone.utc)
    tod = _tod()
    tod["night"]["frame_style"] = style
    write_cfg({"frame_style": "wood", "frame_width": 28}, layout="gallery-salon", slots=8, time_of_day=tod)
    w = open_display(at=night)
    assert w.page.evaluate(GALLERY_FRAME_JS)["style"] == style
    write_cfg({"frame_style": "wood", "frame_width": 28}, time_of_day=tod)
    w = open_display(at=night)
    f = frame(w.page, 1)
    assert f"/assets/frames/{style}.png" in f["image"] and f["widths"] == [28] * 4  # the period's width
    assert max(corner_mads(slot_shot(w.page, 1), asset(style), 28)) <= 12
    assert w.errors == []


# --- Editor ---

PREVIEW_IMAGES_JS = "() => [...document.querySelectorAll('.preview-slot')].map(el => getComputedStyle(el).borderImageSource)"


def _saved(write_cfg, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cfg = json.loads(write_cfg.path.read_text())
        if check(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never saved as expected: {write_cfg.path.read_text()}")


def test_editor_default_frame_style(write_cfg, open_display):
    write_cfg({"frame_style": "solid", "frame_width": 8})
    w = open_display("/editor/")
    page = w.page
    options = page.eval_on_selector_all("#g-frame-style option", "els => els.map(o => [o.value, o.textContent])")
    assert options == [["solid", "Solid color"], ["wood", "Wood"], ["gold", "Gold"], ["black", "Black gallery"],
                       ["silver", "Brushed silver"], ["white", "White modern"], ["barnwood", "Rustic barnwood"]]
    assert page.is_enabled("#g-frame-color")

    page.select_option("#g-frame-style", "wood")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"].get("frame_style") == "wood")
    assert cfg["frame_defaults"]["frame_width"] == 28  # a thin solid width jumps to the style's default
    assert page.input_value("#g-frame-width") == "28"
    assert page.is_disabled("#g-frame-color")
    assert page.is_visible("#g-frame-color-hint")
    previews = page.evaluate(PREVIEW_IMAGES_JS)
    assert len(previews) == 4 and all("/assets/frames/wood.png" in p for p in previews)

    page.select_option("#g-frame-style", "solid")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"].get("frame_style") == "solid")
    assert cfg["frame_defaults"]["frame_width"] == 8  # our own bump is undone (the width wasn't changed)
    assert page.input_value("#g-frame-width") == "8"
    assert page.is_enabled("#g-frame-color")
    assert all(p == "none" for p in page.evaluate(PREVIEW_IMAGES_JS))
    assert w.errors == []


DECORATIVE_OPTIONS = [["wood", "Wood"], ["gold", "Gold"], ["black", "Black gallery"], ["silver", "Brushed silver"],
                      ["white", "White modern"], ["barnwood", "Rustic barnwood"]]
OPTIONS_JS = "els => els.map(o => [o.value, o.textContent])"


def test_editor_pickers_list_the_new_styles(write_cfg, open_display):
    write_cfg({"frame_style": "solid", "frame_width": 8}, layout="gallery-salon", slots=8,
              gallery={"frame_override": True})
    w = open_display("/editor/")
    page = w.page
    solid = [["solid", "Solid color"]]
    # Frame section, the gallery's own frame, and each time-of-day period
    assert page.eval_on_selector_all("#g-frame-style option", OPTIONS_JS) == solid + DECORATIVE_OPTIONS
    assert page.eval_on_selector_all("#g-gal-frame-style option", OPTIONS_JS) == solid + DECORATIVE_OPTIONS
    page.click("#tod-configure-btn")
    tod = page.locator(".tod-modal-overlay .field").filter(has_text="Frame style").locator("select")
    assert tod.count() == 2
    for i in range(2):
        assert tod.nth(i).locator("option").evaluate_all(OPTIONS_JS) == [["", "Same as default"], *solid, *DECORATIVE_OPTIONS]
    assert w.errors == []


PREVIEW_FRAME_JS = """sel => { const cs = getComputedStyle(document.querySelector(sel));
  return { image: cs.borderImageSource, slice: cs.borderImageSlice, repeat: cs.borderImageRepeat,
           width: parseFloat(cs.borderTopWidth) }; }"""


@pytest.mark.parametrize("style", NEW_STYLES)
def test_editor_preview_matches_the_display(write_cfg, open_display, style):
    write_cfg({"frame_style": "solid", "frame_width": 8})
    w = open_display("/editor/")
    page = w.page
    page.select_option("#g-frame-style", style)
    width = MANIFEST[style]["default_width"]
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"].get("frame_style") == style)
    assert cfg["frame_defaults"]["frame_width"] == width  # a thin solid width jumps to the style's default
    assert page.input_value("#g-frame-width") == str(width)
    assert page.is_disabled("#g-frame-color")
    preview = page.evaluate(PREVIEW_FRAME_JS, ".preview-slot")
    display = open_display()
    shown = display.page.evaluate(PREVIEW_FRAME_JS, "#slot-1 .mat-frame")
    # Same asset, slice and tiling; the preview draws the display's width at its own scale
    assert f"/assets/frames/{style}.png" in shown["image"] and shown["width"] == width
    assert (preview["image"], preview["slice"], preview["repeat"]) == (shown["image"], shown["slice"], shown["repeat"])
    scale = page.evaluate("() => document.querySelector('.preview-slot').offsetWidth") / display.page.evaluate(
        "() => document.getElementById('slot-1').offsetWidth")
    assert preview["width"] == pytest.approx(width * scale, abs=1)
    assert w.errors == [] and display.errors == []


def test_editor_slot_frame_style(write_cfg, open_display):
    write_cfg({"frame_style": "solid", "frame_width": 8})
    w = open_display("/editor/")
    page = w.page
    page.locator(".preview-slot").nth(1).click()
    section = page.locator("#slot-panel-body .panel-section").filter(
        has=page.get_by_role("heading", name="Frame", exact=True))
    section.locator(".adopt-row .toggle").click()  # stop using the global frame
    style = section.locator(".field").filter(has_text="Frame style").locator("select")
    style.select_option("gold")
    cfg = _saved(write_cfg, lambda c: (c["slots"][1].get("frame") or {}).get("frame_style") == "gold")
    assert cfg["slots"][1]["use_global"]["frame"] is False
    assert cfg["slots"][1]["frame"]["frame_width"] == 36
    assert cfg["frame_defaults"]["frame_style"] == "solid"
    # The panel re-renders: override dot on the style, color disabled
    section = page.locator("#slot-panel-body .panel-section").filter(
        has=page.get_by_role("heading", name="Frame", exact=True))
    assert section.locator(".field").filter(has_text="Frame style").locator(".override-dot").count() == 1
    assert section.locator(".field").filter(has_text="Frame color").locator("input").is_disabled()
    previews = page.evaluate(PREVIEW_IMAGES_JS)
    assert "/assets/frames/gold.png" in previews[1]
    assert [p for i, p in enumerate(previews) if i != 1] == ["none"] * 3
    assert w.errors == []


def test_editor_time_of_day_frame_style(write_cfg, open_display):
    write_cfg({"frame_style": "wood", "frame_width": 28})
    w = open_display("/editor/")
    page = w.page
    page.click("#tod-configure-btn")
    day = page.locator(".tod-modal-overlay .field").filter(has_text="Frame style").first.locator("select")
    assert day.input_value() == ""  # "Same as default"
    day.select_option("gold")
    cfg = _saved(write_cfg, lambda c: (c.get("time_of_day") or {}).get("day", {}).get("frame_style") == "gold")
    assert cfg["time_of_day"]["day"]["frame_width"] == 36  # the template's 8 px default is bumped
    assert page.locator(".tod-modal-overlay .field").filter(has_text="Frame color").first.locator("input[type=color]").is_disabled()
    day.select_option("")
    cfg = _saved(write_cfg, lambda c: "frame_style" not in c["time_of_day"]["day"])
    assert "frame_style" not in cfg["time_of_day"]["night"]
    # The template's width applies to every style, so leaving gold undoes the automatic bump
    assert cfg["time_of_day"]["day"]["frame_width"] == 8
    assert w.errors == []


def test_editor_time_of_day_keeps_a_typed_width_when_leaving_gold(write_cfg, open_display):
    write_cfg({"frame_style": "wood", "frame_width": 28})
    w = open_display("/editor/")
    page = w.page
    page.click("#tod-configure-btn")
    fields = page.locator(".tod-modal-overlay .field")
    day = fields.filter(has_text="Frame style").first.locator("select")
    width = fields.filter(has_text="Frame width").first.locator("input")
    day.select_option("gold")
    width.fill("40")
    _saved(write_cfg, lambda c: (c.get("time_of_day") or {}).get("day", {}).get("frame_width") == 40)
    day.select_option("")
    cfg = _saved(write_cfg, lambda c: "frame_style" not in c["time_of_day"]["day"])
    assert cfg["time_of_day"]["day"]["frame_width"] == 40
    assert w.errors == []


def test_editor_frame_width_is_capped(write_cfg, open_display):
    write_cfg({"frame_style": "solid", "frame_width": 8})
    w = open_display("/editor/")
    page = w.page
    page.fill("#g-frame-width", "200")
    cfg = _saved(write_cfg, lambda c: c["frame_defaults"]["frame_width"] != 8)
    assert cfg["frame_defaults"]["frame_width"] == 80
    assert page.input_value("#g-frame-width") == "80"
    page.click("#tod-configure-btn")
    tod_width = page.locator(".tod-modal-overlay .field").filter(has_text="Frame width").first.locator("input")
    tod_width.fill("500")
    cfg = _saved(write_cfg, lambda c: (c.get("time_of_day") or {}).get("day", {}).get("frame_width") not in (None, 8))
    assert cfg["time_of_day"]["day"]["frame_width"] == 80
    assert w.errors == []
