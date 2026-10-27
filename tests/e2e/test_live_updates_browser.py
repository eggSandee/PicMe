"""Live updates: an editor save reaches an open display through /api/events within a second;
polling still works without EventSource, and the display reconnects after a server restart.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

from conftest import BACKEND_DIR, LANDSCAPE, _make_image, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import SERVER_SCRIPT, _free_port, _wait_for_server, browser, open_page, server  # noqa: E402,F401

WALL_JS = "() => getComputedStyle(document.getElementById('collage')).backgroundColor"


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("live")
    photos = root / "photos"
    photos.mkdir()
    for i, color in enumerate([(200, 80, 40), (40, 120, 200), (40, 200, 80), (180, 180, 40)]):
        _make_image(photos / f"p{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color)
    path = root / "config.json"
    path.write_text(json.dumps(make_config(photos), indent=2))
    return path


@pytest.fixture
def restore_config(e2e_config_path):
    original = e2e_config_path.read_text()
    yield e2e_config_path
    e2e_config_path.write_text(original)


def _post_config(base, cfg):
    req = urllib.request.Request(f"{base}/api/config", data=json.dumps(cfg).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def _open_display(open_page, base, init_script=None):
    w = open_page("about:blank")
    if init_script:
        w.page.add_init_script(init_script)
    w.event_requests = []
    w.page.on("request", lambda r: "/api/events" in r.url and w.event_requests.append(r.url))
    w.page.goto(f"{base}/display/", wait_until="networkidle")
    return w


def _wait_live(page, live=True, timeout=10_000):
    page.wait_for_function(f"() => eventsLive === {'true' if live else 'false'}", timeout=timeout)


def test_editor_change_reaches_display_in_under_a_second(server, open_page, restore_config):
    display = _open_display(open_page, server)
    _wait_live(display.page)
    assert display.page.evaluate(WALL_JS) != "rgb(17, 85, 153)"

    editor = open_page(f"{server}/editor/")
    posted = []
    editor.page.on("request", lambda r: r.method == "POST" and r.url.endswith("/api/config")
                   and posted.append(time.monotonic()))
    editor.page.locator("#g-wall-color").evaluate(
        "el => { el.value = '#115599'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
    typed = time.monotonic()
    display.page.wait_for_function(f"() => ({WALL_JS})() === 'rgb(17, 85, 153)'", polling=20, timeout=6_000)
    applied = time.monotonic()

    assert posted, "the editor never saved"
    latency = applied - posted[0]
    print(f"\nPOST -> display applied: {latency * 1000:.0f} ms (input -> applied: {(applied - typed) * 1000:.0f} ms)")
    assert latency < 1.0
    assert json.loads(restore_config.read_text())["wall_color"] == "#115599"
    assert display.errors == [] and editor.errors == []


def test_polling_still_updates_the_display_without_eventsource(server, open_page, restore_config):
    display = _open_display(open_page, server, init_script="delete window.EventSource;")
    display.page.wait_for_timeout(500)
    assert display.page.evaluate("typeof window.EventSource") == "undefined"
    assert display.page.evaluate("eventsLive") is False

    cfg = json.loads(restore_config.read_text())
    started = time.monotonic()
    _post_config(server, {**cfg, "wall_color": "#225588"})
    display.page.wait_for_function(f"() => ({WALL_JS})() === 'rgb(34, 85, 136)'", timeout=6_000)
    # The 3 s fallback poll, not the 30 s safety net
    assert time.monotonic() - started < 4.5
    assert display.event_requests == []
    assert display.errors == []


def test_quiet_disk_edit_reaches_display_via_events(server, open_page, restore_config):
    """A hand edit of config.json (no save through the API) is pushed too."""
    display = _open_display(open_page, server)
    _wait_live(display.page)
    cfg = json.loads(restore_config.read_text())
    started = time.monotonic()
    restore_config.write_text(json.dumps({**cfg, "wall_color": "#663311"}))
    display.page.wait_for_function(f"() => ({WALL_JS})() === 'rgb(102, 51, 17)'", timeout=6_000)
    assert time.monotonic() - started < 2.5  # the stream's 1 s mtime check, not the 3 s poll


class Server:
    """A PicMe server on a fixed port that can be stopped and started again."""

    def __init__(self, config_path):
        self.config_path = config_path
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", SERVER_SCRIPT, str(self.config_path), str(self.port)],
            cwd=BACKEND_DIR, env={**os.environ, "PICME_TRANSCODE_AUTOSTART": "0"},
        )
        _wait_for_server(f"{self.base}/api/config", self.proc)

    def stop(self, timeout=10):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise


@pytest.fixture
def restartable_server(e2e_config_path, restore_config):
    s = Server(e2e_config_path)
    s.start()
    yield s
    if s.proc.poll() is None:
        s.stop()


def test_display_reconnects_after_a_server_restart(restartable_server, open_page, restore_config):
    srv = restartable_server
    display = _open_display(open_page, srv.base)
    _wait_live(display.page)

    # An open event stream must not hold up shutdown (uvicorn waits for open connections)
    started = time.monotonic()
    srv.stop(timeout=5)
    assert time.monotonic() - started < 3
    _wait_live(display.page, live=False, timeout=5_000)

    # A save while the server is down is caught up on reconnect
    cfg = json.loads(restore_config.read_text())
    restore_config.write_text(json.dumps({**cfg, "wall_color": "#447700"}))
    srv.start()
    _wait_live(display.page, timeout=15_000)
    display.page.wait_for_function(f"() => ({WALL_JS})() === 'rgb(68, 119, 0)'", timeout=5_000)
    assert len(display.event_requests) >= 2

    # And live pushes work again over the new connection
    started = time.monotonic()
    _post_config(srv.base, {**cfg, "wall_color": "#884400"})
    display.page.wait_for_function(f"() => ({WALL_JS})() === 'rgb(136, 68, 0)'", polling=20, timeout=5_000)
    assert time.monotonic() - started < 1.0
