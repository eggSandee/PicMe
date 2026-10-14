"""Browser end-to-end tests: live uvicorn + Playwright (Chrome) at 1920x1080.

The server runs in a subprocess with ``main.CONFIG_PATH`` pointed at a temp
config and every slot pointed at a generated folder of valid, GPS-free images
(so the display never reaches out for reverse geocoding). The whole module is
skipped if Playwright or a Chromium-family browser can't launch.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import piexif
import pytest

from conftest import BACKEND_DIR, LANDSCAPE, PORTRAIT, _exif_bytes, _make_heic, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

VIEWPORT = {"width": 1920, "height": 1080}
LAYOUT = "classic-4"
SLOT_COUNT = 4
NEW_WALL_COLOR = "#336699"
NEW_WALL_RGB = "rgb(51, 102, 153)"

# Run the real app with CONFIG_PATH patched to the temp config. argv: config, port.
SERVER_SCRIPT = """
import sys
from pathlib import Path
import uvicorn
import main
main.CONFIG_PATH = Path(sys.argv[1])
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, proc: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"uvicorn did not come up at {url}")


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e")
    photos = root / "photos"
    photos.mkdir()
    colors = [(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40), (120, 40, 160), (220, 220, 220)]
    for i, color in enumerate(colors):
        size = PORTRAIT if i % 2 else LANDSCAPE
        _make_image(photos / f"photo_{i}.jpg", size=size, fmt="JPEG", color=color, exif=_exif_bytes(date=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(photos, layout=LAYOUT, slots=SLOT_COUNT), indent=2))
    return path


@pytest.fixture(scope="module")
def server(e2e_config_path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", SERVER_SCRIPT, str(e2e_config_path), str(port)],
        cwd=BACKEND_DIR,
        # The live server converts iPhone (HEVC) clips in the background like a real install
        env={**os.environ, "PICME_TRANSCODE_AUTOSTART": "1"},
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base}/api/config", proc)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Playwright could not start: {e}")
    launched = None
    errors = []
    for kwargs in ({"channel": "chrome"}, {}):
        try:
            launched = pw.chromium.launch(headless=True, **kwargs)
            break
        except Exception as e:  # try bundled Chromium next
            errors.append(f"{kwargs or 'bundled'}: {e}")
    if launched is None:
        pw.stop()
        pytest.skip("Chrome could not launch: " + " | ".join(errors))
    yield launched
    launched.close()
    pw.stop()


class PageWatcher:
    """Collects uncaught page errors and console errors for a page."""

    def __init__(self, page):
        self.page = page
        self.errors: list[str] = []
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("console", self._on_console)

    def _on_console(self, msg) -> None:
        if msg.type != "error":
            return
        url = msg.location.get("url", "")
        # Chrome requests /favicon.ico on its own; PicMe ships none.
        if url.endswith("/favicon.ico"):
            return
        self.errors.append(f"console: {msg.text} ({url})")


@pytest.fixture
def open_page(browser):
    contexts = []

    def _open(url: str, editor_mode: str | None = "advanced") -> PageWatcher:
        ctx = browser.new_context(viewport=VIEWPORT)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        page.goto(url, wait_until="networkidle")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def _visible_slot_images(page) -> int:
    """Number of slots whose visible <img> has actually decoded."""
    return page.evaluate(
        """() => [...document.querySelectorAll('.slot')].filter(s => {
                 const img = s.querySelector('img.visible');
                 return img && img.complete && img.naturalWidth > 0;
             }).length"""
    )


def test_display_loads_without_errors(server, open_page):
    w = open_page(f"{server}/display/")
    w.page.wait_for_timeout(1000)
    assert w.errors == []


def test_editor_loads_without_errors(server, open_page):
    w = open_page(f"{server}/editor/")
    w.page.wait_for_timeout(1000)
    assert w.errors == []


def test_display_renders_configured_slots_with_images(server, open_page):
    w = open_page(f"{server}/display/")
    page = w.page
    assert page.locator(".slot").count() == SLOT_COUNT
    page.wait_for_function(
        f"""() => [...document.querySelectorAll('.slot')].filter(s => {{
                 const img = s.querySelector('img.visible');
                 return img && img.complete && img.naturalWidth > 0;
             }}).length === {SLOT_COUNT}""",
        timeout=10_000,
    )
    assert _visible_slot_images(page) == SLOT_COUNT
    srcs = page.eval_on_selector_all(".slot img.visible", "els => els.map(e => e.src)")
    assert all("/api/image?path=" in s for s in srcs)
    assert w.errors == []


