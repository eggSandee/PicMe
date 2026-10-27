"""Browser end-to-end tests for daily layout rotation (README → Daily layout rotation).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides
``e2e_config_path``. Every page runs in America/New_York with Playwright's fake
clock installed before the first load, so "today" is a fixed date. The fake clock
keeps ticking in real time and carries over when the display reloads itself.

Expected layouts come from the day number (days since 1970-01-01) of the rotation
day, modulo the eligible layouts in /api/layouts order:
classic-4, classic-6, hero-center, hero-left, landscape-strip.
  2026-10-12 = 20738   2026-10-13 = 20739   2026-10-14 = 20740
  2026-12-31 = 20818   2027-01-01 = 20819
  2026-03-07 = 20519   2026-03-08 = 20520 (spring forward)
  2026-10-31 = 20757   2026-11-01 = 20758 (fall back)
"""
from __future__ import annotations

import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

import pytest

from conftest import LANDSCAPE, LAYOUTS_DIR, PORTRAIT, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, fresh_install, server  # noqa: E402,F401

TZ = "America/New_York"
TWO = ["classic-4", "classic-6"]
# Every layout in /api/layouts order (`order`, default 100, then file name: Single first), with its slot count
_LAYOUTS = [json.loads(p.read_text()) for p in sorted(LAYOUTS_DIR.glob("*.json"))]
SLOT_COUNTS = {l["id"]: len(l["slots"]) for l in sorted(_LAYOUTS, key=lambda l: l.get("order", 100))}
ALL = list(SLOT_COUNTS)


def all_on(day: int) -> str:
    """The layout an "every layout" rotation shows on rotation day `day` (days since 1970-01-01)."""
    return ALL[day % len(ALL)]


def local(y, mo, d, h=12, mi=0, s=0) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=ZoneInfo(TZ))


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-rotation")
    photos = root / "photos"
    photos.mkdir()
    for i in range(8):
        _make_image(photos / f"photo_{i}.jpg", size=PORTRAIT if i % 2 else LANDSCAPE, fmt="JPEG",
                    color=(30 * i, 120, 200 - 20 * i), exif=_exif_bytes(date=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(photos, slots=4), indent=2))
    return path


@pytest.fixture
def rotation_config(e2e_config_path):
    """Writes a rotation config for the test and restores the original afterwards."""
    original = e2e_config_path.read_text()
    folder = json.loads(original)["slots"][0]["source_path"]

    def _write(layouts=TWO, change_at="04:00", enabled=True, slots=6, layout="classic-4", **extra) -> dict:
        cfg = make_config(folder, layout=layout, slots=slots, cycle_seconds=300)
        cfg["source_path"] = folder
        if layouts is not None:
            cfg["layout_rotation"] = {"enabled": enabled, "layouts": layouts, "change_at": change_at}
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    _write.path = e2e_config_path
    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_at(browser):
    """Opens a page in New York time with the fake clock installed at `at` before loading."""
    contexts = []

    def _open(url: str, at: dt.datetime | None = None, init_scripts=(), editor_mode="advanced") -> PageWatcher:
        ctx = browser.new_context(viewport=VIEWPORT, timezone_id=TZ)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        for script in init_scripts:
            page.add_init_script(script)
        if at is not None:
            page.clock.install(time=at)
        watcher.loads = 0

        def _count_load(frame):
            if frame == page.main_frame:
                watcher.loads += 1
        page.on("framenavigated", _count_load)
        page.goto(url, wait_until="networkidle")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


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


DISPLAY_STATE = """() => ({
  layout: document.getElementById('collage').dataset.layout || null,
  slots: document.querySelectorAll('.slot').length,
  images: [...document.querySelectorAll('.slot')].filter(s => {
    const img = s.querySelector('img.visible');
    return img && img.complete && img.naturalWidth > 0;
  }).length,
})"""


def _wait_for_layout(page, layout_id, timeout=10.0) -> dict:
    """Waits until the display shows `layout_id` with every slot's photo decoded."""
    n = SLOT_COUNTS[layout_id]
    js = f"""() => {{ const s = ({DISPLAY_STATE})();
        return s.slots === {n} && s.images === {n} && s.layout === {json.dumps(layout_id)} && s; }}"""
    return _until(page, js, timeout)


# --- The resolver itself (the real /display/layout-rotation.js, run in the browser) ---

