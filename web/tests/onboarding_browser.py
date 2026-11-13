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
    state = {"packaged": True, "complete": False, "agent": "codex", "workspace": "D:/팀 프로젝트",
             "data": "C:/Users/fixture/AppData/Local/wiki-agent", "version": "0.1.0", "restart_required": False,
             "task": {"running": False, "action": "", "output": "", "error": "", "code": None}}
    ready, logged, requests = set(), False, []

    @fake.get("/api/mobile/status")
    def mobile():
        return {"local": True}

    @fake.api_route("/api/onboarding{path:path}", methods=["GET", "POST"])
    async def api(path: str, request: Request):
        nonlocal logged
        body = await request.json() if request.method == "POST" else {}
        requests.append((path, body))
        if path == "/detect":
            return {"tools": {name: {"ready": name in ready, "version": "fixture", "error": "fixture missing"}
                              for name in ("git", "pwsh", "gh", "fd", "jq", state["agent"])},
                    "authenticated": logged, "auth_error": "", "winget": True}
        if path == "/configure":
            state["agent"] = body["agent"]
            if body.get("workspace"):
                state["workspace"] = body["workspace"]
        elif path == "/install":
            state["task"].update(running=True, action=f"install:{body['tool']}", output="fixture progress", error="")
        elif path == "/cancel":
            state["task"].update(running=False, code=130, error="fixture canceled")
        elif path == "/login":
            logged = True
            state["task"].update(running=False, code=0, error="", output="fixture login verified")
        elif path == "/browse":
            return {"path": "D:/한글 폴더/projects"}
        elif path == "/finish":
            return JSONResponse({"detail": "fixture readiness requires project connection"}, status_code=409)
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
        page.get_by_label("사용할 도구").select_option("claude")
        page.get_by_role("button", name="선택하고 확인", exact=True).click()
        page.get_by_text("Claude Code · 준비 필요", exact=True).wait_for()
        assert not page.get_by_text("Codex · 준비 필요", exact=True).count()
        page.get_by_role("button", name="설치", exact=True).first.click()
        page.get_by_role("button", name="취소", exact=True).click()
        page.get_by_text("fixture canceled", exact=True).wait_for()
        page.get_by_role("button", name="설치", exact=True).first.click()
        page.get_by_role("button", name="취소", exact=True).click()
        page.get_by_role("button", name="공식 로그인 열기", exact=True).click()
        page.get_by_text("로그인 확인됨", exact=True).wait_for()
        page.get_by_role("button", name="폴더 찾기", exact=True).click()
        page.get_by_label("Git 프로젝트들이 들어 있는 폴더").fill("D:/한글 폴더/projects")
        page.get_by_role("button", name="폴더 저장", exact=True).click()
        page.get_by_text("선택 사항 · 서비스 키", exact=True).click()
        page.get_by_label("GEMINI_API_KEY", exact=True).fill("fixture-only-key")
        page.get_by_role("button", name="서비스 키 저장", exact=True).click()
        page.wait_for_function("document.querySelector('input[type=password]').value === ''")
        page.get_by_role("button", name="준비 확인하고 앱 열기", exact=True).click()
        page.get_by_role("alert").filter(has_text="fixture readiness requires project connection").wait_for()
        for width, height in ((1440, 900), (960, 600), (400, 800)):
            page.set_viewport_size({"width": width, "height": height})
            layout = page.evaluate("""() => {
              const controls = [...document.querySelectorAll('input,select,button')].filter(e => e.getBoundingClientRect().width);
              return {overflow: document.documentElement.scrollWidth > innerWidth,
                clipped: controls.filter(e => { const r=e.getBoundingClientRect(); return r.left < 0 || r.right > innerWidth }).length,
                contentWidth: document.querySelector('main > div').getBoundingClientRect().width};
            }""")
            assert not layout["overflow"] and not layout["clipped"], layout
            print(json.dumps({"viewport": [width, height], **layout}))
        assert any(path == "/install" for path, _ in requests)
        assert any(path == "/keys" and body["values"] == {"GEMINI_API_KEY": "fixture-only-key"} for path, body in requests)
        assert not errors, errors
        browser.close()
    print("PASS: onboarding selection, install/cancel/retry, login, workspace, secret entry, readiness errors and responsive layout")


if __name__ == "__main__":
    main()
