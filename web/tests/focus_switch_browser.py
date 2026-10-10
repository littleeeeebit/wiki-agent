"""An answer still streaming keeps its progress across a focus switch.

Run after building: python web/tests/focus_switch_browser.py
The record's question row carries the run id while the answer streams; the
restore once read that as "answered" and dropped the live answer until done.
"""

import asyncio
import json
import sys
import threading

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from mobile_browser import ROOT, fixture
from browser_fixture import served

RUN = "run-focus-switch"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fake, asked, release = FastAPI(), threading.Event(), threading.Event()

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path.startswith("log/") and request.query_params.get("legacy") == "true":
            return []
        if path == "log/next":
            rows = [{"role": "user", "text": "Plan the next task", "run_id": RUN, "ts": 1}] if asked.is_set() else []
            return rows + [{"role": "assistant", "text": "Final answer.", "run_id": RUN, "ts": 2}] if release.is_set() else rows
        if path == "log/wiki":
            return [{"role": "assistant", "text": "Wiki focus record.", "ts": 1}]
        if request.method == "POST" and path == "say/next":
            asked.set()

            async def answer():
                for seq, ev in enumerate(({"kind": "step", "stage": "draft"},
                                          {"kind": "delta", "text": "Partial progress of the answer."})):
                    yield f"data: {json.dumps({**ev, 'run_id': RUN, 'seq': seq})}\n\n".encode()
                while not release.is_set():
                    await asyncio.sleep(.05)
                yield f"data: {json.dumps({'kind': 'done', 'text': 'Final answer.', 'run_id': RUN, 'seq': 2})}\n\n".encode()

            return StreamingResponse(answer(), media_type="text/event-stream")
        return {"local": True} if path == "mobile/status" else fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    try:
        with served(fake) as port, sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://fonts.*/**", lambda route: route.abort())
            page.goto(f"http://127.0.0.1:{port}")
            focus = page.get_by_role("navigation", name="초점")
            focus.get_by_role("button", name="다음 작업").click()
            box = page.get_by_role("region", name="대화").get_by_label("질문 또는 지시")
            box.fill("Plan the next task")
            box.press("Enter")
            page.get_by_text("Partial progress of the answer.").first.wait_for()
            focus.get_by_role("button", name="위키").click()
            page.get_by_text("Wiki focus record.").first.wait_for()
            focus.get_by_role("button", name="다음 작업").click()
            page.get_by_text("Plan the next task").first.wait_for()
            page.get_by_text("Partial progress of the answer.").first.wait_for(timeout=5000)
            release.set()
            page.get_by_text("Final answer.").first.wait_for()
            browser.close()
            assert not errors, errors
            print("PASS: a streaming answer keeps its progress across a focus switch")
    finally:
        release.set()   # an assertion that failed mid-stream still lets the server close


if __name__ == "__main__":
    main()
