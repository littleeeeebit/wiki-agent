"""Exercise the refactoring focus — mode cards, a survey, its cards, legacy runs — against synthetic API responses."""

import json
from pathlib import Path
import socket
import sys
import threading
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from mobile_browser import fixture  # noqa: E402

LEVEL = {"level": "medium", "why": "Three handlers"}
ROLES = {"survey": {"model": "opus", "effort": ""}, "worker": {"model": "sonnet", "effort": ""},
         "reviewer": {"model": "codex:gpt-review", "effort": ""}}
BASELINE = {"cmd": "pytest", "ok": False, "code": 1, "reason": "", "failures": ["tests/test_flaky.py::test_clock"],
            "parsed": True}
MAP = {"pipelines": [{"id": "orders", "name": "Orders", "entry": "src/orders.py", "paths": ["src/orders.py"],
                      "role": "Takes orders from the API"},
                     {"id": "billing", "name": "Billing", "entry": "src/billing.py", "paths": ["src/billing.py"],
                      "role": "Charges accepted orders"}],
       "shared": [], "excluded": [], "omm": {"used": False, "diverged": []}, "unchecked": []}
PIPELINE = {"id": "orders", "what": "Validates and stores an order", "inputs": "HTTP body", "outputs": "Order row",
            "owns": "Order validation", "change": "Validate once at the boundary", "links": [], "misplaced": [],
            "debts": [{"kind": "defensive", "summary": "Order validation repeated in three handlers",
                       "evidence": [{"path": "src/orders.py", "symbol": "create"}], "scope": "3 call sites",
                       "size": LEVEL, "difficulty": LEVEL, "risk": LEVEL, "if_left": "Rules drift apart"}],
            "checked": ["src/orders.py"], "unchecked": []}


def survey(body, generation):
    return {"id": "cafe0001", "kind": "survey", "repo": "fixture", "mode": body["mode"], "role": body["roles"]["survey"],
            "roles": body["roles"],
            "generation": generation, "request_id": body["request_id"], "phase": "baseline", "state": "running",
            "stopped": None, "models": [], "specs": [], "recheck": None, "created": time.time(),
            "spent": {"seconds": 3, "calls": 1, "tokens": 10}}


