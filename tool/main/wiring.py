"""wiring — the machine's hook wiring, watched live and redone for every repository at once.

`connect` reads whether each repository is wired when the screen lists them.
Nothing told the screen when the wiring changed outside it: a hand-run
`setup_agents.py`, a host rewriting its user settings, or the hub checkout
switched to another branch — whose older `apply.py` then judges every
repository unwired. `watch` notices those and asks the screens to list again.

`[모두 다시 연결]` is `setup_agents.py --global --project <each>` from the
screen: the hub move `connect.hub` shows, confirmed by its digest, then the old
per-project hooks out of every connected repository. It is refused while the
hub is off main, since it would write that branch's wiring onto the machine.

What reaches the screen is read by a person and stays Korean.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import apply
from common import indexes

from . import channels, connect, work
from .query import ROOT

router = APIRouter()
EVERY = 3.0     # seconds between two looks; a stat and one small read each


def hub_branch() -> str:
    """The hub checkout's branch, read from its `HEAD`; empty when detached or unreadable."""

    where = indexes.git_dir(ROOT)
    try:
        head = (where / "HEAD").read_text(encoding="utf-8").strip() if where else ""
    except OSError:
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else ""


def mark() -> tuple:
    """What the wiring verdict depends on outside the server: the hub's branch and every host's user settings."""

    files = []
    for host in connect.HOSTS:
        for file in apply.user_files(host):
            try:
                stat = file.stat()
                files.append((str(file), stat.st_mtime_ns, stat.st_size))
            except OSError:
                files.append((str(file), 0, 0))
    return hub_branch(), tuple(files)


def watch(halt: threading.Event, every: float = EVERY) -> None:
    """Until `halt`: on any change of `mark`, the screens list the repositories again (`connect` on the feed)."""

    seen = mark()
    while not halt.wait(every):
        now = mark()
        if now != seen:
            seen = now
            work.feed.put({"kind": "connect", "repo": ""})


def refused() -> str:
    branch = hub_branch()
    if branch == "main":
        return ""
    return (f"허브 체크아웃이 `{branch or '분리된 HEAD'}` 에 있다 — main 으로 돌린 뒤에 다시 연결한다. "
            "다른 브랜치의 코드로 연결하면 그 브랜치의 배선이 이 기계 전체에 걸린다")


def connected() -> list[Path]:
    return [Path(row["path"]) for row in channels.projects() if row["state"] != "미연결"]


@router.get("/api/rewire/plan")
def plan() -> dict:
    """What `[모두 다시 연결]` will write, for its confirmation."""

    return {"refused": refused(), "hub": connect.hub(),
            "repos": [{"name": path.name, "unwire": connect.unwire(path, write=False)} for path in connected()]}


class Rewire(BaseModel):
    hub: str = ""   # the digest of the hub move the person confirmed


@router.post("/api/rewire")
def rewire(body: Rewire) -> dict:
    if why := refused():
        raise HTTPException(409, why)
    if connect.hub()["needed"]:
        if not body.hub:
            raise HTTPException(409, "허브 이전을 먼저 확인받아야 한다")
        connect.move(body.hub)
    changed = {path.name: connect.unwire(path) for path in connected()}
    work.feed.put({"kind": "connect", "repo": ""})
    return {"ok": True, "changed": changed}
