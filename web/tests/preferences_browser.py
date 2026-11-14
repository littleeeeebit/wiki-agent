"""Render desktop preferences with synthetic accounts and updater IPC; no paid calls."""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from browser_fixture import page_of, served
from mobile_browser import fixture

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/settings-redesign-after"


def main():
    fake = FastAPI()
    writes = []
    setup = {"packaged": True, "complete": True, "agents": ["codex"], "version": "0.1.6",
             "data": "C:/Users/example/AppData/Local/wiki-agent-data", "restart_required": False,
             "task": {"running": False, "action": "", "output": "", "error": ""}}
    loop = {"rounds": 12, "concurrent": 2}
    blank = False

    @fake.api_route("/api/{path:path}", methods=["GET", "POST"])
    async def api(path: str, request: Request):
        if path == "channels" and blank:
            return []
        if request.method == "POST":
            body = await request.json()
            writes.append((path, body))
            if path == "loop/settings":
                loop.update(body)
            if path == "onboarding/configure":
                setup["agents"] = body["agents"]
        if path == "onboarding":
            return setup
        if path == "onboarding/configure":
            return setup
        if path == "onboarding/detect":
            return {"tools": {n: {"ready": True, "version": "Installed 1.2.3"} for n in ("git", "pwsh", "gh", "fd", "jq", "codex", "claude")},
                    "accounts": {n: {"authenticated": n == "codex", "error": ""} for n in ("codex", "claude", "gh")}}
        if path == "mobile/status":
            return {"local": True, "enabled": False, "starting": False, "apk_available": False}
        if path == "options":
            return {**fixture(path, request.method), "packaged": True,
                    "models": [{"id": "codex:example", "label": "Codex"}], "efforts": [{"id": "high", "label": "High"}]}
        if path == "connect":
            return {"rows": [], "hub": {"name": "wiki-agent", "needed": False, "refused": ""},
                    "settings": {"survey": True, "survey_tokens": 400000, "survey_minutes": 30, "survey_model": "codex:example", "survey_effort": "high"}}
        if path == "architecture/settings":
            return {"omm_model": "", "omm_effort": ""}
        if path == "recovery/settings":
            return {"research_seconds": 600, "research_calls": 40, "research_tokens": 200000}
        if path == "loop/settings":
            return loop
        if path == "jev":
            return {"mode": "off", "mode_source": "default", "file_mode": "off", "canary": False,
                    "active_projects": [], "model": "Jev", "key": False, "key_source": "none", "health": "disabled",
                    "problem": "", "disabled_sources": [], "limits": {"seconds": 30, "calls": 8, "candidates": 40}}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    OUT.mkdir(parents=True, exist_ok=True)
    measurements = []
    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        errors = []
        page = page_of(browser, errors, viewport={"width": 1040, "height": 760})
        origin = f"http://127.0.0.1:{port}"
        page.goto(origin + "/?page=settings")
        page.get_by_text("Installed 1.2.3", exact=True).first.wait_for()
        assert page.get_by_label("Codex 사용").is_checked()
        page.get_by_label("Claude Code 사용").check()
        page.wait_for_function("document.querySelector('input[aria-label=\"Claude Code 사용\"]').checked")
        assert setup["agents"] == ["codex", "claude"]
        page.get_by_role("button", name="리뷰", exact=True).click()
        page.get_by_label("라운드 상한").fill("15")
        page.get_by_role("button", name="일반 및 알림", exact=True).click()
        page.get_by_role("button", name="리뷰", exact=True).click()
        assert page.get_by_label("라운드 상한").input_value() == "15"
        page.get_by_role("button", name="변경 사항 저장").click()
        page.wait_for_function("[...document.querySelectorAll('button')].some(b => b.textContent === '변경 사항 저장' && b.disabled)")
        assert loop["rounds"] == 15
        for category in ("계정 및 PC 준비", "일반 및 알림", "프로젝트 연결", "질문 · Jev", "리뷰", "휴대폰 연결"):
            page.get_by_role("button", name=category, exact=True).click()
            assert not page.get_by_role("dialog", name="설정", exact=True).count()
        page.get_by_role("button", name="계정 및 PC 준비", exact=True).click()
        for screen in ("settings", "onboarding"):
            if screen == "onboarding":
                setup["complete"] = False
                page.goto(origin)
                page.get_by_role("heading", name="이 PC에서 시작하기").wait_for()
            for width, height in ((1920, 1080), (1440, 900), (1040, 760), (800, 560), (400, 800)):
                page.set_viewport_size({"width": width, "height": height})
                row = page.evaluate("""() => {
                  const controls = [...document.querySelectorAll('button,input,select')].filter(e=>e.getBoundingClientRect().width);
                  return {viewport:[innerWidth,innerHeight], documentHeight:document.documentElement.scrollHeight,
                    overflow:document.documentElement.scrollWidth>innerWidth,
                    clipped:controls.filter(e=>{const r=e.getBoundingClientRect();return r.left<0||r.right>innerWidth}).length,
                    actionTop:document.querySelector('.setup-footer,.preferences-footer')?.getBoundingClientRect().top,
                    contentWidth:document.querySelector('main').getBoundingClientRect().width};
                }""")
                assert not row["overflow"] and not row["clipped"], (screen, row)
                if width >= 800:
                    assert row["actionTop"] < height and row["documentHeight"] == height, (screen, row)
                measurements.append({"screen": screen, **row})
                page.screenshot(path=str(OUT / f"{screen}-{width}.png"))
        setup["complete"] = True
        blank = True
        page.goto(origin)
        try:
            page.get_by_role("region", name="모든 프로젝트", exact=True).wait_for(timeout=5000)
        except Exception:
            print(json.dumps({"errors": errors, "body": page.locator('body').inner_text()}, ensure_ascii=True))
            raise
        page.get_by_role("button", name="폴더 찾기", exact=True).wait_for()
        blank = False
        page.set_viewport_size({"width": 1040, "height": 760})
        page.add_init_script("""window.__fixtureAvailable = false; window.__fixtureError = false;
          window.__TAURI_INTERNALS__ = {metadata:{currentWindow:{label:'updates'},currentWebview:{label:'updates'}},
            transformCallback:()=>1, unregisterCallback:()=>{}, invoke:async command=> {
              if(command==='package_update') {
                if(window.__fixtureError) throw Error('fixture network failure');
                return {packaged:true,current:'0.1.6',version:'0.1.7',available:window.__fixtureAvailable,
                  releases:[null,{version:{},notes:'invalid'}, {version:'0.1.7',notes:'계정 상태와 새 창 개선',date:{}},{version:'0.1.6',notes:'설정과 온보딩 개선'}]};
              } return 1;
            }};""")
        page.goto(origin + "/?page=updates")
        page.get_by_role("status").filter(has_text="최신 버전을 사용하고 있다").wait_for()
        page.get_by_role("heading", name="v0.1.6", exact=True).wait_for()
        page.evaluate("window.__fixtureAvailable = true")
        page.get_by_role("button", name="업데이트 확인", exact=True).click()
        page.get_by_role("button", name="업데이트 설치", exact=True).wait_for()
        page.screenshot(path=str(OUT / "updates-1040.png"))
        page.evaluate("window.__fixtureError = true")
        page.get_by_role("button", name="업데이트 확인", exact=True).click()
        page.get_by_role("alert").filter(has_text="fixture network failure").wait_for()
        page.evaluate("window.__fixtureError = false")
        page.get_by_role("button", name="업데이트 확인", exact=True).click()
        page.wait_for_function("!document.querySelector('[role=alert]')")
        assert not errors, errors
        browser.close()
    (OUT / "measurements.json").write_text(json.dumps(measurements, indent=2) + "\n", encoding="utf-8")
    print("PASS: standalone settings, additive accounts, retained drafts, real save requests, latest/update/error/retry, version history and five viewport sizes")


if __name__ == "__main__":
    main()
