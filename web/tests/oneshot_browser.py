"""The oneshot window: the switch from maintenance with its count, the run list grouped by what needs
the owner, the stage spine, a gate sheet's per-item decisions with its folded section, the re-shown sheet, the
feature board by drag and by keys, a stopped run's `[재개]`, and the start form.

Run after building: python web/tests/oneshot_browser.py
Serves web/dist with synthetic runs; the sheets are short Markdown like the server's, never a real run.
"""

import copy
import re
import sys

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
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
    posted = []
    started: dict = {}
    fake = FastAPI()

    @fake.get("/shot.png")
    def shot():
        return Response(PNG, media_type="image/png")

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "oneshot-specs":
            return {"specs": [{k: v[k] for k in ("repo", "stage", "working", "stopped", "waits")} for v in views.values()]}
        if path == "oneshot" and request.method == "POST":
            body = await request.json()
            posted.append(("", "start", body))
            started.update(id="0123456789abcdef", state="intake", working="intake", stopped=None, picked="",
                           sentence=body["sentence"], models=body["models"], intent=None, profile=None, proposed={},
                           identity=None, steps={}, spec=None, errors=[], warnings=[], schema={}, steps_order=[])
            return started
        if path == "oneshot":
            return {"runs": []}
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
            if request.method == "POST":
                posted.append((repo, act, await request.json()))
                if act == "say" and posted[-1][2]["text"] == REFUSED:
                    return JSONResponse({"detail": "synthetic-reply-rejected"}, status_code=400)
                if act == "r1":   # the edit of R2 re-shows the sheet, T1 changed with it
                    again = copy.deepcopy(views[repo])
                    again["oneshot"]["r1"] = {"round": 2, "decisions": {i: {"approve": True} for i in ITEMS if i != "R2"},
                                              "marks": {"R2": "edited", "T1": "changed"}}
                    views[repo] = again
                if act == "resume":
                    views[repo] = {**views[repo], "stopped": None, "working": "critique"}
            return views[repo]
        if path == "mobile/status":
            return {"local": True}
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
        shown.get_by_label("R2 고칠 내용").fill("합계는 달마다 따로 보여라")
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

        # A stopped run: its reason and `[재개]`.
        page.get_by_role("navigation", name="원샷 실행").get_by_role("button").filter(has_text="stopped-demo").click()
        expect(page.get_by_role("alert").filter(has_text="화면 비평이 멈췄다")).to_contain_text("6회차에도")
        lead = page.locator(".oneshot-shell main h2").first
        expect(lead).to_have_text("화면 비평 멈춤")
        page.get_by_role("button", name="재개", exact=True).click()
        expect(lead.get_by_role("status")).to_have_text("진행 중")
        expect(lead).to_contain_text("화면 비평")
        assert posted[-1][:2] == ("stopped-demo", "resume"), posted[-1]

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

        # Back to maintenance and in again: the run last shown opens again.
        page.get_by_role("tab", name="유지보수").click()
        expect(page.locator(".oneshot-shell")).to_be_hidden()
        page.get_by_role("button", name="프로젝트 · 연결", exact=True).click()
        page.get_by_role("tab", name="원샷").click()
        expect(page.get_by_role("region", name="요구 대화")).to_be_visible()
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
        form.get_by_role("button", name="시작", exact=True).click()
        models = page.locator(".oneshot-shell main > header dd")
        expect(models).to_have_text(["opus · high", "Claude 기본", "codex:gpt-6.1-sol · high", "opus · high (고정)"])
        expect(page.get_by_role("list", name="원샷 단계 1/18").locator("[aria-current=step]")).to_contain_text("하고 싶은 것")
        assert page.url.endswith("#oneshot/run%3A0123456789abcdef"), page.url
        assert posted[-1][2]["models"] == {"spec": {"model": "opus", "effort": "high"}, "build": {"model": "", "effort": ""},
                                           "review": {"model": "codex:gpt-6.1-sol", "effort": "high"}}, posted[-1]
        browser.close()
    assert not errors, errors
    print("PASS: maintenance counts the runs that need the owner on the switch; the oneshot window groups them apart "
          "from the running; a gate sheet needs every item, folded or not, and records the folds opened; an enlarged "
          "picture closes alone; the re-shown sheet opens what needs a decision again; the board moves by drag and by "
          "keys; a stopped run resumes; S2 shows its choices and keeps a refused answer; the list follows a saved spec; "
          "the switch keeps the run shown; [새 프로젝트] opens the start form, which fixes three models picked apart")


if __name__ == "__main__":
    main()
