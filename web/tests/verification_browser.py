"""Both native approval editors preserve synthetic settings across a real browser save."""

import copy
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from browser_fixture import page_of, served
from mobile_browser import fixture

ROOT = Path(__file__).resolve().parents[2]


def main():
    fake, writes = FastAPI(), []
    spec = {"id": "fixture-task", "repo": "fixture", "rev": 1, "goal": "Synthetic native acceptance",
            "done": ["Synthetic check"], "out": [], "state": "멈춤", "worktree": "fixture-task",
            "branch": "fixture", "implementation_environment": "claude-cloud", "rounds": [],
            "pr": {"number": 7, "url": "https://example.test/pr/7", "base": "main", "head": "a" * 40}}
    base = {"environment_id": "fixture", "test_scope": "disposable", "env_file": "C:/fixtures/.env",
            "browser_tool": "fixture", "allowed_origins": ["http://127.0.0.1:8136"],
            "revisions": {"api": "one"}, "setup": "", "cleanup": ""}
    win32 = {"version": 1, "host": "windows-win32", "application": "fixture", "executable": "C:/fixtures/app.exe",
             "executable_sha256": "1" * 64, "profile": "disposable", "ownership": "launch-disposable", "desktop": "attempt-owned"}
    built = {"version": 1, "host": "windows-webview2", "application": "built", "runner": "C:/fixtures/node.exe",
             "runner_sha256": "2" * 64, "profile": "disposable"}
    asset = {"path": "C:/fixtures/app.exe", "sha256": "3" * 64}
    owned = {"version": 2, "host": "webview", "node": {"path": "C:/fixtures/node.exe", "sha256": "2" * 64},
             "applications": {"owned": {"platform": "windows", "binary": asset, "profile": "disposable", "renderer": "auto"}}}
    cases = [
        (2, win32, [{"driver": "win32", "application": "fixture"}], [win32]),
        (3, [win32, built], [{"driver": "win32", "application": "fixture"},
                           {"driver": "webview", "application": "built", "platform": "windows", "build": "fixture-build"}], [win32, built]),
        (2, owned, [{"driver": "webview", "application": "owned", "platform": "windows"}], owned),
    ]
    config = {}

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        if path == "mobile/status":
            return {"local": True, "enabled": False, "starting": False, "apk_available": False}
        if path == "specs":
            return {"project": "fixture", "specs": [spec]}
        if path == "specs/fixture-task/review/log":
            return {"rows": [], "running": None}
        if path == "verification/config":
            assert request.headers.get("x-project") == "fixture"
            if request.method == "PUT":
                body = await request.json()
                writes.append(body)
                config["settings"] = body
            return config
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        errors = []
        page = page_of(browser, errors, viewport={"width": 1440, "height": 900})
        for version, approved, targets, expected in cases:
            config.clear()
            config.update(settings={**base, "version": version, "native": copy.deepcopy(approved)}, manifest_digest="0" * 64,
                manifest={"version": 2, "contracts": ["api.md"], "flows": [
                    {"id": f"save-{i}", "title": "Synthetic save", "kind": "desktop", "command": "wiki-agent-native",
                     "paths": ["*"], "environments": ["api"], "assertions": [{"id": "saved", "expected": "Saved"}],
                     "native": target} for i, target in enumerate(targets)]})
            page.goto(f"http://127.0.0.1:{port}")
            row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
            try:
                row.click(timeout=10000)
            except Exception:
                raise AssertionError({"errors": errors, "screen": page.locator("body").inner_text()}) from None
            page.get_by_role("tab", name="리뷰", exact=True).click()
            page.get_by_role("button", name="프로젝트 검증 설정", exact=True).click()
            save = page.get_by_role("button", name="명령 확인 후 설정 저장", exact=True)
            save.click()
            save.wait_for(state="detached")
            assert writes[-1]["version"] == (2 if approved is owned else 3)
            assert writes[-1]["native"] == expected
            assert writes[-1]["revisions"] == base["revisions"]
            assert not any(k.startswith("native:") or k == "native_scope" for k in writes[-1])
        assert not errors, errors
        browser.close()
    print("PASS: single Win32 approval migrates to per-app list, multiple app approvals and owned-binary JSON survive browser saves")


if __name__ == "__main__":
    main()