def test_editor_change_reaches_open_display(server, open_page, e2e_config_path):
    original = e2e_config_path.read_text()
    display = open_page(f"{server}/display/")
    collage = display.page.locator("#collage")
    assert collage.evaluate("el => getComputedStyle(el).backgroundColor") != NEW_WALL_RGB

    editor = open_page(f"{server}/editor/")
    try:
        editor.page.locator("#g-wall-color").evaluate(
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }",
            NEW_WALL_COLOR,
        )
        started = time.monotonic()
        # Editor autosaves after 500ms; display polls /api/config every 3s.
        display.page.wait_for_function(
            "rgb => getComputedStyle(document.getElementById('collage')).backgroundColor === rgb",
            arg=NEW_WALL_RGB,
            timeout=6_000,
        )
        assert time.monotonic() - started < 6
        assert json.loads(e2e_config_path.read_text())["wall_color"] == NEW_WALL_COLOR
        assert display.errors == []
        assert editor.errors == []
    finally:
        e2e_config_path.write_text(original)


def test_chrome_shows_iphone_heic(server, open_page, tmp_path):
    """Chrome can't decode HEIC itself; /api/image must hand it a JPEG it can render upright."""
    orientation_6 = piexif.dump({"0th": {piexif.ImageIFD.Orientation: 6}, "Exif": {}, "GPS": {},
                                 "1st": {}, "thumbnail": None})
    heic = _make_heic(tmp_path / "IMG_0001.HEIC", size=LANDSCAPE, exif=orientation_6)
    w = open_page(f"{server}/display/")
    dims = w.page.evaluate(
        """src => new Promise((resolve, reject) => {
               const img = new Image();
               img.onload = () => resolve([img.naturalWidth, img.naturalHeight]);
               img.onerror = () => reject(new Error('HEIC did not render'));
               img.src = src;
           })""",
        f"{server}/api/image?path={urllib.parse.quote(str(heic))}",
    )
    assert tuple(dims) == PORTRAIT


# --- Fresh install: no config.json yet ---

@pytest.fixture(scope="module")
def fresh_install(tmp_path_factory):
    """A server started like run.sh (cwd = install dir, photos in ./photos) with no config.json."""
    root = tmp_path_factory.mktemp("fresh")
    photos = root / "photos"
    photos.mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40), (120, 40, 160)]):
        _make_image(photos / f"photo_{i}.jpg", fmt="JPEG", color=color)
    config = root / "config.json"
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", SERVER_SCRIPT, str(config), str(port)],
        cwd=root,  # the default source_path "photos" is relative to the server's cwd
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR), "PICME_TRANSCODE_AUTOSTART": "0"},
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base}/api/layouts", proc)  # not /api/config — that writes the file
        yield base, config
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _wait_all_slots_show_images(page, timeout=10_000):
    page.wait_for_function(
        f"""() => [...document.querySelectorAll('.slot')].filter(s => {{
                 const img = s.querySelector('img.visible');
                 return img && img.complete && img.naturalWidth > 0;
             }}).length === {SLOT_COUNT}""",
        timeout=timeout,
    )


def test_fresh_install_shows_photos(fresh_install, open_page):
    """QA regression: with no config.json the display threw at config.slots[0] and stayed black."""
    base, config = fresh_install
    config.unlink(missing_ok=True)
    w = open_page(f"{base}/display/")
    page = w.page
    assert page.locator(".slot").count() == SLOT_COUNT
    _wait_all_slots_show_images(page)
    saved = json.loads(config.read_text())
    assert saved["layout"] == "classic-4" and len(saved["slots"]) == SLOT_COUNT
    # The first config poll patches in place rather than reloading the page
    page.evaluate("window.__marker = 1")
    page.wait_for_timeout(4000)
    assert page.evaluate("window.__marker") == 1
    assert w.errors == []


