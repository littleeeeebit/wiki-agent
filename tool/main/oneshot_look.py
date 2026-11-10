"""oneshot S7's shooting harness: the built app started by its `live_cmd`, every screen shot at every viewport
in every state, and the mechanical findings read off the pages. A server routine, never a model.

`docs/plans/oneshot/4-frontend.md` ("Look: the shooting harness") is the design. The build fixes the
conventions it relies on: the app serves its screens and its API on one port, the port and the data folder
come from `PORT` and `APP_DATA_DIR`, screens use hash routes (`/#/months`), and `e2e/seed.json` lists the
API calls that fill it. A placement or console finding is high by definition.
"""

from __future__ import annotations

import ctypes
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


def never() -> bool:
    return False


def halted(stopped) -> None:
    if stopped():
        raise ValueError("화면 검사 중에 멈췄다 — [재개] 로 빌드부터 다시 검사한다")


class _Row(ctypes.Structure):   # MIB_TCPROW_OWNER_PID
    _fields_ = [(n, ctypes.c_uint32) for n in ("state", "local", "port", "remote", "remote_port", "pid")]


class _Row6(ctypes.Structure):   # MIB_TCP6ROW_OWNER_PID
    _fields_ = [("local", ctypes.c_ubyte * 16), ("scope", ctypes.c_uint32), ("port", ctypes.c_uint32),
                ("remote", ctypes.c_ubyte * 16), ("remote_scope", ctypes.c_uint32), ("remote_port", ctypes.c_uint32),
                ("state", ctypes.c_uint32), ("pid", ctypes.c_uint32)]


def listeners(port: int) -> set[int] | None:
    """The pids listening on TCP `port`, IPv4 and IPv6, or `None` where this system's table is not read.
    A pid of 0 stands for a socket this process may not see the owner of."""

    if os.name == "nt":
        found = set()
        table = ctypes.windll.iphlpapi.GetExtendedTcpTable
        for family, row in ((socket.AF_INET, _Row), (socket.AF_INET6, _Row6)):
            size = ctypes.c_uint32(0)
            table(None, ctypes.byref(size), False, family, 3, 0)   # TCP_TABLE_OWNER_PID_LISTENER
            buf = ctypes.create_string_buffer(size.value)
            if table(buf, ctypes.byref(size), False, family, 3, 0):
                return None
            count = ctypes.c_uint32.from_buffer(buf).value
            rows = (row * count).from_buffer(buf, ctypes.sizeof(ctypes.c_uint32))   # after `dwNumEntries`
            found |= {r.pid for r in rows if socket.ntohs(r.port & 0xFFFF) == port}
        return found
    if not Path("/proc/net/tcp").is_file():   # ponytail: macOS has no /proc; the launcher check stands alone
        return None
    inodes = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            cols = line.split()
            if cols[3] == "0A" and int(cols[1].rsplit(":", 1)[1], 16) == port:   # LISTEN
                inodes.add(f"socket:[{cols[9]}]")
    if not inodes:
        return set()
    found = set()
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                if any(os.readlink(fd) in inodes for fd in (proc / "fd").iterdir()):
                    found.add(int(proc.name))
            except OSError:   # another user's process, or gone
                continue
    return found or {0}


class _Entry(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("size", ctypes.c_uint32), ("usage", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("heap", ctypes.c_size_t), ("module", ctypes.c_uint32), ("threads", ctypes.c_uint32),
                ("parent", ctypes.c_uint32), ("priority", ctypes.c_long), ("flags", ctypes.c_uint32),
                ("exe", ctypes.c_wchar * 260)]


def descendants(root: int) -> set[int]:
    """`root` and every process started under it, by parent pid. On Windows a job does not hold them all:
    a packaged launcher (the Store's `pwsh`) starts its children outside the job.
    ponytail: a parent pid reused by an unrelated process would count it in; creation times would rule it out."""

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {root}
    parent: dict[int, int] = {}
    try:
        entry = _Entry(size=ctypes.sizeof(_Entry))
        ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            parent[entry.pid] = entry.parent
            ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    found, more = {root}, True
    while more:
        more = {p for p, up in parent.items() if up in found and p not in found and p != up}
        found |= more
    return found


def tree(proc, job) -> set[int]:
    """The pids of the app's own processes, or the process group on POSIX (`process.contained`)."""

    return descendants(proc.pid) if os.name == "nt" else {job}


def owned(proc, job, port: int) -> bool | None:
    """Whether something listens on `port` and every listener is one of the app's processes; `None`
    while nobody listens yet."""

    found = listeners(port)
    if found is None:
        return True   # nothing to read on this system: the launcher's being alive is the check
    if not found:
        return None
    if os.name == "nt":
        return found <= tree(proc, job)
    try:
        return all(pid and os.getpgid(pid) == job for pid in found)
    except OSError:
        return False


def reaped(proc, job) -> None:
    """Every process of the app killed: the job's, and on Windows any descendant outside it."""

    stray = tree(proc, job) - {proc.pid} if os.name == "nt" else set()
    process.terminated(job)
    if proc.poll() is None:
        proc.kill()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True) if stray else None
    for pid in stray:
        handle = k32.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
        if handle:
            k32.TerminateProcess(handle, 1)
            k32.CloseHandle(handle)


