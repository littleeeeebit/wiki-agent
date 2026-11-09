"""The oneshot run's screens: the rail row that waits, the stage strip, a gate sheet's per-item
decisions with its folded section, the re-shown sheet, the feature board by drag and by keys, and a
stopped run's `[재개]`.

Run after building: python web/tests/oneshot_browser.py
Serves web/dist with synthetic runs; the sheets are short Markdown like the server's, never a real run.
"""

import copy
import sys

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from browser_fixture import page_of, served
from mobile_browser import ROOT, fixture

FOLD = "흐름, 요청·응답, 용어집"
SHEET = f"""# 백엔드 설계 — demo

## 시나리오

- R1: 지출을 적으면 이번 달 합계에 더해진다
- R2: 지난 달을 고르면 그 달 목록이 보인다

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


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    views = {"demo": run("demo", "r1"), "board-demo": run("board-demo", "board"),
             "stopped-demo": run("stopped-demo", "critique", waits=False,
                                 stopped={"reason": "critique", "detail": "6회차에도 높은 발견이 남았다"})}
    posted = []
    fake = FastAPI()

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "oneshot-specs":
            return {"specs": [{k: v[k] for k in ("repo", "stage", "working", "stopped", "waits")} for v in views.values()]}
        if path == "oneshot":
            return {"runs": []}
        if path.startswith("oneshot/spec/"):
            repo, _, act = path.removeprefix("oneshot/spec/").partition("/")
            if request.method == "POST":
                posted.append((repo, act, await request.json()))
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
        page.get_by_role("button", name="프로젝트 · 연결", exact=True).click()
        rail = page.get_by_role("navigation", name="원샷 실행")
        expect(rail.get_by_role("button").filter(has_text="demo").first.get_by_label("결정을 기다린다")).to_be_visible()
        expect(rail.get_by_role("button").filter(has_text="stopped-demo").get_by_label("결정을 기다린다")).to_have_count(0)

        # R1: the strip, per-item decisions, the folded section and its "approve all".
        rail.get_by_role("button").filter(has_text="demo").first.click()
        strip = page.get_by_role("navigation", name="원샷 단계")
        expect(strip.locator("[aria-current=step]")).to_have_text("◆ R1 백엔드 설계")
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
        sheet.get_by_text(FOLD, exact=True).click()
        folded.get_by_role("button", name="모두 승인 (2)").click()
        expect(sheet.get_by_text("모두 정했다")).to_be_visible()
        submit.click()
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
        page.keyboard.press("Escape")

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
        board.get_by_role("button", name="보드 제출").click()
        assert posted[-1] == ("board-demo", "board", {"frequency": {"record": "sometimes", "export": "rarely", "month": "every"},
                                                       "first_screen": "오늘 쓴 돈"}), posted[-1]
        page.keyboard.press("Escape")

        # A stopped run: its reason and `[재개]`.
        page.get_by_role("navigation", name="원샷 실행").get_by_role("button").filter(has_text="stopped-demo").click()
        expect(page.get_by_role("alert").filter(has_text="멈췄다 — 화면 비평이 멈췄다")).to_contain_text("6회차에도")
        page.get_by_role("button", name="재개", exact=True).click()
        expect(page.get_by_role("navigation", name="원샷 단계").get_by_role("status")).to_have_text("화면 비평 진행 중…")
        assert posted[-1][:2] == ("stopped-demo", "resume"), posted[-1]
        browser.close()
    assert not errors, errors
    print("PASS: the rail marks a run that waits; a gate sheet needs every item, folded or not, and records the folds "
          "opened; the re-shown sheet opens what needs a decision again; the board moves by drag and by keys; a stopped "
          "run resumes")


if __name__ == "__main__":
    main()