def test_display_copes_with_emptied_config(fresh_install, open_page):
    base, config = fresh_install
    config.write_text("{}")
    w = open_page(f"{base}/display/")
    page = w.page
    assert page.locator(".slot").count() == SLOT_COUNT
    _wait_all_slots_show_images(page)
    page.wait_for_timeout(3500)  # one config poll against the slot-less config
    assert config.read_text() == "{}"
    assert w.errors == []


# --- show_metadata kill switch ---

@pytest.fixture
def restore_config(e2e_config_path):
    original = e2e_config_path.read_text()
    yield e2e_config_path
    e2e_config_path.write_text(original)


def _write_config(path, folder, cycle_seconds=30, layout=LAYOUT, slots=SLOT_COUNT, **extra) -> dict:
    cfg = make_config(folder, layout=layout, slots=slots, cycle_seconds=cycle_seconds) | extra
    path.write_text(json.dumps(cfg, indent=2))
    return cfg


PILLS_PER_SLOT_JS = "() => [...document.querySelectorAll('.slot')].map(s => s.querySelectorAll('.overlay-pill').length)"


def test_show_metadata_false_clears_pills_on_screen(server, open_page, restore_config):
    """QA regression: turning show_metadata off left the pills already on screen."""
    folder = json.loads(restore_config.read_text())["slots"][0]["source_path"]
    cfg = _write_config(restore_config, folder, cycle_seconds=300, show_metadata=True,
                        metadata_defaults={"show_date": True})
    w = open_page(f"{server}/display/")
    page = w.page
    page.wait_for_function(
        f"() => document.querySelectorAll('.slot .overlay-pill.visible').length === {SLOT_COUNT}", timeout=10_000)

    restore_config.write_text(json.dumps(cfg | {"show_metadata": False}))
    page.wait_for_function("() => document.querySelectorAll('.overlay-pill').length === 0", timeout=5_000)

    # Back on: pills return through the same live-update path
    restore_config.write_text(json.dumps(cfg))
    page.wait_for_function(
        f"() => document.querySelectorAll('.slot .overlay-pill.visible').length === {SLOT_COUNT}", timeout=5_000)
    assert w.errors == []


# Holds every /api/metadata request until window.__releaseMetadata() is called
HOLD_METADATA = """
window.__held = [];
const realFetch = window.fetch;
window.fetch = (url, ...rest) => String(url).includes('/api/metadata')
  ? new Promise(resolve => window.__held.push(resolve)).then(() => realFetch(url, ...rest))
  : realFetch(url, ...rest);
window.__releaseMetadata = () => window.__held.splice(0).forEach(resolve => resolve());
"""


def test_metadata_fetch_in_flight_does_not_bring_pills_back(server, open_page, restore_config):
    folder = json.loads(restore_config.read_text())["slots"][0]["source_path"]
    cfg = _write_config(restore_config, folder, cycle_seconds=300, show_metadata=True,
                        metadata_defaults={"show_date": True})
    w = open_page("about:blank")
    page = w.page
    page.add_init_script(HOLD_METADATA)
    page.goto(f"{server}/display/", wait_until="networkidle")
    _wait_all_slots_show_images(page)
    page.wait_for_function(f"() => window.__held.length >= {SLOT_COUNT}", timeout=5_000)

    restore_config.write_text(json.dumps(cfg | {"show_metadata": False}))
    page.wait_for_function("() => config.show_metadata === false", timeout=5_000)
    page.evaluate("window.__releaseMetadata()")
    page.wait_for_timeout(1500)
    assert page.evaluate(PILLS_PER_SLOT_JS) == [0] * SLOT_COUNT
    assert w.errors == []


# --- Photos that won't load ---

# Math.random() just under 1 turns the display's shuffle() into the identity, so the queue
# keeps the order /api/images/list returns
NO_SHUFFLE = "Math.random = () => 0.9999;"


def _corrupt_first(route):
    resp = route.fetch()
    data = resp.json()
    data["images"].sort(key=lambda p: (not os.path.basename(p).startswith("corrupt"), p))
    route.fulfill(response=resp, json=data)


def _make_corrupt(folder, names):
    for name in names:
        (folder / name).write_bytes(b"not an image at all" * 64)


