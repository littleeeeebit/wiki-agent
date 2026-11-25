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

The part of it that touches only a checkout's own untracked Claude settings
needs no click: `settle` writes it whenever `watch` starts or sees a change,
the pages' deny rules included, so a drift the lint reports is never left for
the person to find. The user-level move and every Codex change stay the
person's to confirm.

What reaches the screen is read by a person and stays Korean.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import apply
import setup_agents
from common import indexes
from common.paths import PACKAGED

from . import channels, connect, runtime, work
from .query import ROOT
from .refresh import Refresh

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
    """What the wiring verdict depends on outside the server: the hub's branch, every host's user settings, and the
    pages whose `enforce` a checkout's own settings carry (`apply.declared`)."""

    files = []
    pages = [p for scope in ("operator", "craft") for p in sorted((apply.WIKI / scope).glob("*.md"))]
    for file in [*(f for host in connect.HOSTS for f in apply.user_files(host)), *pages]:
        try:
            stat = file.stat()
            files.append((str(file), stat.st_mtime_ns, stat.st_size))
        except OSError:
            files.append((str(file), 0, 0))
    return hub_branch(), tuple(files)


def watch(halt: threading.Event, every: float = EVERY) -> None:
    """Until `halt`: once at the start and on any change of `mark`, every checkout's own settings are settled
    (`settle`); on a change the screens list the repositories again (`connect` on the feed)."""

    seen = mark()
    if settle():   # a screen that listed the repositories before this write lists them again
        work.feed.put({"kind": "connect", "repo": ""})
    while not halt.wait(every):
        now = mark()
        if now != seen:
            seen = now
            forget_hub()   # the routine reads' last check was of the wiring before this change
            settle()
            work.feed.put({"kind": "connect", "repo": ""})


def settle() -> list[str]:
    """What `[모두 다시 연결]` writes into each connected checkout's own Claude settings, written with no click: the
    old per-project hooks out and the pages' deny rules in (`connect.rewired`), so `apply.wiring_drift` finds
    nothing. Only while Claude is wired at the user level, only a file git does not track, and never while the hub
    is off main or where hooks are switched off, at the user level or the checkout's: those stay on the screen for
    the person, and so does every Codex change (`claude_off`). What was written, as lines."""

    if refused():
        return []
    try:
        paths = {p.resolve() for p in connected()}
    except Exception:   # noqa: BLE001 — a listing that fails leaves the watch running; the screen names it
        return []
    done = []
    for path in paths:
        try:   # each file as it was, absent included, read before the plan: a change after it is never planned over
            sources = {file: raw(file) for file in (path / name for name in setup_agents.SETTINGS.values())}
            plan = connect.pending(path)
            if switched_off(plan):
                continue
            for file, settings, changes in plan:   # Claude's only (`claude_off`); Codex's wait for `[모두 다시 연결]`
                if changes and file.parent.name == ".claude" and apply.user_wired("claude") and not claude_off() \
                        and own(path, file) and replaced(path, file, settings, sources[file]):
                    done += [f"{file}: {change}" for change in changes]
        except Exception:   # noqa: BLE001 — one checkout never stops the others or the watch; the screen names it
            continue
    return done


def claude_off() -> bool:
    """Claude hooks switched off at the user level (`disableAllHooks`), which Claude honours from any layer: the
    watch then writes no Claude settings, as `setup_agents.plan_global` refuses. Codex's checkout hooks are never
    the watch's: only `[모두 다시 연결]` drops them, once Codex confirmed it trusts their user-level replacements."""

    return any(apply.read_json(file).get("disableAllHooks") for file in apply.user_files("claude"))


def raw(file: Path) -> bytes | None:
    """A file's bytes, or `None` when there is none, read from the file the path still names once they are read: on
    macOS and Linux an open file stays readable after a save renames another over it, or it is removed."""

    for _ in range(3):
        try:
            with open(file, "rb") as held:
                data = held.read()
                if os.path.samestat(os.fstat(held.fileno()), os.stat(file)):
                    return data
        except FileNotFoundError:
            return None
    raise OSError(f"{file}: 읽는 동안 계속 바뀌었다")   # never compared: the checkout's write is skipped


