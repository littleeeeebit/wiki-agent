"""Desktop pane containment at 16:10, 16:9 and scaled window sizes.

Run after building: python web/tests/desktop_browser.py
Uses the existing synthetic mobile fixture; never opens a user's app.
"""

import json
import socket
import sys
import threading
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn

from mobile_browser import ROOT, fixture


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    (ROOT / "artifacts").mkdir(exist_ok=True)
    fake = FastAPI()

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        data = {"local": True} if path == "mobile/status" else fixture(path, request.method)
        if path == "channels":
            data.insert(1, {**data[0], "id": "refactor", "label": "리펙터링"})
        if path.startswith("providers/") and path != "providers/usage":
            data["usage"] = {"input_tokens": 123456, "output_tokens": 7890, "scope": "thread"}
        return data

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fake, log_level="error", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    failures = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
            page.route("https://fonts.gstatic.com/**", lambda route: route.abort())

            page.goto(f"http://127.0.0.1:{sock.getsockname()[1]}")
            page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").click()
            page.get_by_text("Synthetic task reply.", exact=True).wait_for()
            for width, height in ((1280, 800), (1440, 900), (1680, 1050), (1920, 1200), (2560, 1600),
                                  (1280, 720), (1366, 768), (1920, 1080), (1200, 750), (1101, 688)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function("document.documentElement.dataset.mobileLayout === 'desktop'")
                result = page.evaluate("""() => {
                  const failures = [];
                  const panes = [...document.querySelectorAll('.app-shell > aside, .conversation-pane, .task-pane')];
                  for (const pane of panes) {
                    const box = pane.getBoundingClientRect();
                    if (!box.width || Math.abs(box.bottom - innerHeight) > 1) failures.push('pane height');
                    const controls = [...pane.querySelectorAll('header button, header [role="combobox"], header input, .composer textarea, .composer button')]
                      .filter(e => !e.matches('[aria-hidden="true"]') && e.getBoundingClientRect().width && e.getBoundingClientRect().height);
                    for (const e of controls) {
                      const r = e.getBoundingClientRect();
                      if (r.left < box.left - 1 || r.right > box.right + 1 || r.top < 0 || r.bottom > innerHeight + 1)
                        failures.push(['clipped', e.textContent || e.getAttribute('aria-label'), r.x, r.y, r.width, r.height]);
                    }
                    for (let i = 0; i < controls.length; i++) for (let j = i + 1; j < controls.length; j++) {
                      const a = controls[i].getBoundingClientRect(), b = controls[j].getBoundingClientRect();
                      if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)
                        failures.push(['overlap', controls[i].textContent, controls[j].textContent]);
                    }
                  }
                  if (document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight)
                    failures.push('document overflow');
                  return {viewport: [innerWidth, innerHeight], failures};
                }""")
                print(json.dumps(result, ensure_ascii=False))
                failures.extend(result["failures"])
                if (width, height) == (1440, 900):
                    page.screenshot(path=str(ROOT / "artifacts/desktop-16-10-fixture.png"))
            browser.close()
            assert not failures, failures
            assert not errors, errors
            print("PASS: desktop controls and composers stay inside all panes at 16:10 and 16:9")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


if __name__ == "__main__":
    main()