def _open_display_watching(open_page, server, init_scripts=(), corrupt_first=False, clock=False):
    w = open_page("about:blank")
    page = w.page
    for script in init_scripts:
        page.add_init_script(script)
    if clock:
        page.clock.install()
    if corrupt_first:
        page.route("**/api/images/list*", _corrupt_first)
    w.warnings = []
    page.on("console", lambda m: m.type == "warning" and w.warnings.append(m.text))
    w.image_requests = []
    page.on("request", lambda r: "/api/image?" in r.url and w.image_requests.append(r.url))
    page.goto(f"{server}/display/", wait_until="networkidle")
    return w


def _visible_files(page) -> list[str | None]:
    """File name each slot shows, or None for a blank slot."""
    srcs = page.evaluate("""() => [...document.querySelectorAll('.slot')].map(s => {
        const img = s.querySelector('img.visible');
        return img && img.complete && img.naturalWidth > 0 ? decodeURIComponent(img.src) : null;
    })""")
    return [os.path.basename(s) if s else None for s in srcs]


def _unexpected_errors(w) -> list[str]:
    # Chrome logs its own console error for the 415 an undecodable HEIC gets — that one is expected
    return [e for e in w.errors if "status of 415" not in e]


def test_corrupt_first_photo_is_skipped_immediately(server, open_page, restore_config, tmp_path):
    """QA regression: a slot whose first pick failed to load stayed blank until its next cycle."""
    for i in range(4):
        _make_image(tmp_path / f"good_{i}.jpg", fmt="JPEG", color=(60 * i, 100, 160))
    _make_corrupt(tmp_path, ["corrupt_a.jpg", "corrupt_b.heic"])  # the HEIC gets a 415
    _write_config(restore_config, tmp_path, cycle_seconds=300)  # the cycle timer can't rescue a slot
    w = _open_display_watching(open_page, server, init_scripts=[NO_SHUFFLE], corrupt_first=True)
    page = w.page
    _wait_all_slots_show_images(page, timeout=5_000)
    assert not any(f.startswith("corrupt") for f in _visible_files(page))
    for name in ("corrupt_a.jpg", "corrupt_b.heic"):
        assert any("failed to load" in m and name in m for m in w.warnings), w.warnings
    assert _unexpected_errors(w) == []


def test_slots_repeat_the_only_good_photo_rather_than_stay_blank(server, open_page, restore_config, tmp_path):
    _make_image(tmp_path / "good.jpg", fmt="JPEG")
    _make_corrupt(tmp_path, ["corrupt_a.jpg", "corrupt_b.png", "corrupt_c.heic"])
    _write_config(restore_config, tmp_path, cycle_seconds=300)
    w = _open_display_watching(open_page, server)
    page = w.page
    _wait_all_slots_show_images(page, timeout=5_000)
    assert _visible_files(page) == ["good.jpg"] * SLOT_COUNT
    assert _unexpected_errors(w) == []


def test_folder_of_only_bad_photos_does_not_spin(server, open_page, restore_config, tmp_path):
    names = ["corrupt_a.jpg", "corrupt_b.png", "corrupt_c.heic"]
    _make_corrupt(tmp_path, names)
    _write_config(restore_config, tmp_path, cycle_seconds=1)  # cycle ticks must not retry bad files
    w = _open_display_watching(open_page, server)
    page = w.page
    page.wait_for_timeout(5000)
    assert len(w.image_requests) <= len(names) * SLOT_COUNT, w.image_requests
    assert page.evaluate("[...unplayablePaths].length") == len(names)
    # The blank slots wait on the folder's backoff timer
    assert page.evaluate("folder => !!photoRetryTimers[folder]", str(tmp_path))
    assert _visible_files(page) == [None] * SLOT_COUNT
    assert _unexpected_errors(w) == []


def test_blank_slot_retries_after_backoff_and_finds_new_photo(server, open_page, restore_config, tmp_path):
    _make_corrupt(tmp_path, ["corrupt_a.jpg", "corrupt_b.jpg"])
    _write_config(restore_config, tmp_path, cycle_seconds=300)
    w = _open_display_watching(open_page, server, clock=True)
    page = w.page
    page.clock.run_for(2000)
    assert _visible_files(page) == [None] * SLOT_COUNT

    _make_image(tmp_path / "added.jpg", fmt="JPEG")
    page.clock.run_for(61_000)  # past the 60s backoff: re-list the folder and try again
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and None in _visible_files(page):
        page.wait_for_timeout(100)
    assert _visible_files(page) == ["added.jpg"] * SLOT_COUNT
    assert _unexpected_errors(w) == []


