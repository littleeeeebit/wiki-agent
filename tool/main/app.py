"""app — the program's main. It weaves the pipelines and nothing weaves it.

Three parts hang here: the wiki query (`query`), the worktrees and their
agents (`work`), and what the whole screen shares — the translation switch,
looking inside a file, the map. The terminal is not here; the Tauri shell
holds it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AnyHttpUrl, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError

import translate
from agent import chat_session
from common import errorlog
from common import settings as settings_file

from . import architecture, channels, connect, improvements, loop, mobile, planning, query, refactor, refactor_api, runtime, specs, suite, survey, update, verification, work
from .runtime import server_owner

# On Windows `mimetypes` reads the registry, where `.js` is commonly
# `text/plain`. The browser then refuses `<script type="module">` silently:
# nothing in the console, just an empty screen. It really did go blank that
# way once.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")

ROOT = channels.WIKI
DIST = ROOT / "web" / "dist"
SWITCH = query.LOGS / "main.json"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """When the server goes down the children go with it, or `claude.exe` piles up.

    Runs on Ctrl+C, on an ordinary shutdown, and when the Tauri shell that
    started this closes its pipe. A forced kill never reaches this.
    """

    with server_owner(specs.SPECS.parent):
        runtime.stopping.clear()
        # Recovery must run only after exclusive ownership is acquired: a
        # second server must never mark the first server's live loops stopped.
        resume = loop.recover()
        planning.recover()
        refactor.recover()
        survey.recover()
        for repo, sid in resume:
            try:
                loop.kick(repo, sid, automatic=True)
            except Exception as exc:
                errorlog.record("review-resume", exc, repo=repo, spec=sid)
        poll_stop = threading.Event()
        poll_thread = threading.Thread(target=loop.poll, args=(poll_stop,), daemon=True)
        poll_thread.start()
        try:
            yield
        finally:
            runtime.stopping.set()
            poll_stop.set()
            poll_thread.join()
            mobile.companion.stop()
            close_turns()
            # Every request has finished by now; a server started again in this process may start providers.
            chat_session.reopen()


def close_turns() -> None:
    """Every running turn ended and its provider gone, and none starts: one
    no registry holds, such as a query's explanation, included. Safe to call again."""

    runtime.stopping.set()
    chat_session.end_all()
    loop.close_all()
    planning.close_all()
    refactor.close_all()
    query.close_all()
    work.close_all()


def ended_first(server) -> None:
    """`server`'s shutdown ends the running turns before it waits for open
    responses. Providers lead their own process group, so Ctrl+C never reaches
    them, and a stream waiting on a turn would hold that wait forever. The wait
    stays unbounded: a merge request in progress finishes while this server
    still owns the hub."""

    drain = server.shutdown

    async def shutdown(sockets=None):
        for listener in server.servers:
            listener.close()
        await asyncio.to_thread(close_turns)
        await drain(sockets)

    server.shutdown = shutdown


app = FastAPI(title="wiki-agent", lifespan=lifespan)
app.include_router(query.router)
app.include_router(architecture.router)
app.include_router(suite.router)
app.include_router(work.router)
app.include_router(specs.router)
app.include_router(loop.router)
app.include_router(planning.router)
app.include_router(refactor_api.router)
app.include_router(improvements.router)
app.include_router(connect.router)
app.include_router(verification.router)
app.include_router(mobile.router)
app.include_router(update.router)

