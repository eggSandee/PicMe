"""Phones and tablets: room to scroll so Safari can hide its bars (README → Screen sizes).

Safari on an iPhone only collapses its tab and address bars when the page scrolls. On a touch
screen the display and the editor's wide layout give the page room to scroll while their content
stays pinned to the visible screen. TVs and computers keep the one-screen page.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip("playwright.sync_api")

from test_browser import PageWatcher, browser, e2e_config_path, server  # noqa: E402,F401

PHONE_LANDSCAPE = {"width": 844, "height": 390}
STATE_JS = """sel => { const el = document.querySelector(sel), r = el.getBoundingClientRect();
  return { coarse: matchMedia('(pointer: coarse)').matches, scrollable: document.scrollingElement.scrollHeight > innerHeight + 100,
           scrollY, top: Math.round(r.top), height: Math.round(r.height), inner: innerHeight }; }"""


def _open(browser, server, path, touch):
    opts = {"viewport": PHONE_LANDSCAPE}
    if touch:
        opts |= {"has_touch": True, "is_mobile": True}
    ctx = browser.new_context(**opts)
    page = ctx.new_page()
    w = PageWatcher(page)
    page.goto(f"{server}{path}", wait_until="networkidle")
    return ctx, w


@pytest.mark.parametrize("path, content", [("/display/", "#collage"), ("/editor/", "#app")])
def test_touch_screens_can_scroll_while_the_content_stays_on_screen(browser, server, path, content):
    ctx, w = _open(browser, server, path, touch=True)
    page = w.page
    before = page.evaluate(STATE_JS, content)
    assert before["coarse"] and before["scrollable"], before
    page.evaluate("() => window.scrollTo(0, 200)")
    page.wait_for_timeout(200)
    after = page.evaluate(STATE_JS, content)
    # The page scrolled (what lets Safari hide its bars), the content didn't move
    assert after["scrollY"] >= 150, after
    assert after["top"] == 0 and after["height"] == after["inner"], after
    assert w.errors == []
    ctx.close()


@pytest.mark.parametrize("path, content", [("/display/", "#collage"), ("/editor/", "#app")])
def test_computers_keep_the_one_screen_page(browser, server, path, content):
    ctx, w = _open(browser, server, path, touch=False)
    state = w.page.evaluate(STATE_JS, content)
    assert not state["coarse"] and not state["scrollable"], state
    assert state["top"] == 0 and state["height"] == state["inner"], state
    ctx.close()
