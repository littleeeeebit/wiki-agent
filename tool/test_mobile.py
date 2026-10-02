"""Pairing, stream delivery and the desktop's existing trust boundary."""

import asyncio
import json
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect
import pytest

from main import app, mobile, query


@pytest.fixture
def companion(tmp_path):
    state = mobile.Companion()
    state.origin = "https://fixture.trycloudflare.com"
    with patch.object(mobile, "companion", state), patch.object(query, "LOGS", tmp_path), \
         patch.object(app, "SWITCH", tmp_path / "switch.json"):
        yield state


def browsers():
    desktop = TestClient(app.app, base_url="http://127.0.0.1:8787", headers={"X-Project": "fixture"})
    phone = TestClient(app.app, base_url="https://fixture.trycloudflare.com", headers={
        "Host": mobile.HOST, "Origin": "https://fixture.trycloudflare.com"})
    return desktop, phone


def pair(desktop, phone):
    link = desktop.post("/api/mobile/link").json()["link"]
    response = phone.post("/api/mobile/pair", json={"code": link.split("#pair=")[1]})
    response.raise_for_status()
    return link, response


def test_pairing_origin_expiry_replay_cookie_and_revocation(companion):
    desktop, phone = browsers()
    assert phone.get("/api/switch").status_code == 401
    assert phone.get("/api/mobile/status").json() == {"local": False, "paired": False}
    link, response = pair(desktop, phone)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert phone.get("/api/switch").status_code == 200
    assert phone.post("/api/mobile/pair", json={"code": link.split("#pair=")[1]}).status_code == 401
    assert phone.post("/api/switch", json={"translate": False}).status_code == 400
    assert phone.post("/api/mobile/link", headers={"X-Project": "fixture"}).status_code == 403
    assert phone.get("/api/switch", headers={"Origin": "https://evil.example"}).status_code == 403
    assert phone.post("/api/switch", json={"translate": False},
                      headers={"Origin": "", "X-Project": "fixture"}).status_code == 403
    assert phone.get("/api/switch", headers={"Host": "localhost:8787", "Origin": "http://localhost:8787",
                                           "cf-connecting-ip": "203.0.113.1"}).status_code == 403
    assert phone.get("/api/switch", headers={"Cookie": f"{mobile.COOKIE}=forged"}).status_code == 401
    # The saved key preserves a paired device across a server object restart.
    fresh = mobile.Companion()
    fresh.origin = companion.origin
    with patch.object(mobile, "companion", fresh):
        assert phone.get("/api/switch").status_code == 200
    desktop.post("/api/mobile/stop").raise_for_status()
    companion.origin = "https://fixture.trycloudflare.com"
    assert phone.get("/api/switch").status_code == 401
    link = desktop.post("/api/mobile/link").json()["link"]
    companion.deadline = time.monotonic() - 1
    assert phone.post("/api/mobile/pair", json={"code": link.split("#pair=")[1]}).status_code == 401


def test_websocket_runs_existing_guards_and_delivers_each_chunk(companion):
    desktop, phone = browsers()
    pair(desktop, phone)
    gate, ended = threading.Event(), threading.Event()

    async def chunks():
        try:
            yield b'data: {"seq": 1}\n\n'
            await asyncio.to_thread(gate.wait, 3)
            yield b'data: {"seq": 2}\n\n'
        finally:
            ended.set()

    async def stream():
        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.app.add_api_route("/api/mobile-fixture-stream", stream)
    try:
        with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
            ws.send_json({"url": "/api/switch", "method": "POST", "body": json.dumps({"translate": False})})
            assert ws.receive_json()["status"] == 400
        with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
            ws.send_json({"url": "/api/switch", "method": "POST", "project": "fixture",
                          "body": json.dumps({"translate": False})})
            assert ws.receive_json()["status"] == 200
            assert json.loads(ws.receive_bytes())["translate"] is False
        with patch.object(query, "_project", "new-project"):
            with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
                ws.send_json({"url": "/api/knowledge/status", "project": "old-project"})
                response = ws.receive_json()
                assert response["status"] == 409
                assert dict(response["headers"])["x-project-moved"] == "new-project"
        with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
            ws.send_json({"url": "/api/mobile-fixture-stream"})
            assert ws.receive_json()["status"] == 200
            assert ws.receive_bytes() == b'data: {"seq": 1}\n\n'
            assert not gate.is_set(), "the first event arrives before the second is produced"
            gate.set()
            assert ws.receive_bytes() == b'data: {"seq": 2}\n\n'
        assert ended.wait(3)
        for target in ("https://evil.example/api/switch", "/api/mobile/link", "/api/%6dobile/link"):
            with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
                ws.send_json({"url": target})
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_json()
        phone.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request"):
                pass
    finally:
        gate.set()
        app.app.router.routes[:] = [r for r in app.app.router.routes if getattr(r, "path", "") != "/api/mobile-fixture-stream"]


def test_start_is_explicit_and_reaps_the_child(companion):
    companion.origin = ""
    desktop, _ = browsers()
    with patch.object(mobile.shutil, "which", return_value=None):
        assert desktop.post("/api/mobile/start").status_code == 503
    from io import StringIO

    class Tunnel:
        def __init__(self, args, **kwargs):
            assert "--http-host-header" in args and mobile.HOST in args
            assert kwargs["encoding"] == "utf-8" and kwargs["stdin"] == mobile.subprocess.DEVNULL
            self.stdout = StringIO("https://fixture.trycloudflare.com\n")
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    with patch.object(mobile.shutil, "which", return_value="cloudflared"), \
         patch.object(mobile.subprocess, "Popen", Tunnel), patch.object(mobile.Companion, "wait_ready"):
        desktop.post("/api/mobile/start").raise_for_status()
        child = companion.process
        desktop.post("/api/mobile/stop").raise_for_status()
        assert child.terminated and companion.process is None and not companion.origin


def test_pairing_waits_for_the_public_path(companion):
    from io import BytesIO

    desktop, _ = browsers()
    process = companion.process = object()
    companion.starting = True
    assert not desktop.get("/api/mobile/status").json()["enabled"]
    assert desktop.post("/api/mobile/link").status_code == 409
    with patch.object(mobile, "urlopen", side_effect=[OSError("DNS pending"), BytesIO(b'{"local":false,"paired":false}')]), \
         patch.object(mobile.time, "sleep"):
        companion.wait_ready(process, companion.origin)
    assert desktop.get("/api/mobile/status").json()["enabled"]
    assert desktop.post("/api/mobile/link").status_code == 200
    companion.process = None
