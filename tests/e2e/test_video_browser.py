"""Browser end-to-end tests for video slots: live uvicorn + Playwright (Chrome) at 1920x1080.

Reuses the server/browser plumbing from ``test_browser.py``; this module only
overrides ``e2e_config_path`` so the server reads a config whose slots point at
generated ffmpeg clips. Each test rewrites that config before opening pages.
Skipped if ffmpeg is missing or the browser can't play H.264 (bundled Chromium).
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import _exif_bytes, _make_image, make_config
from video_fixtures import FFMPEG, HAS_LIBX265, h264_stub, make_clip

def make_video_config(*args, **kwargs) -> dict:
    """A config with Live Photo clips off: they're shown by default, and these tests check that
    off hides them. Hidden clips also aren't converted, so they don't hold up the iPhone clip."""
    return {**make_config(*args, **kwargs), "include_live_photos": False}


pytestmark = pytest.mark.e2e

if FFMPEG is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)

pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import browser, open_page, server  # noqa: E402,F401

CLIP_DATE = "2021-06-15T12:00:00Z"  # midday UTC — same calendar date in any server timezone
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan"]

# Records, in the page, every `ended` event and every sample where two slots show the same clip.
# `ended` doesn't bubble, but a capturing listener on document still sees it.
RECORDER = """
window.__ended = [];
window.__shown = [];
window.__dupes = [];
document.addEventListener('ended', e => {
  const slot = e.target.closest('.slot');
  window.__ended.push({ slot: slot && slot.id, src: e.target.getAttribute('src') });
}, true);
setInterval(() => {
  const srcs = [...document.querySelectorAll('.slot.video-mode video.visible')]
    .map(v => v.getAttribute('src')).filter(Boolean);
  for (const s of srcs) if (!window.__shown.includes(s)) window.__shown.push(s);
  if (new Set(srcs).size !== srcs.length) window.__dupes.push(srcs);
}, 50);
"""

SLOTS_JS = """() => [...document.querySelectorAll('.slot')].map(s => {
  const v = s.querySelector('video.visible');
  return {
    id: s.id,
    video: s.classList.contains('video-mode'),
    src: v && v.getAttribute('src'),
    paused: v ? v.paused : null,
    muted: v ? v.muted : null,
    time: v ? v.currentTime : null,
    pills: [...s.querySelectorAll('.overlay-pill')].map(p => p.textContent),
  };
})"""


@pytest.fixture(scope="module")
def video_lib(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-video")
    lib = {name: root / name for name in ("photos", "videos", "four", "mixed", "iphone")}
    for d in lib.values():
        d.mkdir()
    # GPS-free photos so photo slots never try reverse geocoding
    for i in range(4):
        _make_image(lib["photos"] / f"photo_{i}.jpg", fmt="JPEG", color=(40 * i, 80, 160),
                    exif=_exif_bytes(date=True))
    for i, color in enumerate(COLORS):
        ext = ".mov" if i % 2 else ".mp4"
        make_clip(lib["videos"] / f"clip_{i}{ext}", color=color, creation_time=CLIP_DATE, faststart=True)
    for i, color in enumerate(COLORS[:4]):
        make_clip(lib["four"] / f"four_{i}.mp4", color=color, faststart=True)
    make_clip(lib["mixed"] / "good.mp4", color="green", faststart=True)
    make_clip(lib["mixed"] / "hevc.mov", codec="hevc", color="blue", faststart=True)
    if HAS_LIBX265:
        # iPhone-style: 10-bit HLG HEVC portrait clip, plus a Live Photo pair that stays hidden (include_live_photos is off here)
        make_clip(lib["iphone"] / "IMG_0001.MOV", hdr="hlg", size=(320, 180), rotation=90, quadrants=True,
                  duration=2)
        make_clip(lib["iphone"] / "IMG_0002.MOV", codec="hevc", color="red", duration=2)
        _make_image(lib["iphone"] / "IMG_0002.JPG", fmt="JPEG", exif=_exif_bytes(date=True))
    # Looks like plain H.264 to the server (so it's listed as-is) but can't be decoded
    (lib["mixed"] / "corrupt.mp4").write_bytes(h264_stub(4096))
    return lib


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory, video_lib):
    path = tmp_path_factory.mktemp("e2e-video-config") / "config.json"
    path.write_text(json.dumps(make_video_config(video_lib["photos"])))
    return path


@pytest.fixture(scope="module")
def h264_browser(browser):
    """The browser, provided it can actually decode H.264 MP4 (bundled Chromium can't)."""
    page = browser.new_page()
    try:
        support = page.evaluate("document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')")
    finally:
        page.close()
    if not support:
        pytest.skip("browser cannot play H.264 — needs Chrome (channel='chrome')")
    return browser


def write_config(path, photos, video_folder, video_slots, loops=1, **extra) -> dict:
    cfg = make_video_config(photos)
    for slot in cfg["slots"]:
        if slot["id"] in video_slots:
            slot.update(mode="video", source_path=str(video_folder), video_loops=loops)
    cfg.update(extra)
    path.write_text(json.dumps(cfg, indent=2))
    return cfg


def open_display(open_page, server):
    """Open the display with the in-page recorder installed before any app script runs."""
    w = open_page("about:blank")
    w.page.add_init_script(RECORDER)
    w.warnings = []
    w.page.on("console", lambda m: m.type == "warning" and w.warnings.append(m.text))
    w.page.goto(f"{server}/display/", wait_until="networkidle")
    return w


def wait_video_slots_playing(page, count, timeout=15_000):
    page.wait_for_function(
        """n => [...document.querySelectorAll('.slot.video-mode video.visible')]
                 .filter(v => v.getAttribute('src') && !v.paused && v.currentTime > 0).length === n""",
        arg=count, timeout=timeout,
    )


def test_video_slot_plays_muted_and_advances_after_loops(h264_browser, server, open_page, e2e_config_path, video_lib):
    loops = 2
    write_config(e2e_config_path, video_lib["photos"], video_lib["videos"], video_slots={1}, loops=loops,
                 date_format="iso", metadata_defaults={"show_date": True, "show_location": True})
    w = open_display(open_page, server)
    page = w.page
    wait_video_slots_playing(page, 1)

    slots = {s["id"]: s for s in page.evaluate(SLOTS_JS)}
    video = slots["slot-1"]
    assert video["video"] and "/api/video?path=" in video["src"]
    assert video["muted"] is True
    assert all(not s["video"] for sid, s in slots.items() if sid != "slot-1")
    # Video slots show the clip's container date only — never location etc.
    page.wait_for_function("() => document.querySelector('#slot-1 .overlay-pill')", timeout=5_000)
    assert page.evaluate(SLOTS_JS)[0]["pills"] == ["2021-06-15"]

    # Advance through at least two more clips (~1s each, played `loops` times)
    page.wait_for_function(
        "() => { const e = window.__ended.filter(x => x.slot === 'slot-1'); "
        "return new Set(e.map(x => x.src)).size >= 3; }",
        timeout=20_000,
    )
    ended = [e["src"] for e in page.evaluate("window.__ended") if e["slot"] == "slot-1"]
    runs: list[list] = []  # consecutive `ended` events per clip
    for src in ended:
        if runs and runs[-1][0] == src:
            runs[-1][1] += 1
        else:
            runs.append([src, 1])
    finished = runs[:-1]  # the last clip may still be mid-loop
    assert len(finished) >= 2
    assert [n for _, n in finished] == [loops] * len(finished), runs
    # Each advance picks a different clip
    assert all(a[0] != b[0] for a, b in zip(runs, runs[1:]))
    assert page.evaluate(SLOTS_JS)[0]["muted"] is True
    assert w.errors == []


def test_video_slots_never_show_the_same_clip(h264_browser, server, open_page, e2e_config_path, video_lib):
    """QA blocker regression: 3 video slots on a 4-clip folder must never show a clip twice."""
    write_config(e2e_config_path, video_lib["photos"], video_lib["four"], video_slots={1, 2, 3}, loops=1)
    w = open_display(open_page, server)
    page = w.page

    # Startup is where concurrent slots raced for the same clip — exercise it repeatedly
    for _ in range(5):
        wait_video_slots_playing(page, 3)
        page.wait_for_timeout(1500)
        assert page.evaluate("window.__dupes") == []
        page.reload(wait_until="networkidle")

    wait_video_slots_playing(page, 3)
    page.wait_for_timeout(15_000)
    assert page.evaluate("window.__dupes") == []
    # Not vacuous: slots really advanced and rotated through the whole folder
    ended = page.evaluate("window.__ended")
    assert len(ended) >= 9, ended
    assert len(page.evaluate("window.__shown")) == 4
    srcs = [s["src"] for s in page.evaluate(SLOTS_JS) if s["video"]]
    assert len(srcs) == 3 and len(set(srcs)) == 3
    assert w.errors == []


def test_unplayable_clip_is_skipped(h264_browser, server, open_page, e2e_config_path, video_lib, record_property):
    write_config(e2e_config_path, video_lib["photos"], video_lib["mixed"], video_slots={1}, loops=1)
    w = open_display(open_page, server)
    page = w.page
    wait_video_slots_playing(page, 1)
    # Wait until the slot has tried (and given up on) the corrupt clip
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not any("corrupt.mp4" in m for m in w.warnings):
        page.wait_for_timeout(200)
    assert any("failed to play" in m and "corrupt.mp4" in m for m in w.warnings), w.warnings

    page.wait_for_timeout(3000)
    shown = page.evaluate("window.__shown")
    assert not any("corrupt.mp4" in s for s in shown)
    # The slot is still playing a real clip after skipping it
    wait_video_slots_playing(page, 1, timeout=5_000)
    current = page.evaluate(SLOTS_JS)[0]["src"]
    assert "good.mp4" in current or "hevc.mov" in current
    # Chrome on macOS may decode HEVC via VideoToolbox — informational only
    hevc_played = any("hevc.mov" in s for s in shown)
    hevc_skipped = any("hevc.mov" in m for m in w.warnings)
    record_property("hevc", "played" if hevc_played else "skipped" if hevc_skipped else "not tried")
    print(f"HEVC in this Chrome: played={hevc_played} skipped={hevc_skipped}")
    assert w.errors == []


def test_show_metadata_false_clears_video_date_pill(h264_browser, server, open_page, e2e_config_path, video_lib):
    """QA regression: turning show_metadata off left the pills already on screen."""
    cfg = write_config(e2e_config_path, video_lib["photos"], video_lib["videos"], video_slots={1}, loops=50,
                       show_metadata=True, metadata_defaults={"show_date": True})
    w = open_display(open_page, server)
    page = w.page
    wait_video_slots_playing(page, 1)
    page.wait_for_function("() => document.querySelector('#slot-1 .overlay-pill.visible')", timeout=5_000)

    e2e_config_path.write_text(json.dumps(cfg | {"show_metadata": False}))
    page.wait_for_function("() => !document.querySelector('#slot-1 .overlay-pill')", timeout=5_000)
    assert w.errors == []


LOOPS_INPUT = "#slot-panel-body .field:has(label:text-is('Play each video (times)')) input"
NO_VIDEOS = "#slot-panel-body >> text=/This folder.* has no videos/"


def test_relisting_another_folder_keeps_this_folders_failures(h264_browser, server, open_page, e2e_config_path,
                                                              video_lib):
    write_config(e2e_config_path, video_lib["photos"], video_lib["mixed"], video_slots={1}, loops=1)
    w = open_display(open_page, server)
    page = w.page
    wait_video_slots_playing(page, 1)
    page.wait_for_function("() => [...unplayablePaths].some(p => p.endsWith('corrupt.mp4'))", timeout=15_000)
    # Another folder's queue is re-listed (a second video slot, or the 5-minute refresh)
    still_marked = page.evaluate("""async folder => {
        const source = { folder, recursive: true, key: `r:${folder}` };
        await ensureQueue(source, 'video');
        folderQueues[`video:${source.key}`].stale = true;
        await ensureQueue(source, 'video');
        return [...unplayablePaths].some(p => p.endsWith('corrupt.mp4'));
    }""", str(video_lib["videos"]))
    assert still_marked
    assert w.errors == []


def test_editor_video_mode_controls(h264_browser, server, open_page, e2e_config_path, video_lib):
    photos, videos = str(video_lib["photos"]), str(video_lib["videos"])
    cfg = make_video_config(photos)
    cfg["source_path"] = photos  # slots adopt the global folder
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    def saved_slot1(key, value):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if json.loads(e2e_config_path.read_text())["slots"][0].get(key) == value:
                return True
            time.sleep(0.1)
        return False

    def set_global_folder(path):
        page.locator("#g-source-path").evaluate(
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }", path)

    w = open_page(f"{server}/editor/")
    page = w.page
    page.locator("#collage-preview .preview-slot").first.click()
    assert page.locator(LOOPS_INPUT).count() == 0

    page.locator("#slot-panel-body select:has(option[value='video'])").select_option("video")
    assert saved_slot1("mode", "video")
    assert page.locator("#collage-preview .preview-slot").first.locator("text=▶").count() == 1

    # Global folder (photos only) has no videos → warning; switching the global folder clears it
    page.locator(NO_VIDEOS).wait_for(state="visible", timeout=5_000)
    set_global_folder(videos)
    page.locator(NO_VIDEOS).wait_for(state="hidden", timeout=5_000)
    set_global_folder(photos)
    page.locator(NO_VIDEOS).wait_for(state="visible", timeout=5_000)

    loops = page.locator(LOOPS_INPUT)
    assert loops.input_value() == "2"
    loops.fill("3")
    loops.press("Tab")
    assert saved_slot1("video_loops", 3)
    for bad in ["1e3", "2.5", "0", "-1", ""]:
        loops.fill(bad)
        loops.press("Tab")
        assert loops.input_value() == "3", bad
    page.wait_for_timeout(800)  # past the 500ms autosave debounce
    assert json.loads(e2e_config_path.read_text())["slots"][0]["video_loops"] == 3

    page.locator("#slot-panel-body select:has(option[value='video'])").select_option("photos")
    assert saved_slot1("mode", "photos")
    assert page.locator(LOOPS_INPUT).count() == 0
    assert w.errors == []


def test_editor_no_videos_hint_counts_subfolders(h264_browser, server, open_page, e2e_config_path, video_lib, tmp_path):
    # A video slot on its own folder whose only clip sits in a subfolder
    own = tmp_path / "own"
    (own / "2024").mkdir(parents=True)
    (own / "2024" / "clip.mp4").write_bytes((video_lib["mixed"] / "good.mp4").read_bytes())
    cfg = write_config(e2e_config_path, video_lib["photos"], own, video_slots={1})
    cfg["slots"][0]["use_global"] = {"source": False, "metadata": True, "frame": True}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    w = open_page(f"{server}/editor/")
    page = w.page
    page.locator("#collage-preview .preview-slot").first.click()
    page.wait_for_timeout(800)   # past the 300 ms check debounce
    assert not page.locator(NO_VIDEOS).is_visible()
    # Without subfolders the folder has nothing to play
    page.locator("#slot-panel-body .toggle-row").filter(has_text="Include subfolders").locator(".toggle").click()
    page.locator("#slot-panel-body >> text=This folder has no videos").wait_for(state="visible", timeout=5_000)
    deadline = time.monotonic() + 5
    while json.loads(e2e_config_path.read_text())["slots"][0].get("include_subfolders") is not False:
        assert time.monotonic() < deadline, "include_subfolders never saved"
        time.sleep(0.1)
    assert w.errors == []


def test_typing_a_video_slot_folder_queues_no_conversion(server, open_page, e2e_config_path, video_lib, tmp_path):
    if not HAS_LIBX265:
        pytest.skip("ffmpeg without libx265")
    # A folder whose clip needs converting, reached while typing but never committed
    typed = tmp_path / "typed-iphone"
    typed.mkdir()
    make_clip(typed / "IMG_0900.MOV", codec="hevc", color="blue", size=(320, 180), duration=1)
    cfg = write_config(e2e_config_path, video_lib["photos"], video_lib["videos"], video_slots={1})
    cfg["slots"][0]["use_global"] = {"source": False, "metadata": True, "frame": True}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    w = open_page(f"{server}/editor/")
    page = w.page
    checks = []
    page.on("request", lambda r: ("/api/images/list" in r.url or "/api/videos/status" in r.url)
            and str(typed).replace("/", "%2F") in r.url and checks.append(r.url))
    page.locator("#collage-preview .preview-slot").first.click()
    field = page.locator("#slot-panel-body .path-row input")
    field.fill("")
    field.type(str(typed), delay=20)
    page.wait_for_timeout(4_000)  # the hint has checked the folder, and a queued clip would be converting
    assert checks and all("peek=1" in u for u in checks), checks
    status = page.evaluate("f => fetch('/api/videos/status?peek=1&folder=' + encodeURIComponent(f)).then(r => r.json())",
                           str(typed))
    assert (status["pending"], status["ready"], status["converting"]) == (1, 0, None), status
    assert json.loads(e2e_config_path.read_text())["slots"][0]["source_path"] == str(video_lib["videos"])
    assert w.errors == []


VIDEO_STATUS = "#slot-panel-body >> text=/videos? ready/"


def test_iphone_hevc_clip_is_converted_and_plays_upright(h264_browser, server, open_page, e2e_config_path, video_lib):
    if not HAS_LIBX265:
        pytest.skip("ffmpeg without libx265")
    folder = video_lib["iphone"]
    write_config(e2e_config_path, video_lib["photos"], folder, video_slots={1}, loops=1)

    # Editor: conversion status for the slot's folder (the Live Photo clip stays hidden)
    w = open_page(f"{server}/editor/")
    w.page.locator("#collage-preview .preview-slot").first.click()
    status = w.page.locator(VIDEO_STATUS)
    status.wait_for(state="visible", timeout=10_000)
    w.page.wait_for_function(
        "() => /1 of 1 videos? ready/.test(document.querySelector('#slot-panel-body').innerText)", timeout=60_000)
    assert "1 Live Photo clip hidden" in status.inner_text()
    assert w.errors == []

    # Display: the converted copy plays, in portrait, through /api/video?path=<original>
    w = open_display(open_page, server)
    wait_video_slots_playing(w.page, 1, timeout=30_000)
    info = w.page.evaluate("""() => {
      const v = document.querySelector('.slot.video-mode video.visible');
      return { src: v.getAttribute('src'), w: v.videoWidth, h: v.videoHeight, ready: v.readyState };
    }""")
    assert "IMG_0001.MOV" in info["src"], info
    assert info["h"] > info["w"] and info["ready"] >= 3, info
    assert not any("IMG_0002" in s for s in w.page.evaluate("window.__shown"))
    assert w.errors == []


def test_video_slot_reloads_once_for_a_folder_with_clips_but_not_for_one_without(
        h264_browser, server, open_page, e2e_config_path, video_lib):
    cfg = write_config(e2e_config_path, video_lib["photos"], video_lib["four"], video_slots={1}, loops=5)
    w = open_display(open_page, server)
    page = w.page
    wait_video_slots_playing(page, 1)
    page.evaluate("window.__marker = 1")

    # A folder with no videos (e.g. a path still being typed): the slot keeps playing, no reload
    cfg["slots"][0]["source_path"] = str(video_lib["photos"])
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    page.wait_for_function(f"() => config.slots[0].source_path === {json.dumps(str(video_lib['photos']))}",
                           timeout=8_000)
    assert page.evaluate("window.__marker") == 1
    assert "four_" in page.evaluate(SLOTS_JS)[0]["src"]

    # A folder with clips: one reload into it
    cfg["slots"][0]["source_path"] = str(video_lib["videos"])
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    page.wait_for_function("() => window.__marker === undefined", timeout=8_000)
    wait_video_slots_playing(page, 1)
    assert "clip_" in page.evaluate(SLOTS_JS)[0]["src"]
    assert w.errors == []