@pytest.fixture
def resolver(open_at, server):
    w = open_at(f"{server}/api/layouts")
    page = w.page
    page.add_script_tag(url=f"{server}/display/layout-rotation.js")
    page.evaluate("async () => { window.LAYOUTS = await (await fetch('/api/layouts')).json(); }")

    def resolve(config, y, mo, d, h=12, mi=0):
        return page.evaluate(
            "([cfg, a]) => PicMeRotation.resolveLayout(cfg, LAYOUTS, new Date(a[0], a[1] - 1, a[2], a[3], a[4]))?.id",
            [config, [y, mo, d, h, mi]])

    resolve.page = page
    resolve.watcher = w
    return resolve


def rot(layouts=TWO, change_at="04:00", enabled=True, layout="hero-center"):
    return {"layout": layout, "layout_rotation": {"enabled": enabled, "layouts": layouts, "change_at": change_at}}


def test_resolver_same_layout_all_day_and_switches_at_change_at(resolver):
    cfg = rot()
    assert resolver(cfg, 2026, 10, 13, 4, 0) == "classic-6"
    assert resolver(cfg, 2026, 10, 13, 4, 1) == "classic-6"
    assert resolver(cfg, 2026, 10, 13, 12, 0) == "classic-6"
    assert resolver(cfg, 2026, 10, 13, 23, 59) == "classic-6"
    # Before 04:00 it is still yesterday's rotation day
    assert resolver(cfg, 2026, 10, 13, 3, 59) == "classic-4"
    assert resolver(cfg, 2026, 10, 14, 0, 30) == "classic-6"
    assert resolver(cfg, 2026, 10, 14, 4, 0) == "classic-4"


def test_resolver_consecutive_days_differ_including_new_year_and_dst(resolver):
    cfg = rot(layouts=["classic-4", "hero-left", "landscape-strip"], change_at="00:00")
    picks = resolver.page.evaluate(
        """cfg => { const out = [];
             for (let i = 0; i < 400; i++) out.push(PicMeRotation.resolveLayout(cfg, LAYOUTS, new Date(2026, 0, 1 + i, 12)).id);
             return out; }""", cfg)
    assert len(picks) == 400
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) == {"classic-4", "hero-left", "landscape-strip"}
    # The old day-of-year rotation repeated a layout from Dec 31 (day 365) to Jan 1 (day 1)
    two = rot(change_at="00:00")
    assert resolver(two, 2026, 12, 31) == "classic-4"
    assert resolver(two, 2027, 1, 1) == "classic-6"


def test_resolver_eligible_list_rules(resolver):
    # Unknown ids are dropped and tick order doesn't matter: /api/layouts order is canonical
    assert resolver(rot(["classic-4", "nope", "classic-6"]), 2026, 10, 13) == "classic-6"
    assert resolver(rot(["classic-6", "classic-4", "classic-6"]), 2026, 10, 13) == "classic-6"
    assert resolver(rot(["classic-6", "classic-4"]), 2026, 10, 14) == "classic-4"
    # Empty list means every layout
    assert resolver(rot([]), 2026, 10, 12) == all_on(20738)
    assert resolver(rot([]), 2026, 10, 13) == all_on(20739)
    assert resolver(rot([]), 2026, 10, 14) == all_on(20740)
    assert len({all_on(d) for d in range(20738, 20738 + len(ALL))}) == len(ALL)  # each layout in turn
    # One eligible layout is shown every day; none valid falls back to the base layout
    assert resolver(rot(["hero-left"]), 2026, 10, 13) == "hero-left"
    assert resolver(rot(["hero-left"]), 2026, 10, 14) == "hero-left"
    assert resolver(rot(["nope"]), 2026, 10, 13) == "hero-center"


def test_resolver_disabled_empty_and_garbage_configs(resolver):
    assert resolver(rot(enabled=False), 2026, 10, 13) == "hero-center"
    assert resolver({"layout": "classic-6"}, 2026, 10, 13) == "classic-6"
    assert resolver({}, 2026, 10, 13) == "classic-4"
    assert resolver(None, 2026, 10, 13) == "classic-4"
    assert resolver([], 2026, 10, 13) == "classic-4"
    assert resolver({"layout": "gone"}, 2026, 10, 13) == "classic-4"
    assert resolver({"layout": "hero-left", "layout_rotation": "on"}, 2026, 10, 13) == "hero-left"
    assert resolver({"layout": "hero-left", "layout_rotation": {"enabled": "yes"}}, 2026, 10, 13) == "hero-left"
    assert resolver.page.evaluate("PicMeRotation.resolveLayout({}, [], new Date())") is None