def card(key, rid, depends):
    detail = {"key": key, "title": key, "pipeline": "orders", "files": ["src/orders.py"], "boundary": "Order intake",
              "problem": "Validation repeated", "change": "Order validation runs once at the boundary",
              "impact": "None", "rollback": "Revert", "evidence": [{"path": "src/orders.py", "symbol": "create"}],
              "keep": ["Responses"], "depends": depends, "risk": LEVEL,
              "verify": [{"method": "pytest", "criterion": "passes"}], "tests": {"add": [], "remove": [], "contract": []}}
    return {"id": f"{key}-{rid}", "repo": "fixture", "rev": 1, "goal": f"{key}. {detail['change']}", "out": ["Responses"],
            "done": ["pytest", "pytest: passes"], "grounds": {"pages": [], "files": ["src/orders.py"], "rules": []},
            "decisions": [], "state": "정리됨", "history": [{"ts": 1}], "missing": [], "pr": None, "worktree": None,
            "gate": None, "fault": None, "report": None, "review_profile": "code",
            "source": {"focus": "refactor", "plan": None, "turn": 1, "refactor_mode": "simplify", "refactor_generation": 1},
            "refactor": {"mode": "simplify", "survey": rid, "base": "a" * 40, "pipeline": "orders",
                         "files": ["src/orders.py"], "evidence": ["src/orders.py"], "depends": depends, "detail": detail,
                         "roles": ROLES}, "cell": {**ROLES["worker"], "fast": False}, "reviewer": ROLES["reviewer"]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    fake = FastAPI()
    mode, generation = "preserve", 0
    legacy = {"id": "aaaabbbb", "mode": "cleanup", "state": "stopped", "phase": "steps", "goal": "Old cleanup",
              "stopped": {"reason": "restart", "detail": ""}, "spent": {"seconds": 1, "calls": 1, "tokens": 1},
              "steps": [{"n": 1, "state": "done", "spec": "old-step", "pr": 12}], "created": time.time() - 99}
    runs, specs, surveys, starts, rechecks, actions, errors = [legacy], [], [], [], [], [], []

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        nonlocal mode, generation
        if path == "mobile/status":
            return {"local": True}
        if path == "channels":
            rows = fixture(path, request.method)
            rows.insert(1, {**rows[0], "id": "refactor", "label": "리팩터링", "model": "opus", "effort": "high",
                            "refactor_mode": mode, "refactor_generation": generation, "refactor_pinned": bool(surveys)})
            return rows
        if path == "config/refactor":
            mode = (await request.json())["refactor_mode"]
            generation += 1
            return {"kept": True}
        if path == "refactors" and request.method == "POST":
            surveys.append(await request.json())
            runs.insert(0, survey(surveys[-1], generation))
            return runs[0]
        if path == "refactors":
            return {"repo": "fixture", "scope": "project", "runs": runs}
        if path.startswith("refactors/specs/"):
            rechecks.append((path.split("/")[2], await request.json()))
            return {"id": "recheck1"}
        if path.startswith("refactors/") and path.count("/") == 2:
            actions.append(path.rsplit("/", 1)[1])
            runs[0]["state"] = "stopped" if actions[-1] == "cancel" else "running"
            return runs[0]
        if path == "specs":
            return {"project": "fixture", "gate": "git --version", "specs": specs}
        if path == "log/refactor":
            return []
        if path.startswith("specs/") and path.endswith("/start"):
            starts.append((path.split("/")[1], await request.json()))
            return {"path": "fixture-task", "turn": "t"}
        if path == "errors":
            errors.append(await request.json())
            return {"ok": True}
        if path == "options":
            options = fixture(path, request.method)
            options["models"].extend({"id": m, "label": m, "note": ""} for m in ("opus", "sonnet", "codex:gpt-review"))
            return options
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
            expect(nav.get_by_role("button")).to_have_text(["다음 작업", "리팩터링", "위키", "회고"])
            nav.get_by_role("button", name="리팩터링", exact=True).click()
            modes = page.get_by_role("radiogroup", name="리팩터링 모드", exact=True)
            expect(modes.get_by_role("radio")).to_have_count(3)
            expect(modes.get_by_role("radio", name="동작 보존 구조 정리", exact=False)).to_have_attribute("aria-checked", "true")
            expect(page.get_by_text("이 모드로 한 조사가 아직 없다.", exact=True)).to_be_visible()
            modes.get_by_role("radio", name="불필요한 복잡성·계약 정리", exact=False).click()
            expect(modes.get_by_role("radio", name="불필요한 복잡성·계약 정리", exact=False)).to_have_attribute("aria-checked", "true")
            assert mode == "simplify" and generation == 1

            # Every role is picked here: the chat's model (`opus · high` on this channel) is no default.
            launch = page.get_by_role("button", name="리팩터링 시작", exact=True)
            expect(launch).to_be_disabled()
            expect(page.get_by_text("조사·구현·리뷰 모델을 고르지 않았다", exact=True)).to_be_visible()
            pickers = page.get_by_label("리팩터링 모델", exact=True).get_by_role("combobox")
            for i, role in enumerate(("survey", "worker", "reviewer")):
                pickers.nth(2 * i).click()   # each role's model, then its effort
                page.get_by_role("option", name=ROLES[role]["model"], exact=True).click()
            # The pickers say what was picked; no summary line repeats them, and nothing is left unpicked.
            expect(page.get_by_text("모델을 고르지 않았다", exact=False)).to_have_count(0)
            expect(launch).to_be_enabled()
            launch.click()
            report = page.get_by_label("리팩터링 조사", exact=True)
            expect(report.get_by_role("status")).to_have_text("진행 중 · 기준 커밋 게이트")
            assert [(s["mode"], s["roles"]) for s in surveys] == [("simplify", ROLES)], surveys
            # The conversation that refines its cards keeps the survey model: its toolbar locks at once.
            expect(page.locator("#query-options").get_by_role("combobox").nth(1)).to_be_disabled()
            expect(page.get_by_role("button", name="리팩터링 시작", exact=True)).to_be_disabled()
            run = runs[0]
            run.update(phase="pipelines", base="a" * 40, baseline=BASELINE, map=MAP, pipelines={"orders": PIPELINE},
                       models=[{"phase": "map", "requested": "opus", "reported": "claude-opus-5-5"}])
            expect(report.get_by_role("status")).to_have_text("진행 중 · 파이프라인 설명 1/2")
            expect(report.get_by_text("이미 실패 1건", exact=False)).to_be_visible()
            report.get_by_text("Orders", exact=True).click()
            expect(report.get_by_text("Order validation repeated in three handlers", exact=False)).to_be_visible()
            expect(report.get_by_text("크기 보통", exact=False)).to_be_visible()
            report.get_by_role("button", name="멈추기", exact=True).click()
            report.get_by_role("button", name="재개", exact=True).click()
            expect(report.get_by_role("button", name="멈추기", exact=True)).to_be_visible()
            assert actions == ["cancel", "resume"], actions

            # Done: the cards it wrote sit below the conversation, the dependent one locked.
            specs[:] = [card("shared-check", run["id"], []), card("orders-split", run["id"], [f"shared-check-{run['id']}"])]
            run.update(state="done", phase="done", pipelines={"orders": PIPELINE, "billing": {**PIPELINE, "id": "billing"}},
                       survey={"coverage": {"scope": "Both pipelines and their tests", "unchecked": [], "excluded": []}},
                       specs=[s["id"] for s in specs])
            expect(report.get_by_role("status")).to_have_text("완료")
            cards = page.get_by_label("리팩터링 명세", exact=True)
            expect(cards.get_by_role("button", name="시작 ▸", exact=True)).to_have_count(2)
            first, second = cards.get_by_role("button", name="시작 ▸", exact=True).all()
            expect(first).to_be_enabled()
            expect(second).to_be_disabled()
            expect(cards.get_by_label("시작할 수 없는 이유", exact=True)).to_contain_text(f"선행 명세 shared-check-{run['id']}")
            expect(cards.get_by_role("button", name="명세 수정", exact=True)).to_have_count(0)
            expect(cards.get_by_text("Order validation runs once at the boundary", exact=False).first).to_be_visible()
            cards.get_by_role("button", name="다시 확인", exact=True).last.click()
            first.click()
            for _ in range(50):
                if starts and rechecks:
                    break
                page.wait_for_timeout(100)
            expect(cards.get_by_label("고정 모델", exact=True)).to_have_count(2)
            expect(cards.get_by_label("고정 모델", exact=True).first).to_have_text("조사 opus / 구현 sonnet / 리뷰 codex:gpt-review")
            assert [(sid, body["model"]) for sid, body in starts] == [(f"shared-check-{run['id']}", "sonnet")], starts
            assert [(sid, body["mode"]) for sid, body in rechecks] == [(f"orders-split-{run['id']}", "simplify")], rechecks

            history = page.get_by_text("이전 방식의 실행 1개 · 읽기 전용", exact=True)
            history.click()
            old = page.get_by_label("이전 방식의 실행", exact=True)
            expect(old).to_contain_text("이전 · 빠른 정리")
            expect(old.get_by_role("button")).to_have_count(0)
            measurements = []
            for width in (1440, 400):
                page.set_viewport_size({"width": width, "height": 900})
                if width == 400:
                    page.get_by_role("navigation", name="화면", exact=True).get_by_role("button", name="대화", exact=True).click()
                measurements.append(page.evaluate("() => ({width: innerWidth, document: document.documentElement.scrollWidth})"))
                assert measurements[-1]["document"] <= width, measurements[-1]
            output = ROOT / "artifacts"
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / "refactor-survey.png"), full_page=True)
            assert not errors, errors
            print(json.dumps({"measurements": measurements, "starts": len(starts), "actions": actions}, ensure_ascii=False))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


if __name__ == "__main__":
    main()
