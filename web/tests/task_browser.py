"""Task diff visibility and answer links, using only synthetic fixture data."""

import asyncio
import json
import socket
import threading
import time
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from playwright.sync_api import sync_playwright
import uvicorn

from mobile_browser import ROOT, app, fixture, mobile
from main import loop, work


def main():
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)
    citations = []
    translations = []
    deleted = False
    compacting = False
    compact_done = threading.Event()
    restarted_feed = None
    feed_requests = []
    spec = {"id": "fixture-task", "repo": "fixture", "rev": 1, "goal": "Synthetic task requirements",
            "out": ["Do not change stored data"], "done": ["git --version", "Synthetic check passes"], "state": "작업 중",
            "worktree": "fixture-task", "workspace_mode": "branch", "branch": "fixture",
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [], "history": [],
            "source": {"plan": None}, "missing": []}
    spec["report"] = [{"item": "git --version", "pass": True}]

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        nonlocal deleted
        if path == "switch":
            return {"translate": True, "usage": {"month": "2026-10", "usd": 0, "limit": 10}}
        if path == "translate":
            data = await request.json()
            translations.extend(data["texts"])
            mapping = {"Synthetic task requirements": "합성 작업 요구사항", "Do not change stored data": "저장된 데이터를 바꾸지 않는다",
                       "Synthetic check passes": "합성 검사가 통과한다", "Revised synthetic requirements": "수정된 합성 요구사항",
                       "git --version": "깃 --버전"}
            return {"texts": [mapping.get(text, text) for text in data["texts"]], "statuses": ["translated"] * len(data["texts"])}
        if path == "log/next":
            return [{"role": "assistant", "text": "Synthetic specification", "blocks": [{"name": "spec", "id": spec["id"]}]}]
        if path == "worktrees":
            return {"project": "fixture", "repo": "fixture", "rows": [] if deleted else [
                {"path": "fixture-task", "name": "fixture-task", "branch": "fixture", "dirty": False,
                 "primary": True, "merged": False, "live": False, "busy": compacting}]}
        if path == "specs":
            return {"project": "fixture", "specs": [] if deleted else [spec]}
        if path == "specs/fixture-task/delete":
            deleted = True
            return {"ok": True}
        if path == "providers/usage":
            return {"providers": [{"provider": provider, "quota": windows, "usage": {}, "error": "", "live": True, "connection_ms": 400}
                                  for provider, windows in [("claude", [
                                      {"name": "five_hour", "used_percent": 20, "resets_at": 1800000000},
                                      {"name": "seven_day", "used_percent": 40, "resets_at": 1800600000}]),
                                      ("codex", [{"name": "codex · secondary", "window_minutes": 10080, "used_percent": 10, "resets_at": 1800600000}])]]}
        if path.startswith("providers/"):
            return {"provider": "codex", "quota": [], "usage": {"input_tokens": 1234, "output_tokens": 56, "scope": "thread"},
                    "error": "", "live": True, "connection_ms": 400}
        if path == "loops/events":
            feed_requests.append(dict(request.query_params))
            if restarted_feed is not None:
                with patch.object(work, "feed", restarted_feed):
                    return loop.events(int(request.query_params.get("after", "-1")), request.query_params.get("generation"))
            async def notices():
                await asyncio.sleep(0.5)
                stamp = time.time()
                event = {"kind": "notice", "seq": 1, "ts": stamp, "title": "에이전트 실행 완료", "body": "fixture"}
                for replay in (event, event, {**event, "ts": stamp + .001, "title": "서버 재시작 후 알림"}):
                    yield "data: " + json.dumps(replay) + "\n\n"
                await asyncio.sleep(60)
            return StreamingResponse(notices(), media_type="text/event-stream",
                                     headers={"X-Feed-Cursor": "-1", "X-Feed-Generation": "fixture-old"})
        if path == "work/events":
            async def compact_events():
                for phase in ("started", "completed"):
                    if phase == "completed":
                        while not compact_done.is_set():
                            await asyncio.sleep(0.05)
                    yield "data: " + json.dumps({"kind": "compaction", "text": "Context compaction " + phase,
                        "meta": {"phase": phase, "pre_tokens": 120000 if phase == "completed" else None},
                        "seq": 0 if phase == "started" else 1, "turn": "compact-turn", "session_id": "fixture", "parent_id": None}) + "\n\n"
                yield 'data: {"kind":"done","text":"Finished","meta":{},"seq":2,"turn":"compact-turn","session_id":"fixture","parent_id":null}\n\n'
            return StreamingResponse(compact_events(), media_type="text/event-stream")
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
                    "busy": compacting, "running": {"turn": "compact-turn", "session_id": "fixture", "seq": -1} if compacting else None,
                    "rules": [], "queued": None}
        if path == "work/diff":
            return {"diff": "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n"
                            "@@ -1 +1 @@\n-old\n+new\n", "base": "fixture", "truncated": True, "omitted": [],
                    "totals": {"files": 2, "added": 12_001, "deleted": 12_000, "binary": 0, "unknown": 0}}
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
            page.locator(".live-changes > summary").click()
            diff = page.locator('pre[aria-label="실시간 코드 diff"]')
            diff.wait_for()
            page.get_by_role("link", name="PR", exact=True).wait_for()
            measured = diff.bounding_box()
            assert measured["y"] >= 0 and measured["y"] + measured["height"] < 900, measured
            assert "-old" in diff.inner_text() and "+new" in diff.inner_text()
            summary = page.locator("summary").filter(has_text="코드 변경 현황").inner_text()
            assert "2개 파일" in summary and "+12001" in summary and "−12000" in summary
            pane = page.locator('section[aria-label="에이전트 세션"] > .overflow-y-auto')
            pane.evaluate("e => { e.scrollTop = 0 }")
            assert diff.bounding_box() == measured
            pane.evaluate("e => { e.scrollTop = e.scrollHeight }")
            assert diff.bounding_box() == measured
            page.get_by_role("tab", name="리뷰", exact=True).click()
            assert diff.is_visible()
            page.get_by_role("tab", name="에이전트", exact=True).click()
            page.get_by_label("사용 토큰", exact=True).filter(has_text="1,234 → 56 토큰").wait_for()
            limits = page.get_by_label("CLI 사용 한도", exact=True)
            limits.get_by_text("Claude", exact=True).wait_for()
            assert "5시간" in limits.inner_text() and "7일" in limits.inner_text() and "Codex" in limits.inner_text()
            assert "후 초기화" not in limits.inner_text() and limits.locator("time").count() == 3
            assert not page.get_by_text("연결 준비", exact=False).count()
            assert not page.locator(".task-spec").count()
            measurements = []
            for theme in ("dark", "light"):
                page.evaluate("theme => { document.documentElement.classList.remove('dark', 'light'); document.documentElement.classList.add(theme) }", theme)
                measurements.append(page.evaluate("""() => {
                  const summary = document.querySelector('.live-changes > summary');
                  const limits = document.querySelector('.provider-limits');
                  const s = getComputedStyle(summary), l = getComputedStyle(limits);
                  const rgb = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number);
                  const lum = color => rgb(color).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
                    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
                  const contrast = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
                  return {theme: document.documentElement.className, diffPadding: s.padding, diffFont: s.fontSize,
                    limitPadding: l.padding, diffContrast: contrast(s.color, getComputedStyle(summary.parentElement).backgroundColor),
                    limitContrast: contrast(l.color, getComputedStyle(limits.parentElement).backgroundColor),
                    limitBottom: limits.getBoundingClientRect().bottom, viewport: innerWidth, document: document.documentElement.scrollWidth};
                }"""))
                assert measurements[-1]["diffContrast"] >= 4.5 and measurements[-1]["limitContrast"] >= 4.5, measurements
            page.evaluate("document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark')")
            page.set_viewport_size({"width": 1200, "height": 800})
            if not limits.locator("details").evaluate("e => e.open"):
                page.get_by_text("한도", exact=True).click()
            limits.get_by_text("Claude", exact=True).wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_text("한도", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 900})
            print(json.dumps({"task_layout": measurements}))
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
            page.get_by_role("navigation", name="초점", exact=True).get_by_role("button", name="다음 작업", exact=True).click()
            page.get_by_text("합성 작업 요구사항", exact=True).wait_for()
            page.get_by_text("범위 · 완료 조건 보기", exact=True).click()
            page.get_by_text("저장된 데이터를 바꾸지 않는다", exact=False).wait_for()
            page.get_by_text("합성 검사가 통과한다", exact=False).wait_for()
            assert "Do not change stored data" in translations
            assert "git --version" not in translations, "Executable gate was sent for prose translation"
            page.get_by_text("· git --version", exact=True).wait_for()
            page.get_by_role("button", name="명세 수정", exact=True).click()
            assert page.get_by_role("textbox", name="목표", exact=True).input_value() == "Synthetic task requirements"
            page.get_by_role("textbox", name="목표", exact=True).fill("Revised synthetic requirements")
            page.get_by_role("button", name="저장", exact=True).click()
            page.get_by_text("수정된 합성 요구사항", exact=True).wait_for()
            page.get_by_text("변경 이력 · 현재 판 2", exact=True).wait_for()
            if not limits.locator("details").evaluate("e => e.open"):
                limits.locator("summary").click()
            page.screenshot(path=str(ROOT / "artifacts" / "task-ui-fixture.png"))
            assert not errors, errors
            # Check desktop link dispatch without launching a real browser or app.
            desktop = browser.new_page(viewport={"width": 1440, "height": 900})
            compacting = True
            desktop.add_init_script("""window.openedUrls = []; window.notifications = [];
              window.__TAURI_INTERNALS__ = {
                metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}},
                transformCallback: () => 1,
                unregisterCallback: () => {},
                invoke: async (command, args) => {
                  if (command === 'open_url') window.openedUrls.push(args.url);
                  if (command === 'plugin:notification|notify') window.notifications.push(args.options);
                  if (command === 'plugin:notification|is_permission_granted') return true;
                  return 1;
                }
              };
              window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: () => {}};
            """)
            desktop.goto(base)
            desktop.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(
                has_text="fixture-task").click()
            desktop.get_by_text("문맥 압축 중 · 대화 기록을 요약하고 있다", exact=True).wait_for()
            compact_done.set()
            desktop.get_by_text("문맥 압축 완료", exact=False).wait_for()
            assert not desktop.get_by_text("문맥 압축 중 · 대화 기록을 요약하고 있다", exact=True).count()
            desktop.wait_for_function("window.notifications.length === 2")
            assert desktop.evaluate("window.notifications[0].title") == "에이전트 실행 완료"
            assert desktop.evaluate("window.notifications[1].title") == "서버 재시작 후 알림"
            # Reconnect through the real endpoint with an old cursor/generation.
            restarted_feed = work.Feed()
            restarted_feed.put({"kind": "notice", "ts": time.time(), "title": "재시작 전송 대기 알림", "body": "fixture"})
            restarted_feed.done = True
            desktop.evaluate("window.dispatchEvent(new Event('mobile-reconnect'))")
            desktop.wait_for_function("window.notifications.length === 3")
            assert desktop.evaluate("window.notifications[2].title") == "재시작 전송 대기 알림"
            assert any(r.get("after") == "1" and r.get("generation") == "fixture-old" for r in feed_requests), feed_requests
            desktop.get_by_role("link", name="PR", exact=True).click()
            desktop.wait_for_function("window.openedUrls.length === 1")
            assert desktop.evaluate("window.openedUrls") == ["https://example.com/pull/7"]
            assert desktop.url == base + "/"
            desktop.close()
            compacting = False
            row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
            row.click(button="right")
            page.get_by_role("menuitem", name="삭제", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="삭제", exact=True).click()
            row.wait_for(state="detached")
            page.reload()
            page.get_by_text("아직 작업이 없다.", exact=False).wait_for()
            assert not page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").count()
            browser.close()
            print("PASS: fixed diff across scrolling/tabs; translated spec editing; all CLI limits; tokens; native notices; compaction; persistent task deletion; source links")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


if __name__ == "__main__":
    main()
