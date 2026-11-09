"""oneshot S7's shooting harness: the built app started by its `live_cmd`, every screen shot at every viewport
in every state, and the mechanical findings read off the pages. A server routine, never a model.

`docs/plans/oneshot/4-frontend.md` ("Look: the shooting harness") is the design. The build fixes the
conventions it relies on: the app serves its screens and its API on one port, the port and the data folder
come from `PORT` and `APP_DATA_DIR`, screens use hash routes (`/#/months`), and `e2e/seed.json` lists the
API calls that fill it. A placement or console finding is high by definition.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from common import process, shell

from .oneshot_screens import VIEWPORTS

READY_SECONDS = 180
PAGE_MS = 15_000
SETTLE_MS = 800
MAX_CLICKS = 15
CLICKABLE = "a, button, [role=button], [role=tab], [role=menuitem], summary"
SEED = "e2e/seed.json"


def finding(severity: str, sid: str, view: str, state: str, what: str) -> dict:
    return {"severity": severity, "screen": sid, "viewport": view, "state": state, "what": what}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def served(tree: Path, cmd: str, data: Path):
    """The app running from `cmd` in `tree` on a free port with its data in `data`: its URL.
    Every process it started is killed on the way out."""

    port = free_port()
    env = {**os.environ, "PORT": str(port), "APP_DATA_DIR": str(data), "PYTHONUNBUFFERED": "1"}
    argv, use_shell = shell.launch("gate", command=cmd)
    log = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(argv, shell=use_shell, cwd=tree, env=env, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                            **{**process.background_options(), "creationflags":
                               process.background_options().get("creationflags", 0) | process.SUSPENDED})
    job = process.contained(proc)
    try:
        process.resumed(proc)
        url, deadline = f"http://127.0.0.1:{port}", time.monotonic() + READY_SECONDS
        while True:
            try:
                with urllib.request.urlopen(url + "/", timeout=5) as answer:
                    if answer.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                log.seek(0)
                raise ValueError(f"`live_cmd` 가 {url}/ 에 앱을 띄우지 못했다 ({proc.poll()}) — {log.read()[-600:]}")
            time.sleep(0.5)
        yield url
    finally:
        process.terminated(job)
        if proc.poll() is None:
            proc.kill()
        log.close()


def seeded(tree: Path, url: str) -> None:
    """`e2e/seed.json` replayed through the API: the `full` state, made the way a person would make it."""

    try:
        calls = json.loads((tree / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"`{SEED}` 가 없거나 JSON 이 아니다 — {exc}") from exc
    if not isinstance(calls, list) or not calls or not all(
            isinstance(c, dict) and isinstance(c.get("method"), str) and isinstance(c.get("path"), str)
            and c["path"].startswith("/") for c in calls):
        raise ValueError(f"`{SEED}` 는 `{{method, path, body}}` 호출의 비지 않은 목록이다")
    for c in calls:
        body = json.dumps(c["body"]).encode("utf-8") if "body" in c else None
        request = urllib.request.Request(url + c["path"], data=body, method=c["method"].upper(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except (urllib.error.URLError, OSError) as exc:
            raise ValueError(f"`{SEED}` 의 `{c['method']} {c['path']}` 가 실패했다 — {exc}") from exc


def api_paths(ops: list[str]) -> re.Pattern:
    """A URL path any served operation answers: `{id}` matches one segment."""

    templates = sorted({op.split(" ", 1)[1] for op in ops})
    parts = [re.sub(r"\\\{[^/]*?\\\}", "[^/]+", re.escape(t)) for t in templates]
    return re.compile("(?:" + "|".join(parts) + r")/?") if parts else re.compile(r"(?!)")


def opened(browser, url: str, route: str, view: str, state: str, api: re.Pattern, found: list[str]):
    """A page at `route`, its API answering as `state` asks: `loading` never answers, `error` answers 500."""

    width, height = VIEWPORTS[view]
    page = browser.new_page(viewport={"width": width, "height": height})
    held = []

    def routed(r) -> None:
        path = urllib.parse.urlsplit(r.request.url).path
        if r.request.resource_type in ("fetch", "xhr") and api.fullmatch(path):
            if state == "loading":
                held.append(r)   # never answered while the shot is taken
                return
            if state == "error":
                r.fulfill(status=500, content_type="application/json", body='{"detail": "error"}')
                return
        r.continue_()

    # A failed request logs a console error by itself, so `error` counts uncaught errors only.
    if state != "error":
        page.on("console", lambda m: m.type == "error" and found.append(f"콘솔 오류 {m.text[:200]}"))
    page.on("pageerror", lambda e: found.append(f"페이지 오류 {str(e)[:200]}"))
    page.route("**/*", routed)
    page.goto(f"{url}/#{route}", wait_until="domcontentloaded", timeout=PAGE_MS)
    if state != "loading":
        try:
            page.wait_for_load_state("networkidle", timeout=PAGE_MS)
        except Exception:   # a page that keeps polling is shot as it stands  # noqa: BLE001
            pass
    page.wait_for_timeout(SETTLE_MS)
    return page


def visible(page, fid: str) -> bool:
    found = page.locator(f'[data-feature="{fid}"]')
    return any(found.nth(i).is_visible() for i in range(min(found.count(), 5)))


def one_click(browser, url: str, route: str, view: str, api: re.Pattern, fid: str) -> bool:
    """Whether some single click on the first screen makes the feature visible, there or where it leads."""

    page = opened(browser, url, route, view, "full", api, [])
    try:
        n = min(page.locator(CLICKABLE).count(), MAX_CLICKS)
    finally:
        page.close()
    for i in range(n):
        page = opened(browser, url, route, view, "full", api, [])
        try:
            target = page.locator(CLICKABLE).nth(i)
            if not target.is_visible():
                continue
            target.click(timeout=3_000)
            page.wait_for_timeout(SETTLE_MS)
            if visible(page, fid):
                return True
        except Exception:   # a control that cannot be clicked opens nothing  # noqa: BLE001
            continue
        finally:
            page.close()
    return False


def placement(browser, url: str, front: dict, frequency: dict, view: str, api: re.Pattern) -> list[dict]:
    """The owner's map read from the DOM in the `full` state; each breach is high."""

    first = next(s for s in front["screens"] if s["primary"])
    out = []
    page = opened(browser, url, first["route"], view, "full", api, [])
    try:
        on_first = {f: visible(page, f) for f in frequency}
        present = {f: page.locator(f'[data-feature="{f}"]').count() > 0 for f in frequency}
    finally:
        page.close()
    for f, cls in frequency.items():
        if cls == "every" and not on_first[f]:
            out.append(finding("high", first["id"], view, "full", f"늘 쓰는 기능 `{f}` 가 첫 화면에 바로 보이지 않는다"))
        if cls in ("rarely", "never") and present[f]:
            out.append(finding("high", first["id"], view, "full", f"`{f}` 는 첫 화면에 없어야 한다"))
        if cls == "sometimes" and not on_first[f] and not one_click(browser, url, first["route"], view, api, f):
            out.append(finding("high", first["id"], view, "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out


def look(tree: Path, cmd: str, front: dict, frequency: dict, ops: list[str], views: list[str],
         folder: Path) -> list[dict]:
    """Every screen at every viewport in `empty`, `full`, `loading` and `error`, shot into `folder` as
    `<screen>-<viewport>-<state>.png`; the findings, the placement check's included."""

    from playwright.sync_api import sync_playwright

    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    api, findings = api_paths(ops), []
    shown: dict[str, bool] = {f: False for f in frequency}
    with tempfile.TemporaryDirectory() as scratch, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for data_state, states in (("empty", ["empty"]), ("full", ["full", "loading", "error"])):
                data = Path(scratch) / data_state
                data.mkdir()
                with served(tree, cmd, data) as url:
                    if data_state == "full":
                        seeded(tree, url)
                    for s in front["screens"]:
                        for view in views:
                            for state in states:
                                findings += shot(browser, url, s, view, state, api, folder, shown)
                    if data_state == "full":
                        for view in views:
                            findings += placement(browser, url, front, frequency, view, api)
        finally:
            browser.close()
    first = next(s for s in front["screens"] if s["primary"])
    for f, cls in frequency.items():
        if cls == "never" and shown[f]:
            findings.append(finding("high", first["id"], views[0], "full", f"화면에 없기로 한 `{f}` 가 화면에 있다"))
        if cls != "never" and not shown[f]:
            findings.append(finding("high", first["id"], views[0], "full", f"`{f}` 가 어느 화면에도 없다"))
    return findings


def shot(browser, url: str, s: dict, view: str, state: str, api: re.Pattern, folder: Path, shown: dict) -> list[dict]:
    errors: list[str] = []
    page = opened(browser, url, s["route"], view, state, api, errors)
    try:
        page.screenshot(path=str(folder / f"{s['id']}-{view}-{state}.png"), full_page=True)
        wide = page.evaluate("document.documentElement.scrollWidth") > VIEWPORTS[view][0]
        if state == "full":
            for f in shown:
                shown[f] = shown[f] or page.locator(f'[data-feature="{f}"]').count() > 0
    finally:
        page.close()
    out = [finding("high", s["id"], view, state, e) for e in dict.fromkeys(errors)]
    if wide:
        out.append(finding("medium", s["id"], view, state, "가로로 넘친다 (scrollWidth 가 화면 폭보다 크다)"))
    return out
