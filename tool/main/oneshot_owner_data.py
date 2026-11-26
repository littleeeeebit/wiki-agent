"""A build session on a shared machine (plan 15a): the owner's real data folders watched over its turns, its GPU
jobs ended at its boundary, and the prompt section that explains both.

`begin` writes the run's hold, `owner_watch: {turn, started}`, and starts watching before the turn is sent, as
`oneshot_needs.owe` writes its hold. `read` judges the window so far, right before a check saves anything;
`joined` ends it and settles the hold, on every exit of the check, or `settle` at `work.run_turn`'s finalization
for a turn that never reached it. A hold with no live watch was left by a server that stopped mid-turn: an
unwatched window is never taken for a clean one, so the run stops for the owner, whose resume clears it.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import owner_data
from common import errorlog

from . import oneshot_spec, specs
from .query import ROOT

LEFTOVER = "주인 데이터 폴더를 감시하지 못한 구간이 있다 — 확인 후 재개"
RESOURCES = (ROOT / "tool/prompts/oneshot-resources.md").read_text(encoding="utf-8")
_watches: dict[str, tuple[str, owner_data.Watch, list[Path]]] = {}   # repo -> (turn token, watch, roots)
_lock = threading.Lock()


def leftover(repo: str, o: dict) -> bool:
    """A hold this server has no watch for: a window nobody watched."""

    with _lock:
        return bool(o.get("owner_watch")) and repo not in _watches


def begin(repo: str, tree: Path, store, token: str, text: str, note: str | None) -> str:
    """The hold, then the watch, whose first reads are issued before this returns; `text` as sent, told first what
    the last build turn's watch saw (`note`, and an earlier turn's watch still open, closed now)."""

    found = owner_data.roots(tree)
    store.put(owner_watch={"turn": token, "started": time.time()})
    watch = owner_data.Watch(found)
    with _lock:
        old = _watches.pop(repo, None)
        _watches[repo] = (token, watch, found)
    stale = owner_data.reason(old[1].close(), old[2]) if old else ""
    if seen := "; ".join(filter(None, [note, stale])):   # `send` read the refusal `text` is bound to already
        return f"The server's watch of the owner's data folders, from the last build turn: {seen}\n\n{text}"
    return text


def read(repo: str) -> str:
    """The window so far: "" when clean, else the refusal's text."""

    with _lock:
        entry = _watches.get(repo)
    return owner_data.reason(entry[1].result(), entry[2]) if entry else ""


def close(repo: str, store, token: str | None = None) -> str:
    """End the watch (only `token`'s, when given) and settle the hold: "" when clean, else the refusal's text."""

    with _lock:
        entry = _watches.get(repo)
        if entry is None or token is not None and entry[0] != token:
            return ""
        _watches.pop(repo)
    why = owner_data.reason(entry[1].close(), entry[2])
    store.put(owner_watch=None)
    return why


def joined(repo: str, run, why=""):
    """The check's outcome with the closed watch's: a refusal says what the watch saw too, a pass is told it in
    the turn's log. Every check read it right before it saved, so a pass that stands is only told what came after."""

    if not (seen := close(repo, oneshot_spec.store(repo), getattr(run, "token", None))):
        return why
    if not why:
        specs.note(run, seen)
        return why
    return why if seen in why else type(why)(f"{why}\n\n{seen}")


def settle(run, path: Path) -> None:
    """At a turn's finalization (`work.run_turn`): a oneshot build turn's watch its check never closed is closed,
    and what it saw goes into the next build turn's note (`owner_note`). Any other turn has none."""

    if not ((token := getattr(run, "token", None)) and (repo := getattr(run, "repo", None))):
        return
    try:
        store = oneshot_spec.store(repo)
        if why := close(repo, store, token):
            store.put(owner_note=why)
    except Exception as exc:  # noqa: BLE001 -- the turn's end goes on; the hold left stops the next send
        errorlog.record("owner-watch", exc, turn=getattr(run, "turn", None), path=str(path))


def cleared(repo: str) -> None:
    """The owner's resume: the hold and any watch go, the window is the owner's call."""

    with _lock:
        entry = _watches.pop(repo, None)
    if entry:
        entry[1].close()


def measured(tree: Path):
    """A server measurement's own watch over the same roots: `(watch, roots)`; `owner_data.reason` of its
    `close()` fails the measurement."""

    found = owner_data.roots(tree)
    return owner_data.Watch(found), found


def gpu_boundary(chat) -> None:
    """A build turn's GPU jobs end with it: when its CLI queued one (`gpu_job.mark`), the cached CLI is closed,
    which ends its job and every runner in it, started or starting, and the mark goes. Each runner's holder note
    stays for the next holder to wait on. The next turn starts the CLI again with `--resume`."""

    import gpu_queue

    if os.name == "nt" and (job := getattr(chat, "job_uuid", "")) and (mark := gpu_queue.wrapped(job)).exists():
        chat.close()
        mark.unlink(missing_ok=True)


def resources_text(spec: dict) -> str:
    """The shared machine's section of every build-session prompt (plan 15a, "What the build is told")."""

    import machine

    found = owner_data.roots(Path(spec["worktree"])) if spec.get("worktree") else []
    agent = oneshot_spec.store(spec["repo"]).folder / "measure-app" / "agent" if spec.get("repo") else "measure-app\\agent"
    return "\n" + RESOURCES.replace("{machine}", machine.size_line() or "The machine's size could not be read.") \
        .replace("{roots}", "\n".join(f"  - `{r}`" for r in found) or "  - (none found)").replace("{agent}", str(agent))
