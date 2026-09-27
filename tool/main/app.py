"""app — the program's main. It weaves the pipelines and nothing weaves it.

Three parts hang here: the wiki query (`query`), the worktrees and their
agents (`work`), and what the whole screen shares — the translation switch,
looking inside a file, the map. The terminal is not here; the Tauri shell
holds it.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import translate

from . import channels, connect, loop, query, specs, work

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

    # A loop that ran when the server last went down stopped with it; it
    # says so, and waits for a person's `[계속]`.
    loop.recover()
    threading.Thread(target=loop.poll, daemon=True).start()
    yield
    loop.close_all()
    query.close_all()
    work.close_all()


app = FastAPI(title="wiki-agent", lifespan=lifespan)
app.include_router(query.router)
app.include_router(work.router)
app.include_router(specs.router)
app.include_router(loop.router)
app.include_router(connect.router)

# The names this server answers to. Anything else in `Host` is another site's
# domain resolved to 127.0.0.1 — DNS rebinding — and gets nothing.
LOCAL = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def only_this_screen(request: Request, call_next):
    """Refuse a request another site's page sent.

    This server approves writes now. Binding to 127.0.0.1 keeps the network
    out but not the browser: any open tab can `fetch` it. A page of ours sends
    `Origin` equal to this server, or none on a plain GET; another site's page
    cannot forge either.
    """

    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if urlsplit(f"//{host}").hostname not in LOCAL or (origin is not None and origin != f"http://{host}"):
        return JSONResponse({"detail": "이 화면의 요청이 아니다"}, status_code=403)
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
    if not screen and request.method != "GET":
        return JSONResponse({"detail": "어느 프로젝트의 화면인지 모르는 쓰기는 받지 않는다"}, status_code=400)
    token = query.claimed.set(unquote(screen) if screen else None)
    try:
        return await call_next(request)
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


def translating() -> bool:
    try:
        return bool(json.loads(SWITCH.read_text(encoding="utf-8")).get("translate", True))
    except (OSError, ValueError, AttributeError):
        return True


class Switch(BaseModel):
    translate: bool


@app.get("/api/switch")
def switch() -> dict:
    return {"translate": translating(), "usage": translate.usage()}


@app.post("/api/switch")
def flip(body: Switch) -> dict:
    # The loop's settings share the file; they are kept.
    try:
        saved = json.loads(SWITCH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = {**(saved if isinstance(saved, dict) else {}), "translate": body.translate}
    SWITCH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SWITCH.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(SWITCH)
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
                              encoding="utf-8", timeout=10).stdout.splitlines()
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

    if found := inside(base, path):
        return found, path
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

    ap = argparse.ArgumentParser(prog="python tool/main")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--workspace", type=Path, help="프로젝트들이 들어 있는 폴더")
    # Bound to 127.0.0.1. This server has no authentication, and opening it to
    # the LAN is handing someone a shell.
    ap.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    ap.add_argument("--check", action="store_true", help="자체 점검만 하고 끝낸다")
    # The Tauri shell holds our stdin. When it closes — the window shut, or
    # the shell died — the pipe closes too, and this goes down properly,
    # taking the agent processes with it.
    ap.add_argument("--exit-with-stdin", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

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

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
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