def test_resolver_legacy_flag_rotates_all_layouts_at_midnight(resolver):
    legacy = {"layout": "hero-center", "daily_layout_rotation": True}
    assert resolver(legacy, 2026, 10, 12, 23, 59) == all_on(20738)
    assert resolver(legacy, 2026, 10, 13, 0, 0) == all_on(20739)
    assert resolver(legacy, 2026, 10, 13, 3, 0) == all_on(20739)
    # layout_rotation wins over the legacy flag when both are present
    both = legacy | {"layout_rotation": {"enabled": False, "layouts": [], "change_at": "04:00"}}
    assert resolver(both, 2026, 10, 13) == "hero-center"


def test_resolver_invalid_change_at(resolver):
    for bad in ("abc", "", None, "4", "04-00", 400):
        cfg = rot(change_at=bad)
        assert resolver(cfg, 2026, 10, 13, 3, 59) == "classic-4", bad   # default 04:00
        assert resolver(cfg, 2026, 10, 13, 4, 0) == "classic-6", bad
    del_cfg = rot()
    del del_cfg["layout_rotation"]["change_at"]
    assert resolver(del_cfg, 2026, 10, 13, 3, 59) == "classic-4"
    # Out-of-range parts are clamped: 25:99 is 23:59
    assert resolver(rot(change_at="25:99"), 2026, 10, 13, 23, 58) == "classic-4"
    assert resolver(rot(change_at="25:99"), 2026, 10, 13, 23, 59) == "classic-6"
    assert resolver.page.evaluate("PicMeRotation.parseChangeAt('7:05')") == 7 * 60 + 5


def test_resolver_dst_days(resolver):
    # Spring forward: 02:00 -> 03:00 on 2026-03-08, change_at inside the gap takes effect at 03:00
    spring = rot(change_at="02:30")
    assert resolver(spring, 2026, 3, 8, 1, 59) == "classic-6"  # still 03-07 (20519 odd)
    assert resolver(spring, 2026, 3, 8, 3, 0) == "classic-4"   # 03-08 (20520 even)
    assert resolver(spring, 2026, 3, 8, 2, 30) == "classic-4"  # JS turns 02:30 into 03:30
    assert resolver(spring, 2026, 3, 9, 2, 29) == "classic-4"
    assert resolver(spring, 2026, 3, 9, 2, 30) == "classic-6"
    # Fall back: 01:00-02:00 happens twice on 2026-11-01; the 25 h day doesn't shift the index
    fall = rot(change_at="01:30")
    assert resolver(fall, 2026, 11, 1, 1, 29) == "classic-6"   # 10-31 (20757 odd)
    assert resolver(fall, 2026, 11, 1, 1, 30) == "classic-4"   # 11-01 (20758 even)
    second_130 = resolver.page.evaluate(
        "cfg => PicMeRotation.resolveLayout(cfg, LAYOUTS, new Date(new Date(2026, 10, 1, 1, 30).getTime() + 3600000)).id",
        fall)
    assert second_130 == "classic-4"  # the repeated 01:30 doesn't flip back
    assert resolver(fall, 2026, 11, 1, 23, 59) == "classic-4"
    assert resolver(fall, 2026, 11, 2, 1, 30) == "classic-6"


def test_resolver_next_change(resolver):
    nxt = """([cfg, a]) => { const n = PicMeRotation.nextChange(cfg, LAYOUTS, new Date(a[0], a[1] - 1, a[2], a[3], a[4]));
                return n && [n.at.getFullYear(), n.at.getMonth() + 1, n.at.getDate(), n.at.getHours(), n.at.getMinutes(), n.layout.id]; }"""
    page = resolver.page
    assert page.evaluate(nxt, [rot(), [2026, 10, 13, 10, 0]]) == [2026, 10, 14, 4, 0, "classic-4"]
    assert page.evaluate(nxt, [rot(), [2026, 10, 13, 3, 0]]) == [2026, 10, 13, 4, 0, "classic-6"]
    assert page.evaluate(nxt, [rot(), [2026, 10, 13, 4, 0]]) == [2026, 10, 14, 4, 0, "classic-4"]
    assert page.evaluate(nxt, [rot(enabled=False), [2026, 10, 13, 10, 0]]) is None
    assert page.evaluate(nxt, [rot(["hero-left"]), [2026, 10, 13, 10, 0]]) is None
    assert resolver.watcher.errors == []


# --- Display ---

