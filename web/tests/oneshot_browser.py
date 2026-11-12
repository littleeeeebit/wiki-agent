"""The oneshot window: the switch from maintenance with its count, the run list grouped by what needs
the owner, the stage spine, a gate sheet's per-item decisions with its folded section, the re-shown sheet, the
feature board by drag and by keys, a stopped run's `[재개]`, and the start form.

Run after building: python web/tests/oneshot_browser.py
Serves web/dist with synthetic runs; the sheets are short Markdown like the server's, never a real run.
"""

import asyncio
import copy
import re
import sys
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from browser_fixture import page_of, served
from mobile_browser import ROOT, fixture

FOLD = "흐름, 요청·응답, 용어집"
SHEET = f"""# 백엔드 설계 — demo

## 시나리오

- R1: 지출을 적으면 이번 달 합계에 더해진다
- R2: 지난 달을 고르면 그 달 목록이 보인다

![R1 화면](/shot.png)

## 엔드포인트

| id | 요청 |
| --- | --- |
| E1 | `POST /spendings` |

<details><summary>{FOLD}</summary>

- E2: `GET /months/{{month}}` 의 응답
- T1: `test_a_month_lists_its_spendings`

</details>
"""
ITEMS = ["R1", "R2", "E1", "E2", "T1"]
FEATURES = [{"id": f, "name": n, "class": "every", "why": "자주 쓴다", "endpoints": []}
            for f, n in (("record", "지출 적기"), ("export", "내보내기"), ("month", "달 고르기"))]
# The S2 question rides only in `choices`: the server cuts the block from the text.
ASKED = "한 달 예산을 어떻게 정하나?"
TALK = [{"role": "agent", "text": "아래에서 골라라.",
         "choices": {"question": ASKED, "options": [{"label": "달마다 새로 정한다"}, {"label": "한 번 정하면 그대로 간다"}]}}]
REFUSED = "거절될 답"
TREE = "C:/fixture/stopped-demo-worktrees/oneshot-initial"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082")


def run(repo: str, stage: str, **more) -> dict:
    view = {"repo": repo, "stage": stage, "working": None, "stopped": None, "waits": stage in ("r1", "board"),
            "requirements": [], "oneshot": {"sentence": "가계부를 만들고 싶다", "stage": stage}}
    if stage == "r1":
        view |= {"sheet": SHEET, "items": ITEMS}
        view["oneshot"]["r1"] = {"round": 1, "decisions": {}, "marks": {}}
    if stage == "board":
        view["board"] = {"features": FEATURES, "first_screen": "오늘 쓴 돈", "frequency": {f["id"]: "every" for f in FEATURES}}
    for key, value in more.items():
        view[key] = value
    return view


