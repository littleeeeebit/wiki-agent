"""A spec card with requirements opens the human requirements document the server rendered.

Run after building: python web/tests/spec_views_browser.py
Serves web/dist with synthetic fixture data; the document is `spec_views.human` of a fixture record.
"""

import sys

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from browser_fixture import page_of, served
from mobile_browser import ROOT, fixture
from main import spec_views


def record() -> dict:
    spec = {"id": "fixture-views", "repo": "fixture", "rev": 1, "goal": "Synthetic drafts survive a reload",
            "out": ["Sync"], "done": ["git --version", "Synthetic check passes"], "state": "정리됨", "worktree": None,
            "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [], "history": [{"ts": 1}],
            "source": {"plan": None}, "missing": [], "report": None, "pr": None, "original": "Keep my drafts.",
            "requirements": spec_views.requirements([
                {"id": "R1", "text": "A reload keeps the draft", "source": {"kind": "original", "locator": "request"},
                 "status": "confirmed", "verify": [{"item": "reload the page", "expected": "the draft is shown"}]},
                {"id": "R2", "text": "Drafts older than 30 days go", "source": {"kind": "decision", "locator": "chat"},
                 "status": "undecided"}])}
    spec_views.stamped(None, spec)
    return spec


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    spec, asked = record(), []
    fake = FastAPI()

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "specs":
            return {"project": "fixture", "gate": "git --version", "specs": [spec]}
        if path == "log/next":
            return [{"role": "assistant", "text": "Synthetic specification", "blocks": [{"name": "spec", "id": spec["id"]}]}]
        if path == f"specs/{spec['id']}/views":
            asked.append(path)
            return spec_views.views(spec)
        return {"local": True} if path == "mobile/status" else fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    with served(fake) as port, sync_playwright() as p:
        browser, errors = p.chromium.launch(), []
        page = page_of(browser, errors, viewport={"width": 1280, "height": 900})
        page.goto(f"http://127.0.0.1:{port}")
        page.get_by_role("navigation", name="초점").get_by_role("button", name="다음 작업").click()
        page.get_by_text("요구사항 문서 · 버전 1", exact=True).click()
        document = page.get_by_label("요구사항 문서", exact=True)
        for heading in ("R1 — confirmed", "R2 — undecided"):
            expect(document.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(document).to_contain_text("reload the page ⇒ the draft is shown")
        expect(document).to_contain_text("Keep my drafts.")
        browser.close()
    assert asked == [f"specs/{spec['id']}/views"], asked
    assert not errors, errors
    print("PASS: a spec card with requirements shows the server's requirements document with its ids")


if __name__ == "__main__":
    main()
