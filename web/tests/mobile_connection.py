"""One click with no installed tunnel/APK and broken PC DNS; publishes only fixtures."""

from pathlib import Path
import socket
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.error import URLError

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn

from mobile_browser import ROOT, app, fixture, mobile, query
from main import mobile_transport


def main():
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web" / "dist", html=True))
    download = mobile_transport.cloudflared
    state = mobile.Companion()
    with tempfile.TemporaryDirectory(prefix="wiki-mobile-first-click-") as scratch, \
         patch.object(mobile, "companion", state), patch.object(query, "LOGS", Path(scratch)), \
         patch.object(mobile, "APK", Path(scratch) / "absent.apk"), \
         patch.object(mobile_transport.shutil, "which", return_value=None), \
         patch.object(mobile_transport, "cloudflared", side_effect=lambda directory: download(Path(scratch) / "runtime")), \
         patch.object(mobile, "urlopen", side_effect=URLError(socket.gaierror("Synthetic failed PC DNS"))) as system_probe:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        state.port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(fake, log_level="error", proxy_headers=False, ws="wsproto",
                                              timeout_graceful_shutdown=1))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    desktop = browser.new_page(viewport={"width": 1440, "height": 900})
                    desktop.goto(f"http://127.0.0.1:{state.port}")
                    desktop.get_by_role("button", name="설정", exact=True).click()
                    desktop.get_by_role("button", name="외부 연결 켜기", exact=True).click()
                    link = desktop.get_by_role("textbox", name="휴대폰 연결 링크", exact=True)
                    link.wait_for(timeout=180000)
                    assert system_probe.called, "The broken system DNS path was not exercised"
                    assert list((Path(scratch) / "runtime").glob("cloudflared*")), "No private runtime was downloaded"
                    assert state.status()["enabled"] and not state.status()["apk_available"]
                    print("PASS: verified fresh runtime, failed PC DNS recovery, automatic pairing QR after one click")
                    phone = browser.new_page(viewport={"width": 400, "height": 800})
                    phone.goto(link.input_value(), timeout=60000)
                    phone.get_by_text("fixture-task", exact=True).wait_for(timeout=30000)
                    assert "pair=" not in phone.url
                    cookie = next(c for c in phone.context.cookies() if c["name"] == mobile.COOKIE)
                    assert cookie["httpOnly"] and cookie["secure"]
                    assert phone.evaluate("window.isSecureContext")
                    print("PASS: real public HTTPS, phone browser pairing and authenticated WebSocket APIs without APK")
                    phone.reload()
                    phone.get_by_text("fixture-task", exact=True).wait_for(timeout=30000)
                    print("PASS: saved pairing survives reload")
                finally:
                    browser.close()
        finally:
            state.stop()
            server.should_exit = True
            thread.join(5)
            listener.close()


if __name__ == "__main__":
    main()
