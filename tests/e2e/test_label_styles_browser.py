"""Browser end-to-end tests for metadata label styles (README → Metadata label styles).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides ``e2e_config_path``.
Photos carry date and camera EXIF (no GPS); the location comes through a Playwright route on
/api/metadata, set to the editor's sample place so the editor preview and the display show the
same text. Gallery mode is ``gallery-salon`` (a heart, a circle and a hexagon among rectangles);
separate frames are ``hero-left``.
"""
from __future__ import annotations

import json
import re
import time

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401

STYLES = ["caption", "placard", "whisper"]
LOCATION = "Lake Tahoe, California"  # PicMeLabels.SAMPLE.location
FIELDS = {"show_date": True, "show_location": True, "show_camera": True, "show_fstop": True}
# Priority order of the non-date fields (the display's METADATA_FIELDS)
ORDER = ["location", "camera", "megapixels", "fstop", "shutter_speed", "iso", "focal_length"]
LIGHT, DARK, MID = "#f4f1ea", "#1b1b1b", "#8a8478"
MIN_TEXT_PX = 16
MODES = {"gallery": "gallery-salon", "slots": "hero-left"}


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-labels")
    (root / "photos").mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40), (120, 40, 160),
                               (230, 230, 230), (20, 20, 20), (90, 140, 200)]):
        _make_image(root / "photos" / f"photo_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                    exif=_exif_bytes(date=True, camera=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "photos"), indent=2))
    return path


@pytest.fixture
def write_cfg(e2e_config_path):
    """Writes a label-style config (photos never cycle) and restores the original after."""
    original = e2e_config_path.read_text()
    root = e2e_config_path.parent

    def _write(style="pill", mode="gallery", mat=LIGHT, meta=None, **extra) -> dict:
        layout = MODES[mode]
        cfg = make_config(root / "photos", layout=layout, slots=8 if mode == "gallery" else 5, cycle_seconds=300)
        cfg["label_style"] = style
        cfg["max_metadata_pills"] = None
        cfg["metadata_defaults"] = {**FIELDS, **(meta or {})}
        cfg["frame_defaults"] = {"mat_color": mat, "mat_width": 24, "show_frame": True, "frame_color": "#2c2318",
                                 "frame_width": 12, "frame_style": "solid"}
        cfg["gallery"] = {"mat_color": mat, "frame_width": 40}
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    yield _write
    e2e_config_path.write_text(original)


def _until(page, js, timeout=10.0, arg=None):
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


def with_location(route):
    response = route.fetch()
    data = response.json()
    if "date_iso" in data and "duration" not in data:
        data["location"] = LOCATION
    route.fulfill(response=response, json=data)


LABELS_READY_JS = """() => { const slots = [...document.querySelectorAll('.slot')];
  return slots.length > 0 && slots.every(s => s.querySelector('.slot-overlay.label-visible .lbl')); }"""
PILLS_READY_JS = """() => { const slots = [...document.querySelectorAll('.slot')];
  return slots.length > 0 && slots.every(s => s.querySelector('.overlay-pill.visible')); }"""


@pytest.fixture
def open_page(browser, server):
    contexts = []

    def _open(path="/display/", viewport=VIEWPORT, ready=LABELS_READY_JS, editor_mode="advanced") -> PageWatcher:
        ctx = browser.new_context(viewport=viewport, device_scale_factor=1)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        ctx.route("**/api/metadata*", with_location)
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.goto(server + path, wait_until="networkidle")
        if ready:
            _until(page, ready)
            page.evaluate("document.fonts.ready.then(() => true)")
            page.wait_for_timeout(300)  # the refit after the serif loads
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


# Every slot's label and what it must stay clear of, in page px
GEOMETRY_JS = """() => {
  const box = el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, r: r.right, b: r.bottom }; };
  const inset = (el, cs = getComputedStyle(el)) => { const r = box(el);
    return { x: r.x + parseFloat(cs.borderLeftWidth), y: r.y + parseFloat(cs.borderTopWidth),
             r: r.r - parseFloat(cs.borderRightWidth), b: r.b - parseFloat(cs.borderBottomWidth) }; };
  const gallery = !!document.querySelector('#collage.gallery');
  const matEl = gallery ? document.querySelector('.gallery-mat') : null;
  return [...document.querySelectorAll('.slot')].map(slot => {
    const ov = slot.querySelector('.slot-overlay');
    const root = ov.querySelector('.lbl');
    const mf = slot.querySelector('.mat-frame');
    const img = slot.querySelector('.mat-frame img.visible');
    const items = [...root.querySelectorAll('.lbl-item')].map(i => { const cs = getComputedStyle(i);
      return { key: i.dataset.key, font: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight),
               color: cs.color, family: cs.fontFamily, italic: cs.fontStyle === 'italic', width: i.getBoundingClientRect().width }; });
    const out = {
      id: slot.id, style: ov.dataset.labelStyle, where: ov.dataset.labelWhere, label: box(root), items,
      hidden: Number(ov.dataset.hidden || 0), photo: gallery ? box(mf) : box(img),
      frameInside: gallery ? inset(matEl) : inset(mf),
      matColor: getComputedStyle(gallery ? matEl : mf).backgroundColor,
      cardColor: getComputedStyle(root).backgroundColor,
      scrim: !!mf.querySelector('.lbl-scrim'),
      scrimImage: mf.querySelector('.lbl-scrim') ? getComputedStyle(mf.querySelector('.lbl-scrim')).backgroundImage : '',
      lift: ov.classList.contains('label-lift'),
    };
    if (gallery) {
      out.box = box(slot.querySelector('.opening-box'));
      out.shape = slot.dataset.shape;
      const s = PicMeShapes.get(out.shape).safe(mf.clientWidth, mf.clientHeight), m = box(mf);
      out.safe = { x: m.x + s.x, y: m.y + s.y, r: m.x + s.x + s.w, b: m.y + s.y + s.h };
    }
    return out;
  });
}"""


def geometry(page):
    return page.evaluate(GEOMETRY_JS)


def inside(a, b, tol=0.6):
    return a["x"] >= b["x"] - tol and a["y"] >= b["y"] - tol and a["r"] <= b["r"] + tol and a["b"] <= b["b"] + tol


def overlaps(a, b, tol=0.5):
    return a["x"] < b["r"] - tol and b["x"] < a["r"] - tol and a["y"] < b["b"] - tol and b["y"] < a["b"] - tol


def rgb(css: str) -> tuple[float, float, float]:
    return tuple(float(v) for v in re.findall(r"[\d.]+", css)[:3])


def luminance(c):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(rgb(a)), luminance(rgb(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def visible_keys(info):
    return [i["key"] for i in info["items"]]


# --- Each style in both modes, on light and dark mats ---

@pytest.mark.parametrize("mat", [LIGHT, DARK], ids=["light-mat", "dark-mat"])
@pytest.mark.parametrize("mode", ["gallery", "slots"])
@pytest.mark.parametrize("style", STYLES)
def test_style_places_legible_labels_clear_of_neighbours_frames_and_bevels(write_cfg, open_page, style, mode, mat):
    write_cfg(style=style, mode=mode, mat=mat)
    page = open_page().page
    info = geometry(page)
    assert len(info) == (8 if mode == "gallery" else 5)
    for s in info:
        assert s["style"] == style, s
        # The date is never hidden
        assert "date" in visible_keys(s), s
        # Legibility: every visible field at least MIN_TEXT_PX and a regular weight
        for item in s["items"]:
            assert item["font"] >= MIN_TEXT_PX - 0.01, (s["id"], item)
            assert item["weight"] >= 400, (s["id"], item)
        # Inside the frame (never on the moulding)
        assert inside(s["label"], s["frameInside"]), s
        if style == "whisper":
            assert s["where"] == "photo"
            assert inside(s["label"], s["photo"]), s  # on the photo, inside the bevel
            if mode == "gallery":
                assert inside(s["label"], s["safe"]), s  # inside a heart's, circle's, hexagon's safe area
            assert s["scrim"] and "linear-gradient" in s["scrimImage"] and "radial" not in s["scrimImage"], s
        else:
            assert s["where"] == "mat"
            # Under the photo (in gallery mode, under the opening and its bevel), never on it
            assert s["label"]["y"] >= (s["box"] if mode == "gallery" else s["photo"])["b"] - 0.5, s
            # Left-aligned to the photo's left edge
            assert abs(s["label"]["x"] - s["photo"]["x"]) <= 1.5, s
            assert not s["scrim"]
    # Clear of every other slot's label and photo (and, in a gallery, every other opening)
    for a in info:
        for b in info:
            if a is b:
                continue
            assert not overlaps(a["label"], b["label"]), (a["id"], b["id"])
            assert not overlaps(a["label"], b["photo"]), (a["id"], b["id"])
            if mode == "gallery":
                assert not overlaps(a["label"], b["box"]), (a["id"], b["id"])


@pytest.mark.parametrize("mat", [LIGHT, DARK, MID], ids=["light-mat", "dark-mat", "mid-mat"])
@pytest.mark.parametrize("mode", ["gallery", "slots"])
@pytest.mark.parametrize("style", ["caption", "placard"])
def test_mat_styles_contrast_at_least_4_5(write_cfg, open_page, style, mode, mat):
    write_cfg(style=style, mode=mode, mat=mat)
    info = geometry(open_page().page)
    for s in info:
        # The caption is ink on the mat; the placard is ink on its card
        bg = s["matColor"] if style == "caption" else s["cardColor"]
        for item in s["items"]:
            assert contrast(item["color"], bg) >= 4.5, (s["id"], item, bg)
        if style == "caption":
            dark_ink = luminance(rgb(s["items"][0]["color"])) < luminance(rgb(s["matColor"]))
            assert dark_ink == (mat == LIGHT) or mat == MID, s
            if mat == MID:
                assert s["lift"], s  # mid-tone mat: pure ink, heavier


@pytest.mark.parametrize("mode", ["gallery", "slots"])
def test_caption_and_placard_typography(write_cfg, open_page, mode):
    write_cfg(style="caption", mode=mode)
    page = open_page().page
    s = geometry(page)[0]
    date = next(i for i in s["items"] if i["key"] == "date")
    rest = [i for i in s["items"] if i["key"] != "date"]
    assert "PicMe Serif" in date["family"] and date["italic"]
    assert rest and all(not i["italic"] and i["weight"] >= 400 and abs(i["font"] - date["font"]) < 0.5 for i in rest)
    assert page.evaluate("document.fonts.check('16px \"PicMe Serif\"')")
    assert page.evaluate("[...document.fonts].some(f => f.family.includes('PicMe Serif') && f.status === 'loaded')")

    write_cfg(style="placard", mode=mode)
    page = open_page().page
    card = page.evaluate("""() => { const r = document.querySelector('#slot-1 .lbl-placard'); const cs = getComputedStyle(r);
      const t = getComputedStyle(r.querySelector('.lbl-title')), d = getComputedStyle(r.querySelector('.lbl-details'));
      return { shadow: cs.boxShadow, border: parseFloat(cs.borderTopWidth), title: parseFloat(t.fontSize),
               details: parseFloat(d.fontSize), dWeight: parseInt(d.fontWeight), tWeight: parseInt(t.fontWeight),
               titleText: r.querySelector('.lbl-title').textContent }; }""")
    assert card["shadow"] == "none" and card["border"] >= 1
    assert card["titleText"] == LOCATION
    assert card["details"] >= 0.85 * card["title"] and card["dWeight"] > card["tWeight"]


@pytest.mark.parametrize("style", STYLES)
def test_fit_hides_lowest_priority_fields_never_the_date(write_cfg, open_page, style):
    every = {f"show_{k}": True for k in ORDER}
    write_cfg(style=style, mode="gallery", meta=every)
    page = open_page(viewport={"width": 1280, "height": 720}).page
    info = geometry(page)
    # The fields the photos have, in priority order (location comes from the route)
    path = page.evaluate("decodeURIComponent(document.querySelector('#slot-1 .mat-frame img.visible').src.split('path=')[1])")
    meta = page.evaluate("p => fetch('/api/metadata?path=' + encodeURIComponent(p)).then(r => r.json())", path)
    available = [k for k in ORDER if meta.get(k)]
    assert len(available) >= 5
    assert any(s["hidden"] > 0 for s in info), "nothing needed hiding: the test proves nothing"
    for s in info:
        keys = visible_keys(s)
        assert "date" in keys
        rest = [k for k in ORDER if k in keys]
        # Kept fields are the highest-priority ones: a prefix of the priority order
        assert rest == available[:len(rest)], (s["id"], keys)
        assert len(rest) + s["hidden"] == len(available), s


def test_max_metadata_pills_and_show_metadata_still_apply(write_cfg, open_page):
    write_cfg(style="caption", mode="slots", max_metadata_pills=1)
    page = open_page().page
    assert {tuple(visible_keys(s)) for s in geometry(page)} == {("date", "location")}
    write_cfg(style="caption", mode="slots", show_metadata=False)
    page = open_page(ready=None).page
    page.wait_for_timeout(1500)
    assert page.evaluate("document.querySelectorAll('.lbl, .overlay-pill').length") == 0


@pytest.mark.parametrize("style,position,where", [("placard", "photo", "mat"), ("caption", "photo", "mat"),
                                                   ("whisper", "mat", "photo")])
def test_the_style_decides_where_labels_go_over_label_position(write_cfg, open_page, style, position, where):
    write_cfg(style=style, mode="slots", label_position=position)
    page = open_page().page
    assert {s["where"] for s in geometry(page)} == {where}
    assert page.evaluate("document.getElementById('collage').dataset.labels") == where


def test_unknown_style_is_pills(write_cfg, open_page):
    write_cfg(style="neon", mode="slots")
    page = open_page(ready=PILLS_READY_JS).page
    assert page.evaluate("document.querySelectorAll('.lbl').length") == 0


def test_one_size_per_screen(write_cfg, open_page):
    write_cfg(style="caption", mode="gallery")
    page = open_page().page
    sizes = page.evaluate("[...document.querySelectorAll('.slot-overlay.label-styled')].map(o => o.style.getPropertyValue('--label-font-size'))")
    assert len(sizes) == 8 and len(set(sizes)) == 1


# --- Editor ---

def _saved(write_cfg, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cfg = json.loads(write_cfg.path.read_text())
        if check(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never saved as expected: {write_cfg.path.read_text()}")


def test_editor_picker_switches_the_display_live_without_a_reload(write_cfg, open_page):
    write_cfg(style="pill", mode="gallery")
    display = open_page(ready=PILLS_READY_JS).page
    display.evaluate("window.__noReload = 1")
    editor = open_page("/editor/", ready=None, editor_mode="basic").page
    _until(editor, "() => document.querySelectorAll('#g-label-style .label-style-option').length === 4")
    # Every option's preview is the display's renderer (Basic mode shows the picker)
    assert editor.evaluate("""() => ['caption', 'placard', 'whisper'].every(s =>
      document.querySelector(`#g-label-style [data-style=${s}] .lbl-${s}`)) && !!document.querySelector('#g-label-style [data-style=pill] .swatch-pill')""")
    assert editor.is_visible("#g-label-style")
    for style in ["placard", "whisper", "caption"]:
        editor.click(f"#g-label-style [data-style={style}]")
        _saved(write_cfg, lambda c, s=style: c.get("label_style") == s)
        _until(display, f"() => [...document.querySelectorAll('.slot')].every(s => s.querySelector('.lbl-{style}'))", timeout=10)
        assert editor.evaluate("document.getElementById('g-label-position').disabled")
    editor.click("#g-label-style [data-style=pill]")
    _saved(write_cfg, lambda c: c.get("label_style") == "pill")
    _until(display, PILLS_READY_JS, timeout=10)
    assert display.evaluate("document.querySelectorAll('.lbl, .lbl-scrim').length") == 0
    assert not editor.evaluate("document.getElementById('g-label-position').disabled")
    assert display.evaluate("window.__noReload") == 1


PREVIEW_JS = """sel => [...document.querySelectorAll(sel)].map(o => {
  const box = o.querySelector('.opening-box').getBoundingClientRect();
  const lbl = o.querySelector('.lbl'); const k = box.width / o.querySelector('.opening-box').offsetWidth;
  if (!lbl) return null;
  const r = lbl.getBoundingClientRect();
  const date = getComputedStyle(lbl.querySelector('[data-key=date]'));
  return { dx: (r.x - box.x) / k, dy: (r.y - box.bottom) / k, w: r.width / k, keys: [...lbl.querySelectorAll('.lbl-item')].map(i => i.dataset.key),
           font: parseFloat(date.fontSize), family: date.fontFamily, italic: date.fontStyle, style: o.querySelector('.slot-overlay').dataset.labelStyle };
})"""


@pytest.mark.parametrize("style", STYLES)
def test_editor_preview_matches_the_display(write_cfg, open_page, style):
    write_cfg(style=style, mode="gallery")
    display = open_page().page
    shown = display.evaluate(PREVIEW_JS, "#collage .opening")
    editor = open_page("/editor/", ready=None).page
    _until(editor, "() => document.querySelectorAll('#collage-preview .opening .lbl').length === 8")
    editor.evaluate("document.fonts.ready.then(() => true)")
    editor.wait_for_timeout(300)
    preview = editor.evaluate(PREVIEW_JS, "#collage-preview .opening")
    assert len(shown) == len(preview) == 8
    for d, p in zip(shown, preview):
        assert d["style"] == p["style"] == style
        assert d["keys"] == p["keys"]
        assert d["font"] == p["font"] and d["family"] == p["family"] and d["italic"] == p["italic"]
        assert abs(d["dx"] - p["dx"]) <= 2 and abs(d["dy"] - p["dy"]) <= 2 and abs(d["w"] - p["w"]) <= 3, (d, p)
    # The picker's swatch uses the same type as the display
    swatch = editor.evaluate(f"""() => {{ const d = document.querySelector('#g-label-style [data-style={style}] [data-key=date]');
      const cs = getComputedStyle(d); return {{ family: cs.fontFamily, italic: cs.fontStyle }}; }}""")
    assert swatch == {"family": shown[0]["family"], "italic": shown[0]["italic"]}


def test_editor_separate_frame_preview_draws_the_style(write_cfg, open_page):
    write_cfg(style="placard", mode="slots")
    editor = open_page("/editor/", ready=None).page
    _until(editor, "() => document.querySelectorAll('.preview-slot .lbl-placard').length === 5")
    # On the mat under each photo, from its left edge
    assert editor.evaluate("""() => [...document.querySelectorAll('.preview-slot')].every(c => {
      const inner = [...c.children].find(e => e.textContent.trim().length && !e.classList.contains('slot-overlay'));
      const r = c.querySelector('.lbl').getBoundingClientRect(), p = inner.getBoundingClientRect();
      return r.top >= p.bottom - 0.5 && Math.abs(r.left - p.left) <= 1; })""")


# --- Caption ink on any mat (QA: saturated mid-tones) ---

SATURATED = ["#0f87a5", "#008787", "#0078e1"]


def test_caption_ink_contrast_sweep_over_mat_colours(write_cfg, open_page):
    """PicMeLabels.inkFor over a 17-step RGB grid (4,913 mats) and the QA's saturated mid-tones:
    the ink it picks is at least 4.5:1 against every one (contrast computed here, not by the module)."""
    write_cfg(style="caption", mode="slots")
    page = open_page().page
    steps = [round(i * 255 / 16) for i in range(17)]
    mats = [f"#{r:02x}{g:02x}{b:02x}" for r in steps for g in steps for b in steps] + SATURATED
    inks = page.evaluate("mats => mats.map(m => PicMeLabels.inkFor(m).ink)", mats)
    worst = sorted((contrast(_css(ink), _css(mat)), mat, ink) for mat, ink in zip(mats, inks))[:5]
    assert worst[0][0] >= 4.5, worst


def _css(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(h[i:i + 2], 16) for i in (0, 2, 4)))


@pytest.mark.parametrize("mat", SATURATED)
@pytest.mark.parametrize("mode", ["gallery", "slots"])
def test_caption_on_saturated_mid_tone_mats_reads_at_4_5(write_cfg, open_page, mode, mat):
    write_cfg(style="caption", mode=mode, mat=mat)
    for s in geometry(open_page().page):
        for item in s["items"]:
            assert contrast(item["color"], s["matColor"]) >= 4.5, (s["id"], item, s["matColor"])
