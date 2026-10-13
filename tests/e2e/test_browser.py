"""Browser end-to-end tests: live uvicorn + Playwright (Chrome) at 1920x1080.

The server runs in a subprocess with ``main.CONFIG_PATH`` pointed at a temp
config and every slot pointed at a generated folder of valid, GPS-free images
(so the display never reaches out for reverse geocoding). The whole module is
skipped if Playwright or a Chromium-family browser can't launch.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import piexif
import pytest

from conftest import BACKEND_DIR, LANDSCAPE, PORTRAIT, _exif_bytes, _make_heic, _make_image, make_config

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

    def _open(url: str) -> PageWatcher:
        ctx = browser.new_context(viewport=VIEWPORT)
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
