"""Two windows on one server keep their own projects (docs/plans/window-project, stage 3).

Run after building: python web/tests/window_project_browser.py
Page A shows `fixture-a`; page B starts on the server's default and picks `fixture-b`, which
moves the default. A reload of A still asks for `fixture-a`. A's rail names B's running turn in
a shared checkout by the project the server gives, and shows the approval it waits on. With a
stand-in for the desktop shell, closing one of two windows asks nothing, and closing the last
asks about the answer another window is waiting for; cancelling leaves the window open. A status
read that never answers still lets the question come, and a session store that throws still
lets a window start.
"""

import asyncio
import json
import sys
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from mobile_browser import ROOT, fixture
from browser_fixture import page_of, served

SHARED = "C:/repos/fixture-b"   # a primary checkout whose parent folder is not its project

# The desktop shell as the page sees it: `invoke` answers from `window.__shell`, and the close
# listener the page registers is kept so the test can fire it.
SHELL = """
window.__shell = { windows: ['main'], calls: [], listeners: {} }
let next = 0
window.__TAURI_INTERNALS__ = {
  metadata: { currentWindow: { label: 'main' }, currentWebview: { windowLabel: 'main', label: 'main' } },
  transformCallback(fn) { const id = ++next; window['_' + id] = fn; return id },
  unregisterCallback(id) { delete window['_' + id] },
  async invoke(cmd, args) {
    window.__shell.calls.push(cmd)
    if (cmd === 'plugin:event|listen') { window.__shell.listeners[args.event] = window['_' + args.handler]; return 1 }
    if (cmd === 'plugin:window|get_all_windows') return window.__shell.windows
    return null
  },
}
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fake, default, asked = FastAPI(), ["fixture-a"], []
    live, faults = {"answers": 0}, {"stall": False, "channels": False}

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        named = unquote(request.headers.get("x-project", ""))
        asked.append((path, named))
        project = named or default[0]
        if path == "channels":
            if faults["channels"]:
                return JSONResponse({"detail": "fixture channel failure"}, status_code=500)
            return [{**c, "repo": project} for c in fixture(path, request.method)]
        if path == "config/wiki" and request.method == "POST":
            default[0] = (await request.json())["repo"]
            return {**fixture("channels", "GET")[1], "repo": default[0]}
        if path == "options":
            options = fixture(path, request.method)
            options["projects"] = [{**options["projects"][0], "id": name} for name in ("fixture-a", "fixture-b")]
            return options
        if path in ("worktrees", "specs", "prs"):
            rows = [{"path": f"{project}-task", "name": f"{project}-task", "branch": "fixture", "dirty": False,
                     "merged": False, "live": False, "busy": False}]
            return {"project": project, "repo": project, "rows": rows, "specs": []}
        if path == "loops":
            if faults["stall"]:
                await asyncio.sleep(30)
            return {"loops": [], "turns": [{"path": SHARED, "repo": "fixture-b", "spec": None}], **live}
        if path == "work/log" and request.query_params["path"] == SHARED:
            return {"rows": [], "session_id": "b", "busy": True, "running": {"turn": "turn-b", "session_id": "b", "seq": 0},
                    "rules": [], "queued": None}
        if path == "work/events" and request.query_params["path"] == SHARED:
            async def waiting():
                ev = {"kind": "approval", "text": "Run the checks", "meta": {"id": "ask-b", "tool": "Bash"},
                      "seq": 0, "turn": "turn-b", "session_id": "b"}
                yield f"data: {json.dumps(ev)}\n\n".encode()
                await asyncio.sleep(60)
            return StreamingResponse(waiting(), media_type="text/event-stream")
        return {"local": True} if path == "mobile/status" else fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    with served(fake) as port, sync_playwright() as p:
        browser = p.chromium.launch()
        base, errors = f"http://127.0.0.1:{port}", []
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        a, b = page_of(context, errors), page_of(context, errors)
        picker = lambda page: page.get_by_role("combobox", name="프로젝트", exact=True)   # noqa: E731
        a.goto(base)
        expect(picker(a)).to_contain_text("fixture-a")
        b.goto(base)
        expect(picker(b)).to_contain_text("fixture-a")   # a new window starts from the default
        picker(b).click()
        b.get_by_role("option", name="fixture-b", exact=True).click()
        expect(picker(b)).to_contain_text("fixture-b")
        assert default[0] == "fixture-b"

        from_a = []
        # The phone link's own status is no project's.
        a.on("request", lambda r: "/api/" in r.url and "/api/mobile/" not in r.url
             and from_a.append((r.url, r.headers.get("x-project"))))
        a.reload()
        expect(picker(a)).to_contain_text("fixture-a")
        a.get_by_text("fixture-a-task").first.wait_for()
        assert from_a and all(named == "fixture-a" for _, named in from_a), from_a

        other = a.get_by_role("button", name="fixture-b/fixture-b")
        other.wait_for()
        assert "repos" not in a.get_by_role("navigation").first.inner_text()
        other.click()
        expect(a.get_by_role("button", name="fixture-b/fixture-b")).to_contain_text("승인 1")

        shell = page_of(context, errors)
        shell.add_init_script(SHELL)
        shell.goto(base)
        shell.get_by_role("button", name="새 창").click()
        shell.wait_for_function("window.__shell.calls.includes('new_window')")
        shell.wait_for_function("'tauri://close-requested' in window.__shell.listeners")
        close = "window.__shell.listeners['tauri://close-requested']({ event: 'tauri://close-requested', id: 1 })"

        dialogs = []
        shell.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        shell.evaluate("window.__shell.windows = ['main', 'main-2']")
        shell.evaluate(close)
        shell.wait_for_function("window.__shell.calls.includes('plugin:window|destroy')")
        assert not dialogs, f"closing one of two windows asked {dialogs}"

        def last_close(page, dialogs):
            """Closes `page` as the last window and cancels: the question it asked."""
            dialogs.clear()
            page.evaluate("window.__shell.windows = ['main']; window.__shell.calls = []")
            page.evaluate(close)
            for _ in range(80):   # the status read gives up after three seconds
                if dialogs:
                    break
                page.wait_for_timeout(100)
            page.wait_for_timeout(300)
            assert "plugin:window|destroy" not in page.evaluate("window.__shell.calls"), "a cancelled close closed"
            return dialogs[0] if dialogs else ""

        live["answers"] = 1
        asked_now = last_close(shell, dialogs)
        assert "도는 작업 1개" in asked_now and "만드는 답 1개" in asked_now, asked_now
        # Review R1-2: a status read that never answers held the close for good.
        faults["stall"] = True
        assert "읽지 못했다" in last_close(shell, dialogs)
        faults["stall"], faults["channels"] = False, True
        fresh = page_of(context, errors)   # no project stored, and its first channel list fails
        fresh.add_init_script(SHELL)
        fresh.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        fresh.goto(base)
        fresh.wait_for_function("'tauri://close-requested' in window.__shell.listeners")
        assert "읽지 못했다" in last_close(fresh, dialogs)
        faults["channels"] = False

        # Review R1-1: a session store that throws, on reading or on a full write, kept the window
        # from starting. It only forgets the project.
        broken = page_of(context, errors)
        broken.add_init_script("""for (const name of ['getItem', 'setItem']) {
          const real = Storage.prototype[name]
          Storage.prototype[name] = function (...args) {
            if (this === window.sessionStorage) throw new DOMException('fixture', 'QuotaExceededError')
            return real.apply(this, args)
          }
        }""")
        broken.goto(base)
        expect(picker(broken)).to_contain_text("fixture-b")
        expect(picker(broken)).to_be_enabled()
        picker(broken).click()
        broken.get_by_role("option", name="fixture-a", exact=True).click()
        expect(picker(broken)).to_contain_text("fixture-a")
        broken.get_by_text("fixture-a-task").first.wait_for()
        browser.close()
    assert not errors, errors
    print("PASS: two windows keep their own projects; the rail and the close question cover every window")


if __name__ == "__main__":
    main()
