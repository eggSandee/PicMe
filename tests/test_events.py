"""/api/events: the server-push stream that tells open displays the config changed.

The stream never ends by itself, so these drive the real ASGI app directly (scope, receive,
send) to read it event by event and to hang up like a closing browser tab.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time

import pytest
from fastapi.testclient import TestClient

import main
from conftest import make_config


class EventStream:
    """GET /api/events on ``main.app``, read incrementally."""

    def __init__(self):
        self.status = None
        self.headers = {}
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._buffer = ""
        self.task = None

    async def __aenter__(self):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/api/events", "raw_path": b"/api/events",
            "query_string": b"", "root_path": "", "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        }
        self.task = asyncio.create_task(main.app(scope, self._receive, self._send))
        return self

    async def __aexit__(self, *exc):
        self.disconnect()
        await asyncio.wait_for(self.task, timeout=5)

    async def _receive(self):
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            self._chunks.put_nowait(message.get("body", b""))

    def disconnect(self):
        self._disconnect.set()

    async def next_message(self, timeout: float = 3.0) -> str:
        """The next blank-line-terminated SSE message (an event or a comment)."""
        deadline = asyncio.get_running_loop().time() + timeout
        while "\n\n" not in self._buffer:
            remaining = deadline - asyncio.get_running_loop().time()
            self._buffer += (await asyncio.wait_for(self._chunks.get(), timeout=max(remaining, 0.01))).decode()
        message, self._buffer = self._buffer.split("\n\n", 1)
        return message

    async def next_config_event(self, timeout: float = 3.0) -> str:
        while True:
            message = await self.next_message(timeout)
            if "event: config" in message:
                return message


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


@pytest.fixture
def fast_checks(monkeypatch):
    monkeypatch.setattr(main, "EVENTS_CHECK_S", 0.05)


@pytest.fixture(autouse=True)
def events_open():
    main.config_events.closing = False
    yield
    main.config_events.closing = False


def test_events_sends_config_on_connect_with_sse_headers(client, written_config):
    async def go():
        async with EventStream() as s:
            first = await s.next_config_event()
            assert s.status == 200
            assert s.headers["content-type"].startswith("text/event-stream")
            assert s.headers["cache-control"] == "no-cache"
            assert "retry: " in first
    run(go())


@pytest.mark.parametrize("save", [
    lambda c, cfg: c.post("/api/config", json={**cfg, "wall_color": "#336699"}),
    lambda c, cfg: c.post("/api/config/reset"),
    lambda c, cfg: (c.post("/api/config/reset"), c.post("/api/config/undo"))[-1],
    lambda c, cfg: (c.put("/api/presets/p1", json={"name": "P"}), c.post("/api/presets/p1/apply"))[-1],
], ids=["post", "reset", "undo", "preset-apply"])
def test_events_emits_config_on_every_kind_of_save(client, written_config, save):
    async def go():
        async with EventStream() as s:
            await s.next_config_event()
            started = time.monotonic()
            r = await asyncio.to_thread(save, client, written_config)
            assert r.status_code == 200
            event = await s.next_config_event(timeout=2)
            # Woken by the save itself, not by the 1 s mtime check
            assert time.monotonic() - started < 0.5
            assert "data: " in event
    run(go())


def test_events_emits_when_config_json_is_edited_on_disk(client, config_path, written_config, fast_checks):
    async def go():
        async with EventStream() as s:
            await s.next_config_event()
            config_path.write_text(json.dumps({**written_config, "wall_color": "#112233"}))
            later = time.time() + 5  # a coarse-mtime filesystem still sees a change
            os.utime(config_path, (later, later))
            await s.next_config_event(timeout=2)
    run(go())


def test_events_quiet_stream_sends_keep_alive_comments(client, written_config, monkeypatch, fast_checks):
    monkeypatch.setattr(main, "EVENTS_KEEPALIVE_S", 0.2)

    async def go():
        async with EventStream() as s:
            await s.next_config_event()
            started = time.monotonic()
            assert (await s.next_message(timeout=2)).startswith(":")
            assert time.monotonic() - started >= 0.15
            assert (await s.next_message(timeout=2)).startswith(":")
    run(go())


def test_events_closes_on_client_disconnect(client, written_config):
    async def go():
        s = EventStream()
        await s.__aenter__()
        await s.next_config_event()
        assert len(main.config_events._waiters) == 1
        s.disconnect()
        await asyncio.wait_for(s.task, timeout=2)  # well before the 1 s check / 15 s keep-alive
        assert main.config_events._waiters == set()
    run(go())


def test_events_end_when_server_shuts_down(client, written_config):
    async def go():
        async with EventStream() as s:
            await s.next_config_event()
            main.config_events.close()
            await asyncio.wait_for(s.task, timeout=2)
            assert main.config_events._waiters == set()
    run(go())


def test_exit_signal_ends_streams_then_runs_uvicorns_handler():
    """Uvicorn waits for open connections on shutdown/reload; the wrapped handler ends the streams
    first, then lets uvicorn's own handler run."""
    calls = []
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
    try:
        main.end_event_streams_on_exit_signal()
        main.end_event_streams_on_exit_signal()  # a second startup doesn't wrap twice
        assert not main.config_events.closing
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert main.config_events.closing
        assert calls == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_server_lifespan_installs_the_exit_hook(config_path):
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: None)
    try:
        # TestClient runs the lifespan off the main thread, where signals can't be set: no crash
        with TestClient(main.app) as c:
            assert c.get("/api/config").status_code == 200
        assert not getattr(signal.getsignal(signal.SIGTERM), "_picme_events", False)

        async def lifespan():
            async with main.lifespan(main.app):
                assert getattr(signal.getsignal(signal.SIGTERM), "_picme_events", False)
        asyncio.run(lifespan())
    finally:
        signal.signal(signal.SIGTERM, previous)
