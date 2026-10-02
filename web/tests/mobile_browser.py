"""Phone layout and optional real HTTPS tunnel checks against synthetic data.

python web/tests/mobile_browser.py [--live-tunnel]
Requires the local Playwright package and Chromium. Never opens a user's app.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tool"))
os.environ["WIKI_SEARCH"] = "off"

from main import app, mobile, query  # noqa: E402


def fixture(path, method):
    if path == "channels":
        return [{"id": cid, "label": label, "blurb": "Synthetic conversation", "live": False,
                 "repo": "fixture", "remote": "", "model": "", "model_name": "Default", "effort": ""}
                for cid, label in (("next", "다음 작업"), ("wiki", "위키"), ("retro", "회고"))]
    if path == "options":
        return {"projects": [{"id": "fixture", "state": "연결 완료", "missing": [], "notes": [], "wired": True}],
                "models": [{"id": "", "label": "기본", "note": ""}], "efforts": [], "codex_error": ""}
    if path == "graph":
        return {"repo": "fixture", "hub": False, "wiki": "fixture", "layers": {
            "repo": {"nodes": [], "edges": []}, "hub": {"nodes": [], "edges": [], "ladder": []}},
                "metrics": {"pages": 0, "orphans": 0, "lint": 0}}
    if path == "switch":
        return {"translate": False, "usage": {"month": "2026-10", "usd": 0, "limit": 10}}
    if path == "worktrees":
        return {"project": "fixture", "repo": "fixture", "rows": [
            {"path": "fixture-task", "name": "fixture-task", "branch": "fixture", "dirty": False,
             "merged": False, "live": False, "busy": False}]}
    if path == "specs":
        return {"project": "fixture", "specs": []}
    if path == "prs":
        return {"project": "fixture", "rows": []}
    if path == "loops":
        return {"loops": [], "turns": []}
    if path == "loop/settings":
        return {"rounds": 3, "concurrent": 1}
    if path.startswith("log/"):
        return [{"role": "assistant", "text": "Synthetic conversation visible on the phone.", "ts": 1}]
    if path == "work/log":
        return {"rows": [{"role": "assistant", "text": "Synthetic task reply."}], "session_id": "fixture",
                "busy": False, "running": None, "rules": [], "queued": None}
    if path == "work/diff":
        return {"diff": "+synthetic change", "base": "fixture", "truncated": False, "omitted": []}
    if path.startswith("providers/"):
        return {"provider": "claude", "live": False, "connection_ms": None, "error": "", "quota": [], "usage": {}}
    if path == "connect":
        return {"rows": [], "settings": {"survey": False}, "hub": {"name": "fixture", "needed": False, "refused": ""}}
    if path == "work/settings":
        return {"bypass": False}
    if path.endswith("events"):
        async def idle():
            yield b"data: null\n\n"
            await asyncio.sleep(60)
        return StreamingResponse(idle(), media_type="text/event-stream")
    if method == "POST" and path.startswith("say/"):
        return StreamingResponse(iter([b'data: {"kind":"done","text":"Synthetic sent reply.","seq":0}\n\n']),
                                 media_type="text/event-stream")
    return {}


def inspect_phone(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    nav = page.get_by_role("navigation", name="화면", exact=True)
    nav.get_by_role("button", name="작업 목록", exact=True).click()
    settings_width = page.get_by_role("button", name="설정", exact=True).bounding_box()["width"]
    row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
    row.wait_for()
    assert row.inner_text().find("fixture-task") >= 0
    row.click()
    page.get_by_text("Synthetic task reply.", exact=True).wait_for()
    assert not page.get_by_role("tab", name="터미널", exact=True).count()
    page.get_by_role("tab", name="리뷰", exact=True).click()
    page.get_by_role("tab", name="에이전트", exact=True).click()
    nav.get_by_role("button", name="대화", exact=True).click()
    page.get_by_text("Synthetic conversation visible on the phone.", exact=True).first.wait_for()
    page.get_by_role("button", name="옵션", exact=True).click()
    measured = page.evaluate("""() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth,
      oversized: [...document.querySelectorAll('header, main, nav, textarea, button, [role="combobox"]')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().right > innerWidth + 1)
        .map(e => [e.tagName, e.className, e.getBoundingClientRect().right]),
      input: getComputedStyle(document.querySelector('textarea')).fontSize,
      targets: [...document.querySelectorAll('.mobile-navigation button')].map(e => e.getBoundingClientRect().height)
    })""")
    assert measured["document"] <= width and not measured["oversized"], measured
    assert measured["input"] == "16px" and min(measured["targets"]) >= 44, measured
    assert settings_width >= 44, settings_width
    page.get_by_role("button", name="옵션", exact=True).click()
    page.get_by_role("tab", name="지도", exact=True).click()
    page.get_by_role("button", name="되돌리기", exact=True).wait_for()
    assert page.locator('.map-toolbar').evaluate("e => e.scrollWidth <= e.clientWidth"), "map controls overflow"
    page.get_by_role("tab", name="대화", exact=True).click()
    print(json.dumps({"width": width, "height": height, **measured}))


def inspect_modes(page):
    page.set_viewport_size({"width": 844, "height": 390})
    # Rotation does not select Landscape on the paired companion.
    assert page.locator("html").get_attribute("data-mobile-layout") == "portrait"
    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="작업 목록").click()
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("radio", name="가로 모드 PC처럼 나란히 보기").check()
    page.get_by_role("button", name="닫기", exact=True).click()
    row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
    row.click()
    page.get_by_text("Synthetic task reply.", exact=True).wait_for()
    assert page.get_by_text("Synthetic conversation visible on the phone.", exact=True).first.is_visible()
    assert not page.get_by_role("navigation", name="화면", exact=True).is_visible()
    page.get_by_role("button", name="옵션", exact=True).click()
    for width, height in ((844, 390), (932, 430)):
        page.set_viewport_size({"width": width, "height": height})
        measured = page.evaluate("""() => ({
          mode: document.documentElement.dataset.mobileLayout,
          document: document.documentElement.scrollWidth,
          columns: getComputedStyle(document.querySelector('.app-shell')).gridTemplateColumns,
          oversized: [...document.querySelectorAll('header, main, nav, textarea, button, [role="combobox"]')]
            .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().right > innerWidth + 1)
            .map(e => [e.tagName, e.className, e.getBoundingClientRect().right])
        })""")
        assert measured["mode"] == "landscape" and measured["document"] <= width, measured
        assert not measured["oversized"], measured
        assert len(measured["columns"].split()) == 3, measured
        print(json.dumps({"width": width, "height": height, **measured}))
    page.get_by_role("button", name="옵션", exact=True).click()
    page.screenshot(path=str(ROOT / "artifacts" / "mobile-landscape-fixture.png"))
    page.reload()
    page.get_by_text("fixture-task", exact=True).wait_for()
    assert page.locator("html").get_attribute("data-mobile-layout") == "landscape"
    page.set_viewport_size({"width": 400, "height": 800})
    assert page.locator("html").get_attribute("data-mobile-layout") == "landscape"
    # Settings stays reachable even if the phone is rotated before changing mode.
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("radio", name="세로 모드 한 화면씩 보기").check()
    page.get_by_role("button", name="닫기", exact=True).click()
    page.reload()
    inspect_phone(page, 400, 800)
    page.set_viewport_size({"width": 1440, "height": 900})
    assert page.get_by_role("navigation", name="화면", exact=True).is_visible()
    assert page.locator("html").get_attribute("data-mobile-layout") == "portrait"
    print("PASS: manual mode selection, three panes, rotation independence and saved preference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-tunnel", action="store_true")
    opts = parser.parse_args()
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web" / "dist", html=True))
    with tempfile.TemporaryDirectory(prefix="wiki-mobile-fixture-") as scratch:
        query.LOGS = Path(scratch)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        mobile.companion.port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(fake, log_level="error", proxy_headers=False, ws="wsproto",
                                              timeout_graceful_shutdown=1))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                (ROOT / "artifacts").mkdir(exist_ok=True)
                base = f"http://127.0.0.1:{mobile.companion.port}"
                page.goto(base)
                page.get_by_text("fixture-task", exact=True).wait_for()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for width, height in ((400, 800), (320, 740), (844, 390), (932, 430), (1100, 800)):
                    inspect_phone(page, width, height)
                # Generate a real one-use fixture link without starting a tunnel.
                mobile.companion.origin = "https://fixture.trycloudflare.com"
                page.set_viewport_size({"width": 1440, "height": 900})
                page.get_by_role("button", name="설정", exact=True).click()
                page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                qr = page.get_by_role("img", name="휴대폰 연결 QR 코드", exact=True)
                qr.wait_for()
                first = qr.inner_html()
                page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                page.wait_for_function("old => document.querySelector('svg[aria-label=\"휴대폰 연결 QR 코드\"]').innerHTML !== old", arg=first)
                qr.screenshot(path=str(ROOT / "artifacts" / "mobile-pairing-qr-fixture.png"))
                (ROOT / "artifacts" / "mobile-pairing-qr-fixture.txt").write_text(
                    page.get_by_role("textbox", name="휴대폰 연결 링크", exact=True).input_value(), encoding="utf-8")
                page.context.grant_permissions(["clipboard-read", "clipboard-write"])
                page.get_by_role("button", name="링크 복사", exact=True).click()
                assert page.evaluate("navigator.clipboard.readText()") == page.get_by_role(
                    "textbox", name="휴대폰 연결 링크", exact=True).input_value()
                page.clock.install()
                page.clock.fast_forward(301_000)
                qr.wait_for(state="detached")
                page.get_by_text("연결 링크가 만료됐습니다. 새로 만드세요.", exact=True).wait_for()
                print("PASS: QR regeneration, copy fallback and expiry")
                page.get_by_role("button", name="닫기", exact=True).click()
                mobile.companion.origin = ""

                # Exercise remote UI locally, without publishing data or needing DNS.
                mock_phone = browser.new_page(viewport={"width": 400, "height": 800})
                mock_phone.on("pageerror", lambda error: errors.append(str(error)))
                mock_phone.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                mock_phone.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                mock_phone.route("**/api/mobile/status", lambda route: route.fulfill(
                    json={"local": False, "paired": True}))

                def bridge(ws):
                    def answer(message):
                        request = json.loads(message)
                        path = request["url"].removeprefix("/api/").split("?")[0]
                        stream = path.endswith("events") or path.startswith("say/")
                        ws.send(json.dumps({"status": 200, "headers": [["content-type",
                            "text/event-stream" if stream else "application/json"]]}))
                        ws.send(b"data: null\n\n" if stream else json.dumps(fixture(path, request["method"])).encode())
                        if not path.endswith("events"):
                            ws.close(code=1000)
                    ws.on_message(answer)

                mock_phone.route_web_socket("**/api/mobile/request", bridge)
                mock_phone.goto(base)
                inspect_phone(mock_phone, 400, 800)
                inspect_modes(mock_phone)
                mock_phone.close()
                if opts.live_tunnel:
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.get_by_role("button", name="설정", exact=True).click()
                    page.get_by_role("button", name="외부 연결 켜기", exact=True).click()
                    page.get_by_role("button", name="연결 링크 만들기", exact=True).wait_for(timeout=65000)
                    page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                    link = page.get_by_role("textbox", name="휴대폰 연결 링크", exact=True).input_value()
                    phone = browser.new_page(viewport={"width": 400, "height": 800})
                    phone.on("pageerror", lambda error: errors.append(str(error)))
                    phone.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    phone.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                    # Only this synthetic fixture is published. No user repository or record is served.
                    phone.goto(link, timeout=60000)
                    inspect_phone(phone, 400, 800)
                    assert "pair=" not in phone.url
                    cookie = next(c for c in phone.context.cookies() if c["name"] == mobile.COOKIE)
                    assert cookie["httpOnly"] and cookie["secure"]
                    phone.reload()
                    inspect_phone(phone, 400, 800)
                    phone.context.set_offline(True)
                    phone.get_by_role("button", name="다시 연결", exact=True).wait_for()
                    phone.context.set_offline(False)
                    phone.get_by_role("button", name="다시 연결", exact=True).click()
                    inspect_phone(phone, 400, 800)
                    inspect_modes(phone)
                    print("PASS: real HTTPS tunnel, pairing cookie, streamed API transport, reload and network recovery")
                assert errors == [], errors
                preview = phone if opts.live_tunnel else page
                preview.set_viewport_size({"width": 400, "height": 800})
                preview.screenshot(path=str(ROOT / "artifacts" / "mobile-companion-fixture.png"))
                browser.close()
                print("PASS: desktop, portrait, small phone, landscape and touch target checks")
        finally:
            mobile.companion.stop()
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    main()
