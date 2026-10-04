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


def test_install_download_precedes_pairing_without_opening_private_files(companion, tmp_path):
    desktop, phone = browsers()
    apk = tmp_path / "wiki-agent.apk"
    payload = b"synthetic-apk-download"
    with patch.object(mobile, "APK", apk):
        assert not desktop.get("/api/mobile/status").json()["apk_available"]
        assert phone.get("/mobile-install").status_code == 404
        assert phone.get("/mobile-install.apk").status_code == 404
        apk.write_bytes(payload)
        assert desktop.get("/api/mobile/status").json()["apk_available"]
        code = desktop.post("/api/mobile/link").json()["link"]
        page = phone.get("/mobile-install")
        assert page.status_code == 200 and 'href="/mobile-install.apk"' in page.text
        downloaded = phone.get("/mobile-install.apk")
        assert downloaded.status_code == 200 and downloaded.content == payload
        assert downloaded.headers["content-type"] == "application/vnd.android.package-archive"
        assert 'filename="wiki-agent.apk"' in downloaded.headers["content-disposition"]
        assert downloaded.headers["cache-control"] == "no-store"
        assert mobile.COOKIE not in phone.cookies
        assert companion.code == code.split("#pair=")[1]
        assert phone.get("/api/switch").status_code == 401
        assert phone.get("/artifacts/wiki-agent-debug.apk").status_code == 404
        assert phone.get("/mobile-install.apk", headers={"Host": "evil.example"}).status_code == 403
        with patch.object(app, "DIST", tmp_path):
            assert phone.get("/mobile-install").status_code == 404


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


def test_an_early_phone_disconnect_finishes_the_http_stream_and_releases_the_socket(companion):
    desktop, phone = browsers()
    pair(desktop, phone)
    ended = threading.Event()

    async def chunks():
        try:
            yield b"first event"
            await asyncio.Event().wait()
        finally:
            ended.set()

    async def stream():
        return StreamingResponse(chunks(), media_type="text/event-stream")

    path = "/api/mobile-fixture-disconnect"
    app.app.add_api_route(path, stream)
    try:
        with phone.websocket_connect("wss://fixture.trycloudflare.com/api/mobile/request") as ws:
            ws.send_json({"url": path})
            assert ws.receive_json()["status"] == 200
            assert ws.receive_bytes() == b"first event"
        assert ended.wait(3), "The disconnected phone left its HTTP producer running"
        assert not companion.sockets
    finally:
        app.app.router.routes[:] = [r for r in app.app.router.routes if getattr(r, "path", "") != path]


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


def test_fixed_origin_cli_normalizes_browser_origins_and_rejects_invalid_urls(companion, tmp_path):
    import uvicorn

    def launch(value):
        return patch.object(app.sys, "argv", ["wiki-agent", "--workspace", str(tmp_path), "--mobile-origin", value])

    valid = {
        "https://EXAMPLE.com": "https://example.com",
        "https://example.com:443": "https://example.com",
        "https://example.com:0443/": "https://example.com",
        "https://example.com:8443": "https://example.com:8443",
        "https://[0:0:0:0:0:0:0:1]:443": "https://[::1]",
        "https://bücher.example": "https://xn--bcher-kva.example",
    }
    with patch.object(app.sys, "stdout"), patch.object(app.sys, "stderr"), \
         patch.object(app.channels, "WORKSPACE", tmp_path), patch.object(app, "taken", return_value=False), \
         patch.object(uvicorn, "Server") as server:
        for raw, expected in valid.items():
            with launch(raw):
                assert app.main() == 0
            assert companion.origin == expected
            desktop = TestClient(app.app, base_url="http://127.0.0.1:8787", headers={"X-Project": "fixture"})
            # This TestClient version cannot parse IPv6 base URLs; the actual
            # tunnel Host is overridden, and the browser Origin is what matters.
            phone = TestClient(app.app, base_url="https://fixture.trycloudflare.com",
                               headers={"Host": mobile.HOST, "Origin": expected})
            pair(desktop, phone)
            assert phone.get("/api/switch").status_code == 200
        assert server.return_value.run.call_count == len(valid)
        server.reset_mock()
        for raw in ("https://example.com:bad", "https://example.com:65536", "https://example.com:0",
                    "https://[broken]", "http://example.com", "https://example.com/path",
                    "https://user:pass@example.com", "https://example.com?x=1", "https://example.com#x"):
            before = companion.origin
            with launch(raw), patch.object(companion, "key") as key, pytest.raises(SystemExit) as error:
                app.main()
            assert error.value.code == 2 and companion.origin == before
            key.assert_not_called()
        server.assert_not_called()