# --- No duplicate photos across slots ---
#
# How to measure "the same photo in two slots": each slot keeps two <img>s and crossfades
# between them. The outgoing one keeps its src under .hidden and fades out over 0.8s, so a
# sampler that reads both <img>s, or anything with opacity > 0, reports a "duplicate" whenever
# another slot fades that photo in. That is a crossfade, not a duplicate. Sample only each
# slot's img.visible that has decoded (complete && naturalWidth > 0), as _visible_files does,
# or read the claims directly: displayedPaths (path -> number of slots holding it) and
# slotState[id].currentPath. SAMPLE_SLOTS below does both from inside the page every 25ms, so
# it also catches the short window while new photos are still loading.

SAMPLE_SLOTS = """
window.__samples = [];
setInterval(() => {
  try {
    const claims = Math.max(0, ...displayedPaths.values());
    const visible = [...document.querySelectorAll('.slot')].map(s => {
      const img = s.querySelector('img.visible');
      return img && img.complete && img.naturalWidth > 0 ? decodeURIComponent(img.src).split('/').pop() : null;
    });
    window.__samples.push({claims, visible});
  } catch (e) { /* display script not loaded yet */ }
}, 25);
"""


def _slow_images(route):
    # Holds each photo for a moment, so every slot's load is still in flight when the others
    # pick on the same cycle tick (as on a TV over Wi-Fi), instead of depending on timing luck
    time.sleep(0.15)
    route.continue_()


def _lockstep_dup_run(open_page, server, restore_config, tmp_path, run_ms, **extra):
    for i in range(10):
        _make_image(tmp_path / f"p{i}.jpg", fmt="JPEG", color=(25 * i, 255 - 25 * i, 120))
    cfg = _write_config(restore_config, tmp_path, cycle_seconds=3, layout="classic-6", slots=6)
    for s in cfg["slots"]:
        s.update(extra)
    restore_config.write_text(json.dumps(cfg, indent=2))
    w = open_page("about:blank")
    page = w.page
    page.add_init_script(SAMPLE_SLOTS)
    page.route("**/api/image?*", _slow_images)
    page.goto(f"{server}/display/")
    page.wait_for_function(
        "() => document.querySelectorAll('.slot img.visible').length === 6 && "
        "[...document.querySelectorAll('.slot img.visible')].every(i => i.complete && i.naturalWidth > 0)",
        timeout=10_000)
    page.evaluate("window.__samples = []")
    page.wait_for_timeout(run_ms)
    samples = page.evaluate("window.__samples")
    assert len(samples) > run_ms / 100
    return w, samples


def _assert_no_duplicates(samples, min_distinct):
    claimed_twice = [s for s in samples if s["claims"] > 1]
    shown_twice = [s["visible"] for s in samples
                   if len([f for f in s["visible"] if f]) != len({f for f in s["visible"] if f})]
    assert claimed_twice == [], f"{len(claimed_twice)}/{len(samples)} samples had a path claimed by 2 slots"
    assert shown_twice == [], f"{len(shown_twice)}/{len(samples)} samples showed a photo twice: {shown_twice[:3]}"
    # No slot is starved into keeping one photo forever
    per_slot = [{s["visible"][i] for s in samples} - {None} for i in range(6)]
    assert all(len(files) >= min_distinct for files in per_slot), per_slot


def test_no_duplicate_photos_when_all_slots_tick_together(server, open_page, restore_config, tmp_path):
    """QA regression: 6 slots, 10 photos, 3s cycle showed one photo in two slots for whole cycles.

    Six slots ticking at once with only 4 free photos left the last two slots to repeat a
    photo another slot was showing or switching to."""
    w, samples = _lockstep_dup_run(open_page, server, restore_config, tmp_path, run_ms=13_000)
    _assert_no_duplicates(samples, min_distinct=3)
    assert _unexpected_errors(w) == []


def test_no_duplicate_photos_when_a_group_ticks_together(server, open_page, restore_config, tmp_path):
    w, samples = _lockstep_dup_run(open_page, server, restore_config, tmp_path, run_ms=7_000, group="a")
    _assert_no_duplicates(samples, min_distinct=2)
    assert _unexpected_errors(w) == []