@contextmanager
def served(tree: Path, cmd: str, data: Path, stopped=never):
    """The app running from `cmd` in `tree` on a free port with its data in `data`: its URL.
    Every process it started is killed on the way out; a tree it cannot own is never started, and
    a port another program took meanwhile is refused before a request goes to it."""

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
        if not job:   # its children would outlive the kill, holding the port and the data folder
            proc.kill()
            proc.wait()
            raise ValueError("`live_cmd` 의 프로세스 트리를 묶지 못해 앱을 띄우지 않았다")
        process.resumed(proc)
        url, deadline = f"http://127.0.0.1:{port}", time.monotonic() + READY_SECONDS
        while True:
            halted(stopped)
            mine = owned(proc, job, port)
            if mine is False:   # the seed's writes would go to that program
                raise ValueError(f"다른 프로그램이 포트 {port} 를 잡았다 — 앱이 띄운 프로세스가 아니라 요청을 보내지 않았다")
            if mine and proc.poll() is None:   # an answer from a port whose launcher exited is someone else's
                try:
                    with urllib.request.urlopen(url + "/", timeout=5) as answer:
                        if answer.status == 200 and proc.poll() is None:
                            break
                except (urllib.error.URLError, OSError):
                    pass
            if proc.poll() is not None or time.monotonic() > deadline:
                log.seek(0)
                raise ValueError(f"`live_cmd` 가 {url}/ 에 앱을 띄우지 못했다 ({proc.poll()}) — {log.read()[-600:]}")
            time.sleep(0.5)
        yield url
    finally:
        reaped(proc, job)
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


# Shown and inside the viewport as it stands: `is_visible` alone counts an element far below the fold.
IN_VIEW = """el => { const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth; }"""


def in_view(element) -> bool:
    return element.is_visible() and element.evaluate(IN_VIEW)


def visible(page, fid: str) -> bool:
    found = page.locator(f'[data-feature="{fid}"]')
    return any(in_view(found.nth(i)) for i in range(min(found.count(), 5)))


def one_click(browser, url: str, route: str, view: str, api: re.Pattern, fid: str, stopped=never) -> bool:
    """Whether some single click on the first screen, on a control in view without scrolling, makes the
    feature visible in view, there or where it leads."""

    page = opened(browser, url, route, view, "full", api, [])
    try:
        n = min(page.locator(CLICKABLE).count(), MAX_CLICKS)
    finally:
        page.close()
    for i in range(n):
        halted(stopped)
        page = opened(browser, url, route, view, "full", api, [])
        try:
            target = page.locator(CLICKABLE).nth(i)
            if not in_view(target):   # Playwright would scroll to it: that is more than one click
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


def placement(browser, url: str, front: dict, frequency: dict, view: str, api: re.Pattern,
              stopped=never) -> list[dict]:
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
        if cls == "sometimes" and not on_first[f] and not one_click(browser, url, first["route"], view, api, f, stopped):
            out.append(finding("high", first["id"], view, "full", f"가끔 쓰는 기능 `{f}` 가 한 번 눌러서 보이지 않는다"))
    return out


def look(tree: Path, cmd: str, front: dict, frequency: dict, ops: list[str], views: list[str],
         folder: Path, stopped=never) -> list[dict]:
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
                with served(tree, cmd, data, stopped) as url:
                    if data_state == "full":
                        seeded(tree, url)
                    for s in front["screens"]:
                        for view in views:
                            for state in states:
                                halted(stopped)   # a stop ends the look: the app and the browser go below
                                findings += shot(browser, url, s, view, state, api, folder, shown)
                    if data_state == "full":
                        for view in views:
                            findings += placement(browser, url, front, frequency, view, api, stopped)
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
