"""Exercise the refactoring conversation against synthetic API responses."""

import json
from pathlib import Path
import socket
import sys
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from mobile_browser import fixture  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    fake = FastAPI()
    mode, scan_calls, starts = "cleanup", [], []
    generation = 0
    records, specs = [], []
    run = {"id": "synthetic-run", "mode": "restructure", "state": "running", "phase": "audit",
           "goal": "Remove duplicate order handling", "done": ["No duplicated order handling"], "out": [],
           "spec": "clean-orders", "spent": {"seconds": 7, "calls": 1, "tokens": 12, "unknown": True},
           "steps": [], "tests": None, "stopped": None, "created": time.time()}
    actions, errors, requests = [], [], []

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        nonlocal mode, generation
        if path == "mobile/status":
            return {"local": True}
        if path == "channels":
            rows = fixture(path, request.method)
            rows.insert(1, {**rows[0], "id": "refactor", "label": "리펙터링", "refactor_mode": mode, "refactor_generation": generation})
            return rows
        if path == "config/refactor":
            mode = (await request.json())["refactor_mode"]
            generation += 1
            return {"kept": True}
        if path == "refactors/scan":
            scan_calls.append(request.headers.get("x-project"))
            return {"repo": "fixture", "scope": "project", "rows": [{"path": "src/orders.py", "lines": 900,
                     "cap": 800, "dup": 12, "block": 90, "churn": 4, "debt": 122}]}
        if path == "refactors":
            return {"repo": "fixture", "runs": [run] if starts else []}
        if path == "specs":
            return {"project": "fixture", "gate": "git --version", "specs": specs}
        if path == "log/refactor":
            return [] if request.query_params.get("legacy") == "true" else records
        if path == "say/refactor":
            requests.append((await request.json())["text"])
            spec = {"id": "clean-orders", "repo": "fixture", "rev": len(records) + 1,
                "goal": run["goal"], "out": [], "done": ["git --version", *run["done"]],
                "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [], "state": "정리됨",
                "source": {"focus": "refactor", "plan": None, "turn": 1, "refactor_generation": generation}, "history": [{"ts": 1}],
                "missing": [], "pr": None, "worktree": None, "gate": None, "fault": None, "report": None,
                "refactor": {"mode": mode, "files": ["src/orders.py"]}, "review_profile": "code"}
            specs[:] = [spec]
            blocks = [{"name": "spec", "id": "clean-orders"}]
            records.append({"role": "assistant", "text": "Purpose and completion conditions settled.", "blocks": blocks})
            events = [{"kind": "done", "text": records[-1]["text"]}, {"kind": "blocks", "blocks": blocks}]
            return StreamingResponse(iter(["data: " + json.dumps(e) + "\n\n" for e in events]), media_type="text/event-stream")
        if path == "specs/clean-orders/start":
            starts.append(await request.json())
            specs[0]["refactor"]["run"] = run["id"]
            return {"refactor": run["id"]}
        if path == "refactors/synthetic-run":
            return run
        if path.startswith("refactors/synthetic-run/"):
            action = path.rsplit("/", 1)[1]
            actions.append(action)
            run["state"] = "stopped" if action == "cancel" else "running"
            return run
        if path == "errors":
            errors.append(await request.json())
            return {"ok": True}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(fake, log_level="error", ws="wsproto", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            nav = page.get_by_role("navigation", name="초점", exact=True)
            expect(nav.get_by_role("button")).to_have_text(["다음 작업", "리펙터링", "위키", "회고"])
            expect(page.get_by_role("tab", name="리펙터링", exact=True)).to_have_count(0)
            nav.get_by_role("button", name="리펙터링", exact=True).click()
            candidate = page.get_by_role("button", name="중복된 처리 정리", exact=False)
            expect(candidate).to_be_visible()
            assert scan_calls == ["fixture"] and not starts
            assert not page.get_by_text("대상 파일 수", exact=True).count()
            for label in ("초 한도", "호출 한도", "토큰 한도"):
                assert not page.get_by_text(label, exact=True).count()
            candidate.click()
            start = page.get_by_role("button", name="시작 ▸", exact=True).first
            expect(start).to_be_enabled()
            picker = page.get_by_role("combobox", name="리펙터링 모드", exact=True)
            picker.select_option("restructure")
            expect(start).to_be_disabled()
            expect(page.get_by_text("Purpose and completion conditions settled.", exact=True)).to_be_visible()
            page.get_by_role("button", name="모듈 책임과 경계 재구성", exact=False).click()
            expect(start).to_be_enabled()
            start.click()
            status = page.get_by_label("리펙터링 실행", exact=True).first
            expect(status).to_be_visible()
            expect(page.get_by_label("리펙터링 실행", exact=True)).to_have_count(1)
            expect(status.get_by_text("확인된 토큰", exact=False)).not_to_be_visible()
            status.get_by_text("단계·사용량 상세", exact=True).click()
            expect(status.get_by_text("확인된 토큰", exact=False)).to_be_visible()
            status.get_by_role("button", name="멈추기", exact=True).click()
            expect(status.get_by_role("button", name="재개", exact=True)).to_be_visible()
            status.get_by_role("button", name="재개", exact=True).click()
            expect(status.get_by_role("button", name="멈추기", exact=True)).to_be_visible()
            assert len(starts) == 1 and actions == ["cancel", "resume"]
            measurements = []
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
                measurements.append(page.evaluate("""() => ({width: innerWidth, document: document.documentElement.scrollWidth,
                    mode: document.querySelector('[aria-label="리펙터링 모드"]').getBoundingClientRect().toJSON(),
                    controls: [...document.querySelectorAll('[aria-label="리펙터링 실행"] button')].map(e => {
                        const r = e.getBoundingClientRect(); return {text: e.textContent, width: r.width, height: r.height};
                    })})"""))
                assert measurements[-1]["document"] <= width, measurements[-1]
                expect(picker).to_be_visible()
            page.reload()
            page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
            page.get_by_role("navigation", name="초점", exact=True).get_by_role("button", name="리펙터링", exact=True).click()
            expect(page.get_by_label("리펙터링 실행", exact=True).first).to_be_visible()
            output = ROOT / "artifacts"
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / "refactor-conversation.png"), full_page=True)
            run.update(state="stopped", stopped={"reason": "scope", "detail": "Public migration is required"},
                scope_change={"reason": "Two independent contracts must change", "questions": [
                    {"header": "API", "question": "Change the public API?", "options": [
                        {"label": "Yes", "note": "Approve API migration"}, {"label": "No", "note": "Keep the API"}]},
                    {"header": "Storage", "question": "Change the storage schema?", "options": [
                        {"label": "Yes", "note": "Approve storage migration"}, {"label": "No", "note": "Keep storage"}]}]})
            for index in range(2):
                status.locator('fieldset').nth(index).get_by_role('radio', name='Yes', exact=True).check()
            status.get_by_role('button', name='답하고 이어 가기', exact=True).click()
            expect(start).to_be_enabled()
            answer = requests[-1]
            for context in ("Change the public API?", "Change the storage schema?", "Approve API migration",
                            "Approve storage migration", "Two independent contracts must change", "Public migration is required",
                            "1. [API] Change the public API?\nAnswer: Yes", "2. [Storage] Change the storage schema?\nAnswer: Yes"):
                assert context in answer, (context, answer)
            assert not errors, errors
            print(json.dumps({"measurements": measurements, "starts": len(starts), "actions": actions}, ensure_ascii=False))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


if __name__ == "__main__":
    main()
