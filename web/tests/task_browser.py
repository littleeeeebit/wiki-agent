"""Task diff visibility and answer links, using only synthetic fixture data."""

import socket
import threading
import time
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn

from mobile_browser import ROOT, app, fixture, mobile


def main():
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)
    citations = []
    spec = {"id": "fixture-task", "repo": "fixture", "rev": 1, "goal": "Synthetic task requirements",
            "out": [], "done": ["git --version", "Synthetic check passes"], "state": "작업 중",
            "worktree": "fixture-task", "workspace_mode": "branch", "branch": "fixture",
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [], "history": [],
            "source": {"plan": None}, "missing": []}

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "specs":
            return {"project": "fixture", "specs": [spec]}
        if path == "specs/fixture-task" and request.method == "PUT":
            change = await request.json()
            assert change["rev"] == spec["rev"]
            assert change["done"] == ["Synthetic check passes"]
            spec["revisions"] = [{"rev": spec["rev"], "reason": "Changed task requirements"}]
            spec.update(goal=change["goal"], out=change["out"], done=["git --version", *change["done"]],
                        rev=spec["rev"] + 1)
            return spec
        if path == "work/log":
            reply = "\n\n".join("Long synthetic paragraph " + str(i) for i in range(50))
            reply += "\n\n[Windows source](C:/fixture/src/app.ts:12) "
            reply += "[Encoded file](file:///C:/fixture/My%20Project/app.ts#L20) "
            reply += "[Relative source](src/app.ts:3) [PR](https://example.com/pull/7)"
            return {"rows": [{"role": "assistant", "text": reply}], "session_id": "fixture",
                    "busy": False, "running": None, "rules": [], "queued": None}
        if path == "work/diff":
            return {"diff": "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n"
                            "@@ -1 +1 @@\n-old\n+new\n", "base": "fixture", "truncated": False, "omitted": []}
        if path == "file":
            params = parse_qs(urlparse(str(request.url)).query)
            citations.append(params)
            line = int(params["line"][0])
            return {"path": params["path"][0], "line": line, "start": line, "total": 30,
                    "lines": ["Synthetic cited source line"]}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    mobile.companion.port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(fake, log_level="error", ws="wsproto", timeout_graceful_shutdown=1))
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
            page.goto(base)
            page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(
                has_text="fixture-task").click()
            diff = page.locator('pre[aria-label="실시간 코드 diff"]')
            diff.wait_for()
            page.get_by_role("link", name="PR", exact=True).wait_for()
            measured = diff.bounding_box()
            assert measured["y"] >= 0 and measured["y"] + measured["height"] < 900, measured
            assert "-old" in diff.inner_text() and "+new" in diff.inner_text()
            assert "1개 파일" in page.locator("summary").filter(has_text="코드 변경 현황").inner_text()
            for label, path, line in (("Windows source", "C:/fixture/src/app.ts", "12"),
                                       ("Encoded file", "C:/fixture/My Project/app.ts", "20"),
                                       ("Relative source", "src/app.ts", "3")):
                page.get_by_role("button", name=label, exact=True).click()
                page.locator(".file-peek").filter(has_text="Synthetic cited source line").wait_for()
                assert citations[-1]["path"] == [path] and citations[-1]["line"] == [line], citations
                page.locator(".file-peek").get_by_role("button", name="닫기", exact=True).click()
            page.route("https://example.com/**", lambda route: route.fulfill(body="Synthetic PR"))
            with page.expect_popup() as popup:
                page.get_by_role("link", name="PR", exact=True).click()
            popup.value.wait_for_load_state()
            assert popup.value.url == "https://example.com/pull/7"
            popup.value.close()
            page.get_by_role("button", name="명세 Synthetic task requirements", exact=True).click()
            page.get_by_role("button", name="명세 수정", exact=True).click()
            page.get_by_role("textbox", name="목표", exact=True).fill("Revised synthetic requirements")
            page.get_by_role("button", name="저장", exact=True).click()
            page.get_by_role("button", name="명세 Revised synthetic requirements", exact=True).wait_for()
            page.get_by_text("변경 이력 · 현재 판 2", exact=True).wait_for()
            assert not errors, errors
            # Check desktop link dispatch without launching a real browser or app.
            desktop = browser.new_page(viewport={"width": 1440, "height": 900})
            desktop.add_init_script("""window.openedUrls = [];
              window.__TAURI_INTERNALS__ = {
                metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}},
                transformCallback: () => 1,
                unregisterCallback: () => {},
                invoke: async (command, args) => {
                  if (command === 'open_url') window.openedUrls.push(args.url);
                  return 1;
                }
              };
              window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: () => {}};
            """)
            desktop.goto(base)
            desktop.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(
                has_text="fixture-task").click()
            desktop.get_by_role("link", name="PR", exact=True).click()
            desktop.wait_for_function("window.openedUrls.length === 1")
            assert desktop.evaluate("window.openedUrls") == ["https://example.com/pull/7"]
            assert desktop.url == base + "/"
            desktop.close()
            browser.close()
            print("PASS: visible diff after auto-scroll; source links; browser/desktop link dispatch; active spec editing")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


if __name__ == "__main__":
    main()