def test_display_switches_layout_live_at_change_at(server, open_at, rotation_config):
    rotation_config()  # classic-4 / classic-6, change at 04:00, 6 slots
    w = open_at(f"{server}/display/", at=local(2026, 10, 13, 3, 59))
    page = w.page
    _wait_for_layout(page, "classic-4")
    # A reload before 04:00 keeps yesterday's layout
    page.reload(wait_until="networkidle")
    _wait_for_layout(page, "classic-4")
    page.evaluate("window.__marker = 1")

    # Past 04:00 — the 30 s check reloads into today's layout. Only to the first check after
    # 04:00: the fake clock would run the next one before the reload lands, counting it twice
    page.clock.run_for(65_000)
    _wait_for_layout(page, "classic-6")
    assert page.evaluate("window.__marker") is None  # it really reloaded
    assert page.evaluate("new Date().getHours() * 60 + new Date().getMinutes()") >= 4 * 60  # clock carried over
    # Stable for the rest of the day, including across reloads
    page.reload(wait_until="networkidle")
    _wait_for_layout(page, "classic-6")
    page.evaluate("window.__marker = 2")
    page.clock.run_for(65_000)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__marker") == 2
    assert page.evaluate("JSON.parse(sessionStorage.getItem('picme.rotationReloads')).length") == 1
    assert w.errors == []


def test_display_legacy_flag_rotates_all_layouts_at_midnight(server, open_at, rotation_config):
    rotation_config(layouts=None, slots=4, daily_layout_rotation=True)
    w = open_at(f"{server}/display/", at=local(2026, 10, 12, 23, 59, 30))
    page = w.page
    # Hand-written 4-slot config: bigger layouts pad from slot 1 (old code picked classic-4 by day-of-year)
    _wait_for_layout(page, all_on(20738))
    page.clock.run_for(60_000)
    _wait_for_layout(page, all_on(20739))
    assert w.errors == []


def test_display_follows_rotation_edits_without_needless_reloads(server, open_at, rotation_config):
    cfg = rotation_config()
    w = open_at(f"{server}/display/", at=local(2026, 10, 13, 10, 0))
    page = w.page
    _wait_for_layout(page, "classic-6")
    page.evaluate("window.__marker = 1")

    # Dropping today's layout from the list switches the TV within one poll
    cfg["layout_rotation"]["layouts"] = ["classic-4", "hero-left"]
    rotation_config.path.write_text(json.dumps(cfg))
    _wait_for_layout(page, "hero-left", timeout=6)  # [classic-4, hero-left], 20739 odd
    assert page.evaluate("window.__marker") is None

    # A change that keeps today's pick (and an unrelated wall colour) patches in place
    page.evaluate("window.__marker = 2")
    cfg["layout_rotation"]["change_at"] = "05:30"
    cfg["wall_color"] = "#336699"
    rotation_config.path.write_text(json.dumps(cfg))
    _until(page, "() => getComputedStyle(document.getElementById('collage')).backgroundColor === 'rgb(51, 102, 153)'", 6)
    page.wait_for_timeout(3500)
    assert page.evaluate("window.__marker") == 2
    assert page.evaluate("config.layout_rotation.change_at") == "05:30"

    # Removing the rotation key leaves the base layout; the stale settings must not linger
    del cfg["layout_rotation"]
    cfg["layout"] = "hero-left"
    rotation_config.path.write_text(json.dumps(cfg))
    page.wait_for_timeout(4000)
    assert page.evaluate("window.__marker") == 2
    assert page.evaluate("'layout_rotation' in config") is False
    page.clock.run_for(31_000)  # a rotation check against the patched config
    page.wait_for_timeout(500)
    assert page.evaluate("window.__marker") == 2
    assert w.errors == []


def test_editing_a_dormant_slot_does_not_reload_the_display(server, open_at, rotation_config):
    cfg = rotation_config()
    w = open_at(f"{server}/display/", at=local(2026, 10, 14, 10, 0))
    page = w.page
    _wait_for_layout(page, "classic-4")  # slots 5-6 are dormant today
    page.evaluate("window.__marker = 1")
    cfg["slots"][5] |= {"cycle_seconds": 7, "mode": "video", "group": "g1"}
    cfg["slots"][4] |= {"cycle_seconds": 9}
    rotation_config.path.write_text(json.dumps(cfg))
    _until(page, "() => config.slots[5].cycle_seconds === 7 && config.slots[4].cycle_seconds === 9", 6)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__marker") == 1
    # A visible slot's timer still reloads, as before
    cfg["slots"][1]["cycle_seconds"] = 11
    rotation_config.path.write_text(json.dumps(cfg))
    _until(page, "() => window.__marker === undefined && config.slots[1].cycle_seconds === 11", 8)
    _wait_for_layout(page, "classic-4")
    assert w.errors == []


