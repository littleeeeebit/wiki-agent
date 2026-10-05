"""Task diff visibility and answer links, using only synthetic fixture data."""

import asyncio
import json
import socket
import sys
import threading
import time
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from playwright.sync_api import expect, sync_playwright
import uvicorn

from mobile_browser import ROOT, app, fixture, mobile
from main import architecture, loop, work


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)
    citations = []
    translations = []
    say_requests = []
    conversation_records = {}
    translation_mode = "full"
    deleted = False
    compacting = False
    compact_done = threading.Event()
    restarted_feed = None
    feed_requests = []
    fast_requests = []
    screen_errors = []
    fast_models = [("opus", True), ("opus[1m]", True), ("claude-opus-4-8", True),
                   ("claude-opus-5", True), ("claude-opus-5-5", True),
                   ("claude-opus-5-5-20261001[1m]", True), ("claude-opus-4-6", False),
                   ("claude-opus-4-7", False), ("claude-opus-5-9", False),
                   ("sonnet", False), ("codex:fixture-fast", True), ("codex:fixture-standard", False)]
    architecture_revision = 1
    architecture_installed = False
    architecture_adds = []
    architecture_nodes = architecture.read_existing(ROOT)["nodes"]
    suite_fault = False
    suite_rows = [{"id": f"suite-{kind}", "kind": kind, "target": "fixture-task" if kind != "query" else "wiki",
                   "task": "fixture-task" if kind != "query" else None, "path": "fixture-task" if kind != "query" else None,
                   "pr": 7 if kind == "review" else None, "status": status, "model": "Synthetic cell model",
                   "cell": f"cell-{kind}", "started_at": time.time() - 60, "finished_at": None,
                   "prompt": "Synthetic suite instruction", "text": "Synthetic suite answer",
                   "error": "Synthetic review failure" if kind == "review" else "",
                   "steps": [{"kind": "tool", "text": "Synthetic suite check"}]}
                  for kind, status in (("work", "running"), ("review", "failed"), ("planning", "completed"), ("query", "completed"))]
    spec = {"id": "fixture-task", "repo": "fixture", "rev": 1, "goal": "Synthetic task requirements",
            "out": ["Do not change stored data"], "done": ["git --version", "Synthetic check passes"], "state": "작업 중",
            "worktree": "fixture-task", "workspace_mode": "branch", "branch": "fixture",
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [], "history": [],
            "source": {"plan": None}, "missing": []}
    spec["report"] = [{"item": "git --version", "pass": True}]

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        nonlocal deleted, architecture_installed, translation_mode
        if path == "switch":
            if request.method == "POST":
                translation_mode = (await request.json())["mode"]
            return {"translate": translation_mode != "off", "mode": translation_mode,
                    "usage": {"month": "2026-10", "usd": 0, "limit": 10}}
        if path == "suite":
            assert request.headers.get("x-project") == "fixture"
            if suite_fault:
                return JSONResponse({"detail": "Synthetic suite unavailable"}, status_code=503)
            return {"repo": "fixture", "rows": suite_rows, "history_limit": 100}
        if path == "errors":
            screen_errors.append(await request.json())
            return {"ok": True}
        if path == "options":
            options = fixture(path, request.method)
            options["models"].extend({"id": model, "label": model, "note": "", "supports_fast": supported}
                                     for model, supported in fast_models)
            return options
        if path == "translate":
            data = await request.json()
            translations.extend(data["texts"])
            mapping = {"Synthetic task requirements": "합성 작업 요구사항", "Do not change stored data": "저장된 데이터를 바꾸지 않는다",
                       "Synthetic check passes": "합성 검사가 통과한다", "Revised synthetic requirements": "수정된 합성 요구사항",
                       "git --version": "깃 --버전", "Web": "화면", "Server": "서버", "Router": "요청 분배",
                       "Request handling": "요청 처리", "Web internals": "화면 내부", "Leaf details": "말단 설명",
                       "overall architecture / web": "전체 구조 / 화면", "전체 구조 / web": "전체 구조 / 화면"}
            return {"texts": [mapping.get(text, text) for text in data["texts"]], "statuses": ["translated"] * len(data["texts"])}
        if path.startswith("log/") and path.split("/")[1] in conversation_records:
            return [] if request.query_params.get("legacy") == "true" else conversation_records[path.split("/")[1]]
        if path == "log/next":
            return [{"role": "assistant", "text": "Synthetic specification", "blocks": [{"name": "spec", "id": spec["id"]}]}]
        if request.method == "POST" and path.startswith("say/"):
            focus = path.split("/")[1]
            say_requests.append(focus)
            frames = [{"kind": "tool", "text": f"Synthetic interactive tool {focus}", "seq": 0},
                      {"kind": "done", "text": f"Synthetic interactive answer {focus}", "seq": 1}]
            conversation_records[focus] = [{"role": "user", "text": "Synthetic interactive question"},
                                           {"role": "assistant", "text": frames[-1]["text"]}]
            async def interactive():
                for frame in frames:
                    yield "data: " + json.dumps(frame) + "\n\n"
                    await asyncio.sleep(0.1)
            return StreamingResponse(interactive(), media_type="text/event-stream")
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
                        "seq": 0 if phase == "started" else 2, "turn": "compact-turn", "session_id": "fixture", "parent_id": None}) + "\n\n"
                    if phase == "started":
                        yield 'data: {"kind":"tool","text":"Background bg-fixture · running · 40 percent","meta":{"tool":"background","task_id":"bg-fixture","status":"running"},"seq":1,"turn":"compact-turn","session_id":"fixture","parent_id":null}\n\n'
                yield 'data: {"kind":"tool","text":"Background bg-fixture · completed · 10/10","meta":{"tool":"background","task_id":"bg-fixture","status":"completed"},"seq":3,"turn":"compact-turn","session_id":"fixture","parent_id":null}\n\n'
                yield 'data: {"kind":"tool","text":"RAW_BACKGROUND_OUTPUT","meta":{"tool":"tool_result"},"seq":4,"turn":"compact-turn","session_id":"fixture","parent_id":null}\n\n'
                yield 'data: {"kind":"done","text":"Finished","meta":{},"seq":5,"turn":"compact-turn","session_id":"fixture","parent_id":null}\n\n'
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
            reply = f"Synthetic final reply {translation_mode}\n\n" + "\n\n".join("Long synthetic paragraph " + str(i) for i in range(50))
            reply += "\n\n[Windows source](C:/fixture/src/app.ts:12) "
            reply += "[Encoded file](file:///C:/fixture/My%20Project/app.ts#L20) "
            reply += "[Relative source](src/app.ts:3) [PR](https://example.com/pull/7)"
            return {"rows": [{"role": "assistant", "text": reply,
                    "steps": [{"kind": "progress", "text": f"Synthetic translation progress {translation_mode}"},
                              {"kind": "tool", "text": f"Synthetic translation tool {translation_mode}"},
                              {"kind": "approval", "id": "translation-choice", "tool": "requestUserInput",
                               "text": "Choose", "answer": "none", "by": "person",
                               "input": {"questions": [{"header": "Choice", "question": f"Synthetic translation question {translation_mode}",
                                                       "options": [{"label": "Keep", "description": f"Synthetic translation option {translation_mode}"}]}]}}]}], "session_id": "fixture",
                    "busy": compacting, "running": {"turn": "compact-turn", "session_id": "fixture", "seq": -1} if compacting else None,
                    "rules": [], "queued": None}
        if path == "work/diff":
            return {"diff": "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n"
                            "@@ -1,2 +1,2 @@\n-old\n+new\n---legacy\n+++counter\n", "base": "fixture", "truncated": True, "omitted": [],
                    "totals": {"files": 2, "added": 12_001, "deleted": 12_000, "binary": 0, "unknown": 0},
                    "files": [{"path": "src/app.ts", "added": 12001, "deleted": 12000, "binary": False, "untracked": False},
                              {"path": "assets/binary.bin", "added": None, "deleted": None, "binary": True, "untracked": True}]}
        if path == "work/say":
            fast_requests.append(await request.json())
            return StreamingResponse(iter(['data: {"kind":"done","text":"Finished","meta":{},"seq":0,"turn":"fast-turn","session_id":"fixture"}\n\n']), media_type="text/event-stream")
        if path == "architecture":
            if request.method == "POST":
                architecture_adds.append(request.headers.get("x-project"))
                architecture_installed = True
            if not architecture_installed:
                return {"repo": "fixture", "installed": False, "revision": "missing", "files": 0, "nodes": []}
            return {"repo": "fixture", "installed": True, "revision": str(architecture_revision), "files": architecture_revision,
                    "nodes": [{"path": "overall-architecture", "description": f"Synthetic source revision {architecture_revision}",
                               "diagram": 'graph LR\n    web["Web\\nweb/src/"]\n    tool["Server\\ntool/main/"]\n    web -->|"HTTP"| tool\n'},
                              {"path": "overall-architecture/web", "description": "Web internals",
                               "diagram": 'graph TD\nrouter["Router\\nweb/src/App.tsx"]\nrouter -->|Request handling| router'},
                              {"path": "overall-architecture/web/router", "description": "Leaf details", "diagram": ""},
                              *[{**node, "path": "actual/" + node["path"]} for node in architecture_nodes]]}
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
            page.route("**/api/worktrees", lambda route: route.fulfill(json={"project": "fixture", "repo": "fixture", "rows": []}))
            page.route("**/api/specs", lambda route: route.fulfill(json={"project": "fixture", "specs": []}))
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(base)
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="선택한 작업").click()
                    page.get_by_role("button", name="작업 옵션 열기", exact=True).click()
                agent = page.get_by_label("에이전트 세션", exact=True)
                picker = agent.get_by_role("combobox").first
                picker.wait_for()
                expect(picker).to_be_enabled()
                picker.click()
                page.get_by_role("option", name="codex:fixture-fast", exact=True).click()
                assert agent.get_by_label("선택한 작업 모델", exact=True).inner_text() == "codex:fixture-fast"
                assert agent.get_by_role("textbox").is_disabled()
            page.unroute("**/api/worktrees")
            page.unroute("**/api/specs")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            # Fresh pages avoid overlay cache hits hiding accidental requests.
            for mode in ("off", "partial", "full"):
                probe = browser.new_page(viewport={"width": 1440, "height": 900})
                probe.goto(base)
                probe.get_by_role("button", name="설정", exact=True).first.click()
                picker = probe.get_by_role("combobox", name="한국어 번역 모드")
                expect(picker.locator("option")).to_have_text(["끄기", "전체 활성화", "일부 활성화"])
                picker.select_option(mode)
                probe.get_by_role("dialog").get_by_role("button", name="닫기", exact=True).click()
                translations.clear()
                probe.reload()
                probe.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").click()
                probe.get_by_text(f"Synthetic final reply {mode}", exact=True).wait_for()
                probe.get_by_text(f"Synthetic translation option {mode}", exact=True).wait_for()
                probe.wait_for_timeout(300)
                assert (f"Synthetic final reply {mode}" in translations) == (mode != "off"), (mode, translations)
                assert (f"Synthetic translation question {mode}" in translations) == (mode != "off"), (mode, translations)
                assert (f"Synthetic translation option {mode}" in translations) == (mode != "off"), (mode, translations)
                assert (f"Synthetic translation progress {mode}" in translations) == (mode == "full"), (mode, translations)
                assert (f"Synthetic translation tool {mode}" in translations) == (mode == "full"), (mode, translations)
                if mode == "partial":
                    for focus, label in (("wiki", "위키"), ("retro", "회고"), ("next", "다음 작업")):
                        focus_button = probe.get_by_role("navigation", name="초점", exact=True).get_by_role("button", name=label, exact=True)
                        if focus_button.get_attribute("aria-pressed") != "true":
                            with probe.expect_response(lambda response: response.url.split("?")[0].endswith(f"/api/log/{focus}")):
                                focus_button.focus()
                                focus_button.press("Enter")
                        expect(focus_button).to_have_attribute("aria-pressed", "true")
                        probe.wait_for_timeout(100)
                        composer = probe.get_by_label("대화", exact=True).get_by_role("textbox", name="질문 또는 지시")
                        composer.fill("Synthetic interactive question")
                        composer.press("Enter")
                        probe.wait_for_timeout(300)
                        assert focus in say_requests, (focus, say_requests,
                            probe.get_by_label("대화", exact=True).inner_text()[:2000])
                        probe.get_by_text(f"Synthetic interactive answer {focus}", exact=True).wait_for()
                        probe.wait_for_timeout(200)
                        assert f"Synthetic interactive answer {focus}" in translations, translations
                        assert f"Synthetic interactive tool {focus}" in translations, translations
                probe.close()
            translation_mode = "full"
            page.reload()
            page.evaluate("window.dispatchEvent(new ErrorEvent('error', {error: new Error('Synthetic screen failure')}))")
            page.wait_for_timeout(200)
            assert screen_errors and screen_errors[0]["message"] == "Synthetic screen failure", screen_errors
            page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(
                has_text="fixture-task").click()
            page.locator(".live-changes > summary").click()
            page.get_by_label("변경된 파일 목록").locator("summary").filter(has_text="src/app.ts").click()
            diff = page.locator('pre[aria-label="src/app.ts diff"]')
            diff.wait_for()
            page.get_by_role("link", name="PR", exact=True).wait_for()
            measured = diff.bounding_box()
            assert measured["y"] >= 0 and measured["y"] + measured["height"] < 900, measured
            assert "-old" in diff.inner_text() and "+new" in diff.inner_text()
            diff_colors = []
            for theme in ("dark", "light"):
                page.evaluate("theme => { document.documentElement.classList.remove('dark', 'light'); document.documentElement.classList.add(theme) }", theme)
                colors = diff.evaluate("""el => ['+new', '-old', '+++counter', '---legacy', '+++ b/src/app.ts', '--- a/src/app.ts'].map(text => {
                  const row = [...el.children].find(row => row.textContent === text);
                  return getComputedStyle(row).color.match(/[\\d.]+/g).slice(0, 3).map(Number);
                })""")
                assert colors[0][1] > colors[0][0] and colors[0][1] > colors[0][2], colors
                assert colors[1][0] > colors[1][1] and colors[1][0] > colors[1][2], colors
                assert colors[2] == colors[0] and colors[3] == colors[1], colors
                assert colors[4] == colors[5] and colors[4] not in colors[:2], colors
                diff_colors.append({"theme": theme, "added": colors[0], "deleted": colors[1]})
            page.evaluate("document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark')")
            print(json.dumps({"diff_colors": diff_colors}))
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
            fast = page.get_by_role("switch", name="FAST 모드")
            assert fast.get_attribute("aria-checked") == "false"
            assert fast.is_disabled()  # The unresolved CLI default is not a capability signal.
            model_picker = page.get_by_label("에이전트 세션", exact=True).get_by_role("combobox").first
            for model, supported in fast_models:
                model_picker.click()
                page.get_by_role("option", name=model, exact=True).click()
                assert fast.is_enabled() == supported, model
                assert fast.get_attribute("aria-checked") == "false", model
            model_picker.click()
            page.get_by_role("option", name="opus", exact=True).click()
            fast.click()
            assert fast.get_attribute("aria-checked") == "true"
            palette = []
            for theme in ("dark", "light"):
                page.evaluate("theme => { document.documentElement.classList.remove('dark', 'light'); document.documentElement.classList.add(theme) }", theme)
                palette.append(fast.evaluate("""el => {
                  const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
                  const sample = (color, background) => {
                    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = background; ctx.fillRect(0, 0, 1, 1);
                    ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
                  };
                  const lum = rgb => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
                    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
                  const measure = () => {
                    const s = getComputedStyle(el), parent = getComputedStyle(document.body).backgroundColor;
                    const background = sample(s.backgroundColor, parent), foreground = sample(s.color, 'white');
                    return (Math.max(lum(background), lum(foreground)) + .05) / (Math.min(lum(background), lum(foreground)) + .05);
                  };
                  const original = el.className, outlined = measure();
                  el.className = original.replace('bg-primary/10 text-primary', 'bg-primary text-primary-foreground');
                  const filled = measure(); el.className = original;
                  return {theme: document.documentElement.className, outlined, filled, font: getComputedStyle(el).fontSize};
                }"""))
                assert min(palette[-1]["outlined"], palette[-1]["filled"]) >= 4.5, palette
            page.evaluate("document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark')")
            print(json.dumps({"fast_palette": palette}))
            page.screenshot(path=str(ROOT / "artifacts" / "task-features-fixture.png"))
            composer = page.get_by_label("에이전트 세션", exact=True).get_by_role("textbox", name="질문 또는 지시")
            composer.fill("Synthetic FAST enabled instruction")
            composer.press("Enter")
            fast.wait_for(state="visible")
            page.wait_for_function("!document.querySelector('[aria-label=\"FAST 모드\"]').disabled")
            assert fast_requests[-1]["fast"] is True
            # Switching to an unsupported identity clears an enabled preference.
            model_picker.click()
            page.get_by_role("option", name="sonnet", exact=True).click()
            assert fast.is_disabled() and fast.get_attribute("aria-checked") == "false"
            model_picker.click()
            page.get_by_role("option", name="opus", exact=True).click()
            assert fast.get_attribute("aria-checked") == "false"
            fast.click()
            fast.click()
            assert fast.get_attribute("aria-checked") == "false"
            composer.fill("Synthetic FAST disabled instruction")
            composer.press("Enter")
            page.wait_for_function("!document.querySelector('[aria-label=\"FAST 모드\"]').disabled")
            assert fast_requests[-1]["fast"] is False
            page.get_by_role("tab", name="스위트", exact=True).click()
            suite = page.get_by_role("region", name="스위트 · 셀 실행 목록", exact=True)
            suite.locator("summary").filter(has_text="작업 · fixture-task").wait_for()
            assert suite.locator("li > details > summary").count() == 4
            suite.get_by_label("실행 상태").select_option("active")
            assert suite.locator("li > details > summary").count() == 1
            suite.locator("summary").filter(has_text="작업 · fixture-task").click()
            suite.get_by_text("Synthetic suite check", exact=True).wait_for()
            suite.get_by_text("실행 지시", exact=True).click()
            suite.get_by_text("Synthetic suite instruction", exact=True).wait_for()
            suite_rows[0].update(status="completed", finished_at=time.time())
            suite.get_by_text("표시할 셀 실행이 없다.", exact=True).wait_for(timeout=6000)
            suite.get_by_label("실행 상태").select_option("completed")
            assert suite.locator("li > details > summary").count() == 3
            suite.get_by_label("실행 상태").select_option("failed")
            suite.locator("summary").filter(has_text="리뷰 · fixture-task").click()
            suite.get_by_text("Synthetic review failure", exact=True).wait_for()
            suite.get_by_role("button", name="리뷰에서 열기", exact=True).click()
            assert page.get_by_role("tab", name="리뷰", exact=True).get_attribute("aria-selected") == "true"
            suite.get_by_label("실행 상태").select_option("all")
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
                    assert page.get_by_role("button", name="스위트", exact=True).bounding_box()["height"] >= 44
                suite.wait_for(state="visible")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "artifacts" / f"suite-{width}-fixture.png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            suite_fault = True
            suite.get_by_role("alert").wait_for(timeout=6000)
            assert "Synthetic suite unavailable" in suite.get_by_role("alert").inner_text()
            suite_fault = False
            suite.get_by_role("alert").wait_for(state="detached", timeout=6000)
            suite.locator("summary").filter(has_text="작업 · fixture-task").click()
            suite.get_by_role("button", name="에이전트에서 열기", exact=True).click()
            assert page.get_by_role("tab", name="에이전트", exact=True).get_attribute("aria-selected") == "true"
            page.get_by_role("tab", name="앱 구조", exact=True).click()
            add_omm = page.get_by_role("button", name=".omm 추가", exact=True)
            add_omm.wait_for()
            assert not architecture_adds
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert add_omm.bounding_box()["height"] >= 44
            page.set_viewport_size({"width": 1440, "height": 900})
            page.route("**/assets/mermaid.core-*.js", lambda route: route.fulfill(status=404, body="Missing old build chunk"))
            add_omm.click()
            add_omm.wait_for(state="detached")
            assert architecture_adds == ["fixture"]
            reload_screen = page.get_by_role("button", name="화면 새로고침", exact=True)
            reload_screen.wait_for()
            page.get_by_role("region", name="앱 구조", exact=True).get_by_text("도표를 표시하지 못했다", exact=False).wait_for()
            page.unroute("**/assets/mermaid.core-*.js")
            reload_screen.click()
            page.wait_for_load_state()
            page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(
                has_text="fixture-task").click()
            model_picker.click()
            page.get_by_role("option", name="opus", exact=True).click()
            page.get_by_role("tab", name="앱 구조", exact=True).click()
            diagram = page.get_by_role("group", name="overall-architecture 구조 도표")
            diagram.locator("svg").wait_for()
            assert "HTTP" in diagram.inner_text()
            page.wait_for_function("document.querySelector('.architecture-diagram')?.textContent.includes('화면')")
            assert "web/src/" in diagram.inner_text() and "서버" in diagram.inner_text(), diagram.evaluate("el => ({text:el.innerText, content:el.textContent})")
            def transform():
                return diagram.evaluate("el => [Number(el.dataset.scale), Number(el.dataset.x), Number(el.dataset.y)]")
            box = diagram.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(100)
            assert transform() == [1, 0, 0]
            page.mouse.down(button="right")
            page.mouse.move(x - 40, y - 40, steps=3)
            page.mouse.up(button="right")
            assert transform() == [1, 0, 0]
            page.mouse.move(x, y)
            page.mouse.wheel(0, -400)
            page.wait_for_function("Number(document.querySelector('.architecture-diagram').dataset.scale) > 1")
            zoomed = transform()
            page.mouse.down(button="right")
            page.mouse.move(x - 20, y - 20, steps=3)
            page.mouse.up(button="right")
            page.wait_for_timeout(100)
            assert transform()[0] == zoomed[0] and transform()[1:] != zoomed[1:]
            page.mouse.move(x, y)
            page.mouse.wheel(0, 100)
            page.wait_for_timeout(100)
            assert transform()[0] < zoomed[0]
            page.get_by_role("button", name="원래 크기로 보기", exact=True).click()
            assert transform() == [1, 0, 0]
            assert diagram.get_by_role("button").count(), diagram.locator("g.node").evaluate_all("nodes => nodes.map(n => ({id:n.id, text:n.textContent, role:n.getAttribute('role')}))")
            diagram.get_by_role("button", name="화면", exact=False).click()
            child = page.get_by_role("group", name="overall-architecture/web 구조 도표")
            child.locator("svg").wait_for()
            page.get_by_text("화면 내부", exact=True).wait_for()
            assert "요청 처리" in child.inner_text()
            assert child.get_attribute("data-scale") == "1"
            child.get_by_role("button", name="요청 분배", exact=False).focus()
            page.keyboard.press("Enter")
            page.get_by_text("말단 설명", exact=True).wait_for()
            assert page.locator(".architecture-diagram").count() == 0
            page.get_by_role("navigation", name="구조 경로").get_by_role("button", name="전체 구조", exact=True).click()
            diagram.locator("svg").wait_for()
            architecture_revision = 2
            page.get_by_text("Synthetic source revision 2", exact=True).wait_for(timeout=12000)
            page.get_by_text("Mermaid 원본", exact=True).click()
            assert 'graph LR' in page.get_by_role("region", name="앱 구조", exact=True).inner_text()
            for node in architecture_nodes:
                if not node["diagram"]:
                    continue
                page.get_by_label("구조 보기", exact=True).select_option("actual/" + node["path"])
                try:
                    page.get_by_role("group", name="actual/" + node["path"] + " 구조 도표").locator("svg").wait_for(timeout=10000)
                except Exception:
                    print(page.get_by_role("region", name="앱 구조", exact=True).inner_text())
                    raise
                last_diagram = node["path"]
            page.get_by_label("구조 보기", exact=True).select_option("actual/overall-architecture")
            last_diagram = "overall-architecture"
            page.get_by_role("group", name="actual/overall-architecture 구조 도표").locator("svg").wait_for()
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
                    page.get_by_role("group", name="actual/" + last_diagram + " 구조 도표").wait_for()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "artifacts" / f"architecture-{width}-fixture.png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("tab", name="대화", exact=True).click()
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
            assert page.get_by_label("선택한 작업 모델", exact=True).is_visible()
            assert page.get_by_label("선택한 작업 모델", exact=True).inner_text() == "opus"
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
            desktop.get_by_text("40 percent", exact=False).wait_for()
            compact_done.set()
            desktop.get_by_text("문맥 압축 완료", exact=False).wait_for()
            desktop.get_by_text("completed · 10/10", exact=False).wait_for()
            desktop.get_by_text("RAW_BACKGROUND_OUTPUT", exact=False).wait_for()
            assert not any("RAW_BACKGROUND_OUTPUT" in text for text in translations)
            desktop.get_by_label("에이전트 세션", exact=True).locator('p[role="status"]').filter(has_text="실행 중").wait_for(state="detached")
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
            # A merged task remains actionable while cleanup waits, then disappears
            # even though its shared checkout still exists on the server.
            deleted = False
            spec.update(state="머지됨", cleanup_complete=False)
            page.reload()
            row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
            row.wait_for()
            assert "정리 대기" in row.inner_text()
            spec["cleanup_complete"] = True
            page.reload()
            page.get_by_text("아직 작업이 없다.", exact=False).wait_for()
            assert not page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").count()
            browser.close()
            print("PASS: Background progress/completion and idle UI; Suite polling and error recovery; file diff toggles; FAST forwarding; explicit OMM add and saved-document polling; all architecture diagrams; 400px/1440px containment; task workflow regressions")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


if __name__ == "__main__":
    main()
