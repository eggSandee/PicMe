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
    name: {k: int(v) for k, v in re.findall(r"(slice|tile|default_width): (\d+)", body)}
    for name, body in re.findall(r"^\s*(\w+): \{(.*?)\},?$", (FRAMES_DIR / "frames.js").read_text(), re.M)
}
S, T = MANIFEST["wood"]["slice"], MANIFEST["wood"]["tile"]
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


@pytest.mark.parametrize("style", ["wood", "gold"])
def test_corners_scale_uniformly_at_the_default_width(write_cfg, open_display, style):
    width = MANIFEST[style]["default_width"]
    write_cfg({"frame_style": style, "frame_width": width})
    w = open_display()
    for slot_id in slot_ids(w.page):
        assert frame(w.page, slot_id)["widths"] == [width] * 4
    mads = corner_mads(slot_shot(w.page, 1), asset(style), width)
    assert max(mads) <= 12, mads
    assert w.errors == []


def test_edges_repeat_the_tile_rather_than_stretch(write_cfg, open_display):
    write_cfg({"frame_style": "wood", "frame_width": S}, layout="hero-left", slots=5)
    w = open_display()
    shot = slot_shot(w.page, 1)
    W, H = shot.size
    v = S // 2
    edges = {
        "top": [shot.getpixel((x, v)) for x in range(S, W - S)],
        "bottom": [shot.getpixel((x, H - 1 - v)) for x in range(S, W - S)],
        "left": [shot.getpixel((v, y)) for y in range(S, H - S)],
        "right": [shot.getpixel((W - 1 - v, y)) for y in range(S, H - S)],
    }
    for side, line in edges.items():
        period, diff = _best_period(line, T // 2 + 10, T * 3 // 2)
        # The texture repeats about every tile length, a whole number of times along the edge
        assert diff <= 8, (side, period, diff)
        assert abs(len(line) / period - round(len(line) / period)) < 0.05, (side, len(line), period)
        assert abs(period - T) <= T / 2, (side, period)
    # And the first tile on the top edge is the asset's edge tile, squeezed only to fit
    n = round((W - 2 * S) / T)
    tile_len = round((W - 2 * S) / n)
    expected = asset("wood").crop((S, 0, S + T, S)).resize((tile_len, S), Image.LANCZOS)
    assert mad(shot.crop((S, 0, S + tile_len, S)), expected) <= 10
    assert w.errors == []


def test_small_slots_cap_the_moulding_and_keep_corners_square(write_cfg, open_display):
    write_cfg({"frame_style": "gold", "frame_width": 36, "mat_color": "#ff00ff", "mat_width": 6},
              layout="hero-left", slots=5, wall_color="#00ffff")
    w = open_display(viewport={"width": 800, "height": 450})
    widths = {}
    for slot_id in slot_ids(w.page):
        f = frame(w.page, slot_id)
        assert "/assets/frames/gold.png" in f["image"]
        assert len(set(f["widths"])) == 1
        widths[slot_id] = f["widths"][0]
        # Capped at a sixth of the slot's short side, never above the configured width
        assert widths[slot_id] == min(36, f["short"] // 6), (slot_id, f)
    assert widths[1] == 36          # the big slot keeps the configured width...
    assert widths[2] < 36           # ...the small ones (185 x 207) are capped
    shot = slot_shot(w.page, 2)
    assert max(corner_mads(shot, asset("gold"), int(widths[2]))) <= 14
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


def test_missing_frames_js_falls_back_to_solid(write_cfg, open_display):
    requested = []

    def not_found(route):
        requested.append(route.request.url)
        route.fulfill(status=404, body="")

    write_cfg({"frame_style": "wood", "frame_width": 20, "frame_color": "#00ff00"})
    w = open_display(routes=[("**/assets/frames/frames.js", not_found)])
    assert requested, "the display never asked for frames.js"
    for slot_id in slot_ids(w.page):
        f = frame(w.page, slot_id)
        assert f["image"] == "none"
        assert f["widths"] == [20] * 4 and f["color"] == "rgb(0, 255, 0)"
    assert _no_errors(w) == []


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
    assert options == [["solid", "Solid color"], ["wood", "Wood"], ["gold", "Gold"]]
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
    assert page.locator(".tod-modal-overlay .field").filter(has_text="Frame color").first.locator("input").is_disabled()
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