# Makes every rotation check disagree with the layout init() picked — a stand-in for a clock
# that jumps back on each page load — so the display would reload every 30 s forever.
DISAGREEING_RESOLVER = """
(() => {
  let wrapped;
  Object.defineProperty(window, 'PicMeRotation', {
    configurable: true,
    get() { return wrapped; },
    set(real) {
      let firstCall = true;
      wrapped = { ...real, resolveLayout(cfg, layouts, now) {
        const pick = real.resolveLayout(cfg, layouts, now);
        if (firstCall) { firstCall = false; return pick; }
        // Only the live config (checkRotation); polled configs (checkConfigUpdate) stay honest
        return cfg === config ? layouts.find(l => l.id !== pick.id) : pick;
      } };
    },
  });
})();
"""


def test_rotation_reload_loop_is_cut_off(server, open_at, rotation_config):
    rotation_config()
    w = open_at(f"{server}/display/", at=local(2026, 10, 13, 10, 0), init_scripts=[DISAGREEING_RESOLVER])
    page = w.page
    _wait_for_layout(page, "classic-6")
    assert w.loads == 1
    for _ in range(6):  # six 30 s checks, all within the 5-minute window
        _until(page, "() => configHash !== ''")  # init() has started its timers
        page.clock.run_for(31_000)
        page.wait_for_timeout(300)
    _until(page, "() => rotationReloadsStopped === true")
    assert w.loads == 1 + 3  # three rotation reloads, then it stays put
    assert "keeps switching layouts" in page.locator("#error-banner").text_content()
    page.evaluate("window.__marker = 1")
    page.clock.run_for(61_000)
    page.wait_for_timeout(300)
    assert page.evaluate("window.__marker") == 1
    _wait_for_layout(page, "classic-6")  # still showing photos
    assert w.errors == []


# --- Editor ---

def _click(page, selector):
    page.locator(selector).evaluate("el => el.click()")


def _saved(path, predicate, timeout=5.0) -> dict:
    deadline = time.monotonic() + timeout
    cfg = None
    while time.monotonic() < deadline:
        cfg = json.loads(path.read_text())
        if predicate(cfg):
            return cfg
        time.sleep(0.1)
    raise AssertionError(f"config never matched; last saved: {json.dumps(cfg)[:500]}")


# Rotation off: each layout's box and the switch time are greyed and disabled, saying why
GREYED_PICKS_JS = """() => [...document.querySelectorAll('.rotation-pick-corner')].map(c =>
  c.classList.contains('field-inactive') && c.querySelector('.rotation-pick').disabled && /Turn on Rotate layout daily/.test(c.title))"""


def _assert_rotation_greyed(page):
    picks = page.evaluate(GREYED_PICKS_JS)
    assert picks and all(picks), picks
    assert page.locator("#rotation-options.field-inactive").is_visible()
    assert page.locator("#g-rotation-change-at").is_disabled()
    assert page.locator("#rotation-options .inactive-reason").text_content() == "Turn on Rotate layout daily to use these."


PICKS_JS = "() => Object.fromEntries([...document.querySelectorAll('.rotation-pick')].map(c => [c.dataset.id, c.checked]))"


def test_editor_rotation_controls(server, open_at, rotation_config):
    cfg = rotation_config(layouts=[], enabled=False, slots=4)
    cfg["slots"][0]["mode"] = "video"  # new slots must not copy slot 1's mode
    rotation_config.path.write_text(json.dumps(cfg))
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0))
    page = w.page
    path = rotation_config.path
    _assert_rotation_greyed(page)

    _click(page, "#g-rotation-enabled")
    assert page.evaluate(PICKS_JS) == {k: True for k in SLOT_COUNTS}
    saved = _saved(path, lambda c: c["layout_rotation"]["enabled"] is True)
    assert saved["layout_rotation"]["layouts"] == []  # all ticked is stored as "all"
    # Slots up to the biggest layout now exist: photo slots on the global settings
    assert len(saved["slots"]) == max(SLOT_COUNTS.values())
    for slot in saved["slots"][4:]:
        assert slot["mode"] == "photos"
        assert slot["use_global"] == {"source": True, "metadata": True, "frame": True}
        assert slot["source_path"] == saved["source_path"]
        assert slot["cycle_seconds"] == 300
    assert saved["slots"][0]["mode"] == "video"

    for lid in (l for l in ALL if l not in TWO):
        _click(page, f".rotation-pick[data-id={lid}]")
    saved = _saved(path, lambda c: c["layout_rotation"]["layouts"] == TWO)
    assert saved["layout"] == "classic-4"  # ticking isn't selecting the base layout
    assert page.locator(".layout-thumb.active").get_attribute("data-id") == "classic-4"
    assert page.locator("#rotation-status").text_content() == "Today: Classic 6 · Classic 4 from 04:00 tomorrow"
    assert page.locator(".layout-thumb .today-badge").count() == 1
    assert page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id") == "classic-6"

    # One ticked: a hint, and the last box can't be unticked
    _click(page, ".rotation-pick[data-id=classic-6]")
    _saved(path, lambda c: c["layout_rotation"]["layouts"] == ["classic-4"])
    assert page.locator("#rotation-status").text_content() == "Tick at least 2 layouts to rotate."
    _click(page, ".rotation-pick[data-id=classic-4]")
    assert page.evaluate(PICKS_JS)["classic-4"] is True
    page.wait_for_timeout(800)
    assert json.loads(path.read_text())["layout_rotation"]["layouts"] == ["classic-4"]
    _click(page, ".rotation-pick[data-id=classic-6]")
    _saved(path, lambda c: c["layout_rotation"]["layouts"] == TWO)

    page.locator("#g-rotation-change-at").evaluate(
        "el => { el.value = '11:15'; el.dispatchEvent(new Event('change', { bubbles: true })); }")
    _saved(path, lambda c: c["layout_rotation"]["change_at"] == "11:15")
    # Before 11:15 today is still yesterday's rotation day
    assert page.locator("#rotation-status").text_content() == "Today: Classic 4 · Classic 6 from 11:15 today"
    assert page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id") == "classic-4"
    assert w.errors == []


