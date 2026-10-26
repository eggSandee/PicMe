"""Browser end-to-end tests for drag-to-swap slots in the editor (README → Editor).

Reuses the server/browser plumbing from ``test_browser.py`` and overrides
``e2e_config_path`` with two photo folders ("red" and "blue"), so a swap is visible
on an open display. Mouse drags use Playwright's real mouse; touch drags send real
touch events through the Chrome DevTools protocol (Playwright's touchscreen only taps),
so the browser's own scroll handling and pointer events are exercised.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest

from conftest import LANDSCAPE, _exif_bytes, _make_image, editor_mode_init_js, make_config

pytestmark = pytest.mark.e2e

sync_api = pytest.importorskip("playwright.sync_api")

# Imported fixtures run per module; `server` resolves `e2e_config_path` to this module's override.
from test_browser import VIEWPORT, PageWatcher, browser, server  # noqa: E402,F401

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 820, "height": 1180}
TZ = "America/New_York"
CLASSIC_4_DAY = dt.datetime(2026, 10, 12, 12, tzinfo=ZoneInfo(TZ))  # [classic-4, classic-6] rotation shows classic-4


@pytest.fixture(scope="module")
def e2e_config_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-slot-drag")
    for name, color in (("red", (220, 30, 30)), ("blue", (30, 30, 220))):
        folder = root / name
        folder.mkdir()
        for i in range(4):
            _make_image(folder / f"{name}_{i}.jpg", size=LANDSCAPE, fmt="JPEG", color=color,
                        exif=_exif_bytes(date=True))
    path = root / "config.json"
    path.write_text(json.dumps(make_config(root / "red"), indent=2))
    return path


@pytest.fixture
def folders(e2e_config_path):
    root = e2e_config_path.parent
    return {"red": str(root / "red"), "blue": str(root / "blue")}


@pytest.fixture
def write_config(e2e_config_path, folders):
    """Writes a classic-4 config (slots on the red folder, timers long enough not to fire)."""
    original = e2e_config_path.read_text()

    def _write(slots=4, layout="classic-4", **extra) -> dict:
        cfg = make_config(folders["red"], layout=layout, slots=slots, cycle_seconds=300)
        cfg["source_path"] = folders["red"]
        cfg |= extra
        e2e_config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    yield _write
    e2e_config_path.write_text(original)


@pytest.fixture
def open_at(browser):
    """Opens a page; `touch` (a viewport) gives a mobile touch context, `at` installs a fake clock."""
    contexts = []

    def _open(url: str, *, touch=False, at=None, editor_mode="advanced") -> PageWatcher:
        if touch:
            ctx = browser.new_context(viewport=touch, has_touch=True, is_mobile=True, timezone_id=TZ)
        else:
            ctx = browser.new_context(viewport=VIEWPORT, timezone_id=TZ)
        ctx.add_init_script(editor_mode_init_js(editor_mode))
        contexts.append(ctx)
        page = ctx.new_page()
        watcher = PageWatcher(page)
        if at is not None:
            page.clock.install(time=at)
        page.goto(url, wait_until="networkidle")
        return watcher

    yield _open
    for ctx in contexts:
        ctx.close()


def _until(fn, timeout=10.0, what="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                return last
        except sync_api.Error:
            pass  # the page is reloading
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


def _saved(path) -> dict:
    return json.loads(path.read_text())


def _wait_saved(path, check, timeout=5.0) -> dict:
    def _ok():
        try:
            cfg = _saved(path)
        except json.JSONDecodeError:
            return None  # mid-write
        return cfg if check(cfg) else None
    return _until(_ok, timeout, "the editor to save")


def _center(page, selector):
    box = page.locator(selector).bounding_box()
    assert box, f"{selector} is not visible"
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _cell(n):
    return f"#collage-preview .preview-slot[data-slot='{n}']"


def _mouse_drag(page, src, dst, *, release=True):
    x0, y0 = _center(page, src)
    x1, y1 = _center(page, dst)
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=10)
    if release:
        page.mouse.up()


class Touch:
    """Real touch input through CDP: the browser turns it into pointer, touch and scroll events."""

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)

    def _send(self, kind, x=None, y=None):
        points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    def drag(self, src, dst, *, hold_ms):
        # Make sure the preview is on screen (a touch outside the visual viewport isn't a real touch)
        self.page.evaluate("document.getElementById('preview-area').scrollIntoView({block: 'center', inline: 'center'})")
        x0, y0 = _center(self.page, src)
        x1, y1 = _center(self.page, dst)
        self._send("touchStart", x0, y0)
        self.page.wait_for_timeout(hold_ms)
        for i in range(1, 11):
            self._send("touchMove", x0 + (x1 - x0) * i / 10, y0 + (y1 - y0) * i / 10)
            self.page.wait_for_timeout(16)
        self._send("touchEnd")


def _slot_without_id(slot):
    return {k: v for k, v in slot.items() if k != "id"}


# --- Editor: the swap itself ---

def test_mouse_drag_swaps_all_settings_but_the_slot_number(server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config()
    cfg["slots"][0] |= {"mode": "video", "video_loops": 3, "video_audio": {"muted": False, "volume": 40},
                        "group": None, "use_global": {"frame": False, "metadata": True, "source": False},
                        "source_path": folders["blue"],
                        "frame": {"frame_style": "gold", "frame_width": 24}}
    cfg["slots"][2] |= {"group": "A", "cycle_seconds": 45, "fill_mode": "cover"}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    w = open_at(f"{server}/editor/")
    before = w.page.evaluate("() => structuredClone(config.slots)")  # after the editor's own migrations
    _mouse_drag(w.page, _cell(1), _cell(3))

    saved = _wait_saved(e2e_config_path, lambda c: c["slots"][0].get("group") == "A")
    assert [s["id"] for s in saved["slots"]] == [1, 2, 3, 4]
    assert _slot_without_id(saved["slots"][2]) == _slot_without_id(before[0])
    assert _slot_without_id(saved["slots"][0]) == _slot_without_id(before[2])
    assert saved["slots"][1] == before[1] and saved["slots"][3] == before[3]
    assert list(saved["slots"][2])[0] == "id"  # id stays first, so config.json diffs read well
    # The preview redraws: the video badge moved with the video settings
    assert w.page.locator(f"{_cell(3)} >> text=▶").count() == 1
    assert w.page.locator(f"{_cell(1)} >> text=▶").count() == 0
    assert "Swapped slots 1 and 3" in w.page.locator("#status-msg").inner_text()
    assert w.errors == []


def test_dragging_back_restores_the_config(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][1] |= {"group": "B", "fill_mode": "cover"}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    w = open_at(f"{server}/editor/")
    before = w.page.evaluate("() => structuredClone(config.slots)")

    _mouse_drag(w.page, _cell(2), _cell(4))
    _wait_saved(e2e_config_path, lambda c: c["slots"][3].get("group") == "B")
    _mouse_drag(w.page, _cell(4), _cell(2))
    saved = _wait_saved(e2e_config_path, lambda c: c["slots"][1].get("group") == "B")
    assert saved["slots"] == before
    assert w.errors == []


def test_click_and_tiny_move_select_without_swapping(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    original = e2e_config_path.read_text()
    w = open_at(f"{server}/editor/")
    page = w.page

    page.locator(_cell(2)).click()
    assert page.locator("#slot-panel-title").inner_text() == "Slot 2 settings"

    x, y = _center(page, _cell(3))
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 2, y + 1)
    page.mouse.up()
    assert page.locator("#slot-panel-title").inner_text() == "Slot 3 settings"
    assert page.locator(f"{_cell(3)}.selected").count() == 1

    page.wait_for_timeout(800)  # past the autosave delay
    assert e2e_config_path.read_text() == original
    assert w.errors == []


def test_drop_does_not_also_select_the_slot_under_the_pointer(server, open_at, write_config, e2e_config_path):
    write_config()
    w = open_at(f"{server}/editor/")
    page = w.page
    page.locator(_cell(4)).click()
    _mouse_drag(page, _cell(2), _cell(3))
    page.wait_for_timeout(100)
    # The click after the drag was swallowed: slot 4 stays selected
    assert page.locator("#slot-panel-title").inner_text() == "Slot 4 settings"
    assert w.errors == []


def test_selection_follows_the_moved_settings(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    w = open_at(f"{server}/editor/")
    page = w.page
    page.locator(_cell(1)).click()
    assert page.locator("#slot-panel-title").inner_text() == "Slot 1 settings"

    _mouse_drag(page, _cell(1), _cell(3))
    assert page.locator("#slot-panel-title").inner_text() == "Slot 3 settings"
    assert page.locator(f"{_cell(3)}.selected").count() == 1
    _wait_saved(e2e_config_path, lambda c: c["slots"][2].get("group") == "A")
    assert w.errors == []


def test_escape_cancels_a_drag(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    original = e2e_config_path.read_text()
    w = open_at(f"{server}/editor/")
    page = w.page

    _mouse_drag(page, _cell(1), _cell(2), release=False)
    assert page.locator(".drag-ghost").count() == 1
    assert page.locator(f"{_cell(2)}.drop-target").count() == 1
    page.keyboard.press("Escape")
    assert page.locator(".drag-ghost").count() == 0
    page.mouse.up()

    page.wait_for_timeout(800)
    assert e2e_config_path.read_text() == original
    assert page.locator(".drop-target, .drag-source").count() == 0
    assert w.errors == []


def test_drop_outside_any_slot_changes_nothing(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    original = e2e_config_path.read_text()
    w = open_at(f"{server}/editor/")
    _mouse_drag(w.page, _cell(1), "#slot-panel-title")
    w.page.wait_for_timeout(800)
    assert e2e_config_path.read_text() == original
    assert w.page.locator(".drag-ghost").count() == 0
    assert w.errors == []


def test_dragging_onto_a_slot_without_a_config_entry_creates_it(server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config(slots=2)
    cfg["slots"][0] |= {"group": "A", "fill_mode": "cover"}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    w = open_at(f"{server}/editor/")

    _mouse_drag(w.page, _cell(4), _cell(1))  # cell 4 is only padded on the display

    saved = _wait_saved(e2e_config_path, lambda c: len(c["slots"]) == 4)
    assert [s["id"] for s in saved["slots"]] == [1, 2, 3, 4]
    assert saved["slots"][3]["group"] == "A" and saved["slots"][3]["fill_mode"] == "cover"
    assert saved["slots"][0]["mode"] == "photos" and "group" not in saved["slots"][0]
    assert saved["slots"][0]["source_path"] == folders["red"]
    assert w.errors == []


# --- Touch ---

@pytest.mark.parametrize("viewport", [PHONE, TABLET], ids=["phone", "tablet"])
def test_touch_long_press_then_drag_swaps(server, open_at, write_config, e2e_config_path, viewport):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    w = open_at(f"{server}/editor/", touch=viewport)

    Touch(w.page).drag(_cell(1), _cell(4), hold_ms=500)

    saved = _wait_saved(e2e_config_path, lambda c: c["slots"][3].get("group") == "A")
    assert "group" not in saved["slots"][0]
    assert w.page.locator(".drag-ghost").count() == 0
    assert w.errors == []


def test_touch_moving_before_the_long_press_scrolls_instead(server, open_at, write_config, e2e_config_path):
    cfg = write_config()
    cfg["slots"][0]["group"] = "A"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    original = e2e_config_path.read_text()
    w = open_at(f"{server}/editor/", touch=PHONE)

    Touch(w.page).drag(_cell(1), _cell(4), hold_ms=50)

    w.page.wait_for_timeout(800)
    assert e2e_config_path.read_text() == original
    assert w.page.evaluate("PicMeSlotDrag.isDragging()") is False
    assert w.page.locator(".drag-ghost").count() == 0
    assert w.errors == []


# --- Dormant slots (daily rotation) ---

def test_drop_on_a_dormant_slot_button_swaps_with_it(server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config(slots=6, layout_rotation={"enabled": True, "layouts": ["classic-4", "classic-6"],
                                                 "change_at": "04:00"})
    cfg["slots"][1] |= {"use_global": {"source": False, "frame": True, "metadata": True},
                        "source_path": folders["blue"]}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    display = open_at(f"{server}/display/", at=CLASSIC_4_DAY)
    dpage = display.page
    _until(lambda: dpage.evaluate("document.querySelectorAll('.slot img.visible').length") == 4,
           what="the classic-4 display")
    assert "blue" in unquote(dpage.locator("#slot-2 img.visible").get_attribute("src"))
    dpage.evaluate("window.__marker = 1")

    w = open_at(f"{server}/editor/", at=CLASSIC_4_DAY)
    button = "#rotation-slot-hint .dormant-slot-btn[data-slot='6']"
    assert w.page.locator(button).count() == 1
    _mouse_drag(w.page, _cell(2), button)

    saved = _wait_saved(e2e_config_path, lambda c: c["slots"][5].get("source_path") == folders["blue"])
    assert saved["slots"][5]["id"] == 6 and saved["slots"][5]["use_global"]["source"] is False
    assert saved["slots"][1]["use_global"]["source"] is True

    # The display swaps slot 2's photo without reloading
    _until(lambda: "red" in unquote(dpage.locator("#slot-2 img.visible").get_attribute("src") or ""),
           what="slot 2 to show the red folder")
    assert dpage.evaluate("window.__marker") == 1
    assert w.errors == [] and display.errors == []


def test_dormant_slot_button_can_be_dragged_onto_the_preview(server, open_at, write_config, e2e_config_path):
    cfg = write_config(slots=6, layout_rotation={"enabled": True, "layouts": ["classic-4", "classic-6"],
                                                 "change_at": "04:00"})
    cfg["slots"][4]["group"] = "E"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))
    w = open_at(f"{server}/editor/", at=CLASSIC_4_DAY)

    _mouse_drag(w.page, "#rotation-slot-hint .dormant-slot-btn[data-slot='5']", _cell(1))

    saved = _wait_saved(e2e_config_path, lambda c: c["slots"][0].get("group") == "E")
    assert "group" not in saved["slots"][4]
    # The swallowed click didn't select slot 5 via its button
    assert w.page.locator("#slot-panel-title").inner_text() != "Slot 5 settings"
    assert w.errors == []


# --- Open display updates live ---

def test_open_display_shows_swapped_folders_without_reloading(server, open_at, write_config, e2e_config_path, folders):
    cfg = write_config()
    for i, name in ((0, "red"), (1, "blue")):
        cfg["slots"][i] |= {"use_global": {"source": False, "frame": True, "metadata": True},
                            "source_path": folders[name]}
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    display = open_at(f"{server}/display/")
    dpage = display.page
    src = lambda n: unquote(dpage.locator(f"#slot-{n} img.visible").get_attribute("src") or "")  # noqa: E731
    _until(lambda: "red" in src(1) and "blue" in src(2), what="the display's first photos")
    dpage.evaluate("window.__marker = 1")

    w = open_at(f"{server}/editor/")
    _mouse_drag(w.page, _cell(1), _cell(2))

    # Well inside the 300 s cycle timer: the swap shows within a poll or two
    _until(lambda: "blue" in src(1) and "red" in src(2), timeout=8, what="slots 1 and 2 to swap folders")
    assert dpage.evaluate("window.__marker") == 1
    assert w.errors == [] and display.errors == []


def test_open_display_drops_settings_a_swap_removed(server, open_at, write_config, e2e_config_path):
    """A key only one of the swapped slots had (fill_mode here) must not linger on the other."""
    cfg = write_config()
    cfg["slots"][0]["fill_mode"] = "cover"
    e2e_config_path.write_text(json.dumps(cfg, indent=2))

    display = open_at(f"{server}/display/")
    dpage = display.page
    fit = lambda n: dpage.evaluate(  # noqa: E731
        f"getComputedStyle(document.querySelector('#slot-{n} img.visible')).objectFit")
    _until(lambda: fit(1) == "cover" and fit(2) == "contain", what="the display's fill modes")
    dpage.evaluate("window.__marker = 1")

    w = open_at(f"{server}/editor/")
    _mouse_drag(w.page, _cell(1), _cell(2))

    _until(lambda: fit(1) == "contain" and fit(2) == "cover", timeout=8, what="the fill modes to swap")
    assert dpage.evaluate("window.__marker") == 1
    assert w.errors == [] and display.errors == []