def replaced(repo: Path, file: Path, settings: dict, source: bytes | None) -> bool:
    """`settings` staged in the folder `own` checked and swapped in whole, only if, once the staging is written,
    that folder is still the same one, the file still the checkout's own and untracked and still `source`, the
    bytes the plan was made from (`None`: absent), with `disableAllHooks` off in both files, the hub still on main,
    and the server not going down. An edit or removal the person made since writes nothing.

    Accepted limits: the final checks read the folder, the index (one `git ls-files`), both files' opt-out and the
    bytes once each, in that order, and no lock holds them still; an action that lands after the check that would
    have seen it and before the swap is not seen, however long that window runs. A folder renamed while the staging
    is written keeps that `.part` file, which the old path no longer reaches. The watch guards against mistakes, not
    races; after a write the screens list the repositories again (`watch`), which shows the wiring as it now is."""

    file.parent.mkdir(exist_ok=True)
    where = file.parent.resolve()
    before = os.stat(where)
    fd, partial = tempfile.mkstemp(dir=where, prefix=file.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(connect.settled(settings))
        plan = connect.pending(repo)   # the slow read first: what the person does meanwhile, the checks below see
        if runtime.stopping.is_set() or refused() or file.parent.resolve() != where \
                or not os.path.samestat(before, os.stat(where)) \
                or not own(repo, file) or switched_off(plan) or claude_off() or raw(file) != source:   # bytes last
            return False
        os.replace(partial, where / file.name)
        return True
    finally:
        Path(partial).unlink(missing_ok=True)


def own(repo: Path, file: Path) -> bool:
    """Whether `file` is the checkout's own untracked settings: at its own path with no link on the way, in no git
    checkout of its own (a submodule, a nested repository), and a git that read its index and lists no such file,
    in any capitalization. Anything else is the person's to change, never the watch's."""

    if file.is_symlink() or file.resolve() != repo / file.relative_to(repo):
        return False
    # The folder alone names the checkout and its index: no inherited `GIT_DIR`, `GIT_INDEX_FILE` or pathspec mode.
    plain = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    if file.parent.is_dir():   # the checkout its folder belongs to, which must be this one
        top = subprocess.run(["git", "-C", str(file.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, encoding="utf-8", errors="replace", env=plain)
        if top.returncode or Path(top.stdout.strip()).resolve() != repo.resolve():
            return False
    done = subprocess.run(["git", "-C", str(repo), "ls-files", "--", f":(icase){file.relative_to(repo).as_posix()}"],
                          capture_output=True, encoding="utf-8", errors="replace", env=plain)
    return done.returncode == 0 and not done.stdout.strip()


def switched_off(plan: list[tuple[Path, dict, list[str]]]) -> list[Path]:
    """The Claude settings files of `plan` with `disableAllHooks` on, a local one beside them included: as
    `setup_agents.plan_global` refuses, it is the person's own switch, never one to clear for them."""

    claude = [file for file, _s, _c in plan if file.parent.name == ".claude"]
    return [file for file in [*claude, *(f.with_name("settings.local.json") for f in claude)]
            if apply.read_json(file).get("disableAllHooks")]


# The routine reads' answer (`hub_seen`): the last check that finished, so the project list and the settings line
# never wait on a Codex `app-server`; its end announces `connect`, and the screens read again.
HUB_TTL = 60.0
_hub = Refresh("hub-check", lambda: connect.hub(), HUB_TTL, HUB_TTL,
               done=lambda: work.feed.put({"kind": "connect", "repo": ""}))


def hub_seen() -> dict:
    """`connect.hub()` without waiting, `checking` while a check runs; before the first one ends, nothing needed."""

    shown, error, checking = _hub.read()
    return {**(shown or {"needed": bool(error), "refused": error}), "checking": checking}


def forget_hub() -> None:
    """The machine's wiring changed: the next routine read checks again, and a check begun before is not kept."""

    _hub.forget()


def refused() -> str:
    if PACKAGED:
        return ""
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
            off = switched_off(plan)
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
    only the hub move — or nothing. `failed` names what failed after others
    were written, and `written` what had landed by then. A hub move that fails
    partway stops there: the repositories' hooks come out only once the
    machine's wiring calls this hub."""

    shown, files = _survey()
    if why := "\n".join(filter(None, [shown["refused"], shown["hub"]["refused"],
                                       *(repo["refused"] for repo in shown["repos"])])):
        raise HTTPException(409, why)
    if body.plan != shown["digest"]:
        raise HTTPException(409, "확인한 뒤에 바뀐 것이 있다 — 계획을 다시 읽고 확인한다")
    written, failed = [], []
    try:
        if shown["hub"]["needed"]:
            try:
                connect.move(shown["hub"]["digest"], done=written)
            except HTTPException:
                raise
            except Exception as exc:   # whatever a user file, link or Codex's writer raised, said with how far it got
                why = " — ".join([f"{type(exc).__name__}: {exc}", *getattr(exc, "__notes__", [])])   # a kept link, named
                return {"ok": False, "written": written, "failed": [f"허브 이전: {why}"]}
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