def test_editor_can_configure_slots_the_base_layout_hides(server, open_at, rotation_config):
    rotation_config(slots=4)  # classic-4 base, classic-6 eligible, slots 5-6 not created yet
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0))
    page = w.page
    hint = page.locator("#rotation-slot-hint")
    assert hint.is_visible()
    assert "Slots 5–6 appear only on days showing Classic 6." in hint.text_content()
    rotation_config(layouts=["classic-4", "classic-6", "hero-left"], slots=6)
    page.reload(wait_until="networkidle")
    assert "Slot 5 appears only on days showing Classic 6 or Hero Left." in hint.text_content()
    assert "Slot 6 appears only on days showing Classic 6." in hint.text_content()
    assert page.locator(".dormant-slot-btn").count() == 2
    _click(page, ".dormant-slot-btn[data-slot='5']")
    assert page.locator("#slot-panel-title").text_content() == "Slot 5 settings"
    assert page.evaluate("config.slots.length") == 6
    assert w.errors == []


def test_editor_selecting_a_bigger_layout_creates_its_slots(server, open_at, rotation_config):
    """The display pads missing slots, but the editor's padded preview slots used to be dead clicks."""
    rotation_config(enabled=False, slots=4)
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0))
    page = w.page
    assert page.locator("#rotation-slot-hint").is_hidden()
    _click(page, ".layout-thumb[data-id=classic-6]")
    # Picking a layout is saved, together with the slots it created
    saved = _saved(rotation_config.path, lambda c: c["layout"] == "classic-6")
    assert [s["id"] for s in saved["slots"]] == [1, 2, 3, 4, 5, 6]
    page.locator("#collage-preview .preview-slot").nth(5).click()
    assert page.locator("#slot-panel-title").text_content() == "Slot 6 settings"
    assert page.evaluate("config.slots.map(s => s.mode)") == ["photos"] * 6
    assert w.errors == []


def test_editor_today_badge_follows_the_clock_past_change_at(server, open_at, rotation_config):
    """An editor left open across change_at updates its Today badge and status without a reload."""
    rotation_config(slots=6)
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 3, 58))
    page = w.page
    status = page.locator("#rotation-status")
    assert status.text_content() == "Today: Classic 4 · Classic 6 from 04:00 today"
    assert page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id") == "classic-4"
    page.clock.run_for(3 * 60_000)
    _until(page, "() => document.querySelector('#rotation-status').textContent.startsWith('Today: Classic 6')")
    assert status.text_content() == "Today: Classic 6 · Classic 4 from 04:00 tomorrow"
    assert page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id") == "classic-6"
    assert w.loads == 1
    assert w.errors == []


