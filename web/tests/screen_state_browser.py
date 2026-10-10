"""Screen state that must survive: a theme change repaints the terminal's shell
without reopening it, and run details recover from a transient failure.

Run after building: python web/tests/screen_state_browser.py
Serves web/dist with the synthetic fixture and stubs the Tauri IPC, counting
`pty_open`/`pty_close`; never opens a user's app or a real shell.
"""

import asyncio
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from browser_fixture import page_of, served
from mobile_browser import ROOT, fixture

STUB = """
window.ipc = [];
window.__TAURI_INTERNALS__ = {
  metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}},
  transformCallback: () => 1,
  unregisterCallback: () => {},
  invoke: async (command) => { window.ipc.push(command); return command === 'pty_open' ? 7 : 1; }
};
window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: () => {}};
"""


def terminal_theme(page):
    page.add_init_script(STUB)
    page.reload()
    page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").click()
    page.get_by_role("tab", name="터미널").click()
    page.wait_for_function("window.ipc.includes('pty_open')")
    def seen():
        return page.evaluate("""() => [
          window.ipc.filter(x => x === 'pty_open').length, window.ipc.filter(x => x === 'pty_close').length,
          document.querySelector('.xterm .xterm-scrollable-element').style.backgroundColor]""")

    before = seen()
    page.get_by_role("button", name="설정").first.click()
    page.get_by_text("어두운 화면", exact=True).locator(
        "xpath=ancestor::*[.//input[@role='switch']][1]//input[@role='switch']").click()
    page.wait_for_timeout(300)
    after = seen()
    assert before[:2] == after[:2] == [1, 0], f"a theme change reopened the shell: {before} -> {after}"
    assert before[2] != after[2], f"the terminal kept the old palette: {before[2]}"


def run_retry(page):
    page.get_by_text("근거와 판단 보기").first.click()
    page.get_by_text("실행 기록을 못 읽었다", exact=False).wait_for()
    page.get_by_role("button", name="다시 읽기").click()
    page.get_by_text("결과", exact=True).first.wait_for()


def run_unmount(page, asked):
    # A reply still on its way when the view goes must not start the polling again.
    page.get_by_text("근거와 판단 보기").nth(1).click()
    page.wait_for_timeout(300)
    page.get_by_role("navigation", name="초점").get_by_role("button", name="다음 작업").click()
    page.wait_for_timeout(4500)
    assert asked.count("knowledge/runs/run-y") == 1, f"an unmounted view kept polling: {asked}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fake, asked = FastAPI(), []

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "knowledge/runs/run-x":
            asked.append(path)
            if len(asked) == 1:
                return JSONResponse({"detail": "transient"}, status_code=500)
            return {"schema_version": 1, "run_id": "run-x", "done": True, "outcome": "answered", "notes": []}
        if path == "knowledge/runs/run-y":
            asked.append(path)
            await asyncio.sleep(1)
            return {"schema_version": 1, "run_id": "run-y", "done": False, "notes": []}
        if path.startswith("log/"):
            return [{"role": "assistant", "text": "Synthetic answer with a run.", "ts": 1, "run_id": "run-x"},
                    {"role": "assistant", "text": "An answer still being explained.", "ts": 2, "run_id": "run-y"}]
        return {"local": True} if path == "mobile/status" else fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    errors = []
    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        page = page_of(browser, errors, viewport={"width": 1440, "height": 900})
        page.goto(f"http://127.0.0.1:{port}")
        run_retry(page)
        assert asked == ["knowledge/runs/run-x"] * 2, asked
        run_unmount(page, asked)
        terminal_theme(page)
        browser.close()
    assert not errors, errors
    print("PASS: run details recover after a failure and stop asking once gone; "
          "a theme change keeps the shell and repaints it")


if __name__ == "__main__":
    main()