@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    errorlog.record("http", exc.detail, method=request.method, route=request.url.path, status=exc.status_code)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Validation exceptions embed submitted inputs; record only locations/types.
    errorlog.record("http", "Invalid request", method=request.method, route=request.url.path, status=422,
                    fields=[{"loc": e["loc"], "type": e["type"]} for e in exc.errors()])
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def log_failures(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        errorlog.record("http", exc, method=request.method, route=request.url.path)
        raise
    if response.status_code >= 500:
        errorlog.record("http-response", "Server error", method=request.method, route=request.url.path,
                        status=response.status_code)
    return response

class ScreenError(BaseModel):
    message: str = Field(max_length=8000)
    stack: str = Field(default="", max_length=16000)


@app.post("/api/errors")
def screen_error(body: ScreenError) -> dict:
    errorlog.record("screen", body.message, stack=body.stack)
    return {"ok": True}

@app.middleware("http")
async def only_this_screen(request: Request, call_next):
    """Keep the local origin guard and require pairing on the explicit tunnel."""

    def refused(status: int, message: str):
        errorlog.record("http-guard", message, method=request.method, route=request.url.path, status=status)
        return JSONResponse({"detail": message}, status_code=status)

    if not mobile.companion.allowed(request):
        return refused(403, "이 화면의 요청이 아니다")
    remote = not mobile.local(request)
    pairing = request.url.path == "/api/mobile/pair"
    if remote:
        if request.method != "GET" and request.headers.get("origin") != mobile.companion.origin:
            return refused(403, "이 화면의 요청이 아니다")
        if request.url.path.startswith("/api/") and request.url.path != "/api/mobile/status" and not pairing \
                and not mobile.companion.authenticated(request):
            return refused(401, "PC에서 연결 링크를 만들어 휴대폰을 연결하세요")
    # Which project the screen shows. `query.project()` refuses a request from
    # a screen that shows another — a switch included: a screen that shows the
    # current project switches as before, and a stale one must not act at all.
    # Leaving `/api/config/*` out let a stale screen's model change switch the
    # server back to the project it showed.
    screen = request.headers.get("x-project")
    # A write that does not say which project it is for is not judged by the
    # check above — it would land in whichever project the server is on. The
    # screen holds everything but `/api/channels` until it knows its project;
    # this is the check behind that promise.
    if not screen and request.method != "GET" and not pairing:
        return refused(400, "어느 프로젝트의 화면인지 모르는 쓰기는 받지 않는다")
    token = query.claimed.set(unquote(screen) if screen else None)
    try:
        response = await call_next(request)
        if remote:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response
    finally:
        query.claimed.reset(token)


# -- The translation switch -------------------------------------------------
#
# One for the whole app, remembered. Off means `/api/translate` never calls the
# translator: the screen also stops asking, but that is a promise, and this is
# the check. The English the hooks give the agent is not behind this switch —
# it is the agent's input, and a screen's choice must not cut it.

TRANSLATE_MAX = 40          # sentences per request
TRANSLATE_CHARS = 40_000    # characters per request
TRANSLATE_SECONDS = 60.0    # an overlay renders whole answers; the hook's 6s cut them (#19)


def translation_mode() -> str:
    try:
        saved = json.loads(SWITCH.read_text(encoding="utf-8"))
        mode = saved.get("translation_mode")
        if mode in ("off", "full", "partial"):
            return mode
        return "full" if saved.get("translate", True) else "off"
    except (OSError, ValueError, AttributeError):
        return "full"


def translating() -> bool:
    return translation_mode() != "off"


class Switch(BaseModel):
    translate: bool | None = None
    mode: Literal["off", "full", "partial"] | None = None


@app.get("/api/switch")
def switch() -> dict:
    return {"translate": translating(), "mode": translation_mode(), "usage": translate.usage()}


@app.post("/api/switch")
def flip(body: Switch) -> dict:
    if body.mode is None and body.translate is None:
        raise HTTPException(400, "번역 모드를 선택하세요")
    mode = body.mode or ("full" if body.translate else "off")
    # The loop's settings share the file; they are kept.
    settings_file.saved(SWITCH, {"translate": mode != "off", "translation_mode": mode})
    work.feed.put({"kind": "sync"})
    return switch()


class Rendering(BaseModel):
    texts: list[str]
    direction: str = translate.EN_KO
    # A verified answer: a rendering that changes a number or identifier is refused (`translate.checked`).
    checked: bool = False


@app.post("/api/translate")
def render(body: Rendering) -> dict:
    """Render what a screen is about to show. Failure returns the original.

    The size limits are here because the monthly limit is shared with the
    hooks. A screen that accidentally posts a whole document burns what the
    hooks were going to spend.
    """

    if body.direction not in (translate.KO_EN, translate.EN_KO):
        raise HTTPException(400, "그런 방향이 없다")
    if len(body.texts) > TRANSLATE_MAX:
        raise HTTPException(413, f"한 번에 {TRANSLATE_MAX} 문장까지다")
    if sum(len(t) for t in body.texts) > TRANSLATE_CHARS:
        raise HTTPException(413, f"한 번에 {TRANSLATE_CHARS} 자까지다")
    if not translating():
        return {"texts": list(body.texts), "off": True}
    if body.checked:
        done = translate.checked(list(body.texts), body.direction, time.monotonic() + TRANSLATE_SECONDS)
        return {"texts": [t for t, _ in done], "statuses": [s for _, s in done]}
    return {"texts": translate.translate(
        list(body.texts), body.direction, time.monotonic() + TRANSLATE_SECONDS
    )}


# -- Looking inside a file --------------------------------------------------

def inside(base: Path, path: str) -> Path | None:
    target = (base / path).resolve()
    return target if base.resolve() in target.parents and target.is_file() else None


def git_lines(base: Path, *args: str) -> list[str]:
    try:
        return subprocess.run(["git", *args], cwd=base, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def locate(base: Path, path: str) -> tuple[Path, str]:
    """The file a cite means, and its path from where it was found.

    An answer cites three ways: from the checkout's root, as a hub page
    (`craft/x.md` — the injected rules live in the hub, not in the
    repository being worked on), or by a tail of the path (`7-verify.md`,
    `main/work.py`). Several files ending with that tail: an answer talks
    about what was just worked on, so the one touched most recently —
    uncommitted first, then commit by commit. Still a tie, they are named in
    the refusal instead of a guess.
    """

    path = unquote(path).replace("\\", "/")
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    if found := inside(base, path):
        return found, found.relative_to(base.resolve()).as_posix()
    if found := inside(channels.WIKI, path):
        return found, path
    tail = "/" + path.removeprefix("./")
    hits = [f for f in git_lines(base, "ls-files", "-co", "--exclude-standard") if ("/" + f).endswith(tail)]
    if len(hits) > 1:
        groups = [git_lines(base, "diff", "--name-only", "HEAD")
                  + git_lines(base, "ls-files", "-o", "--exclude-standard")]
        for line in git_lines(base, "log", "--name-only", "--format=format:@", "-n", "50"):
            if line == "@":
                groups.append([])
            elif line:
                groups[-1].append(line)
        hits = next(([h for h in hits if h in group] for group in groups if set(hits) & set(group)), hits)
    if len(hits) == 1 and (found := inside(base, hits[0])):
        return found, hits[0]
    if hits:
        raise HTTPException(404, f"그 이름의 파일이 여럿이다 — {', '.join(hits[:5])}")
    raise HTTPException(404, f"그 파일이 없다 — {path}")


@app.get("/api/file")
def peek(repo: str, path: str, line: int = 1, around: int = 25) -> dict:
    """Show the place a quoted `path:line` points at.

    `repo` is a project name, or a worktree path `work` knows. Never outside
    that checkout.
    """

    base = channels.repo_for(repo) or (work.known(repo) if Path(repo).is_absolute() else None)
    if base is None:
        raise HTTPException(400, "그런 저장소가 없다")
    target, path = locate(base, path)
    if target.stat().st_size > 2_000_000:
        raise HTTPException(413, "너무 크다")
    rows = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - around)
    end = min(len(rows), line + around)
    return {"path": path, "start": start, "line": line, "total": len(rows),
            "lines": rows[start - 1:end]}


# -- The screen -------------------------------------------------------------

@app.get("/mobile-install.apk")
def mobile_apk() -> FileResponse:
    # Installation precedes pairing. Expose only this fixed build artifact.
    if not mobile.APK.is_file():
        raise HTTPException(404, "Android 설치 파일이 없습니다. PC에서 APK를 먼저 빌드하세요.")
    return FileResponse(mobile.APK, media_type="application/vnd.android.package-archive",
                        filename="wiki-agent.apk", headers={"Cache-Control": "no-store"})


@app.get("/mobile-install")
def mobile_install() -> FileResponse:
    if not mobile.APK.is_file() or not (DIST / "mobile-install.html").is_file():
        raise HTTPException(404, "Android 설치 파일이 준비되지 않았습니다. PC에서 다시 확인하세요.")
    return FileResponse(DIST / "mobile-install.html", headers={"Cache-Control": "no-store"})


@app.get("/api/graph")
def wiki_graph(repo: str = "") -> dict:
    """The map of one repository, in two layers the screen toggles.

    `repo`: the repository's own documents and what points at what, built in
    memory (`repo_graph.picture`). `hub`: the hub rules as they stand in that
    repository, in `graph.build`'s shape — for the hub itself, `graph.json`
    as `graph.py` produced it, the policy graph unchanged. Nothing here writes:
    `repo_graph.write` would write the original checkout's `.wiki/graph.json`,
    which is the `sync` hook's to keep.
    """

    import graph
    import repo_graph
    import repo_lint

    path = channels.repo_for(repo) if repo else ROOT
    if path is None:
        raise HTTPException(404, "그런 저장소가 없다")
    if path == ROOT:
        page = ROOT / "graph.json"
        if not page.exists():
            raise HTTPException(404, "python tool/graph.py 를 먼저 돌려라")
        hub = json.loads(page.read_text(encoding="utf-8"))
    else:
        # Only the hub's own rules: another repository's knowledge pages do
        # not apply here, and this one's are the other layer.
        hub = graph.build(graph.load_pages(), [path])
    mine = repo_graph.picture(path)
    return {
        "repo": path.name,
        "hub": path == ROOT,
        "wiki": ROOT.name,
        "layers": {
            "repo": {"nodes": mine["nodes"], "edges": mine["edges"]},
            "hub": {"nodes": hub["nodes"], "edges": hub["links"],
                    "ladder": hub["ladder"]},
        },
        "metrics": {"pages": len(mine["nodes"]), "orphans": len(mine["orphans"]),
                    "lint": len(repo_lint.check(path))},
    }


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        # The assets under it are content-hashed and may be cached forever.
        # This file is the only thing that says which hash is current, so a
        # cached copy of it pins the window to a build that no longer exists.
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(DIST / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/mobile-icon.svg")
    def mobile_icon() -> FileResponse:
        return FileResponse(DIST / "mobile-icon.svg", media_type="image/svg+xml")

    @app.get("/pwa-192.png")
    @app.get("/pwa-512.png")
    @app.get("/pwa-maskable-512.png")
    @app.get("/apple-touch-icon.png")
    def mobile_png(request: Request) -> FileResponse:
        return FileResponse(DIST / request.url.path.removeprefix("/"), media_type="image/png")

    @app.get("/offline.html")
    def offline() -> FileResponse:
        return FileResponse(DIST / "offline.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(DIST / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
else:
    @app.get("/")
    def index() -> dict:
        return {"화면이 아직 없다": "npm --prefix web run build 를 먼저 돌려라"}


def taken(host: str, port: int) -> bool:
    """Is somebody already listening on that port?"""

    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def demo() -> None:
    """Does the conversation survive an effort change? It once did not.

    No HTTP. The bug was `session()` swapping a dead process for a new object
    and throwing the `session_id` away.

        python tool/main --check
    """

    cid = "wiki"
    token = "QUOKKA-9"
    wipe = query.Clear(keep="delete")
    query.reset(cid, wipe)
    try:
        first = last = {}
        for ev in query.session(cid).say(f"이 표를 기억해라: {token}. '알겠다' 한 마디만."):
            if ev.kind == "done":
                first = ev.meta

        answer = query.configure(cid, query.Config(repo=query.config(cid)["repo"], effort="max"))
        assert answer["kept"], answer

        text = ""
        for ev in query.session(cid).say("아까 기억하라고 한 표를 그대로 적어라. 그것만."):
            if ev.kind == "done":
                text, last = ev.text, ev.meta

        assert first.get("session_id"), first
        assert first["session_id"] == last.get("session_id"), (first, last)
        assert token in text, text
        print(f"ok  effort 를 바꿔도 대화가 남는다 — session {first['session_id']}")
    finally:
        query.reset(cid, wipe)
        query.close_all()


def main() -> int:
    import uvicorn

    # The port notice and the self-check output are both Korean. The encoding
    # is not left to the environment — this repository has been burned by one
    # cp949 character eight times.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    previous_exception, previous_thread_exception = sys.excepthook, threading.excepthook
    def exception(kind, value, trace):
        errorlog.record("uncaught", value)
        previous_exception(kind, value, trace)
    def thread_exception(args):
        errorlog.record("thread", args.exc_value, thread=args.thread.name if args.thread else "")
        previous_thread_exception(args)
    sys.excepthook, threading.excepthook = exception, thread_exception

    ap = argparse.ArgumentParser(prog="python tool/main")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--workspace", type=Path, help="프로젝트들이 들어 있는 폴더")
    # Local requests have no login. Remote access uses the paired tunnel,
    # never a listener exposed to the LAN.
    ap.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    ap.add_argument("--mobile-origin", default="", help="고정 HTTPS 터널 주소 (Host는 mobile.wiki-agent.invalid)")
    ap.add_argument("--check", action="store_true", help="자체 점검만 하고 끝낸다")
    # The Tauri shell holds our stdin. When it closes — the window shut, or
    # the shell died — the pipe closes too, and this goes down properly,
    # taking the agent processes with it.
    ap.add_argument("--exit-with-stdin", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    mobile.companion.port = args.port
    if args.mobile_origin:
        try:
            origin = AnyHttpUrl(args.mobile_origin)
            if origin.scheme != "https" or not origin.host or origin.username is not None or origin.password is not None \
                    or origin.path not in (None, "/") or origin.query is not None or origin.fragment is not None \
                    or origin.port == 0:
                raise ValueError
        except ValueError:
            ap.error("--mobile-origin은 경로 없는 HTTPS 주소여야 합니다")
        mobile.companion.key()
        # Use the browser's canonical host and port at every exact-origin guard.
        mobile.companion.origin = str(origin).removesuffix("/")

    if args.workspace:
        channels.WORKSPACE = args.workspace.expanduser().resolve()
    if not channels.WORKSPACE.is_dir():
        ap.error("프로젝트 폴더가 없습니다. --workspace로 실제 폴더를 지정하세요.")
    # Pointed at the wiki itself, there are no repositories underneath and the
    # list quietly shrinks to the wiki alone.
    if channels.WORKSPACE == channels.WIKI:
        ap.error("프로젝트 폴더가 위키 자신입니다. 프로젝트들이 들어 있는 상위 폴더를 지정하세요: "
                 f"{channels.WIKI.parent}")

    if args.check:
        demo()
        return 0

    # With the port taken, uvicorn prints one English line and exits 1. Say
    # what is blocked and how to clear it, first.
    if taken(args.host, args.port):
        print(
            f"\n{args.port} 번 포트를 이미 누가 듣고 있다. 그래서 안 뜬다.\n\n"
            f"  누구인지 본다   netstat -ano | findstr :{args.port}\n"
            f"  다른 포트로     python tool/main --port 9090\n",
            file=sys.stderr,
        )
        return 1

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning",
                                        proxy_headers=False, ws="wsproto", ws_max_size=100_000))
    ended_first(server)
    if args.exit_with_stdin:
        # The pipe is read through a private copy, and fd 0 becomes devnull.
        # On Windows a synchronous read pending on the handle a child would
        # inherit as its stdin blocks `CreateProcess`: every `git` and every
        # agent hung, while the routes that start nothing answered.
        pipe = os.dup(0)    # not inheritable
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)

        def watch() -> None:
            with os.fdopen(pipe, "rb") as parent:
                while parent.read(4096):
                    pass
            server.should_exit = True

        threading.Thread(target=watch, daemon=True).start()

    print(f"http://{args.host}:{args.port}/  — 끄려면 Ctrl+C", flush=True)
    server.run()
    return 0