def test_editor_migrates_legacy_flag(server, open_at, rotation_config):
    rotation_config(layouts=None, slots=4, daily_layout_rotation=True)
    path = rotation_config.path
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0))
    page = w.page
    assert page.locator("#g-rotation-enabled").is_checked()
    assert page.evaluate(PICKS_JS) == {k: True for k in SLOT_COUNTS}
    assert page.locator("#g-rotation-change-at").input_value() == "00:00"
    assert page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id") == all_on(20739)

    page.locator("#g-wall-color").evaluate(
        "el => { el.value = '#336699'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
    saved = _saved(path, lambda c: c.get("wall_color") == "#336699")
    assert "daily_layout_rotation" not in saved
    assert saved["layout_rotation"] == {"enabled": True, "layouts": [], "change_at": "00:00"}
    assert len(saved["slots"]) == max(SLOT_COUNTS.values())
    assert w.errors == []


def test_fresh_install_editor_has_rotation_off(fresh_install, open_at):
    base, config = fresh_install
    config.unlink(missing_ok=True)
    w = open_at(f"{base}/editor/")
    page = w.page
    assert page.locator("#g-rotation-enabled").count() == 1
    assert not page.locator("#g-rotation-enabled").is_checked()
    _assert_rotation_greyed(page)
    assert json.loads(config.read_text())["layout_rotation"]["enabled"] is False
    assert w.errors == []


# --- Corner checkboxes and Select all / Deselect all (Basic and Advanced) ---

RECT_JS = "el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, w: r.width, h: r.height }; }"


def _rect(page, selector) -> dict:
    return page.locator(selector).evaluate(RECT_JS)


@pytest.mark.parametrize("editor_mode", ["basic", "advanced"])
def test_rotation_checkbox_in_each_thumbnail_corner(server, open_at, rotation_config, editor_mode):
    rotation_config(layouts=[], enabled=False, slots=4)
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0), editor_mode=editor_mode)
    page = w.page
    path = rotation_config.path
    _assert_rotation_greyed(page)
    assert page.locator("#rotation-bulk").is_hidden()

    page.click("#g-rotation-enabled + .toggle-track")  # the visible toggle, in either mode
    _saved(path, lambda c: c["layout_rotation"]["enabled"] is True)
    assert page.locator(".layout-thumb").count() == len(ALL)
    for lid in ALL:
        thumb = f".layout-thumb[data-id='{lid}']"
        assert page.locator(f"{thumb} .rotation-pick").is_visible(), lid
        grid, box = _rect(page, f"{thumb} .thumb-grid"), _rect(page, f"{thumb} .rotation-pick")
        # Small, inside the thumbnail's picture, in its bottom-right quadrant
        assert box["w"] <= 16 and box["h"] <= 16, box
        assert grid["x"] + grid["w"] / 2 <= box["x"] and box["right"] <= grid["right"] + 0.5, (lid, grid, box)
        assert grid["y"] + grid["h"] / 2 <= box["y"] and box["bottom"] <= grid["bottom"] + 0.5, (lid, grid, box)
        assert page.locator(f"{thumb} .rotation-pick").get_attribute("aria-label") == \
            f"Rotate {page.locator(f'{thumb} > span').first.text_content()} daily"
    # The Today badge stays, clear of the checkbox
    today = page.locator(".layout-thumb:has(.today-badge)").get_attribute("data-id")
    badge, box = _rect(page, ".today-badge"), _rect(page, f".layout-thumb[data-id='{today}'] .rotation-pick")
    assert badge["bottom"] <= box["y"] or badge["right"] <= box["x"]
    # The Mat chip (frames per rotated layout) is Advanced
    assert page.locator(".layout-thumb[data-id='classic-6'] .layout-mode-chip").is_visible() == (editor_mode == "advanced")

    # A real click on a box ticks it and doesn't pick the layout; a click on the thumbnail does
    page.click(".rotation-pick[data-id='hero-left']")
    saved = _saved(path, lambda c: c["layout_rotation"]["layouts"] == [l for l in ALL if l != "hero-left"])
    assert saved["layout"] == "classic-4"
    assert page.locator(".layout-thumb.active").get_attribute("data-id") == "classic-4"
    assert not page.locator(".rotation-pick[data-id='hero-left']").is_checked()
    page.click(".rotation-pick[data-id='hero-left']")
    _saved(path, lambda c: c["layout_rotation"]["layouts"] == [])  # all ticked again = "all"
    page.click(".layout-thumb[data-id='classic-6'] .thumb-grid")
    _saved(path, lambda c: c["layout"] == "classic-6")

    page.click("#g-rotation-enabled + .toggle-track")
    _saved(path, lambda c: c["layout_rotation"]["enabled"] is False)
    _assert_rotation_greyed(page)
    assert w.errors == []