def posting(page):
    """Waits for the click's request to answer, so `posted` holds it."""
    return page.expect_response(lambda r: r.request.method == "POST" and "/api/oneshot/spec/" in r.url)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    views = {"demo": run("demo", "r1"), "board-demo": run("board-demo", "board"),
             "stopped-demo": run("stopped-demo", "critique", waits=False,
                                 stopped={"reason": "critique", "detail": "6회차에도 높은 발견이 남았다"}),
             "talk-demo": run("talk-demo", "spec", waits=True),
             "auto-demo": run("auto-demo", "build", working="build", waits=False)}
    views["talk-demo"]["oneshot"]["talk"] = TALK
    # The stopped run's record and worktree: what the screen tracks while a stage runs on its own.
    views["stopped-demo"] |= {"worktree": TREE, "limits": {"attempts": 3, "refusals": 5, "critiques": 6}}
    views["stopped-demo"]["oneshot"] |= {
        "spend": {"usd": 12.5, "seconds": 3725, "calls": 9},
        "critiques": [{"look": 5, "head": "a" * 40, "clean": True, "findings": []},
                      {"look": 6, "head": "b" * 40, "clean": False, "findings": [
                          {"route": "/", "viewport": "phone", "state": "empty", "what": "버튼 대비가 낮다", "severity": "high"}]}]}
    tree_turns = [{"role": "user", "text": "Build the screens from the board."},
                  {"role": "assistant", "text": "Built the screens.", "steps": [{"kind": "tool", "text": "npm run build"}]}]
    posted = []
    started: dict = {}
    # A start answer held back, S1 list reads that fail, and spec list answers that are late or carry no list.
    slow = {"start": 0.0, "fail": 0, "bad": False, "hold": False}
    fake = FastAPI()

    @fake.get("/shot.png")
    def shot():
        return Response(PNG, media_type="image/png")

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "oneshot-specs":   # one broken item drops alone; the rest still shows
            if slow["bad"]:   # successful answers that carry no list
                return {"specs": None}
            return {"specs": [None, *({k: v[k] for k in ("repo", "stage", "working", "stopped", "waits")} for v in views.values())]}
        if path == "oneshot" and request.method == "POST":
            await asyncio.sleep(slow["start"])
            body = await request.json()
            posted.append(("", "start", body))
            started.update(id="0123456789abcdef", state="intake", working="intake", stopped=None, picked="",
                           sentence=body["sentence"], models=body["models"], intent=None, profile=None, proposed={},
                           identity=None, steps={}, spec=None, errors=[], warnings=[], schema={}, steps_order=[])
            return started
        if path == "oneshot":
            if slow["fail"]:   # a read that fails once, as a dropped connection would
                slow["fail"] -= 1
                return JSONResponse({"detail": "synthetic-list-failure"}, status_code=500)
            return {"runs": [None, *([started] if started else [])]}
        if path == f"oneshot/{started.get('id')}":
            return started
        if path == "options":
            return {**fixture(path, request.method), "efforts": [{"id": "", "label": "기본", "note": ""},
                                                                  {"id": "high", "label": "high", "note": ""}],
                    "models": [{"id": "", "label": "Claude 기본", "note": ""}, {"id": "opus", "label": "Opus", "note": ""},
                               {"id": "codex:gpt-6.1-sol", "label": "GPT-6.1-Sol", "note": "Codex", "is_default": True,
                                "efforts": [{"id": "high", "label": "high", "note": ""}]}]}
        if path == "loop/settings":
            return {**fixture(path, request.method), "review_model": "codex:gpt-6.1-sol", "review_effort": "high"}
        if path.startswith("oneshot/spec/"):
            repo, _, act = path.removeprefix("oneshot/spec/").partition("/")
            if repo not in views:   # deleted, as the server answers it
                return JSONResponse({"detail": "원샷 명세가 없다"}, status_code=404)
            if request.method == "POST":
                posted.append((repo, act, await request.json()))
                if act == "say" and posted[-1][2]["text"] == REFUSED:
                    return JSONResponse({"detail": "synthetic-reply-rejected"}, status_code=400)
                if act == "r1":   # the edit of R2 re-shows the sheet, T1 changed with it
                    again = copy.deepcopy(views[repo])
                    again["oneshot"]["r1"] = {"round": 2, "decisions": {i: {"approve": True} for i in ITEMS if i != "R2"},
                                              "marks": {"R2": "edited", "T1": "changed"}}
                    views[repo] = again
                if act == "resume":   # the critic's model call, sent 75 seconds ago
                    views[repo] = {**views[repo], "stopped": None, "working": "critique", "oneshot": {
                        **views[repo]["oneshot"], "inflight": {"role": "critic", "codex": False, "sent": time.time() - 75}}}
            return views[repo]
        if path == "mobile/status":
            return {"local": True}
        if path == "work/log" and request.query_params.get("path") == TREE:
            return {"rows": tree_turns, "session_id": "tree", "busy": False, "running": None, "rules": [], "queued": None}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    errors = []
    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        page = page_of(browser, errors, viewport={"width": 1280, "height": 960})
        page.goto(f"http://127.0.0.1:{port}/")
        # Maintenance says a oneshot run waits, on the switch to the oneshot window.
        switch = page.get_by_role("tablist", name="창")
        expect(switch.get_by_role("tab", name="원샷").get_by_label("결정이 필요한 실행 4개")).to_be_visible()
        switch.get_by_role("tab", name="원샷").click()
        expect(page.get_by_role("tab", name="원샷")).to_have_attribute("aria-selected", "true")
        rail = page.get_by_role("navigation", name="원샷 실행")
        need = rail.get_by_role("region", name="결정이 필요한 것")
        expect(need.get_by_role("button").filter(has_text=re.compile(r"^demo"))).to_be_visible()
        expect(need.get_by_role("button").filter(has_text="stopped-demo")).to_contain_text("멈춤 · 화면 비평")
        expect(rail.get_by_role("region", name="도는 중").get_by_role("button").filter(has_text="auto-demo")).to_be_visible()

        # The run opened by default stays when the list reorders under it, with what was typed there.
        expect(page.locator(".oneshot-shell main > header h1")).to_have_text("demo")
        page.get_by_role("region", name="R1 시트").locator("[data-item=R2]").get_by_role("button", name="고침").click()
        page.get_by_label("R2 고칠 내용").fill("합계는 달마다 따로 보여라")
        views["demo"]["waits"] = False   # board-demo is now the first that waits
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")
        expect(need.get_by_role("button").filter(has_text=re.compile(r"^demo"))).to_have_count(0)
        expect(page.locator(".oneshot-shell main > header h1")).to_have_text("demo")
        expect(page.get_by_label("R2 고칠 내용")).to_have_value("합계는 달마다 따로 보여라")
        views["demo"]["waits"] = True
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")
        rail.get_by_role("button").filter(has_text="board-demo").click()   # the sheet below starts fresh

        # R1: the stages, per-item decisions, the folded section and its "approve all".
        need.get_by_role("button").filter(has_text=re.compile(r"^demo")).click()
        strip = page.get_by_role("list", name="원샷 단계 6/18")
        expect(strip.locator("[aria-current=step]")).to_contain_text("R1 백엔드 설계")
        # the start's three, the conversation, the backend design
        expect(strip.locator("li li").filter(has_text="— 지남")).to_have_count(5)
        sheet = page.get_by_role("region", name="R1 시트")
        shown, folded = sheet.get_by_role("region", name="펼쳐진 항목"), sheet.get_by_role("region", name="접힌 부분의 항목")
        expect(shown.locator("[data-item]")).to_have_count(3)
        expect(folded.locator("[data-item]")).to_have_count(2)
        submit = sheet.get_by_role("button", name="제출", exact=True)
        for item in ("R1", "E1"):
            shown.locator(f"[data-item={item}]").get_by_role("button", name="승인").click()
        shown.locator("[data-item=R2]").get_by_role("button", name="고침").click()
        edit = shown.get_by_label("R2 고칠 내용")
        edit.press_sequentially("합계는")   # typed key by key, the item stays open where it was, focus and all
        expect(edit).to_be_visible()
        expect(edit).to_be_focused()
        edit.fill("합계는 달마다 따로 보여라")
        for item in ("R1", "E1", "R2"):   # an item decided on this sheet stays in sight
            expect(shown.locator(f"[data-item={item}]")).to_be_visible()
        expect(sheet.get_by_text("정하지 않은 항목 2개")).to_be_visible()
        expect(submit).to_be_disabled()   # the folded section's items are undecided, though nobody opened it

        # The enlarged picture: its own dialog; Escape closes only it, the sheet and its decisions stay.
        thumb = sheet.get_by_role("button", name="R1 화면")
        thumb.click()
        zoom = page.get_by_role("dialog", name="화면 사진")
        expect(zoom.get_by_role("button", name="닫기")).to_be_focused()
        page.keyboard.press("Tab")
        page.keyboard.press("Escape")
        expect(zoom).to_have_count(0)
        expect(sheet.get_by_text("정하지 않은 항목 2개")).to_be_visible()
        expect(shown.get_by_label("R2 고칠 내용")).to_have_value("합계는 달마다 따로 보여라")
        expect(thumb).to_be_focused()
        sheet.get_by_text(FOLD, exact=True).click()
        folded.get_by_role("button", name="모두 승인 (2)").click()
        expect(sheet.get_by_text("모두 정했다")).to_be_visible()
        submit.click()
        expect(sheet.get_by_text("정하지 않은 항목 1개")).to_be_visible()   # the answer, re-shown, has landed
        repo, act, body = posted[-1]
        assert (repo, act) == ("demo", "r1"), posted
        assert body["decisions"] == {"R1": {"approve": True}, "R2": {"edit": "합계는 달마다 따로 보여라"}, "E1": {"approve": True},
                                     "E2": {"approve": True}, "T1": {"approve": True}}, body
        assert body["reading"]["opened"] == [FOLD] and body["reading"]["seconds"] > 0, body

        # The re-shown sheet: what needs a decision again is open, what stays approved is collapsed.
        sheet = page.get_by_role("region", name="R1 시트")
        expect(sheet.locator("[data-item=R2]")).to_be_visible()
        expect(sheet.locator("[data-item=T1]").first).to_be_visible()
        expect(sheet.locator("[data-item=R1]")).to_be_hidden()
        expect(sheet.get_by_text("정하지 않은 항목 1개")).to_be_visible()

        # The board: one card dragged, one moved with the arrow keys, the whole map sent once.
        page.get_by_role("navigation", name="원샷 실행").get_by_role("button").filter(has_text="board-demo").click()
        board = page.get_by_role("region", name="기능 보드")
        board.get_by_role("button", name="지출 적기 — 늘 쓴다").drag_to(board.get_by_role("group", name="가끔 쓴다"))
        expect(board.get_by_role("button", name="지출 적기 — 가끔 쓴다")).to_be_visible()
        card = board.get_by_role("button", name="내보내기 — 늘 쓴다")
        card.focus()
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        expect(board.get_by_role("button", name="내보내기 — 드물게 쓴다")).to_be_focused()
        with posting(page):
            board.get_by_role("button", name="보드 제출").click()
        assert posted[-1] == ("board-demo", "board", {"frequency": {"record": "sometimes", "export": "rarely", "month": "every"},
                                                       "first_screen": "오늘 쓴 돈"}), posted[-1]

        # A stopped run: its reason and `[재개]` over what happened — the record beside, the worktree's turns below.
        page.get_by_role("navigation", name="원샷 실행").get_by_role("button").filter(has_text="stopped-demo").click()
        expect(page.get_by_role("alert").filter(has_text="화면 비평이 멈췄다")).to_contain_text("6회차에도")
        lead = page.locator(".oneshot-shell main h2").first
        expect(lead).to_have_text("화면 비평 멈춤")
        record = page.get_by_role("complementary", name="기록")
        expect(record).to_contain_text("$12.50 · 1시간 2분 · 호출 9번")
        looks = record.get_by_role("region", name="화면 비평")
        expect(looks.locator("details").first).to_contain_text("2회차 높은 발견 있음 · 촬영 6")
        expect(looks.get_by_text("버튼 대비가 낮다")).to_be_visible()   # the latest open, the earlier folded
        expect(looks.get_by_text("1회차")).to_be_visible()
        trail = page.get_by_role("region", name="작업 기록")
        expect(trail.get_by_text("npm run build")).to_be_visible()
        expect(trail.get_by_text("서버가 보낸 지시")).to_be_visible()
        page.get_by_role("button", name="재개", exact=True).click()
        expect(lead.get_by_role("status")).to_contain_text("진행 중")
        expect(lead).to_contain_text("화면 비평")
        assert posted[-1][:2] == ("stopped-demo", "resume"), posted[-1]
        # Running: what runs and for how long, and a turn the server starts in the worktree shows up as it comes.
        now = page.get_by_role("region", name="지금")
        expect(now).to_contain_text("화면 비평 모델 호출")
        expect(now.get_by_role("timer")).to_contain_text("1분")
        tree_turns.append({"role": "assistant", "text": "Fixed the button contrast.", "steps": []})
        page.evaluate(f"window.dispatchEvent(new CustomEvent('work-turn', {{ detail: {{ path: {TREE!r} }} }}))")
        expect(trail.get_by_text("Fixed the button contrast.")).to_be_visible()

        # S2: the question that rides only in `choices` is shown and answered; a refused answer stays.
        page.get_by_role("navigation", name="원샷 실행").get_by_role("button").filter(has_text="talk-demo").click()
        talk = page.get_by_role("region", name="요구 대화")
        expect(talk.get_by_text(ASKED)).to_be_visible()
        reply = talk.get_by_label("답", exact=True)
        reply.fill(REFUSED)
        talk.get_by_role("button", name="보내기").click()
        expect(page.get_by_role("alert").filter(has_text="synthetic-reply-rejected")).to_be_visible()
        expect(reply).to_have_value(REFUSED)
        talk.get_by_label("달마다 새로 정한다").check()
        with posting(page):
            talk.get_by_role("button", name="답하고 이어 가기").click()
        expect(page.get_by_role("alert")).to_have_count(0)
        assert posted[-1] == ("talk-demo", "say", {"text": "달마다 새로 정한다"}), posted[-1]
        expect(reply).to_have_value(REFUSED)   # the choice was sent, not the box
        reply.fill("좋다")
        talk.get_by_role("button", name="보내기").click()
        expect(reply).to_have_value("")
        assert posted[-1] == ("talk-demo", "say", {"text": "좋다"}), posted[-1]

        # The rail follows a spec the server saves, with no focus change: an automatic stage reaching a gate.
        row = (page.get_by_role("navigation", name="원샷 실행").get_by_role("region", name="결정이 필요한 것")
               .get_by_role("button").filter(has_text="auto-demo"))
        expect(row).to_have_count(0)
        views["auto-demo"] = run("auto-demo", "r1")
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")
        expect(row).to_be_visible()
        views["auto-demo"] = run("auto-demo", "build", working="build", waits=False)   # and a feed resync alone
        page.evaluate("window.dispatchEvent(new Event('server-resync'))")
        expect(row).to_have_count(0)

        # Back to maintenance and in again: the run last shown opens again, with the half-typed answer kept.
        reply.fill("아직 보내지 않은 답")
        page.get_by_role("tab", name="유지보수").click()
        expect(page.locator(".oneshot-shell")).to_be_hidden()
        page.get_by_role("button", name="프로젝트 · 연결", exact=True).click()
        page.get_by_role("tab", name="원샷").click()
        expect(page.get_by_role("region", name="요구 대화")).to_be_visible()
        expect(reply).to_have_value("아직 보내지 않은 답")

        # A list answer that is not the latest, or that carries no list, deletes nothing: the run and draft stay.
        title = page.locator(".oneshot-shell main > header h1")
        # Every list hook reads on each event, so the older reads are all held in the browser and answered late,
        # with a list from before talk-demo existed.
        late: list = []
        page.route("**/api/oneshot-specs", lambda route: late.append(route) if slow["hold"] else route.continue_())
        slow["hold"] = True
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")   # the older reads
        page.wait_for_timeout(300)
        slow["hold"] = False
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")   # the newer reads, answered first
        page.wait_for_timeout(500)
        assert late, "no list read was held"
        older = [{k: v[k] for k in ("repo", "stage", "working", "stopped", "waits")}
                 for name, v in views.items() if name != "talk-demo"]
        for route in late:
            route.fulfill(json={"specs": older})
        page.wait_for_timeout(500)
        page.unroute("**/api/oneshot-specs")
        expect(title).to_have_text("talk-demo")
        expect(reply).to_have_value("아직 보내지 않은 답")
        slow["bad"] = True
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")
        page.wait_for_timeout(500)
        slow["bad"] = False
        expect(title).to_have_text("talk-demo")
        expect(reply).to_have_value("아직 보내지 않은 답")
        page.get_by_role("tab", name="유지보수").click()

        # Maintenance's [새 프로젝트] opens the start form in the oneshot window.
        page.get_by_role("button", name="새 프로젝트", exact=True).click()
        form = page.locator(".oneshot-shell main")
        expect(form.get_by_role("heading", name="새 실행")).to_be_visible()
        group = {role: form.get_by_role("group", name=f"{role} 모델") for role in ("명세", "구현", "리뷰")}
        expect(group["리뷰"].get_by_role("combobox", name="모델")).to_contain_text("GPT-6.1-Sol")
        expect(group["명세"].get_by_role("combobox", name="모델")).to_contain_text("Claude 기본")
        # A person's press: the list opens on the press and lies over the trigger before the release,
        # so the click lands on what holds both — never read as a click on the backdrop.
        box = group["명세"].get_by_role("combobox", name="모델").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        expect(page.get_by_role("option", name="Opus")).to_be_visible()
        page.mouse.up()
        expect(form).to_be_visible()
        page.get_by_role("option", name="Opus").click()
        expect(form).to_be_visible()
        group["명세"].get_by_role("combobox", name="추론 강도").click()
        page.get_by_role("option", name="high").click()
        expect(group["구현"].get_by_role("combobox", name="모델")).to_contain_text("Claude 기본")   # apart from 명세
        form.get_by_role("textbox").first.fill("가계부를 만들고 싶다")
        # A start answer that lands after another run was picked only joins the list; the picked run stays.
        slow["start"] = 1.5
        form.get_by_role("button", name="시작", exact=True).click()
        rail = page.get_by_role("navigation", name="원샷 실행")
        rail.get_by_role("button").filter(has_text="talk-demo").click()
        new_row = rail.get_by_role("region", name="도는 중").get_by_role("button").filter(has_text="새 실행")
        expect(new_row).to_be_visible(timeout=5000)
        expect(page.get_by_role("region", name="요구 대화")).to_be_visible()
        assert page.url.endswith("#oneshot/spec%3Atalk-demo"), page.url
        slow["start"] = 0.0
        new_row.click()
        models = page.locator(".oneshot-shell main > header dd")
        expect(models).to_have_text(["opus · high", "Claude 기본", "codex:gpt-6.1-sol · high", "opus · high (고정)"])
        expect(page.get_by_role("list", name="원샷 단계 1/18").locator("[aria-current=step]")).to_contain_text("하고 싶은 것")
        assert page.url.endswith("#oneshot/run%3A0123456789abcdef"), page.url
        assert posted[-1][2]["models"] == {"spec": {"model": "opus", "effort": "high"}, "build": {"model": "", "effort": ""},
                                           "review": {"model": "codex:gpt-6.1-sol", "effort": "high"}}, posted[-1]

        # The S1 run's row and the count follow it while another run is open, through a failed list read:
        # running, then waiting for [맞다].
        expect(new_row).to_be_visible()
        expect(page.get_by_role("tab", name="원샷").get_by_label("결정이 필요한 실행 3개")).to_be_visible()   # stopped-demo resumed
        rail.get_by_role("button").filter(has_text="talk-demo").click()
        slow["fail"] = 1
        page.wait_for_timeout(3500)   # one 3s re-read fails
        assert slow["fail"] == 0, slow
        expect(new_row).to_be_visible()
        started.update(working=None, intent={"restated": "가계부", "kind": "webapp", "name": "budget", "users": "나",
                                             "first_screen": "이번 달 합계"})
        waiting = rail.get_by_role("region", name="결정이 필요한 것").get_by_role("button").filter(has_text="budget")
        expect(waiting).to_contain_text("하고 싶은 것", timeout=8000)
        expect(page.get_by_role("tab", name="원샷").get_by_label("결정이 필요한 실행 4개")).to_be_visible()
        waiting.click()
        expect(page.locator(".oneshot-shell main h2").first).to_contain_text("결정 필요")

        # A bootstrap that finishes while maintenance shows stays out of sight, and opens on the way back.
        started.update(state="bootstrap", working="bootstrap", intent=None)
        rail.get_by_role("button").filter(has_text="talk-demo").click()
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        rail.get_by_role("region", name="도는 중").get_by_role("button").filter(has_text="새 실행").click()
        expect(page.locator(".oneshot-shell main h2").first).to_contain_text("저장소 만들기")
        page.get_by_role("tab", name="유지보수").click()
        started.update(state="spec", working=None, spec={"repo": "talk-demo", "id": "s"})
        page.wait_for_timeout(2500)   # the hidden pane's 1.5s read lands
        expect(page.locator(".oneshot-shell")).to_be_hidden()
        assert not page.url.split("#", 1)[-1].startswith("oneshot"), page.url
        page.get_by_role("tab", name="원샷").click()
        expect(page.get_by_role("region", name="요구 대화")).to_be_visible()

        # A deleted run is neither kept nor reopened: picked by hand, the window falls back to the default run;
        # opened by default, it moves on to the next. A failed read is not a deletion.
        title = page.locator(".oneshot-shell main > header h1")
        rail.get_by_role("button").filter(has_text="board-demo").click()
        expect(title).to_have_text("board-demo")
        board_view = views.pop("board-demo")
        page.evaluate("window.dispatchEvent(new Event('server-resync'))")   # what a deletion's `sync` sends
        expect(title).to_have_text("demo")
        expect(page).to_have_url(re.compile(r"#oneshot$"))
        views["board-demo"] = board_view
        demo_view = views.pop("demo")
        page.evaluate("window.dispatchEvent(new Event('spec-changed'))")
        expect(title).not_to_have_text("demo")
        views["demo"] = demo_view

        # Under 1100px the list is a drawer that holds the keyboard: Tab goes round inside it, Escape gives the
        # focus back to its toggle, and a run picked there takes the focus to the run's title.
        page.set_viewport_size({"width": 1000, "height": 800})
        toggle = page.get_by_role("button", name="실행 목록")
        toggle.click()
        inside = "() => !!document.activeElement?.closest('#oneshot-runs')"
        assert page.evaluate(inside)
        for key in ["Shift+Tab"] * 3 + ["Tab"] * 25:
            page.keyboard.press(key)
            assert page.evaluate(inside), key
        page.keyboard.press("Escape")
        expect(toggle).to_be_focused()
        toggle.click()
        page.locator("#oneshot-runs [aria-current=page]").focus()
        page.keyboard.press("Enter")
        expect(page.locator(".oneshot-shell main > header h1")).to_be_focused()
        browser.close()
    assert not errors, errors
    print("PASS: maintenance counts the runs that need the owner on the switch; the oneshot window groups them apart "
          "from the running; a gate sheet needs every item, folded or not, and records the folds opened; an enlarged "
          "picture closes alone; the re-shown sheet opens what needs a decision again; the board moves by drag and by "
          "keys; a stopped run resumes, and a stopped or running stage shows its record and its worktree's turns as they "
          "come; S2 shows its choices and keeps a refused answer; the list follows a saved spec; "
          "the switch keeps the run shown and its draft; [새 프로젝트] opens the start form, which fixes three models "
          "picked apart; a late start answer leaves the picked run; an S1 run's row and the count follow it through "
          "a failed read; a broken list item drops alone; the run opened by default stays when the list reorders; "
          "a deleted run is neither kept nor reopened, while a late or listless answer deletes nothing; the drawer holds the keyboard")


