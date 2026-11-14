"""Exercise onboarding controls and layout against synthetic local setup responses."""

import argparse
import json
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from browser_fixture import page_of, served

ROOT = Path(__file__).resolve().parents[2]
mimetypes.add_type("application/javascript", ".js")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "web/dist")
    args = parser.parse_args()
    fake = FastAPI()
    state = {"packaged": True, "complete": False, "agents": ["codex"],
             "data": "C:/Users/fixture/AppData/Local/wiki-agent-data", "version": "0.1.0", "restart_required": False,
             "task": {"running": False, "action": "", "output": "", "error": "", "code": None}}
    ready, logged, requests = set(), set(), []

    @fake.get("/api/mobile/status")
    def mobile():
        return {"local": True}

    @fake.api_route("/api/onboarding{path:path}", methods=["GET", "POST"])
    async def api(path: str, request: Request):
        body = await request.json() if request.method == "POST" else {}
        requests.append((path, body))
        if path == "/detect":
            return {"tools": {name: {"ready": name in ready, "version": "fixture", "error": "fixture missing"}
                              for name in ("git", "pwsh", "gh", "fd", "jq", "codex", "claude")},
                    "accounts": {name: {"authenticated": name in logged, "error": ""} for name in ("codex", "claude", "gh")}}
        if path == "/configure":
            state["agents"] = body["agents"]
        elif path == "/install":
            state["task"].update(running=True, action=f"install:{body['tool']}", output="fixture progress", error="")
        elif path == "/cancel":
            state["task"].update(running=False, code=130, error="fixture canceled")
        elif path == "/login":
            logged.add(body["provider"])
            state["task"].update(running=False, code=0, error="", output="fixture login verified")
        elif path == "/finish":
            return JSONResponse({"detail": "fixture readiness requires tools"}, status_code=409)
        return state

    fake.mount("/", StaticFiles(directory=args.dist, html=True))
    with served(fake) as port, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        errors = []
        page = page_of(browser, errors)
        page.goto(f"http://127.0.0.1:{port}")
        try:
            page.get_by_role("heading", name="이 PC에서 시작하기").wait_for(timeout=10000)
        except Exception:
            print(json.dumps({"errors": errors, "requests": requests, "body": page.locator('body').inner_text()}, ensure_ascii=True))
            raise
        page.get_by_label("Claude Code 사용").check()
        page.wait_for_function("document.querySelector('input[aria-label=\"Claude Code 사용\"]').checked")
        assert state["agents"] == ["codex", "claude"]
        assert page.get_by_label("Codex 사용").is_checked()
        assert not page.get_by_role("button", name="폴더 찾기").count()
        assert not page.locator('select').count()
        page.get_by_role("button", name="설치", exact=True).first.click()
        page.get_by_role("button", name="취소", exact=True).click()
        page.get_by_text("fixture canceled", exact=True).wait_for()
        page.get_by_role("button", name="설치", exact=True).first.click()
        page.get_by_role("button", name="취소", exact=True).click()
        ready.update({"codex", "claude", "gh"})
        page.get_by_role("button", name="상태 확인", exact=True).click()
        page.locator('.account-row').filter(has_text="GitHub").get_by_role("button", name="로그인", exact=True).click()
        page.locator('.account-row').filter(has_text="GitHub").get_by_text("연결됨", exact=True).wait_for()
        page.get_by_role("button", name="서비스 키", exact=True).click()
        page.get_by_label("GEMINI_API_KEY", exact=True).fill("fixture-only-key")
        page.get_by_role("button", name="서비스 키 저장", exact=True).click()
        page.wait_for_function("document.querySelector('input[type=password]').value === ''")
        page.get_by_role("button", name="준비 확인하고 앱 열기", exact=True).click()
        page.get_by_role("alert").filter(has_text="fixture readiness requires tools").wait_for()
        assert any(path == "/install" for path, _ in requests)
        assert any(path == "/keys" and body["values"] == {"GEMINI_API_KEY": "fixture-only-key"} for path, body in requests)
        assert not errors, errors
        browser.close()
    print("PASS: independent agents, install/cancel/retry, GitHub login, no project picker and secret entry")


if __name__ == "__main__":
    main()