@pytest.mark.parametrize("editor_mode", ["basic", "advanced"])
def test_select_all_and_deselect_all_on_the_layout_header(server, open_at, rotation_config, editor_mode):
    rotation_config(layouts=TWO, slots=6)
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0), editor_mode=editor_mode)
    page = w.page
    path = rotation_config.path
    status = page.locator("#rotation-status")
    select_all, deselect_all = page.locator("#rotation-select-all"), page.locator("#rotation-deselect-all")
    assert select_all.is_visible() and deselect_all.is_visible()
    # Small links on the Layout header row, at its right
    h3 = page.locator(".sidebar-section h3", has_text="Layout").first
    title, links = h3.evaluate(RECT_JS), page.locator("#rotation-bulk").evaluate(RECT_JS)
    assert links["y"] < title["bottom"] and title["y"] < links["bottom"]  # same row
    assert links["x"] > title["right"]
    section = page.locator("#layout-picker").evaluate(RECT_JS)
    assert links["right"] == pytest.approx(section["right"], abs=2)
    # The links don't make the title taller than another section's
    other = page.locator(".sidebar-section h3", has_text="Global Settings").evaluate(RECT_JS)
    assert title["h"] == pytest.approx(other["h"], abs=0.5) and links["h"] <= title["h"] + 1

    select_all.click()
    _saved(path, lambda c: c["layout_rotation"]["layouts"] == [])
    assert page.evaluate(PICKS_JS) == {k: True for k in ALL}
    assert status.text_content().startswith("Today: ")
    assert select_all.is_disabled() and not deselect_all.is_disabled()

    deselect_all.click()
    saved = _saved(path, lambda c: c["layout_rotation"]["layouts"] == ["classic-4"])  # the base layout stays
    assert page.evaluate(PICKS_JS) == {k: k == "classic-4" for k in ALL}
    assert status.text_content() == "Tick at least 2 layouts to rotate."
    assert deselect_all.is_disabled() and not select_all.is_disabled()
    assert saved["layout"] == "classic-4"

    select_all.click()
    saved = _saved(path, lambda c: c["layout_rotation"]["layouts"] == [])
    assert status.text_content().startswith("Today: ")
    assert saved["layout"] == "classic-4"
    assert page.locator(".layout-thumb.active").get_attribute("data-id") == "classic-4"

    page.click("#g-rotation-enabled + .toggle-track")
    _saved(path, lambda c: c["layout_rotation"]["enabled"] is False)
    assert page.locator("#rotation-bulk").is_hidden()
    assert w.errors == []


@pytest.mark.parametrize("editor_mode", ["basic", "advanced"])
def test_rotation_checkbox_has_a_24px_hit_area_clear_of_the_badges(server, open_at, rotation_config, editor_mode):
    rotation_config(layouts=[], enabled=True, slots=4)
    w = open_at(f"{server}/editor/", at=local(2026, 10, 13, 10, 0), editor_mode=editor_mode)
    page = w.page
    path = rotation_config.path
    base = page.evaluate("() => document.querySelector('.layout-thumb.active').dataset.id")
    for lid in ("hero-left", "gallery-12"):
        thumb = f".layout-thumb[data-id='{lid}']"
        hit = _rect(page, f"{thumb} .rotation-pick-corner")
        grid = _rect(page, f"{thumb} .thumb-grid")
        assert hit["w"] >= 24 and hit["h"] >= 24, hit
        assert grid["right"] + 0.5 >= hit["right"] and grid["bottom"] + 0.5 >= hit["bottom"], (grid, hit)
        for badge in page.locator(f"{thumb} .gallery-badge, {thumb} .today-badge").all():
            b = badge.evaluate(RECT_JS)
            assert b["right"] <= hit["x"] or b["x"] >= hit["right"] or b["bottom"] <= hit["y"] or b["y"] >= hit["bottom"], (b, hit)

    # A click in the padding, well outside the 14px box, still toggles it (and doesn't pick the layout).
    # Mouse clicks are by viewport position, and Layout sits below Global Settings, so scroll to it first
    page.locator(".layout-thumb[data-id='hero-left']").scroll_into_view_if_needed()
    hit = _rect(page, ".layout-thumb[data-id='hero-left'] .rotation-pick-corner")
    page.mouse.click(hit["x"] + 2, hit["y"] + 2)
    _saved(path, lambda c: "hero-left" not in c["layout_rotation"]["layouts"] and c["layout_rotation"]["layouts"])
    assert not page.locator(".rotation-pick[data-id='hero-left']").is_checked()
    assert page.evaluate("() => document.querySelector('.layout-thumb.active').dataset.id") == base
    page.mouse.click(hit["right"] - 2, hit["bottom"] - 2)
    _saved(path, lambda c: c["layout_rotation"]["layouts"] == [])
    assert page.locator(".rotation-pick[data-id='hero-left']").is_checked()
