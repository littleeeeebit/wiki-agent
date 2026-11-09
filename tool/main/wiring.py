"""wiring — the machine's hook wiring, watched live and redone for every repository at once.

`connect` reads whether each repository is wired when the screen lists them.
Nothing told the screen when the wiring changed outside it: a hand-run
`setup_agents.py`, a host rewriting its user settings, or the hub checkout
switched to another branch — whose older `apply.py` then judges every
repository unwired. `watch` notices those and asks the screens to list again.

`[모두 다시 연결]` is `setup_agents.py --global --project <each>` from the
screen: the hub move `connect.hub` shows, then the old per-project hooks out
of every connected repository, all confirmed by one digest of the whole plan.
It is refused while the hub is off main, since it would write that branch's
wiring onto the machine, and where a repository has its hooks switched off.

What reaches the screen is read by a person and stays Korean.
"""

from __future__ import annotations

import json
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


def _survey() -> tuple[dict, list[tuple[Path, dict]]]:
    """What `[모두 다시 연결]` will write, with a digest of all of it, and the
    settings it was read from — every file read before any is written, so a
    broken one refuses the whole rewire instead of stopping it halfway."""

    repos, files = [], []
    for path in connected():
        entry = {"name": path.name, "path": str(path), "unwire": [], "refused": ""}
        try:
            plan = connect.pending(path)
            claude = [file for file, _s, _c in plan if file.parent.name == ".claude"]
            # As `setup_agents.plan_global` refuses: Claude honours it from any layer,
            # and it is the person's own switch, never one to clear for them.
            off = [file for file in [*claude, *(f.with_name("settings.local.json") for f in claude)]
                   if apply.read_json(file).get("disableAllHooks")]
        except (OSError, ValueError) as exc:
            entry["refused"] = f"{path}: 설정을 읽을 수 없다 — {exc}"
        else:
            entry["unwire"] = [f"{file}: {change}" for file, _s, changes in plan for change in changes]
            entry["refused"] = "\n".join(f"{file}: disableAllHooks 가 켜져 있다 — 직접 검토한다" for file in off)
            files += [(file, settings) for file, settings, changes in plan if changes]
        repos.append(entry)
    shown = {"refused": refused(), "hub": connect.hub(), "repos": repos}
    return {**shown, "digest": connect.digest(json.dumps(shown, sort_keys=True, ensure_ascii=False).encode())}, files


@router.get("/api/rewire/plan")
def plan() -> dict:
    """What `[모두 다시 연결]` will write, for its confirmation."""

    return _survey()[0]


class Rewire(BaseModel):
    plan: str = ""   # the digest of the whole plan the person confirmed


@router.post("/api/rewire")
def rewire(body: Rewire) -> dict:
    """Write exactly the plan confirmed — the repositories and every line, not
    only the hub move — or nothing. `failed` names a file whose write failed
    after others were written, and `written` what had landed by then."""

    shown, files = _survey()
    if why := "\n".join(filter(None, [shown["refused"], shown["hub"]["refused"],
                                       *(repo["refused"] for repo in shown["repos"])])):
        raise HTTPException(409, why)
    if body.plan != shown["digest"]:
        raise HTTPException(409, "확인한 뒤에 바뀐 것이 있다 — 계획을 다시 읽고 확인한다")
    written, failed = [], []
    try:
        if shown["hub"]["needed"]:
            connect.move(shown["hub"]["digest"])
            written.append("허브 이전")
        for file, settings in files:
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(connect.settled(settings), encoding="utf-8", newline="\n")
                written.append(str(file))
            except OSError as exc:
                failed.append(f"{file}: {exc}")
    finally:
        work.feed.put({"kind": "connect", "repo": ""})
    return {"ok": not failed, "written": written, "failed": failed}