def tracking():
    """A running stage's turn streams while its run is shown and its stream closes when another run is, however
    often the owner goes back and forth; a stopped check counts the failed checks; critiques count by sequence,
    their shots apart, against the cap since it restarted; a failed record read says so; partial translation
    leaves progress in English, and the switch set elsewhere is followed at once."""
    trees = {name: f"C:/fixture/{name}-worktrees/oneshot-initial" for name in ("a-run", "b-run", "c-run", "d-run", "e-run", "f-run")}
    limits = {"attempts": 3, "refusals": 5, "critiques": 6}
    look = lambda n, clean: {"look": n, "head": "c" * 40, "clean": clean, "findings": [] if clean else [
        {"route": "/", "viewport": "phone", "state": "empty", "what": "글자가 잘린다", "severity": "high"}]}
    views = {
        "a-run": run("a-run", "build", working="build", waits=False),
        "b-run": run("b-run", "build", waits=False, stopped={"reason": "build", "detail": "검사가 세 번 실패했다"}),
        "c-run": run("c-run", "critique", working="critique", waits=False),
        # After an R3 fix, the first critique call failed: the same reason as a cap stop, and no critique.
        "d-run": run("d-run", "critique", waits=False, stopped={"reason": "critique", "detail": "비평이 그림을 받지 못했다"}),
        "e-run": run("e-run", "critique", waits=False, stopped={"reason": "critique", "detail": "6회차에도 높은 발견이 남았다",
                                                               "counted": 6}),
        # The same cap stop as a server before `counted` saved it: the count only in its detail.
        "f-run": run("f-run", "critique", waits=False, stopped={"reason": "critique",
                                                               "detail": "6회차에도 높은 발견이 남았다 — / phone empty: 글자가 잘린다"}),
    }
    for name, view in views.items():
        view |= {"worktree": trees[name], "limits": limits}
    views["a-run"]["oneshot"] |= {"attempts": 1, "inflight": {"role": "build", "codex": False, "sent": time.time(), "turn": "t1"}}
    views["b-run"]["oneshot"] |= {"attempts": 3}
    # Two clean looks at shot 1 (a second look at the same shots), then an R3 fix restarted the count.
    views["c-run"]["oneshot"] |= {"critiques": [look(1, True), look(1, True), look(2, False)], "critique_base": 2}
    views["d-run"]["oneshot"] |= {"critiques": [look(1, True), look(1, True)], "critique_base": 2}
    for name in ("e-run", "f-run"):
        views[name]["oneshot"] |= {"critiques": [look(n, False) for n in range(1, 7)], "critique_base": 6}
    state = {"open": 0, "opened": 0, "fail": 1, "mode": "partial"}
    translations: list = []
    fake = FastAPI()

    @fake.get("/api/work/events")
    async def events(path: str, turn: str):
        async def stream():
            state["open"] += 1
            state["opened"] += 1
            try:
                yield ('data: {"kind": "delta", "text": "Working on the build.", "turn": "t1", "session_id": "s", '
                       '"seq": 0}\n\n')
                while True:   # a turn awaiting an answer: the stream stays open until the browser lets go
                    await asyncio.sleep(0.1)
            finally:
                state["open"] -= 1
        assert path == trees["a-run"] and turn == "t1", (path, turn)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "oneshot-specs":
            return {"specs": [{k: v[k] for k in ("repo", "stage", "working", "stopped", "waits")} for v in views.values()]}
        if path.startswith("oneshot/spec/"):
            return views[path.removeprefix("oneshot/spec/").partition("/")[0]]
        if path == "switch":
            return {"translate": state["mode"] != "off", "mode": state["mode"], "usage": {"month": "2026-11", "usd": 0, "limit": 10}}
        if path == "translate":
            texts = (await request.json())["texts"]
            translations.extend(texts)
            mapping = {"Built the screens.": "화면을 만들었다.", "Reading the board.": "보드를 읽는다."}
            return {"texts": [mapping.get(t, t) for t in texts], "statuses": ["translated"] * len(texts)}
        if path == "mobile/status":
            return {"local": True}
        if path == "work/log":
            where = request.query_params.get("path")
            if where == trees["a-run"]:
                return {"rows": [], "session_id": "s", "busy": True, "rules": [], "queued": None,
                        "running": {"turn": "t1", "session_id": "s", "seq": 0}}
            if where == trees["c-run"]:
                if state["fail"]:
                    state["fail"] -= 1
                    return JSONResponse({"detail": "synthetic-log-failure"}, status_code=500)
                return {"rows": [{"role": "assistant", "text": "Built the screens.",
                                  "steps": [{"kind": "progress", "text": "Reading the board."}]}],
                        "session_id": "c", "busy": False, "running": None, "rules": [], "queued": None}
            if where in (trees["b-run"], trees["d-run"], trees["e-run"], trees["f-run"]):
                return {"rows": [], "session_id": "", "busy": False, "running": None, "rules": [], "queued": None}
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    errors = []

    def settled(want: int):
        for _ in range(40):
            if state["open"] == want:
                return
            time.sleep(0.1)
        raise AssertionError(f"{state['open']} streams open, {want} wanted ({state['opened']} opened)")

    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        page = page_of(browser, errors, viewport={"width": 1600, "height": 960})
        page.goto(f"http://127.0.0.1:{port}/#oneshot")
        rail = page.get_by_role("navigation", name="원샷 실행")
        pick = lambda name: rail.get_by_role("button").filter(has_text=name).click()
        trail, record = page.get_by_role("region", name="작업 기록"), page.get_by_role("complementary", name="기록")

        pick("a-run")
        expect(trail).to_contain_text("Working on the build.")
        expect(record).to_contain_text("시도2 / 3")
        settled(1)
        pick("b-run")
        expect(record).to_contain_text("검사 실패3 / 3")   # three checks failed: no fourth try
        settled(0)
        for name in ("a-run", "b-run", "a-run", "b-run", "a-run"):
            pick(name)
            expect(page.locator(".oneshot-shell main > header h1")).to_have_text(name)
        expect(trail).to_contain_text("Working on the build.")
        settled(1)   # the streams of the runs left are closed, however often
        assert state["opened"] == 4, state

        pick("c-run")
        settled(0)
        expect(trail.get_by_role("alert")).to_contain_text("작업 기록을 읽지 못했다")
        trail.get_by_role("button", name="다시 읽기").click()
        expect(trail.get_by_text("화면을 만들었다.")).to_be_visible()
        expect(trail.get_by_text("Reading the board.")).to_be_visible()   # partial: progress stays as it came
        assert "Reading the board." not in translations, translations
        state["mode"] = "full"   # set in maintenance's settings, which tells every record
        page.evaluate("window.dispatchEvent(new CustomEvent('switch', { detail: { translate: true, mode: 'full' } }))")
        expect(trail.get_by_text("보드를 읽는다.")).to_be_visible()
        state["mode"] = "partial"   # set in another window: this one hears only the feed's `sync`, a resync
        page.evaluate("window.dispatchEvent(new Event('server-resync'))")
        expect(trail.get_by_text("Reading the board.")).to_be_visible()

        expect(record).to_contain_text("화면 비평1 / 6 · 모두 3회")
        looks = record.get_by_role("region", name="화면 비평")
        expect(looks.locator("details summary")).to_have_text([
            re.compile(r"^3회차 높은 발견 있음 · 촬영 2"), re.compile(r"^2회차 높은 발견 없음 · 촬영 1$"),
            re.compile(r"^1회차 높은 발견 없음 · 촬영 1$")])
        pick("d-run")   # a failed call is no critique: nothing counted against the fresh cap
        expect(record).to_contain_text("화면 비평0 / 6 · 모두 2회")
        pick("e-run")   # the cap reached: the count restarted at once, the stop keeps what it reached
        expect(record).to_contain_text("화면 비평6 / 6")
        expect(record).not_to_contain_text("모두")
        pick("f-run")   # saved before `counted`: read off the stop's own words, never as none
        expect(page.locator(".oneshot-shell main > header h1")).to_have_text("f-run")
        expect(record).to_contain_text("화면 비평6 / 6")
        expect(record).not_to_contain_text("모두")
        browser.close()
    assert not errors, errors
    print("PASS: a run's stream closes when another run is shown, however often; a stopped check shows the checks "
          "that failed; critiques count by sequence with their shots apart, against the cap since it restarted; a "
          "failed record read says so and reads again; partial translation leaves progress, and the switch is followed")


if __name__ == "__main__":
    main()
    tracking()
